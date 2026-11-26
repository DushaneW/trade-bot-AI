# How Quorum works

This page follows one candle from the exchange to an order, then covers the parts around that loop. File names
are relative to `quorum/`.

## The loop

`engine.py` runs one asyncio loop. Every half second it looks at each connected account and, when the account's
poll interval has passed, does one of two things:

- **The council is running:** fetch the latest candles for every watched market and, if a new candle has closed,
  run a full tick (below).
- **The council is stopped:** fetch only the markets with open positions and check their stop losses. Nothing else
  happens until the owner presses Start.

Every 20 seconds, running or not, it also checks the backup stops resting on the exchange (see *Backup stops*).

## One tick

1. **Series.** The closed candles become a `Series` (high, low, close, volume arrays, oldest first). On crypto,
   `connector.extras()` adds funding, open interest and order-book data when an analyst needs it.
2. **Regime and correlation.** The market is tagged `trend` or `chop` from its efficiency ratio, and the
   correlation between the account's markets is refreshed.
3. **Scoring.** `learning.Council.score()` resolves predictions made 8 bars ago against the current price and
   updates each analyst's record: overall, for that market, and for that regime.
4. **Analysts.** Each analyst reads the markets it watches and returns a `Reading`: a kind, a direction from -1 to
   +1, a confidence and a note. The council stores it as a prediction and publishes it on the bus.
5. **The vote.** For each market, `Council.consensus()` weighs every fresh reading by how often that analyst has
   been right *in this market and this regime*, shrunk toward its overall record until it has history of its
   own. Volatility warnings go into a separate `risk_off` score.
6. **Traders.** Each trader looks at its own setup and the vote and may return an `Intent`: buy, sell or exit,
   with a size as a fraction of its equity. The engine scales the size by the risk profile and by how united the
   vote is.
7. **Risk.** `risk.RiskManager.review()` turns the intent into a quantity or refuses it: position cap, cash,
   correlated holdings, exchange minimum, the kill switch. Exits are always allowed.
8. **Order.** The connector places a market order. The fill is booked in that trader's own `Ledger`, saved, and
   published.
9. **Backup stop.** After every fill the engine makes sure one stop order rests on the exchange for the trader's
   position, sized to it and priced just past the trader's own stop loss.

## Who owns what money

Several traders can share one exchange account. Each has a `Ledger` with its own budget, cash and positions, and
the risk manager only ever looks at that ledger. Nothing a trader does can spend another trader's budget or the
part of the account the owner kept back.

## Backup stops

The trader's own stop loss runs inside Quorum, which only works while Quorum runs. So after every fill the engine
also places a stop order on the exchange, 0.5% past the trader's stop. Normally the trader's stop fires first and
the backup is cancelled before the sale (a resting stop holds the coins, so selling would otherwise fail). If the
computer was off and the exchange filled the backup, the next check books that sale in the ledger.

## The Chair

`lead.py` is the owner's contact. It runs setup through `onboarding.py`, answers questions, sends briefings while
the council runs and proposes changes the owner applies with a click. Without an Anthropic key it answers from
rules; with one, Claude writes the answer from the same facts.

## Around the loop

| File | Job |
| --- | --- |
| `bus.py` | in-process messages; the dashboard receives them over a WebSocket |
| `storage.py` | SQLite: accounts, bots, fills, equity, reputations, chat, settings |
| `security.py` | Fernet encryption for keys, with the key in `data/secret.key` |
| `backtest.py` | replays history through the same analysts, vote, traders and risk checks |
| `notify.py` | Telegram and Discord messages, Telegram commands |
| `plugins.py` | loads analysts and traders from the `strategies` folder |
| `auth.py` | the optional dashboard password |
| `api.py` | FastAPI routes and the static dashboard |
