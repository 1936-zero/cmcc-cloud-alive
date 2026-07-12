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
import select
import ssl
import struct
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from . import core
from . import desktop_keepalive
from . import kpi_hooks
from cmcc_cloud_alive import auth_taxonomy as auth_tax
from . import spice_protocol as sp

# Forever soft-recover: platform maintenance / VM mass-off / CEM blips must not
# kill the long-running product loop (aligned with ZTE interactive backoff).
_FOREVER_BACKOFF_CAP_S = 60.0
_FOREVER_BACKOFF_BASE_S = 5.0

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

# I-PHASE-I-HOLD dual-plane timing (master ACCEPT D1 §5.1 / G3):
#   fast plane: ~1s select/drain cadence (frame reply / trunk_switch)
#   slow plane: SOHO + mouse_mode every ~25s (not every select tick)
# No synthetic 174B writer; observe native 174 payloads only.
HOLD_SELECT_SECONDS = 1.0
HOLD_KEEPALIVE_INTERVAL = 25.0


def _hold_should_run_slow_plane(
    now: float,
    last: Optional[float],
    interval: float = HOLD_KEEPALIVE_INTERVAL,
) -> bool:
    """True when slow plane (SOHO/mouse) should fire.

    last is None means never run yet → fire immediately on first tick.
    Do NOT use 0.0 as sentinel (monotonic can be ~0 at process start).
    """
    if last is None:
        return True
    return (now - last) >= interval


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


def _frame_dump_path() -> str:
    """Full TX/RX hex dump path; empty disables. No f2 filter."""
    return os.environ.get("SCG_AUTH_FRAME_DUMP", "") or ""


def _frame_dump_write(line: str) -> None:
    path = _frame_dump_path()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as df:
            df.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def _frame_dump_raw(direction: str, data: bytes, note: str = "") -> None:
    """Append one raw TX/RX line: dir len note hex (full, no filter)."""
    if not _frame_dump_path():
        return
    note_s = (" " + note) if note else ""
    _frame_dump_write(
        "%s t=%.3f len=%d%s hex=%s"
        % (direction, time.monotonic(), len(data or b""), note_s, (data or b"").hex())
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
        # Dump opaque head even when version mismatches (full raw, no filter).
        _frame_dump_raw(
            "RX-HEAD-BAD",
            head,
            "ver=%s" % version,
        )
        raise ValueError("unexpected Chuanyun version %r" % version)
    payload = _recv_exact(sock, payload_len, timeout) if payload_len else b""
    full = head + payload
    _frame_dump_raw(
        "RX",
        full,
        "pkt=%s f1=%s f2=%s plen=%s" % (pkt_type, field1, field2, payload_len),
    )
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
        auth_pkt = build_auth_packet(sc_auth_code, vm_id)
        _frame_dump_write(
            "meta connect_scg scg=%s:%s vm_id=%s auth_code_len=%d"
            % (scg_ip, scg_port, vm_id, len(sc_auth_code or ""))
        )
        _frame_dump_raw("TX-AUTH", auth_pkt, "tcp_auth")
        raw.sendall(auth_pkt)
        # Match Go ConnectSCG: one Read into a 128-byte buffer, then parse bytes 6..8.
        old_timeout = raw.gettimeout()
        raw.settimeout(10.0)
        try:
            response = raw.recv(128)
        finally:
            raw.settimeout(old_timeout)
        _frame_dump_raw("RX-AUTH", response or b"", "tcp_auth_resp")
        if not response or response[0] != 0x00:
            if response and response[0] == 0x0B:
                raise RuntimeError("auth downgrade (token expired or replay)")
            raise RuntimeError("auth failed: byte[0]=0x%02x" % (response[0] if response else -1))
        if len(response) < 9:
            raise RuntimeError("auth response too short: %d bytes" % len(response))
        session_id = (response[6] << 16) | (response[7] << 8) | response[8]
        _frame_dump_write("meta auth_session_id=%s" % session_id)
        ctx = ssl.create_default_context()
        # Go uses InsecureSkipVerify=true and no ServerName for this private SCG endpoint.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        tls_sock = ctx.wrap_socket(raw)
        return tls_sock, session_id
    except Exception:
        raw.close()
        raise


def build_channel_auth(
    sid: int,
    channel_id: int,
    channel_type: int,
    connection_id: int = 0,
    vm_id: int = 0,
    *,
    vmid_endian: str = "be",
) -> bytes:
    """Build ExtInfo + SPICE REDQ token frame (SCG/Chuanyun channel-auth).

    T49: LIVE-E dump proved ExtInfo[10:14] BE u32 ``0x00010820`` (==67616) is
    what the server echoes as ``vmid`` on get_redirect miss. Product pin never
    appeared on channel-auth wire. When ``vm_id`` > 0, overwrite that 4-byte
    slot so redirect lookup can key on the real VM id.

    ``vmid_endian``: ``be`` (default, matches observed server read of magic) or
    ``le`` (alternate hypothesis if LIVE still fails).
    """
    # Template last byte is channel_type placeholder (overwritten below).
    ext_info = bytearray(bytes.fromhex("010013f300080000000000010820f1000101f2000104"))
    ext_info[-1] = channel_type & 0xFF
    if vm_id and int(vm_id) > 0:
        # ExtInfo[10:14]: was hard-coded BE 0x00010820 (server vmid=67616).
        end = (vmid_endian or "be").strip().lower()
        if end == "le":
            ext_info[10:14] = struct.pack("<I", int(vm_id) & 0xFFFFFFFF)
        else:
            ext_info[10:14] = struct.pack(">I", int(vm_id) & 0xFFFFFFFF)

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
        kpi_hooks.maybe("note_ticket", channel_id, False)
        return False
    auth_type = struct.pack("<I", 1)
    ticket = sp.encode_spice_ticket(pub, password)
    blob = (
        frame_head_pack(DATA_TYPE, len(auth_type), sid, channel_id)
        + auth_type
        + frame_head_pack(DATA_TYPE, len(ticket), sid, channel_id)
        + ticket
    )
    _frame_dump_raw(
        "TX",
        blob,
        "ticket_ch=%s sid=%s ticket_len=%s" % (channel_id, sid, len(ticket)),
    )
    sock.sendall(blob)
    # Ticket bytes sent (auth result still decided by 4-byte reply).
    kpi_hooks.maybe("note_ticket", channel_id, True)
    return True


def _strip_spice_token_prefix(payload: bytes) -> bytes:
    """Strip optional 6-byte token prefix (u16 magic + u32 declared).

    Some Chuanyun/SCG wrappers prefix spice mini/data with ``\\x00\\x01`` +
    declared length. Do **not** treat ``\\x01\\x01`` as a token — that pattern is
    the Chuanyun frame head (version=1, type=1) and must stay on the outer
    layer only (T50 dump: display spice bodies start at mini type, no token).
    Only strip when the declared size fits the remaining buffer.
    """
    if len(payload) < 6:
        return payload
    if payload[0:2] != b"\x00\x01":
        return payload
    declared = struct.unpack_from("<I", payload, 2)[0]
    if 6 + declared <= len(payload):
        return payload[6:]
    return payload


def _apply_display_spice_type(
    mtype: int,
    body: bytes,
    responses: List[bytes],
    progress: Dict[str, bool],
) -> None:
    """Update display progress + queue ACK_SYNC/PONG for known spice types."""
    if mtype == sp.SpiceMessage.SET_ACK:
        try:
            gen = sp.decode_set_ack_payload(body)["generation"]
        except Exception:
            # Mini SET_ACK sometimes carries a short body; fall back generation=1.
            if len(body) >= 4:
                gen = struct.unpack_from("<I", body, 0)[0]
            else:
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


def _handle_display_payload(sock: socket.socket, sid: int, channel_id: int, payload: bytes, progress: Dict[str, bool], stats: Dict[str, int]) -> None:
    """Respond to common SPICE display mini/data messages.

    Live SCG (T50 dump ground truth) delivers display traffic as SPICE *mini*
    headers (type u16 + size u32) after the Chuanyun frame head. Offline proof
    fixtures still use DATA headers. Try mini first, then DATA; never raise.
    """
    data = _strip_spice_token_prefix(payload)
    responses: List[bytes] = []
    try:
        while data:
            consumed = 0
            # Prefer mini: live SET_ACK/MARK/SURFACE are mini-framed.
            if len(data) >= sp.MINI_HEADER_SIZE:
                try:
                    mini = sp.decode_mini_message(data)
                    mtype = mini["header"]["type"]
                    # Mini types used on display are small enums / known spice msgs.
                    # Avoid treating random DATA-serial low words as mini by
                    # requiring a plausible declared size that fits the buffer
                    # (decode_mini_message already checks) and a known type or
                    # size that is not enormous relative to remaining bytes.
                    known = mtype in (
                        sp.SpiceMessage.SET_ACK,
                        sp.SpiceMessage.PING,
                        sp.SpiceMessage.PONG,
                        sp.SpiceMessage.ACK_SYNC,
                        sp.SpiceMessage.ACK,
                        sp.SpiceMessage.MARK,
                        sp.SpiceMessage.DISPLAY_INIT,
                        sp.SpiceMessage.DRAW_COPY,
                        sp.SpiceMessage.SURFACE_CREATE,
                    )
                    if known or mini["header"]["size"] <= len(data) - sp.MINI_HEADER_SIZE:
                        _apply_display_spice_type(mtype, mini["payload"], responses, progress)
                        consumed = sp.MINI_HEADER_SIZE + mini["header"]["size"]
                except Exception:
                    consumed = 0
            if not consumed and len(data) >= sp.DATA_HEADER_SIZE:
                try:
                    msg = sp.decode_data_message(data)
                    _apply_display_spice_type(
                        msg["header"]["type"], msg["payload"], responses, progress
                    )
                    consumed = sp.DATA_HEADER_SIZE + msg["header"]["size"]
                except Exception:
                    consumed = 0
            if not consumed:
                break
            data = data[consumed:]
    except Exception:
        # The server can coalesce opaque bytes; keep the connection alive even if decode fails.
        return
    for resp in responses:
        sock.sendall(frame_head_pack(DATA_TYPE, len(resp), sid, channel_id) + resp)
        stats["responses"] = stats.get("responses", 0) + 1


def spice_handshake(
    sock: socket.socket,
    max_wait: float = 12.0,
    vm_id: str = "",
) -> Dict[str, object]:
    first = recv_trunk_frame(sock, 3.0)
    sid = first.field1
    spice_session_id = 0  # initialized 0, only set from MAIN_INIT (line 594), matching Go's SpiceHandshake
    try:
        vm_id_int = int(str(vm_id).strip()) if str(vm_id).strip() else 0
    except (TypeError, ValueError):
        vm_id_int = 0

    connected: List[int] = []
    progress = sp.create_protocol_progress()
    stats: Dict[str, int] = {"frames": 1, "responses": 0}

    def recv_frame_ar(timeout: float = 2.0) -> Optional[Frame]:
        frame = recv_trunk_frame(sock, timeout)
        stats["frames"] += 1
        _reply_keepalive_frame(sock, sid, frame, stats)
        return frame

    def authenticate_channel(channel_id: int, channel_type: int, wait_seconds: float, connection_id: int = 0) -> bool:
        # HyScg ExtInfo+REDQ path (observed-only KPI; no cadence change).
        # T49: pass product vm_id into ExtInfo[10:14] so server does not bind magic 0x10820.
        kpi_hooks.maybe("note_hyscg", f"auth_ch{channel_id}")
        auth_blob = build_channel_auth(
            sid, channel_id, channel_type, connection_id, vm_id=vm_id_int
        )
        _frame_dump_raw(
            "TX",
            auth_blob,
            "auth_ch=%s type=%s conn=%s sid=%s vm=%s"
            % (channel_id, channel_type, connection_id, sid, vm_id_int),
        )
        sock.sendall(auth_blob)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                frame = recv_frame_ar(2.0)
            except Exception as exc:
                _frame_dump_write("exc ch=%s %s" % (channel_id, exc))
                break
            if frame is None:
                continue
            # Full RX already dumped in recv_trunk_frame (no f2 filter).
            if frame.pkt_type == CONTROL_TYPE:
                continue
            # Drain/respond display traffic while waiting for this channel's REDQ.
            # T50: ch3/4 auth saw zero RX because SET_ACK on ch2 was never ACK_SYNC'd
            # while authenticate_channel discarded non-matching field2 frames.
            if frame.field2 != channel_id:
                if frame.field2 == CHANNEL_DISPLAY and frame.payload:
                    try:
                        _handle_display_payload(
                            sock, sid, CHANNEL_DISPLAY, frame.payload, progress, stats
                        )
                    except Exception:
                        pass
                continue
            if b"REDQ" not in frame.payload:
                # Display-side spice may still land with field2==channel after open;
                # for non-display channels ignore non-REDQ noise.
                if channel_id == CHANNEL_DISPLAY and frame.payload:
                    try:
                        _handle_display_payload(
                            sock, sid, CHANNEL_DISPLAY, frame.payload, progress, stats
                        )
                    except Exception:
                        pass
                continue
            kpi_hooks.maybe("note_redq", channel_id)
            kpi_hooks.maybe("note_hyscg", f"redq_ch{channel_id}")
            if not _send_ticket(sock, sid, channel_id, frame.payload):
                kpi_hooks.maybe("note_channel_open", channel_id, False)
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
                    if auth_frame.field2 == CHANNEL_DISPLAY and auth_frame.payload:
                        try:
                            _handle_display_payload(
                                sock,
                                sid,
                                CHANNEL_DISPLAY,
                                auth_frame.payload,
                                progress,
                                stats,
                            )
                        except Exception:
                            pass
                    continue
                if len(auth_frame.payload) != 4:
                    continue
                ok = struct.unpack("<I", auth_frame.payload)[0] == 0
                kpi_hooks.maybe("note_channel_open", channel_id, ok)
                if ok:
                    kpi_hooks.maybe("note_hyscg", f"open_ch{channel_id}")
                return ok
            kpi_hooks.maybe("note_channel_open", channel_id, False)
            return False
        kpi_hooks.maybe("note_channel_open", channel_id, False)
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
            if progress.get("markReceived"):
                break
            if frame.field2 != CHANNEL_DISPLAY or len(frame.payload) < 6:
                continue
            # Live SCG: MARK is mini type 102 at payload[0:2] (no token prefix).
            msg_type = struct.unpack_from("<H", frame.payload, 0)[0]
            if msg_type == sp.SpiceMessage.MARK:
                progress["markReceived"] = True
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
                # G2: attach inputs(3) + cursor(4) after main+display (full HyScg→REDQ→ticket)
                if authenticate_channel(CHANNEL_INPUTS, sp.SpiceChannel.INPUTS, 60.0, spice_session_id):
                    connected.append(CHANNEL_INPUTS)
                if authenticate_channel(CHANNEL_CURSOR, sp.SpiceChannel.CURSOR, 60.0, spice_session_id):
                    connected.append(CHANNEL_CURSOR)

    return {
        "session_id": sid,
        "spice_session_id": spice_session_id,
        "connected_channels": [CHANNEL_NAMES.get(c, str(c)) for c in connected],
        "progress": progress,
        "spice_ok": bool(spice_session_id),
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


def _scg_probe_socket(sock: socket.socket) -> None:
    """Non-consuming liveness check via select.select."""
    try:
        r, _, _ = select.select([sock], [], [], 0.0)
    except (ValueError, OSError):
        raise EOFError("SCG connection lost")


def _scg_sleep_drain(sock: socket.socket, interval: float, sid: int, stats: Dict[str, int]) -> None:
    """Sleep while draining incoming frames via select.select.
    
    Mimics Go's readFramesWithTimeout goroutine: prevents TCP receive buffer
    from filling up by continuously reading frames during the sleep period.
    """
    deadline = time.monotonic() + interval
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        chunk = min(1.0, remaining)
        try:
            r, _, _ = select.select([sock], [], [], chunk)
        except (ValueError, OSError):
            return  # socket closed
        if not r:
            continue  # timeout, still within sleep period
        # Data available - drain available frames
        try:
            frames = recv_all_frames(sock, 0.5, 5)
        except (EOFError, ConnectionError):
            raise
        except Exception:
            frames = []
        if not frames:
            continue
        stats["frames"] = stats.get("frames", 0) + len(frames)
        for frame in frames:
            # Observed-only: count native 174-byte payloads if present (never synthesize).
            if len(getattr(frame, "payload", b"") or b"") == 174:
                kpi_hooks.maybe("note_wan_174b", 174)
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
        kpi_hooks.maybe("note_hold_reply")
    elif msg_type == 0x03:  # SET_ACK -> ACK_SYNC
        generation = struct.unpack_from("<I", frame.payload, 6)[0] if len(frame.payload) >= 10 else 0
        ack_sync = struct.pack("<HII", 0x01, 4, generation)
        sock.sendall(frame_head_pack(DATA_TYPE, len(ack_sync), sid, frame.field2) + ack_sync)
        stats["responses"] = stats.get("responses", 0) + 1
        kpi_hooks.maybe("note_hold_reply")



# Closed fail-reason taxonomy (D1 §5.1 / I-PHASE-I-FLAGS). Empty string = PASS path.
FAIL_REASON_TAXONOMY = frozenset({
    "",
    "tls_hold_mode_spice_skipped",
    "spice_main_init_timeout_or_missing",
    "auth_failed",
    "tls_hold_interrupted",
    "scg_exception",
    "unknown",
})


def enforce_honesty_flags(result: Dict[str, object], mode: str = "spice") -> Dict[str, object]:
    """Fail-closed honesty for spice_ok / degraded / keepalive_mode / tls_hold_ok.

    Invariants (always true after this call):
      * mode==tls_hold  => spice_ok is False, degraded is True, keepalive_mode==tls_hold
      * not (keepalive_mode==tls_hold and spice_ok)
      * fail_reason is from FAIL_REASON_TAXONOMY (unknown coerced)
    Mutates and returns the same dict.
    """
    if not isinstance(result, dict):
        return result
    mode_n = str(mode or result.get("keepalive_mode") or "spice").strip().lower()
    if mode_n not in ("spice", "tls_hold"):
        mode_n = "spice"

    if mode_n == "tls_hold":
        result["keepalive_mode"] = "tls_hold"
        result["spice_ok"] = False
        result["degraded"] = True
        # Do not invent tls_hold_ok=True; only preserve explicit True from hold loop.
        if "tls_hold_ok" not in result:
            result["tls_hold_ok"] = False
        fr = result.get("fail_reason")
        if not fr:
            result["fail_reason"] = "tls_hold_mode_spice_skipped"
    else:
        result["keepalive_mode"] = str(result.get("keepalive_mode") or "spice")
        spice_ok = bool(result.get("spice_ok"))
        result["spice_ok"] = spice_ok
        if not spice_ok:
            result["degraded"] = True
            if not result.get("fail_reason"):
                result["fail_reason"] = "spice_main_init_timeout_or_missing"
        else:
            result["degraded"] = bool(result.get("degraded", False))
            result.setdefault("fail_reason", "")

    # Final invariant: never claim SPICE success under tls_hold label.
    km = str(result.get("keepalive_mode") or "").strip().lower()
    if km == "tls_hold" and result.get("spice_ok"):
        result["spice_ok"] = False
        result["degraded"] = True
        if not result.get("fail_reason"):
            result["fail_reason"] = "tls_hold_mode_spice_skipped"

    fr = result.get("fail_reason")
    if fr is None:
        result["fail_reason"] = ""
    elif str(fr) not in FAIL_REASON_TAXONOMY:
        # Keep original text but mark unknown for closed taxonomy consumers.
        result["fail_reason_raw"] = fr
        result["fail_reason"] = "unknown"

    return result


def _run_once(
    scg_ip: str,
    scg_port: str,
    sc_auth_code: str,
    vm_id: str,
    duration: Optional[int] = 60,
    user_service_id: str = "",
    state_path: Optional[str] = None,
    mode: str = "spice",
) -> Dict[str, object]:
    duration_seconds = _round_seconds(duration)
    mode = (mode or "spice").strip().lower()
    if mode not in ("spice", "tls_hold"):
        raise ValueError("unsupported scg mode: %s (expected spice|tls_hold)" % mode)
    # I-G4: session-scoped KPI collector (observed-only; no fake 174 pads).
    kpi_hooks.start_session(session_tag="scg:%s:%s:%s" % (scg_ip, scg_port, mode))
    sock, auth_session_id = connect_scg(scg_ip, scg_port, sc_auth_code, vm_id)
    started = time.monotonic()
    heartbeat_count = 0
    soho_heartbeat_count = 0
    try:
        if mode == "tls_hold":
            # Explicit degradation: Auth+TLS only; do NOT claim SPICE success.
            sid = int(auth_session_id or 0)
            stats: Dict[str, object] = {
                "frames": 0,
                "responses": 0,
                "trunk_switch_replies": 0,
                "tls_hold": True,
            }
            result: Dict[str, object] = {
                "session_id": sid,
                "spice_ok": False,
                "connected_channels": [],
                "progress": {
                    "authOk": True,
                    "tlsOk": True,
                    "spiceHandshakeSkipped": True,
                    "mode": "tls_hold",
                },
                "stats": stats,
                "auth_session_id": auth_session_id,
                "heartbeats": 0,
                "sohoHeartbeats": 0,
                "keepalive_mode": "tls_hold",
                "degraded": True,
                "fail_reason": "tls_hold_mode_spice_skipped",
            }
        else:
            # T49: pass product pin vm_id into ExtInfo[10:14] for redirect lookup.
            result = spice_handshake(sock, vm_id=vm_id)
            sid = int(result.get("session_id") or 0)
            stats = result.get("stats")
            if not isinstance(stats, dict):
                stats = {}
                result["stats"] = stats
            result["auth_session_id"] = auth_session_id
            result["heartbeats"] = 0
            result["sohoHeartbeats"] = 0

        # I-PHASE-I-HOLD dual-plane hold loop:
        #   fast: ~HOLD_SELECT_SECONDS select/drain (frame reply / trunk_switch / 174 observe)
        #   slow: SOHO + mouse_mode every HOLD_KEEPALIVE_INTERVAL (~25s), not every select tick
        # Outer 25s sleep-only drain removed (G3). No synthetic 174B writer.
        # I-PHASE-I-KPI: 4-sample VM power via power_monitor.snapshot
        #   phases: start / one_third / two_thirds / end  (wall clock ≠ VM powered claim)
        last_keepalive_plane = None  # None → force first slow-plane on first tick
        stats["hold_select_seconds"] = HOLD_SELECT_SECONDS
        stats["hold_keepalive_interval"] = HOLD_KEEPALIVE_INTERVAL
        stats["hold_plane"] = "dual"
        started_wall = time.time()
        vm_phases_fired = set()  # type: set
        # sample #1: start (before hold ticks)
        kpi_hooks.maybe_vm_sample_via_power_monitor(
            "start",
            user_service_id=user_service_id or "",
            state_path=state_path,
            started_wall=started_wall,
            index=0,
        )
        vm_phases_fired.add("start")
        while True:
            now = time.monotonic()
            elapsed = now - started
            if duration_seconds > 0 and elapsed >= duration_seconds:
                break

            # I-PHASE-I-KPI mid-hold samples at 1/3 and 2/3 of requested duration
            if duration_seconds > 0:
                if "one_third" not in vm_phases_fired and elapsed >= (duration_seconds / 3.0):
                    kpi_hooks.maybe_vm_sample_via_power_monitor(
                        "one_third",
                        user_service_id=user_service_id or "",
                        state_path=state_path,
                        started_wall=started_wall,
                        index=1,
                    )
                    vm_phases_fired.add("one_third")
                if "two_thirds" not in vm_phases_fired and elapsed >= (2.0 * duration_seconds / 3.0):
                    kpi_hooks.maybe_vm_sample_via_power_monitor(
                        "two_thirds",
                        user_service_id=user_service_id or "",
                        state_path=state_path,
                        started_wall=started_wall,
                        index=2,
                    )
                    vm_phases_fired.add("two_thirds")

            # --- slow plane: SOHO / mouse every ~25s ---
            if _hold_should_run_slow_plane(now, last_keepalive_plane, HOLD_KEEPALIVE_INTERVAL):
                last_keepalive_plane = now
                heartbeat_count += 1
                kpi_hooks.maybe("note_hold_heartbeat")
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

            # --- fast plane: ~1s select/drain (replies handled inside _scg_sleep_drain) ---
            select_budget = HOLD_SELECT_SECONDS
            if duration_seconds > 0:
                remaining = duration_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    break
                select_budget = min(HOLD_SELECT_SECONDS, remaining)
            _scg_sleep_drain(sock, select_budget, sid, stats)
            _scg_probe_socket(sock)

        # sample #4: end (after hold loop; independent of wall success)
        kpi_hooks.maybe_vm_sample_via_power_monitor(
            "end",
            user_service_id=user_service_id or "",
            state_path=state_path,
            started_wall=started_wall,
            index=3,
        )
        wall_hold = float(time.monotonic() - started)
        kpi_hooks.maybe("set_wall_hold_seconds", wall_hold)

        result["heartbeats"] = heartbeat_count
        result["sohoHeartbeats"] = soho_heartbeat_count
        result["duration_seconds"] = int(wall_hold)
        # Keepalive mode + degradation flags (fail-closed via enforce_honesty_flags).
        if mode == "tls_hold":
            result["tls_hold_ok"] = True  # held for full duration without exception
            stats["tls_hold_ok"] = True
        enforce_honesty_flags(result, mode=mode)
        auth_tax.annotate_result(result)
        # I-G4: push spice_ok / degraded into collector and merge KPI into stats.
        kpi_hooks.maybe("set_spice_ok", bool(result.get("spice_ok")))
        kpi_hooks.maybe("set_degraded", bool(result.get("degraded")))
        coll = kpi_hooks.get_active()
        if coll is not None and isinstance(stats, dict):
            coll.merge_into_stats(stats)
            result["stats"] = stats
            # surface VM samples at result top-level for consumers (honest, optional)
            result["vm_samples"] = stats.get("vm_samples")
            result["vm_sample_count"] = stats.get("vm_sample_count")
            result["vm_powered_throughout"] = stats.get("vm_powered_throughout")
            result["wall_hold_seconds"] = stats.get("wall_hold_seconds")

        # Surface lock-screen soho codes if present in nested stats.
        soho_code = stats.get("soho_heartbeat_code")
        try:
            soho_code_i = int(soho_code) if soho_code is not None else None
        except Exception:
            soho_code_i = None
        if soho_code_i in {4039, 4040, 4041, 4042}:
            result["desktop_lock_hint"] = True
            stats["desktop_lock_hint"] = True
        return result
    finally:
        try:
            kpi_hooks.end_session(flush=True)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass


def classify_scg_soft_failure(exc: BaseException) -> Dict[str, object]:
    """Classify CEM / transport / VM-off errors for soft-recover tagging.

    Platform maintenance often mass-powers-off desktops; CEM then returns 4xx/5xx
    or connection errors. These must be recoverable under forever mode so the
    product process does not exit (ZTE parity).
    """
    msg = "%s: %s" % (type(exc).__name__, exc)
    low = msg.lower()
    tags: Dict[str, object] = {
        "error": msg,
        "recoverable": True,
        "platform_maintenance": False,
        "fail_reason": "scg_exception",
    }
    maint_hints = (
        "maintenance",
        "maintain",
        "维护",
        "升级",
        "powered off",
        "poweroff",
        "power off",
        "vm_powered_off",
        "not running",
        "desktop is off",
        "status is off",
        "关机",
        "已关机",
        "停机",
    )
    if any(h in low for h in maint_hints):
        tags["platform_maintenance"] = True
        tags["fail_reason"] = "vm_powered_off"
    # CEM / HTTP gateway blips
    if any(
        h in low
        for h in (
            "getconnectinfo",
            "cem",
            "http error",
            "http 5",
            "http 4",
            "502",
            "503",
            "504",
            "timeout",
            "timed out",
            "temporarily",
            "connection reset",
            "connection refused",
            "network is unreachable",
            "name or service not known",
        )
    ):
        tags["recoverable"] = True
        if tags["fail_reason"] == "scg_exception":
            tags["fail_reason"] = "token_transient" if "token" in low else "scg_cem_blip"
        # Mass offline during official maintenance often surfaces as CEM 4xx/5xx
        if any(h in low for h in ("502", "503", "504", "维护", "maintenance")):
            tags["platform_maintenance"] = True
    return tags


def run_scg_keepalive(
    scg_ip: str,
    scg_port: str,
    sc_auth_code: str,
    vm_id: str,
    duration: Optional[int] = 60,
    forever: bool = False,
    user_service_id: str = "",
    state_path: Optional[str] = None,
    mode: str = "spice",
    reconnect_fn: Optional[Callable[[], Dict[str, str]]] = None,
    backoff_base_s: float = _FOREVER_BACKOFF_BASE_S,
    backoff_cap_s: float = _FOREVER_BACKOFF_CAP_S,
) -> SCGKeepaliveResult:
    """Run SCG native keepalive in-process using the pure-Python SCG route.

    mode:
      - spice: full trunk-SPICE handshake + keepalive (default)
      - tls_hold: Auth+TLS hold + soho HTTP heartbeat; skips SPICE handshake

    forever soft-recover:
      Single-round exceptions (CEM blip, VM mass-off during platform maintenance,
      TCP drop) are logged, tagged recoverable, and the loop continues with
      exponential backoff. Optional reconnect_fn() may refresh scg_ip/port/
      scAuthCode each recovery cycle (product path re-fetches getConnectInfo).
      Only KeyboardInterrupt / SystemExit abort forever.
    """
    rounds = 0
    last: Dict[str, object] = {}
    stdout_lines: List[str] = []
    mode = (mode or "spice").strip().lower()
    consecutive_fails = 0
    cur_ip, cur_port, cur_auth = scg_ip, scg_port, sc_auth_code

    def _apply_reconnect() -> None:
        nonlocal cur_ip, cur_port, cur_auth
        if reconnect_fn is None:
            return
        try:
            fresh = reconnect_fn() or {}
        except Exception as re_exc:  # noqa: BLE001
            stdout_lines.append(
                "SCG reconnect_fn failed (will retry with previous endpoint): %s: %s"
                % (type(re_exc).__name__, re_exc)
            )
            return
        if not isinstance(fresh, dict):
            return
        cur_ip = str(fresh.get("scgIp") or fresh.get("scg_ip") or cur_ip)
        cur_port = str(fresh.get("scgPort") or fresh.get("scg_port") or cur_port)
        cur_auth = str(fresh.get("scAuthCode") or fresh.get("sc_auth_code") or cur_auth)

    while True:
        rounds += 1
        try:
            if forever and rounds > 1:
                # Refresh endpoint after a prior failure / between forever rounds
                # so post-maintenance IP/port/auth changes are picked up.
                _apply_reconnect()
            last = _run_once(
                cur_ip,
                cur_port,
                cur_auth,
                vm_id,
                duration=duration,
                user_service_id=user_service_id,
                state_path=state_path,
                mode=mode,
            )
            consecutive_fails = 0
            stdout_lines.append(
                "SCG round %d mode=%s spice_ok=%s tls_hold_ok=%s channels=%s heartbeats=%s sohoHeartbeats=%s progress=%s" % (
                    rounds,
                    last.get("keepalive_mode") or mode,
                    last.get("spice_ok"),
                    last.get("tls_hold_ok"),
                    ",".join(last.get("connected_channels") or []),
                    last.get("heartbeats"),
                    last.get("sohoHeartbeats"),
                    last.get("progress"),
                )
            )
            if not forever:
                break
            # Soft-fail round (spice_ok false) under forever: still continue after backoff
            spice_ok = bool(last.get("spice_ok"))
            tls_hold_ok = bool(last.get("tls_hold_ok"))
            round_ok = tls_hold_ok if mode == "tls_hold" else spice_ok
            if not round_ok:
                consecutive_fails += 1
                last = dict(last)
                last["recoverable"] = True
                if last.get("fail_reason") in ("", None):
                    last["fail_reason"] = "scg_round_soft_fail"
                enforce_honesty_flags(last, mode=mode)
                auth_tax.annotate_result(last)
                delay = min(backoff_cap_s, backoff_base_s * (2 ** min(consecutive_fails - 1, 4)))
                stdout_lines.append(
                    "SCG forever soft-fail round=%d delay=%.1fs reason=%s"
                    % (rounds, delay, last.get("fail_reason"))
                )
                time.sleep(delay)
                _apply_reconnect()
                continue
            # Successful forever round: brief pause then next
            time.sleep(max(1.0, float(backoff_base_s)))
            _apply_reconnect()
            continue
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            tags = classify_scg_soft_failure(exc)
            stderr = str(tags.get("error") or exc)
            last = dict(last) if last else {}
            last["recoverable"] = bool(tags.get("recoverable", True))
            last["platform_maintenance"] = bool(tags.get("platform_maintenance", False))
            last["error"] = stderr
            if mode == "tls_hold":
                last["tls_hold_ok"] = False
                last["fail_reason"] = last.get("fail_reason") or tags.get("fail_reason") or "tls_hold_interrupted"
            else:
                last["spice_ok"] = False
                last["fail_reason"] = last.get("fail_reason") or tags.get("fail_reason") or "scg_exception"
            enforce_honesty_flags(last, mode=mode)
            auth_tax.annotate_result(last)
            stdout_lines.append(
                "SCG round %d exception recoverable=%s platform_maintenance=%s: %s"
                % (rounds, last.get("recoverable"), last.get("platform_maintenance"), stderr)
            )
            if not forever:
                return SCGKeepaliveResult(
                    1,
                    "\n".join(stdout_lines),
                    stderr,
                    stats={"rounds": rounds, "last": last, "mode": mode},
                )
            # forever: backoff + reconnect + continue (do NOT exit process)
            consecutive_fails += 1
            delay = min(backoff_cap_s, backoff_base_s * (2 ** min(consecutive_fails - 1, 4)))
            stdout_lines.append(
                "SCG forever continue after exception round=%d delay=%.1fs"
                % (rounds, delay)
            )
            time.sleep(delay)
            _apply_reconnect()
            continue
    return SCGKeepaliveResult(0, "\n".join(stdout_lines), "", stats={"rounds": rounds, "last": last, "mode": mode})
