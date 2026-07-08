#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure-Python SCG keepalive route.

This module mirrors the SCG branch of ``cloud-computer-keepalive`` in pure Python:
  1. SCG TCP auth packet: AES-128-CTR, same static key/counter as upstream.
  2. TLS upgrade after auth success.
  3. Chuanyun trunk frames carrying SPICE link/auth/display keepalive traffic.

It intentionally is *not* the official HTTP keepalive loop; SCG must stay on the
native TCP/TLS + Chuanyun/SPICE route.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import os
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from . import core
from . import desktop_keepalive
from . import spice_protocol as sp

# Bypass any system proxy (clash TUN / env vars) for all direct HTTP calls.
# Go's http.DefaultTransport does NOT use ProxyFromEnvironment by default when
# the binary is built without cgo/netgo; Python's urllib picks up proxy env
# unconditionally.  Use a dedicated opener to stay proxy-free.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

try:  # cryptography is optional in pyproject but present in the target runtime.
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except Exception:  # pragma: no cover - fallback is validated at runtime.
    Cipher = algorithms = modes = default_backend = None


TRUNK_HELLO = 3
TRUNK_DATA = 4
TRUNK_SWITCH = 5
TRUNK_GBN = 6
FRAME_HEAD_SIZE = 24
DATA_TYPE = 1
CONTROL_TYPE = 2

AUTH_AES_KEY = b"\xfe" * 16
AUTH_CTR_INIT = 0xFEFEFEFEFEFEFEFE
CEM_BASE = "https://api.soho.komect.com:1443"
CEM_CLIENT_ID = "sc-user-5e38ece5"
CEM_BIZ_CODE = "10002"
CEM_RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDRwADvpa+s20CapaSeDeWA"
    "fRKbK5zD91jIUxNDe/2twuvKdQA+Ln3VWFtL8opVod0ebqQanpVb/uITI56G"
    "coVdSzis2IgqIkVvN+iOPH+on/FK+6EXYeIZn3MYmVxsmS0IVifVl2EGLeOC"
    "RMwjPmy9fHB+gByQtGnxAsknwBKUqQIDAQAB"
)

# Chuanyun field2 channel IDs used by B/cloud-computer-keepalive SCG SPICE route.
CHANNEL_CTRL = 0
CHANNEL_MAIN = 1
CHANNEL_DISPLAY = 2
CHANNEL_INPUTS = 3
CHANNEL_CURSOR = 4
CHANNEL_PLAYBACK = 5
CHANNEL_RECORD = 6
CHANNEL_NAMES = {
    CHANNEL_CTRL: "ctrl",
    CHANNEL_MAIN: "main",
    CHANNEL_DISPLAY: "display",
    CHANNEL_INPUTS: "inputs",
    CHANNEL_CURSOR: "cursor",
    CHANNEL_PLAYBACK: "playback",
    CHANNEL_RECORD: "record",
}


@dataclasses.dataclass
class SCGKeepaliveResult:
    """Outcome compatible with the old subprocess result object."""

    returncode: int
    stdout: str
    stderr: str
    command: List[str] = dataclasses.field(default_factory=list)
    config_path: Optional[str] = None
    stats: Dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0



def _cem_rsa_encrypt(text: str) -> str:
    encrypted = core.rsa_pkcs1_v15_encrypt_b64(str(text), CEM_RSA_PUBLIC_KEY_B64)
    return "{rsa}" + encrypted


def exchange_cem_access_token(sc_auth_code: str, timeout: float = 30.0) -> str:
    if not sc_auth_code:
        raise ValueError("empty scAuthCode in firm auth response")
    form = urllib.parse.urlencode({
        "bizCode": CEM_BIZ_CODE,
        "client_id": CEM_CLIENT_ID,
        "grant_type": "ext",
        "source": "biz",
        "token": sc_auth_code,
    }).encode("utf-8")
    req = urllib.request.Request(CEM_BASE + "/gzs/auth/oauth/token", data=form, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as res:
        raw = res.read().decode("utf-8", "replace")
    result = json.loads(raw)
    if result.get("code") != "00000":
        raise RuntimeError("oauth/token failed: code=%s msg=%s" % (result.get("code"), result.get("msg")))
    token = ((result.get("data") or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("oauth/token returned empty access_token")
    return token


def cem_request(path: str, body: Dict[str, str], access_token: str, device_id: str = "", timeout: float = 30.0) -> Dict[str, object]:
    payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(CEM_BASE + path, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + access_token)
    req.add_header("gzs-client-id", CEM_CLIENT_ID)
    req.add_header("gzs-timestamp", str(int(time.time() * 1000)))
    req.add_header("sc-terminal-sn", device_id or "")
    req.add_header("sc-network-type", "2")
    req.add_header("sc-unit-type", "MacBookPro")
    req.add_header("User-Agent", "cdpsdk-macos-2.18.21(2.18.21.159)")
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as res:
        raw = res.read().decode("utf-8", "replace")
    return json.loads(raw)


def wait_vm_ready(access_token: str, vm_id: str, trace_id: str, device_id: str = "", timeout: float = 30.0,
                  attempts: int = 20, interval: float = 3.0) -> Dict[str, object]:
    """Poll CEM VM ready status after getConnectInfo triggers SCG VM boot.

    This mirrors the Go implementation: getConnectInfo can return readyStatus!=1
    with a traceId; then getVmReadyStatus is polled up to 20 times. The ready
    response may contain a refreshed scAuthCode, which must be preferred for the
    subsequent SCG auth packet.
    """
    if not trace_id:
        raise RuntimeError("empty traceId; cannot wait VM ready")
    vm_encrypted = _cem_rsa_encrypt(vm_id)
    total = max(1, int(attempts))
    last_error = "not ready"
    for attempt in range(total):
        result = cem_request(
            "/sc/open-portal/openapi/terminal/v1/getVmReadyStatus",
            {"vmId": vm_encrypted, "traceId": trace_id},
            access_token,
            device_id=device_id,
            timeout=timeout,
        )
        if result.get("code") == "00000" or result.get("returnCode") == "00000":
            data = result.get("data") or {}
            if data.get("readyStatus") == 1 or str(data.get("readyStatus")) == "1":
                return {
                    "readyStatus": 1,
                    "scAuthCode": data.get("scAuthCode") or "",
                }
            last_error = "readyStatus=%r" % (data.get("readyStatus"),)
        else:
            last_error = "code=%s msg=%s" % (
                result.get("code") or result.get("returnCode"),
                result.get("msg") or result.get("returnMsg"),
            )
        if attempt + 1 < total:
            time.sleep(max(0.0, float(interval)))
    raise RuntimeError("VM ready timeout after getConnectInfo trigger: %s" % last_error)


def get_connect_info(sc_auth_code: str, vm_id: str, device_id: str = "", timeout: float = 30.0) -> Dict[str, object]:
    access_token = exchange_cem_access_token(sc_auth_code, timeout=timeout)
    result = cem_request(
        "/sc/open-portal/openapi/terminal/v1/getConnectInfo",
        {"vmId": _cem_rsa_encrypt(vm_id)}, access_token, device_id=device_id, timeout=timeout)
    if result.get("code") != "00000" and result.get("returnCode") != "00000":
        raise RuntimeError("getConnectInfo failed: code=%s msg=%s" % (result.get("code") or result.get("returnCode"), result.get("msg") or result.get("returnMsg")))
    data = result.get("data") or {}
    scg_ip = data.get("scgIp") or data.get("scgIP") or ""
    scg_port = data.get("scgTcpPort") or data.get("scgPort") or "10800"
    if not scg_ip:
        raise RuntimeError("getConnectInfo returned empty scgIp")
    connect_info = {
        "scgIp": scg_ip,
        "scgPort": str(scg_port),
        "scAuthCode": data.get("scAuthCode") or sc_auth_code,
        "traceId": data.get("traceId") or "",
        "readyStatus": data.get("readyStatus"),
    }
    # Go keepalive.go: getConnectInfo triggers SCG VM boot. If it is not ready
    # yet and traceId is present, poll getVmReadyStatus and prefer its scAuthCode.
    if connect_info.get("readyStatus") != 1 and str(connect_info.get("readyStatus")) != "1" and connect_info.get("traceId"):
        ready_info = wait_vm_ready(
            access_token,
            vm_id,
            str(connect_info["traceId"]),
            device_id=device_id,
            timeout=timeout,
        )
        if ready_info.get("scAuthCode"):
            connect_info["scAuthCode"] = ready_info["scAuthCode"]
        connect_info["readyStatus"] = ready_info.get("readyStatus", 1)
    return connect_info


@dataclasses.dataclass
class Frame:
    pkt_type: int
    payload: bytes
    field1: int
    field2: int


def _aes_ctr_encrypt_go_compatible(plaintext: bytes) -> bytes:
    """Match Go crypto.AESCTREncrypt: custom block counter, little endian."""
    if Cipher is None:
        raise RuntimeError("cryptography is required for SCG AES-CTR auth")
    # Go implementation encrypts counter blocks manually:
    # counter[0:8]=LE(init+i), counter[8:16]=LE(init), then XOR keystream.
    cipher = Cipher(algorithms.AES(AUTH_AES_KEY), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    stream = bytearray()
    blocks = (len(plaintext) + 15) // 16
    for i in range(blocks):
        counter = struct.pack("<QQ", AUTH_CTR_INIT + i, AUTH_CTR_INIT)
        stream.extend(encryptor.update(counter))
    encryptor.finalize()
    return bytes(a ^ b for a, b in zip(plaintext, stream))


def build_auth_packet(sc_auth_code: str, vm_id: str) -> bytes:
    tlv_value = (sc_auth_code + "|" + vm_id).encode("utf-8")
    if len(tlv_value) > 0xFFFF:
        raise ValueError("SCG auth TLV too long")
    plaintext = b"\x00\x02" + struct.pack(">Q", int(time.time())) + b"\x03" + struct.pack(">H", len(tlv_value)) + tlv_value
    encrypted = _aes_ctr_encrypt_go_compatible(plaintext)
    return b"\x01" + bytes([len(encrypted) & 0xFF]) + encrypted


def frame_head_pack(pkt_type: int, payload_len: int, field1: int, field2: int) -> bytes:
    if payload_len > 0xFFFF:
        raise ValueError("Chuanyun payload too large for 16-bit frame length")
    # Go chuanyun.FrameHeadPack: version, pktType, payloadLen, reserved, field1, field2.
    return struct.pack(
        "<BBHIQQ",
        1,
        pkt_type & 0xFF,
        payload_len,
        0,
        field1 & 0xFFFFFFFFFFFFFFFF,
        field2 & 0xFFFFFFFFFFFFFFFF,
    )


def _recv_exact(sock: socket.socket, n: int, timeout: float) -> bytes:
    old = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        data = bytearray()
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError("connection closed")
            data.extend(chunk)
        return bytes(data)
    finally:
        sock.settimeout(old)


def recv_trunk_frame(sock: socket.socket, timeout: float = 3.0) -> Frame:
    head = _recv_exact(sock, FRAME_HEAD_SIZE, timeout)
    version, pkt_type, payload_len, _reserved, field1, field2 = struct.unpack("<BBHIQQ", head)
    if version != 1:
        raise ValueError("unexpected Chuanyun version %r" % version)
    payload = _recv_exact(sock, payload_len, timeout) if payload_len else b""
    return Frame(pkt_type=pkt_type, payload=payload, field1=field1, field2=field2)


def recv_all_frames(sock: socket.socket, timeout: float, max_frames: int) -> List[Frame]:
    frames: List[Frame] = []
    for _ in range(max_frames):
        try:
            frames.append(recv_trunk_frame(sock, timeout))
        except Exception:
            break
    return frames


def trunk_switch_pack(
    target_cid: int,
    sender_cid: int,
    param: int,
    switch_reason: int,
    extra_id: int,
    field1: int,
    field2: int,
) -> bytes:
    """Match Go chuanyun.TrunkSwitchPack byte-for-byte.

    Go builds a Chuanyun TrunkSwitch frame with a 24-byte frame header and
    a 32-byte payload:
      targetCID:u64, senderCID:u64, param:u32, switchReason:u8,
      3 bytes padding, extraID:u64.
    """
    if switch_reason > 6:
        switch_reason = 6
    payload = struct.pack(
        "<QQIB3xQ",
        target_cid & 0xFFFFFFFFFFFFFFFF,
        sender_cid & 0xFFFFFFFFFFFFFFFF,
        param & 0xFFFFFFFF,
        switch_reason & 0xFF,
        extra_id & 0xFFFFFFFFFFFFFFFF,
    )
    return frame_head_pack(TRUNK_SWITCH, len(payload), field1, field2) + payload


def connect_scg(scg_ip: str, scg_port: str, sc_auth_code: str, vm_id: str, timeout: float = 10.0) -> Tuple[ssl.SSLSocket, int]:
    if not scg_ip or not scg_port:
        raise ValueError("SCG endpoint missing: scg_ip/scg_port are required")
    raw = socket.create_connection((scg_ip, int(scg_port)), timeout=timeout)
    try:
        raw.sendall(build_auth_packet(sc_auth_code, vm_id))
        # Match Go ConnectSCG: one Read into a 128-byte buffer, then parse bytes 6..8.
        old_timeout = raw.gettimeout()
        raw.settimeout(10.0)
        try:
            response = raw.recv(128)
        finally:
            raw.settimeout(old_timeout)
        if not response or response[0] != 0x00:
            if response and response[0] == 0x0B:
                raise RuntimeError("auth downgrade (token expired or replay)")
            raise RuntimeError("auth failed: byte[0]=0x%02x" % (response[0] if response else -1))
        if len(response) < 9:
            raise RuntimeError("auth response too short: %d bytes" % len(response))
        session_id = (response[6] << 16) | (response[7] << 8) | response[8]
        ctx = ssl.create_default_context()
        # Go uses InsecureSkipVerify=true and no ServerName for this private SCG endpoint.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls_sock = ctx.wrap_socket(raw)
        return tls_sock, session_id
    except Exception:
        raw.close()
        raise


def build_channel_auth(sid: int, channel_id: int, channel_type: int, connection_id: int = 0) -> bytes:
    """Build ExtInfo + SPICE REDQ token frame like Go buildChannelREDQ."""
    ext_info = bytearray(bytes.fromhex("010013f300080000000000010820f1000101f2000104"))
    ext_info[-1] = channel_type & 0xFF

    redq = bytearray()
    redq.extend(b"REDQ")
    redq.extend(struct.pack("<I", 2))
    redq.extend(struct.pack("<I", 2))
    if channel_type in (1, 2, 5, 6):
        redq.extend(struct.pack("<I", 26))
        redq.extend(struct.pack("<I", connection_id & 0xFFFFFFFF))
        redq.extend(bytes([channel_type & 0xFF, 0]))
        redq.extend(struct.pack("<I", 1))
        redq.extend(struct.pack("<I", 1))
        redq.extend(struct.pack("<I", 18))
        redq.extend(struct.pack("<I", 0x00000009))
        redq.extend(struct.pack("<I", 0x0000000F))
    else:
        redq.extend(struct.pack("<I", 22))
        redq.extend(struct.pack("<I", connection_id & 0xFFFFFFFF))
        redq.extend(bytes([channel_type & 0xFF, 0]))
        redq.extend(struct.pack("<I", 1))
        redq.extend(struct.pack("<I", 0))
        redq.extend(struct.pack("<I", 14))
        redq.extend(struct.pack("<I", 0x00000009))

    token_redq = os.urandom(16) + bytes(redq)
    return (
        frame_head_pack(DATA_TYPE, len(ext_info), sid, channel_id)
        + bytes(ext_info)
        + frame_head_pack(DATA_TYPE, len(token_redq), sid, channel_id)
        + token_redq
    )


def _find_reply_pubkey(payload: bytes) -> Optional[bytes]:
    # B extracts ASN.1 SubjectPublicKeyInfo from SPICE LinkReply. Prefer generic DER marker.
    marker = b"\x30\x81\x9f\x30\x0d"
    off = payload.find(marker)
    if off >= 0 and len(payload) >= off + sp.SPICE_TICKET_PUBKEY_BYTES:
        return payload[off:off + sp.SPICE_TICKET_PUBKEY_BYTES]
    # Fallback: decode a full SPICE link reply after REDQ header if present.
    redq = payload.find(b"REDQ")
    if redq >= 0 and len(payload) >= redq + sp.SPICE_LINK_HEADER_SIZE:
        try:
            hdr = sp.decode_spice_link_header(payload[redq:redq + sp.SPICE_LINK_HEADER_SIZE])
            body_off = redq + sp.SPICE_LINK_HEADER_SIZE
            body = payload[body_off:body_off + hdr["size"]]
            m = marker + body.split(marker, 1)[1] if marker in body else b""
            if m and len(m) >= sp.SPICE_TICKET_PUBKEY_BYTES:
                return m[:sp.SPICE_TICKET_PUBKEY_BYTES]
        except Exception:
            pass
    return None


def _send_ticket(sock: socket.socket, sid: int, channel_id: int, payload: bytes, password: bytes = b"") -> bool:
    pub = _find_reply_pubkey(payload)
    if not pub:
        return False
    auth_type = struct.pack("<I", 1)
    ticket = sp.encode_spice_ticket(pub, password)
    sock.sendall(
        frame_head_pack(DATA_TYPE, len(auth_type), sid, channel_id)
        + auth_type
        + frame_head_pack(DATA_TYPE, len(ticket), sid, channel_id)
        + ticket
    )
    return True


def _handle_display_payload(sock: socket.socket, sid: int, channel_id: int, payload: bytes, progress: Dict[str, bool], stats: Dict[str, int]) -> None:
    """Respond to common SPICE display mini/data messages."""
    offset = 0
    # Strip possible token prefix used by Chuanyun framing.
    if len(payload) >= 6 and payload[0:2] == b"\x00\x01":
        declared = struct.unpack_from("<I", payload, 2)[0]
        if 6 + declared <= len(payload):
            offset = 6
    data = payload[offset:]
    responses: List[bytes] = []
    try:
        # Server messages are usually DATA headers after auth.
        while data:
            if len(data) >= sp.DATA_HEADER_SIZE:
                msg = sp.decode_data_message(data)
                raw_len = sp.DATA_HEADER_SIZE + msg["header"]["size"]
                mtype = msg["header"]["type"]
                body = msg["payload"]
                if mtype == sp.SpiceMessage.SET_ACK:
                    try:
                        gen = sp.decode_set_ack_payload(body)["generation"]
                    except Exception:
                        gen = 1
                    responses.append(sp.encode_ack_sync(gen))
                    progress["setAckReceived"] = True
                    progress["ackSyncSent"] = True
                elif mtype == sp.SpiceMessage.PING:
                    responses.append(sp.encode_pong(body))
                    progress["pingReceived"] = True
                    progress["pongSent"] = True
                elif mtype == sp.SpiceMessage.SURFACE_CREATE:
                    progress["surfaceCreateReceived"] = True
                elif mtype == sp.SpiceMessage.DRAW_COPY:
                    progress["drawCopyReceived"] = True
                elif mtype == sp.SpiceMessage.MARK:
                    progress["markReceived"] = True
                data = data[raw_len:]
                continue
            break
    except Exception:
        # The server can coalesce opaque bytes; keep the connection alive even if decode fails.
        return
    for resp in responses:
        sock.sendall(frame_head_pack(DATA_TYPE, len(resp), sid, channel_id) + resp)
        stats["responses"] = stats.get("responses", 0) + 1


def spice_handshake(sock: socket.socket, max_wait: float = 12.0) -> Dict[str, object]:
    first = recv_trunk_frame(sock, 3.0)
    sid = first.field1
    spice_session_id = first.field2 & 0xFFFFFFFF

    connected: List[int] = []
    progress = sp.create_protocol_progress()
    stats: Dict[str, int] = {"frames": 1, "responses": 0}

    def recv_frame_ar(timeout: float = 2.0) -> Optional[Frame]:
        frame = recv_trunk_frame(sock, timeout)
        stats["frames"] += 1
        _reply_keepalive_frame(sock, sid, frame, stats)
        return frame

    def authenticate_channel(channel_id: int, channel_type: int, wait_seconds: float, connection_id: int = 0) -> bool:
        sock.sendall(build_channel_auth(sid, channel_id, channel_type, connection_id))
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                frame = recv_frame_ar(2.0)
            except Exception:
                break
            if frame.pkt_type == CONTROL_TYPE:
                continue
            if frame.field2 != channel_id:
                continue
            if b"REDQ" not in frame.payload:
                continue
            if not _send_ticket(sock, sid, channel_id, frame.payload):
                return False
            for _ in range(10):
                if time.monotonic() >= deadline:
                    break
                try:
                    auth_frame = recv_frame_ar(2.0)
                except Exception:
                    break
                if auth_frame.pkt_type == CONTROL_TYPE:
                    continue
                if auth_frame.field2 != channel_id:
                    continue
                if len(auth_frame.payload) != 4:
                    continue
                return struct.unpack("<I", auth_frame.payload)[0] == 0
            return False
        return False

    def wait_main_init(wait_seconds: float = 30.0) -> int:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                frame = recv_frame_ar(2.0)
            except Exception:
                continue
            if frame.pkt_type == CONTROL_TYPE or frame.field2 != CHANNEL_MAIN or len(frame.payload) < 10:
                continue
            msg_type = struct.unpack_from("<H", frame.payload, 0)[0]
            msg_size = struct.unpack_from("<I", frame.payload, 2)[0]
            if msg_type == sp.SpiceMessage.MAIN_INIT and msg_size >= 4:
                return struct.unpack_from("<I", frame.payload, 6)[0]
        return 0

    def send_client_info_and_attach() -> None:
        # Matches Go: hex 7200140000001000000064000000080000002008010000000000 + 680000000000
        client_info = bytes.fromhex("7200140000001000000064000000080000002008010000000000")
        attach_channels = bytes.fromhex("680000000000")
        sock.sendall(
            frame_head_pack(DATA_TYPE, len(client_info), sid, CHANNEL_MAIN)
            + client_info
            + frame_head_pack(DATA_TYPE, len(attach_channels), sid, CHANNEL_MAIN)
            + attach_channels
        )
        stats["responses"] = stats.get("responses", 0) + 2

    def wait_channels_list(wait_seconds: float = 20.0) -> None:
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                frame = recv_frame_ar(2.0)
            except Exception:
                break
            if frame.pkt_type == CONTROL_TYPE:
                continue
            if frame.field2 == CHANNEL_MAIN and len(frame.payload) >= 6:
                msg_type = struct.unpack_from("<H", frame.payload, 0)[0]
                if msg_type == sp.SpiceMessage.CHANNELS_LIST:
                    break

    def wait_display_mark(wait_seconds: float = 40.0) -> None:
        deadline = time.monotonic() + wait_seconds
        for _ in range(20):
            if time.monotonic() >= deadline:
                break
            try:
                frame = recv_frame_ar(2.0)
            except Exception:
                break
            if frame.pkt_type == CONTROL_TYPE:
                continue
            if frame.field2 == CHANNEL_DISPLAY and frame.payload:
                _handle_display_payload(sock, sid, CHANNEL_DISPLAY, frame.payload, progress, stats)
            if frame.field2 != CHANNEL_DISPLAY or len(frame.payload) < 6:
                continue
            msg_type = struct.unpack_from("<H", frame.payload, 0)[0]
            if msg_type == sp.SpiceMessage.MARK:
                break

    if authenticate_channel(CHANNEL_MAIN, sp.SpiceChannel.MAIN, 40.0):
        connected.append(CHANNEL_MAIN)
        main_init_session_id = wait_main_init()
        if main_init_session_id:
            spice_session_id = main_init_session_id
            send_client_info_and_attach()
            wait_channels_list()
            if authenticate_channel(CHANNEL_DISPLAY, sp.SpiceChannel.DISPLAY, 120.0, spice_session_id):
                connected.append(CHANNEL_DISPLAY)
                init = sp.encode_display_init()
                sock.sendall(frame_head_pack(DATA_TYPE, len(init), sid, CHANNEL_DISPLAY) + init)
                progress["displayInitSent"] = True
                wait_display_mark()

    end = time.monotonic() + max_wait
    while time.monotonic() < end:
        try:
            frame = recv_frame_ar(1.0)
        except Exception:
            continue
        if frame.field2 == CHANNEL_DISPLAY and frame.payload:
            _handle_display_payload(sock, sid, CHANNEL_DISPLAY, frame.payload, progress, stats)
        if sp.is_protocol_keepalive_success(progress):
            break

    return {
        "session_id": sid,
        "spice_session_id": spice_session_id,
        "connected_channels": [CHANNEL_NAMES.get(c, str(c)) for c in connected],
        "progress": progress,
        "spice_ok": sp.is_protocol_keepalive_success(progress) or CHANNEL_DISPLAY in connected,
        "stats": stats,
    }


def _round_seconds(duration: Optional[int]) -> int:
    """Normalize SCG keepalive duration. 0 mirrors Go persistent mode."""
    if duration is None:
        return 60
    try:
        value = int(duration)
    except Exception:
        return 60
    return value if value > 0 else 0


def _send_mouse_mode(sock: socket.socket, sid: int) -> None:
    # Go keepaliveLoop sends Spice MOUSE_MODE_REQUEST on main channel each heartbeat.
    payload = struct.pack("<HII", 0x69, 4, 2)
    sock.sendall(frame_head_pack(DATA_TYPE, len(payload), sid, CHANNEL_MAIN) + payload)


def _reply_keepalive_frame(sock: socket.socket, sid: int, frame: Frame, stats: Dict[str, int]) -> None:
    if frame.pkt_type != DATA_TYPE or len(frame.payload) < 6:
        return
    msg_type = struct.unpack_from("<H", frame.payload, 0)[0]
    if msg_type == 0x04:  # PING -> PONG
        ping_data = frame.payload[6:]
        pong = struct.pack("<HI", 0x03, len(ping_data)) + ping_data
        sock.sendall(frame_head_pack(DATA_TYPE, len(pong), sid, frame.field2) + pong)
        stats["responses"] = stats.get("responses", 0) + 1
    elif msg_type == 0x03:  # SET_ACK -> ACK_SYNC
        generation = struct.unpack_from("<I", frame.payload, 6)[0] if len(frame.payload) >= 10 else 0
        ack_sync = struct.pack("<HII", 0x01, 4, generation)
        sock.sendall(frame_head_pack(DATA_TYPE, len(ack_sync), sid, frame.field2) + ack_sync)
        stats["responses"] = stats.get("responses", 0) + 1


def _run_once(
    scg_ip: str,
    scg_port: str,
    sc_auth_code: str,
    vm_id: str,
    duration: Optional[int] = 60,
    user_service_id: str = "",
    state_path: Optional[str] = None,
) -> Dict[str, object]:
    duration_seconds = _round_seconds(duration)
    sock, auth_session_id = connect_scg(scg_ip, scg_port, sc_auth_code, vm_id)
    started = time.monotonic()
    heartbeat_count = 0
    soho_heartbeat_count = 0
    try:
        result = spice_handshake(sock)
        sid = int(result.get("session_id") or 0)
        stats = result.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            result["stats"] = stats
        result["auth_session_id"] = auth_session_id
        result["heartbeats"] = 0
        result["sohoHeartbeats"] = 0

        while True:
            if duration_seconds > 0 and time.monotonic() - started >= duration_seconds:
                break
            heartbeat_count += 1
            if user_service_id:
                try:
                    heartbeat_response = desktop_keepalive.heartbeat(user_service_id, state_path)
                    stats["soho_heartbeat_code"] = int(heartbeat_response.get("code") or 0)
                    soho_heartbeat_count += 1
                    stats.pop("soho_heartbeat_error", None)
                except Exception as exc:  # noqa: BLE001
                    stats["soho_heartbeat_error"] = "%s: %s" % (type(exc).__name__, exc)
            if result.get("spice_ok") and sid:
                _send_mouse_mode(sock, sid)
                stats["mouse_mode_requests"] = stats.get("mouse_mode_requests", 0) + 1

            frames = recv_all_frames(sock, 1.0, 10)
            stats["frames"] = stats.get("frames", 0) + len(frames)
            for frame in frames:
                if frame.pkt_type == TRUNK_SWITCH:
                    if len(frame.payload) >= 32:
                        _target_cid, sender_cid, param, switch_reason, extra_id = struct.unpack(
                            "<QQIB3xQ", frame.payload[:32]
                        )
                        sock.sendall(
                            trunk_switch_pack(
                                sender_cid,
                                sid,
                                param,
                                switch_reason,
                                extra_id,
                                frame.field1,
                                frame.field2,
                            )
                        )
                        stats["trunk_switch_replies"] = stats.get("trunk_switch_replies", 0) + 1
                    continue
                _reply_keepalive_frame(sock, sid, frame, stats)

            if not frames:
                old_timeout = sock.gettimeout()
                sock.settimeout(0.5)
                try:
                    probe = sock.recv(1)
                    if probe == b"":
                        raise EOFError("SCG connection lost")
                except socket.timeout:
                    pass
                finally:
                    sock.settimeout(old_timeout)

            if duration_seconds > 0:
                remaining = duration_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                time.sleep(min(25.0, remaining))
            else:
                time.sleep(25.0)

        result["heartbeats"] = heartbeat_count
        result["sohoHeartbeats"] = soho_heartbeat_count
        result["duration_seconds"] = int(time.monotonic() - started)
        return result
    finally:
        try:
            sock.close()
        except Exception:
            pass


def run_scg_keepalive(
    scg_ip: str,
    scg_port: str,
    sc_auth_code: str,
    vm_id: str,
    duration: Optional[int] = 60,
    forever: bool = False,
    user_service_id: str = "",
    state_path: Optional[str] = None,
) -> SCGKeepaliveResult:
    """Run SCG native keepalive in-process using the pure-Python SCG route."""
    rounds = 0
    last: Dict[str, object] = {}
    stdout_lines: List[str] = []
    try:
        while True:
            rounds += 1
            last = _run_once(
                scg_ip,
                scg_port,
                sc_auth_code,
                vm_id,
                duration=duration,
                user_service_id=user_service_id,
                state_path=state_path,
            )
            stdout_lines.append(
                "SCG round %d ok=%s channels=%s heartbeats=%s sohoHeartbeats=%s progress=%s" % (
                    rounds,
                    last.get("spice_ok"),
                    ",".join(last.get("connected_channels", [])),
                    last.get("heartbeats"),
                    last.get("sohoHeartbeats"),
                    last.get("progress"),
                )
            )
            if not forever:
                break
    except Exception as exc:  # noqa: BLE001
        stderr = "%s: %s" % (type(exc).__name__, exc)
        return SCGKeepaliveResult(1, "\n".join(stdout_lines), stderr, stats={"rounds": rounds, "last": last})
    return SCGKeepaliveResult(0, "\n".join(stdout_lines), "", stats={"rounds": rounds, "last": last})
