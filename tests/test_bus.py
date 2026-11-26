"""The in-process message bus between the engine, the Chair and the dashboard."""
from quorum.bus import Bus
from quorum.models import Fill, Mode, Side


def test_history_is_bounded_and_skips_unkept_messages():
    bus = Bus(history=3)
    for i in range(5):
        bus.publish("signal", {"n": i})
    bus.publish("bar", {"n": 99}, keep=False)
    assert [m["data"]["n"] for m in bus.history] == [2, 3, 4]


def test_topic_and_wildcard_subscribers_both_hear():
    bus, heard = Bus(), []
    bus.subscribe("fill", lambda m: heard.append(("fill", m["data"]["n"])))
    bus.subscribe("*", lambda m: heard.append(("*", m["topic"])))
    bus.publish("fill", {"n": 1})
    bus.publish("signal", {"n": 2})
    assert heard == [("fill", 1), ("*", "fill"), ("*", "signal")]


def test_a_broken_subscriber_does_not_stop_the_others():
    bus, heard = Bus(), []

    def broken(_):
        raise ValueError("boom")
    bus.subscribe("x", broken)
    bus.subscribe("x", lambda m: heard.append(m["data"]))
    bus.publish("x", {"ok": True})
    assert heard == [{"ok": True}]


async def test_streams_receive_and_slow_clients_do_not_block():
    bus = Bus()
    q = bus.stream()
    for i in range(1005):
        bus.publish("signal", {"n": i})
    assert q.qsize() == 1000  # the rest is dropped for that client; it resyncs from history
    bus.unstream(q)
    bus.publish("signal", {"n": -1})
    assert q.qsize() == 1000


def test_fills_serialise_with_plain_strings():
    d = Fill("o", "b", "acc:X", Side.BUY, 1.0, 2.0, 0.01, Mode.LIVE, "why").to_dict()
    assert d["side"] == "buy" and d["mode"] == "live" and d["pnl"] == 0.0
