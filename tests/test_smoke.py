"""The package imports and the fixtures build a working team."""
from quorum import __version__


def test_version_is_set():
    assert __version__.count(".") == 2


async def test_team_fixture_loads_prices(team):
    engine, acc = team
    assert len([k for k in engine.prices if k.startswith(acc.id)]) == 3
    assert engine.running
