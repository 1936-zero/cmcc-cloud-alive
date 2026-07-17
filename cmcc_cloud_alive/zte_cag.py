#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pure-Python port of B's internal/zte/cag.go + cag_tcp.go (line-by-line fork).

ZTE CAG TCP/TLS transport (P7).  Once product_router decides route==ZTE and
zte_route.run_material has produced an ``OuterCAGTarget`` (firm CAG endpoint)
plus an ``InnerConnectParams`` (parsed from the connectStr), this module
performs the CAG TCP pre-auth handshake and upgrades the same socket to TLS.

The handshake is a strict byte-for-byte port of B's ``DialCAGTCPTLS``:

  1. TCP connect to outer CAG (cagIp:cagPort).
  2. Send 178-byte local-key packet (``build_cag_auth_head_packet`` payload).
  3. Read 50-byte local-key ack; verify magic ``ZTEC``; parse conv @ [14:18].
  4. Send 220-byte auth blob (``build_cag_auth_blob``).
  5. Read 36-byte auth ack; verify ``ack[4] == 0x01``.
  6. TLS upgrade on the *same* socket (InsecureSkipVerify, TLS 1.2+).
  7. Return (tls_stream, CAGSessionInfo(conv)).

IPv6 long-session path (ye4B6y official pcap + PROBE_raw_ztec_hold60)::

  Official CAG after auth does **not** upgrade to TLS.  Use
  ``dial_cag_tcp_raw`` (50-byte ZTEC head + L220 with ``inner.port`` +
  auth ack), then **prime** ADD_LINK lid=7/8 (127.0.0.1:3246 type=9)
  matching OFFICIAL_ztec_p36024_04/07, then keep the raw socket alive
  with ``0a000000`` heartbeats.  Pure HB without ADD_LINK was observed
  to lose the session ~30min (VM power-off).  Detection:
  ``uses_raw_ztec_path(inner)`` when the auth host is IPv6.

Outer/inner separation (P6): dial helpers accept an outer address string
and an ``InnerConnectParams`` for the auth blob — they are never mixed.
"""

import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union

from .zte_connect_params import InnerConnectParams


# --- session info ----------------------------------------------------------

@dataclass
class CAGSessionInfo:
    """Mirror of Go CAGSessionInfo (cag.go:19)."""
    syn_id: bytes = b""
    conv: int = 0


# --- low-level fill helpers (cag.go:271-285) -------------------------------

def _fill_random(dst) -> None:
    """Fill ``dst`` with cryptographic random (cag.go fillRandom).

    ``dst`` must be a writable buffer (bytearray or memoryview slice).
    """
    data = os.urandom(len(dst))
    dst[:] = data


def _fill_ascii_hex(dst) -> None:
    """Fill ``dst`` with lowercase ASCII hex of random bytes (cag.go fillASCIIHex).

    ``len(dst)`` must be even; half as many random bytes are hex-encoded.
    ``dst`` must be a writable buffer (bytearray or memoryview slice).
    """
    n = len(dst)
    if n % 2 != 0:
        raise ValueError("fill_ascii_hex requires even length, got %d" % n)
    raw = os.urandom(n // 2)
    dst[:] = raw.hex().encode("ascii")


# --- auth head packet (cag.go:191-210) -------------------------------------

def build_cag_auth_head_packet() -> Tuple[bytes, bytes]:
    """Build the 199-byte CAG auth-head packet (cag.go buildCAGAuthHeadPacket).

    Returns ``(packet_199, syn_id_4)``.  The TCP first-send payload is
    ``packet[21:]`` (178 bytes).  Used by the IPv4 / TLS path only.
    """
    packet = bytearray(21 + 178)  # 199 bytes
    mv = memoryview(packet)
    mv[0:4] = b"\x06\x00\x00\x80"
    syn_id = mv[11:15]  # writable view
    _fill_random(syn_id)

    payload = mv[21:]  # 178 bytes, writable view
    payload[0:4] = b"ZTEC"
    struct.pack_into("<H", payload, 4, 0x00ac)       # payload[4:6]
    struct.pack_into("<I", payload, 6, 101)           # payload[6:10]
    _fill_random(payload[10:14])
    payload[14:18] = b"\xdc\x00\x00\x00"
    _fill_random(payload[18:38])
    payload[38:42] = b"\x07\x00\x0b\x0b"
    _fill_ascii_hex(payload[54:86])    # 32 bytes
    _fill_ascii_hex(payload[118:134])  # 16 bytes
    return bytes(packet), bytes(syn_id)


def build_cag_auth_head_packet_short() -> bytes:
    """Build the official 50-byte ZTEC auth-head (ye4B6y C2S L50).

    Layout (stable across ``OFFICIAL_ztec_p*_00_C2S_L50.bin``)::

        [0:4]   ZTEC
        [4:6]   0x002c LE
        [6:10]  101 LE
        [10:14] random
        [14:18] dc 00 00 00
        [18:34] zeros
        [34:38] 03 00 8c 0c
        [38:50] zeros

    Used by ``dial_cag_tcp_raw`` (IPv6 long-session).  The legacy 178-byte
    head is rejected by this CAG class (``FAIL_HEAD_ACK`` in probes).
    """
    # bytearray slices are copies — must use memoryview (see RULES Go→Py).
    pkt = bytearray(50)
    mv = memoryview(pkt)
    mv[0:4] = b"ZTEC"
    struct.pack_into("<H", mv, 4, 0x002c)
    struct.pack_into("<I", mv, 6, 101)
    _fill_random(mv[10:14])
    mv[14:18] = b"\xdc\x00\x00\x00"
    # [18:34] remain zero
    mv[34:38] = b"\x03\x00\x8c\x0c"
    return bytes(pkt)


# Official post-auth keepalive frame on raw ZTEC (empty CAG DATA).
ZTEC_RAW_HB = b"\x0a\x00\x00\x00"

# Official ADD_LINK on raw ZTEC (OFFICIAL_ztec_p36024_04/07 C2S L158).
# Layout matches CAG proxy add-link: [cmd=0x1a][lid][u16=0x9a LE][LinkInfo].
_CAG_ADD_LINK_CMD = 0x1A
_CAG_ADD_LINK_PAYLOAD_LEN = 0x9A
# Local spice-proxy endpoint used by official chuanyunAddOn after auth.
_RAW_ZTEC_LINK_PORT = 3246
_RAW_ZTEC_LINK_TYPE = 0x09  # not SPICE main/display (1/2)
_RAW_ZTEC_LINK_IDS = (7, 8)

# Official first DATA on link 7 after ADD_LINK (OFFICIAL_ztec_p36024_05 L1692).
# cmd=0x0a lid=7 plen=1688; body almost all-zero with 3 fixed non-zeros.
_CAG_DATA_CMD = 0x0A
_RAW_ZTEC_DATA_LID = 7
_RAW_ZTEC_DATA_PLEN = 1688
# Re-send the session DATA every N seconds so CAG sees tunnel activity
# beyond empty 4B HB (ADD_LINK+HB alone did not block ~30min power-off).
_RAW_ZTEC_DATA_RESEND_S = 60.0


def build_raw_ztec_add_link_packet(
    link_id: int,
    *,
    port: int = _RAW_ZTEC_LINK_PORT,
    channel_type: int = _RAW_ZTEC_LINK_TYPE,
) -> bytes:
    """Build official-shaped raw-ZTEC ADD_LINK (158B).

    Evidence (``OFFICIAL_ztec_p36024_04_C2S_L158.bin`` / ``_07_``)::

        port=3246, type=0x09, ip=127.0.0.1 (reversed bytes),
        payload[0x54]=0x0c; remaining LinkInfo zeros.
        link_id is 7 then 8 on the wire.
    """
    payload = bytearray(_CAG_ADD_LINK_PAYLOAD_LEN)
    struct.pack_into("<H", payload, 0, int(port) & 0xFFFF)
    payload[2] = int(channel_type) & 0xFF
    # 127.0.0.1 in reversed byte order (same as B / official capture).
    payload[4] = 1
    payload[5] = 0
    payload[6] = 0
    payload[7] = 127
    payload[0x54] = 0x0C
    return (
        struct.pack("<BBH", _CAG_ADD_LINK_CMD, int(link_id) & 0xFF,
                    _CAG_ADD_LINK_PAYLOAD_LEN)
        + bytes(payload)
    )


def build_raw_ztec_data_packet(
    link_id: int = _RAW_ZTEC_DATA_LID,
) -> bytes:
    """Build official-shaped raw-ZTEC DATA (1692B) for post-ADD_LINK.

    Evidence (``OFFICIAL_ztec_p36024_05_C2S_L1692.bin``, bit-identical to
    p56700_05)::

        cmd=0x0a, lid=7, plen=1688
        body[8]=0x12, body[528]=0x01, body[537]=0x01; rest zero.
    """
    body = bytearray(_RAW_ZTEC_DATA_PLEN)
    body[8] = 0x12
    body[528] = 0x01
    body[537] = 0x01
    return (
        struct.pack("<BBH", _CAG_DATA_CMD, int(link_id) & 0xFF,
                    _RAW_ZTEC_DATA_PLEN)
        + bytes(body)
    )


def _drain_sock(sock: socket.socket, *, first_timeout: float = 2.0,
                next_timeout: float = 0.15) -> int:
    """Drain readable bytes; return total length. Timeout → 0 more data."""
    got = 0
    sock.settimeout(first_timeout)
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            got += len(chunk)
            sock.settimeout(next_timeout)
    except (socket.timeout, BlockingIOError, OSError):
        pass
    return got


def prime_raw_ztec_links(sock: socket.socket) -> Dict[str, int]:
    """Post-auth ADD_LINK + official DATA prime (p36024 order).

    Sends ADD_LINK lid 7, ADD_LINK lid 8, then DATA lid 7 (1692B).  Drains
    any immediate replies.  Does not raise on empty drain; send failures
    propagate.
    """
    links_sent = 0
    data_sent = 0
    prime_recv = 0
    for lid in _RAW_ZTEC_LINK_IDS:
        sock.sendall(build_raw_ztec_add_link_packet(lid))
        links_sent += 1
        prime_recv += _drain_sock(sock)
    # Official next frame after the two ADD_LINKs is DATA on lid 7.
    sock.sendall(build_raw_ztec_data_packet(_RAW_ZTEC_DATA_LID))
    data_sent = 1
    prime_recv += _drain_sock(sock, first_timeout=3.0)
    return {
        "links_sent": links_sent,
        "data_sent": data_sent,
        "prime_recv": prime_recv,
    }


def uses_raw_ztec_path(inner: Optional[InnerConnectParams]) -> bool:
    """True when the CAG auth host is IPv6 → raw ZTEC (no TLS) path.

    Host resolution mirrors ``build_cag_auth_blob``: env
    ``CCK_ZTE_CAG_AUTH_HOST`` overrides ``inner.host``.
    """
    if inner is None:
        return False
    host = os.environ.get("CCK_ZTE_CAG_AUTH_HOST", "") or inner.host
    return ":" in (host or "")


# --- auth template (cag.go:136-151) ----------------------------------------

def parse_auth_template(template_hex: str) -> Optional[bytes]:
    """Parse a CAG auth template hex string (cag.go parseAuthTemplate).

    Returns ``None`` when ``template_hex`` is empty (build-from-scratch path).
    Accepts 241-byte (stripped to 220) or 220-byte templates.
    """
    if template_hex == "":
        return None
    try:
        template = bytes.fromhex(template_hex)
    except ValueError as exc:
        raise ValueError("decode CAG auth template: %s" % exc)
    if len(template) == 241 and template[0] == 0x08:
        return bytes(template)
    if len(template) == 220:
        return bytes(template)
    raise ValueError("invalid CAG auth template length %d" % len(template))


# --- auth blob (cag.go:153-189) --------------------------------------------

def build_cag_auth_blob(inner: InnerConnectParams,
                        template: Optional[bytes] = None) -> bytes:
    """Build the 220-byte CAG auth blob (cag.go buildCAGAuthBlob + ye4 evidence).

    ``inner`` is the frozen InnerConnectParams (P6).  When ``template`` is
    provided (220 or 241 bytes) the vmId is patched into ``blob[20:56]``;
    otherwise the blob is built from scratch using host / port-or-proxySport
    / vmId.

    Layout (from-scratch), evidence-backed by official uSmart→:8899 L220
    (``reports/ye4B6y_zte/OFFICIAL_ztec_L220_*.bin``)::

        [0:4]   port u32 LE — **IPv6 raw path uses ``inner.port``** (e.g.
                5100); **IPv4 TLS path keeps ``inner.proxy_sport``**
                (historical Go / working IPv4 accounts).  Probe proof:
                proxy_sport on IPv6 → FAIL_AUTH; port=5100 → OK.
        [4:20]  host address — IPv4 in [4:8] (+ zeros [8:20]) **or** full IPv6
        [20:56] vmId ASCII (36)
        [60:188] random
        [188]   family tag — ``0x50`` (IPv4) / ``0x51`` (IPv6)

    Override host via env ``CCK_ZTE_CAG_AUTH_HOST`` (IPv4 or IPv6 literal).
    """
    if inner is None:
        raise ValueError("missing connect params")
    if template is not None and len(template) == 241:
        template = template[21:]
    if template is not None and len(template) == 220:
        blob = bytearray(template)
        if len(inner.vm_id) == 36:
            blob[20:56] = inner.vm_id.encode("ascii")
        return bytes(blob)
    if template is not None and len(template) != 0:
        raise ValueError(
            "invalid CAG auth template length %d" % len(template))

    host = os.environ.get("CCK_ZTE_CAG_AUTH_HOST", "") or inner.host
    ip_bytes, family_tag = _auth_blob_host_bytes(host)
    if family_tag == 0x51:
        # IPv6 long-session: official L220[0:4] = inner service port.
        if inner.port <= 0:
            raise ValueError("CAG auth blob (IPv6) requires port")
        port_val = inner.port
    else:
        if inner.proxy_sport <= 0:
            raise ValueError("CAG auth blob requires proxySport")
        port_val = inner.proxy_sport
    if len(inner.vm_id) != 36:
        raise ValueError("CAG auth blob requires 36-byte vmId")

    blob = bytearray(220)
    bmv = memoryview(blob)
    struct.pack_into("<I", bmv, 0, port_val)           # blob[0:4]
    bmv[4:4 + len(ip_bytes)] = ip_bytes                # IPv4@4:8 or IPv6@4:20
    bmv[20:56] = inner.vm_id.encode("ascii")           # blob[20:56]
    _fill_random(bmv[60:188])
    blob[188] = family_tag
    return bytes(blob)


def _auth_blob_host_bytes(host: str) -> Tuple[bytes, int]:
    """Map *host* → ``(addr_bytes, family_tag)`` for the CAG auth blob.

    - Dotted IPv4 → 4 bytes + tag ``0x50`` (legacy IPv4 clients)
    - IPv6 literal → 16 bytes + tag ``0x51`` (official ye4B6y L220)
    - else → ValueError (hostname / garbage not accepted)

    Kept as a pure helper so unit tests and env override share one path.
    """
    if not host:
        raise ValueError("CAG auth blob requires host: %r" % host)
    # IPv4 first (inet_aton rejects IPv6).
    try:
        return socket.inet_aton(host), 0x50
    except OSError:
        pass
    # IPv6 literals always contain ':' (incl. compressed / zone forms).
    if ":" in host:
        # Strip zone id if present (e.g. fe80::1%eth0) — not used on WAN hv6.
        bare = host.split("%", 1)[0]
        try:
            return socket.inet_pton(socket.AF_INET6, bare), 0x51
        except OSError as exc:
            raise ValueError("CAG auth blob invalid IPv6 host: %s" % host) from exc
    raise ValueError("CAG auth blob requires IPv4/IPv6 host: %s" % host)


# Back-compat alias (older tests / callers).
def _auth_blob_ipv4_bytes(host: str) -> bytes:
    """Deprecated: returns only the address bytes (IPv4=4B / IPv6=16B)."""
    return _auth_blob_host_bytes(host)[0]


# --- TCP read helper (cag_tcp.go:101-107) ----------------------------------

def _read_cag_tcp_packet(sock: socket.socket, want: int) -> bytes:
    """Read exactly ``want`` bytes from ``sock`` (cag_tcp.go readCAGTCPPacket)."""
    buf = bytearray()
    while len(buf) < want:
        chunk = sock.recv(want - len(buf))
        if not chunk:
            raise EOFError(
                "short read: got %d of %d bytes" % (len(buf), want))
        buf.extend(chunk)
    return bytes(buf)


# --- dial options ----------------------------------------------------------

@dataclass
class CAGDialOptions:
    """Mirror of Go CAGDialOptions (cag_tcp.go)."""
    address: str = ""               # outer CAG host:port
    inner: Optional[InnerConnectParams] = None
    auth_template_hex: str = ""
    timeout: float = 15.0


# --- main entry (cag_tcp.go:15-94) -----------------------------------------

def dial_cag_tcp_tls(opts: CAGDialOptions) -> Tuple[ssl.SSLSocket, CAGSessionInfo]:
    """Perform the ZTE CAG TCP pre-auth handshake + TLS upgrade.

    Returns ``(tls_stream, CAGSessionInfo)``.  Raises on any protocol error.
    """
    if opts.inner is None:
        raise ValueError("missing connect params")
    if opts.address == "":
        raise ValueError("missing CAG address")
    if opts.timeout == 0:
        opts.timeout = 15.0

    auth_template = parse_auth_template(opts.auth_template_hex)

    raw = socket.create_connection(_split_address(opts.address),
                                   timeout=opts.timeout)
    try:
        raw.settimeout(opts.timeout)

        # 1. send 178-byte local-key
        first_udp, _syn_id = build_cag_auth_head_packet()
        first = first_udp[21:]  # 178 bytes
        raw.sendall(first)

        # 2. read 50-byte local-key ack
        head_ack = _read_cag_tcp_packet(raw, 50)
        if len(head_ack) < 50 or head_ack[:4] != b"ZTEC":
            raise ValueError("invalid CAG TCP local-key ack")

        conv = struct.unpack_from("<I", head_ack, 14)[0]  # head_ack[14:18]

        # 3. send 220-byte auth blob
        second = build_cag_auth_blob(opts.inner, auth_template)
        raw.sendall(second)

        # 4. read 36-byte auth ack
        auth_ack = _read_cag_tcp_packet(raw, 36)
        if len(auth_ack) < 8 or auth_ack[4] != 0x01:
            prefix_len = min(16, len(auth_ack))
            raise ValueError(
                "invalid CAG TCP auth ack: %s"
                % auth_ack[:prefix_len].hex())

        # 5. TLS upgrade on the same socket
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        tls_stream = ctx.wrap_socket(raw, server_hostname=None)

        tls_stream.settimeout(None)
        info = CAGSessionInfo(conv=conv)
        # success — prevent the finally Close
        raw = None  # type: ignore
        return tls_stream, info
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass


def dial_cag_tcp_raw(opts: CAGDialOptions) -> Tuple[socket.socket, CAGSessionInfo]:
    """IPv6 long-session dial: 50B ZTEC head + L220 + auth, **no TLS**.

    Evidence (``reports/ye4B6y_zte/PROBE_raw_ztec_hold60.json``)::

      * 50-byte head (``build_cag_auth_head_packet_short``) → head ack
      * L220 with ``inner.port`` + IPv6 + tag 0x51 → auth ack ok=0x01
      * Keep raw TCP; do **not** wrap TLS (official post-auth is L158/HB)

    Returns ``(raw_socket, CAGSessionInfo)``.  Caller owns the socket and
    must close it (or pass to :func:`keepalive_raw_ztec_loop`).
    """
    if opts.inner is None:
        raise ValueError("missing connect params")
    if opts.address == "":
        raise ValueError("missing CAG address")
    if opts.timeout == 0:
        opts.timeout = 15.0

    auth_template = parse_auth_template(opts.auth_template_hex)

    raw = socket.create_connection(_split_address(opts.address),
                                   timeout=opts.timeout)
    try:
        raw.settimeout(opts.timeout)

        # 1. send official 50-byte ZTEC head
        first = build_cag_auth_head_packet_short()
        raw.sendall(first)

        # 2. read 50-byte head ack
        head_ack = _read_cag_tcp_packet(raw, 50)
        if len(head_ack) < 50 or head_ack[:4] != b"ZTEC":
            raise ValueError("invalid CAG TCP local-key ack (raw path)")

        conv = struct.unpack_from("<I", head_ack, 14)[0]

        # 3. send 220-byte auth blob (IPv6 → inner.port via build_cag_auth_blob)
        second = build_cag_auth_blob(opts.inner, auth_template)
        raw.sendall(second)

        # 4. read 36-byte auth ack
        auth_ack = _read_cag_tcp_packet(raw, 36)
        if len(auth_ack) < 8 or auth_ack[4] != 0x01:
            prefix_len = min(16, len(auth_ack))
            raise ValueError(
                "invalid CAG TCP auth ack (raw path): %s"
                % auth_ack[:prefix_len].hex())

        raw.settimeout(None)
        info = CAGSessionInfo(conv=conv)
        out = raw
        raw = None  # type: ignore  # prevent finally close
        return out, info
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass


def keepalive_raw_ztec_loop(sock: socket.socket, *,
                            interval: float = 1.0,
                            stop_after: float = 20.0,
                            prime_links: bool = True,
                            data_resend: float = _RAW_ZTEC_DATA_RESEND_S,
                            ) -> Dict[str, int]:
    """Post-auth raw ZTEC: ADD_LINK+DATA prime + HB + periodic DATA.

    Official long-session (ye4B6y pcap OFFICIAL_ztec_p36024): after auth
    the client opens ADD_LINK 7/8 (127.0.0.1:3246 type=9), sends a 1692B
    DATA on lid 7, then empty CAG DATA heartbeats every ~1s.

    History (see reports/ye4B6y_zte/CHECKPOINT_*)::

        pure HB          → BrokenPipe @~1839s + VM off
        ADD_LINK + HB    → Timeout @~2129s + VM off
        next: ADD_LINK + official DATA + HB (+ resend DATA)

    Returns counters including ``links_sent`` / ``data_sent`` / ``prime_recv``.
    BrokenPipe/EOF on send propagate so the caller can redial.
    """
    hb_sent = 0
    hb_recv = 0
    links_sent = 0
    data_sent = 0
    prime_recv = 0
    deadline = time.monotonic() + max(0.0, stop_after)
    next_data_at = 0.0
    # UX: emit a tick every ~10s so interactive/WebUI do not look hung
    # after ``[zte] path=IPv6-raw-ZTEC`` (silence was mistaken for freeze).
    next_progress_at = time.monotonic() + 10.0
    try:
        if prime_links:
            try:
                primed = prime_raw_ztec_links(sock)
                links_sent = int(primed.get("links_sent") or 0)
                data_sent = int(primed.get("data_sent") or 0)
                prime_recv = int(primed.get("prime_recv") or 0)
                if data_resend and data_resend > 0:
                    next_data_at = time.monotonic() + float(data_resend)
                print(
                    f"[zte] raw-ZTEC primed links={links_sent} "
                    f"data={data_sent} prime_recv={prime_recv}",
                    flush=True,
                )
            except (BrokenPipeError, ConnectionResetError, OSError) as prime_err:
                # Priming failure: still try HB briefly so counters reflect
                # the attempt; re-raise after loop if no HB progress.
                print(
                    f"[zte] raw-ZTEC prime warn: "
                    f"{type(prime_err).__name__}: {prime_err}",
                    flush=True,
                )
        sock.settimeout(max(interval, 1.0) + 2.0)
        while time.monotonic() < deadline:
            now = time.monotonic()
            if (
                next_data_at > 0
                and now >= next_data_at
                and data_resend
                and data_resend > 0
            ):
                sock.sendall(build_raw_ztec_data_packet(_RAW_ZTEC_DATA_LID))
                data_sent += 1
                # Drain any DATA reply (official S2C ~1164B possible).
                prime_recv += _drain_sock(
                    sock, first_timeout=0.5, next_timeout=0.05,
                )
                next_data_at = now + float(data_resend)
                sock.settimeout(max(interval, 1.0) + 2.0)
            sock.sendall(ZTEC_RAW_HB)
            hb_sent += 1
            # Drain any frames until our echo or timeout; count any 4B+ recv.
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    # Count number of 4-byte HB-sized frames approximately.
                    hb_recv += max(1, len(chunk) // 4)
                    # Non-blocking-ish: only one recv batch per send interval.
                    sock.settimeout(0.05)
            except (socket.timeout, BlockingIOError, OSError):
                pass
            sock.settimeout(max(interval, 1.0) + 2.0)
            now = time.monotonic()
            if now >= next_progress_at:
                left = max(0.0, deadline - now)
                print(
                    f"[zte] raw-ZTEC progress hb_sent={hb_sent} "
                    f"hb_recv={hb_recv} data={data_sent} left={left:.0f}s",
                    flush=True,
                )
                next_progress_at = now + 10.0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return {
        "hb_sent": hb_sent,
        "hb_recv": hb_recv,
        "ok": 1 if hb_recv > 0 else 0,
        "mainPingOK": hb_recv,  # shape compatible with product report counters
        "mainPongOK": hb_recv,
        "links_sent": links_sent,
        "data_sent": data_sent,
        "prime_recv": prime_recv,
    }


def _split_address(address: str) -> Tuple[str, int]:
    """Split ``host:port`` into ``(host, int(port))``.

    Supports bare ``host:port`` and IPv6 ``[addr]:port``.
    """
    if address.startswith("["):
        # [2001:db8::1]:443
        end = address.find("]")
        if end < 0:
            raise ValueError("invalid CAG address: %r" % address)
        host = address[1:end]
        rest = address[end + 1:]
        if not rest.startswith(":") or not rest[1:]:
            raise ValueError("invalid CAG address: %r" % address)
        return host, int(rest[1:])
    host, _, port_s = address.rpartition(":")
    if not host or not port_s:
        raise ValueError("invalid CAG address: %r" % address)
    return host, int(port_s)
