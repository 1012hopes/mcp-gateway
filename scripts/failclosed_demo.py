"""D4 / G1 demo: the trace proves what happened, and refuses to lie when it cannot.

    {python} scripts/failclosed_demo.py

Three segments, in increasing order of how much they would be worth lying about:

S1  a normal run: N calls -> N records, chain verifies
S2  **write permission genuinely removed** (read-only attribute; no stub, no
    monkeypatch): the gateway exits with code 3 and never starts. The existing
    chain is byte-identical afterwards -- refusing to run did not damage the
    evidence that was already there.
S3  a write failure **mid-run** (injected at fsync, labelled as such): the call
    executes, the record does not land, the result is discarded, the run is
    aborted, and every later call is refused. The invariant
    ``results_delivered == records_written`` still holds.

S3 needs fault injection because a real disk does not fail on request. It is
labelled ``[注入]`` in the output for exactly that reason: the point of this
project is that claims are checkable, so a claim about a simulated failure must
say that it is simulated.
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from echo_client import drive  # noqa: E402
from gateway.config import load_config  # noqa: E402
from gateway.session import RunSession  # noqa: E402
from gateway.trace.sink import JsonlSink  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402
from gateway.transport import Gateway  # noqa: E402
from gateway.upstream import StdioUpstream  # noqa: E402
from mcp import types  # noqa: E402

DEMO_DIR = ROOT / "traces" / "demo"
SHORT_CALLS = [
    ("sqlite.list_tables", {}),
    ("sqlite.count_rows", {"table": "repos"}),
]


def _line(char: str = "-") -> None:
    print(char * 78)


def _records(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def segment_1_normal() -> Dict[str, Any]:
    _line("=")
    print("S1  正常一次运行：N 次调用 -> N 条记录")
    _line("=")
    outcome = asyncio.run(drive(SHORT_CALLS))
    path: Path = outcome["trace_path"]
    for index, call in enumerate(outcome["calls"], start=1):
        flag = "ERR" if call["is_error"] else "ok"
        print(f"  {index}. [{flag}] {call['name']:<22} {call['text'][:60]}")
    result = verify_path(path)
    calls = len(outcome["calls"])
    records = _records(path)
    print(f"  calls={calls}  records={records}  verify={result.summary()}")
    healthy = result.ok and calls == records
    print(f"  {'PASS' if healthy else 'FAIL'}: 每次交付的调用都有对应记录")
    return {"ok": healthy, "path": path, "run_id": outcome["run_id"], "records": records}


def _gateway_exit_code(run_id: str, trace_dir: Path) -> int:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "gateway",
            "--run-id",
            run_id,
            "--trace-dir",
            str(trace_dir),
        ],
        cwd=str(ROOT),
        input=b"",
        capture_output=True,
        timeout=60,
    )
    if b"refusing to start" not in completed.stderr:
        print(f"  stderr: {completed.stderr.decode('utf-8', 'replace')[:200]}")
    return completed.returncode


def segment_2_permission() -> Dict[str, Any]:
    _line("=")
    print("S2  拔掉写权限（真实 chmod，非模拟）：网关拒绝启动")
    _line("=")
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    trace_dir = DEMO_DIR
    run_id = "run-demo-readonly"
    path = trace_dir / f"{run_id}.jsonl"
    for stale in trace_dir.glob(f"{run_id}*.jsonl"):
        os.chmod(stale, stat.S_IWRITE | stat.S_IREAD)  # in case a demo died half-way
        stale.unlink()

    # Build a real chain first, so S2 can also show it survives untouched.
    asyncio.run(drive(SHORT_CALLS, run_id=run_id, trace_dir=trace_dir))
    before = path.read_bytes()
    print(f"  先跑通一次：{path.name}，{_records(path)} 条记录，{len(before)} 字节")

    os.chmod(path, stat.S_IREAD)  # read-only attribute on Windows, 0o400 on POSIX
    print(f"  已设为只读：mode={oct(path.stat().st_mode & 0o777)}")
    try:
        code = _gateway_exit_code(run_id, trace_dir)
        after = path.read_bytes()
        unchanged = after == before
        refused = code == 3
        result = verify_path(path)
        print(f"  网关退出码 = {code}（约定 3 = 拒绝启动）")
        print(f"  原链字节未变：{unchanged}；verify：{result.summary()}")
        print(f"  {'PASS' if refused and unchanged and result.ok else 'FAIL'}: "
              "记录不下来就不执行，且旧证据没被破坏")
        return {"ok": refused and unchanged and result.ok}
    finally:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


async def _run_with_injected_failure(trace_dir: Path) -> Dict[str, Any]:
    config = load_config()
    spec = config.upstreams[0]
    run_id = "run-demo-injected"
    path = trace_dir / f"{run_id}.jsonl"
    path.unlink(missing_ok=True)

    upstream = StdioUpstream(
        spec.name, spec.command, spec.args, env=spec.env, cwd=spec.cwd
    )

    syncs = {"count": 0}

    def failing_fsync(fd: int) -> None:
        syncs["count"] += 1
        if syncs["count"] == 2:
            raise OSError(28, "No space left on device (injected)")
        os.fsync(fd)

    sink = JsonlSink(path, run_id=run_id, fsync_fn=failing_fsync)
    session = RunSession(sink=sink, run_id=run_id)
    gateway = Gateway(config, [upstream], session)

    async with upstream:
        await gateway.list_all_tools()
        results: List[types.CallToolResult] = []
        for index, (name, arguments) in enumerate(
            [
                ("sqlite.list_tables", {}),
                ("sqlite.count_rows", {"table": "repos"}),
                ("sqlite.describe_table", {"table": "repos"}),
            ],
            start=1,
        ):
            params = types.CallToolRequestParams(name=name, arguments=arguments)
            result = await gateway.handle_call_tool(None, params)
            results.append(result)
            note = ""
            if name == "sqlite.count_rows":
                note = "  <- 第 2 次记录写盘失败"
            print(f"  调用 {index}: {name:<22} is_error={result.is_error}{note}")

    session.sink.close()
    stats = session.stats()
    records = _records(path)
    result = verify_path(path)
    return {
        "stats": stats,
        "records": records,
        "results": results,
        "verify": result,
        "path": path,
    }


def segment_3_injected() -> Dict[str, Any]:
    _line("=")
    print("S3  [注入] 运行中写盘失败：结果丢弃、run 中止、后续调用被拒")
    _line("=")
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    outcome = asyncio.run(_run_with_injected_failure(DEMO_DIR))
    stats = outcome["stats"]
    result = outcome["verify"]
    records = outcome["records"]
    flags = [r.is_error for r in outcome["results"]]
    invariant = stats["results_delivered"] == stats["records_written"] == records
    aborted = stats["state"] == "ABORTED"
    rejected = flags == [False, True, True]
    print(f"  stats: {stats}")
    print(f"  记录行数={records}  verify={result.summary()}")
    print(f"  交付的结果 = {stats['results_delivered']}，写入记录 = {stats['records_written']}")
    print(f"  {'PASS' if invariant and aborted and rejected else 'FAIL'}: "
          "交付数 == 记录数；写失败之后没有任何结果泄漏给模型")
    return {"ok": invariant and aborted and rejected}


def main() -> int:
    s1 = segment_1_normal()
    s2 = segment_2_permission()
    s3 = segment_3_injected()
    _line("=")
    all_ok = s1["ok"] and s2["ok"] and s3["ok"]
    print(f"结论：{'PASS' if all_ok else 'FAIL'}  (S1={s1['ok']} S2={s2['ok']} S3={s3['ok']})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
