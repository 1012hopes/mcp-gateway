"""The gateway as an MCP server: the single choke point between agent and tools.

Everything the project claims depends on this file being the *only* way a tool
gets called. Four consequences, in order of how easy they are to get wrong:

1. **Nothing is written to stdout except protocol messages.** A stray ``print``
   corrupts the JSON-RPC stream and the failure looks like a hung client, not
   like a logging bug. All diagnostics go to stderr.

2. **The record is written before the result is returned.** If persisting the
   record fails, the caller gets an error result, the run is marked aborted and
   every later call is refused. Handing back a result whose record never landed
   would create exactly the gap a hash chain cannot detect (DESIGN 1.5).

3. **``offered`` is captured, not inferred.** It is the set of tools the agent
   could have picked from *at that moment*. Without it "was the wrong tool
   chosen" has no denominator -- Accuracy@K is uncomputable (DESIGN 2.4). The
   registry decides membership, so ``offered`` is exactly what ``tools/list``
   returned; D14 will shrink it by gating and the field keeps its meaning.

4. **The seven lifecycle steps are ordered, not merged.** Pre-stage guards (R5
   safety, R1 duplicate, R6 unreachable) run before the upstream is touched;
   post-stage guards (R4 completeness, R2 progress, R3 budget) run after,
   because they need the new evidence. Swapping either pair does not raise -- it
   just makes the numbers quietly wrong, which is why D6 wired the stages before
   any rule existed.

Handlers are plain methods rather than closures so that the fail-closed path can
be driven in-process by tests and by ``scripts/failclosed_demo.py`` without a
subprocess in the way.
"""

from __future__ import annotations

import sys
import time
from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .config import GatewayConfig
from .evidence import EvidenceModel
from .limits import CallSignature
from .registry import Registry, ToolEntry, build_registry
from .session import MAX_RESULT_BYTES, RunSession, digest_of
from .stop import POST, PRE, StopContext, StopController, StopVerdict, merge_checks
from .trace.sink import TraceChainError, TraceWriteError
from .upstream import StdioUpstream, UpstreamFailure, tail_of_exception
from .version import GATEWAY_VERSION
from .wire import KIND_CLOSED, KIND_HALT, format_notice

__all__ = ["Gateway", "result_payload", "text_result", "error_result"]

#: How many delivered results stay replayable. Above any plausible run length
#: (the default call budget is 24) so a miss means a bug, not bookkeeping.
DELIVERED_CACHE_LIMIT = 64


def result_payload(result: types.CallToolResult) -> Dict[str, Any]:
    """What gets digested. Digested, not stored: traces must not become data copies."""
    return result.model_dump(mode="json", exclude_none=True)


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=is_error
    )


def error_result(message: str) -> types.CallToolResult:
    return text_result(message, is_error=True)


class _Blocked(Exception):
    """The gateway refused before forwarding. Carries the decision name."""

    def __init__(self, decision: str, reason: str) -> None:
        super().__init__(reason)
        self.decision = decision
        self.reason = reason


class Gateway:
    """Aggregates upstreams and records every call. One per run."""

    def __init__(
        self,
        config: GatewayConfig,
        upstreams: Sequence[StdioUpstream],
        session: RunSession,
        *,
        controller: Optional[StopController] = None,
        evidence: Optional[EvidenceModel] = None,
        baseline: Optional[Mapping[str, str]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.upstreams = list(upstreams)
        self.session = session
        self.controller = controller or StopController(())
        # No model means no fact can be claimed. That default is honest for a
        # gateway built directly (tests, the fail-closed demo); ``__main__``
        # always loads one, and refuses to start if it cannot.
        self.evidence_model = evidence or EvidenceModel.empty()
        # The stamp in the trace must come from the rules *actually installed*,
        # not from a constant: a trace claiming rules-v0(none) while two rules
        # were running is worse than no stamp at all.
        self.session.rules_version = self.controller.version
        self._clock = clock
        self._baseline = baseline
        self._registry: Optional[Registry] = None
        self._by_upstream: Dict[str, StdioUpstream] = {}
        #: What this run already handed back, for R1's replay. Keyed by call
        #: identity, bounded, and never persisted: the trace records digests,
        #: not payloads, so this is the only place a payload can be replayed
        #: from (DESIGN 2.4 -- the trace cannot be read back as a data store).
        self._delivered: Dict[CallSignature, types.CallToolResult] = {}
        for upstream in self.upstreams:
            if upstream.name in self._by_upstream:
                # Two servers under one prefix would silently shadow each other
                # and make ``chosen`` ambiguous in every trace written afterwards.
                raise ValueError(f"duplicate upstream name: {upstream.name!r}")
            self._by_upstream[upstream.name] = upstream

    # -- tool surface ------------------------------------------------------

    async def registry(self, *, refresh: bool = False) -> Registry:
        """List every upstream once, then normalize/namespace/hash the union."""
        if self._registry is not None and not refresh:
            return self._registry
        listing = [
            (upstream.name, await upstream.list_tools(refresh=refresh))
            for upstream in self.upstreams
        ]
        registry = build_registry(listing, baseline=self._baseline)
        self._registry = registry
        # stderr, never stdout: stdout is the JSON-RPC stream. A warning that
        # lands in the protocol stream looks like a hung client, not a warning.
        for note in registry.warnings():
            print(f"registry: {note}", file=sys.stderr, flush=True)
        return registry

    async def list_all_tools(self, *, refresh: bool = False) -> List[ToolEntry]:
        """The tools the agent may pick from right now (frozen ones excluded)."""
        return list((await self.registry(refresh=refresh)).offered)

    # -- MCP handlers ------------------------------------------------------

    async def handle_list_tools(self, ctx: Any, params: Any) -> types.ListToolsResult:
        registry = await self.registry()
        return types.ListToolsResult(tools=[entry.to_mcp() for entry in registry.offered])

    def stop_context(
        self,
        tool: str,
        args_hash: str,
        *,
        available: Sequence[str],
        state_hash: Optional[str] = None,
        evidence: Optional[Mapping[str, str]] = None,
    ) -> StopContext:
        """Snapshot the run for a rule. Built here so rules never see the session.

        ``available`` is required rather than defaulted: a snapshot without the
        tool surface cannot answer "can this fact ever arrive", and a default of
        "nothing is available" would make every rule believe the run is
        unrecoverable.

        ``evidence`` is *merged* into the session's set, not substituted for it:
        the post-stage rule has to see the fact this call produced, while the
        session must not keep it until the record exists. Merging rather than
        overriding is not a detail -- an override would hide every fact the run
        already had, so a completeness rule would see ``missing`` grow on the very
        call that completed it and never fire.
        """
        session = self.session
        offered = frozenset(available)
        facts: Mapping[str, str] = session.evidence
        if evidence:
            facts = {**facts, **evidence}
        return StopContext(
            run_id=session.run_id,
            turn=session.turn,
            tool=tool,
            args_hash=args_hash,
            evidence=frozenset(facts),
            required=frozenset(session.required),
            history=tuple(session.history),
            budget=session.budget,
            # Counts the call being judged, not just the finished ones: a rule
            # that says "this is call 3 of 3" must agree with the number a
            # reviewer reads off the trace, and that record is seq 3.
            calls_used=session.records_written + 1,
            wall_ms=session.wall_ms(),
            state_trail=tuple(session.state_trail),
            state_hash=state_hash,
            available=offered,
            # Computed here, from one definition, so R4 and R6 cannot disagree
            # about which facts are still obtainable.
            unreachable=frozenset(self.evidence_model.unavailable(session.required, offered)),
        )

    def _refuse(self, session: RunSession) -> types.CallToolResult:
        reason = session.abort_reason if session.aborted else session.halt_reason
        label = "run aborted" if session.aborted else f"run {session.state.lower()}"
        notice = format_notice(
            KIND_CLOSED, state=session.state, rule=session.halt_rule or "-", reason=reason or ""
        )
        return error_result(
            f"{label} ({reason}); no further calls are accepted\n{notice}"
        )

    async def handle_call_tool(
        self, ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        session = self.session
        session.calls_attempted += 1

        if session.closed:
            # The run is already over. Refusing here is what stops an agent from
            # continuing on evidence that was never written down.
            return self._refuse(session)

        registry = await self.registry()
        chosen = params.name
        offered = registry.offered_names()
        entry = registry.get(chosen)
        args = dict(params.arguments or {})
        signature = session.proposed_signature(chosen, args)
        started = self._clock()

        decision = "forward"
        attempted: List[str] = []
        verdict: Optional[StopVerdict] = None
        evidence_added: List[str] = []
        #: Facts this call produced but has not committed yet. Held here, not in
        #: the session, for the same reason as ``committed`` below: if the record
        #: cannot be written, the result is dropped and the run is aborted, and
        #: the session must not be left claiming a fact the trace cannot prove.
        pending_evidence: Dict[str, str] = {}
        keep_result = True
        truncated = False
        #: Whether the halt notice is *already* the response. A blocked call has
        #: nothing else to say, so the notice is its result; a forwarded or
        #: replayed one carries data and gets the notice appended. A boolean
        #: here beats inferring it from the state name: DEGRADED is terminal but
        #: is not a "HALTED_" state, and the replay path keeps its result.
        notice_in_result = False
        #: Fingerprint of what the run knows at this point. Updated to the
        #: provisional *after* state once the result exists, and only absorbed by
        #: the session once the record has landed -- the session must never claim
        #: knowledge the trace cannot prove (see ``commit_result``).
        state_hash = session.state_hash()
        committed: Optional[str] = None

        # -- steps 2-3: pre-stage guards ----------------------------------
        pre = self.controller.evaluate(
            PRE, self.stop_context(chosen, signature.args_hash, available=offered)
        )
        attempted.extend(pre.attempted)

        if pre.verdict is not None:
            # A pre-stage verdict means the call must not reach the upstream at
            # all. That is the whole reason these rules run first.
            verdict = pre.verdict
            decision = verdict.decision
            keep_result = False
            session.halt(verdict.state, rule=verdict.rule, reason=verdict.reason)
            replayed = self._recall(signature) if verdict.replay_last else None
            if replayed is not None:
                # R1's replay: the data is already in the transcript, so handing
                # it back is both cheaper and more useful than an error. It is
                # also auditable -- the record's result_digest equals the earlier
                # step's, which proves the same bytes were returned.
                response = replayed
                payload: Dict[str, Any] = result_payload(replayed)
            else:
                response = error_result(
                    format_notice(
                        KIND_HALT,
                        state=verdict.state,
                        decision=verdict.decision,
                        rule=verdict.rule,
                        reason=verdict.reason,
                    )
                )
                notice_in_result = True
                payload = {"blocked": verdict.reason}
        else:
            response, payload, forwarded_decision = await self._forward(
                entry, chosen, offered, args
            )
            decision = forwarded_decision

            # -- steps 5-6: evidence, then post-stage guards ---------------
            # Truncation is decided *before* extraction, not after. A payload
            # cut in half is not a complete fact, and a half-returned row set
            # that counted as evidence would halt the run on a false completion
            # (DESIGN 2.6 pitfall 2). Computing the flag later would leave the
            # extractor reading a partial body as if it were whole.
            truncated = len(repr(payload)) > MAX_RESULT_BYTES
            pending_evidence = self.evidence_model.extract(
                payload, tool=chosen, args=args, truncated=truncated
            )
            evidence_added = sorted(
                name for name in pending_evidence if name not in session.evidence
            )
            # The digest is computed once, before the write, because R2 has to
            # judge *this* result and the record that will hold it must agree:
            # a provisional hash that the trace then contradicts would make the
            # plateau unreadable exactly where it matters.
            committed = digest_of(payload)
            state_hash = session.state_hash(committed, evidence=pending_evidence)
            post = self.controller.evaluate(
                POST,
                self.stop_context(
                    chosen,
                    signature.args_hash,
                    available=offered,
                    state_hash=state_hash,
                    evidence=pending_evidence,
                ),
            )
            attempted.extend(post.attempted)
            if post.verdict is not None:
                verdict = post.verdict
                decision = verdict.decision
                session.halt(verdict.state, rule=verdict.rule, reason=verdict.reason)

        latency_ms = int((self._clock() - started) * 1000)
        # The record describes the run *including* this call, so it reports the
        # merged view. The session still holds only what earlier records prove.
        check = merge_checks(
            verdict=verdict,
            attempted=attempted,
            evidence={**session.evidence, **pending_evidence},
            missing=session.missing_with(*pending_evidence),
            state_hash=state_hash,
        )

        try:
            session.record_call(
                offered=offered,
                chosen=chosen,
                args=args,
                result=payload,
                latency_ms=latency_ms,
                decision=decision,
                state=session.state,
                evidence_added=evidence_added,
                stop_check=check,
                truncated=truncated,
            )
        except (TraceWriteError, TraceChainError) as exc:
            # The call happened but cannot be proven afterwards. Drop the result
            # on the floor rather than let the agent act on unrecorded evidence.
            session.abort(f"trace write failed at seq {session.records_written + 1}: {exc}")
            return error_result(
                "trace write failed; run aborted and this result is discarded "
                "(fail-closed)"
            )

        # Only now may the session keep what this call taught it: the fact and
        # the digest both become real when -- and only when -- the record that
        # proves them exists.
        session.add_evidence(pending_evidence)
        if committed is not None:
            # A blocked call never reaches here -- it taught the run nothing and
            # R1's replay hands back an earlier result whose digest is already
            # absorbed.
            session.commit_result(committed)

        if keep_result:
            # Remembered only after the record landed: the cache is a cache of
            # *delivered* results, and a result withheld by fail-closed was never
            # delivered. Anything else would let a later replay hand back
            # evidence the trace says never reached the model.
            self._remember(signature, response)

        # -- step 7b: deliver, with the halt notice attached ----------------
        if verdict is not None and not notice_in_result:
            # Appended *after* the digest: the notice is gateway metadata, not
            # upstream data. Digesting it would make the same upstream answer
            # hash differently depending on the gateway's decision.
            response.content.append(
                types.TextContent(
                    type="text",
                    text=format_notice(
                        KIND_HALT,
                        state=session.state,
                        decision=verdict.decision,
                        rule=verdict.rule,
                        reason=verdict.reason,
                    ),
                )
            )

        session.mark_delivered()
        session.turn += 1
        return response

    async def _forward(
        self,
        entry: Optional[ToolEntry],
        chosen: str,
        offered: Sequence[str],
        args: Mapping[str, Any],
    ) -> tuple[types.CallToolResult, Dict[str, Any], str]:
        """Steps 4-5: the only place an upstream is actually called."""
        try:
            if entry is None:
                raise _Blocked(
                    "block_unknown_tool",
                    f"unknown tool {chosen!r}; offered: {', '.join(offered) or '(none)'}",
                )
            if entry.frozen:
                # The upstream changed its contract since the baseline was
                # frozen. Serving it would put incomparable traces in the same
                # file; refusing keeps the comparison honest (DESIGN 2.6 pitfall 3).
                raise _Blocked(
                    "block_schema_drift",
                    f"{chosen} is frozen: {entry.drift}. Re-freeze the baseline "
                    f"only after deciding the old traces stay comparable.",
                )
            result = await self._by_upstream[entry.upstream].call_tool(
                entry.original_name, args
            )
            return result, result_payload(result), "forward"
        except _Blocked as blocked:
            # Not an upstream failure: the gateway refused before forwarding.
            # Recorded like any other call so the refusal is auditable.
            return (
                error_result(f"{chosen} failed: {blocked.reason}"),
                {"error": blocked.reason, "decision": blocked.decision},
                blocked.decision,
            )
        except UpstreamFailure as exc:
            return (
                error_result(f"{chosen} failed: {exc}"),
                {"error": tail_of_exception(exc)},
                "forward",
            )

    # -- cross-turn result memory (R1's replay) ----------------------------

    def _remember(self, signature: CallSignature, result: types.CallToolResult) -> None:
        """Keep a copy of what the model received. Bounded, insertion-ordered."""
        if signature in self._delivered:
            return
        if len(self._delivered) >= DELIVERED_CACHE_LIMIT:
            # Evict the oldest. A miss only downgrades a replay to "blocked,
            # explained", so the bound is a memory limit, not a correctness one.
            self._delivered.pop(next(iter(self._delivered)))
        self._delivered[signature] = result.model_copy(deep=True)

    def _recall(self, signature: CallSignature) -> Optional[types.CallToolResult]:
        """A fresh copy: the caller appends a halt notice to what it gets back."""
        cached = self._delivered.get(signature)
        return cached.model_copy(deep=True) if cached is not None else None

    # -- run loop ----------------------------------------------------------

    def build_server(self) -> Server:
        return Server(
            self.config.name,
            version=GATEWAY_VERSION,
            on_list_tools=self.handle_list_tools,
            on_call_tool=self.handle_call_tool,
        )

    async def run(self) -> None:
        async with AsyncExitStack() as stack:
            for upstream in self.upstreams:
                await stack.enter_async_context(upstream)
            server = self.build_server()
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
