"""SQLite persistence: accounts, bot configs, fills, equity snapshots."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    venue TEXT NOT NULL,
    sandbox INTEGER NOT NULL DEFAULT 1,
    secrets TEXT NOT NULL DEFAULT '',
    symbols TEXT NOT NULL DEFAULT '[]',
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    data TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS equity (
    bot_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reputation (
    analyst_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat(ts);
CREATE INDEX IF NOT EXISTS idx_fills_bot ON fills(bot_id, ts);
CREATE INDEX IF NOT EXISTS idx_equity_bot ON equity(bot_id, ts);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    # accounts
    def save_account(self, a: dict) -> None:
        self._exec(
            "INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?)",
            (a["id"], a["name"], a["market"], a["venue"], int(a["sandbox"]),
             a.get("secrets", ""), json.dumps(a.get("symbols", [])), a["created"]),
        )

    def accounts(self) -> list[dict]:
        out = []
        for r in self._exec("SELECT * FROM accounts ORDER BY created"):
            d = dict(r)
            d["symbols"] = json.loads(d["symbols"])
            d["sandbox"] = bool(d["sandbox"])
            out.append(d)
        return out

    def delete_account(self, account_id: str) -> None:
        self._exec("DELETE FROM accounts WHERE id=?", (account_id,))

    # bots
    def save_bot(self, bot_id: str, config: dict) -> None:
        self._exec("INSERT OR REPLACE INTO bots VALUES (?,?)", (bot_id, json.dumps(config)))

    def bots(self) -> list[dict]:
        return [json.loads(r["config"]) for r in self._exec("SELECT config FROM bots")]

    def delete_bot(self, bot_id: str) -> None:
        self._exec("DELETE FROM bots WHERE id=?", (bot_id,))

    # fills & equity
    def add_fill(self, fill: dict) -> None:
        self._exec("INSERT INTO fills (bot_id, data, ts) VALUES (?,?,?)",
                   (fill["bot_id"], json.dumps(fill), fill["ts"]))

    def fills(self, bot_id: str | None = None, limit: int = 200) -> list[dict]:
        if bot_id:
            rows = self._exec("SELECT data FROM fills WHERE bot_id=? ORDER BY ts DESC LIMIT ?", (bot_id, limit))
        else:
            rows = self._exec("SELECT data FROM fills ORDER BY ts DESC LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in rows]

    def add_equity(self, bot_id: str, ts: int, value: float) -> None:
        self._exec("INSERT INTO equity VALUES (?,?,?)", (bot_id, ts, value))

    def equity(self, bot_id: str, limit: int = 400) -> list[tuple[int, float]]:
        rows = self._exec("SELECT ts, value FROM equity WHERE bot_id=? ORDER BY ts DESC LIMIT ?", (bot_id, limit))
        return [(r["ts"], r["value"]) for r in reversed(rows)]

    # lead chat
    def save_chat(self, msg: dict) -> None:
        self._exec("INSERT OR REPLACE INTO chat VALUES (?,?,?)", (msg["id"], msg["ts"], json.dumps(msg)))

    def chat(self, limit: int = 100) -> list[dict]:
        rows = self._exec("SELECT data FROM chat ORDER BY ts DESC LIMIT ?", (limit,))
        return [json.loads(r["data"]) for r in reversed(rows)]

    def chat_message(self, msg_id: str) -> dict | None:
        rows = self._exec("SELECT data FROM chat WHERE id=?", (msg_id,))
        return json.loads(rows[0]["data"]) if rows else None

    # settings
    def setting(self, key: str, default: Any = None) -> Any:
        rows = self._exec("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    # reputation
    def save_reputation(self, analyst_id: str, data: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO reputation VALUES (?,?)", (analyst_id, json.dumps(data)))

    def reputations(self) -> dict[str, dict]:
        return {r["analyst_id"]: json.loads(r["data"]) for r in self._exec("SELECT * FROM reputation")}
