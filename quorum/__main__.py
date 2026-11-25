"""Run with:  python -m quorum  [--host 127.0.0.1] [--port 8420]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import webbrowser

import uvicorn

from .api import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="quorum", description="Self-hosted multi-bot trading council")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Interface to bind. Keep 127.0.0.1 unless you secure the network yourself.")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard on start")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("QUORUM_PASSWORD"):
        sys.exit(f"Refusing to listen on {args.host} without a password: anyone who can reach this port could "
                 "trade with your money. Set QUORUM_PASSWORD, or keep the default 127.0.0.1.")
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
