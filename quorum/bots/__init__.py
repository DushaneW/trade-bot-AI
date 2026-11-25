from .analysts import ANALYSTS
from .base import Analyst, Consensus, Intent, PositionView, Reading, Series, Strategy, Trader
from .traders import TRADERS

STRATEGIES: dict[str, type[Strategy]] = {**ANALYSTS, **TRADERS}


def catalog() -> list[dict]:
    return [
        {"key": cls.key, "title": cls.title, "persona": cls.persona, "role": cls.role,
         "summary": cls.summary, "defaults": cls.defaults}
        for cls in STRATEGIES.values()
    ]


__all__ = ["Analyst", "Consensus", "Intent", "PositionView", "Reading", "Series", "Strategy", "Trader",
           "ANALYSTS", "TRADERS", "STRATEGIES", "catalog"]
