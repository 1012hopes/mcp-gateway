"""The committed sample must stay trustworthy.

If ``traces/samples/demo-12-steps.jsonl`` is corrupted (or hand-edited during a
demo), every screenshot and every claim in the README loses its evidence. So
the sample is tested like code: it must verify, and it must be regenerable
byte-for-byte.
"""

import importlib.util
import unittest
from pathlib import Path

from gateway.trace.verify import verify_path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "traces" / "samples" / "demo-12-steps.jsonl"
SCRIPT = ROOT / "scripts" / "make_sample.py"


def load_sample_module():
    spec = importlib.util.spec_from_file_location("make_sample", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SampleTraceTests(unittest.TestCase):
    def test_committed_sample_verifies(self):
        result = verify_path(SAMPLE)
        self.assertTrue(result.ok, result.summary())
        self.assertEqual(result.records, 12)

    def test_sample_is_byte_identical_to_a_fresh_build(self):
        module = load_sample_module()
        rebuilt = "".join(r.to_json_line() + "\n" for r in module.build_records())
        self.assertEqual(rebuilt, SAMPLE.read_text(encoding="utf-8"))

    def test_sample_reaches_evidence_completeness_at_step_three(self):
        """The story only works if the first three steps are the sufficient ones."""
        module = load_sample_module()
        record = module.build_records()[2]
        self.assertEqual(
            record.stop_check.evidence, ["commit.list", "file.content", "repo.list"]
        )

    def test_sample_never_claims_an_early_halt(self):
        """Baseline: 12 steps, none of them stopped early."""
        module = load_sample_module()
        records = module.build_records()
        self.assertEqual(sum(1 for r in records if r.stop_check.hit), 1)
        self.assertEqual(records[-1].decision, "halt_budget")


if __name__ == "__main__":
    unittest.main()
