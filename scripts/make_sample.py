"""Generate the sample trace used by the demo and the tests.

Deterministic on purpose: no timestamps, no randomness, no network. Running it
twice produces byte-identical output, which is what makes ``traces/samples/``
worth committing -- a reviewer can regenerate the file and diff it.

    py -3.12 scripts/make_sample.py

The 12 steps tell the story the project is about: 3 steps were necessary, the
other 9 were the same information re-fetched with different wording. Nothing
here stops early -- this is the *baseline*, and the number to beat is 12.

The sample is minted with the current schema version. It carries no
``stop_check.state_hash`` because it models a run with no rules installed, i.e.
nothing computed a state fingerprint; that key is optional in v2 precisely so a
record can be honest about not having the measurement.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.trace.canonical import canonical_bytes  # noqa: E402
from gateway.trace.record import (  # noqa: E402
    StopCheck,
    TraceRecord,
    genesis_hash,
)
from gateway.trace.sink import JsonlSink  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

RUN_ID = "run-demo-12-steps"
SESSION_ID = "sess-demo"
GATEWAY_VERSION = "0.1.0"
RULES_VERSION = "rules-v1(d1-none)"
OFFERED = ["gh.search", "gh.get_file", "gh.list_commits", "sql.query", "wiki.search"]

# (tool, args, evidence_added) -- first three are the "necessary" ones.
STEPS = [
    ("gh.search", {"q": "mcp gateway stop condition", "limit": 10}, ["repo.list"]),
    ("gh.get_file", {"path": "gateway/stop.py", "ref": "main"}, ["file.content"]),
    ("gh.list_commits", {"repo": "p1s4/mcp-search-proxy", "limit": 20}, ["commit.list"]),
    ("gh.search", {"q": "mcp proxy termination", "limit": 10}, []),
    ("gh.search", {"q": "mcp gateway when to stop", "limit": 10}, []),
    ("gh.get_file", {"path": "gateway/stop.py", "ref": "main"}, []),
    ("gh.search", {"q": "agent loop detection", "limit": 10}, []),
    ("gh.get_file", {"path": "gateway/stop.py", "ref": "main"}, []),
    ("sql.query", {"sql": "select count(*) from repos"}, []),
    ("gh.search", {"q": "max_turns recursion_limit stop", "limit": 10}, []),
    ("wiki.search", {"q": "终止判据"}, []),
    ("gh.search", {"q": "mcp gateway stop condition", "limit": 10}, []),
]


def _digest(payload) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def build_records() -> list[TraceRecord]:
    records: list[TraceRecord] = []
    prev_hash = genesis_hash(RUN_ID)
    for index, (tool, args, evidence) in enumerate(STEPS, start=1):
        last = index == len(STEPS)
        record = TraceRecord(
            seq=index,
            run_id=RUN_ID,
            session_id=SESSION_ID,
            turn=index,
            model_id="baseline-stub",
            gateway_version=GATEWAY_VERSION,
            rules_version=RULES_VERSION,
            offered=list(OFFERED),
            chosen=tool,
            args_hash=_digest(args),
            result_digest=_digest({"tool": tool, "index": index}),
            evidence_added=list(evidence),
            stop_check=StopCheck(
                rule="R3_budget" if last else None,
                hit=last,
                evidence=sorted({e for _, _, ev in STEPS[: index] for e in ev}),
                missing=[],
                attempted=["R5", "R1", "R4", "R2", "R3"],
            ),
            decision="halt_budget" if last else "forward",
            state="HALTED_BUDGET" if last else "RUNNING",
            latency_ms=180 + index * 7,
            prev_hash=prev_hash,
        ).seal()
        records.append(record)
        prev_hash = record.hash
    return records


def main() -> int:
    out = ROOT / "traces" / "samples" / "demo-12-steps.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    with JsonlSink(out, run_id=RUN_ID) as sink:
        sink.append_all(build_records())

    result = verify_path(out)
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size} bytes)")
    print(result.summary())
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
