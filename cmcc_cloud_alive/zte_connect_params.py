#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pure-Python port of B's internal/zte/connect_params.go (line-by-line fork).

Parses the ZTE desktop connect string. The raw connect string is a hex
AES-ECB(VDI_KEY) ciphertext; once decrypted it yields a command-line of
flag tokens (``-h host -p port -k key --vmid ... --accessToken ...``),
possibly quoted, which is tokenized and parsed into a ConnectParams object.
"""

from dataclasses import dataclass
from urllib.parse import unquote

from .zte_security import decode_connect_string


class ConnectParams:
    def __init__(self):
        self.raw = ""
        self.args = {}
        self.host = ""
        self.port = 0
        self.key = ""
        self.vm_id = ""
        self.access_token = ""
        self.proxy_sport = 0
        self.vm_ip = ""

    def __repr__(self):
        return (
            "ConnectParams(host=%r, port=%d, key=%r, vm_id=%r, "
            "access_token=%r, proxy_sport=%d, vm_ip=%r)"
            % (self.host, self.port, self.key, self.vm_id,
               self.access_token, self.proxy_sport, self.vm_ip)
        )


# --- P6: inner/outer strict separation -------------------------------------
#
# ``InnerConnectParams`` is the FROZEN contract handed to the CAG transport
# layer (zte_cag) and the SPICE mux (worker-2's zte_cag_mux).  It carries only
# the *inner* (desktop-side) connection material decoded from connectStr —
# never the *outer* firm CAG host/port, which lives in ``OuterCAGTarget``
# (zte_route).  This split prevents the outer CAG endpoint from being
# accidentally fed into the inner SPICE link builder and vice-versa.
#
# Field order/types are frozen; do NOT rename without coordinating worker-2.
@dataclass(frozen=True)
class InnerConnectParams:
    host: str
    port: int
    key: str
    vm_id: str
    access_token: str
    proxy_sport: int
    vm_ip: str

    def __repr__(self):
        # Never leak key/access_token in repr — they are secrets.
        return (
            "InnerConnectParams(host=%r, port=%d, vm_id=%r, "
            "proxy_sport=%d, vm_ip=%r, key=<redacted>, access_token=<redacted>)"
            % (self.host, self.port, self.vm_id, self.proxy_sport, self.vm_ip)
        )


def inner_from_connect_params(cp: ConnectParams) -> InnerConnectParams:
    """Build the frozen inner contract from a parsed ConnectParams (P6-002)."""
    return InnerConnectParams(
        host=cp.host,
        port=cp.port,
        key=cp.key,
        vm_id=cp.vm_id,
        access_token=cp.access_token,
        proxy_sport=cp.proxy_sport,
        vm_ip=cp.vm_ip,
    )


def _split_command_line(s):
    """Port of B's splitCommandLine: shell-like tokenization with quote/escape support."""
    out = []
    b = []
    quote = ""
    escaped = False
    for r in s:
        if escaped:
            b.append(r)
            escaped = False
            continue
        if r == "\\":
            escaped = True
            continue
        if quote:
            if r == quote:
                quote = ""
            else:
                b.append(r)
            continue
        if r == "'" or r == '"':
            quote = r
        elif r in (" ", "\t", "\r", "\n"):
            if b:
                out.append("".join(b))
                b = []
        else:
            b.append(r)
    if escaped:
        b.append("\\")
    if quote:
        raise ValueError("unterminated quote in connectStr")
    if b:
        out.append("".join(b))
    return out


def _int_value(args, key, default=0):
    v = args.get(key, "")
    if v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def decode_connect_params(connect_str):
    """Port of B's DecodeConnectParams: AES-ECB decrypt then parse flag tokens."""
    plain = decode_connect_string(connect_str)
    return parse_connect_params(plain)


def parse_connect_params(raw):
    """Port of B's ParseConnectParams: tokenize + parse ``-h``/``-p``/``--vmid`` flag tokens.

    IPv6-only connectStr may omit ``-h``/``-p`` and only provide ``--hv6``/``--pv6``
    (ye4B6y live 2026-07-17). Prefer IPv4 when both present; else fall back to v6.
    """
    cp = ConnectParams()
    cp.raw = raw
    tokens = _split_command_line(raw)
    args = {}
    i = 0
    n = len(tokens)
    while i < n:
        key = tokens[i]
        if not key.startswith("-"):
            i += 1
            continue
        value = "true"
        if i + 1 < n and not tokens[i + 1].startswith("-"):
            i += 1
            value = tokens[i]
        # url.QueryUnescape — best effort, keep raw on failure (matches Go behaviour)
        try:
            value = unquote(value)
        except Exception:
            pass
        args[key] = value
        i += 1

    cp.args = args
    # Prefer IPv4 (-h/-p) when present; fall back to IPv6-only (--hv6/--pv6).
    # Align go-zte-hv6-probe firstNonEmpty(-h,--hv6) / (-p,--pv6) / (--vmip,--hv6).
    cp.host = args.get("-h", "") or args.get("--hv6", "") or ""
    cp.key = args.get("-k", "")
    cp.vm_id = args.get("--vmid", "")
    cp.access_token = args.get("--accessToken", "")
    cp.vm_ip = args.get("--vmip", "") or args.get("--hv6", "") or ""
    cp.port = _int_value(args, "-p", 0) or _int_value(args, "--pv6", 0)
    cp.proxy_sport = _int_value(args, "--proxy-sport", 0)

    if cp.host == "":
        raise ValueError("connectStr missing -h/--hv6 host")
    if cp.port == 0:
        raise ValueError("connectStr missing -p/--pv6 port")
    if cp.vm_id == "":
        raise ValueError("connectStr missing --vmid")
    return cp
