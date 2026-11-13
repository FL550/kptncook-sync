from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import time

from .config import ConfigurationError, Settings
from .locking import locked_state
from .state import StateError, StateStore
from .sync import sync_once


def _store(settings: Settings) -> StateStore:
    return StateStore(
        settings.state_path,
        settings.kptncook_account_id,
        settings.destination_identity,
    )


def _admin(args: argparse.Namespace, settings: Settings) -> int:
    store = _store(settings)
    with locked_state(settings.state_path):
        store.initialize()
        if args.command == "list-uncertain":
            for row in store.list_uncertain():
                print(f"{row.source_oid}\t{row.status}\t{row.updated_at}")
            return 0
        if args.command == "mark-imported":
            _validate_source_oid(args.source_oid)
            if args.destination_id < 1:
                raise ValueError("Destination ID must be positive")
            store.reconcile_imported(args.source_oid, args.destination_id)
            return 0
        if args.command == "clear-for-retry":
            _validate_source_oid(args.source_oid)
            if not args.confirm_no_remote_recipe:
                raise ValueError("Pass --confirm-no-remote-recipe only after checking Tandoor")
            store.reconcile_clear(args.source_oid)
            return 0
    return 2


def _validate_source_oid(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._~-]{1,256}", value):
        raise ValueError("Source OID must be a non-empty ID without whitespace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kptncook-tandoor-sync")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list-uncertain", help="List writes needing human reconciliation")
    mark = subparsers.add_parser("mark-imported", help="Resolve an uncertain write as imported")
    mark.add_argument("source_oid")
    mark.add_argument("destination_id", type=int)
    clear = subparsers.add_parser("clear-for-retry", help="Clear an uncertain write after verification")
    clear.add_argument("source_oid")
    clear.add_argument("--confirm-no-remote-recipe", action="store_true")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        settings = Settings.from_env()
        if args.command:
            raise SystemExit(_admin(args, settings))
        else:
            _run_schedule(settings)
    except (ConfigurationError, StateError, ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(2) from None


def _run_schedule(settings: Settings) -> None:
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        import importlib.metadata

        version = importlib.metadata.version("kptncook")
    except importlib.metadata.PackageNotFoundError:
        version = "missing"
    logging.getLogger(__name__).info("Starting with published kptncook version %s", version)
    while not stopping:
        try:
            sync_once(settings)
        except Exception:
            logging.getLogger(__name__).exception("Synchronization run failed")
        deadline = time.monotonic() + settings.interval_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))


if __name__ == "__main__":
    main()

