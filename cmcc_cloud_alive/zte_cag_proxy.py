#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZTE CAG proxy add-link packet builder + single-link proxy conn.

Pure-Python port of B's ``internal/zte/cag_proxy.go``.

This module owns the *wire format* of the CAG proxy framing protocol:

    +----+--------+--------+-----------------+
    |cmd | linkID | u16len |   payload ...   |
    +----+--------+--------+-----------------+
      1B    1B       2B LE      len bytes

Three commands are defined (aligned 1:1 with B's Go constants):

    * ``CAG_PROXY_DATA_CMD``      (0x0a) — link data frame
    * ``CAG_PROXY_ADD_LINK_CMD``  (0x1a) — open a new link (carries LinkInfo)
    * ``CAG_PROXY_CLOSE_LINK_CMD``(0x2a) — close a link (peer sees EOF)

``zte_cag_mux`` imports the frame helpers + add-link builder from here so the
two modules never form an import cycle (mux → proxy, never the reverse).
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from typing import Optional, Tuple

__all__ = [
    # constants
    "CAG_PROXY_DATA_CMD",
    "CAG_PROXY_ADD_LINK_CMD",
    "CAG_PROXY_CLOSE_LINK_CMD",
    "CAG_PROXY_PAYLOAD_MAX",
    # frame helpers
    "pack_frame",
    "parse_frame_header",
    "read_frame",
    "recv_exact",
    # id helpers
    "random_hex",
    "new_zte_link_uuid",
    "parse_uuid_bytes",
    "hex_prefix",
    "copy_c_string",
    # packet builder
    "build_cag_proxy_add_link_packet",
    # single-link proxy conn
    "CAGProxyConn",
    "open_cag_proxy_link",
    "open_cag_proxy_link_with_trace",
]


# ---------------------------------------------------------------------------
# Constants — aligned with B's cag_proxy.go / cag_mux.go
# ---------------------------------------------------------------------------
CAG_PROXY_DATA_CMD = 0x0A
CAG_PROXY_ADD_LINK_CMD = 0x1A
CAG_PROXY_CLOSE_LINK_CMD = 0x2A

#: Maximum payload bytes per data frame (u16 length field → 0xffff).
CAG_PROXY_PAYLOAD_MAX = 0xFFFF

#: Fixed size of the add-link LinkInfo payload (B uses a 0x9a-byte block).
_CAG_ADD_LINK_PAYLOAD_LEN = 0x9A


# ---------------------------------------------------------------------------
# Frame helpers (shared by cag_proxy + cag_mux)
# ---------------------------------------------------------------------------
def pack_frame(cmd: int, link_id: int, payload: bytes = b"") -> bytes:
    """Pack a 4-byte-header CAG proxy frame (P8-002).

    Layout: ``[cmd][linkID][u16 len LE][payload]``.
    """
    payload = bytes(payload)
    if len(payload) > CAG_PROXY_PAYLOAD_MAX:
        raise ValueError(
            "cag proxy frame payload too large: %d > %d"
            % (len(payload), CAG_PROXY_PAYLOAD_MAX)
        )
    return struct.pack("<BBH", cmd & 0xFF, link_id & 0xFF, len(payload)) + payload


def parse_frame_header(hdr: bytes) -> Tuple[int, int, int]:
    """Parse a 4-byte frame header → (cmd, link_id, length) (P8-003)."""
    if len(hdr) < 4:
        raise ValueError("cag proxy frame header needs 4 bytes, got %d" % len(hdr))
    cmd, link_id, length = struct.unpack("<BBH", hdr[:4])
    return cmd, link_id, length


def recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from *conn*, handling partial reads (P8-003).

    Raises ``ConnectionError`` on premature EOF (mirrors Go's ``io.ErrUnexpectedEOF``)
    and propagates socket timeouts as ``TimeoutError``.
    """
    chunks = []
    remain = n
    while remain > 0:
        try:
            data = conn.recv(remain)
        except socket.timeout as exc:
            raise TimeoutError("cag proxy read timeout") from exc
        if not data:
            raise ConnectionError(
                "cag proxy short read: wanted %d, got %d" % (n, n - remain)
            )
        chunks.append(data)
        remain -= len(data)
    return b"".join(chunks)


def read_frame(conn: socket.socket) -> Tuple[int, int, bytes]:
    """Read one full CAG proxy frame from *conn* (P8-003 partial-read safe).

    Returns ``(cmd, link_id, payload)``.
    """
    hdr = recv_exact(conn, 4)
    cmd, link_id, length = parse_frame_header(hdr)
    payload = recv_exact(conn, length) if length else b""
    return cmd, link_id, payload


# ---------------------------------------------------------------------------
# ID / string helpers — port of B's randomHex / newZTELinkUUID / copyCString
# ---------------------------------------------------------------------------
def random_hex(n: int) -> str:
    """Return *2n* hex chars from *n* random bytes (port of ``randomHex``)."""
    return os.urandom(n).hex()


def new_zte_link_uuid() -> bytes:
    """Generate a 16-byte UUID-v4-style link UUID (port of ``newZTELinkUUID``).

    Sets the version (0x40) and variant (0x80) bits like a RFC-4122 v4 UUID.
    """
    link_uuid = bytearray(os.urandom(16))
    link_uuid[6] = (link_uuid[6] & 0x0F) | 0x40  # version 4
    link_uuid[8] = (link_uuid[8] & 0x3F) | 0x80  # RFC-4122 variant
    return bytes(link_uuid)


def parse_uuid_bytes(value: str) -> bytes:
    """Decode a dashed/undashed hex UUID string into 16 raw bytes."""
    cleaned = value.replace("-", "").strip()
    if len(cleaned) != 32:
        raise ValueError("invalid UUID length: %r" % value)
    return bytes.fromhex(cleaned)


def hex_prefix(s: str, n: int) -> str:
    """Return the first *n* hex chars of *s* (port of ``hexPrefix``)."""
    return s[:n]


def copy_c_string(buf: bytearray, offset: int, size: int, text: str) -> None:
    """Write *text* into ``buf[offset:offset+size]`` C-string style (P8-011).

    Bytes beyond the text are left zero (the buffer is pre-zeroed by the
    caller); the text is truncated to ``size-1`` so a NUL terminator always
    remains, matching Go's ``copyCString``.
    """
    encoded = text.encode("utf-8", "replace")[: size - 1]
    buf[offset : offset + len(encoded)] = encoded
    # remainder stays zero (NUL-padded)


# ---------------------------------------------------------------------------
# Add-link packet builder — port of B's buildCAGProxyAddLinkPacket (P8-010..012)
# ---------------------------------------------------------------------------
def build_cag_proxy_add_link_packet(
    params,
    link_id: int,
    link_uuid: bytes = b"",
    trace_id: str = "",
    span_id: str = "",
) -> bytes:
    """Build the CAG proxy *add-link* packet (P8-010/011/012).

    The packet is ``4 + 0x9a`` bytes::

        [0]    = CAG_PROXY_ADD_LINK_CMD (0x1a)
        [1]    = link_id
        [2:4]  = 0x009a (LE u16 payload length)
        [4:]   = LinkInfo payload (0x9a bytes)

    LinkInfo payload layout (aligned 1:1 with B's Go):

        [0:2]   port            (LE u16)
        [2]     channel type    (1 if link_id==1 else 2)
        [4:8]   IPv4            (reversed byte order: ip[3],ip[2],ip[1],ip[0])
        [0x53]  QoS             (0x05)
        [0x54]  SPICE main ch   (0x01 when link_id==1)
        [0x68:0x89] trace_id    (33-byte C string)
        [0x89:0x9a] span_id     (17-byte C string)

    Note: ``link_uuid`` is accepted for API symmetry with B but is **not**
    written into the add-link payload (B's Go does the same — the UUID is
    carried out-of-band by the caller / REDQ builder).
    """
    host = params.host
    port = int(params.port)
    # Parse IPv4 → 4 bytes; reject non-IPv4 (B only supports IPv4 here).
    try:
        packed_ip = socket.inet_aton(host)
    except OSError as exc:
        raise ValueError(
            "CAG proxy add-link currently supports IPv4 only, got %r" % host
        ) from exc
    ip = struct.unpack("<BBBB", packed_ip)  # network order → (a,b,c,d)

    payload = bytearray(_CAG_ADD_LINK_PAYLOAD_LEN)  # zero-initialised
    struct.pack_into("<H", payload, 0, port & 0xFFFF)
    payload[2] = 0x01 if link_id == 1 else 0x02
    # Reversed byte order, matching B's addr.As4() assignment.
    payload[4] = ip[3]
    payload[5] = ip[2]
    payload[6] = ip[1]
    payload[7] = ip[0]
    payload[0x53] = 0x05  # QoS value used by the ZTE tunnel LinkInfo.
    if link_id == 1:
        payload[0x54] = 0x01  # SPICE main channel type.
    copy_c_string(payload, 0x68, 0x21, trace_id)  # 0x89-0x68 = 0x21 = 33
    copy_c_string(payload, 0x89, 0x11, span_id)  # 0x9a-0x89 = 0x11 = 17

    return pack_frame(CAG_PROXY_ADD_LINK_CMD, link_id, bytes(payload))


# ---------------------------------------------------------------------------
# CAGProxyConn — single-link proxy over a raw TLS conn (port of cag_proxy.go)
# ---------------------------------------------------------------------------
class CAGProxyConn:
    """A single-link CAG proxy connection (port of B's ``CAGProxyConn``).

    Wraps a raw (already-TLS) socket: the add-link packet is sent on
    construction, then ``read``/``write``/``close`` operate on data frames
    filtered by ``link_id``.  This is the *non-multiplexed* path used when
    only one inner link is needed.
    """

    def __init__(
        self,
        conn: socket.socket,
        params,
        link_id: int,
        link_uuid: bytes,
        trace_id: str,
        span_id: str,
    ):
        self.conn = conn
        self.link_id = link_id
        self.link_uuid = link_uuid
        self.trace_id = trace_id
        self.span_id = span_id
        self.redq_span_id = random_hex(8)
        self._rbuf = bytearray()
        self._closed = False
        self._mu = threading.Lock()

    # -- factory -----------------------------------------------------------
    @classmethod
    def open(
        cls,
        conn: socket.socket,
        params,
        link_id: int,
        trace_id: str = "",
        span_id: str = "",
        link_uuid: Optional[bytes] = None,
    ) -> "CAGProxyConn":
        """Send the add-link packet and return a ready ``CAGProxyConn``."""
        if link_uuid is None:
            link_uuid = new_zte_link_uuid()
        packet = build_cag_proxy_add_link_packet(
            params, link_id, link_uuid, trace_id, span_id
        )
        conn.sendall(packet)
        return cls(conn, params, link_id, link_uuid, trace_id, span_id)

    # -- read --------------------------------------------------------------
    def read(self, n: int = 65536) -> bytes:
        """Read up to *n* bytes; returns ``b""`` on close-frame EOF."""
        with self._mu:
            if self._rbuf:
                out = bytes(self._rbuf[:n])
                del self._rbuf[:n]
                return out
        while True:
            cmd, link_id, payload = read_frame(self.conn)
            if cmd == CAG_PROXY_CLOSE_LINK_CMD:
                return b""  # EOF
            if link_id != self.link_id:
                continue  # discard frames for other links
            if len(payload) <= n:
                return payload
            out = payload[:n]
            with self._mu:
                self._rbuf.extend(payload[n:])
            return out

    def recv(self, n: int = 65536) -> bytes:
        """Alias for :meth:`read` (socket-compatible name)."""
        return self.read(n)

    # -- write -------------------------------------------------------------
    def write(self, data: bytes) -> int:
        """Write *data*, splitting at ``CAG_PROXY_PAYLOAD_MAX`` (P8-007)."""
        data = bytes(data)
        written = 0
        p = memoryview(data)
        while len(p) > 0:
            chunk_len = min(len(p), CAG_PROXY_PAYLOAD_MAX)
            frame = pack_frame(CAG_PROXY_DATA_CMD, self.link_id, bytes(p[:chunk_len]))
            self.conn.sendall(frame)
            written += chunk_len
            p = p[chunk_len:]
        return written

    sendall = write

    # -- close -------------------------------------------------------------
    def close(self) -> None:
        """Send a close-link frame (peer will see EOF) and mark closed."""
        with self._mu:
            if self._closed:
                return
            self._closed = True
        try:
            self.conn.sendall(pack_frame(CAG_PROXY_CLOSE_LINK_CMD, self.link_id, b""))
        except OSError:
            pass

    # -- deadlines ---------------------------------------------------------
    def settimeout(self, seconds: Optional[float]) -> None:
        self.conn.settimeout(seconds)

    def set_read_deadline(self, seconds: Optional[float]) -> None:
        self.conn.settimeout(seconds)

    def set_write_deadline(self, seconds: Optional[float]) -> None:
        self.conn.settimeout(seconds)

    def fileno(self) -> int:
        return self.conn.fileno()

    # -- buffer inspection (parity with B's discard/take helpers) ----------
    def discard_read_buffer(self) -> None:
        with self._mu:
            self._rbuf.clear()

    def take_read_buffer(self) -> bytes:
        with self._mu:
            out = bytes(self._rbuf)
            self._rbuf.clear()
            return out

    def take_read_buffer_n(self, n: int) -> bytes:
        with self._mu:
            out = bytes(self._rbuf[:n])
            del self._rbuf[:n]
            return out


def open_cag_proxy_link(
    conn: socket.socket, params, link_id: int
) -> CAGProxyConn:
    """Open a single-link CAG proxy connection (no trace/span)."""
    return CAGProxyConn.open(conn, params, link_id)


def open_cag_proxy_link_with_trace(
    conn: socket.socket,
    params,
    link_id: int,
    trace_id: str,
    span_id: str,
    link_uuid: Optional[bytes] = None,
) -> CAGProxyConn:
    """Open a single-link CAG proxy connection with explicit trace/span IDs."""
    return CAGProxyConn.open(
        conn, params, link_id, trace_id=trace_id, span_id=span_id, link_uuid=link_uuid
    )
