"""SPICE and Chuanyun protocol codecs for the native desktop route."""

import hashlib
import os
import struct


SPICE_MAGIC = b"REDQ"
SPICE_LINK_HEADER_SIZE = 16
SPICE_LINK_MESS_BASE_SIZE = 18
SPICE_LINK_REPLY_BASE_SIZE = 178
SPICE_TICKET_PUBKEY_BYTES = 162
MINI_HEADER_SIZE = 6
DATA_HEADER_SIZE = 18
CHUANYUN_HEAD_SIZE = 24
CHUANYUN_VERSION = 0x01


class SpiceChannel:
    MAIN = 1
    DISPLAY = 2
    INPUTS = 3
    CURSOR = 4


class SpiceMessage:
    SET_ACK = 0x0003
    PING = 0x0004
    PONG = 0x0005
    ACK_SYNC = 0x0006
    ACK = 0x0007
    MARK = 0x0066
    MAIN_INIT = 0x0067
    CHANNELS_LIST = 0x0068
    DISPLAY_INIT = 0x0065
    DRAW_COPY = 0x0130
    SURFACE_CREATE = 0x013A


class ChuanyunFrameType:
    DATA = 1
    CONTROL = 2
    SERVER_CLOSE = 3


class ProtocolStage:
    DISPLAY_INIT_SENT = "display_init_sent"
    SET_ACK_RECEIVED = "set_ack_received"
    ACK_SYNC_SENT = "ack_sync_sent"
    PING_RECEIVED = "ping_received"
    PONG_SENT = "pong_sent"
    SURFACE_CREATE_RECEIVED = "surface_create_received"
    DRAW_COPY_RECEIVED = "draw_copy_received"
    MARK_RECEIVED = "mark_received"


def _u8(value, name):
    if not isinstance(value, int) or value < 0 or value > 0xFF:
        raise ValueError(f"{name} must be uint8")
    return value


def _u16(value, name):
    if not isinstance(value, int) or value < 0 or value > 0xFFFF:
        raise ValueError(f"{name} must be uint16")
    return value


def _u32(value, name):
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFF:
        raise ValueError(f"{name} must be uint32")
    return value


def _u64(value, name):
    if not isinstance(value, int) or value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"{name} must be uint64")
    return value


def encode_capability_words(bits=None):
    bits = bits or []
    words = []
    for bit in bits:
        if not isinstance(bit, int) or bit < 0:
            raise ValueError("capability bit must be non-negative")
        index = bit // 32
        while len(words) <= index:
            words.append(0)
        words[index] |= 1 << (bit % 32)
    return b"".join(struct.pack("<I", word) for word in words)


def decode_capability_words(data, word_count, offset=0):
    if len(data) < offset + word_count * 4:
        raise ValueError("capability data is incomplete")
    words = []
    bits = []
    for index in range(word_count):
        word = struct.unpack_from("<I", data, offset + index * 4)[0]
        words.append(word)
        for bit in range(32):
            if word & (1 << bit):
                bits.append(index * 32 + bit)
    return {"words": words, "bits": bits}


def encode_spice_link_header(size, major=2, minor=2):
    return SPICE_MAGIC + struct.pack("<III", _u32(major, "major"), _u32(minor, "minor"), _u32(size, "size"))


def decode_spice_link_header(data):
    if len(data) < SPICE_LINK_HEADER_SIZE:
        raise ValueError("SPICE link header is incomplete")
    if data[:4] != SPICE_MAGIC:
        raise ValueError(f"unsupported SPICE magic: {data[:4].hex()}")
    major, minor, size = struct.unpack_from("<III", data, 4)
    return {"majorVersion": major, "minorVersion": minor, "size": size}


def encode_spice_link_mess(connection_id=0, channel_type=SpiceChannel.MAIN, channel_id=0, common_caps=None, channel_caps=None):
    common = bytes(common_caps if common_caps is not None else encode_capability_words([3]))
    channel = bytes(channel_caps or b"")
    if len(common) % 4 or len(channel) % 4:
        raise ValueError("capability byte length must be a multiple of 4")
    caps_offset = SPICE_LINK_MESS_BASE_SIZE
    body = struct.pack(
        "<IBBIII",
        _u32(connection_id, "connectionId"),
        _u8(channel_type, "channelType"),
        _u8(channel_id, "channelId"),
        len(common) // 4,
        len(channel) // 4,
        caps_offset,
    )
    body += common + channel
    return encode_spice_link_header(len(body)) + body


def decode_spice_link_mess(data):
    header = decode_spice_link_header(data)
    total = SPICE_LINK_HEADER_SIZE + header["size"]
    if len(data) < total:
        raise ValueError("SPICE link message is incomplete")
    body = data[SPICE_LINK_HEADER_SIZE:total]
    if len(body) < SPICE_LINK_MESS_BASE_SIZE:
        raise ValueError("SPICE link message body is incomplete")
    connection_id, channel_type, channel_id, num_common, num_channel, caps_offset = struct.unpack_from("<IBBIII", body, 0)
    return {
        "header": header,
        "connectionId": connection_id,
        "channelType": channel_type,
        "channelId": channel_id,
        "numCommonCaps": num_common,
        "numChannelCaps": num_channel,
        "capsOffset": caps_offset,
        "commonCaps": decode_capability_words(body, num_common, caps_offset),
        "channelCaps": decode_capability_words(body, num_channel, caps_offset + num_common * 4),
        "rest": data[total:],
    }


def _read_der_length(data, offset):
    if offset >= len(data):
        raise ValueError("DER length is missing")
    first = data[offset]
    if first & 0x80 == 0:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 4 or offset + 1 + count > len(data):
        raise ValueError("unsupported DER length")
    value = 0
    for index in range(count):
        value = (value << 8) | data[offset + 1 + index]
    return value, offset + 1 + count


def _read_der_tlv(data, offset, expected_tag=None):
    if offset >= len(data):
        raise ValueError("DER object is missing")
    tag = data[offset]
    if expected_tag is not None and tag != expected_tag:
        raise ValueError(f"DER tag 0x{expected_tag:02x} expected, got 0x{tag:02x}")
    length, value_offset = _read_der_length(data, offset + 1)
    end = value_offset + length
    if end > len(data):
        raise ValueError("DER object is incomplete")
    return tag, data[value_offset:end], end


def _der_integer_to_int(value):
    if not value:
        raise ValueError("DER integer is empty")
    while len(value) > 1 and value[0] == 0:
        value = value[1:]
    return int.from_bytes(value, "big")


def parse_rsa_public_key_der(der):
    """Parse SPKI or PKCS#1 RSA public key DER and return modulus/exponent."""
    der = bytes(der)
    _, outer, end = _read_der_tlv(der, 0, 0x30)
    if end != len(der):
        raise ValueError("trailing data after RSA public key DER")

    try:
        _, n_raw, cursor = _read_der_tlv(outer, 0, 0x02)
        _, e_raw, cursor = _read_der_tlv(outer, cursor, 0x02)
        if cursor == len(outer):
            n = _der_integer_to_int(n_raw)
            e = _der_integer_to_int(e_raw)
            return {"n": n, "e": e, "modulusBytes": max(1, (n.bit_length() + 7) // 8)}
    except ValueError:
        pass

    _, _algorithm, cursor = _read_der_tlv(outer, 0, 0x30)
    _, bit_string, cursor = _read_der_tlv(outer, cursor, 0x03)
    if cursor != len(outer):
        raise ValueError("unexpected trailing SPKI data")
    if not bit_string or bit_string[0] != 0:
        raise ValueError("unsupported RSA public key bit string")
    return parse_rsa_public_key_der(bit_string[1:])


def mgf1(seed, length, hash_name="sha1"):
    seed = bytes(seed)
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.new(hash_name, seed + struct.pack(">I", counter)).digest())
        counter += 1
    return bytes(out[:length])


def xor_bytes(left, right):
    return bytes(a ^ b for a, b in zip(left, right))


def rsa_oaep_encrypt(message, public_key_der, label=b"", seed=None, hash_name="sha1"):
    """RSAES-OAEP encrypt for SPICE ticket auth using only stdlib primitives."""
    key = parse_rsa_public_key_der(public_key_der)
    n = key["n"]
    e = key["e"]
    k = key["modulusBytes"]
    message = bytes(message)
    label = bytes(label)
    h_len = hashlib.new(hash_name).digest_size
    if len(message) > k - 2 * h_len - 2:
        raise ValueError("message too long for RSA-OAEP key")
    if seed is None:
        # Align with Go rsa.EncryptOAEP(sha1, rand.Reader, ...) which uses
        # a cryptographically random seed each time (non-deterministic).
        seed = os.urandom(h_len)
    seed = bytes(seed)
    if len(seed) != h_len:
        raise ValueError(f"OAEP seed must be {h_len} bytes")
    l_hash = hashlib.new(hash_name, label).digest()
    ps = b"\x00" * (k - len(message) - 2 * h_len - 2)
    db = l_hash + ps + b"\x01" + message
    db_mask = mgf1(seed, k - h_len - 1, hash_name)
    masked_db = xor_bytes(db, db_mask)
    seed_mask = mgf1(masked_db, h_len, hash_name)
    masked_seed = xor_bytes(seed, seed_mask)
    encoded = b"\x00" + masked_seed + masked_db
    cipher_int = pow(int.from_bytes(encoded, "big"), e, n)
    return cipher_int.to_bytes(k, "big")


def encode_spice_ticket(public_key_der, password=b"", seed=None):
    return rsa_oaep_encrypt(bytes(password), bytes(public_key_der), seed=seed)


def encode_mini_header(message_type, size):
    return struct.pack("<HI", _u16(message_type, "messageType"), _u32(size, "size"))


def decode_mini_header(data):
    if len(data) < MINI_HEADER_SIZE:
        raise ValueError("SPICE mini header is incomplete")
    message_type, size = struct.unpack_from("<HI", data, 0)
    return {"type": message_type, "size": size}


def encode_mini_message(message_type, payload=b""):
    payload = bytes(payload)
    return encode_mini_header(message_type, len(payload)) + payload


def decode_mini_message(data):
    header = decode_mini_header(data)
    total = MINI_HEADER_SIZE + header["size"]
    if len(data) < total:
        raise ValueError("SPICE mini message is incomplete")
    return {"header": header, "payload": data[MINI_HEADER_SIZE:total], "rest": data[total:]}


def encode_data_header(message_type, size, serial=0, sub_list=0):
    return struct.pack(
        "<QHII",
        _u64(serial, "serial"),
        _u16(message_type, "messageType"),
        _u32(size, "size"),
        _u32(sub_list, "subList"),
    )


def decode_data_header(data):
    if len(data) < DATA_HEADER_SIZE:
        raise ValueError("SPICE data header is incomplete")
    serial, message_type, size, sub_list = struct.unpack_from("<QHII", data, 0)
    return {"serial": serial, "type": message_type, "size": size, "subList": sub_list}


def encode_data_message(message_type, payload=b"", serial=0, sub_list=0):
    payload = bytes(payload)
    return encode_data_header(message_type, len(payload), serial=serial, sub_list=sub_list) + payload


def decode_data_message(data):
    header = decode_data_header(data)
    total = DATA_HEADER_SIZE + header["size"]
    if len(data) < total:
        raise ValueError("SPICE data message is incomplete")
    return {"header": header, "payload": data[DATA_HEADER_SIZE:total], "rest": data[total:]}


def encode_display_init(pixmap_cache_id=1, pixmap_cache_size=20 * 1024 * 1024, glz_dictionary_id=1, glz_dictionary_window_size=0x7FFC00):
    payload = struct.pack(
        "<BqBI",
        _u8(pixmap_cache_id, "pixmapCacheId"),
        int(pixmap_cache_size),
        _u8(glz_dictionary_id, "glzDictionaryId"),
        _u32(glz_dictionary_window_size, "glzDictionaryWindowSize"),
    )
    return encode_mini_message(SpiceMessage.DISPLAY_INIT, payload)


def decode_set_ack_payload(data):
    if len(data) < 8:
        raise ValueError("SET_ACK payload is incomplete")
    generation, window = struct.unpack_from("<II", data, 0)
    return {"generation": generation, "window": window}


def encode_ack_sync(generation):
    return encode_mini_message(SpiceMessage.ACK_SYNC, struct.pack("<I", _u32(generation, "generation")))


def encode_ack():
    return encode_mini_message(SpiceMessage.ACK, b"")


def encode_pong(ping_payload=b""):
    return encode_mini_message(SpiceMessage.PONG, bytes(ping_payload))


def encode_chuanyun_head(frame_type=ChuanyunFrameType.DATA, payload_length=0, session_id=0, channel_id=SpiceChannel.MAIN):
    return struct.pack(
        "<BBHIQQ",
        CHUANYUN_VERSION,
        _u8(frame_type, "frameType"),
        _u16(payload_length, "payloadLength"),
        0,
        _u64(session_id, "sessionId"),
        _u64(channel_id, "channelId"),
    )


def decode_chuanyun_head(data):
    if len(data) < CHUANYUN_HEAD_SIZE:
        raise ValueError("ChuanyunHead is incomplete")
    version, frame_type, payload_length, reserved, session_id, channel_id = struct.unpack_from("<BBHIQQ", data, 0)
    if version != CHUANYUN_VERSION:
        raise ValueError(f"unsupported ChuanyunHead version: {version}")
    return {
        "version": version,
        "type": frame_type,
        "payloadLength": payload_length,
        "reserved": reserved,
        "sessionId": session_id,
        "channelId": channel_id,
    }


def encode_chuanyun_frame(payload=b"", frame_type=ChuanyunFrameType.DATA, session_id=0, channel_id=SpiceChannel.MAIN):
    payload = bytes(payload)
    return encode_chuanyun_head(frame_type, len(payload), session_id, channel_id) + payload


def decode_chuanyun_frame(data):
    head = decode_chuanyun_head(data)
    total = CHUANYUN_HEAD_SIZE + head["payloadLength"]
    if len(data) < total:
        raise ValueError("Chuanyun frame is incomplete")
    return {"head": head, "payload": data[CHUANYUN_HEAD_SIZE:total], "rest": data[total:]}


def create_protocol_progress():
    return {
        "displayInitSent": False,
        "setAckReceived": False,
        "ackSyncSent": False,
        "pingReceived": False,
        "pongSent": False,
        "surfaceCreateReceived": False,
        "drawCopyReceived": False,
        "markReceived": False,
    }


def apply_protocol_event(progress, event):
    next_progress = dict(progress)
    mapping = {
        ProtocolStage.DISPLAY_INIT_SENT: "displayInitSent",
        ProtocolStage.SET_ACK_RECEIVED: "setAckReceived",
        ProtocolStage.ACK_SYNC_SENT: "ackSyncSent",
        ProtocolStage.PING_RECEIVED: "pingReceived",
        ProtocolStage.PONG_SENT: "pongSent",
        ProtocolStage.SURFACE_CREATE_RECEIVED: "surfaceCreateReceived",
        ProtocolStage.DRAW_COPY_RECEIVED: "drawCopyReceived",
        ProtocolStage.MARK_RECEIVED: "markReceived",
    }
    if event not in mapping:
        raise ValueError(f"unknown protocol event: {event}")
    next_progress[mapping[event]] = True
    return next_progress


def is_protocol_keepalive_success(progress):
    surface = bool(progress.get("surfaceCreateReceived"))
    draw = bool(progress.get("drawCopyReceived"))
    mark = bool(progress.get("markReceived"))
    return bool(progress.get("displayInitSent") and mark and (surface or draw))


def create_offline_display_proof():
    progress = create_protocol_progress()
    display_init = encode_display_init()
    progress = apply_protocol_event(progress, ProtocolStage.DISPLAY_INIT_SENT)

    set_ack_payload = struct.pack("<II", 1, 20)
    ping_payload = bytes.fromhex("0102030405060708")
    server_messages = [
        encode_data_message(SpiceMessage.SET_ACK, set_ack_payload, serial=1),
        encode_data_message(SpiceMessage.PING, ping_payload, serial=2),
        encode_data_message(SpiceMessage.SURFACE_CREATE, bytes(20), serial=3),
        encode_data_message(SpiceMessage.MARK, b"", serial=4),
    ]
    responses = []
    for raw in server_messages:
        message = decode_data_message(raw)
        message_type = message["header"]["type"]
        if message_type == SpiceMessage.SET_ACK:
            progress = apply_protocol_event(progress, ProtocolStage.SET_ACK_RECEIVED)
            responses.append(encode_ack_sync(decode_set_ack_payload(message["payload"])["generation"]))
            progress = apply_protocol_event(progress, ProtocolStage.ACK_SYNC_SENT)
        elif message_type == SpiceMessage.PING:
            progress = apply_protocol_event(progress, ProtocolStage.PING_RECEIVED)
            responses.append(encode_pong(message["payload"]))
            progress = apply_protocol_event(progress, ProtocolStage.PONG_SENT)
        elif message_type == SpiceMessage.SURFACE_CREATE:
            progress = apply_protocol_event(progress, ProtocolStage.SURFACE_CREATE_RECEIVED)
        elif message_type == SpiceMessage.DRAW_COPY:
            progress = apply_protocol_event(progress, ProtocolStage.DRAW_COPY_RECEIVED)
        elif message_type == SpiceMessage.MARK:
            progress = apply_protocol_event(progress, ProtocolStage.MARK_RECEIVED)
    return {
        "displayInit": display_init,
        "serverMessages": server_messages,
        "responses": responses,
        "progress": progress,
        "success": is_protocol_keepalive_success(progress),
    }
