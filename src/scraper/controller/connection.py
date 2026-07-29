"""Per-connection read/dispatch/write loop. One call to `handle_connection`
runs for the lifetime of one bridge connection."""

import logging
import socket

from . import framing, protocol
from .video_session import VideoSession, VideoSessionError

logger = logging.getLogger(__name__)


def handle_connection(
    sock: socket.socket, session: VideoSession, generation: int, idle_timeout: float
) -> None:
    sock.settimeout(idle_timeout)
    seen_hello = False

    while True:
        try:
            frame = framing.read_frame(sock)
        except TimeoutError:
            logger.info("connection idle timeout, closing (video session state is retained)")
            return
        except (ConnectionError, framing.FrameTooLarge, OSError) as exc:
            logger.warning("connection error, closing: %s", exc)
            return

        if frame is None:
            return  # clean EOF

        try:
            msg = protocol.decode_extension_message(frame)
            if not seen_hello and msg["type"] != "hello":
                raise protocol.ProtocolError("first message on a connection must be hello")
            protocol.check_protocol_version(msg)
        except protocol.ProtocolVersionMismatch:
            # SPEC-V3: "A protocol version mismatch aborts the run with a
            # clear error rather than degrading." There's no wire message
            # for this in SPEC-V3's table, so the abort is the connection
            # itself closing without a hello_ack.
            logger.error("protocol version mismatch, aborting connection")
            return
        except protocol.ProtocolError as exc:
            logger.warning("protocol error, closing connection: %s", exc)
            return

        seen_hello = True

        try:
            response = session.dispatch(msg, generation)
        except VideoSessionError as exc:
            logger.error("session error, closing connection: %s", exc)
            return

        if response is not None:
            messages = response if isinstance(response, list) else [response]
            try:
                for message in messages:
                    framing.write_frame(sock, protocol.encode_message(message))
            except OSError as exc:
                logger.warning("failed to write response, closing: %s", exc)
                return

        if session.is_done and session.is_current(generation):
            try:
                framing.write_frame(sock, protocol.encode_message(protocol.build_stop()))
            except OSError:
                pass
            return
