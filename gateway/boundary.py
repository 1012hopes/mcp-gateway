"""Where one run ends and the next begins -- declared, never inferred (FR-12).

The problem is a false halt, and it is specific. A conversation asks about
unresolved incidents, then about something else. If the second question runs in
the same session, the evidence set still holds the first answer's facts and the
call history still holds its calls, so:

* completeness can fire on facts from the previous topic (a **false completion**,
  the expensive one), or
* the first answer of the new topic repeats the last answer of the old one, the
  state hash does not move, and R2 halts a healthy run on step one (a **false
  no-progress**, and it looks exactly like a real plateau in the trace).

Neither can be fixed by a better heuristic. "Did the topic change" is a fact
about the *caller's* intent, and the gateway sees tool calls. So it is declared:
the caller attaches ``_meta.gateway.task`` to its requests, and a change of that
value is the boundary. Inference is what the rest of this project exists to
avoid -- a gateway that guessed would be making a decision it cannot prove, and
the trace would record the guess as if it were input.

Why ``_meta`` and not a flag or a control tool
----------------------------------------------
* A CLI flag cannot change mid-process, and the whole point is that one process
  serves several runs.
* A control tool (``gateway.new_task``) would appear in ``offered`` and become
  part of the accuracy denominator -- the agent would be choosing between real
  tools and a housekeeping call.
* ``_meta`` is already in the protocol, is stripped before the tool sees the
  arguments, and is per-request, so a stateless client can attach the same
  declaration to every call. That last property is load-bearing: **repeating a
  declaration must be a no-op**, or every call would start a new run.

The declaration is validated strictly. An unknown key is an error rather than a
shrug, because a typo (``inhert``) that is silently ignored would leave the
caller believing it had declared an inheritance it never got -- and the run would
then look like a false halt with no cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

__all__ = [
    "DECLARATION_KEY",
    "Declaration",
    "DeclarationError",
    "parse_declaration",
]

#: Where the declaration lives inside ``_meta``. Namespaced so a gateway
#: parameter can never be confused with one meant for the server.
DECLARATION_KEY = "gateway"

_KNOWN_KEYS = ("task", "inherit", "require")


class DeclarationError(ValueError):
    """The caller declared something the gateway cannot honour."""


@dataclass(frozen=True)
class Declaration:
    """One request's statement about which run it belongs to."""

    task: Optional[str] = None
    inherit: bool = False
    #: ``None`` means "whatever this run already requires": a follow-up question
    #: usually adds no requirement, and re-sending the set would make every call
    #: carry a declaration it does not need.
    require: Optional[Tuple[str, ...]] = None

    @property
    def declared(self) -> bool:
        return self.task is not None

    def is_boundary(self, current_task: Optional[str]) -> bool:
        """Does this request start a new run?

        Only a *change* of task is a boundary. Same task -- including ``None`` to
        ``None`` -- is a continuation, which is what makes a client free to
        attach the declaration to every request.
        """
        if not self.declared:
            return False
        return self.task != current_task

    def describe(self) -> str:
        if not self.declared:
            return "no declaration"
        carried = "inherits evidence" if self.inherit else "starts empty"
        if self.require is not None:
            carried += f", requires {', '.join(self.require) or '(nothing)'}"
        return f"task={self.task} ({carried})"


NO_DECLARATION = Declaration()


def parse_declaration(params: Any) -> Declaration:
    """Read ``_meta.gateway`` off a ``tools/call`` request.

    Accepts the pydantic params object or a plain mapping, because the transport
    is driven both ways: over the wire by the SDK, and in-process by tests and
    the fail-closed demo.
    """
    meta = _meta_of(params)
    if meta is None:
        return NO_DECLARATION
    if not isinstance(meta, Mapping):
        # A ``_meta`` that is not an object cannot be searched for a declaration,
        # so "it declared nothing" and "it declared something unreadable" look
        # identical from here -- and the second one must not pass as the first.
        # Same rule as the unknown-key check below: a caller who attached a
        # declaration to this call gets an error, not a shrug and a run that
        # never inherited what it asked for.
        raise DeclarationError(
            f"_meta must be an object, got {type(meta).__name__}"
        )
    raw = meta.get(DECLARATION_KEY)
    if raw is None:
        return NO_DECLARATION
    if not isinstance(raw, Mapping):
        raise DeclarationError(
            f"_meta.{DECLARATION_KEY} must be an object, got {type(raw).__name__}"
        )

    unknown = sorted(set(raw) - set(_KNOWN_KEYS))
    if unknown:
        raise DeclarationError(
            f"_meta.{DECLARATION_KEY}: unknown key(s) {', '.join(unknown)}; "
            f"known: {', '.join(_KNOWN_KEYS)}"
        )

    task = raw.get("task")
    if task is not None:
        if not isinstance(task, str) or not task.strip():
            raise DeclarationError(
                f"_meta.{DECLARATION_KEY}.task must be a non-empty string"
            )
        task = task.strip()

    inherit = raw.get("inherit", False)
    if not isinstance(inherit, bool):
        raise DeclarationError(f"_meta.{DECLARATION_KEY}.inherit must be a boolean")
    if inherit and task is None:
        # Nothing to inherit *to*: an inheritance without a task would apply to
        # whatever run happens to be open, which is the inference this module
        # refuses to make.
        raise DeclarationError(
            f"_meta.{DECLARATION_KEY}.inherit needs a task to inherit into"
        )

    require = raw.get("require")
    if require is not None:
        if isinstance(require, str) or not isinstance(require, (list, tuple)):
            raise DeclarationError(
                f"_meta.{DECLARATION_KEY}.require must be a list of evidence types"
            )
        checked = []
        for item in require:
            if not isinstance(item, str) or not item.strip():
                raise DeclarationError(
                    f"_meta.{DECLARATION_KEY}.require entries must be non-empty strings"
                )
            checked.append(item.strip())
        require = tuple(checked)

    return Declaration(task=task, inherit=inherit, require=require)


def _meta_of(params: Any) -> Any:
    """The raw ``_meta``, whatever its shape -- validation happens in the caller.

    Returning ``None`` for a malformed ``_meta`` was the bug: it turned "this
    caller's ``_meta`` is not an object" into "this caller declared nothing".
    """
    if params is None:
        return None
    if isinstance(params, Mapping):
        return params.get("_meta")
    meta = getattr(params, "meta", None)
    if isinstance(meta, Mapping):
        return meta
    # A future SDK may hand back a model instead of a dict. Converting is not
    # generosity -- it is the same bytes under a different wrapper, and refusing
    # it would break valid declarations over a packaging choice.
    dump = getattr(meta, "model_dump", None)
    if callable(dump):
        return dump()
    return meta
