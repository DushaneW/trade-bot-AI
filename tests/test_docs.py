"""The code in the docs must keep working, or the docs are lying."""
import re
from pathlib import Path

from quorum.bots import ANALYSTS, STRATEGIES, TRADERS
from quorum.plugins import load_plugins

ROOT = Path(__file__).parents[1]


def test_plugin_guide_examples_load(tmp_path):
    text = (ROOT / "docs" / "strategies.md").read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```python\n(.*?)```", text, re.S) if "class " in b]
    assert len(blocks) == 2
    for i, block in enumerate(blocks):
        (tmp_path / f"example_{i}.py").write_text(block, encoding="utf-8")
    try:
        loaded, errors = load_plugins(tmp_path)
        assert errors == []
        assert sorted(loaded) == ["round_numbers", "vote_and_dip"]
    finally:
        for key in ("round_numbers", "vote_and_dip"):
            ANALYSTS.pop(key, None)
            TRADERS.pop(key, None)
            STRATEGIES.pop(key, None)


def test_readme_team_table_names_every_built_in_analyst():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for cls in ANALYSTS.values():
        if not cls.plugin:
            assert f"**{cls.persona}**" in readme, cls.persona


def test_no_long_dashes_in_user_facing_text():
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "CONTRIBUTING.md", *ROOT.glob("docs/*.md"),
             ROOT / "quorum" / "web" / "app.js"]
    for f in files:
        text = f.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text, f.name
