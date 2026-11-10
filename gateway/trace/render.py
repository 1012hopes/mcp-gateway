"""Single-file HTML replay view for a trace.

    python -m gateway.trace render traces/runs/run-104d434f33a4.jsonl

The output is one ``.html`` file with no external references -- no CDN, no
fonts, no images -- because the point of this view is that a reviewer can
double-click it on a machine with no network and see the same thing the author
saw. Everything is rendered **server-side**: the diff below each step, the
evidence timeline, the failed hash. JavaScript only switches which card is
visible, never what the card says. That is deliberate -- a viewer that computed
the diff in the browser would be a second implementation of the thing under
review, and the two could disagree.

Four things the view has to make visible (``PLAN.md`` D15):

1. **The offered/chosen difference, per step.** ``offered`` is what the model
   was shown; ``chosen`` is what it called. The gap is the whole subject of
   A1 (access) -- and since D14 it also has a name when the call was outside
   the window (``forward_outside``), which this view states in words rather
   than leaving to be inferred from a list.
2. **A broken chain, in red.** Verification reports the *first* bad record;
   everything after it is unverifiable, and the view says that too instead of
   rendering the rest as if it were fine.
3. **The step where the evidence became complete** (R4). That is the moment
   the run is allowed to say "done", so it gets the only accent colour in the
   page.
4. **Determinism.** No timestamps, no random ids, no set iteration: rendering
   the same records twice produces the same bytes. A view that changes when
   nothing changed makes "diff the replay" useless, and this project's whole
   argument is that diffs are readable.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from .verify import VerifyResult, decode_trace, verify_lines

__all__ = [
    "STATUS_OK",
    "STATUS_BAD",
    "STATUS_AFTER",
    "Step",
    "Replay",
    "load_replay",
    "render_html",
    "render_path",
]

STATUS_OK = "ok"
STATUS_BAD = "bad"
STATUS_AFTER = "after"

#: Decisions that mean "the call happened although the window did not show it".
#: Kept here (not imported from transport) so the viewer stays independent of
#: the gateway's runtime: it renders what the file says, nothing else.
OUTSIDE_DECISIONS = ("forward_outside",)

_STATUS_LABEL = {
    STATUS_OK: "链已验证",
    STATUS_BAD: "此条开始不符",
    STATUS_AFTER: "断链之后，无法验证",
}


@dataclass
class Step:
    """One line of the file, after parsing -- including the lines that fail."""

    line: int
    data: Dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_OK
    error: Optional[str] = None
    parse_failed: bool = False
    #: The original line, kept for the parse-failure case: showing only a JSON
    #: error message would leave the reviewer unable to see *what* was written.
    raw: str = ""

    @property
    def seq(self) -> Optional[int]:
        value = self.data.get("seq")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def chosen(self) -> Optional[str]:
        value = self.data.get("chosen")
        return value if isinstance(value, str) else None

    @property
    def offered(self) -> List[str]:
        value = self.data.get("offered")
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    @property
    def evidence_added(self) -> List[str]:
        value = self.data.get("evidence_added")
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    @property
    def stop_check(self) -> Dict[str, Any]:
        value = self.data.get("stop_check")
        return value if isinstance(value, dict) else {}

    @property
    def rule(self) -> Optional[str]:
        rule = self.stop_check.get("rule")
        return rule if isinstance(rule, str) else None

    @property
    def is_r4(self) -> bool:
        """The step where ``required subset Evidence`` first held.

        Matched on the rule name *and* ``hit``: a post rule evaluated but not
        fired must not get the highlight, or the view would celebrate a run
        that did not stop.
        """
        return self.rule == "R4" and bool(self.stop_check.get("hit"))

    @property
    def decided_outside(self) -> bool:
        return self.data.get("decision") in OUTSIDE_DECISIONS


@dataclass
class Replay:
    source: str
    steps: List[Step] = field(default_factory=list)
    verdict: VerifyResult = field(default_factory=lambda: VerifyResult(ok=False))

    @property
    def records(self) -> List[Step]:
        return [step for step in self.steps if not step.parse_failed]

    @property
    def r4_step(self) -> Optional[Step]:
        for step in self.steps:
            if step.is_r4:
                return step
        return None


# -- loading --------------------------------------------------------------


def _split_lines(text: str) -> List[str]:
    """Split like ``verify`` does: on newlines, keeping a trailing partial line.

    ``str.splitlines()`` also splits on ``\\x0b`` and friends, which would shift
    line numbers relative to the verifier's ``enumerate(handle, 1)``. The view
    reports line numbers next to the verifier's, so they must agree.
    """
    if not text:
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def load_replay(path: Union[str, Path]) -> Replay:
    """Parse a trace file and mark every line with its verification status.

    The bytes are decoded by ``verify.decode_trace`` rather than here: the page
    prints a verdict next to the steps, and two decoders would eventually
    disagree about *which* line is broken -- the one thing this page exists to
    show. A file that is not UTF-8 is still drawn (the bad line as replacement
    characters) because refusing to render it would hide the evidence behind
    the verdict.
    """
    source = Path(path)
    text, failure = decode_trace(source.read_bytes())
    lines = _split_lines(text)
    verdict = failure if failure is not None else verify_lines(lines)

    steps: List[Step] = []
    for index, raw in enumerate(lines, 1):
        if not raw.strip():
            steps.append(
                Step(line=index, error="空行", parse_failed=True, raw=raw)
            )
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            steps.append(
                Step(
                    line=index,
                    error=f"JSON 解析失败: {exc.msg}（col {exc.colno}）",
                    parse_failed=True,
                    raw=raw,
                )
            )
            continue
        if not isinstance(parsed, dict):
            steps.append(
                Step(line=index, error="该行不是 JSON 对象", parse_failed=True, raw=raw)
            )
            continue
        steps.append(Step(line=index, data=parsed, raw=raw))

    # Verification stops at the first bad record, so everything after it is
    # *unverified*, not *fine*. Saying "ok" there would be the exact mistake
    # this project keeps writing tests against.
    bad_line = verdict.first_bad_line
    for step in steps:
        if verdict.ok:
            step.status = STATUS_OK
        elif bad_line is None:
            step.status = STATUS_AFTER
        elif step.line < bad_line:
            step.status = STATUS_OK
        elif step.line == bad_line:
            step.status = STATUS_BAD
            if step.parse_failed:
                step.error = verdict.detail
            else:
                step.error = f"{verdict.reason}: {verdict.detail}"
        else:
            step.status = STATUS_AFTER

    return Replay(source=str(source), steps=steps, verdict=verdict)


# -- rendering helpers ----------------------------------------------------


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _short(digest: Any, keep: int = 10) -> str:
    text = "" if digest is None else str(digest)
    return text if len(text) <= keep * 2 else f"{text[:keep]}…{text[-4:]}"


def _cumulative_evidence(steps: Sequence[Step]) -> List[List[str]]:
    """Evidence known *after* each step, in first-added order (deterministic)."""
    seen: List[str] = []
    result: List[List[str]] = []
    for step in steps:
        for item in step.evidence_added:
            if item not in seen:
                seen.append(item)
        result.append(list(seen))
    return result


def _diff_lines(step: Step) -> List[str]:
    """The offered/chosen difference, stated rather than implied."""
    offered = step.offered
    chosen = step.chosen
    if not offered:
        return ["这一轮没有展示任何工具（offered 为空）"]
    shown = len(offered)
    if chosen is None:
        return [f"展示了 {shown} 个工具，这一次没有调用（chosen 为空）"]
    if chosen in offered:
        return [
            f"展示了 {shown} 个工具，调用的是其中之一：{chosen}",
            f"展示了但没选：{shown - 1} 个",
        ]
    return [
        f"展示了 {shown} 个工具，调用的是不在其中的 {chosen}",
        "这次调用被记成 forward_outside：窗口是注意力，不是许可，"
        "所以调用照常转发，而不是拒绝",
        f"窗口里被跳过的：{shown} 个",
    ]


def _badge(text: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{_e(text)}</span>'


def _step_badges(step: Step, previous: Optional[Tuple[str, bool]]) -> str:
    out: List[str] = []
    if step.is_r4:
        out.append(_badge("R4 证据完备 → 收束", "accent"))
    elif step.rule:
        # Evaluated is not the same as fired. Writing "R4 命中" on a record whose
        # ``hit`` is false would be the view asserting the opposite of the file,
        # which is the one thing a replay must never do.
        if step.stop_check.get("hit"):
            out.append(_badge(f"{step.rule} 命中", "rule"))
        elif previous != (step.rule, False):
            out.append(_badge(f"{step.rule} 已评估 · 未命中", "muted"))
    if step.decided_outside:
        out.append(_badge("窗口外调用", "warn"))
    if step.data.get("truncated"):
        out.append(_badge("结果被截断", "warn"))
    if step.data.get("parent_run_id"):
        out.append(_badge("继承上一 run", "muted"))
    if step.status == STATUS_BAD:
        out.append(_badge("此处断链", "bad"))
    elif step.status == STATUS_AFTER:
        out.append(_badge("断链之后", "muted"))
    return "".join(out)


def _kv_rows(step: Step) -> List[Tuple[str, str]]:
    data = step.data
    return [
        ("decision", str(data.get("decision", ""))),
        ("state", str(data.get("state", ""))),
        ("turn", str(data.get("turn", ""))),
        ("latency_ms", str(data.get("latency_ms", ""))),
        ("args_hash", str(data.get("args_hash", ""))),
        ("result_digest", str(data.get("result_digest", ""))),
        ("prev_hash", str(data.get("prev_hash", ""))),
        ("hash", str(data.get("hash", ""))),
    ]


def _render_offered(step: Step) -> str:
    chosen = step.chosen
    items = []
    for name in step.offered:
        mark = ' class="picked"' if name == chosen else ""
        tick = '<span class="tick">← chosen</span>' if name == chosen else ""
        items.append(f"<li{mark}><code>{_e(name)}</code>{tick}</li>")
    if not items:
        items.append("<li class=\"empty\">（空）</li>")
    return "<ul class=\"offered\">" + "".join(items) + "</ul>"


def _render_stop_check(step: Step) -> str:
    check = step.stop_check
    if not check:
        return '<p class="muted">这一条没有 stop_check（记录损坏或版本过旧）</p>'
    rows = [
        ("rule", check.get("rule")),
        ("hit", check.get("hit")),
        ("attempted", ", ".join(check.get("attempted") or []) or "（无）"),
        ("evidence", ", ".join(check.get("evidence") or []) or "（无）"),
        ("missing", ", ".join(check.get("missing") or []) or "（无）"),
        ("state_hash", _short(check.get("state_hash")) or "（未计算）"),
    ]
    body = "".join(
        f"<dt>{_e(key)}</dt><dd>{_e(value)}</dd>" for key, value in rows
    )
    return f"<dl class=\"kv\">{body}</dl>"


def _render_card(
    step: Step,
    index: int,
    cumulative: Sequence[str],
    previous: Optional[Tuple[str, bool]],
) -> str:
    status_class = f"status-{step.status}"
    r4_class = " is-r4" if step.is_r4 else ""
    seq_label = step.seq if step.seq is not None else f"line {step.line}"
    title = f"seq {seq_label} · {step.chosen or '（未调用）'}"

    if step.parse_failed:
        body = (
            f'<p class="alert">这一行无法解析：{_e(step.error)}</p>'
            f'<pre class="raw">{_e(step.raw)}</pre>'
        )
    else:
        # A record whose bytes changed still parses -- that is exactly why it is
        # dangerous. The verifier's reason goes first, before any of the fields,
        # because a reader who trusts the table below would trust a lie.
        reason = (
            f'<p class="alert">这一条的哈希与内容不符：{_e(step.error)}</p>'
            if step.status == STATUS_BAD and step.error
            else ""
        )
        diff = "".join(f"<li>{_e(line)}</li>" for line in _diff_lines(step))
        added = ", ".join(step.evidence_added) or "（无新增）"
        body = reason + f"""
        <div class="cols">
          <div>
            <h3>offered / chosen 差集</h3>
            <ul class="diff">{diff}</ul>
            {_render_offered(step)}
          </div>
          <div>
            <h3>这一条记录</h3>
            <dl class="kv wide">{_kv_rows_html(step)}</dl>
          </div>
        </div>
        <div class="cols">
          <div>
            <h3>证据</h3>
            <p>本条新增：<code>{_e(added)}</code></p>
            <p>到此累计（{len(cumulative)} 项）：<code>{_e(", ".join(cumulative) or "（空）")}</code></p>
          </div>
          <div>
            <h3>停止判定</h3>
            {_render_stop_check(step)}
          </div>
        </div>
        """

    return f"""
    <article class="card {status_class}{r4_class}" id="card-{index}" data-seq="{_e(index)}"
             data-line="{_e(step.line)}"
             data-chosen="{_e(step.chosen or '')}" data-rule="{_e(step.rule or '')}"
             data-status="{_e(step.status)}">
      <header class="card-head">
        <h2>{_e(title)}</h2>
        <div class="badges">{_step_badges(step, previous)}</div>
      </header>
      {body}
    </article>
    """


def _kv_rows_html(step: Step) -> str:
    return "".join(
        f"<dt>{_e(key)}</dt><dd><code>{_e(_short(value, 16))}</code></dd>"
        for key, value in _kv_rows(step)
    )


# -- the page -------------------------------------------------------------

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#f7f5f0; --panel:#fffefb; --ink:#1c1c1e; --muted:#6c6a66; --line:#e0dbd1;
  --accent:#0b6b5b; --accent-soft:#e4f1ee; --bad:#b3261e; --bad-soft:#fbe9e7;
  --warn:#8a5a00; --warn-soft:#fbf0da; --rule:#3d4e8a; --rule-soft:#e8ebf6;
}
html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:15px/1.55 -apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif;padding:0 0 3rem}
code,pre,.mono{font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;font-size:12.5px}
header.page{padding:1.4rem 1.6rem 1rem;border-bottom:1px solid var(--line);background:var(--panel)}
header.page h1{margin:0 0 .3rem;font-size:1.18rem;letter-spacing:.01em}
.sub{color:var(--muted);font-size:13px}
.verdict{display:inline-block;margin-top:.6rem;padding:.15rem .6rem;border-radius:999px;
  font-size:12.5px;font-weight:600;border:1px solid}
.verdict.pass{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.verdict.fail{color:var(--bad);border-color:var(--bad);background:var(--bad-soft)}
.legend{margin-top:.7rem;font-size:12.5px;color:var(--muted);display:flex;gap:1rem;flex-wrap:wrap}
.legend b{font-weight:600;color:var(--ink)}
main{display:grid;grid-template-columns:minmax(240px,320px) minmax(0,1fr);gap:0;align-items:start}
nav.steps{position:sticky;top:0;max-height:100vh;overflow:auto;padding:.8rem;border-right:1px solid var(--line)}
nav.steps h2{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:.2rem .4rem .6rem}
.step{display:block;width:100%;text-align:left;margin:0 0 .28rem;padding:.42rem .55rem;border-radius:7px;
  border:1px solid transparent;background:transparent;color:inherit;cursor:pointer;font:inherit}
.step:hover{background:var(--panel);border-color:var(--line)}
.step.current{background:var(--panel);border-color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.06)}
.step .row1{display:flex;gap:.4rem;align-items:baseline}
.step .seq{color:var(--muted);font-size:11.5px;min-width:1.7em}
.step .tool{font-family:inherit;font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.step .row2{font-size:11.5px;color:var(--muted);padding-left:2.1em;display:flex;gap:.35rem;flex-wrap:wrap}
.step.status-bad{background:var(--bad-soft);border-color:var(--bad)}
.step.status-after .tool{color:var(--muted);text-decoration:line-through}
.step.is-r4{box-shadow:inset 3px 0 0 var(--accent)}
section.cards{padding:1rem 1.2rem;min-width:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem;margin:0 0 1rem}
.card h2{margin:0;font-size:1rem}
.card h3{margin:.9rem 0 .35rem;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.card-head{display:flex;justify-content:space-between;gap:.8rem;align-items:center;flex-wrap:wrap;
  padding-bottom:.6rem;border-bottom:1px solid var(--line)}
.card.status-bad{border-color:var(--bad);box-shadow:0 0 0 1px var(--bad) inset}
.card.status-after{opacity:.72}
.card.is-r4{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.badges{display:flex;gap:.35rem;flex-wrap:wrap}
.badge{font-size:11.5px;padding:.1rem .45rem;border-radius:999px;border:1px solid;white-space:nowrap}
.badge-accent{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}
.badge-rule{color:var(--rule);border-color:var(--rule);background:var(--rule-soft)}
.badge-warn{color:var(--warn);border-color:var(--warn);background:var(--warn-soft)}
.badge-bad{color:var(--bad);border-color:var(--bad);background:var(--bad-soft)}
.badge-muted{color:var(--muted);border-color:var(--line)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
@media (max-width:900px){main{grid-template-columns:1fr}nav.steps{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}.cols{grid-template-columns:1fr}}
ul.diff{margin:.2rem 0 .6rem;padding-left:1.1rem}
ul.diff li{margin:.15rem 0}
ul.offered{list-style:none;margin:.2rem 0;padding:0;max-height:15rem;overflow:auto;
  border:1px solid var(--line);border-radius:8px}
ul.offered li{padding:.2rem .5rem;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:.5rem}
ul.offered li:last-child{border-bottom:0}
ul.offered li.picked{background:var(--accent-soft);font-weight:600}
ul.offered li.empty{color:var(--muted)}
.tick{color:var(--accent);font-size:11.5px}
dl.kv{display:grid;grid-template-columns:5.6rem 1fr;gap:.15rem .6rem;margin:.2rem 0;font-size:12.5px}
dl.kv dt{color:var(--muted)}
dl.kv dd{margin:0;overflow-wrap:anywhere}
dl.kv.wide{grid-template-columns:6.8rem 1fr}
p{margin:.3rem 0}
.alert{color:var(--bad);background:var(--bad-soft);border:1px solid var(--bad);
  border-radius:8px;padding:.5rem .6rem;font-size:13px}
pre.raw{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:.5rem;overflow:auto}
.muted{color:var(--muted)}
.band{position:sticky;bottom:0;background:var(--panel);border-top:1px solid var(--line);
  padding:.7rem 1.2rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap;font-size:12.5px}
.band h2{margin:0;font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.band ul{list-style:none;display:flex;gap:.35rem;flex-wrap:wrap;margin:0;padding:0}
.band li{padding:.08rem .45rem;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.band li.lit{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.band .count{color:var(--muted);margin-left:auto}
footer.page{padding:1rem 1.6rem;color:var(--muted);font-size:12px;border-top:1px solid var(--line)}
body.js-on .card{display:none}
body.js-on .card.active{display:block}
"""

JS = """
(function(){
  var body=document.body, cards=[].slice.call(document.querySelectorAll('.card')),
      steps=[].slice.call(document.querySelectorAll('.step'));
  if(!cards.length) return;
  body.classList.add('js-on');
  function show(seq, push){
    seq=String(seq);
    cards.forEach(function(c){c.classList.toggle('active', c.dataset.seq===seq);});
    steps.forEach(function(s){s.classList.toggle('current', s.dataset.goto===seq);});
    if(push && history.replaceState){history.replaceState(null,'','#step-'+seq);}
  }
  steps.forEach(function(s){s.addEventListener('click', function(){show(s.dataset.goto,true);});});
  document.addEventListener('keydown', function(e){
    var at=steps.findIndex(function(s){return s.classList.contains('current');});
    if(e.key==='ArrowDown'||e.key==='j'){if(at+1<steps.length){show(steps[at+1].dataset.goto,true);e.preventDefault();}}
    if(e.key==='ArrowUp'||e.key==='k'){if(at>0){show(steps[at-1].dataset.goto,true);e.preventDefault();}}
  });
  var m=/^#step-(\\d+)$/.exec(location.hash);
  show(m?m[1]:steps[0].dataset.goto, false);
})();
"""


def _summary_line(replay: Replay) -> str:
    steps = replay.records
    if not steps:
        return "没有任何可解析的记录"
    states = {}
    for step in steps:
        state = str(step.data.get("state", ""))
        states[state] = states.get(state, 0) + 1
    ordered = sorted(states.items(), key=lambda item: (-item[1], item[0]))
    return " · ".join(f"{name or '（空）'} {count}" for name, count in ordered)


def _run_meta(replay: Replay) -> List[Tuple[str, str]]:
    steps = replay.records
    if not steps:
        return []
    first = steps[0].data
    last = steps[-1].data
    return [
        ("run_id", str(first.get("run_id", ""))),
        ("task_id", str(first.get("task_id") or "（未声明）")),
        ("session_id", str(first.get("session_id", ""))),
        ("parent_run_id", str(first.get("parent_run_id") or "（无）")),
        ("gateway_version", str(first.get("gateway_version", ""))),
        ("rules_version", str(first.get("rules_version", ""))),
        ("model_id", str(first.get("model_id", ""))),
        ("终态", str(last.get("state", ""))),
    ]


def _evidence_band(steps: Sequence[Step]) -> str:
    """The bottom band: which required facts the run had, and which it never got.

    ``required`` is not stored as such -- it is recoverable from the last record
    that ran a post rule, because ``stop_check.evidence`` is the set the run had
    and ``missing`` is ``required - Evidence``. Reconstructing it here is the
    honest alternative to guessing at the task file: everything on screen then
    comes from the trace and nothing else (DESIGN 2.7's hard line).
    """
    last: Optional[Step] = None
    for step in steps:
        check = step.stop_check
        if check.get("evidence") or check.get("missing"):
            last = step
    if last is None:
        return ""

    evidence = [item for item in last.stop_check.get("evidence") or [] if isinstance(item, str)]
    missing = [item for item in last.stop_check.get("missing") or [] if isinstance(item, str)]
    required = list(evidence) + [item for item in missing if item not in evidence]
    if not required:
        return ""

    chips = "".join(
        f'<li class="{"lit" if name in evidence else "dim"}">{_e(name)}</li>'
        for name in required
    )
    state = "齐了" if not missing else f"缺 {len(missing)} 项"
    return (
        '<section class="band">'
        "<h2>证据进度</h2>"
        f"<ul>{chips}</ul>"
        f'<span class="count">{len(evidence)}/{len(required)} · {_e(state)}'
        f"（取自 seq {_e(last.seq if last.seq is not None else last.line)} 的 stop_check）</span>"
        "</section>"
    )


def render_html(
    replay: Replay,
    *,
    title: Optional[str] = None,
) -> str:
    """Render the whole page. Same input -> same bytes, always."""
    steps = replay.steps
    cumulative = _cumulative_evidence(steps)
    verdict = replay.verdict

    nav = []
    cards = []
    previous: Optional[Tuple[str, bool]] = None
    for index, step in enumerate(steps, 1):
        nav.append(_render_nav_item(step, index))
        cards.append(_render_card(step, index, cumulative[index - 1], previous))
        if step.rule:
            previous = (step.rule, bool(step.stop_check.get("hit")))

    verdict_class = "pass" if verdict.ok else "fail"
    verdict_text = (
        f"链完整 · {verdict.records} 条记录"
        if verdict.ok
        else f"链断裂 · 首个坏记录 seq={verdict.first_bad_seq} line={verdict.first_bad_line} · {verdict.reason}"
    )

    r4 = replay.r4_step
    r4_note = (
        f"R4 在 seq={r4.seq} 收束（证据齐了）"
        if r4
        else "这条轨迹里没有 R4 命中"
    )

    meta_rows = "".join(
        f"<dt>{_e(key)}</dt><dd>{_e(value)}</dd>" for key, value in _run_meta(replay)
    )

    notes = "".join(f"<li>{_e(note)}</li>" for note in verdict.notes)
    notes_block = f'<ul class="notes">{notes}</ul>' if notes else ""

    heading = title or f"回放：{Path(replay.source).name}"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(heading)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="page">
  <h1>{_e(heading)}</h1>
  <div class="sub">来源 <code>{_e(Path(replay.source).name)}</code> · {len(steps)} 步 · {_e(_summary_line(replay))}</div>
  <div class="verdict {verdict_class}">{_e(verdict_text)}</div>
  <dl class="kv" style="margin-top:.8rem">{meta_rows}</dl>
  <div class="legend">
    <span><b>点左边的步</b>看这一轮的 offered / chosen 差集</span>
    <span><b>{_e(r4_note)}</b></span>
    <span>红色 = 从这一条起链断了；灰色删除线 = 断链之后（无法验证，不是“没问题”）</span>
  </div>
  {notes_block}
</header>
<main>
  <nav class="steps">
    <h2>步</h2>
    {''.join(nav)}
  </nav>
  <section class="cards">
    {''.join(cards)}
  </section>
</main>
{_evidence_band(steps)}
<footer class="page">
  由 <code>python -m gateway.trace render</code> 生成。页面里的一切都是服务端渲染的，
  JavaScript 只负责切换显示哪一张卡片——所以关掉脚本也不会少看到内容。
  校验口径与 <code>python -m gateway.trace verify</code> 完全相同（同一个
  <code>verify_lines</code>），这里不会出现“回放说没问题、verify 说断了”的情况。
</footer>
<script>{JS}</script>
</body>
</html>
"""


def _render_nav_item(step: Step, index: int) -> str:
    seq_label = step.seq if step.seq is not None else f"L{step.line}"
    tool = step.chosen or ("（解析失败）" if step.parse_failed else "（未调用）")
    decision = str(step.data.get("decision", "") or "")
    flags = []
    if step.is_r4:
        flags.append("R4")
    if step.decided_outside:
        flags.append("窗口外")
    if step.status != STATUS_OK:
        flags.append("断链" if step.status == STATUS_BAD else "未验证")
    classes = [f"step status-{step.status}"]
    if step.is_r4:
        classes.append("is-r4")
    flag_text = " · ".join(flags)
    return (
        f'<button class="{" ".join(classes)}" data-goto="{index}" type="button">'
        f'<span class="row1"><span class="seq">{_e(seq_label)}</span>'
        f'<span class="tool">{_e(tool)}</span></span>'
        f'<span class="row2"><span>{_e(decision)}</span>'
        f'{f"<span>{_e(flag_text)}</span>" if flag_text else ""}</span>'
        f"</button>"
    )


def render_path(
    path: Union[str, Path],
    out: Optional[Union[str, Path]] = None,
    *,
    title: Optional[str] = None,
) -> Path:
    """Render ``path`` to a single ``.html`` file and return where it landed."""
    replay = load_replay(path)
    target = Path(out) if out is not None else Path(path).with_suffix(".html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(replay, title=title), encoding="utf-8", newline="\n")
    return target
