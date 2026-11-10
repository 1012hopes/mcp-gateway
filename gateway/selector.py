"""A1: which tools the agent is shown this turn.

Why a ranker at all
-------------------
A catalogue of one server and four tools needs no gate: the model can read all
of them. The gate exists for the case the project is actually about -- thirty
tools across heterogeneous servers, where showing everything dilutes attention
and spends context on tools that cannot answer the question. The claim under
test is therefore narrow and measurable: **narrowing the surface to K must not
hide the tool the run needs.**

What this module does *not* do
------------------------------
It does not decide what may be *called*. The window is what the agent is shown;
a call to a catalogue tool outside the window is still forwarded (and recorded
as ``forward_outside``). The reason is in DESIGN A1: the gateway sees calls, not
intentions, so a hard window turns one ranking mistake into one failed task and
leaves the trace unable to distinguish "the plan was wrong" from "the gate hid
the tool". Ranking is revisable mid-run; a blocked call is not.

Three rules, in the order A1 gives them
---------------------------------------
1. **Score** every servable tool with BM25 over a document built per tool.
2. **Reserve** the producers of any required-but-missing fact. This is the one
   rule that is not A1's: without it the gate can hand R6 an ``offered`` set
   that lacks the only tool able to produce a required fact, and R6 will call a
   completable run ``DEGRADED``. A window that can make a task uncompletable is
   not a narrower window, it is a wrong one.
3. **Stabilise**: ``S_t = topK ∪ (S_{t-1} ∩ topM)``. A tool that vanishes
   between turns makes the model re-plan, which costs more than the context it
   saved -- A1's argument, kept because the demo measures it.

Cold start shows everything
---------------------------
No calls yet means no context to rank by, and a first window guessed from the
task sentence alone would hide exactly the structural tools (``list_tables``,
``describe_table``) that such a sentence never mentions. A1 says the same thing;
this module just refuses to treat "the goal is a sentence" as if it were
context.

Determinism
-----------
Ties break on the exposed name, and the document order comes from the registry,
so two runs of the same call stream produce byte-identical windows. The demo
relies on it: it recomputes each turn's window offline and requires the result
to equal the ``offered`` list recorded at the time.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "BM25",
    "COLD_START",
    "DEFAULT_K",
    "DEFAULT_M",
    "EMPTY_QUERY",
    "INDEX_VARIANTS",
    "RANKED",
    "Selector",
    "Window",
    "sticky_seed",
    "tool_document",
    "tokenize",
]

DEFAULT_K = 5
#: A1: M = 2K. The stability rule can only re-admit tools from here, so this is
#: also the ceiling on how far a window can grow past K.
DEFAULT_M = 10

COLD_START = "cold_start"
EMPTY_QUERY = "empty_query"
RANKED = "bm25"

#: Which text a tool is indexed by. ``name`` is the floor (it cannot encode
#: anything the caller did not write), ``description`` is what a server author
#: says, ``enriched`` adds the fact types the deployment declares the tool can
#: produce. The demo measures all three: the difference between them is the
#: honest answer to "why does your gate work", and it is not a paragraph of
#: prose.
INDEX_VARIANTS = ("name", "description", "enriched")

_SPLIT_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")

#: Function words only. A real stopword list would also drop "get" and "list",
#: which are the verbs these tools are named after -- dropping them would make
#: every tool look equally (un)related to every query.
#:
#: The interrogatives are here for a measured reason: the goal "which
#: repositories have unresolved incidents and how many stars do they have"
#: otherwise ranks a wiki page about tag counts first, because that page says
#: "how many pages carry it". No correct tool contains "how many"; a question
#: does. That is the one failure mode of a lexical ranker that is cheap to see
#: and cheap to remove.
_STOP = frozenset(
    "a an the of in on to for and or is are was be been with by from that this "
    "it its as at all any one into than then when where which who how what "
    "many they them their there here does do did my our your we you i us "
    "please can could should would will may might not no if so such each more "
    "most other some about over up out".split()
)


def tokenize(text: object) -> List[str]:
    """Lowercase words, split on punctuation, snake_case and camelCase, fold plurals.

    The plural fold is five lines and it is not cosmetic. Measured without it:
    after a run lists its tables, the next call ranks ``describe_table`` 11th of
    30, because the query says "list tables" and the document says "column names
    and types for one table" -- the two share no token even though they are
    obviously about the same thing. That is the structural-tool failure mode of
    a lexical ranker, and one trailing ``s`` is most of the fix. Deliberately
    not a Porter stemmer: the extra recall it buys on "describing"/"described"
    is not worth an algorithm nobody can re-derive by reading five lines.
    """
    spaced = _SPLIT_CAMEL.sub(" ", str(text or ""))
    out: List[str] = []
    for word in _WORD.findall(spaced.lower()):
        if len(word) > 1 and word not in _STOP:
            out.append(_fold(word))
    return out


def _fold(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        # "repositories" -> "repository": stripping the s alone leaves
        # "repositorie", which matches nothing.
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _arg_names(entry: object) -> List[str]:
    schema = getattr(entry, "input_schema", None) or {}
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    return list(properties) if isinstance(properties, Mapping) else []


def tool_document(
    entry: object,
    *,
    variant: str = "enriched",
    subjects: Sequence[str] = (),
) -> List[str]:
    """The index text for one tool, as tokens.

    ``subjects`` are the fact types the deployment declares this tool can
    produce (``sql.rows.incidents`` and friends). They are part of the index
    because a description of *how* a tool works ("run a read-only SELECT")
    cannot say *what it can answer* ("incidents, repos, commits"), and the
    gateway already knows the latter from the evidence model. This is index
    construction, not a per-turn hint: the same tokens are attached on every
    turn of every run, so the gate still has to rank live.
    """
    if variant not in INDEX_VARIANTS:
        raise ValueError(f"unknown index variant {variant!r}; known: {INDEX_VARIANTS}")
    parts: List[str] = [str(getattr(entry, "exposed_name", ""))]
    if variant != "name":
        parts.append(str(getattr(entry, "description", "") or ""))
        parts.extend(_arg_names(entry))
    if variant == "enriched":
        parts.extend(subjects)
    return tokenize(" ".join(parts))


class BM25:
    """Okapi BM25 over a fixed document set. Small, pure, and deterministic.

    ``k1``/``b`` are the textbook values and are not tuned here: tuning them
    against the same twenty tasks the demo reports on would make the number a
    property of the tuning, not of the design.
    """

    def __init__(
        self,
        docs: Mapping[str, Sequence[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._docs: Dict[str, Counter] = {
            name: Counter(tokens) for name, tokens in docs.items()
        }
        self._lengths = {name: sum(counts.values()) for name, counts in self._docs.items()}
        total = sum(self._lengths.values())
        self._avgdl = (total / len(self._docs)) if self._docs else 0.0
        df: Counter = Counter()
        for counts in self._docs.values():
            df.update(counts.keys())
        n = len(self._docs)
        self._idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def scores(self, query: Sequence[str]) -> Dict[str, float]:
        """Sum of IDF-weighted term frequencies, saturating as in BM25."""
        terms = [t for t in query if t in self._idf]
        out: Dict[str, float] = {}
        for name, counts in self._docs.items():
            length = self._lengths[name]
            norm = 1 - self._b + self._b * (length / self._avgdl if self._avgdl else 0)
            score = 0.0
            for term in terms:
                freq = counts.get(term, 0)
                if not freq:
                    continue
                score += self._idf[term] * (freq * (self._k1 + 1)) / (
                    freq + self._k1 * norm
                )
            out[name] = score
        return out


@dataclass(frozen=True)
class Window:
    """The tool surface for one turn, plus why it looks like that."""

    names: Tuple[str, ...]
    reason: str
    pinned: Tuple[str, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        return name in self.names

    def __len__(self) -> int:
        return len(self.names)


def sticky_seed(window: Optional[Window]) -> Tuple[str, ...]:
    """What the stability merge may carry over from the previous turn.

    A cold-start window is the whole catalogue, and it is not a *ranking* -- so
    it must not seed the merge. Measured the other way round (the first
    implementation did seed it): the second turn's window became the top M of
    the new query, i.e. 2K wide, and stayed there; a "K=5" gate that shows ten
    tools on every turn is not the thing under test, and the Accuracy@K curve
    came out flat because every window was wide enough to contain everything
    plausible. The premise of the merge is a ranked previous window.
    """
    if window is None or window.reason == COLD_START:
        return ()
    return window.names


class Selector:
    """Ranks the servable catalogue for one query. Stateless by design.

    The previous window is passed in rather than remembered: a selector that
    kept it would make the window depend on how many times ``window`` was
    called, and the demo's offline replay (one call per turn) would then
    disagree with the live run for a reason that has nothing to do with the
    ranking.
    """

    def __init__(
        self,
        *,
        k: int = DEFAULT_K,
        m: int = DEFAULT_M,
        variant: str = "enriched",
        selector_subjects: Optional[Mapping[str, Sequence[str]]] = None,
    ) -> None:
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        if m < k:
            raise ValueError(f"m must be >= k ({m} < {k})")
        if variant not in INDEX_VARIANTS:
            raise ValueError(f"unknown index variant {variant!r}; known: {INDEX_VARIANTS}")
        self.k = k
        self.m = m
        self.variant = variant
        self._subjects = {
            name: tuple(values) for name, values in (selector_subjects or {}).items()
        }
        self._cache: Dict[Tuple[str, ...], BM25] = {}

    # -- index -------------------------------------------------------------

    def _index(self, entries: Sequence[object]) -> BM25:
        key = tuple(getattr(entry, "exposed_name", "") for entry in entries)
        index = self._cache.get(key)
        if index is None:
            docs = {
                str(getattr(entry, "exposed_name", "")): tool_document(
                    entry,
                    variant=self.variant,
                    subjects=self._subjects.get(
                        str(getattr(entry, "exposed_name", "")), ()
                    ),
                )
                for entry in entries
            }
            index = BM25(docs)
            self._cache[key] = index
        return index

    def rank(self, query: str, entries: Sequence[object]) -> List[str]:
        """Every servable tool, best first. Ties break on the name."""
        index = self._index(entries)
        scores = index.scores(tokenize(query))
        return sorted(
            (str(getattr(entry, "exposed_name", "")) for entry in entries),
            key=lambda name: (-scores.get(name, 0.0), name),
        )

    # -- the window --------------------------------------------------------

    def window(
        self,
        query: str,
        entries: Sequence[object],
        *,
        previous: Sequence[str] = (),
        pins: Iterable[str] = (),
        cold_start: bool = False,
    ) -> Window:
        """A1's four steps, plus the pin rule from this module's docstring."""
        servable = [
            str(getattr(entry, "exposed_name", ""))
            for entry in entries
            if getattr(entry, "offered", True)
        ]
        terms = tokenize(query)
        if cold_start:
            return Window(tuple(servable), COLD_START)
        if not terms:
            # Nothing to rank by. Guessing here would hide the structural tools
            # a bare goal sentence never names; the honest fallback is A1's.
            return Window(tuple(servable), EMPTY_QUERY)

        index = self._index(entries)
        scores = index.scores(terms)
        ranked = sorted(servable, key=lambda name: (-scores.get(name, 0.0), name))
        top_k = ranked[: self.k]
        top_m = set(ranked[: self.m])

        reserved = [name for name in dict.fromkeys(pins) if name in set(servable)]
        # Pins take slots rather than adding them: |S_t| stays K, and the cost of
        # reserving one is visible -- it displaces the lowest-ranked match. A pin
        # that would have ranked anyway costs nothing, because of the dedupe.
        chosen: List[str] = list(reserved)
        for name in top_k:
            if len(chosen) >= max(self.k, len(reserved)):
                break
            if name not in chosen:
                chosen.append(name)
        # Stability: anything the last window showed and the ranker still likes
        # enough to be in the top M stays. Bounded by construction at K + M.
        for name in previous:
            if name in top_m and name not in chosen:
                chosen.append(name)

        chosen.sort(key=lambda name: (-scores.get(name, 0.0), name))
        return Window(
            tuple(chosen),
            RANKED,
            pinned=tuple(reserved),
            scores={name: scores.get(name, 0.0) for name in chosen},
        )
