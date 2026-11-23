#!/usr/bin/env python3
"""cbBTC trend bot for make.fun bars-replay/v1.

Reads one JSON line per minute (hello, then one batch per bar, then done)
on stdin and answers every batch with exactly one reply carrying orders.

Strategy (long only, one symbol at a time, full size):
  * Enter when the 3-hour momentum is strong (close up >= 1.5% vs 180 bars
    ago) and the fast EMA (20 bars) sits above the slow EMA (90 bars).
    A move that big over three hours is a real trend, not minute noise.
  * Exit on any of: a 2% trailing stop below the peak since entry, a 3%
    hard stop below the entry fill, a breakeven guard (once the trade is
    1% in profit, exit if it falls back to entry + 0.3%), or the 3-hour
    momentum turning negative (<= -0.5%).
  * After an exit, wait 60 bars before re-entering, so one whipsaw cannot
    churn the account. Never trade during warm-up bars.
  * Every order is sized from the account the harness sends (cash and
    positions are the source of truth); buys leave room for the fill fee.
  * Fully deterministic: every decision is a pure function of the bars and
    the hello seed; no clock, no randomness, no network, stdlib only.

Money is integer units throughout: quote units are micro-USDC (scale 6),
prices are integers at the symbol's price_scale, quantities at qty_scale.
"""

import json
import sys

# --- strategy constants (in fractions of price) ---
FAST_SPAN = 20          # fast EMA span, in bars (minutes)
SLOW_SPAN = 90          # slow EMA span, in bars
MOM_BARS = 180          # momentum lookback, in bars (3 hours)
MOM_ENTER = 0.015       # enter when 3h return >= +1.5%
MOM_EXIT = -0.005       # exit when 3h return <= -0.5%
TRAIL = 0.02            # trailing stop: 2% below peak since entry
HARD_STOP = 0.03        # hard stop: 3% below entry fill
BREAKEVEN_ARM = 0.01    # arm the breakeven guard at +1% profit
BREAKEVEN_EXIT = 0.003  # ...then exit if price falls back to +0.3%
COOLDOWN_BARS = 60      # bars to wait after an exit before re-entering
MAX_SLIPPAGE_BPS = 200  # 2% max slippage vs the latest close


class SymbolState:
    """Per-symbol trend state, fed one bar at a time."""

    def __init__(self, price_scale):
        self.price_scale = price_scale
        self.closes = []          # float closes, oldest first
        self.ema_fast = None
        self.ema_slow = None
        self.entry = None         # entry fill price (float USDC), None if flat
        self.peak = None          # highest close since entry
        self.breakeven_armed = False
        self.cool_until = -1      # bar index before which no re-entry

    def on_close(self, close):
        self.closes.append(close)
        alpha_fast = 2.0 / (FAST_SPAN + 1)
        alpha_slow = 2.0 / (SLOW_SPAN + 1)
        if self.ema_fast is None:
            self.ema_fast = close
            self.ema_slow = close
        else:
            self.ema_fast = alpha_fast * close + (1.0 - alpha_fast) * self.ema_fast
            self.ema_slow = alpha_slow * close + (1.0 - alpha_slow) * self.ema_slow

    def momentum(self):
        if len(self.closes) <= MOM_BARS:
            return None
        old = self.closes[-1 - MOM_BARS]
        if old <= 0:
            return None
        return self.closes[-1] / old - 1.0

    def entry_signal(self, bar_index):
        if bar_index < self.cool_until:
            return False
        if self.ema_fast is None or self.ema_slow is None:
            return False
        if self.ema_fast <= self.ema_slow:
            return False
        mom = self.momentum()
        if mom is None or mom < MOM_ENTER:
            return False
        return True

    def exit_signal(self, close):
        if self.entry is None:
            return False
        if self.peak is not None and close <= self.peak * (1.0 - TRAIL):
            return True
        if close <= self.entry * (1.0 - HARD_STOP):
            return True
        if self.peak is not None and self.peak >= self.entry * (1.0 + BREAKEVEN_ARM):
            self.breakeven_armed = True
        if self.breakeven_armed and close <= self.entry * (1.0 + BREAKEVEN_EXIT):
            return True
        mom = self.momentum()
        if mom is not None and mom <= MOM_EXIT:
            return True
        return False


class Bot:
    def __init__(self):
        self.symbols = {}         # symbol -> {"price_scale": int, "qty_scale": int}
        self.state = {}           # symbol -> SymbolState
        self.caps = {}
        self.fees = {}
        self.warmup_bars = 0
        self.bar_index = -1       # counts batches seen (0-based)
        self.order_no = 0
        self.positions = {}       # symbol -> qty units (int), from account

    def next_client_id(self, side):
        self.order_no += 1
        return "%s-%06d" % (side, self.order_no)

    def handle_hello(self, msg):
        self.caps = msg.get("caps", {}) or {}
        self.fees = msg.get("fees", {}) or {}
        self.warmup_bars = int(msg.get("warmup_bars") or 0)
        for sym in msg.get("symbols", []):
            name = sym["symbol"]
            self.symbols[name] = {
                "price_scale": int(sym["price_scale"]),
                "qty_scale": int(sym["qty_scale"]),
            }
            self.state[name] = SymbolState(int(sym["price_scale"]))

    def handle_batch(self, msg):
        self.bar_index += 1
        seq = msg.get("seq")
        account = msg.get("account", {}) or {}
        cash = int(account.get("cash_quote_units") or 0)
        equity = int(account.get("equity_quote_units") or cash)
        # Positions from the account are the source of truth.
        self.positions = {}
        for pos in account.get("positions", []) or []:
            qty = int(pos.get("qty") or 0)
            if qty > 0:
                self.positions[pos["symbol"]] = qty
        # Take the entry price from a filled buy, exactly as the harness
        # reported it (price is an integer at the symbol's price_scale).
        # This runs before the cost-basis fallback below, so a fill that
        # the account has not shown yet still anchors the trade.
        for fill in msg.get("fills", []) or []:
            if fill.get("status") != "filled" or fill.get("side") != "buy":
                continue
            st = self.state.get(fill["symbol"])
            if st is None:
                continue
            scale = self.symbols[fill["symbol"]]["price_scale"]
            st.entry = int(fill["price"]) / float(10 ** scale)
            st.peak = st.entry
            st.breakeven_armed = False
        # Adopt a cost basis for any position we hold but have no entry for.
        for pos in account.get("positions", []) or []:
            name = pos["symbol"]
            qty = int(pos.get("qty") or 0)
            cost = int(pos.get("cost_quote_units") or 0)
            st = self.state.get(name)
            if st is None or qty <= 0:
                continue
            if st.entry is None and cost > 0:
                scale = self.symbols[name]["price_scale"]
                qty_scale = self.symbols[name]["qty_scale"]
                # cost is micro-USDC; qty is in qty units.
                st.entry = (cost / 1_000_000.0) / (qty / float(10 ** qty_scale))
                st.peak = st.entry
        # Feed the bars, one per symbol, in sorted order (deterministic).
        bars = {}
        for bar in msg.get("bars", []) or []:
            bars[bar["symbol"]] = bar
        for name in sorted(bars):
            st = self.state.get(name)
            if st is None:
                continue
            bar = bars[name]
            scale = self.symbols[name]["price_scale"]
            close = int(bar["c"]) / float(10 ** scale)
            st.on_close(close)
            if st.entry is not None:
                if st.peak is None or close > st.peak:
                    st.peak = close

        orders = []
        if self.bar_index >= self.warmup_bars:
            max_orders = int(self.caps.get("max_orders_per_bar") or 1)
            max_positions = int(self.caps.get("max_open_positions") or 1)
            max_weight_bps = int(self.caps.get("max_weight_bps") or 10000)
            min_notional = int(self.fees.get("min_notional_quote_units") or 0)
            fee_bps = int(self.fees.get("fee_bps") or 0)
            open_count = len(self.positions)
            for name in sorted(bars):
                if len(orders) >= max_orders:
                    break
                st = self.state[name]
                close = st.closes[-1] if st.closes else None
                if close is None:
                    continue
                held = self.positions.get(name, 0)
                if held > 0:
                    if st.exit_signal(close):
                        orders.append({
                            "side": "sell",
                            "client_id": self.next_client_id("sell"),
                            "symbol": name,
                            "qty": str(held),
                            "max_slippage_bps": MAX_SLIPPAGE_BPS,
                        })
                        st.entry = None
                        st.peak = None
                        st.breakeven_armed = False
                        st.cool_until = self.bar_index + COOLDOWN_BARS
                elif open_count < max_positions and st.entry_signal(self.bar_index):
                    # Full-size buy, capped by max weight and by cash
                    # (leaving room for the fill fee).
                    by_weight = equity * max_weight_bps // 10000
                    by_cash = cash * 10000 // (10000 + fee_bps) if fee_bps else cash
                    notional = by_weight if by_weight < by_cash else by_cash
                    if notional >= max(min_notional, 1):
                        orders.append({
                            "side": "buy",
                            "client_id": self.next_client_id("buy"),
                            "symbol": name,
                            "notional_quote_units": str(int(notional)),
                            "max_slippage_bps": MAX_SLIPPAGE_BPS,
                        })
        sys.stdout.write(json.dumps(
            {"type": "reply", "seq": seq, "orders": orders},
            separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def run(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "hello":
                self.handle_hello(msg)
            elif kind == "batch":
                self.handle_batch(msg)
            elif kind == "done":
                break


def main():
    Bot().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
