"""An example analyst plugin. Copy this file to a name without the leading underscore to switch it on.

    cp strategies/_example_analyst.py strategies/gap_watch.py

Restart Quorum and "Gap" joins the team. The council scores its calls like
everyone else's, so if it is no good, its vote weight shrinks on its own.

The contract:
  * subclass Analyst (or Trader, see quorum/bots/traders.py for examples),
  * give it a unique `key`, a `title`, a `persona` (the name it introduces itself with) and a `summary`,
  * `read(s)` gets a Series with numpy arrays `s.high`, `s.low`, `s.close`, `s.volume`, oldest first,
    and returns a Reading or None:
        kind        what you measure: "trend", "momentum", "risk", "flow", "structure", "breadth", "positioning"
        direction   -1 bearish .. +1 bullish (for kind "risk": -1 means "turbulent, size down")
        confidence  0 .. 1
        note        one short sentence the owner will read
  * `defaults` holds tunable numbers; they show up on the bot's page.
"""
from quorum.bots import Analyst, Reading, Series


class GapAnalyst(Analyst):
    key = "gap_watch"
    title = "Gap Watch"
    persona = "Gap"
    summary = "Notices when a bar opens far from the previous close and expects part of the gap to fill."
    defaults = {"min_gap": 0.004}

    def read(self, s: Series) -> Reading | None:
        prev_close, last_close = float(s.close[-2]), float(s.close[-1])
        # The Series carries no opens; the previous close against this bar's range is close enough.
        gap = (float(s.low[-1]) - prev_close) / prev_close if s.low[-1] > prev_close else \
              (float(s.high[-1]) - prev_close) / prev_close if s.high[-1] < prev_close else 0.0
        if abs(gap) < self.p["min_gap"]:
            return None
        direction = -0.4 if gap > 0 else 0.4  # gaps up tend to sag back, gaps down tend to bounce
        return Reading("structure", direction, 0.35,
                       f"Gapped {gap:+.2%} from {prev_close:.6g}, now {last_close:.6g}: expecting a partial fill")
