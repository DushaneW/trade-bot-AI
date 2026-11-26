"""What the owner reads must match what the code does."""
import re
from pathlib import Path

import pytest

from quorum.risk import PROFILES

README = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")


def pct(v: float) -> str:
    return f"{v * 100:g}%"


@pytest.mark.parametrize("name", list(PROFILES))
def test_profile_hints_quote_the_real_numbers(name):
    p = PROFILES[name]
    hint, limits = p["hint"], p["limits"]
    assert pct(limits.stop_loss_pct) + " stop loss" in hint
    for number in re.findall(r"(\d+)% drawdown", hint):
        assert float(number) / 100 == limits.max_drawdown_pct


@pytest.mark.parametrize("name", list(PROFILES))
def test_readme_risk_table_matches_the_profiles(name):
    p = PROFILES[name]
    row = next(line for line in README.splitlines() if line.startswith(f"| **{p['label']}** |"))
    cells = [c.strip() for c in row.strip("|").split("|")][1:]
    lim = p["limits"]
    assert cells == [pct(lim.stop_loss_pct), pct(lim.max_position_pct), pct(lim.daily_loss_pct),
                     pct(lim.max_drawdown_pct)]


def test_readme_states_the_correlation_caps_of_every_profile():
    caps = [pct(PROFILES[k]["limits"].max_correlated_pct) for k in ("careful", "balanced", "bold")]
    assert f"{caps[0]}, {caps[1]} or {caps[2]}" in README
