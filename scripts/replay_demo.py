"""D15 acceptance in one command: render a real trace, then break it and render again.

    {python} scripts/replay_demo.py
    {python} scripts/replay_demo.py --task t01 --keep

Writes three files under ``results/replay/``:

* ``r4.html``        -- a governed run that stopped on R4 (evidence complete)
* ``tampered.html``  -- the same bytes with one field changed after the fact
* ``escaping.html``  -- a synthetic record full of HTML, to prove the view escapes

The checks below are the D15 criteria written as assertions rather than as a
description. Two of them exist because the obvious implementation gets them
wrong:

* **"the page is one file"** is checked by forbidding every external reference,
  not by eyeballing it. A viewer that silently pulls a font from a CDN works
  on the author's machine and shows tofu on the reviewer's.
* **"a broken chain is red"** is checked by asserting the *first* bad record is
  the only red one and that everything after it is marked unverified. Marking
  all of them red would be a different claim (that all of them are bad), and
  marking none of them would be the claim this project exists to avoid.

Exit code 0 only if every check passes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import load_tasks, run_task_sync  # noqa: E402
from gateway.trace.render import (  # noqa: E402
    Replay,
    Step,
    load_replay,
    render_html,
    render_path,
)
from gateway.trace.verify import verify_path  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"


# -- helpers --------------------------------------------------------------


def _checks(inp: Dict[str, bool]) -> int:
    width = max(len(name) for name in inp)
    for name, ok in inp.items():
        print(f"  [{PASS if ok else FAIL}] {name:<{width}}")
    return sum(1 for ok in inp.values() if not ok)


def _cards(html: str) -> List[str]:
    return re.findall(r"<article\b.*?</article>", html, re.S)


def _attr(chunk: str, name: str) -> Optional[str]:
    match = re.search(rf'{name}="([^"]*)"', chunk)
    return match.group(1) if match else None


def _steps(html: str) -> List[str]:
    return re.findall(r'<button class="step[^"]*"[^>]*>.*?</button>', html, re.S)


def _tamper(src: Path, dst: Path, *, line_index: int = 2) -> Dict[str, Any]:
    """Change one hashed field on one line, leaving the JSON valid.

    The point is that a reader with the file open sees nothing unusual: the
    numbers still look like a trace. Only the hash disagrees.
    """
    lines = src.read_text(encoding="utf-8").splitlines()
    data = json.loads(lines[line_index])
    before = dict(data)
    data["latency_ms"] = int(data.get("latency_ms", 0)) + 1234
    lines[line_index] = json.dumps(data, ensure_ascii=False, sort_keys=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"seq": data.get("seq"), "line": line_index + 1, "before": before, "after": data}


class _Balance(HTMLParser):
    """Counts tags that open and never close -- a proxy for "did I emit valid HTML"."""

    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: List[str] = []
        self.errors: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"</{tag}> without an opener")
            return
        if self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes <{self.stack[-1]}>")
            return
        self.stack.pop()


# -- checks ---------------------------------------------------------------


def _self_contained(html: str) -> Dict[str, bool]:
    return {
        "no <script src=> / <link> / @import": not re.search(
            r"<script[^>]+\bsrc=|<link\b|@import", html, re.I
        ),
        "no absolute URL anywhere (offline-proof)": not re.search(
            r"https?://(?!www\.w3\.org)", html, re.I
        ),
        "no external font or image request": not re.search(
            r"url\(\s*['\"]?(?!data:)", html, re.I
        ),
        "well-formed tags": _balance_ok(html),
    }


def _balance_ok(html: str) -> bool:
    parser = _Balance()
    parser.feed(html)
    parser.close()
    return not parser.errors and not parser.stack


def _one_entry_per_step(html: str, steps: int) -> Dict[str, bool]:
    cards = _cards(html)
    navs = _steps(html)
    seqs = [_attr(card, "data-seq") for card in cards]
    return {
        "one card per record": len(cards) == steps,
        "one nav button per card": len(navs) == steps,
        "card ids are unique": len({_attr(card, "id") for card in cards}) == steps,
        "data-seq is 1..n in order": seqs == [str(i) for i in range(1, steps + 1)],
    }


def _diff_checks(html: str, records: List[Dict[str, Any]]) -> Dict[str, bool]:
    cards = _cards(html)
    outside = [r for r in records if r.get("chosen") not in (r.get("offered") or [])]
    outside_cards = [c for c in cards if "forward_outside" in c]
    return {
        "chosen-outside-window cards match records": len(outside_cards) == len(outside),
        "every offered item is listed": all(
            all(f"<code>{name}</code>" in card for name in (record.get("offered") or []))
            for card, record in zip(cards, records)
        ),
        "exactly one item is marked chosen": all(
            card.count('class="picked"')
            == (1 if record.get("chosen") in (record.get("offered") or []) else 0)
            for card, record in zip(cards, records)
        ),
        "the diff states the count, not just the colour": all(
            "展示了" in card for card in cards
        ),
    }


def _r4_checks(replay: Replay, html: str) -> Dict[str, bool]:
    r4 = replay.r4_step
    cards = _cards(html)
    marked = [c for c in cards if _attr(c, "data-rule") == "R4"]
    return {
        "there is an R4 step": r4 is not None,
        "exactly one card is marked R4": len(marked) == 1,
        "the R4 card is the accent one": bool(marked) and "is-r4" in marked[0],
        "the R4 card says so in words": bool(marked) and "R4 证据完备" in marked[0],
        "R4 is where the run stopped": bool(marked)
        and _attr(marked[0], "data-seq") == str(len(cards)),
    }


def _tampered_checks(
    tampered: Path, html: str, marker: Dict[str, Any]
) -> Dict[str, bool]:
    verdict = verify_path(tampered)
    cards = _cards(html)
    bad = [c for c in cards if _attr(c, "data-status") == "bad"]
    after = [c for c in cards if _attr(c, "data-status") == "after"]
    return {
        "verify reports the break": not verdict.ok,
        "the break is localised to the tampered line": verdict.first_bad_line
        == marker["line"],
        "exactly one card is red": len(bad) == 1,
        "the red card is the tampered one": bool(bad)
        and _attr(bad[0], "data-line") == str(marker["line"]),
        "everything after it is unverified, not green": len(after)
        == len(cards) - marker["line"],
        "the page shows the reason": bool(bad) and "hash_mismatch" in bad[0],
        "the banner says FAIL": "verdict fail" in html,
    }


def _escaping_checks() -> Dict[str, bool]:
    """A trace is upstream-controlled text. It must never become markup."""
    payload = '<script>alert("x")</script>'
    attr_payload = '"><img src=x onerror=alert(1)>'
    step = Step(
        line=1,
        data={
            "seq": 1,
            "run_id": "run-xss",
            "turn": 1,
            "offered": [payload, "sqlite.query"],
            "chosen": payload,
            "decision": attr_payload,
            "state": "RUNNING",
            "args_hash": attr_payload,
            "result_digest": "",
            "prev_hash": "",
            "hash": "",
            "evidence_added": [payload],
            "stop_check": {"rule": None, "hit": False, "evidence": [], "missing": [],
                           "attempted": [], "state_hash": None},
        },
    )
    replay = Replay(source="synthetic.jsonl", steps=[step])
    html = render_html(replay)
    return {
        "script tag in a field stays text": payload not in html,
        "it is escaped instead": "&lt;script&gt;" in html,
        "attribute-quote payload cannot break out": 'onerror=alert(1)>' not in html,
        "the page still well-formed with hostile input": _balance_ok(html),
    }


def _band_checks(html: str, replay: Replay) -> Dict[str, bool]:
    """The bottom band has to agree with the last record that ran a post rule."""
    band = re.search(r'<section class="band">.*?</section>', html, re.S)
    body = band.group(0) if band else ""
    last = None
    for step in replay.records:
        if step.stop_check.get("evidence") or step.stop_check.get("missing"):
            last = step
    evidence = (last.stop_check.get("evidence") or []) if last else []
    missing = (last.stop_check.get("missing") or []) if last else []
    return {
        "the evidence band is present": bool(band),
        "it counts what the run had": f"{len(evidence)}/" in body,
        "every chip is lit when nothing is missing": (
            missing == [] and 'class="dim"' not in body
        )
        or (bool(missing) and 'class="dim"' in body),
        "it says where the numbers came from": "stop_check" in body,
    }


def _determinism_checks(replay: Replay) -> Dict[str, bool]:
    first = render_html(replay)
    second = render_html(replay)
    return {
        "rendering twice gives identical bytes": first == second,
        "no timestamp or uuid leaked in": not re.search(
            r"\b20\d\d-\d\d-\d\dT|\b[0-9a-f]{8}-[0-9a-f]{4}-", first
        ),
    }


# -- main -----------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="D15: single-file HTML replay view")
    parser.add_argument("--task", default="t01", help="task id to run (default t01)")
    parser.add_argument("--out", default=str(ROOT / "results" / "replay"))
    parser.add_argument("--trace-dir", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the run's trace dir")
    parser.add_argument(
        "--sample-out",
        default=None,
        help="copy the clean trace here (e.g. traces/samples/replay-r4.jsonl) so the "
        "view can be demoed without running the agent",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = {task.id: task for task in load_tasks()}
    if args.task not in tasks:
        print(f"没有这条任务: {args.task}", file=sys.stderr)
        return 2
    task = tasks[args.task]

    import tempfile

    tmp = tempfile.TemporaryDirectory()
    trace_dir = Path(args.trace_dir) if args.trace_dir else Path(tmp.name)
    trace_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"replay-{uuid.uuid4().hex[:6]}"
    print(f"跑 {task.id} 一次，产出轨迹 {run_id} …")
    result = run_task_sync(task, run_id=run_id, trace_dir=trace_dir)
    print(f"  {result.steps} 步，停在 {result.stop_reason}")

    if args.sample_out:
        sample = Path(args.sample_out)
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_bytes(result.trace_path.read_bytes())
        print(f"  冻结样例 {sample}（{sample.stat().st_size} 字节，"
              f"以后可以直接 render 它，不必再跑 agent）")

    print("=" * 78)
    replay = load_replay(result.trace_path)
    clean_html = render_html(replay)
    clean_path = out_dir / "r4.html"
    clean_path.write_text(clean_html, encoding="utf-8", newline="\n")
    records = [step.data for step in replay.records]
    print(f"写出 {clean_path}（{clean_path.stat().st_size} 字节）")

    print()
    print("-- 1. 单文件，双击就能看 " + "-" * 48)
    failures = _checks(_self_contained(clean_html))
    failures += _checks(_one_entry_per_step(clean_html, len(replay.steps)))

    print()
    print("-- 2. offered / chosen 差集 " + "-" * 44)
    failures += _checks(_diff_checks(clean_html, records))

    print()
    print("-- 3. 证据齐的那步（R4）高亮 " + "-" * 38)
    failures += _checks(_r4_checks(replay, clean_html))
    failures += _checks(_band_checks(clean_html, replay))

    print()
    print("-- 4. 篡改之后链条标红 " + "-" * 43)
    tampered_path = out_dir / "tampered.jsonl"
    marker = _tamper(result.trace_path, tampered_path)
    print(f"  改动：line {marker['line']} (seq {marker['seq']}) 的 latency_ms "
          f"{marker['before'].get('latency_ms')} → {marker['after'].get('latency_ms')}，"
          "其余字节不动")
    tampered_replay = load_replay(tampered_path)
    tampered_html = render_html(tampered_replay)
    tampered_out = out_dir / "tampered.html"
    tampered_out.write_text(tampered_html, encoding="utf-8", newline="\n")
    print(f"  写出 {tampered_out}（{tampered_out.stat().st_size} 字节）")
    failures += _checks(_tampered_checks(tampered_path, tampered_html, marker))

    print()
    print("-- 5. 轨迹内容是不可信文本 " + "-" * 39)
    failures += _checks(_escaping_checks())
    (out_dir / "escaping.html").write_text(
        render_html(
            Replay(
                source="synthetic.jsonl",
                steps=[
                    Step(
                        line=1,
                        data={
                            "seq": 1,
                            "run_id": "run-xss",
                            "turn": 1,
                            "offered": ['<script>alert("x")</script>'],
                            "chosen": '<script>alert("x")</script>',
                            "decision": '"><img src=x onerror=alert(1)>',
                            "state": "RUNNING",
                        },
                    )
                ],
            )
        ),
        encoding="utf-8",
        newline="\n",
    )

    print()
    print("-- 6. 同一个输入，同一份字节 " + "-" * 37)
    failures += _checks(_determinism_checks(replay))

    print()
    print("-- 7. 命令行入口 " + "-" * 49)
    from gateway.trace.cli import main as cli_main

    cli_out = out_dir / "cli.html"
    code = cli_main(["render", str(result.trace_path), "-o", str(cli_out)])
    failures += _checks(
        {
            "`python -m gateway.trace render` 退出码 0": code == 0,
            "CLI 写出的文件非空": cli_out.exists() and cli_out.stat().st_size > 4096,
            "同一个文件，CLI 与库给出同样的字节": cli_out.read_text(encoding="utf-8")
            == clean_html,
        }
    )

    print("-" * 78)
    print(f"replay: {len(replay.steps)} 步，{len(records)} 条记录，"
          f"R4 在 seq={replay.r4_step.seq if replay.r4_step else '—'}")
    print(f"offered/chosen 差集：{sum(1 for r in records if r.get('chosen') not in (r.get('offered') or []))} "
          f"步调用了窗口外的工具")
    print(f"files: {clean_path.name} / {tampered_path.name.replace('.jsonl', '.html')} / escaping.html")

    if failures:
        print(f"{FAIL}: {failures} 项没通过")
        if not args.keep:
            tmp.cleanup()
        return 1

    print(f"{PASS} 双击 {clean_path} 就能看：点左栏的步看差集，"
          f"{tampered_out.name} 里第 {marker['line']} 条起是红的")
    if not args.keep:
        tmp.cleanup()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
