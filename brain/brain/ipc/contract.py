"""IPC contract for the Halo WebSocket protocol.

Source of truth: shared/ipc-contract.json (message names + required fields).
Mirrored by hand in ui/src/ipc/contract.ts — keep both in sync; run
`python shared/check_contract_sync.py` after editing either.
Full contract prose: systemdesign/11-ipc-contract.md

Importable by both `brain` and `voice` (voice installs brain in dev via
`pip install -e ../brain`; see DEVELOPMENT.md).
"""

from __future__ import annotations

import math
from typing import Literal, NotRequired, TypedDict, Union


class IpcEnvelope(TypedDict):
    type: str
    id: str
    ts: str


# ---- Inbound to Brain (from UI or Voice) ----


# The protocol version this build speaks. Bump the major on any breaking
# envelope/field change; a major mismatch across the WS is refused loudly on
# both sides. Hand-mirrored with ui/src/ipc/contract.ts CONTRACT_VERSION and
# shared/ipc-contract.json "version" -- check_contract_sync.py compares them.
CONTRACT_VERSION = "1.1"


def contract_major(version: object) -> int | None:
    if not isinstance(version, str):
        return None
    try:
        return int(version.split(".")[0])
    except ValueError:
        return None


class HelloMsg(IpcEnvelope):
    token: str
    role: NotRequired[Literal["ui", "voice"]]  # absent -> "ui" (full stream); Voice opts into its subset
    contract_version: NotRequired[str]  # major.minor; absent -> pre-versioning client, treated as compatible


class UserMsg(IpcEnvelope):
    text: str
    conversation_id: str
    source: Literal["ui", "voice"]


class InterruptMsg(IpcEnvelope):
    conversation_id: str


class ApprovalResponseMsg(IpcEnvelope):
    reply_to: str
    decision: Literal["approve", "deny", "edit"]
    edited_args: NotRequired[object]


class MemoryEditMsg(IpcEnvelope):
    belief_id: str
    op: Literal["edit", "delete", "restore", "purge"]
    text: NotRequired[str]


class MemoryQueryMsg(IpcEnvelope):
    pass


class SkillOpMsg(IpcEnvelope):
    skill_name: str
    op: Literal["trial", "disable", "restore", "delete"]


class LanePinMsg(IpcEnvelope):
    task_id: str
    lane: Literal[1, 2, 3]


class TaskOpMsg(IpcEnvelope):
    task_id: NotRequired[str]
    op: Literal["pause", "resume", "stop"]


class MicMsg(IpcEnvelope):
    op: Literal["mute", "unmute"]


class SettingsUpdateMsg(IpcEnvelope):
    key: str
    value: object


class UndoMsg(IpcEnvelope):
    undo_token: str


# ---- Outbound from Brain (to UI; Voice receives the subset it speaks) ----


class HelloAckMsg(IpcEnvelope):
    contract_version: NotRequired[str]  # the Brain's protocol version; absent -> old Brain, treated as compatible


class TokenMsg(IpcEnvelope):
    text: str
    conversation_id: str


class ActivityMsg(IpcEnvelope):
    text: str
    narrate: bool
    task_id: str
    undoable: bool
    undo_token: NotRequired[str]
    tier: NotRequired[Literal[1, 2, 3]]
    lane: NotRequired[Literal[1, 2, 3]]


class ApprovalRequestMsg(IpcEnvelope):
    # The approval's own domain id (distinct from the envelope message `id`).
    # approval_response.reply_to references this value.
    approval_id: str
    tool: str
    args_redacted: object
    tier: Literal[1, 2, 3]
    task_id: str
    summary: NotRequired[str]
    destructive: NotRequired[bool]
    conversation_id: NotRequired[str]


class DoneMsg(IpcEnvelope):
    conversation_id: str
    task_id: NotRequired[str]
    interrupted: NotRequired[bool]


class ErrorMsg(IpcEnvelope):
    code: str
    message: str
    recoverable: bool
    conversation_id: NotRequired[str]
    operation_kind: NotRequired[
        Literal["undo", "memory_edit", "approval_response", "interrupt", "task_op", "lane_pin", "mic", "skill_op"]
    ]
    operation_id: NotRequired[str]


class TaskStateMsg(IpcEnvelope):
    task_id: str
    state: Literal["running", "paused", "waiting_approval", "done", "failed"]
    lane: Literal[1, 2, 3]
    title: NotRequired[str]
    step: NotRequired[int]
    steps_total: NotRequired[int]
    step_label: NotRequired[str]
    reason: NotRequired[str]


class StreamFrameMsg(IpcEnvelope):
    task_id: str
    jpeg_b64: str
    seq: int


class VoiceStateMsg(IpcEnvelope):
    state: Literal["idle", "wake", "listening", "thinking", "speaking", "muted"]


class TranscriptMsg(IpcEnvelope):
    text: str
    final: bool
    conversation_id: str


class SpendUpdateMsg(IpcEnvelope):
    session_usd: float
    month_usd: float
    session_tokens: NotRequired[int]
    last_turn_tokens: NotRequired[int]


class SettingsStateMsg(IpcEnvelope):
    key: str
    status: Literal["set", "missing", "invalid", "unverified"]


class CapabilitiesStateMsg(IpcEnvelope):
    voice_input: bool
    task_controls: bool
    skill_controls: bool
    demo_scenarios: bool


class BeliefStateMsg(IpcEnvelope):
    belief_id: str
    text: str
    kind: Literal["preference", "project", "workflow", "decision", "lesson"]
    provenance: Literal["user", "inferred"]
    salience: float
    status: Literal["active", "archived", "superseded"]
    superseded_by: NotRequired[str]
    used_at: NotRequired[str]


class BeliefDeletedMsg(IpcEnvelope):
    belief_id: str


class MemoryHistoryStateMsg(IpcEnvelope):
    complete: bool


class SnapshotCompleteMsg(IpcEnvelope):
    """Last frame of the connect snapshot. Payload-free: a boundary marker
    needs no state. Exists so the UI never has to infer the boundary from
    `spend_update`, which the contract treats as a global that may arrive at
    any time."""


class SkillStateMsg(IpcEnvelope):
    skill_name: str
    origin: Literal["auto", "user"]
    kind: Literal["skill", "playbook"]
    uses: int
    success_rate: float
    status: Literal["active", "paused", "retired"]
    born_at: str
    reason: NotRequired[str]


IpcMessage = Union[
    HelloMsg,
    UserMsg,
    InterruptMsg,
    ApprovalResponseMsg,
    MemoryEditMsg,
    MemoryQueryMsg,
    SkillOpMsg,
    LanePinMsg,
    TaskOpMsg,
    MicMsg,
    SettingsUpdateMsg,
    UndoMsg,
    HelloAckMsg,
    TokenMsg,
    ActivityMsg,
    ApprovalRequestMsg,
    DoneMsg,
    ErrorMsg,
    TaskStateMsg,
    StreamFrameMsg,
    VoiceStateMsg,
    TranscriptMsg,
    SpendUpdateMsg,
    SettingsStateMsg,
    CapabilitiesStateMsg,
    BeliefStateMsg,
    BeliefDeletedMsg,
    MemoryHistoryStateMsg,
    SnapshotCompleteMsg,
    SkillStateMsg,
]

def _field(kind: str, enum: list[object] | None = None) -> dict:
    spec: dict = {"type": kind}
    if enum is not None:
        spec["enum"] = enum
    return spec


def _message(direction: str, required: list[str], fields: dict[str, dict]) -> dict:
    return {"direction": direction, "required": required, "fields": fields}


S, B, I, N, O, J = "string", "boolean", "integer", "number", "object", "json"
IN, OUT = "inbound", "outbound"
LANES = [1, 2, 3]

# Complete hand mirror of shared/ipc-contract.json. Runtime validation and the
# drift checker both consume this structure, so optional fields cannot hide in
# type hints while remaining unchecked at the process boundary.
CONTRACT_SPEC: dict = {
    "envelope": {"required": ["type", "id", "ts"], "fields": {
        "type": _field(S), "id": _field(S), "ts": _field(S),
    }},
    "messages": {
        "hello": _message(IN, ["token"], {
            "token": _field(S), "role": _field(S, ["ui", "voice"]), "contract_version": _field(S),
        }),
        "user_msg": _message(IN, ["text", "conversation_id", "source"], {
            "text": _field(S), "conversation_id": _field(S), "source": _field(S, ["ui", "voice"]),
        }),
        "interrupt": _message(IN, ["conversation_id"], {"conversation_id": _field(S)}),
        "approval_response": _message(IN, ["reply_to", "decision"], {
            "reply_to": _field(S), "decision": _field(S, ["approve", "deny", "edit"]),
            "edited_args": _field(O),
        }),
        "memory_edit": _message(IN, ["belief_id", "op"], {
            "belief_id": _field(S), "op": _field(S, ["edit", "delete", "restore", "purge"]), "text": _field(S),
        }),
        "memory_query": _message(IN, [], {}),
        "skill_op": _message(IN, ["skill_name", "op"], {
            "skill_name": _field(S), "op": _field(S, ["trial", "disable", "restore", "delete"]),
        }),
        "lane_pin": _message(IN, ["task_id", "lane"], {"task_id": _field(S), "lane": _field(I, LANES)}),
        "task_op": _message(IN, ["op"], {
            "task_id": _field(S), "op": _field(S, ["pause", "resume", "stop"]),
        }),
        "mic": _message(IN, ["op"], {"op": _field(S, ["mute", "unmute"])}),
        "settings_update": _message(IN, ["key", "value"], {"key": _field(S), "value": _field(J)}),
        "undo": _message(IN, ["undo_token"], {"undo_token": _field(S)}),
        "hello_ack": _message(OUT, [], {"contract_version": _field(S)}),
        "token": _message(OUT, ["text", "conversation_id"], {
            "text": _field(S), "conversation_id": _field(S),
        }),
        "activity": _message(OUT, ["text", "narrate", "task_id", "undoable"], {
            "text": _field(S), "narrate": _field(B), "task_id": _field(S), "undoable": _field(B),
            "undo_token": _field(S), "tier": _field(I, LANES), "lane": _field(I, LANES),
        }),
        "approval_request": _message(OUT, ["approval_id", "tool", "args_redacted", "tier", "task_id"], {
            "approval_id": _field(S), "tool": _field(S), "args_redacted": _field(O),
            "tier": _field(I, LANES), "task_id": _field(S), "summary": _field(S),
            "destructive": _field(B), "conversation_id": _field(S),
        }),
        "done": _message(OUT, ["conversation_id"], {
            "conversation_id": _field(S), "task_id": _field(S), "interrupted": _field(B),
        }),
        "error": _message(OUT, ["code", "message", "recoverable"], {
            "code": _field(S), "message": _field(S), "recoverable": _field(B), "conversation_id": _field(S),
            "operation_kind": _field(S, ["undo", "memory_edit", "approval_response", "interrupt", "task_op", "lane_pin", "mic", "skill_op"]),
            "operation_id": _field(S),
        }),
        "task_state": _message(OUT, ["task_id", "state", "lane"], {
            "task_id": _field(S),
            "state": _field(S, ["running", "paused", "waiting_approval", "done", "failed"]),
            "lane": _field(I, LANES), "title": _field(S), "step": _field(I), "steps_total": _field(I),
            "step_label": _field(S), "reason": _field(S),
        }),
        "stream_frame": _message(OUT, ["task_id", "jpeg_b64", "seq"], {
            "task_id": _field(S), "jpeg_b64": _field(S), "seq": _field(I),
        }),
        "voice_state": _message(OUT, ["state"], {
            "state": _field(S, ["idle", "wake", "listening", "thinking", "speaking", "muted"]),
        }),
        "transcript": _message(OUT, ["text", "final", "conversation_id"], {
            "text": _field(S), "final": _field(B), "conversation_id": _field(S),
        }),
        "spend_update": _message(OUT, ["session_usd", "month_usd"], {
            "session_usd": _field(N), "month_usd": _field(N),
            "session_tokens": _field(I), "last_turn_tokens": _field(I),
        }),
        "settings_state": _message(OUT, ["key", "status"], {
            "key": _field(S), "status": _field(S, ["set", "missing", "invalid", "unverified"]),
        }),
        "capabilities_state": _message(
            OUT,
            ["voice_input", "task_controls", "skill_controls", "demo_scenarios"],
            {
                "voice_input": _field(B), "task_controls": _field(B),
                "skill_controls": _field(B), "demo_scenarios": _field(B),
            },
        ),
        "belief_state": _message(OUT, ["belief_id", "text", "kind", "provenance", "salience", "status"], {
            "belief_id": _field(S), "text": _field(S),
            "kind": _field(S, ["preference", "project", "workflow", "decision", "lesson"]),
            "provenance": _field(S, ["user", "inferred"]), "salience": _field(N),
            "status": _field(S, ["active", "archived", "superseded"]),
            "superseded_by": _field(S), "used_at": _field(S),
        }),
        "belief_deleted": _message(OUT, ["belief_id"], {"belief_id": _field(S)}),
        "memory_history_state": _message(OUT, ["complete"], {"complete": _field(B)}),
        "snapshot_complete": _message(OUT, [], {}),
        "skill_state": _message(OUT, ["skill_name", "origin", "kind", "uses", "success_rate", "status", "born_at"], {
            "skill_name": _field(S), "origin": _field(S, ["auto", "user"]),
            "kind": _field(S, ["skill", "playbook"]), "uses": _field(I), "success_rate": _field(N),
            "status": _field(S, ["active", "paused", "retired"]), "born_at": _field(S), "reason": _field(S),
        }),
    },
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    name: tuple(spec["required"]) for name, spec in CONTRACT_SPEC["messages"].items()
}
DIRECTIONS: dict[str, str] = {
    name: spec["direction"] for name, spec in CONTRACT_SPEC["messages"].items()
}


class IpcValidationError(ValueError):
    """Raised when a decoded frame doesn't match the IPC contract."""


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _value_matches(value: object, field_spec: dict) -> bool:
    kind = field_spec["type"]
    matches = {
        S: isinstance(value, str),
        B: isinstance(value, bool),
        I: isinstance(value, int) and not isinstance(value, bool),
        N: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        O: isinstance(value, dict) and _is_json_value(value),
        J: _is_json_value(value),
    }.get(kind, False)
    return matches and ("enum" not in field_spec or value in field_spec["enum"])


def _sample_value(field_spec: dict) -> object:
    if "enum" in field_spec:
        return field_spec["enum"][0]
    return {S: "x", B: True, I: 1, N: 1.5, O: {"key": "value"}, J: ["x", 1, True, None]}[field_spec["type"]]


def _invalid_value(field_spec: dict) -> object:
    if "enum" in field_spec:
        return "__invalid_enum__" if field_spec["type"] == S else 999
    return {S: [], B: "false", I: 1.5, N: "1.5", O: [], J: object()}[field_spec["type"]]


def parse_ipc_message(raw: object, expected_direction: Literal["inbound", "outbound"] | None = None) -> IpcMessage:
    """Validate an arbitrary decoded-JSON frame against the contract.

    Raises IpcValidationError on an unknown `type` or a missing required
    field — never returns a partial message.
    """
    if not isinstance(raw, dict):
        raise IpcValidationError("ipc: frame is not an object")

    msg_type = raw.get("type")
    if not isinstance(msg_type, str) or msg_type not in REQUIRED_FIELDS:
        raise IpcValidationError(f"ipc: unknown message type {msg_type!r}")

    message_spec = CONTRACT_SPEC["messages"][msg_type]
    if expected_direction is not None and message_spec["direction"] != expected_direction:
        raise IpcValidationError(
            f'ipc: "{msg_type}" is {message_spec["direction"]}, expected {expected_direction}'
        )

    envelope = CONTRACT_SPEC["envelope"]
    for field in envelope["required"]:
        if field not in raw:
            raise IpcValidationError(f'ipc: envelope missing required field "{field}"')
    for field in message_spec["required"]:
        if field not in raw:
            raise IpcValidationError(f'ipc: "{msg_type}" missing required field "{field}"')

    allowed = set(envelope["fields"]) | set(message_spec["fields"])
    extras = set(raw) - allowed
    if extras:
        raise IpcValidationError(f'ipc: "{msg_type}" has unknown field "{sorted(extras)[0]}"')

    for field, field_spec in envelope["fields"].items():
        if not _value_matches(raw[field], field_spec):
            raise IpcValidationError(f'ipc: envelope field "{field}" has an invalid value')
    for field, field_spec in message_spec["fields"].items():
        if field in raw and not _value_matches(raw[field], field_spec):
            raise IpcValidationError(f'ipc: "{msg_type}" field "{field}" has an invalid value')
    if msg_type == "error" and (("operation_kind" in raw) != ("operation_id" in raw)):
        raise IpcValidationError('ipc: "error" operation_kind and operation_id must appear together')

    return raw  # type: ignore[return-value]


def _self_check() -> None:
    """Round-trip a user_msg and confirm bad frames are rejected. Run via
    `python -m brain.ipc.contract`."""
    sample: UserMsg = {
        "type": "user_msg",
        "id": "11111111-1111-1111-1111-111111111111",
        "ts": "2026-07-10T00:00:00Z",
        "text": "hello",
        "conversation_id": "conv-1",
        "source": "ui",
    }
    parsed = parse_ipc_message(dict(sample))
    assert parsed == sample, "round-trip changed the message"

    # The envelope's `id` is the frame's own message id, and payloads are
    # flattened onto it -- a payload field named `id` would silently overwrite
    # it. This is why approval_request carries `approval_id` (see CLAUDE.md).
    for msg_type, spec in CONTRACT_SPEC["messages"].items():
        assert "id" not in spec["fields"], f'"{msg_type}": payload field "id" collides with the envelope message id'

    try:
        parse_ipc_message({"type": "not_a_real_type", "id": "x", "ts": "x"})
    except IpcValidationError:
        pass
    else:
        raise AssertionError("expected unknown type to be rejected")

    try:
        parse_ipc_message({"type": "user_msg", "id": "x", "ts": "x", "text": "hi"})
    except IpcValidationError:
        pass
    else:
        raise AssertionError("expected malformed frame (missing fields) to be rejected")

    for lane in ([], {}):
        try:
            parse_ipc_message({
                "type": "lane_pin", "id": "x", "ts": "x",
                "task_id": "task", "lane": lane,
            })
        except IpcValidationError:
            pass
        else:
            raise AssertionError("expected non-scalar lane to be rejected")

    # Regression: these malformed outbound values previously passed because
    # runtime validation covered only a small inbound-oriented field subset.
    for malformed in (
        {"type": "activity", "id": "x", "ts": "x", "text": "did it", "narrate": "false",
         "task_id": "task", "undoable": True},
        {"type": "activity", "id": "x", "ts": "x", "text": "did it", "narrate": False,
         "task_id": [], "undoable": True},
        {"type": "activity", "id": "x", "ts": "x", "text": "did it", "narrate": False,
         "task_id": "task", "undoable": "yes"},
    ):
        try:
            parse_ipc_message(malformed)
        except IpcValidationError:
            pass
        else:
            raise AssertionError(f"expected malformed outbound frame to be rejected: {malformed}")

    # Every declared required and optional field gets one valid generated
    # frame and one wrong-type/enum mutation. This prevents future executable
    # fields from silently joining the type hints without runtime validation.
    for msg_type, spec in CONTRACT_SPEC["messages"].items():
        frame = {"type": msg_type, "id": "x", "ts": "x"}
        for field, field_spec in spec["fields"].items():
            frame[field] = _sample_value(field_spec)
        parse_ipc_message(frame, spec["direction"])
        opposite = "outbound" if spec["direction"] == "inbound" else "inbound"
        try:
            parse_ipc_message(frame, opposite)
        except IpcValidationError:
            pass
        else:
            raise AssertionError(f"expected {msg_type} to be rejected as {opposite}")
        for field, field_spec in spec["fields"].items():
            bad = dict(frame)
            bad[field] = _invalid_value(field_spec)
            try:
                parse_ipc_message(bad)
            except IpcValidationError:
                pass
            else:
                raise AssertionError(f'expected {msg_type}.{field} invalid value to be rejected')

    print("[brain.ipc.contract] self-check OK")


if __name__ == "__main__":
    _self_check()
