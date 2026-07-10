"""IPC contract for the Halo WebSocket protocol.

Source of truth: shared/ipc-contract.json (message names + required fields).
Mirrored by hand in ui/src/ipc/contract.ts — keep both in sync; run
`python shared/check_contract_sync.py` after editing either.
Full contract prose: systemdesign/11-ipc-contract.md

Importable by both `brain` and `voice` (voice installs brain in dev via
`pip install -e ../brain`; see DEVELOPMENT.md).
"""

from __future__ import annotations

from typing import Literal, TypedDict, Union


class IpcEnvelope(TypedDict):
    type: str
    id: str
    ts: str


# ---- Inbound to Brain (from UI or Voice) ----


class HelloMsg(IpcEnvelope):
    token: str


class UserMsg(IpcEnvelope):
    text: str
    conversation_id: str
    source: Literal["ui", "voice"]


class InterruptMsg(IpcEnvelope):
    conversation_id: str


class ApprovalResponseMsg(IpcEnvelope, total=False):
    reply_to: str
    decision: Literal["approve", "deny", "edit"]
    edited_args: object


class MemoryEditMsg(IpcEnvelope, total=False):
    belief_id: str
    op: Literal["edit", "delete", "restore"]
    text: str


class SkillOpMsg(IpcEnvelope):
    skill_name: str
    op: Literal["trial", "disable", "restore", "delete"]


class LanePinMsg(IpcEnvelope):
    task_id: str
    lane: Literal[1, 2, 3]


class TaskOpMsg(IpcEnvelope, total=False):
    task_id: str
    op: Literal["pause", "resume", "stop"]


class MicMsg(IpcEnvelope):
    op: Literal["mute", "unmute"]


class SettingsUpdateMsg(IpcEnvelope):
    key: str
    value: object


# ---- Outbound from Brain (to UI; Voice receives the subset it speaks) ----


class TokenMsg(IpcEnvelope):
    text: str
    conversation_id: str


class ActivityMsg(IpcEnvelope, total=False):
    text: str
    narrate: bool
    task_id: str
    undoable: bool
    undo_token: str


class ApprovalRequestMsg(IpcEnvelope):
    # The approval's own domain id (distinct from the envelope message `id`).
    # approval_response.reply_to references this value.
    approval_id: str
    tool: str
    args_redacted: object
    tier: Literal[1, 2, 3]
    task_id: str


class DoneMsg(IpcEnvelope, total=False):
    conversation_id: str
    task_id: str


class ErrorMsg(IpcEnvelope, total=False):
    code: str
    message: str
    recoverable: bool
    conversation_id: str


class TaskStateMsg(IpcEnvelope):
    task_id: str
    state: Literal["running", "paused", "waiting_approval", "done", "failed"]
    lane: Literal[1, 2, 3]


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

    print("[brain.ipc.contract] self-check OK")


if __name__ == "__main__":
    _self_check()
