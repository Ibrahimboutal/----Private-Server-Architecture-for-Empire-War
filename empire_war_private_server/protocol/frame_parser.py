"""Bounded asyncio codec for the evidenced Empire War binary transport.

Wire format: ``mode:u8 || body_length:u32be || body``. Mode 4 contains
XOR(JSON); mode 8 contains gzip(XOR(JSON)). Native encode uses mode 8 only
above 4,096 UTF-8 bytes. Each payload restarts the 17-byte key at offset zero;
the inspected client has no rotation, padding, or terminating NUL.

Evidence: ``game_source/_analysis_reports/ghidra/protocol-update-lua.c.txt``
and ``ghidra-protocol-audit.md``. Native inflateInit2(47) accepts gzip/zlib;
outgoing frames use gzip. Gzip metadata and JSON member ordering are not
claimed to be byte-identical to all captured encoders. Strict JSON validation,
duplicate-key rejection, and size limits are deliberate emulator hardening.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import gzip
import json
import math
import struct
from typing import Any
import zlib

from .constants import (
    COMPRESSION_THRESHOLD,
    MAX_DECODED_BYTES,
    MAX_FRAME_BYTES,
    PACKET_TYPE_GZIP_XOR_JSON,
    PACKET_TYPE_XOR_JSON,
    PACKET_TYPES,
    XOR_KEY,
)

HEADER = struct.Struct(">BI")


class ProtocolError(ValueError):
    """Malformed or oversized frame; messages never contain packet contents."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete, successfully decoded application message."""

    packet_type: int
    payload: dict[str, Any]
    wire_length: int
    plaintext_length: int


def xor_crypt(data: bytes, key: bytes = XOR_KEY) -> bytes:
    """Apply Utils::XOR's repeating-key transform from payload offset zero."""
    if not isinstance(data, bytes) or not isinstance(key, bytes):
        raise TypeError("data and key must be bytes")
    if not key:
        raise ValueError("XOR key must not be empty")
    return bytes(value ^ key[index % len(key)] for index, value in enumerate(data))


def _reject_constant(_value: str) -> Any:
    raise ProtocolError("non-finite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError("non-finite JSON number")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON object key")
        result[key] = value
    return result


def _limit(value: int, name: str, maximum: int | None = None) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} exceeds its maximum")
    return value


class FrameCodec:
    """Encode and decode supported transport modes without network side effects.

    ``read_frame`` distinguishes a clean EOF between frames from truncation.
    Its caller can impose an overall request deadline with asyncio.timeout;
    an optional timeout here covers the whole header-and-body read.
    """

    def __init__(
        self,
        key: bytes = XOR_KEY,
        *,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_decoded_bytes: int = MAX_DECODED_BYTES,
    ) -> None:
        if not isinstance(key, bytes):
            raise TypeError("key must be bytes")
        if not key:
            raise ValueError("XOR key must not be empty")
        self._key = key
        self.max_frame_bytes = _limit(max_frame_bytes, "max_frame_bytes", 0xFFFFFFFF)
        self.max_decoded_bytes = _limit(max_decoded_bytes, "max_decoded_bytes")

    def _validate_header(self, packet_type: int, body_length: int) -> None:
        if type(packet_type) is not int or packet_type not in PACKET_TYPES:
            raise ProtocolError("unsupported packet type")
        if body_length > self.max_frame_bytes:
            raise ProtocolError("frame body exceeds configured limit")

    def _inflate(self, body: bytes) -> bytes:
        inflater = zlib.decompressobj(wbits=47)
        try:
            decoded = inflater.decompress(body, self.max_decoded_bytes + 1)
        except zlib.error as exc:
            raise ProtocolError("invalid gzip/zlib payload") from exc
        if len(decoded) > self.max_decoded_bytes or inflater.unconsumed_tail:
            raise ProtocolError("expanded payload exceeds configured limit")
        if not inflater.eof:
            raise ProtocolError("truncated gzip/zlib payload")
        if inflater.unused_data:
            raise ProtocolError("trailing data after compressed payload")
        return decoded

    def _decode_body(self, packet_type: int, body: bytes) -> Frame:
        encrypted = self._inflate(body) if packet_type == PACKET_TYPE_GZIP_XOR_JSON else body
        if len(encrypted) > self.max_decoded_bytes:
            raise ProtocolError("decoded payload exceeds configured limit")
        plaintext = xor_crypt(encrypted, self._key)
        try:
            payload = json.loads(
                plaintext.decode("utf-8", errors="strict"),
                parse_constant=_reject_constant,
                parse_float=_finite_float,
                object_pairs_hook=_unique_object,
            )
        except ProtocolError:
            raise
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProtocolError("payload is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ProtocolError("packet JSON must be an object")
        return Frame(packet_type, payload, len(body), len(plaintext))

    def decode_frame(self, wire: bytes) -> Frame:
        """Decode exactly one complete frame; reject trailing/coalesced bytes."""
        if len(wire) < HEADER.size:
            raise ProtocolError("truncated frame header")
        packet_type, body_length = HEADER.unpack_from(wire)
        self._validate_header(packet_type, body_length)
        if len(wire) != HEADER.size + body_length:
            raise ProtocolError("frame length does not match header")
        return self._decode_body(packet_type, wire[HEADER.size:])

    def _serialize(self, payload: dict[str, Any]) -> bytes:
        if not isinstance(payload, dict):
            raise ProtocolError("packet JSON must be an object")
        try:
            plaintext = json.dumps(
                payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProtocolError("payload cannot be encoded as UTF-8 JSON") from exc
        if len(plaintext) > self.max_decoded_bytes:
            raise ProtocolError("decoded payload exceeds configured limit")
        return plaintext

    def _encode(self, packet_type: int, plaintext: bytes) -> bytes:
        self._validate_header(packet_type, 0)
        body = xor_crypt(plaintext, self._key)
        if packet_type == PACKET_TYPE_GZIP_XOR_JSON:
            body = gzip.compress(body, mtime=0)
        self._validate_header(packet_type, len(body))
        return HEADER.pack(packet_type, len(body)) + body

    def encode_frame(self, packet_type: int, payload: dict[str, Any]) -> bytes:
        """Encode with an explicitly selected evidenced mode (4 or 8)."""
        return self._encode(packet_type, self._serialize(payload))

    def encode_payload(self, payload: dict[str, Any]) -> bytes:
        """Select mode with the native client's exact 4,096-byte threshold."""
        plaintext = self._serialize(payload)
        packet_type = (
            PACKET_TYPE_GZIP_XOR_JSON
            if len(plaintext) > COMPRESSION_THRESHOLD
            else PACKET_TYPE_XOR_JSON
        )
        return self._encode(packet_type, plaintext)

    async def read_frame(
        self, reader: asyncio.StreamReader, *, timeout: float | None = None
    ) -> Frame | None:
        """Read one frame, returning None only for clean EOF between frames.

        Invalid lengths and modes fail before body allocation. TimeoutError
        and task cancellation propagate so the gateway can close the stream.
        """
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0
        ):
            raise ValueError("timeout must be positive and finite")
        async with asyncio.timeout(timeout):
            try:
                header = await reader.readexactly(HEADER.size)
            except asyncio.IncompleteReadError as exc:
                if not exc.partial:
                    return None
                raise ProtocolError("connection ended inside frame header") from exc
            packet_type, body_length = HEADER.unpack(header)
            self._validate_header(packet_type, body_length)
            try:
                body = await reader.readexactly(body_length)
            except asyncio.IncompleteReadError as exc:
                raise ProtocolError("connection ended inside frame body") from exc
            return self._decode_body(packet_type, body)


_DEFAULT_CODEC = FrameCodec()


def encode_frame(packet_type: int, payload: dict[str, Any]) -> bytes:
    """Encode using the recovered key and default emulator size limits."""
    return _DEFAULT_CODEC.encode_frame(packet_type, payload)


def encode_payload(payload: dict[str, Any]) -> bytes:
    """Encode using the native client's automatic mode selection."""
    return _DEFAULT_CODEC.encode_payload(payload)


def decode_frame(wire: bytes) -> Frame:
    """Decode exactly one complete frame with the default codec."""
    return _DEFAULT_CODEC.decode_frame(wire)


async def read_frame(
    reader: asyncio.StreamReader, *, timeout: float | None = None
) -> Frame | None:
    """Read one complete frame with the default codec."""
    return await _DEFAULT_CODEC.read_frame(reader, timeout=timeout)
