"""The gateway<->agent notice format: one contract, two readers.

Cheap to test, expensive to get wrong: if the gateway emits a notice the agent
cannot parse, a halted run looks like a normal result and the agent keeps
calling into a closed gateway -- which still refuses, but the demo looks broken.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.wire import (  # noqa: E402
    KIND_CLOSED,
    KIND_HALT,
    NOTICE_PREFIX,
    format_notice,
    parse_notice,
)


class NoticeFormatTests(unittest.TestCase):
    def test_round_trip(self):
        text = format_notice(
            KIND_HALT, state="HALTED_COMPLETE", decision="halt_complete", rule="R4", reason="ok"
        )
        parsed = parse_notice(text)
        self.assertEqual(parsed["kind"], "halt")
        self.assertEqual(parsed["state"], "HALTED_COMPLETE")
        self.assertEqual(parsed["decision"], "halt_complete")
        self.assertEqual(parsed["rule"], "R4")
        self.assertEqual(parsed["reason"], "ok")

    def test_notice_starts_with_the_prefix(self):
        text = format_notice(KIND_CLOSED, state="ABORTED")
        self.assertTrue(text.startswith(NOTICE_PREFIX))

    def test_reason_containing_the_separator_survives(self):
        # Reasons are prose and will eventually contain " | ". The reason is
        # emitted last and absorbs the remainder, so it cannot corrupt fields.
        text = format_notice(KIND_HALT, state="HALTED_BUDGET", reason="a | b | c")
        parsed = parse_notice(text)
        self.assertEqual(parsed["state"], "HALTED_BUDGET")
        self.assertEqual(parsed["reason"], "a | b | c")

    def test_notice_is_found_even_when_appended_to_other_blocks(self):
        text = '{"rows": []}\n' + format_notice(KIND_HALT, state="HALTED_COMPLETE")
        parsed = parse_notice(text)
        self.assertEqual(parsed["kind"], "halt")
        self.assertEqual(parsed["state"], "HALTED_COMPLETE")

    def test_plain_result_is_not_a_notice(self):
        self.assertIsNone(parse_notice('{"rows": []}'))
        self.assertIsNone(parse_notice(""))
        self.assertIsNone(parse_notice("[gateway]"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
