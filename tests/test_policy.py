"""D10: redact before hashing, and only redact what is a credential.

One requirement (FR-14) with two failure modes, and both are silent:

* **Too little.** A token reaches ``args_hash``. The same question then hashes
  differently every time the credential rotates, so R1 never fires, R2's plateau
  never forms, and two runs cannot be compared. Nothing raises.
* **Too much.** A redactor that matches "long random strings" also eats order
  ids, commit SHAs and row content. Two genuinely different calls collapse into
  one identity and the gateway halts a healthy run -- the same asymmetry
  ``test_normalize.py`` is built around, one layer over.

So the cases below are paired the same way: what is hidden next to what merely
looks like it, and the pipeline is asserted as a composition rather than by
description (``redact -> normalize -> digest``), because a docstring claiming an
order is exactly the kind of claim that keeps being true after the code stops
being.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateway.policy import CREDENTIAL_TOKENS, REDACTED, Redactor, redact  # noqa: E402
from gateway.session import digest_result  # noqa: E402
from gateway.stop.normalize import normalize_args, spec_for  # noqa: E402
from gateway.trace.canonical import digest_of  # noqa: E402


def identify(tool, args):
    """The gateway's actual path, written out so the order is visible."""
    return digest_of(normalize_args(redact(args), spec_for(tool)))


class CredentialKeyTests(unittest.TestCase):
    def test_two_different_tokens_are_the_same_call(self):
        # FR-14's acceptance sentence.
        self.assertEqual(
            identify("sqlite.query", {"sql": "select 1", "token": "aaa"}),
            identify("sqlite.query", {"sql": "select 1", "token": "bbb"}),
        )

    def test_a_different_question_is_still_a_different_call(self):
        # The other half: redaction must not swallow everything and make the
        # whole test above pass vacuously.
        self.assertNotEqual(
            identify("sqlite.query", {"sql": "select 1", "token": "aaa"}),
            identify("sqlite.query", {"sql": "select 2", "token": "aaa"}),
        )

    def test_the_value_is_replaced_not_masked(self):
        self.assertEqual(redact({"token": "abc"}), {"token": REDACTED})

    def test_the_shape_of_a_credential_object_is_not_preserved(self):
        # Keeping the nested keys would invite a reader to think the structure
        # was meaningful. It is not: none of it may be compared.
        self.assertEqual(
            redact({"credentials": {"user": "me", "token": "x"}}),
            {"credentials": REDACTED},
        )

    def test_name_variants_are_one_key(self):
        for key in ("api_key", "apiKey", "X-API-Key", "API-KEY"):
            with self.subTest(key=key):
                self.assertEqual(redact({key: "v"}), {key: REDACTED})

    def test_near_miss_keys_are_left_alone(self):
        # ``maxtokens`` and ``tokenizer`` are different words, and a plural
        # ("tokens") is deliberately not covered: it usually means a count.
        # Missing a secret here is the cheap direction -- it costs one missed
        # duplicate, not a wrong decision.
        for key in ("maxtokens", "tokenizer", "author", "tokens", "secretary"):
            with self.subTest(key=key):
                self.assertEqual(redact({key: "v"}), {key: "v"})

    def test_an_absent_credential_is_not_invented(self):
        # Replacing ``None`` with a marker would make "no token sent" and "a
        # token was sent" different in a way the caller never declared.
        self.assertEqual(redact({"token": None}), {"token": None})
        self.assertEqual(redact({"token": ""}), {"token": ""})


class CredentialShapeTests(unittest.TestCase):
    def test_a_github_token_inside_a_message_is_scrubbed(self):
        text = "auth failed for ghp_abcdefghijklmnopqrstuvwxyz0123"
        self.assertEqual(redact({"message": text}), {"message": "auth failed for <redacted>"})

    def test_a_bearer_header_is_scrubbed(self):
        self.assertEqual(
            redact({"headers": "Authorization: Bearer abcdefgh12345678"}),
            {"headers": "Authorization: <redacted>"},
        )

    def test_a_token_is_scrubbed_even_under_an_innocent_key(self):
        # The key is not a credential name; the value is a credential. Both
        # judgments have to hold, independently.
        self.assertEqual(
            redact({"note": "sk-abcdefghijklmnopqrst"}), {"note": "<redacted>"}
        )

    def test_ordinary_text_survives(self):
        for text in (
            "sk-learn is a library",
            "ghp_short",
            "select * from repos where stars > 9000",
            "commit 5eea80b252b4b6d2867f626a5e0cf48993cd8b09",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact({"sql": text}), {"sql": text})

    def test_only_the_credential_part_of_a_string_is_replaced(self):
        text = "using ghp_abcdefghijklmnopqrstuvwxyz0123 for maximhq/bifrost"
        self.assertEqual(
            redact({"message": text}), {"message": "using <redacted> for maximhq/bifrost"}
        )


class PipelineTests(unittest.TestCase):
    """The order itself, asserted as a composition (FR-14 / D10 gate)."""

    def test_the_pipeline_is_redact_then_normalize_then_digest(self):
        args = {"sql": "select 1", "token": "  s3cret  "}
        self.assertEqual(identify("sqlite.query", args), digest_of(normalize_args(redact(args))))
        self.assertNotEqual(identify("sqlite.query", args), digest_of(normalize_args(args)))

    def test_normalization_still_applies_after_redaction(self):
        # A credential key that normalization would drop anyway: the identity
        # must equal the call without it, which proves the redactor does not
        # bypass the normalizer.
        self.assertEqual(
            identify("sqlite.query", {"sql": "select 1", "request_id": "abc"}),
            identify("sqlite.query", {"sql": "select 1"}),
        )
        self.assertEqual(
            identify("sqlite.query", {"sql": "select 1", "request_id": "zzz"}),
            identify("sqlite.query", {"sql": "select 1"}),
        )

    def test_redaction_is_idempotent(self):
        once = redact({"token": "x", "message": "Bearer abcdefgh12345678"})
        self.assertEqual(redact(once), once)

    def test_the_input_is_not_mutated(self):
        args = {"token": "abc", "nested": {"password": "p"}}
        redact(args)
        self.assertEqual(args, {"token": "abc", "nested": {"password": "p"}})

    def test_the_result_digest_is_redacted_too(self):
        # Not strictly FR-14 (that says args_hash), but the same argument: two
        # answers that differ only in a returned credential would otherwise
        # never look identical, and R2's plateau would never form.
        self.assertEqual(
            digest_result({"rows": [], "token": "aaa"}),
            digest_result({"rows": [], "token": "bbb"}),
        )
        self.assertNotEqual(
            digest_result({"rows": [], "token": "aaa"}),
            digest_of({"rows": [], "token": "aaa"}),
        )


class NoOpTests(unittest.TestCase):
    """Redaction must be invisible on everything this project already records."""

    def test_a_real_query_is_untouched(self):
        args = {"sql": "select id, repo from incidents where resolved = 0"}
        self.assertEqual(redact(args), args)
        self.assertEqual(identify("sqlite.query", args), digest_of(normalize_args(args)))

    def test_a_real_result_is_untouched(self):
        payload = {
            "rows": [
                {"id": 2, "repo": "maximhq/bifrost", "severity": "sev3"},
                {"id": 4, "repo": "1012hopes/mcp-gateway", "severity": "sev3"},
            ],
            "row_count": 2,
        }
        self.assertEqual(redact(payload), payload)
        self.assertEqual(digest_result(payload), digest_of(payload))

    def test_an_envelope_is_untouched(self):
        payload = {
            "content": [{"type": "text", "text": '{"tables": ["incidents", "repos"]}'}],
            "is_error": False,
        }
        self.assertEqual(redact(payload), payload)


class DeclarationTests(unittest.TestCase):
    def test_a_custom_policy_can_narrow_the_key_set(self):
        narrow = Redactor(key_tokens=frozenset({"password"}))
        self.assertEqual(redact({"token": "x"}, narrow), {"token": "x"})
        self.assertEqual(redact({"password": "x"}, narrow), {"password": REDACTED})

    def test_every_declared_token_is_a_lowercase_token(self):
        # ``key_token`` lowercases and strips separators; a hand-written entry
        # like ``api_key`` would never match and would fail silently.
        for token in CREDENTIAL_TOKENS:
            with self.subTest(token=token):
                self.assertEqual(token, token.lower())
                self.assertNotIn("_", token)
                self.assertNotIn("-", token)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
