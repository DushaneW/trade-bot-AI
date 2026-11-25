"""Strategy plugins: drop a .py file into the strategies folder and its bots join the catalog.

A plugin file defines one or more classes that subclass `quorum.bots.Analyst`
or `quorum.bots.Trader` and set a unique `key`. New analysts join every
existing team on the next start; new traders appear in the hiring menu.

Plugins are ordinary Python running with Quorum's permissions, so only
install files you have read or written yourself. Files starting with an
underscore are skipped, which is how the bundled example stays inactive.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path

from .bots import ANALYSTS, STRATEGIES, TRADERS, Analyst, Trader

log = logging.getLogger(__name__)


def load_plugins(folder: Path) -> tuple[list[str], list[str]]:
    """Import every plugin in `folder`. Returns (loaded strategy keys, human-readable errors)."""
    loaded: list[str] = []
    errors: list[str] = []
    if not folder.is_dir():
        return loaded, errors
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = f"quorum_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as exc:
            log.exception("Plugin %s failed to load", path.name)
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        found = 0
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != name or inspect.isabstract(cls) or not issubclass(cls, (Analyst, Trader)):
                continue
            key = getattr(cls, "key", None)
            if not key or not getattr(cls, "title", None) or not getattr(cls, "persona", None):
                errors.append(f"{path.name}: {cls.__name__} needs key, title and persona")
                continue
            if key in STRATEGIES:
                errors.append(f"{path.name}: {cls.__name__} uses the key '{key}', which is already taken")
                continue
            cls.plugin = path.name
            (ANALYSTS if issubclass(cls, Analyst) else TRADERS)[key] = cls
            STRATEGIES[key] = cls
            loaded.append(key)
            found += 1
        if not found and not any(e.startswith(path.name) for e in errors):
            errors.append(f"{path.name}: no Analyst or Trader class found")
    if loaded:
        log.info("Loaded strategy plugins: %s", ", ".join(loaded))
    return loaded, errors
