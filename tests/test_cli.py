"""The CLI's contract: three exit codes, and no traceback for any input.

Why a whole file for this. The exit code is the only thing a CI gate reads, and
the codes mean different things -- "the chain is broken" (1) versus "I could not
read the file" (2). Until D16 an unreadable path (a directory, a permission
denial) produced a traceback and exit 1, i.e. it answered "your trace was
tampered with" about a file it never opened. A missing file also returned 1, so
the two answers were the same; now they are not, and this file is what holds the
new contract still.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from gateway.trace.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "traces" / "samples" / "replay-r4.jsonl"


def run(argv):
    """Call ``main`` and return (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class VerifyTests(unittest.TestCase):
    def test_an_intact_chain_exits_zero_and_says_so(self):
        code, out, _ = run(["verify", str(SAMPLE)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("[PASS]", out)

    def test_a_broken_chain_exits_one(self):
        lines = SAMPLE.read_text(encoding="utf-8").split("\n")
        record = json.loads(lines[2])
        record["latency_ms"] += 1  # bytes change, hash no longer matches
        lines[2] = json.dumps(record, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tampered.jsonl"
            path.write_bytes("\n".join(lines).encode("utf-8"))
            code, out, _ = run(["verify", str(path)])
        self.assertEqual(code, EXIT_FAIL)
        self.assertIn("hash_mismatch", out)

    def test_a_missing_file_is_a_usage_error_not_a_verdict(self):
        code, out, err = run(["verify", "no-such-trace.jsonl"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("打不开这个路径", err)

    def test_a_directory_is_a_usage_error_and_not_a_traceback(self):
        # The failure this replaces: ``open()`` raised PermissionError out of
        # ``main`` -- on Windows a directory is "permission denied", so the
        # message would also have been wrong about the cause.
        code, _, err = run(["verify", str(ROOT)])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("打不开这个路径", err)

    def test_quiet_prints_nothing_but_still_reports_the_code(self):
        code, out, err = run(["verify", str(SAMPLE), "--quiet"])
        self.assertEqual((code, out, err), (EXIT_OK, "", ""))
        code, out, err = run(["verify", "no-such-trace.jsonl", "--quiet"])
        self.assertEqual((code, out, err), (EXIT_USAGE, "", ""))

    def test_json_mode_is_parseable_even_on_failure(self):
        lines = SAMPLE.read_text(encoding="utf-8").split("\n")
        record = json.loads(lines[0])
        record["chosen"] = None
        lines[0] = json.dumps(record, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tampered.jsonl"
            path.write_bytes("\n".join(lines).encode("utf-8"))
            code, out, _ = run(["verify", str(path), "--json"])
        self.assertEqual(code, EXIT_FAIL)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["first_bad_line"], 1)


class RenderTests(unittest.TestCase):
    def test_render_writes_a_page_and_still_reports_the_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "page.html"
            code, stdout, _ = run(["render", str(SAMPLE), "-o", str(out)])
            self.assertEqual(code, EXIT_OK)
            self.assertIn("写出", stdout)
            self.assertIn("OK:", stdout)  # the verdict travels with the page
            self.assertTrue(out.exists())

    def test_render_refuses_to_crash_on_a_missing_file(self):
        code, _, err = run(["render", "no-such-trace.jsonl"])
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("打不开这个路径", err)

    def test_a_broken_chain_is_still_rendered(self):
        """Exit 0 on purpose: the broken page is the evidence."""
        lines = SAMPLE.read_text(encoding="utf-8").split("\n")
        record = json.loads(lines[1])
        record["turn"] += 1
        lines[1] = json.dumps(record, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "tampered.jsonl"
            trace.write_bytes("\n".join(lines).encode("utf-8"))
            page = Path(tmp) / "page.html"
            code, stdout, _ = run(["render", str(trace), "-o", str(page)])
            self.assertEqual(code, EXIT_OK)
            self.assertIn("FAIL", stdout)
            self.assertIn("hash_mismatch", page.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
