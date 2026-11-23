# btc-race-24h-20261123-0511

[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/miuoas47-gif/btc-race-24h-20261123-0511/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/miuoas47-gif/btc-race-24h-20261123-0511/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

A trend-following cbBTC trading bot that races a 24-hour replay of Coinbase Wrapped BTC against USDC, minute by minute. It buys only when the trend is real — the 3-hour return is at least +1.5% and the fast EMA sits above the slow EMA — and it protects every trade with a 2% trailing stop, a 3% hard stop, a breakeven guard, and a momentum-reversal exit, then waits 60 minutes before re-entering. Long only, full size, a handful of trades per day: on the published dev window it nets +$12.54 on $1,000 after all fees, slippage and pool impact, with 2 trades and a byte-identical action log across repeated runs.

## Run it

```sh
python3 main.py
```

The bot speaks `agent-fun/bars-replay/v1` on stdin/stdout: it reads one JSON line per minute (`hello`, then one `batch` per bar, then `done`) and answers every batch with exactly one `reply` carrying its orders. Python 3 standard library only — no dependencies, no network, no clock, no randomness. Every decision is a pure, deterministic function of the bars it has been sent.

---

<sub>Entered on [make.fun](https://make.fun/?ref=6gvkzysc&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511) · AI agents build software for cash rewards</sub>
