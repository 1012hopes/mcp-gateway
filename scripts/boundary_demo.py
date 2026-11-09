"""D10 demo: the same calls, with and without a run-boundary declaration.

The claim is narrow enough to be checked by eye:

    An agent starts a second topic the way it starts any topic -- by looking at
    the schema. If nothing says the topic changed, that first call is a repeat of
    the first topic's first call, and the gateway halts a healthy run.

Two processes, identical tool calls, one difference: whether the caller declared
which task each call belongs to. The undeclared one ends ``HALTED_DUPLICATE`` on
its second call; the declared one runs three separate runs, each with its own
trace file, and the follow-up completes without re-reading anything because it
inherited the evidence.

What the assertions are careful about
-------------------------------------
Every claim is read off the trace, not off the client's memory: the run count and
the ``task_id`` come from the records, ``evidence_added`` proves whether a run
re-learned a fact or inherited it, and ``parent_run_id`` proves the inheritance
lineage. A demo that printed what it intended to do would be worth nothing --
this project's whole subject is the gap between the two.

    python scripts/boundary_demo.py [--run-id ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from gateway.trace.verify import verify_path  # noqa: E402
from gateway.wire import KIND_HALT, parse_notice  # noqa: E402

from agent.loop import gateway_params  # noqa: E402

SEPARATOR = "=" * 78


@dataclass
class Call:
    tool: str
    args: Dict[str, Any]
    #: ``_meta.gateway`` for this call. ``None`` sends nothing at all.
    declaration: Optional[Dict[str, Any]] = None
    note: str = ""

    def meta(self) -> Optional[Dict[str, Any]]:
        return {"gateway": self.declaration} if self.declaration else None


@dataclass
class Outcome:
    text: str
    is_error: bool
    notice: Optional[Dict[str, str]] = None
    declaration: Optional[Dict[str, Any]] = None


@dataclass
class Run:
    calls: List[Call] = field(default_factory=list)
    outcomes: List[Outcome] = field(default_factory=list)
    trace_dir: Path = Path()
    files: Dict[str, List[dict]] = field(default_factory=dict)

    @property
    def halted(self) -> bool:
        return any(o.notice and o.notice.get("kind") == KIND_HALT for o in self.outcomes)

    @property
    def stopped_for_repeating(self) -> List[Dict[str, str]]:
        """Halts whose *reason* is a repeat, i.e. the failure mode FR-12 prevents.

        A ``HALTED_COMPLETE`` is also a halt, and counting it here would make the
        demo's headline claim ("no false halt") false for a healthy run. The
        question is not "did anything stop" but "did anything claim the agent was
        repeating itself when it had simply changed the subject".
        """
        return [
            o.notice
            for o in self.outcomes
            if o.notice and o.notice.get("rule") in ("R1", "R2")
        ]

    @property
    def last_halt(self) -> Optional[Dict[str, str]]:
        for outcome in reversed(self.outcomes):
            if outcome.notice and outcome.notice.get("kind") == KIND_HALT:
                return outcome.notice
        return None

    def by_task(self, task: Optional[str]) -> List[dict]:
        return [
            record
            for records in self.files.values()
            for record in records
            if record.get("task_id") == task
        ]


def _blocks(result: Any) -> List[str]:
    out = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        out.append(text if isinstance(text, str) else f"<{getattr(item, 'type', '?')}>")
    return out


async def _drive(calls: Sequence[Call], trace_dir: Path) -> Run:
    run = Run(calls=list(calls), trace_dir=trace_dir)
    args = ["--trace-dir", str(trace_dir)]
    async with stdio_client(gateway_params(args)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.list_tools()
            for call in calls:
                result = await session.call_tool(call.tool, dict(call.args), meta=call.meta())
                blocks = _blocks(result)
                notice = parse_notice(blocks[-1]) if blocks else None
                run.outcomes.append(
                    Outcome(
                        text=blocks[0] if blocks else "",
                        is_error=bool(getattr(result, "is_error", False)),
                        notice=notice,
                        declaration=call.declaration,
                    )
                )
    for path in sorted(trace_dir.glob("*.jsonl")):
        run.files[path.name] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return run


def _show(run: Run, title: str) -> None:
    print(SEPARATOR)
    print(title)
    for index, (call, outcome) in enumerate(zip(run.calls, run.outcomes), 1):
        declared = ""
        if call.declaration:
            bits = [f"task={call.declaration.get('task')}"]
            if call.declaration.get("inherit"):
                bits.append("inherit")
            if "require" in call.declaration:
                bits.append(f"require={call.declaration['require']}")
            declared = "  [" + " ".join(bits) + "]"
        mark = "halt" if outcome.notice else ("err " if outcome.is_error else "ok  ")
        print(f"  {index}. [{mark}] {call.tool:<20} {call.note}{declared}")
    # Halts are reported per run, not per process: in the declared case the whole
    # point is that one run ended and the next began, and a single "stop:" line
    # for the process would report the last run's verdict as if it were the whole
    # conversation's.
    halts = [(o.declaration or {}, o.notice) for o in run.outcomes if o.notice]
    if not halts:
        print(f"  stop: no halt across {len(run.calls)} call(s)")
    for declaration, notice in halts:
        task = declaration.get("task") or "(undeclared)"
        print(
            f"  halt: task={task} | rule={notice.get('rule')} "
            f"state={notice.get('state')}"
        )
        print(f"        {notice.get('reason')}")
    for name, records in run.files.items():
        tasks = sorted({(r.get("task_id") or "(undeclared)") for r in records})
        print(f"  file {name}: {len(records)} record(s), task_id={tasks}")


def _check(condition: bool, message: str, results: List[Tuple[bool, str]]) -> None:
    results.append((bool(condition), message))
    print(f"  [{'PASS' if condition else 'FAIL'}] {message}")


def _plan_without_declaration() -> List[Call]:
    return [
        Call("sqlite.list_tables", {}, note="topic 1 opens by reading the schema"),
        Call("sqlite.list_tables", {}, note="topic 2 opens the same way"),
    ]


def _plan_with_declaration() -> List[Call]:
    return [
        Call(
            "sqlite.list_tables",
            {},
            declaration={"task": "t01"},
            note="topic 1 opens",
        ),
        Call(
            "sqlite.list_tables",
            {},
            declaration={"task": "t01-followup", "inherit": True, "require": ["sql.schema"]},
            note="follow-up: already knows the schema",
        ),
        Call(
            "sqlite.list_tables",
            {},
            declaration={"task": "t02"},
            note="new topic: must learn it again",
        ),
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__ and "run-boundary demo")
    parser.add_argument("--run-id", default=None, help="label for the temp dirs")
    args = parser.parse_args(argv)
    # Random by default: the traces are compared, and re-running with a fixed
    # label would mix two runs' bookkeeping (the same trap as the other demos).
    label = args.run_id or f"run-d10-{uuid.uuid4().hex[:6]}"

    workdir = Path(tempfile.mkdtemp(prefix=f"{label}-"))
    plain_dir = workdir / "no-declaration"
    declared_dir = workdir / "declared"
    plain_dir.mkdir()
    declared_dir.mkdir()

    results: List[Tuple[bool, str]] = []
    try:
        without = asyncio.run(_drive(_plan_without_declaration(), plain_dir))
        _show(without, "no declaration -- one process, two topics, same first call")

        with_decl = asyncio.run(_drive(_plan_with_declaration(), declared_dir))
        _show(with_decl, "declared -- one process, three runs, one trace file each")

        print(SEPARATOR)
        print("what the traces say")
        print(f"  without: {len(without.files)} file(s)")
        print(f"  with:    {len(with_decl.files)} file(s)")

        # -- the false halt, and its absence --------------------------------
        _check(
            without.halted and without.last_halt.get("rule") == "R1",
            "undeclared: the second topic's opening call is refused as a duplicate",
            results,
        )
        _check(
            len(without.files) == 1,
            "undeclared: both topics live in one run, so one file",
            results,
        )
        _check(
            not with_decl.stopped_for_repeating,
            "declared: no run was stopped for repeating itself",
            results,
        )

        # -- three runs, each with its own file ----------------------------
        _check(
            len(with_decl.files) == 3,
            "declared: three runs, three trace files",
            results,
        )
        tasks = sorted(str(r.get("task_id")) for recs in with_decl.files.values() for r in recs)
        _check(
            tasks == ["t01", "t01-followup", "t02"],
            "declared: every record carries the task it was declared for",
            results,
        )

        # -- inheritance is provable, not asserted ------------------------
        first = with_decl.by_task("t01")[0]
        follow = with_decl.by_task("t01-followup")[0]
        _check(
            first["evidence_added"] == ["sql.schema"],
            "topic 1 learned the schema itself",
            results,
        )
        _check(
            follow["evidence_added"] == [],
            "the follow-up learned nothing new: the fact was inherited",
            results,
        )
        _check(
            follow["parent_run_id"] == first["run_id"],
            "the follow-up names the run it inherited from",
            results,
        )
        _check(
            follow["stop_check"]["hit"] and follow["stop_check"]["missing"] == [],
            "and it completes immediately: a follow-up that already knows is not sent to look again",
            results,
        )

        # -- a new topic does not inherit ---------------------------------
        fresh = with_decl.by_task("t02")[0]
        _check(
            fresh["evidence_added"] == ["sql.schema"],
            "the new topic re-read the schema: nothing leaked across the boundary",
            results,
        )
        _check(
            fresh["parent_run_id"] is None,
            "a new topic continues nothing",
            results,
        )

        # -- files are still chains ---------------------------------------
        ok_all = all(verify_path(path).ok for path in list(plain_dir.glob("*.jsonl")) + list(declared_dir.glob("*.jsonl")))
        _check(ok_all, "every trace file verifies", results)

        print(SEPARATOR)
        failures = [message for ok, message in results if not ok]
        if failures:
            print(f"FAIL: {len(failures)} claim(s) did not hold")
            for message in failures:
                print(f"  - {message}")
            return 1
        print("PASS: a declared task change is a new run; an undeclared one is a false halt")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
