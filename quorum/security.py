"""Local encryption for API keys.

Keys are encrypted with a Fernet key stored next to the database
(data/secret.key). Nothing ever leaves the user's machine.
"""
from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file = data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
        self._f = Fernet(key_file.read_bytes())

    def seal(self, secrets: dict) -> str:
        return self._f.encrypt(json.dumps(secrets).encode()).decode()

    def open(self, token: str) -> dict:
        if not token:
            return {}
        return json.loads(self._f.decrypt(token.encode()).decode())


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-3:] if len(value) > 9 else "•••"
