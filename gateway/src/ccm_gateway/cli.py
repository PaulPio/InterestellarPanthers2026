"""Command line for the gateway: ``ccm-gateway ingest | analytics | api | public``."""

from __future__ import annotations

import argparse
import importlib
import logging

from ccm_gateway.config import Settings, load_settings
from ccm_gateway.db import connect


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ccm-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="serve the dashboard API")
    sub.add_parser("ingest", help="subscribe to MQTT and store messages")
    sub.add_parser("analytics", help="keep baselines, risk cards and the status snapshot")
    sub.add_parser("public", help="serve the read-only API the public website reads")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if args.command == "api":
        _serve(settings)
    elif args.command == "ingest":
        _ingest(settings)
    elif args.command == "analytics":
        _analytics(settings)
    elif args.command == "public":
        _public(settings)
    else:
        raise SystemExit(2)


def _serve(settings: Settings) -> None:
    from ccm_gateway.api import create_app

    uvicorn = importlib.import_module("uvicorn")
    conn = connect(settings.db_path)
    app = create_app(conn, settings)
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


def _public(settings: Settings) -> None:
    from ccm_gateway.public import create_public_app

    if not settings.public_key:
        raise SystemExit("set CCM_PUBLIC_KEY before starting the public API")
    uvicorn = importlib.import_module("uvicorn")
    conn = connect(settings.db_path)
    app = create_public_app(conn, settings, settings.public_key)
    # Loopback only: Tailscale Funnel connects from this machine, nothing else should.
    uvicorn.run(app, host="127.0.0.1", port=settings.public_port)


def _analytics(settings: Settings) -> None:
    from ccm_gateway.analytics import run

    run(settings)


def _ingest(settings: Settings) -> None:
    """Import ingest only when the command runs. StormyRaven owns that module."""
    module = importlib.import_module("ccm_gateway.ingest")
    module.run(settings)


if __name__ == "__main__":
    main()
