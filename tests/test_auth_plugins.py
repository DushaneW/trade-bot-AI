"""The dashboard password and strategy plugins."""
import shutil
from pathlib import Path

from quorum.auth import Gate
from quorum.bots import ANALYSTS, STRATEGIES
from quorum.plugins import load_plugins

EXAMPLE = Path(__file__).parents[1] / "strategies" / "_example_analyst.py"


def test_gate_is_open_without_a_password(tmp_path):
    gate = Gate(tmp_path, password="")
    assert not gate.enabled and gate.allows(None)


async def test_gate_checks_the_password_and_the_cookie(tmp_path):
    gate = Gate(tmp_path, password="hunter2")
    assert not gate.allows(None) and not gate.allows("forged")
    assert gate.allows(gate.token())
    assert await gate.check_password("hunter2")


def test_changing_the_password_signs_everyone_out(tmp_path):
    (tmp_path / "secret.key").write_bytes(b"k" * 44)
    old = Gate(tmp_path, password="one").token()
    assert not Gate(tmp_path, password="two").allows(old)


def test_underscore_files_stay_inactive(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "_example_analyst.py")
    assert load_plugins(tmp_path) == ([], [])


def test_a_plugin_joins_the_catalog(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "gap_watch.py")
    try:
        loaded, errors = load_plugins(tmp_path)
        assert loaded == ["gap_watch"] and errors == []
        assert ANALYSTS["gap_watch"].plugin == "gap_watch.py"
    finally:
        ANALYSTS.pop("gap_watch", None)
        STRATEGIES.pop("gap_watch", None)


def test_broken_or_clashing_plugins_are_reported_not_fatal(tmp_path):
    (tmp_path / "broken.py").write_text("import not_a_module\n")
    (tmp_path / "clash.py").write_text(
        "from quorum.bots import Analyst\n"
        "class Mine(Analyst):\n"
        "    key, title, persona, summary = 'trend', 'T', 'P', 's'\n"
        "    def read(self, s):\n        return None\n")
    (tmp_path / "empty.py").write_text("x = 1\n")
    loaded, errors = load_plugins(tmp_path)
    assert loaded == []
    assert any("broken.py" in e for e in errors)
    assert any("already taken" in e for e in errors)
    assert any("no Analyst or Trader" in e for e in errors)
    assert ANALYSTS["trend"].__module__ == "quorum.bots.analysts"


def test_missing_folder_is_fine(tmp_path):
    assert load_plugins(tmp_path / "nope") == ([], [])
