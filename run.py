"""Convenience launcher: `python run.py` from anywhere, no install needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quorum.__main__ import main  # noqa: E402

if __name__ == "__main__":
    main()
