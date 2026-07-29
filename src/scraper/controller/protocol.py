"""Message shapes, per SPEC-V3's "Messages" and "Protocol invariants"
sections. Every message carries `type` and `protocolVersion`; nothing here
adds a message type that isn't in SPEC-V3's table.

Extension-supplied messages are untrusted input (SPEC-V3, Input hardening)
and are validated field-by-field on decode. Controller-supplied messages are
built by typed constructors here so a caller can't accidentally omit a
required field or add one that isn't in the spec.
"""

import json
from typing import Any

PROTOCOL_VERSION = 1

ERROR_CODES = frozenset(
    {
        "PAGE_READY_TIMEOUT",
        "CONSENT_WALL",
        "VIDEO_UNAVAILABLE",
        "AGE_RESTRICTED",
        "LOGIN_REQUIRED",
        "SHORTS_EXCLUDED",
        "UNEXPECTED_NAVIGATION",
        "SCHEMA_UNRECOGNISED",
        "CHAIN_STALLED",
        "CIRCUIT_BREAKER",
        "PAYLOAD_TOO_LARGE",
        "CONTROLLER_UNAVAILABLE",
    }
)

COMPLETION_REASONS = frozenset({"max_recommendations_reached", "chain_exhausted"})
PAYLOAD_SOURCES = frozenset({"initial", "network"})

_EXTENSION_MESSAGE_FIELDS = {
    "hello": frozenset({"restart"}),
    "payload": frozenset(
        {"runId", "videoId", "navEpoch", "batchId", "source", "endpoint", "capturedAt", "body"}
    ),
    "video_done": frozenset({"runId", "videoId", "navEpoch", "reason"}),
    "video_failed": frozenset({"runId", "videoId", "navEpoch", "errorCode", "message"}),
    "ping": frozenset({"runId"}),
}


class ProtocolError(Exception):
    """Raised for any malformed or invalid message from the extension. The
    caller should treat the connection as unrecoverable — untrusted input
    that fails validation is not something to guess a recovery for."""


class ProtocolVersionMismatch(ProtocolError):
    pass


def decode_extension_message(raw: bytes) -> dict[str, Any]:
    """Parse and validate one frame's bytes as an extension-to-controller
    message. Raises ProtocolError on anything that doesn't match SPEC-V3's
    table exactly."""
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc

    if not isinstance(msg, dict):
        raise ProtocolError(f"message must be a JSON object, got {type(msg).__name__}")

    msg_type = msg.get("type")
    if msg_type not in _EXTENSION_MESSAGE_FIELDS:
        raise ProtocolError(f"unknown or missing message type: {msg_type!r}")

    if "protocolVersion" not in msg:
        raise ProtocolError("message is missing protocolVersion")

    required = _EXTENSION_MESSAGE_FIELDS[msg_type]
    missing = required - msg.keys()
    if missing:
        raise ProtocolError(f"{msg_type} message missing fields: {sorted(missing)}")

    if msg_type == "payload":
        if msg["source"] not in PAYLOAD_SOURCES:
            raise ProtocolError(f"payload has unknown source: {msg['source']!r}")
    elif msg_type == "video_done":
        if msg["reason"] not in COMPLETION_REASONS:
            raise ProtocolError(f"video_done has unknown reason: {msg['reason']!r}")
    elif msg_type == "video_failed":
        if msg["errorCode"] not in ERROR_CODES:
            raise ProtocolError(f"video_failed has unknown errorCode: {msg['errorCode']!r}")

    return msg


def encode_message(msg: dict[str, Any]) -> bytes:
    return json.dumps(msg).encode("utf-8")


def check_protocol_version(msg: dict[str, Any]) -> None:
    if msg["protocolVersion"] != PROTOCOL_VERSION:
        raise ProtocolVersionMismatch(
            f"protocol version mismatch: extension sent {msg['protocolVersion']!r}, "
            f"controller expects {PROTOCOL_VERSION!r}"
        )


# -- Controller -> extension message constructors ---------------------------


def build_hello_ack(run_id: str, video_id: str, video_url: str, config: dict) -> dict:
    return {
        "type": "hello_ack",
        "protocolVersion": PROTOCOL_VERSION,
        "runId": run_id,
        "videoId": video_id,
        "videoUrl": video_url,
        "config": config,
    }


def build_payload_ack(batch_id: str) -> dict:
    return {"type": "payload_ack", "protocolVersion": PROTOCOL_VERSION, "batchId": batch_id}


def build_payload_nack(batch_id: str, retryable: bool) -> dict:
    return {
        "type": "payload_nack",
        "protocolVersion": PROTOCOL_VERSION,
        "batchId": batch_id,
        "retryable": retryable,
    }


def build_video_done_ack(video_id: str) -> dict:
    return {"type": "video_done_ack", "protocolVersion": PROTOCOL_VERSION, "videoId": video_id}


def build_video_failed_ack(video_id: str) -> dict:
    return {"type": "video_failed_ack", "protocolVersion": PROTOCOL_VERSION, "videoId": video_id}


def build_pong() -> dict:
    return {"type": "pong", "protocolVersion": PROTOCOL_VERSION}


def build_stop() -> dict:
    return {"type": "stop", "protocolVersion": PROTOCOL_VERSION}
