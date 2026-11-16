"""Transport constants recovered from the supplied native-client evidence.

Source: ``game_source/_analysis_reports/ghidra/protocol-update-lua.c.txt``:
TransportMessage::encode (original export 408810-408942), decode
(408953-409177), and Utils::XOR (409351-409368). This is a fixed public
obfuscation constant, not a user credential or a security boundary.
"""

from typing import Final

XOR_KEY: Final[bytes] = b"SLGFORFEELINGTECH"  # All 17 bytes; no terminating NUL.
PACKET_TYPE_XOR_JSON: Final[int] = 4
PACKET_TYPE_GZIP_XOR_JSON: Final[int] = 8
PACKET_TYPES: Final[frozenset[int]] = frozenset({4, 8})
HEADER_SIZE: Final[int] = 5
COMPRESSION_THRESHOLD: Final[int] = 4096

# Emulator safety limits, not limits claimed for the official server.
MAX_FRAME_BYTES: Final[int] = 5_000_000
MAX_DECODED_BYTES: Final[int] = 8_000_000
