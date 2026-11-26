# Writing your own analyst or trader

You can add bots to Quorum without touching its code. Put a Python file in the `strategies` folder and restart.

- A new **analyst** joins every existing team. The council scores its calls like everyone else's, so if it is no
  good, its vote weight shrinks on its own.
- A new **trader** appears in the hiring menu and in backtests.

Settings shows which files loaded and why any failed. Files starting with `_` are skipped, which is how the bundled
example stays inactive.

> Plugins are ordinary Python running with Quorum's permissions, including access to your exchange keys. Only use
> files you have read and understood.

## An analyst

```python
from quorum.bots import Analyst, Reading, Series


class RoundNumbers(Analyst):
    key = "round_numbers"          # unique, never change it once bots use it
    title = "Round Numbers"
    persona = "Tally"              # the name it introduces itself with
    summary = "Treats round numbers as support from above and resistance from below."
    defaults = {"step": 1000}      # tunable numbers, shown on the bot's page

    def read(self, s: Series) -> Reading | None:
        step = self.p["step"]
        level = round(s.price / step) * step
        distance = (s.price - level) / s.price
        if abs(distance) > 0.003:
            return None            # nothing to say
        return Reading("structure", -0.3 if distance < 0 else 0.3, 0.3,
                       f"Price within {abs(distance):.2%} of {level:,.0f}")
```

`read()` gets a `Series` with numpy arrays `s.high`, `s.low`, `s.close` and `s.volume`, oldest first, and
`s.price` for the last close. Return `None` when you have no view.

A `Reading` has:

| Field | Meaning |
| --- | --- |
| `kind` | what you measure: `trend`, `momentum`, `risk`, `flow`, `structure`, `breadth` or `positioning` |
| `direction` | -1 bearish to +1 bullish. For `risk`, -1 means "turbulent, size down" |
| `confidence` | 0 to 1 |
| `note` | one short sentence the owner will read |

Readings with a direction near zero are not scored. Everything else is checked 8 bars later.

**Several markets at once.** Override `read_all(series)`, which gets a dict of market key to `Series` and returns a
dict of market key to `Reading`. See `CorrelationAnalyst` in `quorum/bots/analysts.py`.

**More history.** Set `lookback = 720` to be shown more bars, and `min_bars` for the fewest you can work with.

**Crypto only, or live data.** Set `markets = ("crypto",)` to be hired only on crypto accounts. Set
`needs_extras = True` to receive `s.extra` with `funding_rate`, `oi_change` and `book_imbalance` where the
exchange provides them; such analysts stay silent in backtests.

## A trader

```python
from quorum.bots import Consensus, Intent, PositionView, Series, Trader
from quorum.models import Side


class VoteAndDip(Trader):
    key = "vote_and_dip"
    title = "Vote and Dip"
    persona = "Picker"
    summary = "Buys a 2% dip when the council leans up; sells when the vote turns."
    defaults = {"dip": 0.02, "size": 0.3}
    tunable = {"dip": [0.01, 0.02, 0.04]}   # values walk-forward backtests may try

    def decide(self, s: Series, pos: PositionView, council: Consensus) -> Intent | None:
        recent_high = s.high[-20:].max()
        if pos.flat and council.direction > 0.2 and s.price < recent_high * (1 - self.p["dip"]):
            return Intent(Side.BUY, self.p["size"], f"Dip of {1 - s.price / recent_high:.1%} with the vote at "
                                                     f"{council.direction:+.2f}")
        if pos.long and council.direction < 0:
            return Intent(Side.SELL, 0, "The vote turned", exit=True)
        return None
```

A trader never sizes in money. `size` is the share of its equity it wants to commit; the risk profile, the vote's
unity and the risk manager decide the final amount, and the risk manager can refuse. Exits are always allowed.

## Testing it

Run a backtest from the dashboard, or in code:

```python
from quorum.backtest import BacktestSpec, run_backtest
```

The tests in `tests/test_analysts.py` and `tests/test_traders.py` show how to check a bot on a market shaped to
make its point, without any exchange.
