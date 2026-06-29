"""Protocol parsing and framing helpers."""

import binascii
from collections import deque
import hashlib
import hmac
import math
import re
import struct
import time

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    Cipher = None
    algorithms = None
    modes = None

from bridge_server.constants import (
    ADV_FEAT1_MASK,
    ADV_FEAT2_MASK,
    ADV_LATLON_MASK,
    ADV_NAME_MASK,
    ADV_TYPE_SENSOR,
    BLOCK_STATS_COUNTER_WINDOW_SECS,
    BRIDGE_PACKET_FLAG_RF_RX,
    BRIDGE_PACKET_VERSION,
    BRIDGE_V2_OVERHEAD,
    CIPHER_BLOCK_SIZE,
    CIPHER_MAC_SIZE,
    COMMAND_TIMEOUT_SECS,
    CONTROL_PREFIX,
    CONTROL_TYPE_AUTH,
    CONTROL_TYPE_NAMES,
    CONTROL_TYPE_BRIDGE_PACKET,
    CONTROL_TYPE_CAPS,
    CONTROL_TYPE_COMMAND_REPLY,
    CONTROL_TYPE_HEARTBEAT,
    CONTROL_TYPE_NODE_INFO,
    IP_ADDRESS_RE,
    LOCATION_ROUTE_STATIONARY_METERS,
    LOCATION_ROUTE_STATIONARY_SECS,
    MAX_ADVERT_DATA_SIZE,
    OTA_CHECK_COMMAND_TIMEOUT_SECS,
    OTA_UPDATE_COMMAND_TIMEOUT_SECS,
    PACKET_COUNTER_WINDOW_SECS,
    PATH_HASH_SIZE,
    PAYLOAD_TYPE_ADVERT,
    PAYLOAD_TYPE_ANON_REQ,
    PAYLOAD_TYPE_GRP_DATA,
    PAYLOAD_TYPE_GRP_TXT,
    PAYLOAD_TYPE_MULTIPART,
    PAYLOAD_TYPE_NAMES,
    PAYLOAD_TYPE_PATH,
    PAYLOAD_TYPE_REQ,
    PAYLOAD_TYPE_RESPONSE,
    PAYLOAD_TYPE_TRACE,
    PAYLOAD_TYPE_TXT_MSG,
    PH_ROUTE_MASK,
    PH_TYPE_SHIFT,
    PUB_KEY_SIZE,
    ROUTE_TYPE_FLOOD,
    ROUTE_TYPE_NAMES,
    ROUTE_TYPE_TRANSPORT_DIRECT,
    ROUTE_TYPE_TRANSPORT_FLOOD,
    SIGNATURE_SIZE,
    SUPPORTED_BRIDGE_PACKET_VERSIONS,
    VERSION_RE,
)


def fletcher16(data: bytes) -> int:
    """Compute a Fletcher-16 checksum."""
    s1, s2 = (0, 0)
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return s2 << 8 | s1


def payload_preview(payload: bytes, max_bytes: int=32) -> str:
    """Return a shortened hexadecimal preview of a payload."""
    shown = payload[:max_bytes]
    text = binascii.hexlify(shown, sep=' ').decode('ascii')
    if len(payload) > len(shown):
        text += ' ...'
    return text


def packet_type_name(payload_type: int) -> str:
    """Return the display name for a payload type."""
    return PAYLOAD_TYPE_NAMES.get(payload_type, f'unknown-0x{payload_type:02x}')


def route_type_name(route_type: int) -> str:
    """Return the display name for a route type."""
    return ROUTE_TYPE_NAMES.get(route_type, f'unknown-0x{route_type:02x}')


def redact_public_text(value: str) -> str:
    """Redact IP addresses from public text."""
    return IP_ADDRESS_RE.sub('[hidden]', value)


def redact_public_value(value):
    """Recursively redact IP addresses from public values."""
    if isinstance(value, str):
        return redact_public_text(value)
    if isinstance(value, list):
        return [redact_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_public_value(item) for key, item in value.items() if key != 'addr'}
    return value


def derive_channel_secret(secret_hex: str) -> bytes | None:
    """Derive a fixed-width channel secret from hex text."""
    try:
        raw = bytes.fromhex(secret_hex.strip())
    except ValueError:
        return None
    if len(raw) == 16:
        return raw + b'\x00' * 16
    if len(raw) == 32:
        return raw
    return None


def channel_hash(secret: bytes) -> bytes:
    """Return the path hash for a channel secret."""
    key_len = 16 if secret[16:] == b'\x00' * 16 else 32
    return hashlib.sha256(secret[:key_len]).digest()[:PATH_HASH_SIZE]


def parse_semver(value: str) -> tuple[int, int, int] | None:
    """Parse a semantic version tuple from text."""
    match = VERSION_RE.search(value or '')
    if not match:
        return None
    return tuple((int(part) for part in match.groups()))


def version_is_older(current: str, latest: str) -> bool:
    """Return whether one semantic version is older than another."""
    current_version = parse_semver(current)
    latest_version = parse_semver(latest)
    if current_version is None or latest_version is None:
        return False
    return current_version < latest_version


def aes_ecb_decrypt(secret: bytes, data: bytes) -> bytes | None:
    """Decrypt AES-ECB payload bytes."""
    if Cipher is None or len(data) == 0 or len(data) % CIPHER_BLOCK_SIZE:
        return None
    cipher = Cipher(algorithms.AES(secret[:16]), modes.ECB())
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def mac_then_decrypt(secret: bytes, data: bytes) -> bytes | None:
    """Verify a MAC and decrypt the encrypted payload."""
    if len(data) <= CIPHER_MAC_SIZE:
        return None
    expected = hmac.new(secret, data[CIPHER_MAC_SIZE:], hashlib.sha256).digest()[:CIPHER_MAC_SIZE]
    if not hmac.compare_digest(expected, data[:CIPHER_MAC_SIZE]):
        return None
    return aes_ecb_decrypt(secret, data[CIPHER_MAC_SIZE:])


def trim_c_string(data: bytes) -> str:
    """Trim trailing null bytes from a C string."""
    data = data.split(b'\x00', 1)[0]
    return data.decode('utf-8', errors='replace').strip()


def decode_group_data_plain(plain: bytes) -> tuple[int, bytes] | None:
    """Decode a plain group-data payload."""
    if len(plain) < 3:
        return None
    data_type = plain[0] | plain[1] << 8
    data_len = plain[2]
    data = plain[3:3 + data_len]
    if len(data) < data_len:
        return None
    return (data_type, data)


def describe_peer_encrypted_payload(parsed: dict) -> dict:
    """Describe an encrypted peer payload."""
    payload_type = parsed['payload_type']
    app_payload = parsed['app_payload']
    result = {
        "peer_dest_hash": "",
        "peer_src_hash": "",
        "peer_mac": "",
        "peer_encrypted_len": None,
        "peer_encrypted_preview": "",
        "decoded_status": "",
        "decoded_text": "",
    }
    if payload_type not in (PAYLOAD_TYPE_REQ, PAYLOAD_TYPE_RESPONSE, PAYLOAD_TYPE_TXT_MSG, PAYLOAD_TYPE_PATH):
        return result
    if len(app_payload) < 4:
        result['decoded_status'] = 'short-peer-payload'
        result['decoded_text'] = f'encrypted peer payload too short ({len(app_payload)}B)'
        return result
    dest_hash = app_payload[0]
    src_hash = app_payload[1]
    encrypted = app_payload[4:]
    result['peer_dest_hash'] = f'{dest_hash:02x}'
    result['peer_src_hash'] = f'{src_hash:02x}'
    result['peer_mac'] = binascii.hexlify(app_payload[2:4]).decode('ascii')
    result['peer_encrypted_len'] = len(encrypted)
    result['peer_encrypted_preview'] = payload_preview(encrypted)
    result['decoded_status'] = 'encrypted-peer-payload'
    result['decoded_text'] = f'encrypted peer payload dest={dest_hash:02x} src={src_hash:02x} mac={result['peer_mac']} enc={len(encrypted)}B'
    return result


def parse_bridge_packet_envelope(payload: bytes) -> dict | None:
    """Parse a bridge-v2 packet envelope."""
    if len(payload) < BRIDGE_V2_OVERHEAD:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_BRIDGE_PACKET:
        return None
    if payload[5] not in SUPPORTED_BRIDGE_PACKET_VERSIONS:
        return None
    packet_len = struct.unpack('>H', payload[12:14])[0]
    if packet_len == 0 or len(payload) < BRIDGE_V2_OVERHEAD + packet_len:
        return None
    return {
        "version": payload[5],
        "ttl": payload[6],
        "origin_id": struct.unpack(">I", payload[7:11])[0],
        "flags": payload[11],
        "packet_len": packet_len,
        "mesh_payload": payload[BRIDGE_V2_OVERHEAD:BRIDGE_V2_OVERHEAD + packet_len],
    }


def parse_bridge_packet_error(payload: bytes) -> str:
    """Describe why a bridge-v2 envelope is invalid."""
    if not payload.startswith(CONTROL_PREFIX) or len(payload) < 5 or payload[4] != CONTROL_TYPE_BRIDGE_PACKET:
        return ''
    if len(payload) < BRIDGE_V2_OVERHEAD:
        return 'short bridge packet envelope'
    if payload[5] not in SUPPORTED_BRIDGE_PACKET_VERSIONS:
        return f'unsupported bridge packet version {payload[5]}'
    packet_len = struct.unpack('>H', payload[12:14])[0]
    if packet_len == 0:
        return 'zero mesh payload length'
    if len(payload) < BRIDGE_V2_OVERHEAD + packet_len:
        return f'truncated mesh payload {len(payload) - BRIDGE_V2_OVERHEAD}/{packet_len}'
    return ''


def mesh_payload_for_parsing(payload: bytes) -> bytes:
    """Return the mesh payload from a raw or bridge frame."""
    envelope = parse_bridge_packet_envelope(payload)
    if envelope is not None:
        return envelope['mesh_payload']
    return payload


def parse_mesh_payload(frame_payload: bytes) -> dict | None:
    """Parse a mesh payload header."""
    if len(frame_payload) < 2:
        return None
    header = frame_payload[0]
    payload_type = header >> PH_TYPE_SHIFT & 15
    route_type = header & PH_ROUTE_MASK
    pos = 1
    if route_type in (ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_TRANSPORT_DIRECT):
        if len(frame_payload) < pos + 4:
            return None
        pos += 4
    if len(frame_payload) <= pos:
        return None
    path_len = frame_payload[pos]
    pos += 1
    path_hash_size = (path_len >> 6) + 1
    path_hash_count = path_len & 63
    path_bytes = path_hash_size * path_hash_count
    if len(frame_payload) < pos + path_bytes:
        return None
    pos += path_bytes
    return {
        "payload_type": payload_type,
        "route_type": route_type,
        "path_len": path_len,
        "path_hash_count": path_hash_count,
        "app_payload": frame_payload[pos:],
    }


def parse_heartbeat(payload: bytes) -> dict | None:
    """Parse a bridge heartbeat payload."""
    if len(payload) < 5:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_HEARTBEAT:
        return None
    heartbeat = {'uptime_ms': struct.unpack('>I', payload[5:9])[0] if len(payload) >= 9 else 0}
    if len(payload) >= 28 and payload[9:11] == b'RF' and (payload[11] in (1, 2)):
        used_ms = struct.unpack('>I', payload[12:16])[0]
        max_ms = struct.unpack('>I', payload[16:20])[0]
        window_ms = struct.unpack('>I', payload[20:24])[0]
        duty_limit_centi_pct = struct.unpack('>H', payload[24:26])[0]
        used_centi_pct = struct.unpack('>H', payload[26:28])[0]
        heartbeat['rf_duty'] = {'tx_used_ms': used_ms, 'tx_max_ms': max_ms, 'window_ms': window_ms, 'duty_limit_pct': duty_limit_centi_pct / 100.0, 'tx_used_pct': min(100.0, used_centi_pct / 100.0)}
        if payload[11] >= 2 and len(payload) >= 32:
            heartbeat['rf_duty']['tx_total_ms'] = struct.unpack('>I', payload[28:32])[0]
        if window_ms > 0:
            heartbeat['rf_duty']['actual_window_pct'] = used_ms * 100.0 / window_ms
    if len(payload) >= 40 and payload[32:34] == b'RS' and (payload[34] >= 1):
        noise_floor = struct.unpack('>h', payload[35:37])[0]
        last_rssi = struct.unpack('>h', payload[37:39])[0]
        last_snr_qdb = struct.unpack('b', payload[39:40])[0]
        heartbeat['radio_stats'] = {'noise_floor': noise_floor, 'last_rssi': last_rssi, 'last_snr': last_snr_qdb / 4.0, 'last_snr_qdb': last_snr_qdb}
    if len(payload) >= 45 and payload[40:42] == b'NB' and (payload[42] >= 1):
        heartbeat['neighbor_count'] = struct.unpack('>H', payload[43:45])[0]
    if len(payload) >= 52 and payload[45:47] == b'HL' and (payload[47] >= 1):
        heartbeat['flood_hop_limit_drops'] = struct.unpack('>I', payload[48:52])[0]
    return heartbeat


def parse_node_info(payload: bytes) -> tuple[str, str, str] | None:
    """Parse a bridge node-info payload."""
    if len(payload) < 6:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_NODE_INFO:
        return None
    name_len = payload[5]
    raw_name = payload[6:6 + name_len]
    if len(raw_name) != name_len:
        return None
    name = raw_name.decode('utf-8', errors='replace').strip()[:32]
    firmware = ''
    version_len = 0
    version_pos = 6 + name_len
    if len(payload) > version_pos:
        version_len = payload[version_pos]
        raw_version = payload[version_pos + 1:version_pos + 1 + version_len]
        if len(raw_version) == version_len:
            firmware = raw_version.decode('utf-8', errors='replace').strip()[:32]
    node_id = ''
    node_id_pos = version_pos + 1 + version_len
    if len(payload) > node_id_pos:
        node_id_len = payload[node_id_pos]
        raw_node_id = payload[node_id_pos + 1:node_id_pos + 1 + node_id_len]
        if node_id_len in (4, 8, 32) and len(raw_node_id) == node_id_len:
            node_id = binascii.hexlify(raw_node_id).decode('ascii')
    return (name, firmware, node_id)


def parse_auth(payload: bytes) -> str | None:
    """Parse a bridge authentication payload."""
    if len(payload) < 6:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_AUTH:
        return None
    password_len = payload[5]
    raw_password = payload[6:6 + password_len]
    if len(raw_password) != password_len:
        return None
    return raw_password.decode('utf-8', errors='replace')


def parse_caps(payload: bytes) -> dict | None:
    """Parse a bridge capabilities payload."""
    if len(payload) < 7:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_CAPS:
        return None
    caps = {
        "version": payload[5],
        "flags": payload[6],
        "bridge_v2": bool(payload[6] & 1),
        "bridge_proto_ver": 1,
        "group": "",
        "rf_inject_budget_enabled": False,
        "rf_inject_max_per_min": 0,
        "rf_inject_max_airtime_ms_per_hour": 0,
        "rf_inject_block_duty_above_pct": 0.0,
        "bridge_id": 0,
    }
    if len(payload) >= 9:
        caps['bridge_proto_ver'] = payload[7] or 1
        group_len = min(payload[8], 15)
        group_start = 9
        group_end = group_start + group_len
        if len(payload) >= group_end:
            caps['group'] = payload[group_start:group_end].decode('utf-8', errors='replace').strip()
            pos = group_end
            if len(payload) >= pos + 9:
                caps['rf_inject_budget_enabled'] = payload[pos] != 0
                caps['rf_inject_max_per_min'] = struct.unpack('>H', payload[pos + 1:pos + 3])[0]
                caps['rf_inject_max_airtime_ms_per_hour'] = struct.unpack('>I', payload[pos + 3:pos + 7])[0]
                duty_centi = struct.unpack('>H', payload[pos + 7:pos + 9])[0]
                caps['rf_inject_block_duty_above_pct'] = duty_centi / 100.0
                pos += 9
            if len(payload) >= pos + 4:
                caps['bridge_id'] = struct.unpack('>I', payload[pos:pos + 4])[0]
    return caps


def parse_command_reply(payload: bytes) -> tuple[int, str] | None:
    """Parse a bridge command-reply payload."""
    if len(payload) < 10:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_COMMAND_REPLY:
        return None
    request_id = struct.unpack('>I', payload[5:9])[0]
    reply_len = payload[9]
    raw_reply = payload[10:10 + reply_len]
    if len(raw_reply) != reply_len:
        return None
    return (request_id, raw_reply.decode('utf-8', errors='replace'))


def command_timeout_for(command: str) -> int:
    """Return the timeout for a bridge command."""
    command = (command or '').strip().lower()
    if command.startswith('ota.update'):
        return OTA_UPDATE_COMMAND_TIMEOUT_SECS
    if command.startswith('ota.check'):
        return OTA_CHECK_COMMAND_TIMEOUT_SECS
    return COMMAND_TIMEOUT_SECS


def is_ota_update_command(command: str) -> bool:
    """Return whether a command triggers an OTA update."""
    return (command or '').strip().lower().startswith('ota.update')


def fnv1a64(data: bytes) -> int:
    """Compute the FNV-1a 64-bit hash."""
    value = 14695981039346656037
    for byte in data:
        value ^= byte
        value = value * 1099511628211 & 18446744073709551615
    return value


def packet_identity_for_dedupe(payload: bytes) -> bytes:
    """Return the identity bytes used for packet dedupe."""
    mesh = mesh_payload_for_parsing(payload)
    parsed = parse_mesh_payload(mesh)
    if not parsed:
        return mesh
    identity = bytes([parsed['payload_type']])
    if parsed['payload_type'] == PAYLOAD_TYPE_TRACE:
        identity += bytes([parsed['path_len']])
    return identity + parsed['app_payload']


def packet_fingerprint(payload: bytes) -> int:
    """Return the fingerprint for a packet."""
    return fnv1a64(packet_identity_for_dedupe(payload))


def fingerprint_hex(fingerprint: int) -> str:
    """Format a packet fingerprint as hexadecimal."""
    return f'{fingerprint:016x}'


def short_id_label(short_id: int | None) -> str:
    """Format a short source ID label."""
    return f'0x{short_id:02x}' if short_id is not None else 'unknown'


def decrement_bridge_ttl(payload: bytes) -> bytes | None:
    """Decrement a bridge-v2 TTL when possible."""
    envelope = parse_bridge_packet_envelope(payload)
    if envelope is None:
        return payload
    ttl = envelope['ttl']
    if ttl <= 1:
        return None
    forwarded = bytearray(payload)
    forwarded[6] = ttl - 1
    return bytes(forwarded)


def parse_location_report(payload: bytes) -> dict | None:
    """Parse a tracker location report."""
    if len(payload) < 32:
        return None
    if payload[:4] != b'MCL1' or payload[4] != 1:
        return None
    name_len = payload[31]
    if name_len > 24 or len(payload) < 32 + name_len:
        return None
    lat_microdeg = struct.unpack('>i', payload[10:14])[0]
    lon_microdeg = struct.unpack('>i', payload[14:18])[0]
    altitude_m = struct.unpack('>h', payload[18:20])[0]
    speed_cms = struct.unpack('>H', payload[20:22])[0]
    heading_cdeg = struct.unpack('>H', payload[22:24])[0]
    battery_mv = struct.unpack('>H', payload[25:27])[0]
    timestamp = struct.unpack('>I', payload[27:31])[0]
    name = payload[32:32 + name_len].decode('utf-8', errors='replace').strip()
    return {'version': payload[4], 'flags': payload[5], 'node_id': binascii.hexlify(payload[6:10]).decode('ascii'), 'lat': lat_microdeg / 1000000.0, 'lon': lon_microdeg / 1000000.0, 'altitude_m': altitude_m, 'speed_cms': speed_cms, 'speed_kmh': round(speed_cms * 0.036, 2), 'heading_cdeg': heading_cdeg, 'heading_deg': round(heading_cdeg / 100.0, 2), 'satellites': payload[24], 'battery_mv': battery_mv, 'timestamp': timestamp, 'name': name}


def is_transport_or_message_packet(parsed: dict | None) -> bool:
    """Return whether a payload counts toward transport limits."""
    if not parsed:
        return False
    if parsed['route_type'] in (ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_TRANSPORT_DIRECT):
        return True
    return parsed['payload_type'] in (PAYLOAD_TYPE_REQ, PAYLOAD_TYPE_RESPONSE, PAYLOAD_TYPE_TXT_MSG, PAYLOAD_TYPE_GRP_TXT, PAYLOAD_TYPE_GRP_DATA, PAYLOAD_TYPE_ANON_REQ, PAYLOAD_TYPE_MULTIPART)


def parse_advert_app_data(app_data: bytes) -> dict | None:
    """Parse advert application data."""
    if not app_data:
        return None
    flags = app_data[0]
    pos = 1
    advert = {
        "advert_type": flags & 15,
        "name": "",
        "lat": None,
        "lon": None,
        "feat1": None,
        "feat2": None,
    }
    if flags & ADV_LATLON_MASK:
        if len(app_data) < pos + 8:
            return None
        advert['lat'] = struct.unpack('<i', app_data[pos:pos + 4])[0] / 1000000.0
        pos += 4
        advert['lon'] = struct.unpack('<i', app_data[pos:pos + 4])[0] / 1000000.0
        pos += 4
    if flags & ADV_FEAT1_MASK:
        if len(app_data) < pos + 2:
            return None
        advert['feat1'] = struct.unpack('<H', app_data[pos:pos + 2])[0]
        pos += 2
    if flags & ADV_FEAT2_MASK:
        if len(app_data) < pos + 2:
            return None
        advert['feat2'] = struct.unpack('<H', app_data[pos:pos + 2])[0]
        pos += 2
    if flags & ADV_NAME_MASK:
        name_len = min(len(app_data) - pos, MAX_ADVERT_DATA_SIZE - pos)
        advert['name'] = app_data[pos:pos + name_len].decode('utf-8', errors='replace').strip()
    return advert


def parse_sensor_advert_payload(frame_payload: bytes) -> dict | None:
    """Parse a sensor advert payload."""
    parsed = parse_mesh_payload(frame_payload)
    if not parsed or parsed['payload_type'] != PAYLOAD_TYPE_ADVERT:
        return None
    payload = parsed['app_payload']
    advert_header_len = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE
    if len(payload) < advert_header_len:
        return None
    pub_key = payload[:PUB_KEY_SIZE]
    timestamp = struct.unpack('<I', payload[PUB_KEY_SIZE:PUB_KEY_SIZE + 4])[0]
    app_data = payload[advert_header_len:advert_header_len + MAX_ADVERT_DATA_SIZE]
    advert = parse_advert_app_data(app_data)
    if not advert or advert['advert_type'] != ADV_TYPE_SENSOR:
        return None
    return {'node_id': binascii.hexlify(pub_key).decode('ascii'), 'pubkey_prefix': binascii.hexlify(pub_key[:8]).decode('ascii'), 'timestamp': timestamp, 'name': advert['name'], 'lat': advert['lat'], 'lon': advert['lon'], 'feat1': advert['feat1'], 'feat2': advert['feat2'], 'hops': parsed['path_hash_count'], 'route_type': parsed['route_type']}


def parse_node_advert_payload(frame_payload: bytes) -> dict | None:
    """Parse a node advert payload."""
    parsed = parse_mesh_payload(frame_payload)
    if not parsed or parsed['payload_type'] != PAYLOAD_TYPE_ADVERT:
        return None
    payload = parsed['app_payload']
    advert_header_len = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE
    if len(payload) < advert_header_len:
        return None
    pub_key = payload[:PUB_KEY_SIZE]
    timestamp = struct.unpack('<I', payload[PUB_KEY_SIZE:PUB_KEY_SIZE + 4])[0]
    app_data = payload[advert_header_len:advert_header_len + MAX_ADVERT_DATA_SIZE]
    advert = parse_advert_app_data(app_data)
    if not advert:
        return None
    return {'node_id': binascii.hexlify(pub_key).decode('ascii'), 'pubkey_prefix': binascii.hexlify(pub_key[:8]).decode('ascii'), 'timestamp': timestamp, 'name': advert['name'], 'advert_type': advert['advert_type'], 'hops': parsed['path_hash_count']}


def location_track_point(report: dict) -> dict:
    """Build a persisted tracker point."""
    return {'node_id': report.get('node_id'), 'name': report.get('name', ''), 'lat': report['lat'], 'lon': report['lon'], 'altitude_m': report.get('altitude_m'), 'speed_kmh': report.get('speed_kmh'), 'heading_deg': report.get('heading_deg'), 'satellites': report.get('satellites'), 'battery_mv': report.get('battery_mv'), 'timestamp': report.get('timestamp'), 'received_at': report.get('received_at')}


def location_point_time(point: dict) -> int:
    """Return the best timestamp for a tracker point."""
    value = point.get('timestamp') or point.get('received_at') or int(time.time())
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(time.time())


def location_distance_m(a: dict, b: dict) -> float:
    """Compute distance between tracker points in meters."""
    lat1 = math.radians(float(a['lat']))
    lat2 = math.radians(float(b['lat']))
    dlat = lat2 - lat1
    dlon = math.radians(float(b['lon']) - float(a['lon']))
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.atan2(math.sqrt(hav), math.sqrt(max(0.0, 1.0 - hav)))


def mark_route_breaks(track: list[dict]) -> None:
    """Mark stationary route breaks within a tracker track."""
    anchor = None
    stationary_since = 0
    closed = False
    for point in track:
        if anchor is None:
            anchor = point
            stationary_since = location_point_time(point)
            closed = bool(point.get('route_break_after'))
            continue
        if location_distance_m(anchor, point) <= LOCATION_ROUTE_STATIONARY_METERS:
            point_time = location_point_time(point)
            if not closed and point_time - stationary_since >= LOCATION_ROUTE_STATIONARY_SECS:
                point['route_break_after'] = True
                closed = True
            continue
        anchor = point
        stationary_since = location_point_time(point)
        closed = False


def format_duration(seconds: float) -> str:
    """Format a duration for status output."""
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f'{days}d {hours}h {minutes}m'
    if hours:
        return f'{hours}h {minutes}m {seconds}s'
    if minutes:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'


def parse_block_duration_seconds(value: str) -> int:
    """Parse a block duration string into seconds."""
    value = (value or '').strip().lower()
    match = re.fullmatch('(\\d+)([smhd]?)', value)
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 'd':
        return amount * 86400
    if unit == 'h':
        return amount * 3600
    if unit == 'm':
        return amount * 60
    return amount


def parse_block_stats_reply(kind: str, reply: str) -> list[dict]:
    """Parse firmware block list replies from MyMesh or the TCPBridge mirror."""
    text = (reply or '').strip()
    if not text or text.lower().startswith('error'):
        return []
    if text.startswith('>'):
        text = text[1:].strip()
    for prefix in (f'{kind}.block', kind):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text or text.lower() in ('empty', 'none', 'ok'):
        return []
    entries: list[dict] = []
    for raw_entry in text.split(';'):
        entry = raw_entry.strip()
        if not entry:
            continue
        value = ''
        seconds_left = 0
        drops = 0
        parts = entry.split()
        if len(parts) >= 3:
            value = parts[0].strip().lower()
            seconds_left = parse_block_duration_seconds(parts[1])
            try:
                drops = max(0, int(parts[2]))
            except ValueError:
                drops = 0
        elif len(parts) == 1 and ':' in parts[0]:
            value, ttl = parts[0].split(':', 1)
            value = value.strip().lower()
            seconds_left = parse_block_duration_seconds(ttl)
        elif len(parts) >= 2:
            value = parts[0].strip().lower()
            seconds_left = parse_block_duration_seconds(parts[1])
        if not value:
            continue
        entries.append({'kind': kind, 'value': value, 'seconds_left': seconds_left, 'drops': drops})
    return entries


def new_block_drop_counter_state(now: float | None=None) -> dict:
    """Create a fresh block-drop counter state."""
    now = now or time.time()
    return {
        "window_started_at": now,
        "last": {"node": {}, "path": {}},
        "totals": {"node": 0, "path": 0},
    }


def update_block_drop_counters(state: dict, block_stats: dict, now: float | None=None) -> dict:
    """Update rolling block-drop totals."""
    now = now or time.time()
    if not state:
        state = new_block_drop_counter_state(now)
    if now - float(state.get('window_started_at') or now) >= BLOCK_STATS_COUNTER_WINDOW_SECS:
        state = new_block_drop_counter_state(now)
    last_by_kind = state.setdefault('last', {'node': {}, 'path': {}})
    totals = state.setdefault('totals', {'node': 0, 'path': 0})
    for kind in ('node', 'path'):
        previous = last_by_kind.setdefault(kind, {})
        for entry in block_stats.get(kind) or []:
            value = str(entry.get('value') or '').strip().lower()
            if not value:
                continue
            drops = max(0, int(entry.get('drops') or 0))
            old_drops = previous.get(value)
            if old_drops is None:
                increment = drops
            elif drops >= old_drops:
                increment = drops - old_drops
            else:
                increment = drops
            if increment > 0:
                totals[kind] = int(totals.get(kind) or 0) + increment
            previous[value] = drops
    block_stats['node_drops_24h'] = int(totals.get('node') or 0)
    block_stats['path_drops_24h'] = int(totals.get('path') or 0)
    block_stats['drop_window_started_at'] = state['window_started_at']
    block_stats['drop_window_resets_in_seconds'] = max(0, int(float(state['window_started_at']) + BLOCK_STATS_COUNTER_WINDOW_SECS - now))
    return state


def block_stats_totals(block_stats: dict) -> dict:
    """Summarize block statistics totals."""
    node_entries = block_stats.get('node') or []
    path_entries = block_stats.get('path') or []
    node_drops = int(block_stats['node_drops_24h'] if 'node_drops_24h' in block_stats else sum((int(entry.get('drops') or 0) for entry in node_entries)))
    path_drops = int(block_stats['path_drops_24h'] if 'path_drops_24h' in block_stats else sum((int(entry.get('drops') or 0) for entry in path_entries)))
    return {'node_active': len(node_entries), 'path_active': len(path_entries), 'node_drops': node_drops, 'path_drops': path_drops, 'total_drops': node_drops + path_drops, 'resets_in_seconds': int(block_stats.get('drop_window_resets_in_seconds') or 0)}


def format_sockaddrs(sockets) -> str:
    """Format bound socket addresses."""
    return ', '.join((f'{sock.getsockname()[0]}:{sock.getsockname()[1]}' for sock in sockets or []))


def prune_packet_times(packet_times: deque[float], now: float) -> None:
    """Prune packet timestamps outside the rolling window."""
    cutoff = now - PACKET_COUNTER_WINDOW_SECS
    while packet_times and packet_times[0] < cutoff:
        packet_times.popleft()


def prune_rate_window(times: deque[float], now: float, window_secs: int) -> None:
    """Prune timestamps outside a rate-limit window."""
    cutoff = now - max(1, window_secs)
    while times and times[0] < cutoff:
        times.popleft()


def has_node_identity(stats: dict) -> bool:
    """Return whether stats include a node name or ID."""
    return bool((stats.get('node_name') or '').strip() or (stats.get('node_id') or '').strip())


def make_node_stats_key(client: 'BridgeClient') -> str:
    """Build the stats key for a client."""
    if client.node_id:
        return f'node:{client.node_id.strip().lower()}'
    if client.node_name:
        return f'name:{client.node_name.strip().lower()}'
    return f'host:{client.host}'
