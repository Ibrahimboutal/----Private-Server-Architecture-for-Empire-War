"""Transport vectors and parser abuse cases; no remote network traffic.

Golden vectors are synthetic, not recordings of user credentials. The optional
legacy-capture check reads existing files in URI read-only mode and never
prints/copies payloads. That old capture format saves bodies, not frame headers.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import zlib

import pytest

from empire_war_private_server.protocol.constants import XOR_KEY
from empire_war_private_server.protocol.frame_parser import (
    FrameCodec,
    ProtocolError,
    decode_frame,
    encode_frame,
    encode_payload,
    read_frame,
    xor_crypt,
)

TEST_KEY = bytes(range(16))
PLAIN_VECTOR = bytes.fromhex("04000000117b236d68263f72757d6c2629622f34387d")
GZIP_VECTOR = bytes.fromhex(
    "08000000251f8b080000000000020aab56cecd50b32f2aadcd51d34cd237b1a80500457416ef11000000"
)
# Independent existing fixture, game_source/tests/test_codec.py; spans several
# cycles of the exact 17-byte client key, unlike an encode/decode self-check.
APK_VECTOR = bytes.fromhex(
    "040000002a286e333f3f37647f67182c3d337669612930382e2921707c672a2a2f222e3a206e2e3a3433333d376438"
)


def reference_frame(plaintext: bytes, mode: int = 4, wrapper: str = "gzip") -> bytes:
    body = bytes(value ^ TEST_KEY[index % 16] for index, value in enumerate(plaintext))
    if mode == 8:
        body = gzip.compress(body, mtime=0) if wrapper == "gzip" else zlib.compress(body)
    return struct.pack(">BI", mode, len(body)) + body


def test_independent_plain_and_compressed_vectors() -> None:
    codec = FrameCodec(TEST_KEY)
    for wire in (PLAIN_VECTOR, GZIP_VECTOR):
        frame = codec.decode_frame(wire)
        assert frame.payload == {"ok": True, "n": 7}
        assert frame.wire_length == len(wire) - 5
        assert frame.plaintext_length == 17
    assert codec.encode_frame(4, {"ok": True, "n": 7}) == PLAIN_VECTOR


def test_recovered_key_vector_and_reset_per_payload() -> None:
    payload = {"type": "Test", "action": "offline-fixture"}
    assert len(XOR_KEY) == 17
    assert decode_frame(APK_VECTOR).payload == payload
    assert encode_frame(4, payload) == APK_VECTOR
    assert encode_payload(payload) == APK_VECTOR
    assert xor_crypt(bytes(34)) == XOR_KEY * 2
    assert xor_crypt(xor_crypt(b"a payload extending across several key cycles")) == (
        b"a payload extending across several key cycles"
    )


@pytest.mark.parametrize("byte_length,mode", [(4095, 4), (4096, 4), (4097, 8)])
def test_native_compression_boundary(byte_length: int, mode: int) -> None:
    payload = {"x": "a" * (byte_length - 8)}
    wire = encode_payload(payload)
    assert wire[0] == mode
    assert decode_frame(wire).plaintext_length == byte_length


def test_boundary_counts_utf8_bytes() -> None:
    wire = encode_payload({"x": "é" * 2045})
    assert wire[0] == 8
    assert decode_frame(wire).plaintext_length == 4098


def test_decompression_precedes_xor_and_zlib_is_accepted() -> None:
    codec = FrameCodec(TEST_KEY)
    frame = codec.decode_frame(reference_frame(b'{"ok":true}', mode=8, wrapper="zlib"))
    assert frame.payload == {"ok": True}
    encoded = codec.encode_frame(8, {"ok": True})
    assert gzip.decompress(encoded[5:]) == xor_crypt(b'{"ok":true}', TEST_KEY)


@pytest.mark.parametrize("wire", [
    b"", b"\x04", b"\x04\0\0\0", b"\x04\0\0\0\x02x",
    b"\x04\0\0\0\x01xy", b"\x7f\0\0\0\0",
    b"\x04\xff\xff\xff\xff", PLAIN_VECTOR + PLAIN_VECTOR,
])
def test_malformed_frames_rejected(wire: bytes) -> None:
    with pytest.raises(ProtocolError):
        FrameCodec(TEST_KEY).decode_frame(wire)


@pytest.mark.parametrize("plaintext", [
    b"not JSON", b"\xff", b"null", b"[]", b"1", b'{"x":NaN}',
    b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
    b'{"action":"one","action":"two"}', b'{"x":{"y":1,"y":2}}',
    b'{"x":"embedded\nnewline"}', b'{"x":1}\0',
])
def test_invalid_or_ambiguous_application_json_rejected(plaintext: bytes) -> None:
    with pytest.raises(ProtocolError):
        FrameCodec(TEST_KEY).decode_frame(reference_frame(plaintext))


def test_compressed_crc_truncation_trailing_streams_and_limits() -> None:
    codec = FrameCodec(TEST_KEY, max_decoded_bytes=128)
    body = GZIP_VECTOR[5:]
    corrupt = bytearray(body)
    corrupt[-8] ^= 1
    for malformed in (body[:-1], bytes(corrupt), body + b"x", body + body, b""):
        with pytest.raises(ProtocolError):
            codec.decode_frame(struct.pack(">BI", 8, len(malformed)) + malformed)
    with pytest.raises(ProtocolError, match="limit"):
        codec.decode_frame(reference_frame(b'{"x":"' + b"a" * 10000 + b'"}', 8))
    exact = b'{"x":"' + b"a" * 120 + b'"}'
    assert codec.decode_frame(reference_frame(exact, 8)).plaintext_length == 128
    with pytest.raises(ProtocolError, match="limit"):
        codec.decode_frame(reference_frame(exact + b" "))


def test_encoding_limits_and_invalid_values() -> None:
    codec = FrameCodec(TEST_KEY, max_frame_bytes=16)
    with pytest.raises(ProtocolError, match="limit"):
        codec.encode_frame(4, {"ok": True, "n": 7})
    codec = FrameCodec(TEST_KEY, max_decoded_bytes=16)
    for payload in ({"x": "a" * 100}, {"x": float("nan")}, {"x": object()}, {"x": "\ud800"}):
        with pytest.raises(ProtocolError):
            codec.encode_frame(4, payload)
    with pytest.raises(ProtocolError):
        encode_frame(99, {})
    with pytest.raises(ProtocolError):
        encode_frame(True, {})


@pytest.mark.parametrize("options", [
    {"key": b""}, {"max_frame_bytes": 0}, {"max_frame_bytes": 2**32},
    {"max_decoded_bytes": True}, {"max_decoded_bytes": -1},
])
def test_configuration_validation(options: dict) -> None:
    with pytest.raises(ValueError):
        FrameCodec(**options)


def test_fragmented_and_coalesced_stream() -> None:
    async def scenario() -> None:
        codec = FrameCodec(TEST_KEY)
        reader = asyncio.StreamReader()

        async def feed() -> None:
            for value in PLAIN_VECTOR + GZIP_VECTOR:
                reader.feed_data(bytes((value,)))
                await asyncio.sleep(0)
            reader.feed_eof()

        task = asyncio.create_task(feed())
        try:
            for _ in range(2):
                frame = await codec.read_frame(reader, timeout=1)
                assert frame is not None and frame.payload == {"ok": True, "n": 7}
            assert await codec.read_frame(reader) is None
        finally:
            await task

        coalesced = asyncio.StreamReader()
        coalesced.feed_data(APK_VECTOR * 2)
        coalesced.feed_eof()
        assert (await read_frame(coalesced)).payload["action"] == "offline-fixture"
        assert (await read_frame(coalesced)).payload["action"] == "offline-fixture"
        assert await read_frame(coalesced) is None

    asyncio.run(scenario())


def test_eof_timeout_cancellation_and_oversized_header() -> None:
    async def scenario() -> None:
        for partial in (APK_VECTOR[:3], APK_VECTOR[:8]):
            reader = asyncio.StreamReader()
            reader.feed_data(partial)
            reader.feed_eof()
            with pytest.raises(ProtocolError, match="ended inside"):
                await read_frame(reader)
        reader = asyncio.StreamReader()
        reader.feed_data(b"\x04\xff\xff\xff\xff")
        with pytest.raises(ProtocolError, match="limit"):
            await read_frame(reader, timeout=1)
        with pytest.raises(TimeoutError):
            await read_frame(asyncio.StreamReader(), timeout=0.01)
        task = asyncio.create_task(read_frame(asyncio.StreamReader()))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_available_legacy_capture_bodies_against_recorded_json() -> None:
    """Check retained ciphertext independently; original headers are absent.

    Source stays read-only and no capture material is included in assertions.
    Selecting short mode-4 records avoids this format's truncated body dumps.
    """
    capture = Path(__file__).resolve().parents[1] / "new-set-up" / "game_protocol.db"
    if not capture.exists():
        pytest.skip("optional local capture evidence is not present")
    codec = FrameCodec()
    verified = 0
    with sqlite3.connect(capture.as_uri() + "?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT pkt_type, payload_hex, payload_json FROM captured_packets "
            "WHERE pkt_type=4 AND length(payload_hex)<1000 "
            "AND payload_json IS NOT NULL ORDER BY id LIMIT 100"
        )
        for mode, raw_hex, stored in rows:
            body = bytes.fromhex(raw_hex)
            wire = struct.pack(">BI", mode, len(body)) + body
            actual = codec.decode_frame(wire).payload
            expected = json.loads(stored)
            canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=True).encode()
            # Hashes avoid disclosing payload contents if an assertion fails.
            actual_hash = hashlib.sha256(canonical(actual)).hexdigest()
            expected_hash = hashlib.sha256(canonical(expected)).hexdigest()
            assert actual_hash == expected_hash
            verified += 1
    assert verified == 100
