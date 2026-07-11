from __future__ import annotations

import argparse
import json

from .service import get_service
from .worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(prog="hermes-live-clipper")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker")
    sub.add_parser("status")
    add = sub.add_parser("add")
    add.add_argument("url")
    add.add_argument("--from-start", action="store_true")
    args = parser.parse_args()
    service = get_service()
    if args.command == "worker":
        Worker(service).run_forever()
    elif args.command == "status":
        print(json.dumps(service.status(), indent=2, default=str))
    elif args.command == "add":
        print(
            json.dumps(
                service.add_job(args.url, "from_start" if args.from_start else "live_edge"),
                indent=2,
            )
        )
