"""Command line entry point.

    python -m gateway.trace verify traces/samples/demo-12-steps.jsonl

Exit codes: 0 = chain intact, 1 = verification failed, 2 = usage error.
The non-zero code matters: it is what a CI step or a demo script checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .verify import verify_path

EXIT_OK = 0
EXIT_FAIL = 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway.trace",
        description="Verify a hash-chained trace file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="check the chain and locate the first bad record")
    verify.add_argument("path", help="path to a .jsonl trace")
    verify.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    verify.add_argument(
        "--quiet", action="store_true", help="只输出退出码，不打印内容"
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "verify":
        try:
            result = verify_path(args.path)
        except FileNotFoundError:
            if not args.quiet:
                print(f"找不到文件: {args.path}", file=sys.stderr)
            return EXIT_FAIL

        if not args.quiet:
            if args.json:
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                marker = "PASS" if result.ok else "FAIL"
                print(f"[{marker}] {args.path}")
                print(f"  {result.summary()}")
                for note in result.notes:
                    print(f"  note: {note}")
        return EXIT_OK if result.ok else EXIT_FAIL

    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
