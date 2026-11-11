"""The served browser: path containment, verdicts, and the two ways in.

The interesting tests here are not "does GET / return 200". They are:

* ``resolve_trace`` -- every URL that reaches it is attacker-controlled text,
  and the check has to happen after normalisation, not on the string.
* the four-valued verdict -- a file that cannot be read must not be reported as
  one that failed to verify.
* the token -- ``/run`` starts a subprocess, so the two refusals (no token,
  wrong token) are load-bearing rather than hygiene.

The HTTP half runs a real ``ThreadingHTTPServer`` on port 0. Mocking the handler
would test that the mock was called, not that a name from a query string can
escape a directory.
"""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from gateway.trace import serve as serve_module
from gateway.trace.record import StopCheck, TraceRecord, genesis_hash
from gateway.trace.serve import (
    DEMOS,
    TraceEntry,
    make_server,
    render_index,
    resolve_trace,
    scan,
)

RUN_ID = "run-serve-001"
TOKEN = "0123456789abcdef0123456789abcdef"


def _record(seq: int, prev: TraceRecord | None = None, **overrides) -> TraceRecord:
    kwargs = dict(
        seq=seq,
        run_id=RUN_ID,
        session_id="sess-serve",
        turn=seq,
        model_id="stub-model",
        gateway_version="0.6.0",
        rules_version="rules-v1",
        offered=["sqlite.query", "sqlite.count_rows"],
        chosen="sqlite.query",
        args_hash="a" * 64,
        result_digest="b" * 64,
        decision="forward",
        state="RUNNING",
        latency_ms=10,
        prev_hash=prev.hash if prev is not None else genesis_hash(RUN_ID),
    )
    kwargs.update(overrides)
    return TraceRecord(**kwargs).seal()


def _write(records, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(r.to_json_line() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _chain(length: int = 3):
    records = []
    previous = None
    for seq in range(1, length + 1):
        record = _record(seq, previous)
        records.append(record)
        previous = record
    return records


class ResolveTests(unittest.TestCase):
    """A name from a query string is text until proven to be a path inside root."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "runs").mkdir()
        _write(_chain(1), self.root / "runs" / "good.jsonl")

    def test_a_nested_name_resolves(self):
        path = resolve_trace(self.root, "runs/good.jsonl")
        self.assertTrue(path.is_file())
        self.assertEqual(path, (self.root / "runs" / "good.jsonl").resolve())

    def test_parent_traversal_is_refused(self):
        out = self.root.parent / "secret.jsonl"
        out.write_text("{}\n", encoding="utf-8")
        self.addCleanup(lambda: out.unlink(missing_ok=True))
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "../secret.jsonl")

    def test_nested_traversal_is_refused(self):
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "runs/../../secret.jsonl")

    def test_an_absolute_path_is_refused(self):
        # ``root / "/etc/passwd.jsonl"`` is "/etc/passwd.jsonl" in pathlib --
        # the join silently drops root, which is exactly why containment is
        # checked on the resolved path rather than on how the name looks.
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "/etc/passwd.jsonl")

    def test_backslashes_are_refused(self):
        # On Windows "..\\secret.jsonl" escapes; the name character class has
        # no backslash in it, so it never reaches the filesystem.
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "..\\secret.jsonl")

    def test_a_non_jsonl_suffix_is_refused(self):
        (self.root / "notes.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "notes.txt")

    def test_a_missing_file_is_refused_with_its_own_reason(self):
        with self.assertRaises(LookupError) as caught:
            resolve_trace(self.root, "runs/nope.jsonl")
        self.assertIn("没有这个文件", str(caught.exception))

    def test_an_empty_name_is_refused(self):
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "")

    def test_a_symlink_pointing_outside_is_refused(self):
        outside = self.root.parent / "outside-target.jsonl"
        outside.write_text("{}\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / "link.jsonl"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("这台机器不允许创建符号链接")
        with self.assertRaises(LookupError):
            resolve_trace(self.root, "link.jsonl")


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def by_name(self, entries):
        return {entry.name: entry for entry in entries}

    def test_a_good_chain_is_ok(self):
        _write(_chain(3), self.root / "good.jsonl")
        entry = scan(self.root)[0]
        self.assertEqual(entry.status, "ok")
        self.assertTrue(entry.ok)
        self.assertEqual(entry.steps, 3)

    def test_a_tampered_chain_is_bad_and_says_where(self):
        path = _write(_chain(3), self.root / "bad.jsonl")
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = lines[1].replace('"latency_ms":10', '"latency_ms":11')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

        entry = scan(self.root)[0]
        self.assertEqual(entry.status, "bad")
        self.assertFalse(entry.ok)
        # The index has to say *where*, because that is the difference between
        # "open it and look" and "this one is already known-broken".
        self.assertIn("line 2", entry.detail)

    def test_a_directory_named_jsonl_is_unreadable_not_ok(self):
        # rglob matches it; stat() succeeds; reading it does not. Reporting it
        # as anything but "unreadable" would be a verdict on a file that was
        # never read. (Windows raises PermissionError here, POSIX
        # IsADirectoryError -- both are OSError, and neither is "broken chain".)
        (self.root / "weird.jsonl").mkdir()
        entry = scan(self.root)[0]
        self.assertEqual(entry.status, "unreadable")
        self.assertIn("Error", entry.detail)

    def test_an_oversized_file_is_listed_but_not_verified(self):
        _write(_chain(1), self.root / "big.jsonl")
        with mock.patch.object(serve_module, "MAX_SCAN_BYTES", 10):
            entry = scan(self.root)[0]
        self.assertEqual(entry.status, "skipped")
        self.assertFalse(entry.ok)
        self.assertIn("未校验", entry.detail)
        # Still listed, and still openable: "we did not check" is not "we hid it".
        self.assertIn("仍可打开", entry.detail)

    def test_the_newest_file_is_first(self):
        older = _write(_chain(1), self.root / "older.jsonl")
        newer = _write(_chain(1), self.root / "newer.jsonl")
        os.utime(older, (1_600_000_000, 1_600_000_000))
        os.utime(newer, (1_700_000_000, 1_700_000_000))
        names = [entry.name for entry in scan(self.root)]
        self.assertEqual(names, ["newer.jsonl", "older.jsonl"])

    def test_the_limit_keeps_the_newest_not_the_first(self):
        # Truncation happens after sorting, so "the newest 2" is not "the first
        # two that sorted early" -- the distinction only shows up when the
        # newest file sorts last by name, which is exactly the case here.
        for index in range(3):
            path = _write(_chain(1), self.root / f"f{index}.jsonl")
            os.utime(path, (1_600_000_000 + index, 1_600_000_000 + index))
        names = [entry.name for entry in scan(self.root, limit=2)]
        self.assertEqual(names, ["f2.jsonl", "f1.jsonl"])

    def test_the_same_basename_in_two_directories_stays_two_rows(self):
        _write(_chain(1), self.root / "a" / "demo.jsonl")
        _write(_chain(1), self.root / "b" / "demo.jsonl")
        # Order is a separate test (it follows mtime); what matters here is that
        # ``demo.jsonl`` appears twice with its directory attached, instead of
        # once because ``path.name`` collapsed them.
        self.assertEqual(
            sorted(entry.name for entry in scan(self.root)),
            ["a/demo.jsonl", "b/demo.jsonl"],
        )


class IndexTests(unittest.TestCase):
    def entries(self):
        return [
            TraceEntry("runs/a.jsonl", 1024, 4, True, "4 条记录，可续写", "ok"),
            TraceEntry("runs/b.jsonl", 2048, 0, False, "line 2 · chain", "bad"),
        ]

    def test_the_same_entries_render_the_same_bytes(self):
        first = render_index(self.entries(), Path("traces"), token=TOKEN)
        second = render_index(self.entries(), Path("traces"), token=TOKEN)
        self.assertEqual(first, second)
        self.assertNotRegex(first, r"\b20\d\d-\d\d-\d\dT")

    def test_the_token_cannot_close_the_script_tag(self):
        page = render_index(self.entries(), Path("traces"), token="</script><b>x</b>")
        # Exactly one closing tag: the page's own. The token's "</script>" is
        # emitted as \u003c so it stays valid JavaScript and inert HTML.
        self.assertEqual(page.count("</script>"), 1)
        self.assertIn("\\u003c/script", page)

    def test_a_hostile_file_name_is_escaped(self):
        entry = TraceEntry(
            "<img src=x>.jsonl", 10, 1, False, "<b>line 1</b>", "bad"
        )
        page = render_index([entry], Path("traces"), token=TOKEN)
        self.assertIn("&lt;img src=x&gt;.jsonl", page)
        self.assertNotIn("<img src=x>", page)

    def test_the_counts_line_names_each_problem(self):
        page = render_index(self.entries(), Path("traces"), token=TOKEN)
        self.assertIn("2 个文件", page)
        self.assertIn("1 个链断裂", page)

    def test_a_truncated_list_says_the_count_is_about_the_shown_slice(self):
        # 138 files were never opened; a line reading "138 个文件 · 1 个链断裂"
        # would be a claim about all of them.
        page = render_index(self.entries(), Path("traces"), token=TOKEN, total=138)
        self.assertIn("共 138 个文件", page)
        self.assertIn("显示最近 2 个", page)
        self.assertIn("这 2 个里", page)

    def test_an_empty_directory_says_so_instead_of_drawing_a_table(self):
        page = render_index([], Path("traces"), token=TOKEN)
        self.assertIn("没有", page)
        self.assertNotIn("<table", page)

    def test_every_whitelisted_demo_gets_a_button(self):
        page = render_index([], Path("traces"), token=TOKEN)
        for name, _ in DEMOS:
            self.assertIn(f'data-demo="{name}"', page)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        _write(_chain(3), cls.root / "runs" / "good.jsonl")
        bad = _write(_chain(2), cls.root / "runs" / "bad.jsonl")
        lines = bad.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace('"latency_ms":10', '"latency_ms":11')
        bad.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

        cls.server = make_server(cls.root, token=TOKEN)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def request(self, path, *, method="GET", payload=None, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        text = response.read().decode("utf-8", "replace")
        connection.close()
        return response.status, text

    def test_health_needs_no_token(self):
        status, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")

    def test_the_index_lists_both_files_with_their_verdicts(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("runs/good.jsonl", body)
        self.assertIn("runs/bad.jsonl", body)
        self.assertIn("tag-ok", body)
        self.assertIn("tag-bad", body)

    def test_view_renders_the_same_page_as_the_renderer(self):
        status, body = self.request("/view?name=runs%2Fgood.jsonl")
        self.assertEqual(status, 200)
        self.assertIn("offered / chosen 差集", body)
        self.assertIn('href="/"', body)

    def test_view_of_a_broken_trace_still_renders(self):
        # A broken trace is exactly what a reviewer wants to look at; refusing
        # to draw it would hide the evidence behind the verdict (cli.py's note).
        status, body = self.request("/view?name=runs%2Fbad.jsonl")
        self.assertEqual(status, 200)
        self.assertIn("此处断链", body)
        self.assertIn("哈希与内容不符", body)

    def test_traversal_through_the_query_string_is_a_404(self):
        status, _ = self.request("/view?name=..%2Fsecret.jsonl")
        self.assertEqual(status, 404)

    def test_a_missing_trace_is_a_404(self):
        status, _ = self.request("/view?name=runs%2Fnope.jsonl")
        self.assertEqual(status, 404)

    def test_a_forged_host_header_is_refused(self):
        status, _ = self.request("/", host="attacker.example")
        self.assertEqual(status, 403)

    def test_an_unknown_path_is_a_404(self):
        status, _ = self.request("/nope")
        self.assertEqual(status, 404)

    def test_run_without_a_token_is_refused(self):
        status, _ = self.request("/run", method="POST", payload={"demo": "agent_demo"})
        self.assertEqual(status, 403)

    def test_run_with_the_wrong_token_is_refused(self):
        status, _ = self.request(
            "/run?token=deadbeef", method="POST", payload={"demo": "agent_demo"}
        )
        self.assertEqual(status, 403)

    def test_run_with_the_right_token_but_an_unlisted_demo_is_refused(self):
        status, body = self.request(
            f"/run?token={TOKEN}", method="POST", payload={"demo": "rm_rf"}
        )
        self.assertEqual(status, 404)
        self.assertIn("白名单", body)

    def test_run_without_a_demo_field_is_a_400(self):
        status, _ = self.request(f"/run?token={TOKEN}", method="POST", payload={})
        self.assertEqual(status, 400)


class RunDemoTests(unittest.TestCase):
    def test_a_name_outside_the_whitelist_never_becomes_a_path(self):
        # The whitelist is checked before the path is built, so a traversal in
        # the demo name is not even a traversal -- it is just not in the dict.
        with self.assertRaises(LookupError):
            serve_module.run_demo("../../etc/passwd")

    def test_the_whitelist_covers_only_real_scripts(self):
        root = serve_module.project_root()
        for name, _ in DEMOS:
            self.assertTrue(
                (root / "scripts" / f"{name}.py").is_file(),
                f"{name} 在白名单里但没有脚本",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
