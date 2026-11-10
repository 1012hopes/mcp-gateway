"""D14 unit tests: the tokenizer, the ranker, and the four window rules.

These are the parts of gating that must hold before any number in the demo means
anything -- and the parts that are cheap to get subtly wrong:

* the tokenizer decides whether ``list_tables`` and ``describe_table`` share a
  term at all (they did not, until the plural fold);
* the ranker decides whether the near-duplicate pair can be separated;
* the window decides whether a narrower surface is still a *correct* one.

The demo measures what happens on twenty real runs; this file pins the rules
those runs depend on.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, Dict, List

from gateway.selector import (
    BM25,
    COLD_START,
    DEFAULT_K,
    DEFAULT_M,
    EMPTY_QUERY,
    RANKED,
    Selector,
    sticky_seed,
    tool_document,
    tokenize,
)


def tool(name: str, description: str, **properties: Any) -> SimpleNamespace:
    return SimpleNamespace(
        exposed_name=name,
        description=description,
        input_schema={"type": "object", "properties": properties or {"q": {"type": "string"}}},
        offered=True,
    )


CATALOGUE = [
    tool("sqlite.query", "Run one read-only SELECT statement and return rows as objects."),
    tool("sqlite.list_tables", "List the tables in the database."),
    tool("sqlite.describe_table", "Show column names and types for one table.", table={}),
    tool("wiki.search", "Search wiki pages by words in the title or body."),
    tool("wiki.get_page", "Fetch one wiki page by slug: title, body text, last editor.", slug={}),
    tool("gh.search", "Search code, issues and pull requests across repositories by keyword."),
    tool("gh.get_repo", "Repository metadata: description, stars, language, default branch."),
    tool("dirty.status", "Report the server status."),
]


class TokenizerTests(unittest.TestCase):
    def test_names_split_on_dots_and_underscores(self):
        self.assertEqual(tokenize("sqlite.list_tables"), ["sqlite", "list", "table"])

    def test_camel_case_splits(self):
        self.assertEqual(tokenize("getRepo"), ["get", "repo"])

    def test_plurals_fold_so_structural_tools_share_a_term(self):
        # The measured failure this prevents: after "list tables", the query and
        # ``describe_table``'s document shared nothing, so the tool ranked 11th
        # of 30 and the window hid it.
        self.assertIn("table", tokenize("tables"))
        self.assertIn("table", tokenize("describe_table"))
        self.assertIn("repository", tokenize("repositories"))
        self.assertEqual(tokenize("repositories"), tokenize("repository"))

    def test_folding_does_not_destroy_words_ending_in_ss(self):
        self.assertEqual(tokenize("class"), ["class"])

    def test_function_words_go_and_tool_verbs_stay(self):
        tokens = tokenize("how many issues does the search return")
        self.assertNotIn("how", tokens)
        self.assertNotIn("many", tokens)
        for verb in ("issue", "search", "return"):
            self.assertIn(verb, tokens)

    def test_single_characters_are_dropped(self):
        self.assertEqual(tokenize("a q x"), [])


class DocumentTests(unittest.TestCase):
    def test_variants_are_cumulative(self):
        entry = tool("gh.search", "Search code and issues.", q={})
        name_only = set(tool_document(entry, variant="name"))
        described = set(tool_document(entry, variant="description"))
        enriched = set(tool_document(entry, variant="enriched", subjects=["sql.rows.incidents"]))
        self.assertTrue(name_only <= described <= enriched)
        self.assertIn("issue", described)
        self.assertIn("incident", enriched)

    def test_unknown_variant_is_an_error_not_a_default(self):
        with self.assertRaises(ValueError):
            tool_document(tool("x.y", "z"), variant="semantic")


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = Selector(k=5, m=10)

    def test_the_near_duplicate_pair_is_separated_by_the_description(self):
        self.assertEqual(self.selector.rank("search the wiki page", CATALOGUE)[0], "wiki.search")
        self.assertEqual(
            self.selector.rank("search code and pull requests", CATALOGUE)[0], "gh.search"
        )

    def test_a_sql_question_finds_the_sql_tool(self):
        ranked = self.selector.rank("select rows from incidents", CATALOGUE)
        self.assertIn("sqlite.query", ranked[:3], ranked)

    def test_scores_are_deterministic_and_ties_break_on_the_name(self):
        first = self.selector.rank("status", CATALOGUE)
        second = Selector(k=5, m=10).rank("status", CATALOGUE)
        self.assertEqual(first, second)
        zero = BM25({"b": [], "a": []}).scores(tokenize("nothing"))
        self.assertEqual(zero, {"b": 0.0, "a": 0.0})


class WindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.selector = Selector(k=3, m=6)

    def names(self, **kwargs: Any) -> List[str]:
        window = self.selector.window(
            kwargs.pop("query", "search the wiki"), CATALOGUE, **kwargs
        )
        return list(window.names)

    def test_cold_start_returns_the_whole_catalogue(self):
        window = self.selector.window("", CATALOGUE, cold_start=True)
        self.assertEqual(window.reason, COLD_START)
        self.assertEqual(len(window), len(CATALOGUE))

    def test_an_empty_query_is_a_cold_start_too(self):
        window = self.selector.window("", CATALOGUE)
        self.assertEqual(window.reason, EMPTY_QUERY)
        self.assertEqual(len(window), len(CATALOGUE))

    def test_the_window_is_at_least_k_and_at_most_k_plus_m(self):
        window = self.selector.window("wiki", CATALOGUE)
        self.assertEqual(window.reason, RANKED)
        self.assertGreaterEqual(len(window), 3)
        self.assertLessEqual(len(window), 3 + 6)

    def test_a_pin_is_in_the_window_even_when_it_scores_nothing(self):
        # R6 reads ``offered``; without the pin, a query that never mentions SQL
        # lets the window drop the only producer of a required fact, and a
        # completable run is then filed as DEGRADED.
        window = self.selector.window(
            "search the wiki for a page", CATALOGUE, pins=["sqlite.query"]
        )
        self.assertIn("sqlite.query", window.names)
        self.assertIn("sqlite.query", window.pinned)
        self.assertEqual(len(window), 3, "a pin takes a slot, it does not add one")

    def test_a_pin_that_is_not_servable_is_ignored(self):
        window = self.selector.window("wiki", CATALOGUE, pins=["ghost.tool"])
        self.assertNotIn("ghost.tool", window.names)

    def test_stability_readmits_a_previous_tool_still_in_the_top_m(self):
        query = "search the wiki for a page about issues"
        first = self.selector.window(query, CATALOGUE)
        second = self.selector.window(query, CATALOGUE, previous=first.names)
        self.assertEqual(set(first.names), set(second.names))

    def test_a_previous_tool_outside_the_top_m_is_dropped(self):
        narrow = Selector(k=2, m=2)
        window = narrow.window("wiki page", CATALOGUE, previous=["dirty.status"])
        self.assertNotIn("dirty.status", window.names)

    def test_a_cold_start_does_not_seed_the_stability_merge(self):
        # Measured: seeding it makes the second window the top M of the new
        # query -- 2K wide, on every turn -- and the K in "Accuracy@K" stops
        # meaning anything.
        cold = self.selector.window("", CATALOGUE, cold_start=True)
        self.assertEqual(sticky_seed(cold), ())
        ranked = self.selector.window("wiki", CATALOGUE)
        self.assertEqual(sticky_seed(ranked), ranked.names)

    def test_frozen_tools_never_appear(self):
        frozen = tool("sqlite.query", "frozen", q={})
        frozen.offered = False
        others = [entry for entry in CATALOGUE if entry.exposed_name != "sqlite.query"]
        window = self.selector.window("select rows", [frozen, *others])
        self.assertNotIn("sqlite.query", window.names)


class IndexFreshnessTests(unittest.TestCase):
    """A cached index must never answer for text that no longer exists.

    The cache was keyed on the tool *names*, so an upstream that re-declared a
    tool -- same name, different description, which is what ``tools/list`` is
    asked again for and what a schema-drift server does -- kept the old rank.
    Measured before the fix: two tools swapped their descriptions and the
    ranking did not move.
    """

    def swapped_entries(self):
        return [
            tool("wiki.search", "Search wiki pages by words in the title or body."),
            tool("gh.search", "Search repositories for code and commits."),
        ]

    def test_a_redeclared_tool_is_ranked_by_its_new_text(self):
        selector = Selector(k=1, m=1)
        entries = self.swapped_entries()
        self.assertEqual(selector.window("find repositories with commits", entries).names[0], "gh.search")

        entries[0] = tool("wiki.search", "Search repositories and commits by keyword.")
        entries[1] = tool("gh.search", "Search wiki pages by title or body.")
        self.assertEqual(selector.window("find repositories with commits", entries).names[0], "wiki.search")

    def test_eviction_does_not_change_an_answer(self):
        # The cache is bounded (an unbounded one is a leak in a long-lived
        # process), so the same corpus must rank the same after the eviction
        # that a few other corpora caused in between.
        selector = Selector(k=1, m=1)
        entries = self.swapped_entries()
        first = selector.window("find repositories with commits", entries).names
        for i in range(12):
            noise = [tool(f"noise{i}.one", f"unrelated corpus number {i}")]
            selector.window("unrelated", noise)
        again = selector.window("find repositories with commits", entries).names
        self.assertEqual(first, again)


class ConfigurationTests(unittest.TestCase):
    def test_impossible_configurations_are_rejected(self):
        with self.assertRaises(ValueError):
            Selector(k=0)
        with self.assertRaises(ValueError):
            Selector(k=5, m=4)

    def test_defaults_are_the_documented_ones(self):
        self.assertEqual((DEFAULT_K, DEFAULT_M), (5, 10))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
