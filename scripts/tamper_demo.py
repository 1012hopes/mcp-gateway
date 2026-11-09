"""G1 demo: five ways to tamper with a trace, five detections.

    py -3.12 scripts/tamper_demo.py

Prints one line per case. Exit code 0 means every mutation was caught, which
makes it usable as a CI check as well as a demo. Run it after
``scripts/make_sample.py``.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.trace.canonical import canonical_bytes  # noqa: E402
from gateway.trace.verify import verify_path  # noqa: E402

SAMPLE = ROOT / "traces" / "samples" / "demo-12-steps.jsonl"
WORK = ROOT / "traces" / "_tamper.jsonl"


def load() -> list[dict]:
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines()]


def save(records: list[dict]) -> None:
    text = "\n".join(canonical_bytes(r).decode("utf-8") for r in records)
    WORK.write_text(text + "\n", encoding="utf-8")


def set_field(records: list[dict], index: int, field: str, value) -> None:
    records[index][field] = value


CASES: list[tuple[str, str, Callable[[list[dict]], None]]] = [
    ("首行字段", "把 seq 1 的 chosen 换掉", lambda r: set_field(r, 0, "chosen", "sql.query")),
    ("中间行字段", "把 seq 7 的 chosen 换掉", lambda r: set_field(r, 6, "chosen", "sql.query")),
    ("尾行字段", "把 seq 12 的 latency_ms 改成 1", lambda r: set_field(r, 11, "latency_ms", 1)),
    ("offered 字段", "把 seq 4 的 offered 缩减成一个工具", lambda r: set_field(r, 3, "offered", ["sql.query"])),
    ("hash 字段", "把 seq 9 的 hash 改成全 0", lambda r: set_field(r, 8, "hash", "0" * 64)),
    ("删除中间行", "删掉 seq 3 整行", lambda r: r.pop(2)),
]


def main() -> int:
    if not SAMPLE.exists():
        print(f"缺少样例：{SAMPLE}，先跑 scripts/make_sample.py")
        return 2

    baseline = verify_path(SAMPLE)
    print(f"基线：{baseline.summary()}")
    print("-" * 68)

    original = SAMPLE.read_bytes()
    failures = 0
    for name, description, mutate in CASES:
        records = load()
        mutate(records)
        save(records)
        # A demo that silently mutates nothing proves nothing -- same failure
        # class the project is about. Refuse to count it as a detection.
        if WORK.read_bytes() == original:
            failures += 1
            print(f"{name:<12} {description:<28} 无效  篡改未改变任何字节")
            continue
        result = verify_path(WORK)
        detected = not result.ok
        failures += 0 if detected else 1
        mark = "检出" if detected else "漏检"
        print(
            f"{name:<12} {description:<28} {mark}  "
            f"seq={result.first_bad_seq} reason={result.reason}"
        )
        if detected:
            print(f"{'':<12} └─ {result.detail}")

    print("-" * 68)
    print(f"{len(CASES) - failures}/{len(CASES)} 处篡改被检出")
    WORK.unlink(missing_ok=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
