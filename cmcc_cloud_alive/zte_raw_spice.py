#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZTE raw SPICE helpers for CAG TCP/TLS product route.

Port of B's internal/spice/raw.go.  This module intentionally keeps the same
wire-layout quirks as the official ZTE tunnel: REDQ link packets, zero ticket
raw auth, 8-byte ZTE data-message prefix, and 5-byte suffix preservation.
"""

from __future__ import annotations

import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

TERMINAL_GUID = "31BF5444-86E0-4D5D-B1AB-A42FFBAC72C9"
TERMINAL_GUID_BYTES = bytes([
    0x44, 0x54, 0xBF, 0x31, 0xE0, 0x86, 0x5D, 0x4D,
    0xB1, 0xAB, 0xA4, 0x2F, 0xFB, 0xAC, 0x72, 0xC9,
])


def _put_u16(buf: bytearray, off: int, v: int) -> None:
    struct.pack_into("<H", buf, off, v & 0xFFFF)


def _put_u32(buf: bytearray, off: int, v: int) -> None:
    struct.pack_into("<I", buf, off, v & 0xFFFFFFFF)


def _u16(b: bytes) -> int:
    return struct.unpack_from("<H", b, 0)[0]


def _u32(b: bytes) -> int:
    return struct.unpack_from("<I", b, 0)[0]


def copy_c_string(dst: memoryview, s: str) -> None:
    data = s.encode("utf-8", "ignore")
    n = min(len(dst), len(data))
    dst[:n] = data[:n]
    if n < len(dst):
        dst[n] = 0


def buildZTERawMainREDQ(key: str, vmid: str, linkUUID: Optional[bytes], traceID: str, spanID: str) -> bytes:
    if not linkUUID or len(linkUUID) != 16:
        linkUUID = os.urandom(16)
    redq = bytearray(729)
    redq[0:4] = b"REDQ"
    _put_u32(redq, 4, 2)
    _put_u32(redq, 8, 2)
    _put_u32(redq, 12, 713)
    redq[20] = 1
    _put_u32(redq, 22, 1)
    _put_u32(redq, 26, 1)
    _put_u32(redq, 30, 705)
    _put_u32(redq, 42, 0x1400)
    _put_u32(redq, 46, 0x10000)
    copy_c_string(memoryview(redq)[50:95], key + vmid)
    redq[95:111] = linkUUID[:16]
    redq[127:143] = TERMINAL_GUID_BYTES
    copy_c_string(memoryview(redq)[159:192], traceID)
    copy_c_string(memoryview(redq)[192:209], spanID)
    _put_u32(redq, 717, 0x800)
    _put_u32(redq, 721, 0x232900)
    return bytes(redq)


def BuildZTERawChannelREDQ(
    key: str,
    vmid: str,
    linkUUID: Optional[bytes],
    traceID: str,
    spanID: str,
    connectionID: int,
    channelType: int,
    channelID: int,
) -> bytes:
    length = 725
    size = 709
    cap_count = 0
    caps = [0x800]
    if channelType == 2:
        length = 733
        size = 717
        cap_count = 2
        caps = [0xA00, 0xFFC30DEC, 0x48]
    elif channelType == 5:
        length = 729
        size = 713
        cap_count = 1
        caps = [0x800, 0x0E]
    elif channelType == 6:
        length = 729
        size = 713
        cap_count = 1
        caps = [0x800, 0x07]
    if not linkUUID or len(linkUUID) != 16:
        linkUUID = os.urandom(16)
    redq = bytearray(length)
    redq[0:4] = b"REDQ"
    _put_u32(redq, 4, 2)
    _put_u32(redq, 8, 2)
    _put_u32(redq, 12, size)
    _put_u32(redq, 16, connectionID)
    redq[20] = channelType & 0xFF
    redq[21] = channelID & 0xFF
    _put_u32(redq, 22, 1)
    _put_u32(redq, 26, cap_count)
    _put_u32(redq, 30, 705)
    _put_u32(redq, 42, 0x1400)
    _put_u32(redq, 46, 0x10000)
    copy_c_string(memoryview(redq)[50:95], key + vmid)
    redq[95:111] = linkUUID[:16]
    copy_c_string(memoryview(redq)[159:192], traceID)
    copy_c_string(memoryview(redq)[192:209], spanID)
    cap_off = length - len(caps) * 4
    for i, cap in enumerate(caps):
        _put_u32(redq, cap_off + i * 4, cap)
    return bytes(redq)


def buildTerminalInfoMessage() -> bytes:
    msg = bytearray(68)
    _put_u16(msg, 0, 0x7C)
    _put_u32(msg, 2, 57)
    msg[11:11 + len(TERMINAL_GUID)] = TERMINAL_GUID.encode("ascii")
    return bytes(msg)


def BuildZTERawDisplayInit() -> bytes:
    return bytes.fromhex("65001300000000000000000100004001000000000100fc5f000000000003")


def BuildZTERawInputInit() -> bytes:
    return bytes.fromhex("67000200000000000000000200")


def rawMessageWithPrefix(serial: int, msg: bytes) -> bytes:
    out = bytearray(8 + len(msg))
    _put_u32(out, 0, serial)
    out[8:] = msg
    return bytes(out)


def _set_timeout(conn, timeout: Optional[float]) -> None:
    if hasattr(conn, "settimeout"):
        conn.settimeout(timeout)


def _read_exact(conn, n: int) -> bytes:
    chunks = []
    got = 0
    while got < n:
        part = conn.recv(n - got)
        if not part:
            raise EOFError("connection closed")
        chunks.append(part)
        got += len(part)
    return b"".join(chunks)


def readRawLinkReply(conn, timeout: float = 8.0) -> bytes:
    _set_timeout(conn, timeout)
    head = _read_exact(conn, 16)
    if head[:4] != b"REDQ":
        raise ValueError(f"invalid REDQ magic: {head[:4].hex()}")
    size = _u32(head[12:16])
    if size > 4096:
        raise ValueError(f"invalid REDQ reply size {size}")
    return head + _read_exact(conn, size)


@dataclass
class RawState:
    LastSerial: int = 0
    LastSuffix: bytes = b""
    NextSerial: int = 0

    def ReadMessage(self, conn, timeout: float = 2.0) -> Tuple[int, bytes]:
        _set_timeout(conn, timeout)
        head = _read_exact(conn, 6)
        has_zte_prefix = False
        msg_type = _u16(head[:2])
        size = _u32(head[2:6])
        if size == 0:
            serial = _u32(head[:4])
            _ = _read_exact(conn, 2)  # prefix tail
            head = _read_exact(conn, 6)
            msg_type = _u16(head[:2])
            size = _u32(head[2:6])
            self.LastSerial = serial
            self.LastSuffix = b""
            has_zte_prefix = True
        if size > (1 << 20):
            raise ValueError(f"raw SPICE message too large: {size}")
        payload = _read_exact(conn, size)
        if has_zte_prefix and hasattr(conn, "TakeReadBufferN"):
            self.LastSuffix = conn.TakeReadBufferN(5) or b""
        return msg_type, payload

    def AutoReply(self, conn, msgType: int, payload: bytes) -> bool:
        if msgType == 0x04:
            pong = bytearray(6 + len(payload))
            _put_u16(pong, 0, 0x03)
            _put_u32(pong, 2, len(payload))
            pong[6:] = payload
            self.WriteMessage(conn, self.LastSerial, bytes(pong))
            return True
        if msgType == 0x03:
            generation = _u32(payload[:4]) if len(payload) >= 4 else 0
            ack = bytearray(10)
            _put_u16(ack, 0, 0x01)
            _put_u32(ack, 2, 4)
            _put_u32(ack, 6, generation)
            self.WriteMessage(conn, self.LastSerial, bytes(ack))
            return True
        if msgType == 0x74:
            reply = bytearray(7)
            _put_u16(reply, 0, 0x79)
            _put_u32(reply, 2, 1)
            self.WriteMessage(conn, self.nextSerial(), bytes(reply))
            return True
        return False

    def WriteMessage(self, conn, serial: int, msg: bytes) -> int:
        data = rawMessageWithPrefix(serial, msg) + (self.LastSuffix or b"")
        return conn.send(data) if hasattr(conn, "send") else conn.write(data)

    def nextSerial(self) -> int:
        if self.NextSerial == 0:
            self.NextSerial = 4
        serial = self.NextSerial
        self.NextSerial += 1
        return serial


_last_state = RawState()


def ReadRawMessage(conn, timeout: float = 2.0) -> Tuple[int, bytes]:
    global _last_state
    return _last_state.ReadMessage(conn, timeout)


def RawAutoReply(conn, msgType: int, payload: bytes) -> bool:
    return _last_state.AutoReply(conn, msgType, payload)


def WriteRawMessage(conn, serial: int, msg: bytes) -> int:
    data = rawMessageWithPrefix(serial, msg)
    return conn.send(data) if hasattr(conn, "send") else conn.write(data)


@dataclass
class RawHandshakeResult:
    SpiceSessionID: int = 0
    OK: bool = False
    error: Optional[str] = None


def RawMainHandshake(conn, key: str, vmid: str, linkUUID: Optional[bytes], traceID: str, spanID: str) -> RawHandshakeResult:
    state = RawState()
    try:
        conn.sendall(buildZTERawMainREDQ(key, vmid, linkUUID, traceID, spanID))
        reply = readRawLinkReply(conn, 8.0)
        pk_off = reply.find(bytes([0x30, 0x81, 0x9F, 0x30, 0x0D]))
        if pk_off < 0:
            pk_off = reply.find(bytes([0x30, 0x81]))
        if pk_off < 0:
            return RawHandshakeResult(error="raw SPICE link reply has no RSA key")
        # B parses the RSA key as a sanity check; product tunnel still sends a
        # 128-byte zero ticket with no auth-type prefix.  Keep the wire behavior.
        conn.sendall(b"\x00" * 128)
        result = _read_exact(conn, 4)
        code = _u32(result)
        if code != 0:
            return RawHandshakeResult(error=f"raw SPICE auth failed: result={code}")

        spice_session_id = 0
        for _ in range(15):
            msg_type, payload = state.ReadMessage(conn, 2.0)
            if msg_type == 0x67 and len(payload) >= 4:
                spice_session_id = _u32(payload[:4])
                if hasattr(conn, "DiscardReadBuffer"):
                    conn.DiscardReadBuffer()
                break
            state.AutoReply(conn, msg_type, payload)
        if spice_session_id == 0:
            return RawHandshakeResult(error="raw SPICE MAIN_INIT not received")

        attach = bytes.fromhex("680000000000")
        attach_sent = False
        for i in range(4):
            try:
                msg_type, payload = state.ReadMessage(conn, 2.0)
            except Exception:
                break
            if msg_type == 0x04:
                if state.LastSerial == 3 or i == 3:
                    state.WriteMessage(conn, state.LastSerial, attach)
                    attach_sent = True
                    break
                continue
            state.AutoReply(conn, msg_type, payload)
        if not attach_sent:
            conn.sendall(rawMessageWithPrefix(3, attach) + b"\x00" * 5)

        client_info = bytes.fromhex("72000800000000000000000100000001000000")
        conn.sendall(rawMessageWithPrefix(1, client_info))
        conn.sendall(rawMessageWithPrefix(2, buildTerminalInfoMessage()))

        init_ok = False
        for _ in range(5):
            try:
                msg_type, payload = state.ReadMessage(conn, 1.0)
            except Exception:
                break
            if msg_type != 0x04:
                state.AutoReply(conn, msg_type, payload)
            if msg_type in (0x68, 0x73):
                init_ok = True
                if msg_type == 0x73:
                    break
        if not init_ok:
            return RawHandshakeResult(SpiceSessionID=spice_session_id, error="raw SPICE init did not reach CHANNELS_LIST/info")
        return RawHandshakeResult(SpiceSessionID=spice_session_id, OK=True)
    except Exception as exc:
        return RawHandshakeResult(error=str(exc))


def keepaliveRawSpiceLoop(conn, interval: float = 25.0, stop_after: Optional[float] = None) -> dict:
    """Read/auto-reply raw messages and periodically send display/input init.

    This is the Python route's conservative product keepalive loop: it preserves
    B's raw auto replies and injects screen/input channel init bytes as outbound
    traffic when idle.  It returns counters for reports/tests.
    """
    state = RawState()
    started = time.time()
    next_tick = started
    counters = {"messages": 0, "autoReplies": 0, "ticks": 0, "errors": 0}
    while stop_after is None or time.time() - started < stop_after:
        try:
            msg_type, payload = state.ReadMessage(conn, min(1.0, max(0.1, interval)))
            counters["messages"] += 1
            if state.AutoReply(conn, msg_type, payload):
                counters["autoReplies"] += 1
        except (socket.timeout, TimeoutError):
            pass
        except Exception:
            counters["errors"] += 1
            break
        now = time.time()
        if now >= next_tick:
            try:
                conn.sendall(rawMessageWithPrefix(state.nextSerial(), BuildZTERawDisplayInit()))
                conn.sendall(rawMessageWithPrefix(state.nextSerial(), BuildZTERawInputInit()))
                counters["ticks"] += 1
            except Exception:
                counters["errors"] += 1
                break
            next_tick = now + interval
    return counters
