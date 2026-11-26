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


async def finished(lead):
    await walk_to_budget(lead)
    await answer(lead, {"budget": 300, "confirm": True})
    await answer(lead, {"choices": ["trend_rider", "breakout"]})
    await answer(lead, {"choice": "balanced"})
    await answer(lead, {"choice": "start"})


async def test_stop_asks_whether_to_keep_positions(lead, engine):
    await finished(lead)
    await lead.ask("stop")
    prompt = open_prompt(lead)
    assert prompt["step"] == "stop" and engine.running
    await answer(lead, {"choice": "keep"})
    assert not engine.running
    assert any("keep their stop losses" in m["text"] for m in lead.history(5))


async def test_start_and_already_running(lead, engine):
    await finished(lead)
    await lead.ask("start")
    assert any(m["text"] == "The council is already running." for m in lead.history(5))


async def test_questions_about_traders_get_a_rule_answer(lead, engine):
    await finished(lead)
    await lead.ask("How are my traders doing?")
    for job in list(lead._jobs):
        await job
    reply = lead.history(5)[-1]
    assert reply["kind"] == "answer" and "traders manage" in reply["text"]


async def test_briefing_warns_when_prices_stop_arriving(lead, engine):
    import time

    await finished(lead)
    acc = next(iter(engine.accounts.values()))
    acc.last_data = time.time() - 900
    items = lead._briefing_items()
    assert items[0]["verb"] == "Check" and "No prices from" in items[0]["text"]


async def test_briefing_proposes_pausing_a_losing_trader_and_the_owner_applies_it(lead, engine):
    await finished(lead)
    loser = next(b for b in engine.bots.values() if b.config["strategy"] == "trend_rider")
    loser.ledger.trades, loser.ledger.wins = 6, 1
    loser.ledger.cash -= loser.ledger.budget * 0.05
    msg = await lead.brief(force=True)
    pause = next(p for p in msg["proposals"] if p["action"]["type"] == "pause")
    assert pause["action"]["bot_id"] == loser.id
    lead.apply_proposal(msg["id"], pause["id"])
    assert not loser.config["enabled"]
    again = lead.apply_proposal(msg["id"], pause["id"])  # a second click changes nothing
    assert next(p for p in again["proposals"] if p["id"] == pause["id"])["applied"]


async def test_briefing_stays_quiet_when_nothing_changed(lead, engine):
    await finished(lead)
    loser = next(b for b in engine.bots.values() if b.config["strategy"] == "trend_rider")
    loser.ledger.trades, loser.ledger.wins = 6, 1
    loser.ledger.cash -= loser.ledger.budget * 0.05
    assert await lead.brief(force=False) is not None
    assert await lead.brief(force=False) is None


async def test_unknown_proposals_are_404s(lead):
    with pytest.raises(KeyError):
        lead.apply_proposal("nope", "nope")


async def test_setup_refuses_answers_that_make_no_sense(lead):
    with pytest.raises(ConfigError):
        await answer(lead, {"choice": "commodities"})
    await answer(lead, {"choice": "crypto"})
    with pytest.raises(ConfigError):
        await answer(lead, {"choice": "my-bank"})
    await answer(lead, {"choice": "binance"})
    await answer(lead, {"api_key": "k" * 20, "secret": "s", "sandbox": True})
    with pytest.raises(ConfigError):
        await answer(lead, {"choices": []})
    await answer(lead, {"choices": SYMBOLS})
    with pytest.raises(ConfigError):
        await answer(lead, {"budget": 1, "confirm": True})  # below what one trader needs
    await answer(lead, {"budget": 100, "confirm": True})
    with pytest.raises(ConfigError):
        await answer(lead, {"choices": []})


def test_the_testnet_box_only_appears_where_a_testnet_exists(lead):
    fields = lambda venue: {f["name"] for f in lead.onboarding._key_fields("crypto", venue)}  # noqa: E731
    assert "sandbox" in fields("binance")
    assert "sandbox" not in fields("kraken")
    assert "password" in fields("okx") and "password" not in fields("binance")
    forex = {f["name"] for f in lead.onboarding._key_fields("forex", "oanda")}
    assert forex == {"api_key", "account_id", "sandbox"}


async def test_asking_what_is_held_lists_positions_and_their_stops(lead, engine):
    from quorum.models import Side

    await finished(lead)

    async def ask_and_wait(text):
        await lead.ask(text)
        for job in list(lead._jobs):
            await job
        return next(m for m in reversed(lead.history(10)) if m["kind"] == "answer")

    assert (await ask_and_wait("What are we holding?"))["text"] == "Nothing is held right now."
    rider = next(b for b in engine.bots.values() if b.config["strategy"] == "trend_rider")
    acc = next(iter(engine.accounts.values()))
    await engine._tick_account(acc)
    key = f"{acc.id}:SOL/USDT"
    await engine._execute(rider, key, Side.BUY, 60 / engine.prices[key], "test")
    reply = await ask_and_wait("Which positions are open, and where are the stops?")
    assert reply["text"].startswith("1 open position")
    assert "SOL/USDT" in reply["items"][0]["text"] and "exchange stop at" in reply["items"][0]["text"]


async def test_questions_are_matched_to_the_markets_they_name(lead, engine):
    await finished(lead)
    await engine._tick_account(next(iter(engine.accounts.values())))  # the Chair matches against live prices
    found = lambda text: sorted(k.split(":", 1)[1] for k in lead._match_markets(text))  # noqa: E731
    assert found("What about BTC?") == ["BTC/USDT"]
    assert found("eth and sol, buy or wait") == ["ETH/USDT", "SOL/USDT"]
    assert found("how is usdt doing") == []  # the quote currency names no market
    assert found("tell me about the weather") == []
