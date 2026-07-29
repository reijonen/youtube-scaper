import pytest

from scraper.controller import protocol


def test_decode_valid_hello():
    msg = protocol.decode_extension_message(
        protocol.encode_message({"type": "hello", "protocolVersion": 1, "restart": False})
    )
    assert msg["type"] == "hello"


def test_decode_rejects_unknown_type():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_extension_message(
            protocol.encode_message({"type": "not_a_real_type", "protocolVersion": 1})
        )


def test_decode_rejects_missing_required_field():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_extension_message(
            protocol.encode_message({"type": "hello", "protocolVersion": 1})  # missing restart
        )


def test_decode_rejects_invalid_error_code():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_extension_message(
            protocol.encode_message(
                {
                    "type": "video_failed",
                    "protocolVersion": 1,
                    "runId": "r",
                    "videoId": "v",
                    "navEpoch": 1,
                    "errorCode": "NOT_A_REAL_CODE",
                    "message": "x",
                }
            )
        )


def test_decode_rejects_invalid_completion_reason():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_extension_message(
            protocol.encode_message(
                {
                    "type": "video_done",
                    "protocolVersion": 1,
                    "runId": "r",
                    "videoId": "v",
                    "navEpoch": 1,
                    "reason": "because i said so",
                }
            )
        )


def test_decode_rejects_non_json():
    with pytest.raises(protocol.ProtocolError):
        protocol.decode_extension_message(b"not json at all")


def test_check_protocol_version_mismatch_raises():
    with pytest.raises(protocol.ProtocolVersionMismatch):
        protocol.check_protocol_version({"protocolVersion": 999})


def test_check_protocol_version_match_is_silent():
    protocol.check_protocol_version({"protocolVersion": protocol.PROTOCOL_VERSION})


def test_build_hello_ack_shape():
    msg = protocol.build_hello_ack("run1", "vid1", "https://example.com", {"a": 1})
    assert msg == {
        "type": "hello_ack",
        "protocolVersion": protocol.PROTOCOL_VERSION,
        "runId": "run1",
        "videoId": "vid1",
        "videoUrl": "https://example.com",
        "config": {"a": 1},
    }
