#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Byte-level fixture + integration tests for P8 (CAG mux + proxy).

Covers:
  - P8-002  pack_frame header layout + round-trip
  - P8-003  parse_frame_header + read_frame partial-read safety
  - P8-010  build_cag_proxy_add_link_packet exact byte fixture (link 1 & 2)
  - P8-011  copy_c_string truncation / NUL-pad
  - P8-012  IPv4 reversed byte order in LinkInfo
  - helpers new_zte_link_uuid (v4 version/variant), random_hex, parse_uuid_bytes
  - CAGProxyConn open/read/write/close over a socketpair
  - CAGMux multi-link demux / close-link EOF / write / mux close
"""
import os
import socket
import struct
import sys
import threading
import time
import unittest

# Ensure repo root is importable when run directly.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from cmcc_cloud_alive.zte_cag_proxy import (
    CAG_PROXY_ADD_LINK_CMD,
    CAG_PROXY_CLOSE_LINK_CMD,
    CAG_PROXY_DATA_CMD,
    CAG_PROXY_PAYLOAD_MAX,
    CAGProxyConn,
    build_cag_proxy_add_link_packet,
    copy_c_string,
    hex_prefix,
    new_zte_link_uuid,
    pack_frame,
    parse_frame_header,
    parse_uuid_bytes,
    random_hex,
    read_frame,
    recv_exact,
)
from cmcc_cloud_alive.zte_cag_mux import CAGMux, CAGMuxLink


# ---------------------------------------------------------------------------
# Stub params (duck-typed: only .host / .port are read by the builder).
# ---------------------------------------------------------------------------
class _StubParams:
    def __init__(self, host, port):
        self.host = host
        self.port = port


# ===========================================================================
# P8-002 : pack_frame
# ===========================================================================
class TestPackFrame(unittest.TestCase):
    def test_header_layout(self):
        frame = pack_frame(CAG_PROXY_DATA_CMD, 3, b"hello")
        self.assertEqual(frame[:4], struct.pack("<BBH", 0x0A, 3, 5))
        self.assertEqual(frame[4:], b"hello")

    def test_empty_payload(self):
        frame = pack_frame(CAG_PROXY_CLOSE_LINK_CMD, 7, b"")
        self.assertEqual(frame, bytes([0x2A, 7, 0, 0]))

    def test_max_payload_allowed(self):
        payload = b"\x00" * CAG_PROXY_PAYLOAD_MAX
        frame = pack_frame(CAG_PROXY_DATA_CMD, 1, payload)
        self.assertEqual(len(frame), 4 + CAG_PROXY_PAYLOAD_MAX)

    def test_oversize_rejected(self):
        with self.assertRaises(ValueError):
            pack_frame(CAG_PROXY_DATA_CMD, 1, b"\x00" * (CAG_PROXY_PAYLOAD_MAX + 1))


# ===========================================================================
# P8-003 : parse_frame_header + read_frame partial reads
# ===========================================================================
class _ChunkySocket:
    """Fake socket that returns data in small chunks to exercise partial reads."""

    def __init__(self, data: bytes, chunk: int = 1):
        self._buf = bytearray(data)
        self._chunk = chunk
        self.timeout = None

    def recv(self, n):
        if not self._buf:
            return b""
        take = min(n, self._chunk, len(self._buf))
        out = bytes(self._buf[:take])
        del self._buf[:take]
        return out

    def settimeout(self, t):
        self.timeout = t


class TestReadFrame(unittest.TestCase):
    def test_parse_header(self):
        cmd, lid, ln = parse_frame_header(bytes([0x1A, 2, 0x9A, 0x00]))
        self.assertEqual((cmd, lid, ln), (0x1A, 2, 0x9A))

    def test_recv_exact_partial(self):
        s = _ChunkySocket(b"\x01\x02\x03\x04\x05", chunk=2)
        self.assertEqual(recv_exact(s, 5), b"\x01\x02\x03\x04\x05")

    def test_recv_exact_short_eof(self):
        s = _ChunkySocket(b"\x01\x02", chunk=1)
        with self.assertRaises(ConnectionError):
            recv_exact(s, 5)

    def test_read_frame_chunked(self):
        payload = b"the quick brown fox"
        frame = pack_frame(CAG_PROXY_DATA_CMD, 5, payload)
        s = _ChunkySocket(frame, chunk=3)
        cmd, lid, pl = read_frame(s)
        self.assertEqual((cmd, lid), (CAG_PROXY_DATA_CMD, 5))
        self.assertEqual(pl, payload)

    def test_read_frame_empty_payload(self):
        frame = pack_frame(CAG_PROXY_CLOSE_LINK_CMD, 9, b"")
        s = _ChunkySocket(frame, chunk=2)
        cmd, lid, pl = read_frame(s)
        self.assertEqual((cmd, lid, pl), (CAG_PROXY_CLOSE_LINK_CMD, 9, b""))


# ===========================================================================
# P8-010 / P8-011 / P8-012 : add-link packet byte fixture
# ===========================================================================
class TestAddLinkPacket(unittest.TestCase):
    def _expected_payload(self, host, port, link_id, trace_id, span_id):
        """Independent reconstruction of the LinkInfo payload."""
        pl = bytearray(0x9A)
        struct.pack_into("<H", pl, 0, port & 0xFFFF)
        pl[2] = 0x01 if link_id == 1 else 0x02
        a, b, c, d = socket.inet_aton(host)
        pl[4], pl[5], pl[6], pl[7] = d, c, b, a  # reversed
        pl[0x53] = 0x05
        if link_id == 1:
            pl[0x54] = 0x01
        t = trace_id.encode()[:0x20]
        pl[0x68:0x68 + len(t)] = t
        s = span_id.encode()[:0x10]
        pl[0x89:0x89 + len(s)] = s
        return bytes(pl)

    def test_link1_byte_fixture(self):
        params = _StubParams("10.20.30.40", 5900)
        pkt = build_cag_proxy_add_link_packet(
            params, link_id=1, trace_id="trace-abc", span_id="span-xyz"
        )
        # header
        self.assertEqual(pkt[0], CAG_PROXY_ADD_LINK_CMD)
        self.assertEqual(pkt[1], 1)
        self.assertEqual(struct.unpack("<H", pkt[2:4])[0], 0x9A)
        self.assertEqual(len(pkt), 4 + 0x9A)
        # payload
        expected = self._expected_payload(
            "10.20.30.40", 5900, 1, "trace-abc", "span-xyz"
        )
        self.assertEqual(pkt[4:], expected)
        # spot checks
        payload = pkt[4:]
        self.assertEqual(payload[0:2], struct.pack("<H", 5900))
        self.assertEqual(payload[2], 0x01)
        self.assertEqual(payload[4:8], bytes([40, 30, 20, 10]))  # reversed
        self.assertEqual(payload[0x53], 0x05)
        self.assertEqual(payload[0x54], 0x01)
        self.assertEqual(payload[0x68:0x68 + 9], b"trace-abc")
        self.assertEqual(payload[0x89:0x89 + 8], b"span-xyz")

    def test_link2_byte_fixture(self):
        params = _StubParams("192.168.1.1", 443)
        pkt = build_cag_proxy_add_link_packet(
            params, link_id=2, trace_id="t", span_id="s"
        )
        payload = pkt[4:]
        self.assertEqual(payload[2], 0x02)
        self.assertEqual(payload[0x54], 0x00)  # not set for link 2
        self.assertEqual(payload[4:8], bytes([1, 1, 168, 192]))  # reversed

    def test_link_uuid_not_in_payload(self):
        """B's Go does not write linkUUID into the add-link payload."""
        params = _StubParams("1.2.3.4", 80)
        uuid_a = new_zte_link_uuid()
        uuid_b = new_zte_link_uuid()
        pkt_a = build_cag_proxy_add_link_packet(params, 1, uuid_a, "x", "y")
        pkt_b = build_cag_proxy_add_link_packet(params, 1, uuid_b, "x", "y")
        self.assertEqual(pkt_a, pkt_b)  # UUID has no effect on bytes

    def test_non_ipv4_rejected(self):
        params = _StubParams("not.an.ip", 80)
        with self.assertRaises(ValueError):
            build_cag_proxy_add_link_packet(params, 1)

    def test_trace_span_truncation(self):
        """Strings longer than their slot are truncated (NUL kept)."""
        params = _StubParams("1.2.3.4", 80)
        long_trace = "T" * 100
        long_span = "S" * 100
        pkt = build_cag_proxy_add_link_packet(
            params, 1, trace_id=long_trace, span_id=long_span
        )
        payload = pkt[4:]
        # trace slot 0x68:0x89 = 33 bytes, last byte must be NUL
        self.assertEqual(payload[0x88], 0x00)
        self.assertEqual(payload[0x68:0x88], b"T" * 0x20)
        # span slot 0x89:0x9a = 17 bytes, last byte NUL
        self.assertEqual(payload[0x99], 0x00)
        self.assertEqual(payload[0x89:0x99], b"S" * 0x10)


# ===========================================================================
# Helpers
# ===========================================================================
class TestHelpers(unittest.TestCase):
    def test_new_zte_link_uuid_v4(self):
        u = new_zte_link_uuid()
        self.assertEqual(len(u), 16)
        self.assertEqual(u[6] & 0xF0, 0x40)  # version 4
        self.assertEqual(u[8] & 0xC0, 0x80)  # RFC-4122 variant

    def test_random_hex_length(self):
        self.assertEqual(len(random_hex(8)), 16)
        self.assertEqual(len(random_hex(4)), 8)

    def test_parse_uuid_bytes(self):
        u = parse_uuid_bytes("12345678-1234-4234-8234-1234567890ab")
        self.assertEqual(len(u), 16)
        self.assertEqual(u.hex(), "123456781234423482341234567890ab")

    def test_parse_uuid_bad(self):
        with self.assertRaises(ValueError):
            parse_uuid_bytes("short")

    def test_hex_prefix(self):
        self.assertEqual(hex_prefix("abcdef", 3), "abc")

    def test_copy_c_string(self):
        buf = bytearray(8)
        copy_c_string(buf, 0, 8, "hi")
        self.assertEqual(buf, b"hi\x00\x00\x00\x00\x00\x00")
        # truncation keeps a NUL
        buf2 = bytearray(4)
        copy_c_string(buf2, 0, 4, "abcdef")
        self.assertEqual(buf2, b"abc\x00")


# ===========================================================================
# CAGProxyConn integration over socketpair
# ===========================================================================
class TestCAGProxyConn(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.a.settimeout(2)
        self.b.settimeout(2)

    def tearDown(self):
        for s in (self.a, self.b):
            try:
                s.close()
            except OSError:
                pass

    def test_open_sends_add_link(self):
        params = _StubParams("10.0.0.1", 5900)
        conn = CAGProxyConn.open(self.a, params, link_id=1, trace_id="t", span_id="s")
        pkt = self.b.recv(4096)
        self.assertEqual(pkt[0], CAG_PROXY_ADD_LINK_CMD)
        self.assertEqual(pkt[1], 1)
        self.assertEqual(conn.link_id, 1)
        self.assertEqual(len(conn.link_uuid), 16)

    def test_read_filters_by_link_id(self):
        params = _StubParams("10.0.0.1", 5900)
        conn = CAGProxyConn.open(self.a, params, link_id=1)
        self.b.recv(4096)  # consume add-link
        # frame for a different link → must be discarded
        self.b.sendall(pack_frame(CAG_PROXY_DATA_CMD, 9, b"other"))
        # frame for our link
        self.b.sendall(pack_frame(CAG_PROXY_DATA_CMD, 1, b"mine"))
        self.assertEqual(conn.read(64), b"mine")

    def test_close_frame_eof(self):
        params = _StubParams("10.0.0.1", 5900)
        conn = CAGProxyConn.open(self.a, params, link_id=1)
        self.b.recv(4096)
        self.b.sendall(pack_frame(CAG_PROXY_CLOSE_LINK_CMD, 1, b""))
        self.assertEqual(conn.read(64), b"")

    def test_write_and_close(self):
        params = _StubParams("10.0.0.1", 5900)
        conn = CAGProxyConn.open(self.a, params, link_id=2)
        self.b.recv(4096)  # add-link
        conn.write(b"payload")
        frame = self.b.recv(4096)
        cmd, lid, ln = parse_frame_header(frame[:4])
        self.assertEqual((cmd, lid), (CAG_PROXY_DATA_CMD, 2))
        self.assertEqual(frame[4:4 + ln], b"payload")
        conn.close()
        frame2 = self.b.recv(4096)
        cmd2, lid2, _ = parse_frame_header(frame2[:4])
        self.assertEqual((cmd2, lid2), (CAG_PROXY_CLOSE_LINK_CMD, 2))

    def test_write_chunks_large(self):
        params = _StubParams("10.0.0.1", 5900)
        conn = CAGProxyConn.open(self.a, params, link_id=1)
        self.b.recv(4096)
        big = os.urandom(CAG_PROXY_PAYLOAD_MAX + 1000)
        t = threading.Thread(target=conn.write, args=(big,))
        t.start()
        collected = bytearray()
        while len(collected) < len(big) + 8:  # 2 frames * 4B header
            try:
                collected.extend(self.b.recv(65536))
            except socket.timeout:
                break
        t.join()
        # parse frames and concatenate payloads
        off = 0
        payloads = bytearray()
        frames = 0
        while off < len(collected):
            if off + 4 > len(collected):
                break
            c, l, ln = parse_frame_header(bytes(collected[off:off + 4]))
            self.assertEqual(c, CAG_PROXY_DATA_CMD)
            payloads.extend(collected[off + 4:off + 4 + ln])
            off += 4 + ln
            frames += 1
        self.assertEqual(frames, 2)
        self.assertEqual(bytes(payloads), big)


# ===========================================================================
# CAGMux multi-link integration over socketpair
# ===========================================================================
class TestCAGMux(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.a.settimeout(2)
        self.b.settimeout(2)

    def tearDown(self):
        for s in (self.a, self.b):
            try:
                s.close()
            except OSError:
                pass

    def _drain_addlink(self):
        """Read one add-link frame from b, return link_id."""
        hdr = self.b.recv(4)
        cmd, lid, ln = parse_frame_header(hdr)
        self.assertEqual(cmd, CAG_PROXY_ADD_LINK_CMD)
        if ln:
            self.b.recv(ln)
        return lid

    def test_open_two_links(self):
        mux = CAGMux.open(self.a)
        params = _StubParams("10.0.0.1", 5900)
        link1 = mux.open_link(params)
        link2 = mux.open_link(params)
        self.assertEqual(link1.link_id, 1)
        self.assertEqual(link2.link_id, 2)
        self.assertEqual(self._drain_addlink(), 1)
        self.assertEqual(self._drain_addlink(), 2)
        self.assertEqual(mux.link_count(), 2)
        mux.close()

    def test_demux_data_to_correct_link(self):
        mux = CAGMux.open(self.a)
        params = _StubParams("10.0.0.1", 5900)
        link1 = mux.open_link(params)
        link2 = mux.open_link(params)
        self._drain_addlink()
        self._drain_addlink()
        # send data for link 2 first
        self.b.sendall(pack_frame(CAG_PROXY_DATA_CMD, 2, b"two"))
        self.b.sendall(pack_frame(CAG_PROXY_DATA_CMD, 1, b"one"))
        link1.set_read_deadline(2)
        link2.set_read_deadline(2)
        self.assertEqual(link2.read(64), b"two")
        self.assertEqual(link1.read(64), b"one")
        mux.close()

    def test_close_link_eof(self):
        mux = CAGMux.open(self.a)
        params = _StubParams("10.0.0.1", 5900)
        link1 = mux.open_link(params)
        self._drain_addlink()
        self.b.sendall(pack_frame(CAG_PROXY_CLOSE_LINK_CMD, 1, b""))
        link1.set_read_deadline(2)
        self.assertEqual(link1.read(64), b"")
        mux.close()

    def test_link_write(self):
        mux = CAGMux.open(self.a)
        params = _StubParams("10.0.0.1", 5900)
        link1 = mux.open_link(params)
        self._drain_addlink()
        link1.write(b"hello")
        frame = self.b.recv(4096)
        cmd, lid, ln = parse_frame_header(frame[:4])
        self.assertEqual((cmd, lid), (CAG_PROXY_DATA_CMD, 1))
        self.assertEqual(frame[4:4 + ln], b"hello")
        mux.close()

    def test_conn_error_marks_all_links(self):
        mux = CAGMux.open(self.a)
        params = _StubParams("10.0.0.1", 5900)
        link1 = mux.open_link(params)
        self._drain_addlink()
        # close the raw socket from b side → readLoop gets EOF
        self.b.close()
        link1.set_read_deadline(3)
        # Go semantics: on conn error, link.read returns the close error
        # (not EOF). The link must not hang.
        with self.assertRaises((ConnectionError, OSError)):
            link1.read(64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
