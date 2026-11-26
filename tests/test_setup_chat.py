"""The Chair's first-run setup and its start and stop commands, with a simulated account standing in for Binance."""
import pytest

from quorum.engine import ConfigError
from quorum.lead import Lead
from tests.conftest import SYMBOLS


def open_prompt(lead):
    return next(m for m in reversed(lead.history(200)) if m.get("kind") == "prompt" and not m.get("answered"))


async def answer(lead, value):
    await lead.answer_prompt(open_prompt(lead)["id"], value)


@pytest.fixture
async def lead(engine):
    lead = Lead(engine, engine.store, engine.vault, engine.bus)
    await lead.start()
    real_add = engine.add_account

    async def as_sim(data, require_ok=False):
        return await real_add({**data, "venue": "sim", "symbols": SYMBOLS}, require_ok)
    engine.add_account = as_sim
    lead.onboarding._available = lambda acc: SYMBOLS
    yield lead
    await lead.stop()


async def walk_to_budget(lead):
    await answer(lead, {"choice": "crypto"})
    await answer(lead, {"choice": "binance"})
    await answer(lead, {"api_key": "k" * 20, "secret": "s", "sandbox": True})
    await answer(lead, {"choices": SYMBOLS})


async def test_setup_asks_one_thing_at_a_time_and_waits_for_start(lead, engine):
    assert open_prompt(lead)["step"] == "market"
    await walk_to_budget(lead)
    with pytest.raises(ConfigError, match="confirm"):
        await answer(lead, {"budget": 100, "confirm": False})
    await answer(lead, {"budget": 100, "confirm": True})
    team = open_prompt(lead)
    assert team["step"] == "team" and len(team["prompt"]["selected"]) == 4  # 100 USDT fits four traders
    await answer(lead, {"choices": team["prompt"]["selected"]})
    await answer(lead, {"choice": "careful"})
    traders = [b for b in engine.bots.values() if b.is_trader]
    assert {b.config["budget"] for b in traders} == {25.0}
    assert {b.config["profile"] for b in traders} == {"careful"}
    assert not engine.running
    await answer(lead, {"choice": "start"})
    assert engine.running and lead.onboarding.done


async def test_free_text_during_setup_is_redirected(lead):
    await lead.ask("buy some bitcoin")
    assert "finish the setup" in lead.history(5)[-1]["text"]


async def test_an_answered_question_cannot_be_answered_again(lead):
    first = open_prompt(lead)
    await answer(lead, {"choice": "crypto"})
    with pytest.raises(ConfigError, match="already been answered"):
        await lead.answer_prompt(first["id"], {"choice": "stocks"})
