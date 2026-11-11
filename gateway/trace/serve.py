r"""A local browser for a directory of traces.

    python -m gateway.trace serve --dir traces

Why this exists when ``render`` already makes a page: ``render`` answers "what
happened in *this* run". The question that comes immediately after is "which of
these runs should I look at". Answering that by listing a directory in a shell
and opening file after file is where a reviewer stops reviewing.

Two pages, and the difference between them is the whole point:

* ``/`` and ``/view`` **read**. Every number comes off disk at request time --
  no cache -- because a cache would eventually show a run that has since been
  tampered with as still intact, and that is the one lie this project cannot
  afford (DESIGN 2.7: the reading side of the UI never produces data).
* ``/play`` **runs**. It starts a real gateway and a real agent, writes one new
  trace under ``traces/play/``, and plays the calls back one at a time. This is
  the only part of the UI that mutates anything, and it is fenced accordingly:
  whitelisted task ids, token required, its own output directory. Before it
  existed the project had no answer to "what does this thing do" other than
  "read this report", which assumes the reader already cares.

What it is **not**: a management console. Nothing here configures the gateway,
edits a task, or changes a rule. The only two actions are "re-run a demo" and
"run one task to completion", both fixed lists.

Two things this module has to get right that ``render`` never had to:

1. **A name from the URL is not a path.** ``/view?name=../../etc/passwd`` is the
   obvious attack, but the interesting ones are subtler: a name that resolves
   inside the directory through a symlink, or an absolute path that only *looks*
   relative. ``resolve_trace`` normalises first and then checks containment,
   rather than trying to filter the string -- string filters lose to
   ``..%2f``, ``....//`` and Windows backslashes.

2. **It can start a subprocess.** Re-running a demo and playing a task are the
   only actions, so both are fixed whitelists, both require the per-session
   token, and the server binds 127.0.0.1 and checks the ``Host`` header. All of
   it is needed: a random web page in the same browser can POST to localhost
   (CSRF), and DNS rebinding can make an attacker's domain resolve to it.

The index page is deliberately a table, not a dashboard. It shows four facts per
file -- name, steps, size, verdict -- because those are the four that decide
whether to open it. Anything else would be decoration standing where evidence
should be.
"""

from __future__ import annotations

import html
import json
import re
import secrets
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, quote, urlparse

from .charts import Report, chart_accuracy, chart_calls, chart_task_steps, load_report
from .play import render_play, run_once, task_choices
from .render import load_replay, render_html
from .theme import BASE_CSS

__all__ = [
    "TraceEntry",
    "MAX_SCAN_BYTES",
    "DEMOS",
    "resolve_trace",
    "scan",
    "render_index",
    "make_server",
    "serve",
    "main",
]

#: A trace bigger than this is listed but not verified on the index. Verifying
#: it would make the index take a second per file on a directory someone just
#: pointed at a large run folder; the file is still openable, and "not checked"
#: is printed rather than a green dot that was never earned.
MAX_SCAN_BYTES = 4 * 1024 * 1024

#: How many rows the index draws. ``traces/runs`` accumulates one file per demo
#: run -- 534 of them on the machine this was written on -- and a page of 534
#: rows is a page nobody scrolls. The rest are counted and named as "not shown"
#: rather than quietly omitted.
MAX_LISTED = 60

#: Demo scripts the page may run. A whitelist, not a directory scan: scanning
#: would mean "whatever a file is named" decides what gets executed, and the
#: point of the list is that a new script has to be added here on purpose.
DEMOS: Tuple[Tuple[str, str], ...] = (
    ("agent_demo", "一条任务：baseline 与 governed 各跑一遍"),
    ("g2_demo", "多上游：两台上游同名工具、脏 schema、断连"),
    ("evidence_demo", "证据模型：R4 在证据齐的那一刻收束"),
    ("boundary_demo", "run 边界：父子 run、脱敏、截断"),
    ("compat_demo", "兼容性：三种脏上游的归一化"),
    ("gating_demo", "准入：K 曲线与窗口稳定性"),
    ("replay_demo", "回放：渲染 → 篡改 → 再渲染，37 项自检"),
    ("tamper_demo", "篡改：改一字节，从那一行起标红"),
    ("failclosed_demo", "失败关闭：写不进记录就不服务"),
)

_STATUS_LABEL = {
    "ok": "链完整",
    "bad": "链断裂",
    "unreadable": "读不到",
    "skipped": "未校验",
}

_NAME = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/-]*\.jsonl$")


# -- scanning -------------------------------------------------------------


@dataclass(frozen=True)
class TraceEntry:
    """One file in the directory, with the verdict the index will show.

    ``status`` is four-valued on purpose. A trace that cannot be read and a
    trace that did not verify are different problems with different next steps,
    and a two-valued "good/bad" would report a permission error as tampering --
    the same mistake ``cli.py`` has three exit codes to avoid.
    """

    name: str
    size: int
    steps: int
    ok: bool
    detail: str
    status: str


def _describe(path: Path, size: int) -> TraceEntry:
    name = path.name
    try:
        replay = load_replay(path)
    except Exception as exc:  # noqa: BLE001 -- see below
        # Deliberately broad. The index exists to answer "what is in this
        # directory"; a single pathological file that raises something
        # unexpected must not take the whole page down with it, or the reviewer
        # loses the list of the files that *are* fine. The exception type is
        # kept in the detail text rather than swallowed, so an unexpected one
        # shows up as "UnexpectedError" on screen instead of as a blank row.
        return TraceEntry(
            name=name,
            size=size,
            steps=0,
            ok=False,
            detail=f"打开时抛出 {type(exc).__name__}: {exc}",
            status="unreadable",
        )

    verdict = replay.verdict
    if verdict.ok:
        return TraceEntry(
            name=name,
            size=size,
            steps=len(replay.records),
            ok=True,
            detail=f"{verdict.records} 条记录，可续写",
            status="ok",
        )
    where = (
        f"line {verdict.first_bad_line}"
        if verdict.first_bad_line is not None
        else "无坏行"
    )
    return TraceEntry(
        name=name,
        size=size,
        steps=len(replay.records),
        ok=False,
        detail=f"{where} · {verdict.reason}",
        status="bad",
    )


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def scan(root: Path, *, limit: Optional[int] = None) -> List[TraceEntry]:
    """Every ``*.jsonl`` under ``root``, newest first, optionally truncated.

    Newest first because the question that brings someone here is almost always
    "what just happened" -- the demo they ran ten seconds ago. Ties break on the
    relative name so the order is total: two runs can share an mtime to the
    second, and a list that swaps rows between refreshes is worse than one that
    is a little stale.

    ``limit`` truncates *after* sorting, so "the newest N" means the newest N
    and not "the first N that happened to sort early".
    """
    root = Path(root)
    paths = list(root.rglob("*.jsonl"))
    paths.sort(
        key=lambda item: (-_mtime(item), item.relative_to(root).as_posix())
    )
    if limit is not None:
        paths = paths[:limit]
    entries: List[TraceEntry] = []
    for path in paths:
        # ``relative_to`` rather than ``path.name``: the directory has
        # ``runs/`` and ``samples/``, and two files called ``demo.jsonl`` in
        # different subdirectories must not collapse into one row.
        shown = path.relative_to(root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            entries.append(
                TraceEntry(shown, -1, 0, False, f"stat 失败: {exc.strerror or exc}", "unreadable")
            )
            continue
        if size > MAX_SCAN_BYTES:
            entries.append(
                TraceEntry(
                    shown,
                    size,
                    0,
                    False,
                    f"{size} 字节 > {MAX_SCAN_BYTES} 上限，未校验（仍可打开）",
                    "skipped",
                )
            )
            continue
        described = _describe(path, size)
        entries.append(
            TraceEntry(
                name=shown,
                size=described.size,
                steps=described.steps,
                ok=described.ok,
                detail=described.detail,
                status=described.status,
            )
        )
    return entries


# -- path safety ----------------------------------------------------------


def resolve_trace(root: Path, name: str) -> Path:
    """Turn a name from a query string into a file inside ``root``.

    Raises ``LookupError`` -- not ``OSError`` -- because "that name is not
    allowed" and "that file is gone" are answers to the same question and both
    become a 404 with the reason attached. The containment check runs on the
    *resolved* path, after ``resolve()`` has collapsed ``..`` and followed
    symlinks: checking the string first would pass ``link-to-elsewhere`` and
    fail nothing else.
    """
    if not name:
        raise LookupError("缺少 name 参数")
    if not _NAME.match(name):
        raise LookupError("名字只能由字母数字 . _ - / 组成，且必须以 .jsonl 结尾")
    if ".." in name.split("/"):
        raise LookupError("名字里不允许 ..")

    root = Path(root).resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise LookupError("这个路径不在被浏览的目录里")
    if not candidate.is_file():
        raise LookupError("没有这个文件")
    return candidate


# -- index page -----------------------------------------------------------


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _size_text(size: int) -> str:
    if size < 0:
        return "—"
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KB"


_INDEX_CSS = """
.wrap{max-width:1120px;margin:0 auto;padding:2rem 1.6rem 4rem}
.hero{padding:1.6rem 1.8rem;margin-bottom:1.1rem}
.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted)}
.hero h1{margin:.3rem 0 .45rem;font-size:1.6rem;letter-spacing:-.015em}
.lede{margin:0;color:var(--muted);font-size:13.5px;max-width:64ch}
.kpis{display:flex;gap:2.6rem;margin-top:1.3rem;flex-wrap:wrap}
.kpi b{display:block;font-size:1.5rem;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi b .arrow{color:var(--muted);font-weight:400;padding:0 .18em}
.kpi span{display:block;margin-top:.15rem;font-size:11px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--muted)}
.lede b{color:var(--ink);font-weight:600}
.explain{display:grid;grid-template-columns:repeat(3,1fr);gap:.9rem;margin-top:1.2rem}
.explain .item{padding:.75rem .85rem;border-radius:11px;background:rgba(255,255,255,.5);
  border:1px solid var(--line);transition:transform .2s ease,box-shadow .2s ease}
.explain .item:hover{transform:translateY(-1px);box-shadow:0 10px 22px -16px rgba(16,24,40,.5)}
.explain .item b{display:block;font-size:13px;margin-bottom:.2rem}
.explain .item span{font-size:12px;color:var(--muted);line-height:1.55}
@media (max-width:780px){.explain{grid-template-columns:1fr}}
.play-cta{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin:1.1rem 0 0;font-size:12.5px;
  color:var(--muted)}
.play-btn{display:inline-block;font-size:13.5px;font-weight:600;color:#fff;text-decoration:none;
  padding:.5rem 1.05rem;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent-2));
  box-shadow:0 10px 24px -12px rgba(11,107,91,.9);transition:transform .18s ease,box-shadow .18s ease}
.play-btn:hover{text-decoration:none;transform:translateY(-1px);
  box-shadow:0 14px 28px -12px rgba(11,107,91,.95)}
.grid{display:grid;gap:1.1rem;margin-bottom:1.1rem}
.grid.two{grid-template-columns:1.2fr .8fr}
@media (max-width:880px){.grid.two{grid-template-columns:1fr}}
.card{padding:1.05rem 1.25rem;margin-bottom:1.1rem}
.card h2{margin:0;font-size:11.5px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.card .hint{margin:.5rem 0 0;font-size:12px;color:var(--muted)}
.chart{display:block;margin:.7rem 0 .2rem;overflow:visible}
.chart text{font-family:-apple-system,"Segoe UI","Noto Sans SC",system-ui,sans-serif}
table.traces{width:100%;border-collapse:collapse;margin-top:.7rem;font-size:13px}
table.traces th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);font-weight:500;padding:.4rem .6rem;border-bottom:1px solid var(--line)}
table.traces td{padding:.45rem .6rem;border-bottom:1px solid var(--line);vertical-align:top}
table.traces tbody tr{transition:background .18s ease}
table.traces tbody tr:hover{background:rgba(255,255,255,.55)}
table.traces tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{font-size:11px;padding:.1rem .5rem;border-radius:999px;border:1px solid;white-space:nowrap;
  display:inline-block;line-height:1.5}
.tag-ok{color:var(--accent);border-color:rgba(11,107,91,.35);background:var(--accent-soft)}
.tag-bad{color:var(--bad);border-color:rgba(179,38,30,.35);background:var(--bad-soft)}
.tag-unreadable{color:var(--warn);border-color:rgba(138,90,0,.35);background:var(--warn-soft)}
.tag-skipped{color:var(--muted);border-color:var(--line)}
tr.row-bad td:first-child{box-shadow:inset 3px 0 0 var(--bad)}
.buttons{display:flex;flex-wrap:wrap;gap:.55rem;margin:.8rem 0}
.buttons button{font:inherit;font-size:13px;text-align:left;cursor:pointer;color:inherit;
  background:rgba(255,255,255,.6);border:1px solid var(--line);border-radius:10px;
  padding:.45rem .7rem;transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
.buttons button:hover{transform:translateY(-1px);border-color:var(--line-strong);
  box-shadow:0 6px 16px -10px rgba(16,24,40,.45)}
.buttons button:active{transform:none}
.buttons button:disabled{opacity:.45;cursor:progress;transform:none;box-shadow:none}
.buttons button code{display:block;font-size:12.5px;font-weight:600}
.buttons button span{display:block;font-size:11.5px;color:var(--muted);margin-top:.12rem;font-weight:400}
pre.out{background:rgba(22,24,29,.045);border:1px solid var(--line);border-radius:10px;
  padding:.7rem .8rem;margin:.2rem 0 0;font-size:12px;line-height:1.55;max-height:26rem;
  overflow:auto;white-space:pre-wrap}
.empty{border:1px dashed var(--line-strong);border-radius:12px;padding:1.3rem;color:var(--muted);
  font-size:13px;background:rgba(255,255,255,.4)}
.stale{margin-top:.5rem;font-size:12px;color:var(--warn)}
footer.page{max-width:1120px;margin:1.6rem auto 0;color:var(--muted);font-size:12px;line-height:1.6}
"""

_INDEX_JS = """
(function(){
  var buttons=[].slice.call(document.querySelectorAll('[data-demo]'));
  var out=document.getElementById('out');
  if(!buttons.length||!out) return;
  buttons.forEach(function(button){
    button.addEventListener('click', function(){
      var name=button.dataset.demo;
      out.textContent='运行中… '+name+'（最长等 3 分钟）';
      buttons.forEach(function(other){other.disabled=true;});
      fetch('/run?token='+encodeURIComponent(TOKEN), {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({demo:name})
      }).then(function(response){
        return response.json().then(function(body){return {status:response.status, body:body};});
      }).then(function(result){
        var body=result.body;
        if(result.status!==200){
          out.textContent='['+result.status+'] '+(body.error||'请求被拒绝');
          return;
        }
        var head=(body.exit===0?'[PASS] ':'[exit '+body.exit+'] ')+body.demo;
        out.textContent=head+'\\n\\n'+(body.out||'（无输出）')+(body.err?'\\n--- stderr ---\\n'+body.err:'');
      }).catch(function(error){
        out.textContent='请求失败：'+error;
      }).then(function(){
        buttons.forEach(function(other){other.disabled=false;});
      });
    });
  });
})();
"""


def _verdict_counts(entries: Sequence[TraceEntry], total: Optional[int] = None) -> str:
    """One line of counts. Says *which* population the bad count describes.

    With truncation on, "3 个链断裂" out of 538 files would be a claim about
    files that were never opened. The line therefore names the shown slice when
    there is one: the count is true of what was checked and says so.
    """
    shown = len(entries)
    everything = total if total is not None else shown
    if not everything:
        return "0 个文件"
    parts = [f"共 {everything} 个文件" if everything > shown else f"{shown} 个文件"]
    if everything > shown:
        parts.append(f"显示最近 {shown} 个")
    for status in ("bad", "unreadable", "skipped"):
        count = sum(1 for entry in entries if entry.status == status)
        if count:
            where = "其中" if everything == shown else "这 %d 个里" % shown
            parts.append(f"{where} {count} 个{_STATUS_LABEL[status]}")
    return " · ".join(parts)


def _kpi(value: str, label: str) -> str:
    """One headline number. ``value`` may carry markup (the arrow), hence ``_e``
    on the label only -- callers pass literal digits, not user data."""
    return f'<div class="kpi"><b>{value}</b><span>{_e(label)}</span></div>'


def _render_row(entry: TraceEntry) -> str:
    href = f"/view?name={quote(entry.name)}"
    return (
        f'<tr class="row-{_e(entry.status)}">'
        f'<td><a href="{_e(href)}"><code>{_e(entry.name)}</code></a></td>'
        f'<td class="num">{entry.steps if entry.steps else "—"}</td>'
        f'<td class="num">{_e(_size_text(entry.size))}</td>'
        f'<td><span class="tag tag-{_e(entry.status)}">{_e(_STATUS_LABEL[entry.status])}</span> '
        f'<span class="muted">{_e(entry.detail)}</span></td>'
        "</tr>"
    )


def render_index(
    entries: Sequence[TraceEntry],
    root: Path,
    *,
    token: str,
    report: Optional[Report] = None,
    total: Optional[int] = None,
) -> str:
    """The index page. Same inputs -> same bytes (no clock, no order).

    ``report`` is ``None`` when ``results/table.md`` is missing or unparsable,
    and that state is rendered as an empty card with the command that produces
    it -- not as a chart of zeros. A zero is a measurement.
    """
    if entries:
        rows = "".join(_render_row(entry) for entry in entries)
        table = (
            '<table class="traces"><thead><tr>'
            "<th>文件</th><th class=\"num\">步</th><th class=\"num\">大小</th><th>结论</th>"
            f"</tr></thead><tbody>{rows}</tbody></table>"
        )
    else:
        table = (
            '<div class="empty">这个目录里没有 <code>*.jsonl</code>。'
            "先跑 <code>python scripts/replay_demo.py</code> 或 "
            "<code>python scripts/agent_demo.py</code> 生成一条轨迹。</div>"
        )

    buttons = "".join(
        f'<button type="button" data-demo="{_e(name)}">'
        f"<code>{_e(name)}</code><span>{_e(blurb)}</span></button>"
        for name, blurb in DEMOS
    )

    # The token is hex in practice, but this function is public and a caller
    # that passes "</script>" must not be able to close the tag and run code.
    # Escaping "<" as \u003c keeps the JavaScript string valid while making the
    # sequence unrepresentable in the HTML source.
    token_literal = json.dumps(token).replace("<", "\\u003c")

    if report is not None:
        kpis = (
            '<div class="kpis">'
            + _kpi(
                f'{report.baseline_calls}<span class="arrow">→</span>{report.governed_calls}',
                "AI 调工具的次数 · 没它 → 有它",
            )
            + _kpi(
                f"{report.saved_pct:.1f}%" if report.saved_pct is not None else "—",
                "少跑的冤枉路",
            )
            + _kpi(_e(report.right_governed), "该停的时候停了")
            + _kpi(_e(report.early_governed), "没查完就收工")
            + "</div>"
        )
        saved = (
            f"{report.saved_pct:.1f}%" if report.saved_pct is not None else "—"
        )
        charts = (
            '<section class="card glass rise-2">'
            "<h2>每个任务的步数</h2>"
            f"{chart_task_steps(report.tasks)}"
            '<p class="hint">灰 = 手数的最优步数，红 = 基线，绿 = 网关。三根并排才看得出'
            "“网关落在最优上”；只画红绿两色，只能得出“省了”，而“省了”本身不是论点。</p>"
            "</section>"
            '<div class="grid two">'
            f'<section class="card glass rise-3"><h2>调用总数</h2>{chart_calls(report)}'
            f'<p class="hint">省 {_e(saved)}。数字来自 <code>results/table.md</code>，'
            "页面不自己算。</p></section>"
            f'<section class="card glass rise-3"><h2>Accuracy@K</h2>{chart_accuracy(report)}'
            '<p class="hint">纵轴是 0.90–1.00，不是 0–1：两个点都贴着顶的时候，'
            "全量程图什么也说明不了。</p></section>"
            "</div>"
        )
    else:
        kpis = ""
        charts = (
            '<section class="card glass rise-2"><h2>指标</h2>'
            '<div class="empty">还没有 <code>results/table.md</code>。'
            "跑一次 <code>python scripts/experiment.py</code>，刷新就会出现三张图。"
            "现在空着，是因为没有可画的数——不是因为数是零。</div></section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>轨迹与指标 · {_e(root.name)}</title>
<style>{BASE_CSS}{_INDEX_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="hero glass rise">
    <div class="eyebrow">MCP gateway · AI 干活时的监工</div>
    <h1>让 AI 少走弯路，走完还得说得清</h1>
    <p class="lede">
      AI 帮你查数据、翻文件时有两个老毛病：<b>绕远路</b>（3 步能查完，它查了 9 步），
      和<b>没查完就说完了</b>。这个东西插在 AI 和工具中间，管住这两件事；
      它还会把每一步记成一笔改不掉的账——事后有人动过一个字节，它就能指出是哪一步。
    </p>
    <div class="explain">
      <div class="item"><b>少绕路</b><span>每一步只把它用得上的工具摆出来，其余收起来，省下的是来回翻同一张表的时间。</span></div>
      <div class="item"><b>不许糊弄</b><span>该拿的证据没拿齐，就不让它说“做完了”——停不停由证据决定，不由它自己说了算。</span></div>
      <div class="item"><b>说得清</b><span>每一步留一条账，每笔记着上一笔的指纹；改一处，后面整条链就对不上。</span></div>
    </div>
    <p class="play-cta">
      <a class="play-btn" href="/play">亲手跑一次 →</a>
      <span>真的启动一个 AI 去查数据，看着它一步步走；关掉网关再跑一遍就是对照组。</span>
    </p>
    {kpis}
  </header>

  {charts}

  <section class="card glass rise-3">
    <h2>每一次干活的记录</h2>
    <p class="hint"><code>{_e(root)}</code> · {_e(_verdict_counts(entries, total))} ·
      点开任意一条，能看到 AI 每一步干了什么、什么时候被允许停、这中间有没有人动过手脚。</p>
    {table}
  </section>

  <section class="card glass rise-3">
    <h2>自己跑一遍</h2>
    <p class="hint">
      下面每一行都是一条现成的演示，点了就在这台机器上真跑一次，输出原样贴回来。
      浏览器自己不写任何东西——它只是替你敲了那条命令。
    </p>
    <div class="buttons">{buttons}</div>
    <pre class="out" id="out">（选一个跑）</pre>
  </section>

  <footer class="page">
    上面这些数字都是这台机器上真跑出来的（<code>results/table.md</code>），不是写死的。
    页面只读：不改记录、不缓存结论。服务端每次刷新都重新读磁盘——一份刚被改过的记录会立刻
    显示成断裂，而不是像缓存那样继续说它完好。
  </footer>
</div>
<script>var TOKEN={token_literal};{_INDEX_JS}</script>
</body>
</html>
"""


# -- running a demo -------------------------------------------------------


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run_demo(name: str, *, timeout: int = 180) -> Dict[str, Any]:
    """Run one whitelisted demo script and return its output.

    ``timeout`` is not decoration: a demo that hangs would hold the request
    thread and leave the page spinning with no way to tell "still working" from
    "hung". On timeout the answer says so, and the child is killed by
    ``subprocess.run`` itself.
    """
    allowed = dict(DEMOS)
    if name not in allowed:
        raise LookupError(f"不在白名单里：{name}")
    root = project_root()
    script = root / "scripts" / f"{name}.py"
    if not script.is_file():
        raise LookupError(f"脚本不存在：{script}")
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {
            "demo": name,
            "exit": -1,
            "out": "",
            "err": f"超时（{timeout}s）被终止",
        }
    return {
        "demo": name,
        "exit": completed.returncode,
        # Trimmed: a demo can print a lot, and the point of the box is the last
        # few lines -- whether the checks passed.
        "out": (completed.stdout or "")[-20000:],
        "err": (completed.stderr or "")[-8000:],
    }


# -- HTTP -----------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "mcp-gateway-trace"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"  {fmt % args}\n")

    # -- helpers --

    def _send(
        self,
        status: int,
        body: str,
        ctype: str = "text/html; charset=utf-8",
        *,
        close: bool = False,
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        # No caching anywhere: the whole value of this page is that it shows
        # what is on disk *now*. A cached index would keep saying "链完整"
        # about a file that was edited since -- the exact failure mode the
        # project spends its tests on.
        self.send_header("Cache-Control", "no-store")
        if close:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in ("127.0.0.1", "localhost", "[::1]")

    def _error_page(self, status: int, message: str) -> None:
        # Refusals are decided before the request body is read, so whatever the
        # client already sent is still sitting in the socket. On a keep-alive
        # connection the next read would parse that body as a request line
        # ("Bad HTTP/0.9 request type") -- so close instead. Cheap, and it makes
        # "refused" mean refused rather than "refused but I will keep talking".
        self.close_connection = True
        body = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{status}</title><style>{BASE_CSS}</style></head>
<body><div style="max-width:44rem;margin:3rem auto;padding:0 1.6rem">
<h1 style="font-size:1.1rem">{status}</h1>
<p>{_e(message)}</p>
<p><a href="/">← 全部轨迹</a></p></div></body></html>"""
        self._send(status, body, close=True)

    # -- routes --

    def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's naming
        if not self._host_ok():
            # DNS rebinding lands here: a request arrives with the attacker's
            # domain in Host while the socket is 127.0.0.1. Refusing is cheap;
            # the alternative is browsing someone's traces from a web page.
            self._error_page(403, "Host 头不是本机地址，拒绝服务")
            return

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(200, "ok", "text/plain; charset=utf-8")
            return

        if parsed.path == "/play":
            try:
                choices = task_choices()
            except (OSError, ValueError) as exc:
                # A task file that will not load is the same class of problem
                # as a trace that will not verify: the page has to say which
                # file, not die on it.
                self._error_page(500, f"任务清单读不出来：{exc}")
                return
            self._send(
                200,
                render_play(choices=choices, token=self.server.token),  # type: ignore[attr-defined]
            )
            return

        if parsed.path == "/view":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            try:
                path = resolve_trace(self.server.root_dir, name)  # type: ignore[attr-defined]
                replay = load_replay(path)
            except LookupError as exc:
                self._error_page(404, f"打不开 {name!r}：{exc}")
                return
            except OSError as exc:
                # Readable in principle, unreadable in fact: a permission
                # denial or a vanished file. 500 rather than 404, because the
                # name was fine and something else went wrong.
                self._error_page(500, f"读取失败：{exc.strerror or exc}")
                return
            self._send(200, render_html(replay, back_href="/"))
            return

        if parsed.path in ("/", "/index.html"):
            root = self.server.root_dir  # type: ignore[attr-defined]
            entries = scan(root, limit=MAX_LISTED)
            # Counted separately rather than from ``entries``: the header has
            # to say how many files exist, and ``len(entries)`` is capped.
            total = sum(1 for _ in root.rglob("*.jsonl"))
            self._send(
                200,
                render_index(
                    entries,
                    root,
                    token=self.server.token,  # type: ignore[attr-defined]
                    # Read on every request rather than cached at startup: the
                    # demo buttons rewrite table.md, and an index that showed
                    # yesterday's numbers after a successful re-run would teach
                    # the reader not to trust the refresh.
                    report=load_report(project_root() / "results" / "table.md"),
                    total=total,
                ),
            )
            return

        self._error_page(404, f"没有这个路径：{parsed.path}")

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._error_page(403, "Host 头不是本机地址，拒绝服务")
            return

        parsed = urlparse(self.path)
        if parsed.path not in ("/run", "/api/play"):
            self._error_page(404, f"没有这个路径：{parsed.path}")
            return

        token = (parse_qs(parsed.query).get("token") or [""])[0]
        expected = str(self.server.token)  # type: ignore[attr-defined]
        # Constant-time compare: the token is short-lived and local, but a
        # timing oracle is free to avoid and embarrassing to explain.
        if not secrets.compare_digest(token, expected):
            self._error_page(403, "token 不对（这个 token 每次启动都会变）")
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if length <= 0 or length > 4096:
            # Same reason as ``_error_page``: the body was not drained, so the
            # connection cannot be reused.
            self.close_connection = True
            self._json(400, {"error": "请求体为空或过大"}, close=True)
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"请求体不是 JSON：{exc}"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "请求体必须是一个对象"})
            return

        if parsed.path == "/api/play":
            self._play(payload)
            return

        name = payload.get("demo")
        if not isinstance(name, str):
            self._json(400, {"error": "缺少 demo 字段"})
            return

        try:
            result = run_demo(name)
        except LookupError as exc:
            self._json(404, {"error": str(exc)})
            return
        self._json(200, result)

    def _play(self, payload: Dict[str, Any]) -> None:
        task_id = payload.get("task")
        if not isinstance(task_id, str) or not task_id:
            self._json(400, {"error": "缺少 task 字段"})
            return
        # ``rules`` defaults to True: the page's checkbox is the only caller,
        # and a missing field should show the guarded run, not the bare one.
        rules = payload.get("rules")
        if not isinstance(rules, bool):
            rules = True
        try:
            story = run_once(task_id, rules=rules)
        except LookupError as exc:
            self._json(404, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            # A play run can fail for a dozen reasons (a wedged upstream, a
            # task file that will not load, the SDK missing). The page needs a
            # sentence it can print, and the type name is what tells a developer
            # which of the dozen it was -- a traceback here would go to stderr
            # and the page would show "请求失败：undefined".
            self._json(
                500,
                {"error": f"{type(exc).__name__}: {exc}"},
            )
            return
        self._json(200, story)

    def _json(self, status: int, body: Dict[str, Any], *, close: bool = False) -> None:
        self._send(
            status,
            json.dumps(body, ensure_ascii=False),
            "application/json; charset=utf-8",
            close=close,
        )


def make_server(
    root: Path,
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    token: Optional[str] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) the server. Split out so tests can drive it."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"不是一个目录：{root}")
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.root_dir = root  # type: ignore[attr-defined]
    server.token = token or secrets.token_hex(16)  # type: ignore[attr-defined]
    return server


def serve(
    root: Path,
    *,
    port: int = 0,
    host: str = "127.0.0.1",
    open_browser: bool = True,
) -> None:
    server = make_server(root, port=port, host=host)
    actual = server.server_address[1]
    url = f"http://{host}:{actual}/"
    print(f"轨迹浏览器: {url}")
    print(f"  目录: {server.root_dir}")  # type: ignore[attr-defined]
    print("  浏览只读；会写东西的只有两处：重跑 demo、/play 亲手跑一次（都要页面里的 token）")
    print("  Ctrl-C 停止")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 -- a missing browser is not a failure
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止")
    finally:
        server.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gateway.trace serve",
        description="在本机起一个只读的轨迹浏览器（绑定 127.0.0.1）",
    )
    parser.add_argument("--dir", default="traces", help="要浏览的目录（默认 traces）")
    parser.add_argument(
        "--port", type=int, default=0, help="端口（默认 0 = 让系统分配）"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="不要自动打开浏览器"
    )
    args = parser.parse_args(argv)

    root = Path(args.dir)
    if not root.is_dir():
        print(f"不是一个目录: {root}", file=sys.stderr)
        return 2
    try:
        serve(root, port=args.port, open_browser=not args.no_open)
    except OSError as exc:
        # Port in use, or a socket the OS will not hand out. Exit 2 with a
        # sentence: a traceback here would say "the browser crashed" when the
        # truth is "that port is taken".
        print(f"起不来: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
