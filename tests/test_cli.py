"""The command line."""
import pytest

from quorum import __version__
from quorum.__main__ import parser


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as done:
        parser().parse_args(["--version"])
    assert done.value.code == 0
    assert capsys.readouterr().out.strip() == f"Quorum {__version__}"


def test_defaults_keep_the_dashboard_local():
    args = parser().parse_args([])
    assert (args.host, args.port, args.no_browser) == ("127.0.0.1", 8420, False)
