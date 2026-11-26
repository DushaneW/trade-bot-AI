"""SQLite persistence and the local key vault."""
from quorum.security import Vault, mask
from quorum.storage import Store


def test_settings_round_trip_any_json(tmp_path):
    store = Store(tmp_path / "q.db")
    assert store.setting("missing", 7) == 7
    store.set_setting("events", {"fills": False, "summary": True})
    assert store.setting("events") == {"fills": False, "summary": True}


def test_fills_come_back_newest_first_and_per_bot(tmp_path):
    store = Store(tmp_path / "q.db")
    for i, bot in enumerate(["a", "b", "a"]):
        store.add_fill({"bot_id": bot, "ts": i, "n": i})
    assert [f["n"] for f in store.fills("a")] == [2, 0]
    assert len(store.fills()) == 3
    assert store.fills(limit=1)[0]["n"] == 2


def test_equity_comes_back_oldest_first(tmp_path):
    store = Store(tmp_path / "q.db")
    for ts in (3, 1, 2):
        store.add_equity("bot", ts, ts * 10.0)
    assert store.equity("bot") == [(1, 10.0), (2, 20.0), (3, 30.0)]


def test_vault_encrypts_and_keeps_its_key(tmp_path):
    secret = Vault(tmp_path).seal({"api_key": "abc123", "secret": "s3cr3t"})
    assert "abc123" not in secret
    assert Vault(tmp_path).open(secret) == {"api_key": "abc123", "secret": "s3cr3t"}
    assert Vault(tmp_path).open("") == {}


def test_mask_shows_only_the_ends_of_a_key():
    assert mask("ABCDEFGHIJKL") == "ABCD…JKL"
    assert mask("short") == "•••"
    assert mask("") == ""
