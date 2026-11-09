"""G1 demo: attacks on a trace, and what the chain can and cannot catch.

    {python} scripts/tamper_demo.py                     # 12-step sample
    {python} scripts/tamper_demo.py --path traces/runs/<run>.jsonl

Each case prints the *first* record that fails and why. Exit code 0 means every
case behaved as documented, which makes this a CI check as well as a demo.

Three outcomes are distinguished, because lumping them together is how demos
lie:

* ``检出``    the attack was caught -- the expected result for normal tampering
* ``符合预期`` the attack was *not* caught, and it is a documented blind spot
              (rewriting the whole tail keeps the chain self-consistent)
* ``异常``    the result contradicts the documentation -- either the code or the
              limitation section is wrong, and one of them must be fixed

Case 7 is the one worth understanding: changing a field *and* recomputing that
record's hash defeats the digest check, so the chain only notices one line later
-- at the next record's ``prev_hash``. That is why the reported seq moves from
7 to 8, and why "reseal the tail" is the attack that actually works.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.trace.canonical import canonical_bytes  # noqa: E402
from gateway.trace.verify import record_hash, verify_path  # noqa: E402

SAMPLE = ROOT / "traces" / "samples" / "demo-12-steps.jsonl"
WORK = ROOT / "traces" / "_tamper.jsonl"

DETECTED = "detected"
BLIND_SPOT = "blind_spot"


@dataclass
class Case:
    name: str
    describe: str
    mutate: Callable[[List[dict]], None]
    expect: str = DETECTED


def set_field(records: List[dict], index: int, field: str, value) -> None:
    records[index][field] = value


def mid(records: List[dict]) -> int:
    return max(0, len(records) // 2)


def reseal_at(records: List[dict], index: int) -> None:
    """Recompute one record's hash so its bytes agree with its own claim."""
    records[index]["hash"] = record_hash(records[index])


def reseal_tail(records: List[dict], index: int) -> None:
    """Rewire and reseal everything from ``index`` to the end."""
    reseal_at(records, index)
    for position in range(index + 1, len(records)):
        records[position]["prev_hash"] = records[position - 1]["hash"]
        reseal_at(records, position)


def build_cases(count: int) -> List[Case]:
    """Cases are built per file: ``seq 7`` only makes sense for a 12-record trace.

    ``index`` must have a successor, otherwise "delete the middle record" turns
    into "delete the last record" -- which is tail truncation, a documented
    blind spot, and scoring it as a miss would be measuring the wrong thing.
    """
    index = 0 if count < 3 else min(count // 2, count - 2)

    return [
        Case("首行字段", "把 seq 1 的 chosen 换掉", lambda r: set_field(r, 0, "chosen", "sql.query")),
        Case(
            "中间行字段",
            f"把 seq {index + 1} 的 chosen 换掉",
            lambda r: set_field(r, index, "chosen", "sql.query"),
        ),
        Case(
            "尾行字段",
            f"把 seq {count} 的 latency_ms 改成 1",
            lambda r: set_field(r, -1, "latency_ms", 1),
        ),
        Case(
            "offered 字段",
            f"把 seq {index + 1} 的 offered 缩减成一个工具",
            lambda r: set_field(r, index, "offered", ["sql.query"]),
        ),
        Case(
            "hash 字段",
            f"把 seq {index + 1} 的 hash 改成全 0",
            lambda r: set_field(r, index, "hash", "0" * 64),
        ),
        Case("删除中间行", f"删掉 seq {index + 1} 整行", lambda r: r.pop(index)),
        Case(
            "改字段并重算",
            f"改 seq {index + 1} 的 chosen 并重算它的 hash -> 断在下一行",
            lambda r: (set_field(r, index, "chosen", "sql.query"), reseal_at(r, index)),
        ),
        Case(
            "重写整条尾链",
            f"改 seq {index + 1} 并重算其后所有 hash（文档记载的盲区）",
            lambda r: (set_field(r, index, "chosen", "sql.query"), reseal_tail(r, index)),
            expect=BLIND_SPOT,
        ),
    ]


def load(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def save(records: List[dict], path: Path) -> None:
    text = "\n".join(canonical_bytes(r).decode("utf-8") for r in records)
    path.write_text(text + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="tamper with a trace and see what breaks")
    parser.add_argument("--path", default=str(SAMPLE))
    parser.add_argument("--keep", action="store_true", help="keep the tampered copy")
    args = parser.parse_args(argv)

    source = Path(args.path)
    if not source.exists():
        print(f"缺少轨迹：{source}（先跑 scripts/make_sample.py 或 scripts/echo_client.py）")
        return 2

    baseline = verify_path(source)
    print(f"基线：{baseline.summary()}  ({source})")
    if not baseline.ok:
        print("基线本身不通过，先修数据再看篡改")
        return 1
    print("-" * 78)

    original = source.read_bytes()
    count = len(load(source))
    if count < 4:
        print(
            f"注意：轨迹只有 {count} 条记录，中间行用例已退化为首行；"
            "删除尾行与整链重写属于盲区（DESIGN 局限 7）"
        )
    cases = build_cases(count)
    failures = 0
    for number, case in enumerate(cases, start=1):
        records = load(source)
        case.mutate(records)
        save(records, WORK)

        # A demo that silently mutates nothing proves nothing -- the same
        # failure class this project is about. Refuse to score it.
        if WORK.read_bytes() == original:
            failures += 1
            print(f"[{number}] {case.name}  篡改未改变任何字节 -> 无效")
            continue

        result = verify_path(WORK)
        caught = not result.ok

        if case.expect == DETECTED and caught:
            verdict, bad = "检出", False
        elif case.expect == BLIND_SPOT and not caught:
            verdict, bad = "符合预期", False
        else:
            verdict, bad = "异常", True
        failures += 1 if bad else 0

        print(f"[{number}] {case.name}：{case.describe}")
        if caught:
            print(f"    -> {verdict}  seq={result.first_bad_seq}  {result.reason}")
            print(f"       {result.detail}")
        else:
            print(f"    -> {verdict}  链依然自洽（{result.records} 条），无异常上报")
            print("       这是 DESIGN 局限 7：封闭它需要外部锚定，不在本项目范围内")

    print("-" * 78)
    print(f"{len(cases) - failures}/{len(cases)} 个用例符合文档描述")
    if not args.keep:
        WORK.unlink(missing_ok=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
