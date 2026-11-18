# ESOPS — Polymarket Esports Trading Terminal

A self-hosted trading terminal for Polymarket's esports markets (Counter-Strike,
League of Legends, Valorant, Dota 2, Rocket League…), with a quant signal engine
and a paper-trading account for testing the signals risk-free.

**Zero dependencies** — pure Python 3 stdlib. No pip install.

## Quickstart

```bash
git clone https://github.com/Tjfadix/polymarket-esports-terminal
cd polymarket-esports-terminal
python3 -m server.main          # → http://127.0.0.1:8080
```

Upstream Polymarket APIs are public and need no keys.

**No outbound access from your machine?** Run the deterministic demo feed:

```bash
PM_DEMO=1 python3 -m server.main
```

### Configuration (env vars)

| Variable | Default | Meaning |
|---|---|---|
| `PM_PORT` / `PM_HOST` | `8080` / `127.0.0.1` | listen address |
| `PM_TAGS` | see `server/config.py` | Gamma tag slugs to scan |
| `PM_FEED_SECS` | `25` | event scan cadence |
| `PM_BOOK_SECS` | `4` | order-book TTL |
| `PM_TOP_N` | `30` | markets enriched per refresh cycle |
| `PM_PAPER_CASH` | `10000` | paper account starting USDC |
| `PM_PAPER_PATH` | `data/paper_account.json` | paper ledger file |

Tests: `python3 -m unittest discover -s tests`

## The signal stack

Every market is graded on its **YES** token, then mapped to the cheaper
actionable side via outcome parity (buying NO = selling YES at `1 − bid`).

| Weight | Factor | Read |
|---|---|---|
| 30% | **Edge / model** | recency-weighted median of the taker tape (robust fair value) minus the after-fee entry touch |
| 25% | **Book imbalance** | near-touch depth skew of the **merged** book — native YES book joined with the mirrored NO book through minting parity (`1 = YES + NO`) |
| 25% | **Taker flow** | recency-weighted notional skew of the last hour of prints, 15-min half-life |
| 20% | **Momentum** | 5-min vs 45-min EWA divergence of the 1-minute price series, vol-normalized |

- **Volume shock** — 24h trading rate vs the past week's daily average; boosts conviction up to 2× and is flagged on the board.
- **Liquidity gate (0–100)** — spread tightness + $ depth + activity. Below 40, signals are masked as noise rather than shown as trades.
- **Conviction** — |composite| clamped 0–100. Tiers: ≥75 `STRONG BUY`, ≥45 `BUY`, ≥30 `WATCH`.
- **Sizing** — ½-Kelly fraction per signal, capped at 25% of bankroll.
- **Fees** — each market's own taker schedule (esports ≈ 3%, scaled by `min(p, 1−p)` per share) is deducted before edge is reported.

## Terminal layout

- **BOARD** — all active esports markets sorted by conviction: entry, model fair,
  net edge, ½-Kelly, book/flow/momentum gauges, 24h volume with shock flag,
  liquidity score, 6-hour sparkline. Filters: game, 24h volume, text, "only actionable".
- **Detail drawer** — signal stack bars, verdict with reasoning line, YES book
  ladder, taker tape, and a paper ticket with live slippage preview (fills walk
  the real book).
- **PAPER** — $10k simulated account: positions marked to mid, realized/unrealized
  P&L, fill history, one-click close. Ledger persists to disk.

## Architecture

```
server/
  main.py        HTTP server, refresher thread, API routing
  polymarket.py  Gamma events scan + CLOB books + Data-API tape (TTL-cached)
  signals.py     pure signal math (unit-tested)
  paper.py       paper execution & accounting (unit-tested)
  demo_data.py   deterministic synthetic feed (PM_DEMO=1)
  upstream.py    JSON HTTP client
static/          terminal UI (vanilla JS/CSS, dark trading-desk theme)
tests/           31 unit tests
```

API surface: `GET /api/board`, `GET /api/market/<conditionId>`,
`GET /api/paper/account`, `POST /api/paper/order`,
`POST /api/paper/reset`, `GET /api/health`.

## Notes & limits

- Signals are microstructure-based (tape, book, momentum). They are strongest on
  liquid event markets and will correctly mask themselves on stale/thin books.
- `data-api.polymarket.com` tape coverage varies by market; when absent, edge
  falls back to book-only factors and conviction will read lower.
- Paper fills model the live book plus taker fees; they are not a guarantee of
  real fills.
- **Research tool, not financial advice.** Prediction markets can resolve fast
  and reprice violently. This terminal only places paper orders.
