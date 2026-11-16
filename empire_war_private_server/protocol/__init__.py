"""Evidence-based binary framing and asyncio stream decoding."""

from .frame_parser import (
    Frame,
    FrameCodec,
    ProtocolError,
    decode_frame,
    encode_frame,
    encode_payload,
    read_frame,
    xor_crypt,
)

__all__ = [
    "Frame", "FrameCodec", "ProtocolError", "decode_frame", "encode_frame",
    "encode_payload", "read_frame", "xor_crypt",
]
