# Contributing

Thanks for wanting to help. Quorum moves real money for the people who run it, so the bar is simple:
every change should be easy to read, tested, and honest about what it does.

## Getting set up

```bash
git clone https://github.com/DushaneW/trade-bot-AI.git
cd trade-bot-AI
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the checks before you open a pull request. CI runs the same ones on Linux, Windows and macOS.

```bash
ruff check quorum tests strategies
pytest -q
```

The tests use the built-in simulated market, so they need no network and no exchange keys. The full suite takes
about half a minute; most of that is the backtest tests replaying history.

## Where things live

| Folder | What it holds |
| --- | --- |
| `quorum/engine.py` | the main loop: prices, analysts, the vote, traders, risk, orders, backup stops |
| `quorum/bots/` | analysts and traders |
| `quorum/connectors/` | exchanges and brokers, plus the simulator |
| `quorum/learning.py` | how analysts are scored and weighted |
| `quorum/risk.py` | every limit an order passes through |
| `quorum/web/` | the dashboard: plain HTML, CSS and JavaScript, no build step |
| `tests/` | one test file per module |

[docs/architecture.md](docs/architecture.md) explains how a candle turns into an order.

## Adding an analyst or a trader

The quickest way is a plugin in `strategies/`, which needs no change to Quorum itself. See
[docs/strategies.md](docs/strategies.md). If you think it belongs in Quorum for everyone, move it into
`quorum/bots/`, add it to `ANALYSTS` or `TRADERS`, and add tests that show it on a market shaped to make its
point, like the ones in `tests/test_analysts.py`.

## Style

- Python 3.10 or newer, 120 columns, imports sorted by ruff.
- Names that say what something is. Comments only where the code cannot say why.
- English everywhere: code, UI text, docs. No long dashes in text the owner reads.
- User-facing errors are `ConfigError` with a sentence the owner can act on.

## Things we will not merge

- Anything that can withdraw funds or asks for keys that can.
- Anything that places orders without the owner having pressed Start.
- Promises of profit in the UI, the README or the Chair's answers.
- Telemetry, analytics or calls home.

## Commits and pull requests

Small commits with a message that says what changed and why. One topic per pull request. If you change behaviour
the owner can see, mention it in `CHANGELOG.md` under *Unreleased*.
