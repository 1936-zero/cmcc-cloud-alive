#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pure-Python China Mobile family cloud PC protocol client.

This file intentionally uses only Python's standard library. It mirrors the
UOS family-edition Electron client request path:

  app.asar -> src/main/request.js -> src/renderer/src/constants/index.js
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# --- proxy bypass: clear all proxy env vars so urllib never routes through
# clash/system proxy.  Go binary ignores these; Python urllib honours them by
# default, which breaks SCG/CAG requests when a proxy is active. ---
for _p in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_p, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


CONFIG = {
    "app_key": "a2c4f80ec311ce63d06a36e269111b505327e0fe9ddb74767e5ef63bc293c5ce",
    "app_secret_hex": "1ab7eb793c4aeafa5d6b32e4461183eaa16b531ff2b51de14d77c81ff6be8fa6",
    "base_url": "https://soho.komect.com",
    "terminal_prefix": "/terminal",
    "version": "2.23.1",
    "version_num": "2230100",
    "release_num": "1",
    "git_num": "176005e",
}

CLIENT_PROFILES = {
    "linux": dict(CONFIG, platform="Linux"),
    "windows": {
        "app_key": "b866539514246c187171f759ff409de25149407fcdada3c678a0c39c233cefb1",
        "app_secret_hex": "b5630ba3e5e95defd08306b2c1069c8b4b791098d726f107ad747a216f57eaf5",
        "base_url": CONFIG["base_url"],
        "terminal_prefix": CONFIG["terminal_prefix"],
        "version": "2.23.1",
        "version_num": "2230100",
        "release_num": "1",
        "git_num": "dd2313e",
        "platform": "Windows",
    },
    "mac": {
        "app_key": "ef80482854c2a2a36311a46011f3303f144bdf69b4b4223cf916f4c7f0f55135",
        "app_secret_hex": "cd58cf413dc43b07993f82f532b0f8e83d259d3ae2305de76811ccd1303853f7",
        "base_url": CONFIG["base_url"],
        "terminal_prefix": CONFIG["terminal_prefix"],
        "version": "2.18.25",
        "version_num": "2182500",
        "release_num": "1",
        "git_num": "fcc92ee",
        "platform": "Mac",
    },
}

# User-local data dir (NOT the project tree) so credentials never land inside
# a git working copy that might be zipped/shared/pushed by mistake.
DEFAULT_DATA_DIR = Path.home() / ".cmcc-cloud-alive"
DEFAULT_STATE = DEFAULT_DATA_DIR / "state.json"
DEFAULT_PROFILES_DIR = DEFAULT_DATA_DIR / "profiles"
SENSITIVE_REPORT_KEYS = {
    "accessToken",
    "authorization",
    "authPayload",
    "clientId",
    "connectStr",
    "cpsid",
    "jwt",
    "password",
    "sohoToken",
    "token",
}
DEFAULT_SOURCE_PATHS = [
    "/opt/yidongyun/client/opt/chuanyun-vdi-client/resources/app.asar",
]
ZTE_SECURITY_AES_KEY = b"56Acf4c3498fD4c5a0B1fb26947e2daB"
ZTE_SECURITY_AES_IV = b"3498fD4c5a0B1fbA"
DEFAULT_CSAP_ID_HEX = "7aaf97c5d34bf4473c24cc87c0a2d64d918329c1fa90a4442b784c1f2ff75f41"
CLIENTPED_KEY_SEED = "PublicKy"
CLIENTPED_IV = b"0000000000000000"


SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
INV_SBOX = [0] * 256
for _i, _value in enumerate(SBOX):
    INV_SBOX[_value] = _i
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


class CmccError(Exception):
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


def sanitize_report_value(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if str(key).lower() in {name.lower() for name in SENSITIVE_REPORT_KEYS}:
                sanitized[key] = "<redacted>"
            else:
                sanitized[key] = sanitize_report_value(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_report_value(item) for item in value]
    return value


def write_private_text(path, text):
    path = Path(os.path.expanduser(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def write_private_json_report(report, report_file):
    if not report_file:
        return
    path = Path(report_file)
    write_private_text(path, json.dumps(sanitize_report_value(report), ensure_ascii=False, indent=2) + "\n")


def shanghai_now():
    return datetime.now(timezone(timedelta(hours=8)))


def today_mmdd():
    return shanghai_now().strftime("%m%d")


def log_time():
    return shanghai_now().strftime("%Y-%m-%d %H:%M:%S")


def short_time():
    return shanghai_now().strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def state_path(args=None):
    """Return the state json path.

    args may be an argparse object, a raw string/path, or None.  The raw
    string/path support is important for the friendly REPL/profile flow where
    callers pass a selected json path directly.
    """
    if args is not None:
        if isinstance(args, (str, os.PathLike)):
            return Path(args)
        if getattr(args, "state", None):
            return Path(args.state)
    return Path(os.environ.get("CMCC_ALIVE_STATE", DEFAULT_STATE))


def load_state(args=None):
    path = state_path(args)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state, args=None):
    path = state_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def merge_state(patch, args=None):
    state = load_state(args)
    state.update(patch)
    save_state(state, args)
    return state


def default_device_id():
    host = socket.gethostname() or "linux"
    mac = uuid.getnode()
    if (mac >> 40) % 2:
        return host
    mac_text = ":".join(f"{(mac >> shift) & 0xff:02x}" for shift in range(40, -1, -8))
    return f"{host}-{mac_text}"


def rand_id(length=32):
    return "".join(random.choice("0123456789abcdef") for _ in range(length))


def json_dumps_compact(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def client_profile_name(state=None):
    name = (state or {}).get("clientProfile") or os.environ.get("CMCC_ALIVE_PROFILE") or "linux"
    name = str(name).strip().lower()
    if name not in CLIENT_PROFILES:
        raise CmccError(f"unknown client profile: {name}")
    return name


def client_config(state=None):
    state = state or {}
    cfg = dict(CLIENT_PROFILES[client_profile_name(state)])
    overrides = state.get("clientProfileConfig") if isinstance(state.get("clientProfileConfig"), dict) else {}
    key_map = {
        "appKey": "app_key",
        "appSecretHex": "app_secret_hex",
        "version": "version",
        "versionNum": "version_num",
        "releaseNum": "release_num",
        "gitNum": "git_num",
        "platform": "platform",
    }
    for source_key, target_key in key_map.items():
        if overrides.get(source_key):
            cfg[target_key] = overrides[source_key]
    return cfg


def profile_device_id(state, cfg):
    return (
        state.get("clientDeviceId")
        or state.get("deviceId")
        or cfg.get("device_id")
        or default_device_id()
    )


def profile_app_type(state, cfg, device_id):
    if state.get("clientAppType"):
        return state["clientAppType"]
    if state.get("appType"):
        return state["appType"]
    platform = cfg.get("platform") or "Linux"
    if platform.lower() == "windows":
        return f"windows|10.0.22631|Windows-PC|1|-1|{device_id}|"
    if platform.lower() == "mac":
        return f"mac|24.2.0|Mac|1|-1|{device_id}|"
    return f"{platform}|{cfg['version']}|{platform}|-1|-1|{device_id}|"


def profile_rom_version(state, cfg):
    if state.get("clientRomVersion"):
        return state["clientRomVersion"]
    if state.get("romVersion"):
        return state["romVersion"]
    platform = cfg.get("platform") or "Linux"
    if platform.lower() == "windows":
        return "Microsoft Windows-10.0.22631"
    if platform.lower() == "mac":
        return "Apple Inc.-24.2.0"
    return f"{platform}-{cfg['version']}"


def profile_user_agent(state, cfg):
    if state.get("clientUserAgent"):
        return state["clientUserAgent"]
    platform = cfg.get("platform") or "Linux"
    return f"jtydn-{platform}-{cfg['version']}({cfg['release_num']}.{cfg['git_num']}.{today_mmdd()})"


def create_sign(method, url_path, header, body, config=None):
    parts = []
    for key, value in header.items():
        if value:
            parts.append(f"{key}={value}")
    signing = f"{method}&{url_path}&{'&'.join(parts)}"
    encoded = json_dumps_compact(body or {})
    if encoded and encoded != "{}":
        if "{" in encoded:
            parsed = json.loads(encoded)
            signing += f"&body={parsed['data']}"
        else:
            signing += f"&{encoded}"
    key = bytes.fromhex((config or CONFIG)["app_secret_hex"])
    return hmac.new(key, signing.encode("utf-8"), hashlib.sha256).hexdigest()


def read_len(data, pos):
    first = data[pos]
    pos += 1
    if first < 0x80:
        return first, pos
    count = first & 0x7F
    value = int.from_bytes(data[pos:pos + count], "big")
    return value, pos + count


def read_tlv(data, pos):
    tag = data[pos]
    pos += 1
    length, pos = read_len(data, pos)
    value = data[pos:pos + length]
    return tag, value, pos + length


def parse_int(value):
    return int.from_bytes(value.lstrip(b"\x00"), "big")


def parse_rsa_public_key(public_key_body):
    der = base64.b64decode("".join(str(public_key_body).strip().split()))
    tag, outer, pos = read_tlv(der, 0)
    if tag != 0x30 or pos != len(der):
        raise CmccError("invalid RSA public key DER")

    pos = 0
    tag, first, pos = read_tlv(outer, pos)
    if tag == 0x30:
        tag, bit_string, pos = read_tlv(outer, pos)
        if tag != 0x03 or not bit_string:
            raise CmccError("invalid SubjectPublicKeyInfo")
        rsa_der = bit_string[1:]
        tag, rsa_seq, rsa_pos = read_tlv(rsa_der, 0)
        if tag != 0x30 or rsa_pos != len(rsa_der):
            raise CmccError("invalid RSA key sequence")
    elif tag == 0x02:
        rsa_seq = outer
        pos = 0
    else:
        raise CmccError("unsupported RSA public key format")

    pos = 0
    tag, modulus_bytes, pos = read_tlv(rsa_seq, pos)
    if tag != 0x02:
        raise CmccError("missing RSA modulus")
    tag, exponent_bytes, pos = read_tlv(rsa_seq, pos)
    if tag != 0x02:
        raise CmccError("missing RSA exponent")
    modulus = parse_int(modulus_bytes)
    exponent = parse_int(exponent_bytes)
    key_len = (modulus.bit_length() + 7) // 8
    return modulus, exponent, key_len


def rsa_no_padding_encrypt_bytes(raw, public_key_body, chunk_size=None):
    modulus, exponent, key_len = parse_rsa_public_key(public_key_body)
    if chunk_size is None:
        chunk_size = key_len - 11
    out = bytearray()
    for offset in range(0, len(raw), chunk_size):
        chunk = raw[offset:offset + chunk_size]
        if len(chunk) > key_len:
            raise CmccError("RSA chunk too large")
        padded = b"\x00" * (key_len - len(chunk)) + chunk
        encrypted = pow(int.from_bytes(padded, "big"), exponent, modulus)
        out.extend(encrypted.to_bytes(key_len, "big"))
    return base64.b64encode(bytes(out)).decode("ascii")


def rsa_pkcs1_v15_encrypt_b64(text, public_key_body):
    """PKCS#1 v1.5 RSA encryption returning base64 of raw encrypted bytes.

    Mirrors Go's rsa.EncryptPKCS1v15:
        EM = 0x00 || 0x02 || PS (random non-zero) || 0x00 || M
    where PS length = key_len - len(M) - 3.
    """
    raw = str(text).encode("utf-8")
    modulus, exponent, key_len = parse_rsa_public_key(public_key_body)
    if len(raw) > key_len - 11:
        raise CmccError(f"RSA plaintext too long ({len(raw)} > {key_len - 11}) for PKCS1v1.5")
    ps_len = key_len - len(raw) - 3
    ps = bytearray()
    while len(ps) < ps_len:
        b = secrets.randbelow(255) + 1
        ps.append(b)
    em = b"\x00\x02" + bytes(ps) + b"\x00" + raw
    ct = pow(int.from_bytes(em, "big"), exponent, modulus)
    ct_bytes = ct.to_bytes(key_len, "big")
    return base64.b64encode(ct_bytes).decode("ascii")


def rsa_encrypt_body(data, public_key_body):
    raw = json_dumps_compact(data).encode("utf-8")
    return {"data": rsa_no_padding_encrypt_bytes(raw, public_key_body, 117)}


def rsa_encrypt_string(text, public_key_body):
    return rsa_no_padding_encrypt_bytes(str(text).encode("utf-8"), public_key_body, 128)


def xtime(value):
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def gf_mul(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = xtime(a)
        b >>= 1
    return result


def key_expansion(key):
    if len(key) not in (16, 24, 32):
        raise CmccError("AES key must be 16, 24, or 32 bytes")
    nk = len(key) // 4
    nr = nk + 6
    words = [list(key[i:i + 4]) for i in range(0, len(key), 4)]
    for i in range(nk, 4 * (nr + 1)):
        temp = words[i - 1][:]
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            temp = [SBOX[b] for b in temp]
        words.append([a ^ b for a, b in zip(words[i - nk], temp)])
    return [sum(words[i:i + 4], []) for i in range(0, len(words), 4)], nr


def add_round_key(state, round_key):
    for col in range(4):
        for row in range(4):
            state[row][col] ^= round_key[col * 4 + row]


def inv_shift_rows(state):
    state[1] = state[1][-1:] + state[1][:-1]
    state[2] = state[2][-2:] + state[2][:-2]
    state[3] = state[3][-3:] + state[3][:-3]


def inv_sub_bytes(state):
    for row in range(4):
        for col in range(4):
            state[row][col] = INV_SBOX[state[row][col]]


def inv_mix_columns(state):
    for col in range(4):
        s0, s1, s2, s3 = [state[row][col] for row in range(4)]
        state[0][col] = gf_mul(s0, 14) ^ gf_mul(s1, 11) ^ gf_mul(s2, 13) ^ gf_mul(s3, 9)
        state[1][col] = gf_mul(s0, 9) ^ gf_mul(s1, 14) ^ gf_mul(s2, 11) ^ gf_mul(s3, 13)
        state[2][col] = gf_mul(s0, 13) ^ gf_mul(s1, 9) ^ gf_mul(s2, 14) ^ gf_mul(s3, 11)
        state[3][col] = gf_mul(s0, 11) ^ gf_mul(s1, 13) ^ gf_mul(s2, 9) ^ gf_mul(s3, 14)


def aes_decrypt_block(block, round_keys, nr):
    if len(block) != 16:
        raise CmccError("AES block must be 16 bytes")
    state = [[block[row + 4 * col] for col in range(4)] for row in range(4)]
    add_round_key(state, round_keys[nr])
    for round_index in range(nr - 1, 0, -1):
        inv_shift_rows(state)
        inv_sub_bytes(state)
        add_round_key(state, round_keys[round_index])
        inv_mix_columns(state)
    inv_shift_rows(state)
    inv_sub_bytes(state)
    add_round_key(state, round_keys[0])
    return bytes(state[row][col] for col in range(4) for row in range(4))


def aes_ecb_decrypt(data, key):
    if len(data) % 16:
        raise CmccError("AES input must be block aligned")
    round_keys, nr = key_expansion(key)
    return b"".join(aes_decrypt_block(data[i:i + 16], round_keys, nr) for i in range(0, len(data), 16))


def aes_cbc_decrypt(data, key, iv):
    if len(iv) != 16:
        raise CmccError("AES CBC IV must be 16 bytes")
    previous = iv
    out = bytearray()
    round_keys, nr = key_expansion(key)
    for offset in range(0, len(data), 16):
        block = data[offset:offset + 16]
        plain = aes_decrypt_block(block, round_keys, nr)
        out.extend(a ^ b for a, b in zip(plain, previous))
        previous = block
    return bytes(out)


def pkcs7_unpad(data):
    if not data:
        raise CmccError("empty PKCS#7 payload")
    pad = data[-1]
    if pad < 1 or pad > 16 or data[-pad:] != bytes([pad]) * pad:
        raise CmccError("invalid PKCS#7 padding")
    return data[:-pad]


def parse_cag_rsa_public_key(text):
    n_match = re.search(r"\bN\s*=\s*([0-9a-fA-F]+)", str(text or ""))
    e_match = re.search(r"\bE\s*=\s*([0-9a-fA-F]+)", str(text or ""))
    if not n_match or not e_match:
        raise CmccError("CAG RSA public key must contain N = <hex> and E = <hex>")
    modulus = int(n_match.group(1), 16)
    exponent = int(e_match.group(1), 16)
    key_len = (modulus.bit_length() + 7) // 8
    return modulus, exponent, key_len


def rsa_pkcs1_v15_encrypt(text, cag_public_key_text):
    raw = str(text).encode("utf-8")
    modulus, exponent, key_len = parse_cag_rsa_public_key(cag_public_key_text)
    if len(raw) > key_len - 11:
        raise CmccError("CAG RSA plaintext too long")
    ps_len = key_len - len(raw) - 3
    padding = bytearray()
    while len(padding) < ps_len:
        b = secrets.randbelow(255) + 1
        padding.append(b)
    encoded = b"\x00\x02" + bytes(padding) + b"\x00" + raw
    encrypted = pow(int.from_bytes(encoded, "big"), exponent, modulus).to_bytes(key_len, "big")
    password = base64.b64encode(encrypted.hex().upper().encode("ascii")).decode("ascii")
    return password, key_len


def decrypt_zte_security_params(hex_text):
    hex_value = str(hex_text or "").strip()
    if not hex_value or not re.fullmatch(r"[0-9a-fA-F]+", hex_value) or len(hex_value) % 32:
        raise CmccError("ZTE_Security_Params must be AES-CBC hex with block-aligned length")
    plain = aes_cbc_decrypt(bytes.fromhex(hex_value), ZTE_SECURITY_AES_KEY, ZTE_SECURITY_AES_IV)
    return json.loads(pkcs7_unpad(plain).decode("utf-8"))


def decode_cag_security_json(value):
    encrypted = (value or {}).get("ZTE_Security_Params")
    if not isinstance(encrypted, str) or not encrypted:
        return None
    return decrypt_zte_security_params(encrypted)


def get_decoded_connect_info(decoded):
    if not isinstance(decoded, dict):
        return None
    connect_info = decoded.get("connectInfo")
    if isinstance(connect_info, dict):
        return connect_info
    if any(key in decoded for key in ("connectStr", "vmStatus", "asyncQueryTimeInterval")):
        return decoded
    return None


def strip_nul_padding(text):
    return str(text or "").rstrip("\x00")


def client_ped_key_proc(seed=CLIENTPED_KEY_SEED):
    text = str(seed)
    normalized = text.rjust(8, "0") if len(text) <= 8 else text[8:]
    even = 0
    odd = 0
    for index, char in enumerate(normalized):
        value = ord(char) - 0x30
        if index % 2 == 0:
            even += value
        else:
            odd += value
    return f"{normalized}{even + odd:04d}{abs(even - odd):04d}"


def decrypt_client_ped_hex_by_key(encrypted_hex):
    data = bytes.fromhex(str(encrypted_hex or "").strip())
    key = client_ped_key_proc().encode("utf-8")
    return strip_nul_padding(aes_cbc_decrypt(data, key, CLIENTPED_IV).decode("utf-8", errors="replace"))


def tokenize_command_line(text):
    tokens = []
    current = []
    quote = ""
    escaped = False
    for char in str(text or ""):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if current:
        tokens.append("".join(current))
    return tokens


def parse_connect_str_args(connect_str):
    tokens = tokenize_command_line(connect_str)
    args = {"_": []}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            args["_"].append(token)
            i += 1
            continue
        key = token.lstrip("-")
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if not nxt or nxt.startswith("-"):
            args[key] = True
            i += 1
        else:
            args[key] = nxt
            i += 2
    return args


def decode_csap_connect_str(encrypted_connect_str_hex):
    hex_value = str(encrypted_connect_str_hex or "").strip()
    if not hex_value or not re.fullmatch(r"[0-9a-fA-F]+", hex_value) or len(hex_value) % 32:
        raise CmccError("connectStr must be AES block-aligned hex")
    csap_plain = decrypt_client_ped_hex_by_key(DEFAULT_CSAP_ID_HEX)
    key = csap_plain.encode("utf-8")[:16]
    if len(key) != 16:
        raise CmccError("decoded csap key must provide 16 bytes")
    plain = aes_ecb_decrypt(bytes.fromhex(hex_value), key)
    return strip_nul_padding(plain.decode("utf-8", errors="replace"))


def summarize_connect_str(encrypted_connect_str_hex):
    plain = decode_csap_connect_str(encrypted_connect_str_hex)
    args = parse_connect_str_args(plain)
    return {
        "plainLength": len(plain),
        "sha256": hashlib.sha256(plain.encode("utf-8")).hexdigest(),
        "summary": {
            "host": args.get("h") or "",
            "port": int(args.get("p") or 0),
            "vmid": args.get("vmid") or "",
            "type": args.get("type") or "",
            "serverType": args.get("server-type") or "",
            "keyPresent": bool(args.get("k")),
            "accessTokenPresent": bool(args.get("accessToken")),
            "cpsidPresent": bool(args.get("cpsid")),
        },
    }


def summarize_decoded_cag_json(decoded):
    connect_info = get_decoded_connect_info(decoded)
    connect_str_decoded = None
    if isinstance(connect_info, dict) and connect_info.get("connectStr"):
        try:
            connect_str_decoded = summarize_connect_str(connect_info["connectStr"])
        except Exception as err:
            connect_str_decoded = {"error": str(err)}
    return {
        "result": decoded.get("result") if isinstance(decoded, dict) else None,
        "mesg": decoded.get("mesg") if isinstance(decoded, dict) else None,
        "success": decoded.get("success") if isinstance(decoded, dict) else None,
        "hasConnectInfo": isinstance(connect_info, dict),
        "connectInfo": {
            "result": connect_info.get("result"),
            "msg": connect_info.get("msg"),
            "mesg": connect_info.get("mesg"),
            "success": connect_info.get("success"),
            "protocol": connect_info.get("protocol"),
            "vmStatus": connect_info.get("vmStatus"),
            "hasConnectStr": bool(connect_info.get("connectStr")),
            "connectStrLength": len(connect_info.get("connectStr") or ""),
            "connectStrSha256": hashlib.sha256(str(connect_info.get("connectStr") or "").encode("utf-8")).hexdigest() if connect_info.get("connectStr") else None,
            "connectStrDecoded": connect_str_decoded,
            "asyncQueryTimeInterval": connect_info.get("asyncQueryTimeInterval"),
            "accessTokenPresent": bool(connect_info.get("accessToken")),
            "cpsidPresent": bool(connect_info.get("cpsid")),
        } if isinstance(connect_info, dict) else None,
    }


def headers(state, url_path, method, body):
    cfg = client_config(state)
    device_id = profile_device_id(state, cfg)
    header = {
        "X-SOHO-AppKey": cfg["app_key"],
        "X-SOHO-AppType": profile_app_type(state, cfg, device_id),
        "X-SOHO-ClientVersion": cfg["version"],
        "X-SOHO-DeviceId": device_id,
        "X-SOHO-RomVersion": profile_rom_version(state, cfg),
        "X-SOHO-SohoToken": state.get("sohoToken") or "",
        "X-SOHO-Timestamp": str(int(time.time() * 1000)),
        "X-SOHO-UserId": state.get("userId") or "",
        "X-SOHO-Uuid": rand_id(32),
        "X-SOHO-VersionNum": cfg["version_num"],
    }
    all_headers = {
        "Content-Type": "application/json",
        "User-Agent": profile_user_agent(state, cfg),
    }
    all_headers.update(header)
    all_headers["X-SOHO-Signature"] = create_sign(method, url_path, header, body, cfg)
    return all_headers


def api_request(url_path, data=None, args=None, timeout=30, state_override=None):
    state = dict(state_override) if state_override is not None else load_state(args)
    body = None
    if data is not None:
        if not state.get("publicKey"):
            if state_override is not None:
                raise CmccError("missing publicKey in explicit state override")
            ensure_public_key(args)
            state = load_state(args)
        body = rsa_encrypt_body(data, state["publicKey"])

    method = "POST"
    full_url = CONFIG["base_url"] + CONFIG["terminal_prefix"] + url_path
    payload = json_dumps_compact(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(full_url, data=payload, method=method)
    for key, value in headers(state, url_path, method, body).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as res:
            raw = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        raise CmccError(f"HTTP {err.code}: {raw[:300]}")
    except urllib.error.URLError as err:
        raise CmccError(f"network failed: {err.reason}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise CmccError(f"non-json response: {raw[:300]}") from err


def parse_json_argument(value):
    if value is None or value == "":
        return None
    text = str(value)
    if text.startswith("@"):
        with Path(text[1:]).open("r", encoding="utf-8") as f:
            text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        raise CmccError(f"invalid JSON argument: {err}") from err


def api_probe(args):
    path = str(args.path or "")
    if path.startswith(CONFIG["base_url"]):
        parsed = urllib.parse.urlsplit(path)
        path = parsed.path
    if path.startswith(CONFIG["terminal_prefix"] + "/"):
        path = path[len(CONFIG["terminal_prefix"]):]
    if not path.startswith("/"):
        path = "/" + path
    data = parse_json_argument(args.json)
    response = api_request(path, data, args, timeout=int(args.timeout))
    print(json.dumps({
        "ok": True,
        "path": path,
        "sentEncryptedBody": data is not None,
        "response": response,
    }, ensure_ascii=False, indent=2))


def assert_ok(response, label):
    if int(response.get("code", 0)) == 2000 or response.get("msg") == "SUCCESS":
        return response
    raise CmccError(f"{label} failed: code={response.get('code')} msg={response.get('msg')}", response=response)


def ensure_public_key(args=None):
    state = load_state(args)
    if state.get("publicKey"):
        return state["publicKey"]
    response = api_request("/login/encryptKey/v1", None, args)
    assert_ok(response, "encryptKey")
    merge_state({"publicKey": response["data"], "deviceId": state.get("deviceId") or default_device_id()}, args)
    return response["data"]


def password_login(args):
    ensure_public_key(args)
    pub = assert_ok(api_request("/login/publicKey/v1", {"type": 1}, args), "loginPublicKey")["data"]
    encrypted_password = rsa_encrypt_string(args.password, pub)
    response = api_request("/login/namePwdLogin/v1", {
        "username": args.username,
        "password": encrypted_password,
        "verificationCode": args.verification_code or "",
        "randomCode": args.random_code or "",
    }, args)
    assert_ok(response, "passwordLogin")
    user = response.get("data") or {}
    merge_state({
        "userId": user.get("userId"),
        "nickname": user.get("nickname") or "",
        "phone": user.get("phone") or "",
        "sohoToken": user.get("sohoToken"),
        "username": user.get("username") or args.username,
        "isLogined": True,
        "isSubAccount": False,
        "loginMode": "password",
    }, args)
    print("login ok")


def sub_password_login(args):
    """Sub-account password login via /login/home/namePwdLogin/v1.

    Mirrors Go soho.SubAccountPasswordLogin: same RSA password encryption as the
    main-account path, but the account field is ``subAccount`` and the path is
    under ``/login/home/``.
    """
    ensure_public_key(args)
    pub = assert_ok(api_request("/login/publicKey/v1", {"type": 1}, args), "loginPublicKey")["data"]
    encrypted_password = rsa_encrypt_string(args.password, pub)
    response = api_request("/login/home/namePwdLogin/v1", {
        "subAccount": args.username,
        "password": encrypted_password,
        "verificationCode": getattr(args, "verification_code", None) or "",
        "randomCode": getattr(args, "random_code", None) or "",
    }, args)
    assert_ok(response, "subPasswordLogin")
    user = response.get("data") or {}
    merge_state({
        "userId": user.get("userId"),
        "nickname": user.get("nickname") or "",
        "phone": user.get("phone") or "",
        "sohoToken": user.get("sohoToken"),
        "username": user.get("username") or user.get("subAccount") or args.username,
        "isLogined": True,
        "isSubAccount": True,
        "loginMode": "sub_password",
    }, args)
    print("sub-account login ok")


def protocol_check(args):
    state = load_state(args)
    device_id = state.get("deviceId") or default_device_id()
    base_state = dict(state)
    base_state.update({
        "deviceId": device_id,
        "publicKey": "",
        "sohoToken": "",
        "userId": "",
    })
    encrypt_key = api_request("/login/encryptKey/v1", None, args, state_override=base_state)
    assert_ok(encrypt_key, "encryptKey")
    transport_public_key = encrypt_key.get("data") or ""
    encrypted_state = dict(base_state)
    encrypted_state["publicKey"] = transport_public_key
    login_public_key = api_request("/login/publicKey/v1", {"type": 1}, args, state_override=encrypted_state)
    assert_ok(login_public_key, "loginPublicKey")
    report = {
        "ok": True,
        "familyEdition": True,
        "baseUrl": CONFIG["base_url"] + CONFIG["terminal_prefix"],
        "signature": "HMAC-SHA256 over method, path, X-SOHO headers, and encrypted body",
        "bodyEncryption": "RSA-1024 NO_PADDING, 117-byte JSON chunks",
        "deviceHeaderAccepted": True,
        "milestones": [
            {
                "endpoint": "/login/encryptKey/v1",
                "proves": "X-SOHO signature headers accepted by family SOHO gateway",
                "code": encrypt_key.get("code"),
                "msg": encrypt_key.get("msg"),
                "publicKeyBytes": len(base64.b64decode("".join(str(transport_public_key).split()))),
            },
            {
                "endpoint": "/login/publicKey/v1",
                "proves": "RSA encrypted request body accepted and decrypted by server",
                "code": login_public_key.get("code"),
                "msg": login_public_key.get("msg"),
                "publicKeyBytes": len(base64.b64decode("".join(str(login_public_key.get("data") or "").split()))),
            },
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


def list_clouds(args):
    response = api_request("/cc/cloudPc/list/v6", {"pageNum": 1}, args)
    assert_ok(response, "listClouds")
    items = (response.get("data") or {}).get("list") or []
    merge_state({"cloudList": items, "lastCloudListAt": shanghai_now().isoformat()}, args)
    return items


def print_list(args):
    items = list_clouds(args)
    if not items:
        print("no cloud PC found")
        return
    for index, item in enumerate(items):
        print(f"{index}: userServiceId={item.get('userServiceId')} vmName={item.get('vmName') or ''} spuCode={item.get('spuCode') or ''} sku={item.get('skuName') or ''} status={item.get('vmStatusShow') or item.get('vmStatus')}")


def resolve_user_service_id(args):
    if getattr(args, "user_service_id", None):
        return str(args.user_service_id)
    state = load_state(args)
    items = state.get("cloudList") or list_clouds(args)
    if items and items[0].get("userServiceId"):
        return str(items[0]["userServiceId"])
    raise CmccError("no userServiceId found; run list first or pass one")


def cloud_status(args):
    target = resolve_user_service_id(args)
    for item in list_clouds(args):
        if str(item.get("userServiceId")) == target:
            return item
    raise CmccError(f"userServiceId not found: {target}")


def print_cloud_status(args):
    print(json.dumps(cloud_status(args), ensure_ascii=False, indent=2))


def get_firm_auth(args):
    user_service_id = resolve_user_service_id(args)
    response = api_request("/cc/getFirmAuth/v1", {"userServiceId": user_service_id}, args)
    assert_ok(response, "getFirmAuth")
    auth = response.get("data") or {}
    vm_id = auth.get("vmId") or auth.get("vmID") or auth.get("uuid") or ""
    merge_state({
        "lastFirmAuthAt": shanghai_now().isoformat(),
        "lastFirmAuthUserServiceId": user_service_id,
        "lastVmId": vm_id,
        "lastSpuCode": auth.get("spuCode") or "",
        "lastFirmAuthSummary": {
            "vmId": vm_id,
            "spuCode": auth.get("spuCode") or "",
            "hasVmUserName": bool(auth.get("vmUserName")),
            "hasVmPassword": bool(auth.get("vmPassword")),
            "hasConnectId": bool(auth.get("connectId")),
        },
    }, args)
    return auth


def mask(value):
    if value is None or value == "":
        return value
    text = str(value)
    if len(text) <= 8:
        return "***"
    return text[:4] + "***" + text[-4:]


def print_firm_auth(args):
    auth = get_firm_auth(args)
    safe = {}
    for key, value in auth.items():
        safe[key] = mask(value) if key in {"vmPassword", "scAuthCode", "bizCode", "connectId", "token", "accessToken"} else value
    print(json.dumps(safe, ensure_ascii=False, indent=2))


def first_external_ipv4():
    return "127.0.0.1", "00-00-00-00-00-00"


def format_mac(mac):
    text = str(mac or "").replace(":", "-").upper()
    return text if text and text != "00-00-00-00-00-00" else "00-00-00-00-00-00"


def cag_auth_value(auth, *names):
    for name in names:
        value = auth.get(name)
        if value:
            return value
    return ""


def cag_https_request(auth, path, body="", timeout=15):
    host = auth.get("cagIp")
    port = int(auth.get("cagPort") or 8899)
    if not host or not port:
        raise CmccError("CAG host/port is required")
    vmc_ip = auth.get("vmcIp") or auth.get("vmcIP")
    vmc_port = auth.get("vmcPort") or auth.get("vmcPORT")
    payload = str(body or "").encode("utf-8")
    req = urllib.request.Request(f"https://{host}:{port}{path}", data=payload, method="POST")
    headers_map = {
        "Accept": "*/*",
        "Content-Type": "application/xml",
        "X-Ap-sHost": f"{vmc_ip}:{vmc_port}",
        "otlp_parent_id": secrets.token_hex(8),
        "otlp_trace_id": secrets.token_hex(16),
        "process_id": "2",
        "serialNum": str(uuid.uuid4()),
        "Content-Length": str(len(payload)),
    }
    for key, value in headers_map.items():
        req.add_header(key, value)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as res:
            raw = res.read().decode("utf-8", errors="replace")
            return {
                "statusCode": res.status,
                "headers": dict(res.headers.items()),
                "body": raw,
            }
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        return {
            "statusCode": err.code,
            "headers": dict(err.headers.items()),
            "body": raw,
        }
    except urllib.error.URLError as err:
        raise CmccError(f"CAG HTTPS network failed: {err.reason}")
    except TimeoutError as err:
        raise CmccError(f"CAG HTTPS request timed out: {path}") from err
    except OSError as err:
        raise CmccError(f"CAG HTTPS socket failed: {err}") from err


def summarize_cag_response(response):
    try:
        body_json = json.loads(response.get("body") or "{}")
    except json.JSONDecodeError:
        body_json = None
    decoded = decode_cag_security_json(body_json) if body_json else None
    summary = {
        "statusCode": response.get("statusCode"),
        "bodyLength": len(response.get("body") or ""),
        "decoded": summarize_decoded_cag_json(decoded) if decoded else None,
    }
    connect_info = get_decoded_connect_info(decoded)
    if isinstance(connect_info, dict) and connect_info.get("connectStr"):
        summary["businessOk"] = decoded.get("result") == "0" and decoded.get("success") is True and connect_info.get("result") == "0"
    elif decoded:
        summary["businessOk"] = decoded.get("result") == "0" and decoded.get("success") is True
    return summary, decoded


def create_cag_connect_desktop_body(auth, rsa_public_key, args):
    address, mac = first_external_ipv4()
    encrypted_password, encrypted_bytes = rsa_pkcs1_v15_encrypt(auth.get("vmPassword"), rsa_public_key)
    return {
        "body": {
            "RspSecurity": 1,
            "SNcode": str(uuid.uuid4()),
            "allowExtUSBPolicy": 1,
            "allowSwitchRap": 1,
            "clientIp": getattr(args, "client_ip", None) or address,
            "clienttype": 0,
            "diskNo": str(uuid.uuid4()),
            "encrypt": 5,
            "encryption": "1",
            "hardware": 4,
            "hostName": getattr(args, "host_name", None) or socket.gethostname(),
            "isvm": 0,
            "language": "zh",
            "localipandmac": f"{getattr(args, 'client_ip', None) or address},{format_mac(getattr(args, 'mac', None) or mac)}",
            "mac": format_mac(getattr(args, "mac", None) or mac),
            "netType": 2,
            "netflags": 1,
            "newcharsetparse": "1",
            "newpara": 1,
            "ostype": 5,
            "password": encrypted_password,
            "prover": 1,
            "raptype": 2,
            "requestFrom": 5,
            "supportAsync": 1,
            "supportCustomConfig": "00000000000000000000000000000011",
            "type": 0,
            "upmnew": 1,
            "username": auth.get("vmUserName"),
            "uuid": "",
            "verifyTerminalBind": "11",
            "version": getattr(args, "version", None) or "V7.25.40-HY",
            "vmid": cag_auth_value(auth, "vmId", "vmID", "uuid"),
            "watermarkType": 1,
        },
        "encryptedBytes": encrypted_bytes,
        "passwordLength": len(encrypted_password),
    }


def create_cag_async_query_path(auth, decoded):
    connect_info = get_decoded_connect_info(decoded) or {}
    token_info = decoded.get("tokenInfo") if isinstance(decoded, dict) else {}
    access_token = connect_info.get("accessToken") or (token_info or {}).get("accessToken")
    if not access_token:
        raise CmccError("CAG async query accessToken is required")
    vmid = connect_info.get("vmId") or cag_auth_value(auth, "vmId", "vmID", "uuid")
    if not vmid:
        raise CmccError("CAG async query vmid is required")
    return f"/cs/cs_startDesktop_async_query.action?accessToken={urllib.parse.quote(str(access_token))}&language=zh&isvm=0&vmid={urllib.parse.quote(str(vmid))}&RspSecurity=1&prover=1&allowSwitchRap=1"


def wait_for_cag_connect_str(auth, initial_decoded, args):
    connect_info = get_decoded_connect_info(initial_decoded)
    if isinstance(connect_info, dict) and connect_info.get("connectStr"):
        return []
    token_info = initial_decoded.get("tokenInfo") if isinstance(initial_decoded, dict) else {}
    if not ((connect_info or {}).get("accessToken") or (token_info or {}).get("accessToken")):
        return []
    max_wait = max(0, int(getattr(args, "boot_wait", 180)))
    if max_wait <= 0:
        return []
    started = time.time()
    attempts = []
    decoded = initial_decoded
    attempt = 0
    while time.time() - started < max_wait:
        info = get_decoded_connect_info(decoded) or connect_info or {}
        wait_for = max(1, int(info.get("asyncQueryTimeInterval") or 5))
        time.sleep(min(wait_for, max(0, max_wait - (time.time() - started))))
        if time.time() - started >= max_wait:
            break
        attempt += 1
        path = create_cag_async_query_path(auth, initial_decoded)
        response = cag_https_request(auth, path, "", timeout=int(getattr(args, "timeout", 15)))
        summary, decoded = summarize_cag_response(response)
        attempts.append({
            "attempt": attempt,
            "waitedSeconds": int(time.time() - started),
            "response": summary,
        })
        info = get_decoded_connect_info(decoded)
        if isinstance(info, dict) and info.get("connectStr"):
            break
    return attempts


def cag_https_connect_report(auth, args):
    version = getattr(args, "version", None) or "V7.25.40-HY"
    username = auth.get("vmUserName")
    if not username or not auth.get("vmPassword"):
        raise CmccError("firm-auth response is missing vmUserName/vmPassword")
    sys_path = f"/cs/cs_sysConfig.action?version={urllib.parse.quote(version)}&language=zh&requestFrom=5&name={urllib.parse.quote(str(username))}&RspSecurity=1"
    sys_response = cag_https_request(auth, sys_path, "", timeout=int(getattr(args, "timeout", 15)))
    sys_summary, sys_decoded = summarize_cag_response(sys_response)
    rsa_public_key = (sys_decoded or {}).get("rsapub")
    if not rsa_public_key:
        raise CmccError("CAG sysConfig did not include rsapub")
    connect_payload = create_cag_connect_desktop_body(auth, rsa_public_key, args)
    connect_response = cag_https_request(auth, "/cs/cs_connectDesktop.action", json_dumps_compact(connect_payload["body"]), timeout=int(getattr(args, "timeout", 15)))
    connect_summary, connect_decoded = summarize_cag_response(connect_response)
    async_queries = wait_for_cag_connect_str(auth, connect_decoded, args) if connect_decoded else []
    final_connect = async_queries[-1]["response"] if async_queries else connect_summary
    return {
        "sdkStarted": False,
        "route": "cag-https-python",
        "host": auth.get("cagIp"),
        "port": int(auth.get("cagPort") or 8899),
        "passwordTransform": {
            "rsaPadding": "RSA_PKCS1_PADDING",
            "encryptedBytes": connect_payload["encryptedBytes"],
            "passwordLength": connect_payload["passwordLength"],
            "source": "sysConfig.rsapub",
        },
        "sysConfig": sys_summary,
        "connect": connect_summary,
        "asyncQueries": async_queries,
        "finalConnect": final_connect,
    }


def cag_https_connect(args):
    auth = get_firm_auth(args)
    report = cag_https_connect_report(auth, args)
    print(json.dumps({
        "userServiceId": resolve_user_service_id(args),
        "report": report,
    }, ensure_ascii=False, indent=2))


def heartbeat(args):
    user_service_id = resolve_user_service_id(args)
    response = api_request("/cc/cloudPc/heartbeat/v2", {"userServiceId": user_service_id}, args)
    if int(response.get("code", 0)) == 4043 or int(response.get("businessCode") or 0) == 4043:
        raise CmccError("heartbeat returned other-login/recycled response", response=response)
    merge_state({
        "lastHeartbeatAt": shanghai_now().isoformat(),
        "lastHeartbeatUserServiceId": user_service_id,
        "lastHeartbeatResponse": {
            "code": response.get("code"),
            "msg": response.get("msg"),
            "businessCode": response.get("businessCode") or "",
        },
    }, args)
    return response


def print_heartbeat(args):
    response = heartbeat(args)
    print(json.dumps(response, ensure_ascii=False, indent=2))


def alive_once(args):
    user_service_id = resolve_user_service_id(args)
    auth = get_firm_auth(args)
    hb = heartbeat(args)
    status = None
    try:
        status = cloud_status(args)
    except CmccError:
        pass
    return {
        "userServiceId": user_service_id,
        "firmAuth": {
            "vmId": auth.get("vmId") or auth.get("vmID") or "",
            "spuCode": auth.get("spuCode") or "",
            "hasConnectMaterial": bool(auth.get("vmUserName") and auth.get("vmPassword")),
        },
        "heartbeat": {
            "code": hb.get("code"),
            "msg": hb.get("msg"),
            "businessCode": hb.get("businessCode") or "",
        },
        "status": {
            "vmStatus": status.get("vmStatus") if status else None,
            "vmStatusShow": status.get("vmStatusShow") if status else None,
        },
    }


def print_alive_once(args):
    print(json.dumps(alive_once(args), ensure_ascii=False, indent=2))


HTTP_METHOD_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(\S+)\s+HTTP/\d(?:\.\d)?", re.I)
HTTP_METHOD_ANYWHERE_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(\S+)\s+HTTP/\d(?:\.\d)?", re.I)
URL_RE = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+")
HOST_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+(?:com|cn|net|org)\b")
IP_PORT_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b")
VISIBLE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"/terminal/[A-Za-z0-9_./-]+|"
    r"/cc/[A-Za-z0-9_./-]+|"
    r"/system/[A-Za-z0-9_./-]+|"
    r"/login/[A-Za-z0-9_./-]+|"
    r"/cs/cs_[A-Za-z0-9_./-]+|"
    r"/resource/[A-Za-z0-9_./-]+|"
    r"/session/[A-Za-z0-9_./-]+|"
    r"/machine/[A-Za-z0-9_./-]+|"
    r"/sc/[A-Za-z0-9_./-]+"
    r")"
)
PRINTABLE_BYTES_RE = re.compile(rb"[\x20-\x7e]{4,}")
KEEPALIVE_KEYWORDS = (
    "heartbeat",
    "uptime",
    "desktopuptime",
    "machineconnect",
    "pushconnecteventdata",
    "session",
    "resource",
    "alive",
    "keep",
    "duration",
    "runtime",
    "instanceid",
    "userserviceid",
    "vmid",
)
ENTERPRISE_KEEPALIVE_ENDPOINTS = (
    "/resource/desktopUptime",
    "/session/machineConnect",
    "/machine/pushConnectEventData",
)
VISIBLE_CONNECTED_TIMER_ENDPOINTS = (
    "/cc/cloudPc/heartbeat/v2",
    "/cc/cloudPc/infoReport/v2",
    "/system/logReport/config/v2",
)
DECODED_PAYLOAD_TERMS = tuple(dict.fromkeys(
    ENTERPRISE_KEEPALIVE_ENDPOINTS
    + VISIBLE_CONNECTED_TIMER_ENDPOINTS
    + (
        "desktopUptime",
        "machineConnect",
        "pushConnectEventData",
        "instanceId",
        "connectInfo",
        "connectStr",
        "tokenInfo",
        "sysConfig",
        "opDesktopTimeout",
        "vdiPingTimeout",
        "enableVdiDetectVm",
        "html5EnableStream",
    )
))
KNOWN_BOOT_PATH_PREFIXES = (
    "/cs/cs_",
    "/terminal/login/",
    "/terminal/cc/getFirmAuth/",
    "/terminal/cc/cloudPc/list/",
)


def normalize_url_path(url):
    text = str(url or "")
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urllib.parse.urlsplit(text)
        return parsed.path or "/"
    if text.startswith("/"):
        return urllib.parse.urlsplit(text).path or "/"
    return text.split("?", 1)[0]


def soho_api_path(url):
    path = normalize_url_path(url)
    if path.startswith(CONFIG["terminal_prefix"] + "/"):
        return path[len(CONFIG["terminal_prefix"]):]
    return path


def endpoint_key(method, url):
    return f"{str(method or 'POST').upper()} {normalize_url_path(url)}"


def redact_text(text):
    value = str(text or "")
    value = re.sub(r"(?i)(token|accessToken|sohoToken|password|vmPassword|authorization)([\"'=:\s]+)([^\"'&\s,}]+)", r"\1\2***", value)
    value = re.sub(r"(?i)(X-SOHO-SohoToken:\s*)(\S+)", r"\1***", value)
    return value[:2000]


def candidate_score(record, baseline_keys):
    key = endpoint_key(record.get("method"), record.get("url"))
    method = str(record.get("method") or "").upper()
    path = normalize_url_path(record.get("url")).lower()
    body = str(record.get("requestBody") or "").lower()
    joined = f"{path}\n{body}"
    score = 0
    reasons = []
    if method == "CONNECT" or path in ("", "/"):
        score -= 10
        reasons.append("proxy tunnel/noise")
    if baseline_keys and key not in baseline_keys:
        score += 4
        reasons.append("not in baseline")
    for word in KEEPALIVE_KEYWORDS:
        if word in joined:
            score += 2
            reasons.append(f"keyword:{word}")
    if any(path.startswith(prefix.lower()) for prefix in KNOWN_BOOT_PATH_PREFIXES):
        score -= 5
        reasons.append("known boot/login/list route")
    if "/sc/probe-terminal-portal/" in path or "/sc/probe-cfg-portal/" in path:
        score -= 5
        reasons.append("terminalprobe telemetry/config")
    if "/cc/cloudpc/heartbeat/" in path:
        score += 3
        reasons.append("family source heartbeat route")
    return score, reasons


def parse_capture_time(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def median(values):
    nums = sorted(float(value) for value in values)
    if not nums:
        return None
    middle = len(nums) // 2
    if len(nums) % 2:
        return nums[middle]
    return (nums[middle - 1] + nums[middle]) / 2


def endpoint_timing(items):
    times = [parse_capture_time(item.get("startedDateTime")) for item in items]
    times = sorted(value for value in times if value is not None)
    if not times:
        return {
            "first": "",
            "last": "",
            "durationSeconds": None,
            "medianIntervalSeconds": None,
            "minIntervalSeconds": None,
            "maxIntervalSeconds": None,
        }
    deltas = [(times[index] - times[index - 1]).total_seconds() for index in range(1, len(times))]
    return {
        "first": times[0].isoformat(),
        "last": times[-1].isoformat(),
        "durationSeconds": int((times[-1] - times[0]).total_seconds()) if len(times) > 1 else 0,
        "medianIntervalSeconds": round(median(deltas), 3) if deltas else None,
        "minIntervalSeconds": round(min(deltas), 3) if deltas else None,
        "maxIntervalSeconds": round(max(deltas), 3) if deltas else None,
    }


def classify_endpoint(api_path, method="POST"):
    path = soho_api_path(api_path).lower()
    method = str(method or "").upper()
    if method == "CONNECT" or path in ("", "/"):
        return {
            "class": "proxy_tunnel_noise",
            "desktopKeepaliveEvidence": "none",
            "risk": "ignore",
        }
    if path == "/q":
        return {
            "class": "external_or_unrelated_noise",
            "desktopKeepaliveEvidence": "none",
            "risk": "external service path observed in auxiliary capture; ignore for CMCC desktop keepalive",
        }
    if any(word in path for word in (
        "/vops-autoops-protocol/",
        "/toolbox/download",
        "/idp/oauth2/",
        "/irai-filedownload",
        "/zte-paas-exchange",
    )):
        return {
            "class": "external_config_or_support_url",
            "desktopKeepaliveEvidence": "none",
            "risk": "external support/config/download URL visible in client payload; not a desktop session holder",
        }
    if path in {item.lower() for item in ENTERPRISE_KEEPALIVE_ENDPOINTS}:
        return {
            "class": "enterprise_blog_desktop_keepalive_candidate",
            "desktopKeepaliveEvidence": "strong_candidate_if_family_runtime_capture",
            "risk": "requires field replay and long proof",
        }
    if path in {item.lower() for item in VISIBLE_CONNECTED_TIMER_ENDPOINTS}:
        return {
            "class": "official_connected_http_timer",
            "desktopKeepaliveEvidence": "unproven_timer_only",
            "risk": "accepted response is not sleep-prevention proof; watch 4043 and powered state",
        }
    if "/point/" in path or path.endswith("/cc/point"):
        return {
            "class": "analytics_point",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "telemetry; do not treat as keepalive without long proof",
        }
    if "/sc/probe-terminal-portal/" in path:
        return {
            "class": "terminalprobe_telemetry",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "Windows/terminal telemetry; accepted response is not desktop-session keepalive proof",
        }
    if "/sc/probe-cfg-portal/" in path:
        return {
            "class": "terminalprobe_config",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "probe configuration; not a desktop session holder",
        }
    if "/getfirmauth/" in path or path.startswith("/cs/cs_"):
        return {
            "class": "connect_material_or_boot",
            "desktopKeepaliveEvidence": "not_http_session_keepalive",
            "risk": "can occupy or replace an active desktop session",
        }
    if path.endswith("/logout/v2") or "/logout/" in path:
        return {
            "class": "logout_or_disconnect",
            "desktopKeepaliveEvidence": "negative_or_cleanup",
            "risk": "terminates or reports session end",
        }
    if "/getdisconnecttime/" in path:
        return {
            "class": "idle_timeout_info",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "informational disconnect/idle-time query; not a session holder",
        }
    if method == "GET" or any(word in path for word in ("/sohopic/", ".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return {
            "class": "static_asset_or_ui",
            "desktopKeepaliveEvidence": "none",
            "risk": "static/UI asset; ignore for keepalive",
        }
    if any(word in path for word in (
        "/list/", "/sublist/", "/detail/", "/switchlist/", "/display/", "/margin/", "/balancedetails/",
        "/notice", "/checkversion/", "manual", "/feedback", "customercenter", "/claimactivity",
    )):
        return {
            "class": "inventory_status_or_config",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "safe to inspect, not a desktop session holder",
        }
    if any(word in path for word in (
        "/token/checktoken/", "/system/settings/", "/system/mqttconnect/", "/login/encryptkey/",
        "/login/sms/", "/login/namepwdlogin/",
    )):
        return {
            "class": "account_or_system_liveness",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "useful for account/token workflow, not desktop-session keepalive",
        }
    if any(word in path for word in ("/collectinfo/", "/getprivacyversion/")):
        return {
            "class": "telemetry_or_privacy_config",
            "desktopKeepaliveEvidence": "none_by_itself",
            "risk": "client telemetry/configuration path; not a desktop session holder",
        }
    return {
        "class": "unknown",
        "desktopKeepaliveEvidence": "candidate_requires_replay",
        "risk": "inspect request fields and long-test before claiming keepalive",
    }


def capture_window(records):
    times = [parse_capture_time(record.get("startedDateTime")) for record in records]
    times = sorted(value for value in times if value is not None)
    if not times:
        return {
            "first": "",
            "last": "",
            "durationSeconds": None,
        }
    return {
        "first": times[0].isoformat(),
        "last": times[-1].isoformat(),
        "durationSeconds": int((times[-1] - times[0]).total_seconds()) if len(times) > 1 else 0,
    }


def har_text(item):
    text = item.get("text") if isinstance(item, dict) else ""
    if not isinstance(text, str):
        return ""
    if item.get("encoding") == "base64":
        try:
            return base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            return text
    return text


def extract_profile_from_har(path, preferred_host=None):
    with Path(path).open("r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    entries = (((data or {}).get("log") or {}).get("entries") or [])
    best = None
    for entry in entries:
        req = entry.get("request") or {}
        url = req.get("url") or ""
        if preferred_host and preferred_host not in url:
            continue
        headers_by_name = {h.get("name"): h.get("value", "") for h in req.get("headers") or []}
        if not headers_by_name.get("X-SOHO-AppKey") or not headers_by_name.get("X-SOHO-AppType"):
            continue
        best = headers_by_name
        break
    if best is None and preferred_host:
        return extract_profile_from_har(path, None)
    if best is None:
        raise CmccError(f"no X-SOHO profile headers found in HAR: {path}")
    app_type = best.get("X-SOHO-AppType") or ""
    head = app_type.split("|", 1)[0].strip().lower()
    if head == "windows":
        profile = "windows"
    elif head == "mac":
        profile = "mac"
    else:
        profile = "linux"
    return {
        "clientProfile": profile,
        "clientDeviceId": best.get("X-SOHO-DeviceId") or "",
        "clientAppType": app_type,
        "clientRomVersion": best.get("X-SOHO-RomVersion") or "",
        "clientUserAgent": best.get("User-Agent") or "",
        "clientProfileConfig": {
            "appKey": best.get("X-SOHO-AppKey") or "",
            "version": best.get("X-SOHO-ClientVersion") or "",
            "versionNum": best.get("X-SOHO-VersionNum") or "",
        },
    }


def set_profile(args):
    profile = str(args.profile or "").strip().lower()
    if profile == "auto" and not args.from_har:
        raise CmccError("profile auto requires --from-har")
    if profile != "auto" and profile not in CLIENT_PROFILES:
        raise CmccError(f"unknown profile: {profile}; choose one of {', '.join(sorted(CLIENT_PROFILES))}")
    patch = {"clientProfile": profile}
    if args.from_har:
        patch.update(extract_profile_from_har(args.from_har, args.preferred_host))
        if profile != "auto":
            patch["clientProfile"] = profile
    state = merge_state(patch, args)
    print(json.dumps({
        "ok": True,
        "clientProfile": state.get("clientProfile"),
        "clientDeviceId": state.get("clientDeviceId") or state.get("deviceId") or "",
        "clientAppType": state.get("clientAppType") or "",
        "clientRomVersion": state.get("clientRomVersion") or "",
        "clientUserAgent": state.get("clientUserAgent") or "",
    }, ensure_ascii=False, indent=2))


def try_json(text):
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def flatten_json_keys(value, prefix="", limit=120):
    keys = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            keys.append(path)
            if len(keys) >= limit:
                return keys
            keys.extend(flatten_json_keys(item, path, limit - len(keys)))
            if len(keys) >= limit:
                return keys
    elif isinstance(value, list):
        for index, item in enumerate(value[:8]):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            keys.extend(flatten_json_keys(item, path, limit - len(keys)))
            if len(keys) >= limit:
                return keys
    return keys


def classify_decoded_payload(parsed, text):
    if isinstance(parsed, dict):
        keys = set(parsed)
        if {"connectInfo", "sysConfig"} & keys:
            return {
                "class": "cag_decoded_connect_material",
                "desktopKeepaliveEvidence": "session_owning_fallback_only",
                "risk": "CAG decoded startup/connect material; can occupy or replace active desktop session",
            }
        if {"accessToken", "expiresTime", "issuedTime", "validTime"} & keys or "tokenInfo" in keys:
            return {
                "class": "cag_or_account_token_material",
                "desktopKeepaliveEvidence": "none_by_itself",
                "risk": "token/account material; useful for auth workflow, not desktop-session keepalive",
            }
    lowered = str(text or "").lower()
    if any(endpoint.lower() in lowered for endpoint in ENTERPRISE_KEEPALIVE_ENDPOINTS):
        return {
            "class": "decoded_enterprise_style_keepalive_reference",
            "desktopKeepaliveEvidence": "source_or_payload_hint_only",
            "risk": "requires runtime HTTP endpoint capture and replay proof",
        }
    return {
        "class": "decoded_payload",
        "desktopKeepaliveEvidence": "none_by_itself",
        "risk": "decoded data is not an HTTP endpoint without URL/request evidence",
    }


def decoded_payload_findings(paths, limit=40):
    findings = []
    skipped = []
    class_counts = Counter()
    matched_terms = Counter()
    decoded_count = 0
    for path in paths or []:
        if Path(path).suffix.lower() not in (".jsonl", ".ndjson"):
            continue
        try:
            f = Path(path).open("r", encoding="utf-8", errors="replace")
        except OSError as err:
            skipped.append({"source": str(path), "error": str(err)})
            continue
        with f:
            for index, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                event = try_json(line)
                if not isinstance(event, dict):
                    continue
                text = event.get("value")
                if not isinstance(text, str):
                    continue
                parsed = try_json(text)
                if parsed is None:
                    continue
                decoded_count += 1
                text_lower = text.lower()
                terms = [term for term in DECODED_PAYLOAD_TERMS if term.lower() in text_lower]
                for term in terms:
                    matched_terms[term] += 1
                classification = classify_decoded_payload(parsed, text)
                class_counts[classification["class"]] += 1
                if len(findings) < limit and (terms or classification["class"] != "decoded_payload"):
                    findings.append({
                        "source": str(path),
                        "index": index,
                        "event": event.get("event") or "",
                        "process": event.get("process") or "",
                        "function": event.get("function") or "",
                        "phase": event.get("phase") or "",
                        "hash": event.get("hash") or "",
                        "length": event.get("len") or event.get("dumped"),
                        "classification": classification,
                        "matchedTerms": terms,
                        "topLevelKeys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
                        "flattenedKeySamples": flatten_json_keys(parsed, limit=40),
                    })
    enterprise_refs = [
        term for term in ENTERPRISE_KEEPALIVE_ENDPOINTS
        if matched_terms.get(term) or matched_terms.get(term.strip("/").split("/")[-1])
    ]
    return {
        "decodedPayloads": decoded_count,
        "skippedFiles": skipped,
        "classCounts": dict(class_counts.most_common()),
        "matchedTerms": dict(matched_terms.most_common()),
        "enterpriseStyleKeepaliveReferences": enterprise_refs,
        "findings": findings,
        "conclusion": (
            "decoded payload contains enterprise-style keepalive references; runtime URL capture is still required"
            if enterprise_refs else
            "decoded payloads do not expose an independent HTTP desktop-session keepalive endpoint"
            if decoded_count else
            "no decoded JSON payloads were found in JSONL inputs"
        ),
    }


def body_summary(text):
    parsed = try_json(text)
    if not isinstance(parsed, dict):
        return {
            "json": parsed is not None,
            "encryptedBodyDetected": False,
            "keys": [],
            "replayableJson": parsed is not None,
        }
    encrypted = set(parsed.keys()) == {"data"} and isinstance(parsed.get("data"), str)
    return {
        "json": True,
        "encryptedBodyDetected": encrypted,
        "keys": sorted(parsed.keys()),
        "replayableJson": not encrypted,
    }


SENSITIVE_REPLAY_KEYS = {
    "phone",
    "mobile",
    "token",
    "sohotoken",
    "accesstoken",
    "password",
    "vmpassword",
    "authorization",
    "mac",
    "localip",
    "deviceid",
    "traceid",
    "vmid",
    "uuid",
}


def redact_json_value_for_replay(key, value):
    lowered = str(key or "").lower()
    if lowered in SENSITIVE_REPLAY_KEYS:
        return f"<{lowered}>"
    if isinstance(value, dict):
        return {item_key: redact_json_value_for_replay(item_key, item_value) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_json_value_for_replay(key, item) for item in value]
    return value


def redact_json_for_replay(text):
    parsed = try_json(text)
    if parsed is None:
        return str(text or "").strip()
    if isinstance(parsed, dict):
        parsed = {key: redact_json_value_for_replay(key, value) for key, value in parsed.items()}
    elif isinstance(parsed, list):
        parsed = [redact_json_value_for_replay("", item) for item in parsed]
    return json_dumps_compact(parsed)


def replay_command(path, request_body):
    summary = body_summary(request_body)
    api_path = soho_api_path(path)
    if not api_path.startswith("/"):
        return ""
    if summary["replayableJson"]:
        body = redact_json_for_replay(request_body) or "{}"
        return f"python3 bin/cmcc_cloud_alive.py api-probe {api_path} --json '{body}'"
    if summary["encryptedBodyDetected"]:
        return f"# HAR body is app-layer encrypted; recover logical JSON first, then: python3 bin/cmcc_cloud_alive.py api-probe {api_path} --json '<json>'"
    return f"python3 bin/cmcc_cloud_alive.py api-probe {api_path}"


TEXT_SOURCE_EXTENSIONS = {
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".json", ".html", ".css", ".scss", ".txt", ".md"
}


class AsarArchive:
    def __init__(self, path):
        self.path = Path(path)
        with self.path.open("rb") as f:
            prefix = f.read(16)
            if len(prefix) < 16:
                raise CmccError(f"ASAR file is too short: {self.path}")
            self.header_size = int.from_bytes(prefix[8:12], "little")
            self.json_size = int.from_bytes(prefix[12:16], "little")
            f.seek(16)
            self.header = json.loads(f.read(self.json_size).decode("utf-8", errors="replace"))
            self.content_base = 16 + self.header_size

    def iter_files(self):
        def walk(node, prefix=""):
            for name, item in (node.get("files") or {}).items():
                path = f"{prefix}/{name}" if prefix else name
                if "files" in item:
                    yield from walk(item, path)
                elif "offset" in item and "size" in item:
                    yield path, item
        yield from walk(self.header)

    def read_file(self, item):
        size = int(item.get("size") or 0)
        offset = int(item.get("offset") or 0)
        with self.path.open("rb") as f:
            f.seek(max(0, self.content_base + offset - 8))
            raw = f.read(size + 16)
        return raw


def is_text_source_path(path):
    return Path(str(path)).suffix.lower() in TEXT_SOURCE_EXTENSIONS


def decode_source_text(raw):
    text = raw.decode("utf-8", errors="replace")
    # Some ASAR offsets in this package point a few bytes into or before the
    # file. Trim harmless leading tail bytes when a clear source start is nearby.
    starts = ["{", "import ", "const ", "let ", "var ", "export ", "<", "function ", "#", "//"]
    best = 0
    for marker in starts:
        pos = text.find(marker, 0, 32)
        if pos >= 0:
            best = pos if best == 0 else min(best, pos)
    return text[best:]


def source_records_from_path(source_path, max_file_bytes=2_000_000):
    root = Path(source_path)
    records = []
    if root.is_file() and root.suffix == ".asar":
        archive = AsarArchive(root)
        for inner_path, item in archive.iter_files():
            size = int(item.get("size") or 0)
            if size <= 0 or size > max_file_bytes or not is_text_source_path(inner_path):
                continue
            records.append({
                "source": str(root),
                "path": inner_path,
                "text": decode_source_text(archive.read_file(item)),
            })
        return records
    if root.is_file() and is_text_source_path(root):
        records.append({
            "source": str(root),
            "path": root.name,
            "text": root.read_text(encoding="utf-8", errors="replace"),
        })
        return records
    if root.is_dir():
        for file_path in root.rglob("*"):
            if not file_path.is_file() or not is_text_source_path(file_path):
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            if size <= 0 or size > max_file_bytes:
                continue
            records.append({
                "source": str(root),
                "path": str(file_path.relative_to(root)),
                "text": file_path.read_text(encoding="utf-8", errors="replace"),
            })
    return records


def endpoint_terms(endpoint):
    path = soho_api_path(endpoint)
    terms = {path, normalize_url_path(endpoint)}
    for part in path.strip("/").split("/"):
        if len(part) >= 4:
            terms.add(part)
    return {term for term in terms if term}


def source_search(source_paths, queries, limit=80, context=2):
    hits = []
    display_queries = [q for q in queries if q] or list(KEEPALIVE_KEYWORDS)
    lowered_queries = [q.lower() for q in display_queries]
    for source_path in source_paths:
        for record in source_records_from_path(source_path):
            lines = record["text"].splitlines()
            for index, line in enumerate(lines, 1):
                lowered = line.lower()
                matched = [display_queries[i] for i, q in enumerate(lowered_queries) if q in lowered]
                if not matched:
                    continue
                start = max(1, index - context)
                end = min(len(lines), index + context)
                snippet = "\n".join(f"{lineno}: {lines[lineno - 1]}" for lineno in range(start, end + 1))
                hits.append({
                    "source": record["source"],
                    "path": record["path"],
                    "line": index,
                    "matched": sorted(set(matched)),
                    "snippet": redact_text(snippet),
                })
                if len(hits) >= limit:
                    return hits
    return hits


def source_correlation(source_paths, endpoint, limit=12):
    exact_terms = {soho_api_path(endpoint), normalize_url_path(endpoint)}
    exact_terms = {term for term in exact_terms if term and term != "/"}
    broad_terms = endpoint_terms(endpoint) - exact_terms
    exact_hits = source_search(source_paths, sorted(exact_terms), limit=limit, context=2) if exact_terms else []
    broad_hits = []
    if len(exact_hits) < limit and broad_terms:
        broad_hits = source_search(source_paths, sorted(broad_terms), limit=limit - len(exact_hits), context=2)
    return {
        "exactTerms": sorted(exact_terms),
        "broadTerms": sorted(broad_terms),
        "exactHitCount": len(exact_hits),
        "broadHitCount": len(broad_hits),
        "hits": exact_hits + broad_hits,
    }


def source_audit(args):
    source_paths = args.source or DEFAULT_SOURCE_PATHS
    queries = list(args.query or [])
    for endpoint in args.endpoint or []:
        queries.extend(endpoint_terms(endpoint))
    if args.endpoint and not args.query:
        correlations = {
            endpoint: source_correlation(source_paths, endpoint, limit=int(args.limit))
            for endpoint in args.endpoint
        }
        print(json.dumps({
            "ok": True,
            "sourcePaths": source_paths,
            "correlations": correlations,
        }, ensure_ascii=False, indent=2))
        return
    hits = source_search(source_paths, queries, limit=int(args.limit), context=int(args.context))
    print(json.dumps({
        "ok": True,
        "sourcePaths": source_paths,
        "queries": queries or list(KEEPALIVE_KEYWORDS),
        "hitCount": len(hits),
        "hits": hits,
    }, ensure_ascii=False, indent=2))


def parse_har_capture(path):
    with Path(path).open("r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    entries = (((data or {}).get("log") or {}).get("entries") or [])
    records = []
    for index, entry in enumerate(entries, 1):
        req = entry.get("request") or {}
        res = entry.get("response") or {}
        post_data = req.get("postData") or {}
        content = res.get("content") or {}
        records.append({
            "source": str(path),
            "sourceType": "har",
            "index": index,
            "startedDateTime": entry.get("startedDateTime") or "",
            "method": req.get("method") or "",
            "url": req.get("url") or "",
            "status": res.get("status"),
            "requestBody": har_text(post_data),
            "responseBody": har_text(content),
        })
    return records


def parse_plain_http_payload(payload, source, index):
    text = str(payload or "")
    first = text.splitlines()[0] if text.splitlines() else ""
    match = HTTP_METHOD_RE.match(first)
    if match:
        return {
            "source": source,
            "sourceType": "plaintext",
            "index": index,
            "method": match.group(1).upper(),
            "url": match.group(2),
            "status": None,
            "requestBody": text.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in text else "",
            "responseBody": "",
        }
    return None


def binary_strings(path, max_bytes=20_000_000, max_strings=50_000):
    raw = Path(path).read_bytes()[:max_bytes]
    strings = []
    for match in PRINTABLE_BYTES_RE.finditer(raw):
        try:
            text = match.group(0).decode("utf-8", errors="replace")
        except Exception:
            text = match.group(0).decode("latin1", errors="replace")
        strings.append(text)
        if len(strings) >= max_strings:
            break
    return strings


def parse_binary_capture(path):
    records = []
    for index, text in enumerate(binary_strings(path), 1):
        for match in HTTP_METHOD_ANYWHERE_RE.finditer(text):
            records.append({
                "source": str(path),
                "sourceType": "binary",
                "index": index,
                "method": match.group(1).upper(),
                "url": match.group(2),
                "status": None,
                "requestBody": "",
                "responseBody": "",
            })
    return records


def tshark_capture_metadata(paths, limit=60):
    tshark = shutil.which("tshark")
    pcap_inputs = [str(path) for path in paths or [] if Path(path).suffix.lower() in (".pcap", ".pcapng", ".cap")]
    result = {
        "available": bool(tshark),
        "tool": tshark or "",
        "inputFiles": pcap_inputs,
        "errors": [],
        "conversationCount": 0,
        "topConversations": [],
        "topRemotePorts": {},
        "tlsServerNames": {},
        "httpHosts": {},
        "httpRequestUris": {},
        "dnsQueries": {},
        "conclusion": "no pcap input",
    }
    if not pcap_inputs:
        return result
    if not tshark:
        result["conclusion"] = "tshark not available; binary string scan only"
        return result
    conversation_counts = Counter()
    remote_port_counts = Counter()
    sni_counts = Counter()
    http_host_counts = Counter()
    http_uri_counts = Counter()
    dns_counts = Counter()
    fields = [
        "frame.time_epoch",
        "ip.src",
        "tcp.srcport",
        "udp.srcport",
        "ip.dst",
        "tcp.dstport",
        "udp.dstport",
        "tls.handshake.extensions_server_name",
        "http.host",
        "http.request.uri",
        "dns.qry.name",
    ]
    for path in pcap_inputs:
        cmd = [tshark, "-r", path, "-T", "fields", "-E", "separator=|"]
        for field in fields:
            cmd.extend(["-e", field])
        try:
            completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=45)
        except Exception as err:
            result["errors"].append({"source": path, "error": str(err)})
            continue
        if completed.returncode != 0:
            result["errors"].append({"source": path, "error": (completed.stderr or "").strip()[:1000]})
        for line in (completed.stdout or "").splitlines():
            parts = line.split("|")
            if len(parts) < len(fields):
                parts.extend([""] * (len(fields) - len(parts)))
            _, src, tcp_src, udp_src, dst, tcp_dst, udp_dst, sni, http_host, http_uri, dns_query = parts[:len(fields)]
            sport = tcp_src or udp_src
            dport = tcp_dst or udp_dst
            if src and dst:
                left = f"{src}:{sport}" if sport else src
                right = f"{dst}:{dport}" if dport else dst
                key = " <-> ".join(sorted([left, right]))
                conversation_counts[key] += 1
                for endpoint in (left, right):
                    if endpoint.startswith(("127.", "localhost")):
                        continue
                    if ":" in endpoint:
                        remote_port_counts[endpoint.rsplit(":", 1)[1]] += 1
            for value, counter in (
                (sni, sni_counts),
                (http_host, http_host_counts),
                (http_uri, http_uri_counts),
                (dns_query, dns_counts),
            ):
                for item in str(value or "").split(","):
                    item = item.strip()
                    if item:
                        counter[item] += 1
    result["conversationCount"] = len(conversation_counts)
    result["topConversations"] = [
        {"conversation": key, "frames": count}
        for key, count in conversation_counts.most_common(limit)
    ]
    result["topRemotePorts"] = dict(remote_port_counts.most_common(limit))
    result["tlsServerNames"] = dict(sni_counts.most_common(limit))
    result["httpHosts"] = dict(http_host_counts.most_common(limit))
    result["httpRequestUris"] = dict(http_uri_counts.most_common(limit))
    result["dnsQueries"] = dict(dns_counts.most_common(limit))
    if http_host_counts or http_uri_counts:
        result["conclusion"] = "pcap contains plaintext HTTP fields"
    elif sni_counts:
        result["conclusion"] = "pcap contains TLS SNI but no plaintext HTTP request fields"
    else:
        result["conclusion"] = "pcap has no plaintext HTTP request fields visible to tshark"
    return result


def binary_capture_findings(paths, limit=80):
    findings = {
        "inputFiles": [],
        "visibleHosts": {},
        "visibleIpPorts": {},
        "visibleUrls": [],
        "visiblePaths": [],
        "httpRequestLines": [],
        "matchedKeepaliveTerms": {},
        "tshark": tshark_capture_metadata(paths),
        "transportSignals": {
            "sohoHostVisible": False,
            "cag8899Visible": False,
            "mqtt8883Visible": False,
            "loopbackVisible": False,
            "opentelemetryVisible": False,
            "spiceOrSdkSuccessVisible": False,
        },
        "conclusion": "no binary captures supplied",
    }
    host_counts = Counter()
    ip_port_counts = Counter()
    path_counts = Counter()
    term_counts = Counter()
    visible_urls = []
    request_lines = []
    binary_inputs = []
    for path in paths or []:
        suffix = Path(path).suffix.lower()
        if suffix not in (".pcap", ".pcapng", ".cap"):
            continue
        binary_inputs.append(str(path))
        try:
            strings = binary_strings(path)
        except OSError as err:
            findings.setdefault("skippedFiles", []).append({"source": str(path), "error": str(err)})
            continue
        joined_lower = "\n".join(strings).lower()
        signals = findings["transportSignals"]
        signals["sohoHostVisible"] = signals["sohoHostVisible"] or ("soho.komect.com" in joined_lower)
        signals["cag8899Visible"] = signals["cag8899Visible"] or (":8899" in joined_lower or "111.31.3.182" in joined_lower)
        signals["mqtt8883Visible"] = signals["mqtt8883Visible"] or (":8883" in joined_lower or "mqtt" in joined_lower)
        signals["loopbackVisible"] = signals["loopbackVisible"] or ("127.0.0.1" in joined_lower)
        signals["opentelemetryVisible"] = signals["opentelemetryVisible"] or ("opentelemetry" in joined_lower)
        signals["spiceOrSdkSuccessVisible"] = signals["spiceOrSdkSuccessVisible"] or any(
            term in joined_lower for term in ("spice connect", "surface create success", "connect success", "first frame recv success")
        )
        for text in strings:
            for host in HOST_RE.findall(text):
                host_counts[host.lower()] += 1
            for ip_port in IP_PORT_RE.findall(text):
                ip_port_counts[ip_port] += 1
            for url in URL_RE.findall(text):
                if len(visible_urls) < limit:
                    visible_urls.append(redact_text(url))
                path_part = normalize_url_path(url)
                if path_part and path_part != "/":
                    path_counts[path_part] += 1
            for path_match in VISIBLE_PATH_RE.findall(text):
                path_counts[path_match] += 1
            for request in HTTP_METHOD_ANYWHERE_RE.finditer(text):
                if len(request_lines) < limit:
                    request_lines.append(redact_text(request.group(0)))
            lowered = text.lower()
            for term in DECODED_PAYLOAD_TERMS:
                if term.lower() in lowered:
                    term_counts[term] += 1
    findings["inputFiles"] = binary_inputs
    findings["visibleHosts"] = dict(host_counts.most_common(40))
    findings["visibleIpPorts"] = dict(ip_port_counts.most_common(40))
    findings["visibleUrls"] = visible_urls
    visible_paths = []
    for path, count in path_counts.most_common(limit):
        visible_paths.append({
            "path": path,
            "apiPath": soho_api_path(path),
            "count": count,
            "classification": classify_endpoint(path),
        })
    findings["visiblePaths"] = visible_paths
    findings["httpRequestLines"] = request_lines
    findings["matchedKeepaliveTerms"] = dict(term_counts.most_common(40))
    if not binary_inputs:
        findings["conclusion"] = "no binary captures supplied"
    elif request_lines:
        findings["conclusion"] = "binary capture contains plaintext HTTP request lines; inspect endpointDetails"
    elif visible_paths:
        findings["conclusion"] = "binary capture exposes visible strings/URLs only; use HAR or TLS decryption for replayable HTTP paths"
    else:
        findings["conclusion"] = "binary capture has no visible HTTP path strings"
    return findings


def parse_jsonl_capture(path):
    records = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as f:
        for index, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                record = parse_plain_http_payload(line, str(path), index)
                if record:
                    records.append(record)
                continue
            for field in ("value", "data", "payload", "text", "request", "response"):
                if field in event and isinstance(event[field], str):
                    record = parse_plain_http_payload(event[field], str(path), index)
                    if record:
                        record["event"] = event.get("event") or ""
                        record["process"] = event.get("process") or ""
                        records.append(record)
            if event.get("url") or event.get("path"):
                records.append({
                    "source": str(path),
                    "sourceType": "jsonl",
                    "index": index,
                    "method": event.get("method") or "",
                    "url": event.get("url") or event.get("path") or "",
                    "status": event.get("status") or event.get("statusCode"),
                    "requestBody": event.get("body") or event.get("requestBody") or "",
                    "responseBody": event.get("responseBody") or "",
                    "event": event.get("event") or "",
                    "process": event.get("process") or "",
                })
    return records


def parse_capture_file(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".har":
        return parse_har_capture(path)
    if suffix in (".jsonl", ".ndjson"):
        return parse_jsonl_capture(path)
    if suffix in (".pcap", ".pcapng", ".cap"):
        return parse_binary_capture(path)
    try:
        return parse_har_capture(path)
    except Exception:
        return parse_jsonl_capture(path)


def analyze_session_capture(args):
    baseline_records = []
    for path in args.baseline or []:
        baseline_records.extend(parse_capture_file(path))
    baseline_keys = {endpoint_key(r.get("method"), r.get("url")) for r in baseline_records}

    records = []
    for path in args.capture:
        records.extend(parse_capture_file(path))

    endpoint_counts = Counter(endpoint_key(r.get("method"), r.get("url")) for r in records if r.get("url"))
    by_endpoint = defaultdict(list)
    for record in records:
        if record.get("url"):
            by_endpoint[endpoint_key(record.get("method"), record.get("url"))].append(record)

    source_paths = args.source or []
    source_hit_cache = {}
    candidates = []
    endpoint_details = []
    decoded_findings = decoded_payload_findings(args.capture)
    binary_findings = binary_capture_findings(args.capture)
    for key, items in by_endpoint.items():
        score, reasons = candidate_score(items[0], baseline_keys)
        sample = items[0]
        api_path = soho_api_path(sample.get("url"))
        classification = classify_endpoint(api_path, sample.get("method"))
        timing = endpoint_timing(items)
        detail = {
            "endpoint": key,
            "apiPath": api_path,
            "count": len(items),
            "classification": classification,
            "timing": timing,
        }
        endpoint_details.append(detail)
        if score > 0 or args.include_all:
            req_summary = body_summary(sample.get("requestBody") or "")
            res_summary = body_summary(sample.get("responseBody") or "")
            source_corr = None
            if source_paths:
                source_key = api_path or normalize_url_path(sample.get("url"))
                if source_key not in source_hit_cache:
                    source_hit_cache[source_key] = source_correlation(source_paths, source_key, limit=int(args.source_limit))
                source_corr = source_hit_cache[source_key]
            candidates.append({
                "score": score,
                "endpoint": key,
                "apiPath": api_path,
                "count": len(items),
                "statusSamples": sorted({str(item.get("status")) for item in items if item.get("status") is not None})[:8],
                "reasons": reasons,
                "classification": classification,
                "timing": timing,
                "requestBody": req_summary,
                "responseBody": res_summary,
                "replayCommand": replay_command(sample.get("url"), sample.get("requestBody") or ""),
                "sourceCorrelation": source_corr,
                "source": sample.get("source"),
                "sample": {
                    "requestBody": redact_text(sample.get("requestBody")),
                    "responseBody": redact_text(sample.get("responseBody")),
                } if args.samples else None,
            })
    candidates.sort(key=lambda item: (-item["score"], item["endpoint"]))
    endpoint_details.sort(key=lambda item: (-item["count"], item["endpoint"]))

    present_api_paths = {soho_api_path(record.get("url")).lower() for record in records if record.get("url")}
    absent_enterprise = [endpoint for endpoint in ENTERPRISE_KEEPALIVE_ENDPOINTS if endpoint.lower() not in present_api_paths]
    present_visible_timers = [
        endpoint for endpoint in VISIBLE_CONNECTED_TIMER_ENDPOINTS
        if endpoint.lower() in present_api_paths
    ]
    present_enterprise = [
        endpoint for endpoint in ENTERPRISE_KEEPALIVE_ENDPOINTS
        if endpoint.lower() in present_api_paths
    ]
    unknown_candidates = [
        item for item in candidates
        if item["classification"]["class"] == "unknown"
    ]
    official_timer_matrix = [
        item for item in endpoint_details
        if item["classification"]["class"] == "official_connected_http_timer"
    ]
    session_owning_fallbacks = [
        item for item in endpoint_details
        if item["classification"]["class"] == "connect_material_or_boot"
    ]
    excluded_by_class = Counter(item["classification"]["class"] for item in endpoint_details)
    if present_enterprise:
        http_only_verdict = "family_capture_has_enterprise_style_keepalive_candidate"
        proof_reason = "A blog-style desktop endpoint appears in this family capture; replay and long proof are still required."
    elif unknown_candidates:
        http_only_verdict = "capture_has_unknown_http_candidates"
        proof_reason = "Unknown endpoints need field recovery and replay before they can be judged."
    elif present_visible_timers:
        http_only_verdict = "visible_connected_timers_only_unproven"
        proof_reason = "The capture shows official connected-client HTTP timers, but no independent desktop uptime/session endpoint."
    else:
        http_only_verdict = "no_http_desktop_keepalive_candidate_found"
        proof_reason = "No desktop-session HTTP endpoint was visible in this capture."

    report = {
        "ok": True,
        "inputFiles": args.capture,
        "baselineFiles": args.baseline or [],
        "sourceFiles": source_paths,
        "records": len(records),
        "baselineRecords": len(baseline_records),
        "observedWindow": capture_window(records),
        "uniqueEndpoints": len(endpoint_counts),
        "endpointCounts": dict(endpoint_counts.most_common()),
        "endpointDetails": endpoint_details,
        "candidates": candidates,
        "decodedPayloadFindings": decoded_findings,
        "binaryCaptureFindings": binary_findings,
        "officialTimerMatrix": official_timer_matrix,
        "sessionOwningFallbackCandidates": session_owning_fallbacks,
        "excludedByClass": dict(excluded_by_class.most_common()),
        "enterpriseBlogEndpoints": {
            "present": present_enterprise,
            "absent": absent_enterprise,
        },
        "visibleConnectedTimers": present_visible_timers,
        "httpOnlyKeepaliveEvidence": {
            "verdict": http_only_verdict,
            "reason": proof_reason,
            "successSignal": (
                "HTTP-only desktop keepalive is rejected for this project unless a "
                "new family-runtime endpoint is found and independently disproves "
                "the existing power-off evidence."
            ),
            "pureHttpDesktopEndpointFound": bool(present_enterprise or unknown_candidates),
            "visibleTimersOnly": bool(present_visible_timers and not present_enterprise and not unknown_candidates),
            "sessionOwningFallbackFound": bool(session_owning_fallbacks),
            "requiredProof": [
                "family runtime capture endpoint",
                "pure Python replay accepted by service",
                "no 4043 other-login/session replacement",
                "cloud desktop remains running past idle shutdown window",
                "independent per-minute power monitor report",
            ],
        },
        "verdict": http_only_verdict,
        "nextStep": (
            "Recover fields for unknown candidates only as supporting evidence; the active route is RAP/ZIME/SPICE display protocol reproduction."
            if unknown_candidates or present_enterprise else
            "Visible HTTP timers are negative evidence now; continue with native RAP/ZIME/SPICE display protocol capture."
            if present_visible_timers else
            "Current input did not expose HTTP keepalive; continue with native RAP/ZIME/SPICE display protocol capture."
        ),
    }
    write_private_json_report(report, getattr(args, "report_file", ""))
    print(json.dumps(report, ensure_ascii=False, indent=2))


def print_state(args):
    safe = load_state(args)
    for key in ["sohoToken", "publicKey"]:
        if safe.get(key):
            safe[key] = "***"
    print(json.dumps(safe, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="CMCC family cloud PC protocol research helpers")
    parser.add_argument("--state", help="state file path, default ~/.cmcc-cloud-alive/state.json")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("password-login")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("--verification-code", default="")
    p.add_argument("--random-code", default="")
    p.set_defaults(func=password_login)

    p = sub.add_parser("protocol-check")
    p.set_defaults(func=protocol_check)

    p = sub.add_parser("set-profile")
    p.add_argument("profile", choices=["auto", "linux", "windows", "mac"])
    p.add_argument("--from-har", default="", help="import accepted X-SOHO fingerprint headers from HAR")
    p.add_argument("--preferred-host", default="soho.komect.com")
    p.set_defaults(func=set_profile)

    p = sub.add_parser("list")
    p.set_defaults(func=print_list)

    p = sub.add_parser("cloud-status")
    p.add_argument("user_service_id", nargs="?")
    p.set_defaults(func=print_cloud_status)

    p = sub.add_parser("firm-auth")
    p.add_argument("user_service_id", nargs="?")
    p.set_defaults(func=print_firm_auth)

    p = sub.add_parser("heartbeat")
    p.add_argument("user_service_id", nargs="?")
    p.set_defaults(func=print_heartbeat)

    p = sub.add_parser("alive-once")
    p.add_argument("user_service_id", nargs="?")
    p.set_defaults(func=print_alive_once)

    p = sub.add_parser("api-probe")
    p.add_argument("path", help="SOHO API path, for example /cc/cloudPc/heartbeat/v2 or /terminal/...")
    p.add_argument("--json", default=None, help="logical JSON body or @file; body is encrypted like the family client")
    p.add_argument("--timeout", type=int, default=30)
    p.set_defaults(func=api_probe)

    p = sub.add_parser("cag-https-connect")
    p.add_argument("user_service_id", nargs="?")
    p.add_argument("--timeout", type=int, default=15, help="CAG HTTPS request timeout seconds")
    p.add_argument("--boot-wait", type=int, default=180, help="async boot wait seconds")
    p.add_argument("--version", default="V7.25.40-HY")
    p.add_argument("--client-ip", default="")
    p.add_argument("--mac", default="")
    p.add_argument("--host-name", default="")
    p.set_defaults(func=cag_https_connect)

    p = sub.add_parser("analyze-session-capture")
    p.add_argument("capture", nargs="+", help="Reqable HAR or plaintext JSONL captured after official desktop connection")
    p.add_argument("--baseline", action="append", default=[], help="optional pre-connect HAR/JSONL baseline for endpoint diff")
    p.add_argument("--source", action="append", default=[], help="optional unpacked source directory or app.asar for endpoint correlation")
    p.add_argument("--source-limit", type=int, default=12, help="max source hits per candidate")
    p.add_argument("--samples", action="store_true", help="include redacted request/response samples")
    p.add_argument("--include-all", action="store_true", help="include endpoints even when candidate score is not positive")
    p.add_argument("--report-file", default="", help="write full JSON analysis report")
    p.set_defaults(func=analyze_session_capture)

    p = sub.add_parser("source-audit")
    p.add_argument("--source", action="append", default=[], help="source directory or app.asar; defaults to installed family client app.asar")
    p.add_argument("--query", action="append", default=[], help="keyword to search")
    p.add_argument("--endpoint", action="append", default=[], help="endpoint/path to correlate, e.g. /cc/cloudPc/heartbeat/v2")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--context", type=int, default=2)
    p.set_defaults(func=source_audit)

    p = sub.add_parser("state")
    p.set_defaults(func=print_state)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except CmccError as err:
        print(f"Error: {err}", file=sys.stderr)
        if err.response is not None:
            print(json.dumps(err.response, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
