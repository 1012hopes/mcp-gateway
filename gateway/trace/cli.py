"""Command line entry point.

    python -m gateway.trace verify traces/samples/demo-12-steps.jsonl
    python -m gateway.trace render traces/runs/run-104d434f33a4.jsonl

Exit codes: 0 = chain intact (or page written), 1 = verification failed, 2 = the
path could not be read *or* the command line is wrong. The distinction is the
reason there are three: a CI gate that treats every non-zero code as "tampered"
would report a missing file as tampering, so "I could not read this" gets its own
code instead of borrowing exit 1. (It used to borrow it, and to answer a
directory with a traceback.) ``render`` returns 0 even for a broken chain -- a
broken trace is exactly the thing you want to look at, and refusing to draw it
would hide the evidence behind the verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .render import render_path
from .verify import verify_path

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gateway.trace",
        description="Verify a hash-chained trace file, or render it as one HTML page.",
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

    render = sub.add_parser("render", help="write a single-file HTML replay of a trace")
    render.add_argument("path", help="path to a .jsonl trace")
    render.add_argument(
        "-o", "--out", default=None, help="output .html (default: next to the trace)"
    )
    render.add_argument("--title", default=None, help="page title")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "verify":
        try:
            result = verify_path(args.path)
        except OSError as exc:
            # Missing, a directory, a permission denial, a path too long: the
            # user named something this tool cannot read. A traceback is not an
            # answer, and exit 1 would say "the chain is broken" -- a statement
            # about the file, made without having read it.
            if not args.quiet:
                reason = exc.strerror or str(exc)
                print(f"打不开这个路径: {args.path}（{reason}）", file=sys.stderr)
            return EXIT_USAGE

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

    if args.command == "render":
        try:
            out = render_path(args.path, args.out, title=args.title)
            result = verify_path(args.path)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            print(f"打不开这个路径: {args.path}（{reason}）", file=sys.stderr)
            return EXIT_USAGE
        print(f"写出 {out}（{out.stat().st_size} 字节，双击打开）")
        print(f"  链状态: {result.summary()}")
        return EXIT_OK

    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
