"""One task, run in front of you, one call at a time.

``render`` and ``serve`` both answer "what happened". They assume the reader
already believes the thing is worth looking at. This page answers the question
that comes *before* that one -- "what is this thing" -- by letting someone
press a button and watch a real agent run against a real gateway.

It is the only page in the project that mutates anything, so it is fenced:

* It writes **only** under ``traces/play/``. A demo run and a play run never
  share a directory, and neither can overwrite the other's file because the
  run id carries a random suffix.
* It runs a **whitelisted** task id, looked up in ``tasks/*.json``. The id from
  the request is never used as a path.
* It needs the same per-session token as the demo buttons. Starting a
  subprocess is the expensive action here, not reading a file.

The frames are built from two sources on purpose: the *narration* (which tool,
which arguments, what came back) comes from the agent's own turn list, and the
*judgement* (how many evidence types were collected, which rule fired) comes
from the sealed trace. If the page narrated from the trace alone it would have
to re-derive the arguments from a hash; if it judged from the turn list it
would be the agent grading itself, which is the exact thing this project exists
to stop.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .theme import BASE_CSS

__all__ = [
    "PLAY_TIMEOUT",
    "PLAY_SUBDIR",
    "Frame",
    "build_story",
    "run_once",
    "render_play",
    "task_choices",
]

#: Seconds one play run may take. A task is 4-11 calls to a local SQLite file,
#: so 8s is typical; the cap exists so a wedged gateway turns into a sentence
#: instead of a request thread that never answers.
PLAY_TIMEOUT = 90

#: Where play runs write. Separate from the demo output on purpose: this
#: directory only ever grows by someone pressing a button in a browser, so
#: "why are there 500 files here" has a one-line answer.
PLAY_SUBDIR = "play"

#: Rule -> what it did, in the words of someone who does not work on this.
_RULE_PLAIN = {
    "R1": "同一个调用又要发第二遍，被拒了",
    "R2": "连着几次都没查到新东西，判定这条路走不通",
    "R3": "调用次数到上限了",
    "R4": "该查的证据都齐了，收工",
    "R6": "要求的证据这几类工具拿不到，别白跑了",
}


class Frame:
    """One call, as the page draws it. A dict would have been fine; a tuple of
    named fields is what keeps ``build_story`` and the tests honest about which
    keys the page actually reads."""

    __slots__ = (
        "n",
        "tool",
        "args",
        "result",
        "window",
        "total_tools",
        "evidence",
        "missing",
        "decision",
        "rule",
        "hit",
        "is_error",
        "repeat_of",
    )

    def __init__(
        self,
        *,
        n: int,
        tool: str,
        args: str,
        result: str,
        window: Sequence[str],
        total_tools: int,
        evidence: Sequence[str],
        missing: Sequence[str],
        decision: str,
        rule: str,
        hit: bool,
        is_error: bool,
        repeat_of: Optional[int] = None,
    ) -> None:
        self.n = n
        self.tool = tool
        self.args = args
        self.result = result
        self.window = tuple(window)
        self.total_tools = total_tools
        self.evidence = tuple(evidence)
        self.missing = tuple(missing)
        self.decision = decision
        self.rule = rule
        self.hit = hit
        self.is_error = is_error
        #: The earlier step this one repeats exactly, if any. This is the whole
        #: "took the long way round" story in one number: without a gateway the
        #: same query runs three times, and the page can only say so because it
        #: compares the calls to each other rather than trusting a step count.
        self.repeat_of = repeat_of

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n,
            "tool": self.tool,
            "args": self.args,
            "result": self.result,
            "window": list(self.window),
            "total": self.total_tools,
            "evidence": list(self.evidence),
            "missing": list(self.missing),
            "decision": self.decision,
            "rule": self.rule,
            "hit": self.hit,
            "is_error": self.is_error,
            "repeat_of": self.repeat_of,
        }


def _one_line(value: Any, limit: int = 240) -> str:
    """Collapse to a single line and cut. A JSON block with newlines would make
    one frame three screens tall and the timeline unreadable."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def task_choices() -> List[Dict[str, Any]]:
    """The task menu. Read from disk every time rather than frozen here: a task
    added to ``tasks/`` should appear on the page without editing this file."""
    from agent.tasks import load_tasks

    out: List[Dict[str, Any]] = []
    for task in load_tasks():
        out.append(
            {
                "id": task.id,
                "goal": task.goal,
                "optimal": task.optimal_steps,
                "required": list(task.required_evidence),
                "max_turns": task.max_turns,
                "adversarial": not task.required_evidence,
            }
        )
    return out


def _task(task_id: str):
    from agent.tasks import load_tasks

    for task in load_tasks():
        if task.id == task_id:
            return task
    raise LookupError(f"没有这条任务：{task_id!r}")


def _records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _verdict(steps: int, stop_reason: str, halt: Dict[str, str], task) -> Dict[str, str]:
    """The one sentence at the end. Three kinds, because there are three
    outcomes a reader has to be able to tell apart: stopped *because* the work
    was done, stopped *because* the work was pointless, and simply ran out."""
    rule = str(halt.get("rule", ""))
    if stop_reason == "halted" and rule:
        plain = _RULE_PLAIN.get(rule, halt.get("reason", ""))
        good = rule == "R4"
        return {
            "kind": "ok" if good else "stop",
            "title": (
                f"第 {steps} 步收工 —— 该查的都查到了"
                if good
                else f"第 {steps} 步被叫停 —— {plain}"
            ),
            "detail": (
                "证据齐了就不再让它多调一次：后面那些调用只会把已经证明过的"
                "东西再证明一遍。"
                if good
                else f"规则 {rule} 触发。它没有查完，但也没有假装查完 —— "
                "停在这里，并且把原因写进了记录。"
            ),
        }
    if stop_reason == "policy_exhausted":
        return {
            "kind": "open",
            "title": f"第 {steps} 步它自己停了",
            "detail": "没有系统判定。它认为做完了 —— 对不对要看最后的结果。"
            + ("这条任务要的证据它一项都没拿到，所以没人能替它背书。"
               if not task.required_evidence
               else ""),
        }
    if stop_reason == "max_turns":
        return {
            "kind": "bad",
            "title": f"跑满 {steps} 步还没停",
            "detail": "这是兜底上限，不是正常停止。没有网关时常见：没人告诉它"
            "已经够了，它就一直查下去。",
        }
    return {
        "kind": "open",
        "title": f"第 {steps} 步结束（{stop_reason}）",
        "detail": "这次运行没有走到一个判定。",
    }


def build_story(
    result,
    records: Sequence[Dict[str, Any]],
    *,
    task,
    rules: bool,
) -> Dict[str, Any]:
    """Turn one run into the JSON the page plays back. Pure: same run, same
    story -- nothing is read from the clock or from the network here."""
    frames: List[Frame] = []
    #: Tool + raw arguments, in the order first seen. ``_one_line`` truncates
    #: the *display* string; the repeat check uses the full arguments, so two
    #: calls that differ only past the cut are not called identical.
    seen: Dict[Tuple[str, str], int] = {}
    for index, turn in enumerate(result.turns):
        record = records[index] if index < len(records) else {}
        check = record.get("stop_check") or {}
        window = (
            result.visible[index] if index < len(result.visible) else result.tools
        )
        key = (turn.tool, json.dumps(dict(turn.args), sort_keys=True, ensure_ascii=False, default=str))
        repeat_of = seen.get(key)
        seen.setdefault(key, index + 1)
        frames.append(
            Frame(
                n=index + 1,
                tool=turn.tool,
                args=_one_line(turn.args, 200),
                result=_one_line(turn.blocks[0] if turn.blocks else "", 260),
                window=window,
                total_tools=len(result.tools),
                evidence=check.get("evidence") or [],
                missing=check.get("missing") or [],
                decision=str(record.get("decision", "")),
                rule=str(check.get("rule") or ""),
                hit=bool(check.get("hit")),
                is_error=bool(turn.is_error),
                repeat_of=repeat_of,
            )
        )

    verdict = _verdict(result.steps, result.stop_reason, dict(result.halt or {}), task)
    return {
        "task": {
            "id": task.id,
            "goal": task.goal,
            "optimal": task.optimal_steps,
            "required": list(task.required_evidence),
        },
        "rules": rules,
        "steps": result.steps,
        "stop_reason": result.stop_reason,
        "halt": dict(result.halt or {}),
        "verdict": verdict,
        "total_tools": len(result.tools),
        "trace": result.trace_path.name if result.trace_path else "",
        "frames": [frame.as_dict() for frame in frames],
    }


def run_once(task_id: str, *, rules: bool = True) -> Dict[str, Any]:
    """Run one task for real and return the playable story.

    Deliberately blocking: the HTTP handler runs it on its own thread, and a
    background job with a progress endpoint would be a second state machine to
    get wrong for a wait the user can see in the browser.
    """
    # Looked up *before* the SDK import so an unknown id is a 404 rather than a
    # 500 -- the id check needs only the task files, and a page that reports
    # "missing dependency" for a typo teaches the wrong lesson.
    task = _task(task_id)

    # Imported here, not at module scope: ``loop`` needs the MCP SDK, and a
    # bare interpreter must still be able to import this module to test the
    # frame builder. (Same reason ``agent/__init__`` defers its exports.)
    try:
        from agent import run_task
    except ImportError as exc:  # pragma: no cover - depends on the interpreter
        raise RuntimeError(f"这个 Python 环境里没有 MCP SDK：{exc}") from None

    root = Path(__file__).resolve().parents[2]
    trace_dir = root / "traces" / PLAY_SUBDIR
    trace_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"play-{task.id}-{secrets.token_hex(4)}"

    async def _bounded(coro, seconds: float):
        return await asyncio.wait_for(coro, seconds)

    coro = run_task(
        task,
        run_id=run_id,
        trace_dir=trace_dir,
        extra_args=[] if rules else ["--no-rules"],
    )
    try:
        result = asyncio.run(_bounded(coro, PLAY_TIMEOUT))
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"超过 {PLAY_TIMEOUT} 秒还没跑完，已经放弃。"
            "这通常意味着某个上游没响应。"
        ) from None
    return build_story(
        result, _records(result.trace_path), task=task, rules=rules
    )


# -- page -----------------------------------------------------------------

_CSS = """
.wrap{max-width:1180px;margin:0 auto;padding:1.8rem 1.6rem 3rem}
.hero{padding:1.5rem 1.7rem}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.hero h1{margin:.3rem 0 .5rem;font-size:1.55rem;letter-spacing:-.015em}
.lede{margin:0;color:var(--muted);font-size:13.5px;max-width:70ch}
.lede b{color:var(--ink);font-weight:600}

.console{display:flex;flex-wrap:wrap;gap:.9rem;align-items:center;padding:.85rem 1.1rem;
  margin:1rem 0;position:sticky;top:.6rem;z-index:5}
.console .field{display:flex;flex-direction:column;gap:.2rem;min-width:0}
.console .field>span{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
select{font:inherit;font-size:13px;padding:.4rem .5rem;border-radius:9px;color:var(--ink);
  border:1px solid var(--line-strong);background:rgba(255,255,255,.85);max-width:24rem}
.toggle{display:flex;align-items:center;gap:.5rem;font-size:12.5px;cursor:pointer;user-select:none}
.toggle input{position:absolute;opacity:0;width:0;height:0}
.track{width:38px;height:21px;border-radius:999px;background:rgba(22,24,29,.18);
  position:relative;transition:background .25s ease;flex:0 0 auto}
.knob{position:absolute;top:2.5px;left:2.5px;width:16px;height:16px;border-radius:50%;
  background:#fff;box-shadow:0 1px 3px rgba(16,24,40,.4);transition:transform .25s cubic-bezier(.2,.7,.3,1)}
.toggle input:checked+.track{background:var(--accent)}
.toggle input:checked+.track .knob{transform:translateX(17px)}
.toggle input:focus-visible+.track{box-shadow:0 0 0 3px var(--accent-soft)}
button.go{font:inherit;font-size:14px;font-weight:600;cursor:pointer;color:#fff;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));border:0;border-radius:10px;
  padding:.55rem 1.5rem;min-width:5.2rem;letter-spacing:.02em;
  box-shadow:0 8px 20px -10px rgba(11,107,91,.85);
  transition:transform .18s ease,box-shadow .18s ease}
button.go:hover{transform:translateY(-1px);box-shadow:0 12px 24px -10px rgba(11,107,91,.9)}
button.go:disabled{opacity:.55;cursor:progress;transform:none;box-shadow:none}
button.ghost{font:inherit;font-size:12.5px;cursor:pointer;color:var(--muted);background:transparent;
  border:1px solid var(--line-strong);border-radius:9px;padding:.35rem .7rem}
button.ghost:hover{color:var(--ink)}
.eta{font-size:12px;color:var(--muted)}

.goal{margin:0 0 1.1rem;padding:.7rem .95rem;border-left:2px solid var(--accent);
  background:rgba(255,255,255,.5);border-radius:0 10px 10px 0;font-size:13px}
.goal b{font-weight:600}
.goal .meta{display:block;margin-top:.25rem;font-size:11.5px;color:var(--muted)}

.stage{display:grid;grid-template-columns:1.55fr .85fr;gap:1.1rem;align-items:start}
@media (max-width:900px){.stage{grid-template-columns:1fr}}

.panel{padding:1rem 1.15rem}
.panel h2{margin:0 0 .7rem;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);font-weight:600}

ol.timeline{list-style:none;margin:0;padding:0 0 0 1.6rem;position:relative}
ol.timeline::before{content:"";position:absolute;left:.55rem;top:.5rem;bottom:.8rem;width:1px;
  background:linear-gradient(180deg,var(--line-strong),transparent)}
li.step{position:relative;margin:0 0 .7rem;padding:.6rem .8rem;border-radius:11px;
  background:rgba(255,255,255,.62);border:1px solid var(--line);
  animation:rise .34s cubic-bezier(.2,.7,.3,1) both}
li.step::before{content:"";position:absolute;left:-1.24rem;top:.85rem;width:8px;height:8px;
  border-radius:50%;background:var(--line-strong);box-shadow:0 0 0 3px var(--panel-solid)}
li.step.is-halt::before{background:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
li.step.is-bad::before{background:var(--bad);box-shadow:0 0 0 3px var(--bad-soft)}
li.step .head{display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap}
li.step .n{font-family:"SFMono-Regular",Consolas,monospace;font-size:11px;color:var(--muted)}
li.step code.tool{font-size:12.5px;font-weight:600}
li.step .badges{margin-left:auto;display:flex;gap:.3rem;flex-wrap:wrap;justify-content:flex-end}
li.step .badge{font-size:10.5px;padding:.05rem .4rem;border-radius:999px;
  border:1px solid var(--line-strong);color:var(--muted);white-space:nowrap}
li.step .badge.stop{color:var(--accent);border-color:rgba(11,107,91,.4);background:var(--accent-soft)}
li.step .badge.warn{color:var(--bad);border-color:rgba(179,38,30,.4);background:var(--bad-soft)}
li.step .args,li.step .res{margin:.35rem 0 0;font-family:"SFMono-Regular",Consolas,monospace;
  font-size:11.5px;line-height:1.55;overflow-wrap:anywhere}
li.step .args{color:#4b5261}
li.step .res{color:#14161a}
li.step .win{display:flex;align-items:center;gap:.5rem;margin-top:.5rem;font-size:11px;color:#5a6170}
li.step .win .bar{flex:1;height:5px;border-radius:3px;background:rgba(22,24,29,.10);overflow:hidden}
li.step .win .bar i{display:block;height:100%;border-radius:3px;
  background:linear-gradient(90deg,var(--accent),var(--accent-2));transform-origin:left;
  animation:grow .5s cubic-bezier(.2,.7,.3,1) both}
/* A full bar means the window did not narrow anything -- grey, not green. The
   colour has to say "nothing was saved here", or the baseline column reads as
   a page that is doing just as well. */
li.step.wide .win .bar i{background:rgba(22,24,29,.28)}
li.step.faded{opacity:.55}

.meter+.meter{margin-top:1.1rem}
.meter .big{font-size:2.1rem;font-weight:600;letter-spacing:-.03em;line-height:1;
  font-variant-numeric:tabular-nums;transition:color .2s ease}
.meter .big.bump{color:var(--accent)}
.meter .cap{font-size:11.5px;color:var(--muted);margin-top:.25rem}
.ev{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.5rem}
.ev span{font-family:"SFMono-Regular",Consolas,monospace;font-size:10.5px;padding:.15rem .4rem;
  border-radius:6px;border:1px dashed var(--line-strong);color:var(--muted);
  transition:all .3s ease}
.ev span.got{border-style:solid;border-color:rgba(11,107,91,.45);background:var(--accent-soft);
  color:var(--accent)}
.ev span.got::before{content:"✓ ";font-weight:700}

.verdict{margin-top:1.1rem;padding:1.1rem 1.3rem;border-left:3px solid var(--line-strong);
  animation:rise .4s cubic-bezier(.2,.7,.3,1) both}
.verdict.ok{border-left-color:var(--accent)}
.verdict.stop{border-left-color:var(--warn)}
.verdict.bad{border-left-color:var(--bad)}
.verdict h3{margin:0 0 .35rem;font-size:1.05rem;letter-spacing:-.01em}
.verdict p{margin:0;font-size:13px;color:var(--muted);max-width:78ch}
.verdict .acts{margin-top:.75rem;display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
.verdict .acts a{font-size:12.5px}

.placeholder{padding:2.2rem 1.2rem;text-align:center;color:var(--muted);font-size:13px;
  border:1px dashed var(--line-strong);border-radius:12px;background:rgba(255,255,255,.35)}
.placeholder b{color:var(--ink)}

.err{border-left-color:var(--bad)}
footer.page{max-width:1180px;margin:1.4rem auto 0;color:var(--muted);font-size:11.5px;line-height:1.65}
"""

_JS = """
(function(){
  var TASKS = __TASKS__;
  var TOKEN = __TOKEN__;
  var sel = document.getElementById('task');
  var rulesBox = document.getElementById('rules');
  var rulesLabel = document.getElementById('rules-label');
  var go = document.getElementById('go');
  var goal = document.getElementById('goal');
  var timeline = document.getElementById('timeline');
  var calls = document.getElementById('calls');
  var evBox = document.getElementById('ev');
  var cap = document.getElementById('cap');
  var verdict = document.getElementById('verdict');
  var empty = document.getElementById('empty');

  var byId = {};
  TASKS.forEach(function(t){ byId[t.id] = t; });

  function describe(){
    var t = byId[sel.value];
    if(!t){ goal.innerHTML = ''; return; }
    var bits = [];
    if(t.optimal) bits.push('手数最优 <b>' + t.optimal + '</b> 步');
    bits.push(t.required.length
      ? '必须拿到 <b>' + t.required.length + '</b> 类证据'
      : '这条任务<b>没有答案</b>，看它什么时候认输');
    goal.innerHTML = '<b>' + esc(t.goal) + '</b><span class="meta">' + bits.join(' · ') + '</span>';
    goal.hidden = false;
    paintEvidence([], t.required);
  }

  function paintEvidence(got, req, prefix){
    evBox.innerHTML = '';
    if(!req || !req.length){
      evBox.innerHTML = '<span style="border:0;padding:0;color:var(--muted)">' +
        '这条任务不要求证据 —— 看它会不会一直查下去</span>';
      cap.textContent = '没有证据要求';
      return;
    }
    req.forEach(function(name){
      var el = document.createElement('span');
      el.textContent = name;
      if(got.indexOf(name) >= 0){ el.className = 'got'; }
      evBox.appendChild(el);
    });
    var left = req.filter(function(name){ return got.indexOf(name) < 0; });
    cap.textContent = (prefix || '') +
      (left.length ? '还差 ' + left.length + ' 类证据' : '证据齐了');
  }

  function esc(s){
    return String(s).replace(/[&<>"]/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
    });
  }

  function reset(){
    timeline.innerHTML = '';
    evBox.innerHTML = '';
    calls.textContent = '0';
    calls.classList.remove('bump');
    cap.textContent = '还没有调用';
    verdict.hidden = true;
    verdict.innerHTML = '';
    empty.hidden = false;
    empty.style.display = '';
  }

  // The label has to follow the switch. A toggle that keeps saying "开" while
  // it is off is the class of small lie this whole project is about: the
  // reader believes the words, and the words were never wired to anything.
  function syncRules(){
    rulesLabel.textContent = '判停规则：' + (rulesBox.checked ? '开' : '关');
  }
  rulesBox.addEventListener('change', syncRules);
  syncRules();

  sel.addEventListener('change', describe);
  describe();

  var timer = null;
  var playing = null;

  function step(frame, story){
    empty.hidden = true;
    empty.style.display = 'none';
    calls.textContent = String(frame.n);
    calls.classList.add('bump');
    setTimeout(function(){ calls.classList.remove('bump'); }, 380);

    var li = document.createElement('li');
    var narrowed = frame.window.length < frame.total;
    li.className = 'step' + (frame.hit ? ' is-halt' : '') + (frame.is_error ? ' is-bad' : '') +
                   (narrowed ? '' : ' wide');
    var badges = [];
    if(frame.repeat_of){
      badges.push('<span class="badge warn">和第 ' + frame.repeat_of + ' 步一模一样</span>');
    }
    if(frame.hit && frame.rule){
      badges.push('<span class="badge stop">' + esc(frame.rule) + ' · 于是停在这里</span>');
    } else if(frame.rule && !frame.hit){
      badges.push('<span class="badge warn">' + esc(frame.rule) + ' · 检查过，没拦</span>');
    }
    if(frame.is_error){
      badges.push('<span class="badge warn">调用失败</span>');
    }
    var pct = frame.total ? Math.max(4, Math.round(frame.window.length / frame.total * 100)) : 0;
    var winText;
    if(narrowed){
      winText = '这一步只摆出 <b>' + frame.window.length + '</b> / ' + frame.total + ' 个工具';
    } else if(frame.n === 1){
      // The cold start has no history to narrow against, so a full window here
      // is the honest first move rather than an idle one. Saying "一个都没收"
      // would read as the mechanism failing on its very first chance to work.
      winText = '第一步：还没有线索，先把 <b>' + frame.total + '</b> 个全摆出来';
    } else {
      winText = '<b>' + frame.total + '</b> 个工具全摆着，一个都没收';
    }
    var argText = (frame.args === '{}' || !frame.args) ? '（不带参数）' : frame.args;
    li.innerHTML =
      '<div class="head"><span class="n">第 ' + frame.n + ' 步</span>' +
      '<code class="tool">' + esc(frame.tool) + '</code>' +
      '<span class="badges">' + badges.join('') + '</span></div>' +
      '<div class="args">' + esc(argText) + '</div>' +
      '<div class="res">' + esc(frame.result) + '</div>' +
      '<div class="win"><span>' + winText + '</span>' +
      '<span class="bar"><i style="width:' + pct + '%"></i></span></div>';
    timeline.appendChild(li);

    var req = (story && story.task && story.task.required) || [];
    paintEvidence(frame.evidence, req, '第 ' + frame.n + ' 次调用 · ');
  }

  function finish(story){
    var v = story.verdict;
    verdict.hidden = false;
    verdict.className = 'verdict glass ' + (v.kind || '');
    var acts = '';
    if(story.trace){
      acts += '<a href="/view?name=' + encodeURIComponent('play/' + story.trace) + '">' +
              '打开这次运行的全过程 →</a>';
    }
    acts += '<button class="ghost" type="button" id="again">' +
            (story.rules ? '关掉判停规则，再跑一遍' : '开着判停规则，再跑一遍') + '</button>';
    acts += '<span class="eta">' + story.steps + ' 步 · ' +
            (story.rules ? '判停规则开着' : '判停规则关着') + '</span>';
    verdict.innerHTML = '<h3>' + esc(v.title) + '</h3><p>' + esc(v.detail) + '</p>' +
                        '<div class="acts">' + acts + '</div>';
    var again = document.getElementById('again');
    if(again){
      again.addEventListener('click', function(){
        rulesBox.checked = !story.rules;
        syncRules();
        startRun();
        window.scrollTo({top: 0, behavior: 'smooth'});
      });
    }
  }

  function play(story){
    reset();
    var frames = story.frames || [];
    var i = 0;
    playing = story;
    if(timer){ clearTimeout(timer); timer = null; }
    function tick(){
      if(i >= frames.length){ finish(story); go.disabled = false; go.textContent = '再跑一次'; return; }
      step(frames[i], story);
      i += 1;
      timer = setTimeout(tick, 620);
    }
    tick();
  }

  function startRun(){
    if(timer){ clearTimeout(timer); timer = null; }
    go.disabled = true;
    go.textContent = '正在跑…';
    reset();
    describe();
    empty.hidden = false;
    empty.style.display = '';
    empty.innerHTML = '<b>真的在跑。</b>它在启动一个网关进程、连着数据库查东西，' +
      '不是假动画 —— 一次大约 5 到 10 秒。';
    fetch('/api/play?token=' + encodeURIComponent(TOKEN), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task: sel.value, rules: rulesBox.checked})
    }).then(function(r){
      return r.json().then(function(b){ return {status: r.status, body: b}; });
    }).then(function(res){
      if(res.status !== 200){
        go.disabled = false; go.textContent = '跑';
        empty.innerHTML = '<b>没跑起来。</b>' + esc(res.body.error || '被拒绝');
        return;
      }
      play(res.body);
    }).catch(function(err){
      go.disabled = false; go.textContent = '跑';
      empty.innerHTML = '<b>请求失败。</b>' + esc(String(err));
    });
  }

  go.addEventListener('click', startRun);
})();
"""


def _e(value: Any) -> str:
    import html

    return html.escape(str(value), quote=True)


def render_play(
    *,
    choices: Sequence[Dict[str, Any]],
    token: str,
    error: str = "",
) -> str:
    """The page shell. Everything that varies per run arrives later as JSON, so
    this HTML is the same bytes for the same task list."""
    options = "".join(
        f'<option value="{_e(item["id"])}">{_e(item["id"])} · {_e(item["goal"][:52])}…</option>'
        for item in choices
    )
    tasks_json = json.dumps(choices, ensure_ascii=False).replace("<", "\\u003c")
    token_json = json.dumps(token).replace("<", "\\u003c")
    script = _JS.replace("__TASKS__", tasks_json).replace("__TOKEN__", token_json)

    body = (
        f'<div class="placeholder" id="empty"><b>还没开始。</b>'
        f"挑一件活儿，按下「跑」。<br>"
        f"你会看到它每一步调了什么、拿回来什么，以及它当时<b>被允许选</b>哪些工具。</div>"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>亲手跑一次 · MCP gateway</title>
<style>{BASE_CSS}{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hero glass rise">
    <div class="eyebrow">亲手跑一次</div>
    <h1>别读报告了，看着它跑一遍</h1>
    <p class="lede">
      下面每一条都是真任务：让一个 AI 去数据库里查东西，比如「把所有未解决的故障
      连同仓库和严重程度列出来，再给出每个仓库的星数」。<b>按「跑」它就会真的开始</b>，
      一步一个调用，你能看见它每一步伸手拿了什么、拿回来什么。
    </p>
    <p class="lede" style="margin-top:.6rem">
      两件事分开看：<b>每一步只摆出用得上的工具</b>是常开的，这就是「少绕路」；
      而<b>判停规则</b>管的是另一件事——<b>它什么时候可以停下</b>。
      把判停规则关掉再跑一遍，就是「没人管它停不停」的样子。
    </p>
  </header>

  <section class="console glass rise-2">
    <label class="field"><span>挑一件活儿</span>
      <select id="task">{options}</select>
    </label>
    <label class="toggle">
      <input type="checkbox" id="rules" checked>
      <span class="track"><span class="knob"></span></span>
      <b id="rules-label">判停规则：开</b>
    </label>
    <button class="go" id="go" type="button">跑</button>
    <span class="eta">一次 5–10 秒</span>
  </section>

  <p class="goal glass" id="goal" hidden></p>

  <div class="stage">
    <section class="panel glass rise-3">
      <h2>它每一步在做什么</h2>
      <ol class="timeline" id="timeline"></ol>
      {body}
    </section>

    <aside class="panel glass rise-3">
      <h2>此刻的状况</h2>
      <div class="meter">
        <div class="big" id="calls">0</div>
        <div class="cap" id="cap">还没有调用</div>
      </div>
      <div class="meter">
        <div class="cap" style="margin:0 0 .1rem">要求的证据</div>
        <div class="ev" id="ev"></div>
      </div>
    </aside>
  </div>

  <div class="verdict glass" id="verdict" hidden></div>

  <footer class="page">
    这一页会真的启动进程、真的写一条轨迹（只在 <code>traces/play/</code> 下），
    所以需要本页携带的 token，服务只绑 127.0.0.1。
    每一步的「能选几个工具」是网关当轮<b>真正展示给模型</b>的窗口，
    不是事后美化过的数字。
  </footer>
</div>
<script>{script}</script>
</body>
</html>
"""
