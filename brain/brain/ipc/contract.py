"""IPC contract for the Halo WebSocket protocol.

Source of truth: shared/ipc-contract.json (message names + required fields).
Mirrored by hand in ui/src/ipc/contract.ts — keep both in sync; run
`python shared/check_contract_sync.py` after editing either.
Full contract prose: systemdesign/11-ipc-contract.md

Importable by both `brain` and `voice` (voice installs brain in dev via
`pip install -e ../brain`; see DEVELOPMENT.md).
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict, Union


class IpcEnvelope(TypedDict):
    type: str
    id: str
    ts: str


# ---- Inbound to Brain (from UI or Voice) ----


class HelloMsg(IpcEnvelope):
    token: str
    role: NotRequired[Literal["ui", "voice"]]  # absent -> "ui" (full stream); Voice opts into its subset


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
    op: Literal["edit", "delete", "restore"]
    text: NotRequired[str]


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
    pass


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


class DoneMsg(IpcEnvelope):
    conversation_id: str
    task_id: NotRequired[str]


class ErrorMsg(IpcEnvelope):
    code: str
    message: str
    recoverable: bool
    conversation_id: NotRequired[str]


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


class BeliefStateMsg(IpcEnvelope):
    belief_id: str
    text: str
    kind: Literal["preference", "project", "workflow", "decision", "lesson"]
    provenance: Literal["user", "inferred"]
    salience: float
    status: Literal["active", "archived", "superseded"]
    superseded_by: NotRequired[str]
    used_at: NotRequired[str]


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
    BeliefStateMsg,
    SkillStateMsg,
]

# Required payload fields per type (envelope's type/id/ts are checked separately).
# Keep in lockstep with shared/ipc-contract.json "required" lists.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "hello": ("token",),
    "user_msg": ("text", "conversation_id", "source"),
    "interrupt": ("conversation_id",),
    "approval_response": ("reply_to", "decision"),
    "memory_edit": ("belief_id", "op"),
    "skill_op": ("skill_name", "op"),
    "lane_pin": ("task_id", "lane"),
    "task_op": ("op",),
    "mic": ("op",),
    "settings_update": ("key", "value"),
    "undo": ("undo_token",),
    "hello_ack": (),
    "token": ("text", "conversation_id"),
    "activity": ("text", "narrate", "task_id", "undoable"),
    "approval_request": ("approval_id", "tool", "args_redacted", "tier", "task_id"),
    "done": ("conversation_id",),
    "error": ("code", "message", "recoverable"),
    "task_state": ("task_id", "state", "lane"),
    "stream_frame": ("task_id", "jpeg_b64", "seq"),
    "voice_state": ("state",),
    "transcript": ("text", "final", "conversation_id"),
    "spend_update": ("session_usd", "month_usd"),
    "belief_state": ("belief_id", "text", "kind", "provenance", "salience", "status"),
    "skill_state": ("skill_name", "origin", "kind", "uses", "success_rate", "status", "born_at"),
}

_STRING_FIELDS = {
    "hello": ("token",),
    "user_msg": ("text", "conversation_id", "source"),
    "interrupt": ("conversation_id",),
    "approval_response": ("reply_to", "decision"),
    "memory_edit": ("belief_id", "op"),
    "skill_op": ("skill_name", "op"),
    "lane_pin": ("task_id",),
    "task_op": ("op",),
    "mic": ("op",),
    "settings_update": ("key",),
    "undo": ("undo_token",),
    "token": ("text", "conversation_id"),
    "done": ("conversation_id",),
    "error": ("code", "message"),
}

_ENUM_FIELDS = {
    "user_msg": {"source": {"ui", "voice"}},
    "approval_response": {"decision": {"approve", "deny", "edit"}},
    "memory_edit": {"op": {"edit", "delete", "restore"}},
    "skill_op": {"op": {"trial", "disable", "restore", "delete"}},
    "task_op": {"op": {"pause", "resume", "stop"}},
    "mic": {"op": {"mute", "unmute"}},
}


class IpcValidationError(ValueError):
    """Raised when a decoded frame doesn't match the IPC contract."""


def parse_ipc_message(raw: object) -> IpcMessage:
    """Validate an arbitrary decoded-JSON frame against the contract.

    Raises IpcValidationError on an unknown `type` or a missing required
    field — never returns a partial message.
    """
    if not isinstance(raw, dict):
        raise IpcValidationError("ipc: frame is not an object")

    msg_type = raw.get("type")
    if not isinstance(msg_type, str) or msg_type not in REQUIRED_FIELDS:
        raise IpcValidationError(f"ipc: unknown message type {msg_type!r}")

    if not isinstance(raw.get("id"), str) or not isinstance(raw.get("ts"), str):
        raise IpcValidationError("ipc: envelope missing id/ts")

    for field in REQUIRED_FIELDS[msg_type]:
        if field not in raw:
            raise IpcValidationError(
                f'ipc: "{msg_type}" missing required field "{field}"'
            )

    for field in _STRING_FIELDS.get(msg_type, ()):
        if not isinstance(raw[field], str):
            raise IpcValidationError(
                f'ipc: "{msg_type}" field "{field}" must be a string'
            )

    for field, allowed in _ENUM_FIELDS.get(msg_type, {}).items():
        if raw[field] not in allowed:
            choices = ", ".join(sorted(allowed))
            raise IpcValidationError(
                f'ipc: "{msg_type}" field "{field}" must be one of: {choices}'
            )
    if msg_type == "hello" and "role" in raw and (
        not isinstance(raw["role"], str) or raw["role"] not in {"ui", "voice"}
    ):
        raise IpcValidationError('ipc: "hello" field "role" must be one of: ui, voice')
    if msg_type == "memory_edit" and "text" in raw and not isinstance(raw["text"], str):
        raise IpcValidationError('ipc: "memory_edit" field "text" must be a string')
    if msg_type == "lane_pin" and (
        isinstance(raw["lane"], bool) or raw["lane"] not in (1, 2, 3)
    ):
        raise IpcValidationError('ipc: "lane_pin" field "lane" must be 1, 2, or 3')
    if msg_type == "task_op" and "task_id" in raw and not isinstance(raw["task_id"], str):
        raise IpcValidationError('ipc: "task_op" field "task_id" must be a string')
    if msg_type == "error" and not isinstance(raw["recoverable"], bool):
        raise IpcValidationError('ipc: "error" field "recoverable" must be a boolean')
    if msg_type == "error" and "conversation_id" in raw and not isinstance(raw["conversation_id"], str):
        raise IpcValidationError('ipc: "error" field "conversation_id" must be a string')

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

    print("[brain.ipc.contract] self-check OK")


if __name__ == "__main__":
    _self_check()
