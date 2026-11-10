"""Chain verification with first-error localisation.

Two layers, **in this order** (DESIGN 2.8):

1. **L1 integrity** -- seq continuity, ``prev_hash`` linkage, hash recompute.
   Layer 1 answers "were these bytes modified?"
2. **L2 structure** -- per-``schema_version`` required fields and types.
   Layer 2 answers "does this record make sense to this reader?"

The order is not cosmetic. Structural checks first would report a deleted
field as "schema mismatch", hiding the fact that the bytes changed at all.
Integrity first means every modification surfaces as an integrity break, and
L2 only ever speaks about *unmodified* records.

What this cannot do: detect a truncated tail. Delete the last N lines and the
remaining chain is still perfectly valid -- every hash links to its
predecessor. That is a property of hash chains, not a bug in this code, and
:class:`VerifyResult.notes` says so out loud instead of letting the caller
assume a green result means "nothing is missing".
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .canonical import NonCanonicalValue, canonical_bytes
from .record import CURRENT_SCHEMA_VERSION, PAYLOAD_FIELDS_V1, genesis_hash

__all__ = [
    "VerifyResult",
    "REASON_BLANK_LINE",
    "REASON_JSON_PARSE",
    "REASON_MISSING_SEQ",
    "REASON_SEQ_GAP",
    "REASON_PREV_HASH_MISMATCH",
    "REASON_HASH_MISMATCH",
    "REASON_NON_CANONICAL",
    "REASON_NOT_UTF8",
    "REASON_UNSUPPORTED_SCHEMA",
    "REASON_MISSING_FIELD",
    "REASON_TYPE_ERROR",
    "REASON_STOP_CHECK_INCOMPLETE",
    "REASON_EMPTY",
    "decode_trace",
    "verify_lines",
    "verify_path",
    "record_hash",
]

REASON_EMPTY = "empty"
REASON_BLANK_LINE = "blank_line"
REASON_JSON_PARSE = "json_parse"
REASON_MISSING_SEQ = "missing_seq"
REASON_SEQ_GAP = "seq_gap"
REASON_PREV_HASH_MISMATCH = "prev_hash_mismatch"
REASON_HASH_MISMATCH = "hash_mismatch"
#: The bytes parse as JSON but cannot be re-serialized canonically (``NaN``,
#: ``Infinity``). Distinct from ``hash_mismatch`` on purpose: nothing was proved
#: about the writer, only that this record cannot be checked at all.
REASON_NON_CANONICAL = "non_canonical"
#: The file is not UTF-8 where a record should be. Not a JSON error: the reader
#: must not blame the syntax for bytes the writer never wrote.
REASON_NOT_UTF8 = "not_utf8"
REASON_UNSUPPORTED_SCHEMA = "unsupported_schema_version"
REASON_MISSING_FIELD = "missing_field"
REASON_TYPE_ERROR = "type_error"
REASON_STOP_CHECK_INCOMPLETE = "stop_check_incomplete"

_STR_FIELDS = (
    "run_id",
    "session_id",
    "model_id",
    "gateway_version",
    "rules_version",
    "args_hash",
    "result_digest",
    "decision",
    "state",
    "prev_hash",
    "hash",
)
_INT_FIELDS = ("schema_version", "seq", "turn", "latency_ms")
_LIST_FIELDS = ("offered", "evidence_added")
_NULLABLE_STR_FIELDS = ("parent_run_id", "chosen")
#: v3 optional fields: validated only when present. A reader that required them
#: would reject every v1/v2 line -- the opposite of why versions exist.
_OPTIONAL_STR_FIELDS = ("task_id",)
_BOOL_FIELDS = ("truncated",)

_REQUIRED_BY_VERSION: Dict[int, Tuple[str, ...]] = {
    1: PAYLOAD_FIELDS_V1 + ("hash",),
    # v2 adds no payload fields: ``stop_check.state_hash`` is optional, and a
    # reader that demanded it would reject every v1 line it was written to keep
    # verifying.
    2: PAYLOAD_FIELDS_V1 + ("hash",),
    # v3 likewise adds only an optional field (``task_id``).
    3: PAYLOAD_FIELDS_V1 + ("hash",),
}

_STOP_CHECK_REQUIRED = ("rule", "hit", "evidence", "missing", "attempted")

TAIL_NOTE = (
    "链完整只证明「已记录的部分没被改过」：删掉尾部 N 行，剩余链依然自洽；"
    "有写权限的人也能重写整条尾链。要防尾部被截断与整链重写，需要外部锚定"
    "（链头签名或发布到只追加日志），不在本项目范围内。"
)


@dataclass
class VerifyResult:
    ok: bool
    records: int = 0
    first_bad_seq: Optional[int] = None
    first_bad_line: Optional[int] = None
    reason: Optional[str] = None
    detail: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "records": self.records,
            "first_bad_seq": self.first_bad_seq,
            "first_bad_line": self.first_bad_line,
            "reason": self.reason,
            "detail": self.detail,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        if self.ok:
            return f"OK: {self.records} 条记录，链完整"
        return (
            f"FAIL: seq={self.first_bad_seq} line={self.first_bad_line} "
            f"reason={self.reason} :: {self.detail}"
        )


def record_hash(data: Dict[str, Any]) -> str:
    """Recompute the hash of a record dict, ignoring its ``hash`` field.

    Works straight off the parsed JSON -- no dataclass involved -- so it is
    also the compatibility check for fields this version does not know about.
    """
    payload = {k: v for k, v in data.items() if k != "hash"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _fail(
    *,
    reason: str,
    line_no: int,
    seq: Optional[int],
    detail: str,
    records: int,
) -> VerifyResult:
    return VerifyResult(
        ok=False,
        records=records,
        first_bad_seq=seq,
        first_bad_line=line_no,
        reason=reason,
        detail=detail,
    )


def _check_structure(
    data: Dict[str, Any],
) -> Optional[Tuple[str, str]]:
    """L2. Returns ``(reason, detail)`` on failure, ``None`` when fine."""
    version = data.get("schema_version", CURRENT_SCHEMA_VERSION)
    if not isinstance(version, int):
        return REASON_TYPE_ERROR, f"schema_version 不是整数: {version!r}"
    required = _REQUIRED_BY_VERSION.get(version)
    if required is None:
        known = ", ".join(str(v) for v in sorted(_REQUIRED_BY_VERSION))
        return REASON_UNSUPPORTED_SCHEMA, f"schema_version={version}，已知版本: {known}"

    for name in required:
        if name not in data:
            return REASON_MISSING_FIELD, f"缺少字段 {name!r}（schema v{version}）"

    for name in _STR_FIELDS:
        if not isinstance(data[name], str):
            return REASON_TYPE_ERROR, f"{name} 应为 str，实际 {type(data[name]).__name__}"
    for name in _INT_FIELDS:
        if not isinstance(data[name], int) or isinstance(data[name], bool):
            return REASON_TYPE_ERROR, f"{name} 应为 int，实际 {type(data[name]).__name__}"
    for name in _LIST_FIELDS:
        if not isinstance(data[name], list) or not all(
            isinstance(v, str) for v in data[name]
        ):
            return REASON_TYPE_ERROR, f"{name} 应为 [str]"
    for name in _NULLABLE_STR_FIELDS:
        if data[name] is not None and not isinstance(data[name], str):
            return REASON_TYPE_ERROR, f"{name} 应为 str 或 null"
    for name in _BOOL_FIELDS:
        if not isinstance(data[name], bool):
            return REASON_TYPE_ERROR, f"{name} 应为 bool"
    for name in _OPTIONAL_STR_FIELDS:
        if name in data and data[name] is not None and not isinstance(data[name], str):
            return REASON_TYPE_ERROR, f"{name} 应为 str（缺省表示未声明）"

    stop_check = data["stop_check"]
    if not isinstance(stop_check, dict):
        return REASON_TYPE_ERROR, "stop_check 应为 object"
    missing = [k for k in _STOP_CHECK_REQUIRED if k not in stop_check]
    if missing:
        return REASON_STOP_CHECK_INCOMPLETE, f"stop_check 缺少 {missing}"
    if not isinstance(stop_check["hit"], bool):
        return REASON_TYPE_ERROR, "stop_check.hit 应为 bool"
    if "state_hash" in stop_check and not isinstance(stop_check["state_hash"], str):
        return REASON_TYPE_ERROR, "stop_check.state_hash 应为 str（缺省表示未计算）"

    return None


def verify_lines(lines: Iterable[str]) -> VerifyResult:
    """Verify a JSONL trace. Reports the **first** broken record, not a count."""
    expected_seq = 1
    prev_hash: Optional[str] = None
    records = 0

    for line_no, raw in enumerate(lines, 1):
        if not raw.strip():
            return _fail(
                reason=REASON_BLANK_LINE,
                line_no=line_no,
                seq=expected_seq,
                detail="空行：记录被删除或被拼接进了换行符",
                records=records,
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _fail(
                reason=REASON_JSON_PARSE,
                line_no=line_no,
                seq=expected_seq,
                detail=f"JSON 解析失败: {exc.msg}（col {exc.colno}）",
                records=records,
            )

        if not isinstance(data, dict):
            return _fail(
                reason=REASON_JSON_PARSE,
                line_no=line_no,
                seq=expected_seq,
                detail="该行不是 JSON 对象",
                records=records,
            )

        seq = data.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            return _fail(
                reason=REASON_MISSING_SEQ,
                line_no=line_no,
                seq=None,
                detail=f"seq 缺失或非整数: {seq!r}",
                records=records,
            )

        # -- L1: order, linkage, digest ----------------------------------
        if seq != expected_seq:
            return _fail(
                reason=REASON_SEQ_GAP,
                line_no=line_no,
                seq=expected_seq,
                detail=f"期望 seq={expected_seq}，实际 {seq}：中间有记录被删除或插入",
                records=records,
            )

        if prev_hash is None:
            run_id = data.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                return _fail(
                    reason=REASON_TYPE_ERROR,
                    line_no=line_no,
                    seq=seq,
                    detail="首条记录缺少 run_id，无法验证创世哈希",
                    records=records,
                )
            if data.get("prev_hash") != genesis_hash(run_id):
                return _fail(
                    reason=REASON_PREV_HASH_MISMATCH,
                    line_no=line_no,
                    seq=seq,
                    detail="首条记录的 prev_hash 不等于 H(run_id)",
                    records=records,
                )
        elif data.get("prev_hash") != prev_hash:
            return _fail(
                reason=REASON_PREV_HASH_MISMATCH,
                line_no=line_no,
                seq=seq,
                detail=f"prev_hash 未指向 seq={seq - 1} 的哈希",
                records=records,
            )

        # The line *is* JSON -- Python's parser accepts ``NaN``/``Infinity`` --
        # but it cannot be re-serialized, so there is no digest to compare. This
        # sits where the recompute happens rather than in a separate pass: L1 is
        # "were these bytes modified", and a record that cannot be hashed is a
        # fact about these bytes. Reporting ``hash_mismatch`` would claim
        # evidence of tampering this check does not have; crashing -- which it
        # did before a fuzz run of 4000 mutated records turned up one -- would
        # let ``verify`` be taken down by the very file it exists to judge.
        try:
            recomputed = record_hash(data)
        except NonCanonicalValue as exc:
            return _fail(
                reason=REASON_NON_CANONICAL,
                line_no=line_no,
                seq=seq,
                detail=f"记录含无法规范化的值：{exc}；无法重算哈希，这条不可验证",
                records=records,
            )
        if recomputed != data.get("hash"):
            stored = str(data.get("hash", ""))
            return _fail(
                reason=REASON_HASH_MISMATCH,
                line_no=line_no,
                seq=seq,
                detail=(
                    f"内容哈希不符：记录值 {stored[:12]}…，重算得 {recomputed[:12]}…"
                ),
                records=records,
            )

        # -- L2: structure (only reached when bytes are intact) ----------
        structural = _check_structure(data)
        if structural is not None:
            reason, detail = structural
            return _fail(
                reason=reason,
                line_no=line_no,
                seq=seq,
                detail=detail,
                records=records,
            )

        records += 1
        expected_seq += 1
        prev_hash = data["hash"]

    if records == 0:
        return VerifyResult(
            ok=False, records=0, reason=REASON_EMPTY, detail="文件里没有任何记录"
        )

    return VerifyResult(ok=True, records=records, notes=[TAIL_NOTE])


def decode_trace(data: bytes) -> Tuple[str, Optional[VerifyResult]]:
    """Decode a trace file's bytes, or say exactly which line stopped being UTF-8.

    A trace is written as UTF-8 by a process holding the only writer position, so
    bytes that are not UTF-8 were not written by it: the file is corrupt, was
    truncated mid-character by a killed writer, or is not a trace at all. Two
    wrong answers were rejected:

    * **raise** -- ``verify`` then dies on the file it exists to judge, and the
      caller gets a traceback instead of a line number (this is what it did);
    * **replace and let the JSON parser complain** -- reports ``col 1`` on the
      *replaced* text, blaming the syntax for bytes the writer never wrote.

    Counting newlines up to the exact byte offset puts the failure on the line
    the bad byte is on, and the records before it are verified for real -- a
    reviewer wants to know how much of the file is still trustworthy.

    Returns ``(text, None)`` when the bytes are UTF-8, else the replacement-
    decoded text (for display) plus the failure.
    """
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        line_start = data.rfind(b"\n", 0, exc.start) + 1
        line_no = data.count(b"\n", 0, exc.start) + 1
        column = exc.start - line_start + 1
        bad = data[exc.start : exc.start + 1]

        text = data.decode("utf-8", errors="replace")
        records = 0
        if line_start:
            # Everything before this line is valid UTF-8 by construction. If it
            # already fails, *that* is the first bad record, not this byte.
            prefix = verify_lines(io.StringIO(data[:line_start].decode("utf-8")))
            if not prefix.ok:
                return text, prefix
            records = prefix.records

        return text, _fail(
            reason=REASON_NOT_UTF8,
            line_no=line_no,
            seq=None,
            detail=(
                f"第 {line_no} 行第 {column} 字节不是合法 UTF-8"
                f"（0x{bad.hex() if bad else 'EOF'}）：这串字节不是写入者写下的，"
                "可能是写到一半被杀掉，或文件被别的工具转过码"
            ),
            records=records,
        )


def verify_path(path: Union[str, Path]) -> VerifyResult:
    """Verify a file. Anything a damaged file can do to it is reported, not raised."""
    data = Path(path).read_bytes()
    text, failure = decode_trace(data)
    if failure is not None:
        return failure
    # ``StringIO`` iterates like a file opened in text mode, so CRLF and lone CR
    # keep splitting exactly as they did when this function passed a handle.
    return verify_lines(io.StringIO(text))
