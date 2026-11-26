"""Telegram and Discord: what gets said, and who may give commands."""
import pytest

from quorum.notify import Notifier


class FakeHTTP:
    def __init__(self):
        self.sent = []

    async def post(self, url, json):
        self.sent.append((url, json))

        class R:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}
        return R()


@pytest.fixture
async def notifier(team):
    engine, _ = team
    from quorum.lead import Lead

    lead = Lead(engine, engine.store, engine.vault, engine.bus)
    await lead.start()
    n = Notifier(engine, lead, engine.store, engine.vault, engine.bus)
    n.http = FakeHTTP()
    yield n
    await lead.stop()


def test_fill_message_reads_like_a_sentence(notifier):
    text = notifier._describe("fill", {"bot_name": "Rider", "instrument_key": "a:ETH/USDT", "side": "buy",
                                       "qty": 0.02, "price": 2500, "pnl": 0, "reason": "Trend up"})
    assert text == "Rider bought 0.02 ETH/USDT at 2500 (50.00).\nTrend up"


def test_switched_off_events_stay_quiet(notifier):
    notifier.store.set_setting("notify_events", {"fills": False})
    fill = {"bot_name": "R", "instrument_key": "a:X", "side": "buy", "qty": 1, "price": 1, "reason": "x"}
    assert notifier._describe("fill", fill) is None
    stop = {**fill, "side": "sell", "reason": "Backup stop filled on the exchange"}
    assert notifier._describe("fill", stop) is not None  # stops are their own switch


def test_same_error_is_not_repeated(notifier):
    err = {"bot_name": "R", "message": "Order failed"}
    assert notifier._describe("error", err)
    assert notifier._describe("error", err) is None


def test_summary_mentions_the_team_and_holdings(notifier):
    text = notifier.summary()
    assert text.startswith("Team worth") and "Best trader" in text
