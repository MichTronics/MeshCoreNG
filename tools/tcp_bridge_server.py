#!/usr/bin/env python3
"""
MeshCore TCP Bridge Server

Forwards selected mesh packets between connected repeaters as a controlled
backhaul for geographically separated RF islands. This is deliberately not a
blind transparent internet mesh: TCP-only metadata, dedupe, origin IDs, TTL,
groups, rate limits, and RF-inject budgets constrain how packets move.

Usage:
    python3 tcp_bridge_server.py [--host 0.0.0.0] [--port 4200] [--password bridgeSecret]
    open http://localhost:8080/ for connected node status
    open http://localhost:8080/manage for remote management
    open http://localhost:8080/map for persisted tracker routes
    add --admin-password <secret> --allow-path-block-admin for web-admin path quarantine
    use --status-base-path /meshbridgestatus when reverse-proxying below a URL prefix

Repeater firmware configuration (via CLI):
    set wifi.ssid    <your-wifi>
    set wifi.password <your-password>
    set bridge.server <this-server-ip-or-hostname>
    set bridge.port   4200
    set bridge.password <bridgeSecret>  # only when server --password is set
    set bridge.enabled on

Requires Python 3.10+. Public channel decoding additionally needs:
    pip install cryptography
"""

import asyncio
import argparse
import base64
import binascii
from collections import OrderedDict, deque
import hashlib
import hmac
import html
import json
import logging
import math
import re
import struct
import time
from pathlib import Path
from typing import TypeAlias
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    Cipher = None
    algorithms = None
    modes = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tcp_bridge")

SERVER_NAME = "MeshCoreNG TCP Bridge Server"
SERVER_VERSION = "1.1.3"
BRIDGE_MAGIC = 0xC03E
MAX_PAYLOAD = 512   # Allows legacy raw packets and TCP bridge v2 envelopes.
CONTROL_PREFIX = b"MCNG"
CONTROL_TYPE_HEARTBEAT = 0x01
CONTROL_TYPE_NODE_INFO = 0x02
CONTROL_TYPE_AUTH = 0x03
CONTROL_TYPE_CAPS = 0x04
CONTROL_TYPE_COMMAND = 0x10
CONTROL_TYPE_COMMAND_REPLY = 0x11
CONTROL_TYPE_BRIDGE_PACKET = 0x20
BRIDGE_PACKET_VERSION = 1
BRIDGE_PACKET_FLAG_RF_RX = 0x01
BRIDGE_V2_OVERHEAD = 14
SUPPORTED_BRIDGE_PACKET_VERSIONS = {BRIDGE_PACKET_VERSION}
IP_ADDRESS_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\w.])"
    r"|(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F]{0,4}(?::\d{1,5})?(?![\w:])"
)
PAYLOAD_TYPE_ADVERT = 0x04
PAYLOAD_TYPE_REQ = 0x00
PAYLOAD_TYPE_RESPONSE = 0x01
PAYLOAD_TYPE_TXT_MSG = 0x02
PAYLOAD_TYPE_ACK = 0x03
PAYLOAD_TYPE_PATH = 0x08
PAYLOAD_TYPE_ANON_REQ = 0x07
PAYLOAD_TYPE_MULTIPART = 0x0A
PAYLOAD_TYPE_GRP_TXT = 0x05
PAYLOAD_TYPE_GRP_DATA = 0x06
PAYLOAD_TYPE_TRACE = 0x09
PAYLOAD_TYPE_LOCATION = 0x0D
DATA_TYPE_MESHCORENG_TRACKER = 0x0200
PH_ROUTE_MASK = 0x03
PH_TYPE_SHIFT = 2
ROUTE_TYPE_TRANSPORT_FLOOD = 0x00
ROUTE_TYPE_FLOOD = 0x01
ROUTE_TYPE_DIRECT = 0x02
ROUTE_TYPE_TRANSPORT_DIRECT = 0x03
ADV_TYPE_SENSOR = 0x04
ADV_TYPE_REPEATER = 0x02
ADV_LATLON_MASK = 0x10
ADV_FEAT1_MASK = 0x20
ADV_FEAT2_MASK = 0x40
ADV_NAME_MASK = 0x80
PUB_KEY_SIZE = 32
SIGNATURE_SIZE = 64
MAX_ADVERT_DATA_SIZE = 32
PATH_HASH_SIZE = 1
CIPHER_MAC_SIZE = 2
CIPHER_BLOCK_SIZE = 16
DEFAULT_PUBLIC_CHANNEL_SECRET = bytes([
    0x8B, 0x33, 0x87, 0xE9, 0xC5, 0xCD, 0xEA, 0x6A,
    0xC9, 0xE5, 0xED, 0xBA, 0xA1, 0x15, 0xCD, 0x72,
]) + (b"\x00" * 16)
DEFAULT_TRACKER_CHANNEL_SECRET = bytes([
    0x5F, 0x30, 0x3A, 0xC5, 0x07, 0x5F, 0x80, 0x0F,
    0x0F, 0x47, 0x11, 0x31, 0x99, 0xD5, 0x10, 0x53,
]) + (b"\x00" * 16)
LOG_PACKETS = False
LOG_HEX_BYTES = 32
PUBLIC_CHANNELS_FILE = ""
CLIENT_TIMEOUT_SECS = 180
STATUS_INTERVAL_SECS = 60
PACKET_COUNTER_WINDOW_SECS = 24 * 60 * 60
LOCATION_TRACKS_DIR = Path("logs/location_tracks")
LOCATION_ROUTE_STATIONARY_SECS = 30 * 60
LOCATION_ROUTE_STATIONARY_METERS = 30
TRANSPORT_RATE_LIMIT_ENABLE = True
TRANSPORT_RATE_LIMIT_MAX = 20
TRANSPORT_RATE_LIMIT_WINDOW_SECS = 120
TRANSPORT_GLOBAL_RATE_LIMIT_MAX = 80
TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS = 120
REPLACE_SAME_IP = False
BRIDGE_PASSWORD = ""
ADMIN_PASSWORD = ""
ALLOW_PATH_BLOCK_ADMIN = False
STATUS_BASE_PATH = "/meshbridgestatus"
COMMAND_TIMEOUT_SECS = 8
OTA_CHECK_COMMAND_TIMEOUT_SECS = 25
OTA_UPDATE_COMMAND_TIMEOUT_SECS = 120
FIRMWARE_UPDATE_REPO = "MichTronics/MeshCoreNG"
FIRMWARE_UPDATE_CHECK_INTERVAL_SECS = 60 * 60
FIRMWARE_UPDATE_TIMEOUT_SECS = 5
CLIENT_TX_QUEUE_MAX = 250
BRIDGE_DEDUPE_ENABLED = True
BRIDGE_DEDUPE_TTL_SECS = 300
BRIDGE_DEDUPE_MAX_ENTRIES = 4096
BRIDGE_LOOPGUARD_ENABLED = True
BRIDGE_LOOPGUARD_WINDOW_SECS = 60
BRIDGE_LOOPGUARD_THRESHOLD = 5
BRIDGE_LOOPGUARD_QUARANTINE_SECS = 120
BRIDGE_RF_INJECT_ENABLED = False
BRIDGE_RF_INJECT_MAX_PER_MIN = 0
BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR = 0
BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT = 0.0
BRIDGE_GROUP = "default"
BRIDGE_REQUIRE_GROUP_MATCH = False
SHORT_ID_QUARANTINE_ENABLED = False
SHORT_ID_QUARANTINE_WINDOW_SECS = 60
SHORT_ID_QUARANTINE_THRESHOLD = 5
SHORT_ID_QUARANTINE_SECS = 900
BLOCK_STATS_POLL_INTERVAL_SECS = 15
BLOCK_STATS_COMMAND_TIMEOUT_SECS = 4
BLOCK_STATS_COUNTER_WINDOW_SECS = 24 * 60 * 60
next_command_id = 1
next_client_id = 1
SERVER_STARTED_AT = time.time()

connected_clients: set["BridgeClient"] = set()
node_traffic_stats: dict[str, dict] = {}
latest_locations: dict[str, dict] = {}
latest_location_tracks: dict[str, list[dict]] = {}
latest_location_stationary: dict[str, dict] = {}
latest_sensors: dict[str, dict] = {}
transport_rx_times: deque[float] = deque()
transport_rate_dropped = 0
recent_packets: deque[dict] = deque(maxlen=200)
pending_commands: dict[int, asyncio.Future] = {}
packet_fingerprint_cache: OrderedDict[int, dict] = OrderedDict()
bridge_guard_counters: dict[str, int] = {}
short_id_quarantine: dict[int, dict] = {}
short_id_bad_hits: dict[int, deque[float]] = {}
packet_log_total = 0
public_channels: list[dict] = []
latest_firmware_info: dict = {
    "enabled": True,
    "checked_at": 0,
    "latest_version": "",
    "latest_tag": "",
    "latest_url": "",
    "asset_count": 0,
    "bin_count": 0,
    "merged_bin_count": 0,
    "status": "pending",
    "error": "",
}

HttpResponse: TypeAlias = tuple[str, str, bytes, list[tuple[str, str]]]

PAYLOAD_TYPE_NAMES = {
    0x00: "request",
    0x01: "response",
    0x02: "text-message",
    0x03: "ack",
    0x04: "advert",
    0x05: "group-text",
    0x06: "group-data",
    0x07: "anon-request",
    0x08: "path",
    0x09: "trace",
    0x0A: "multipart",
    0x0B: "control",
    0x0C: "atlas",
    0x0D: "location",
    0x0F: "raw-custom",
}

ROUTE_TYPE_NAMES = {
    0x00: "transport-flood",
    0x01: "flood",
    0x02: "direct",
    0x03: "transport-direct",
}

CONTROL_TYPE_NAMES = {
    CONTROL_TYPE_HEARTBEAT: "heartbeat",
    CONTROL_TYPE_NODE_INFO: "node-info",
    CONTROL_TYPE_AUTH: "auth",
    CONTROL_TYPE_CAPS: "capabilities",
    CONTROL_TYPE_COMMAND: "command",
    CONTROL_TYPE_COMMAND_REPLY: "command-reply",
    CONTROL_TYPE_BRIDGE_PACKET: "bridge-v2-packet",
}


def inc_bridge_guard_counter(name: str, amount: int = 1) -> None:
    bridge_guard_counters[name] = bridge_guard_counters.get(name, 0) + amount


def fletcher16(data: bytes) -> int:
    s1, s2 = 0, 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def payload_preview(payload: bytes) -> str:
    shown = payload[:LOG_HEX_BYTES]
    text = binascii.hexlify(shown, sep=" ").decode("ascii")
    if len(payload) > len(shown):
        text += " ..."
    return text


def packet_type_name(payload_type: int) -> str:
    return PAYLOAD_TYPE_NAMES.get(payload_type, f"unknown-0x{payload_type:02x}")


def route_type_name(route_type: int) -> str:
    return ROUTE_TYPE_NAMES.get(route_type, f"unknown-0x{route_type:02x}")


def redact_public_text(value: str) -> str:
    return IP_ADDRESS_RE.sub("[hidden]", value)


def redact_public_value(value):
    if isinstance(value, str):
        return redact_public_text(value)
    if isinstance(value, list):
        return [redact_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_public_value(item) for key, item in value.items() if key != "addr"}
    return value


def derive_channel_secret(secret_hex: str) -> bytes | None:
    try:
        raw = bytes.fromhex(secret_hex.strip())
    except ValueError:
        return None
    if len(raw) == 16:
        return raw + (b"\x00" * 16)
    if len(raw) == 32:
        return raw
    return None


def channel_hash(secret: bytes) -> bytes:
    key_len = 16 if secret[16:] == b"\x00" * 16 else 32
    return hashlib.sha256(secret[:key_len]).digest()[:PATH_HASH_SIZE]


def add_public_channel(name: str, secret: bytes) -> None:
    if any(ch["name"] == name and ch["secret"] == secret for ch in public_channels):
        return
    public_channels.append({
        "name": name,
        "secret": secret,
        "hash": channel_hash(secret),
    })


def load_public_channels(path: str) -> None:
    public_channels.clear()
    add_public_channel("Public", DEFAULT_PUBLIC_CHANNEL_SECRET)
    add_public_channel("Trackers", DEFAULT_TRACKER_CHANNEL_SECRET)
    if not path:
        return
    if Cipher is None:
        log.warning("Public channel decoding disabled: install python package 'cryptography'")
        return

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Public channel decoding disabled: cannot read %s (%s)", path, exc)
        return

    if isinstance(data, dict):
        data = data.get("channels", [])
    if not isinstance(data, list):
        log.warning("Public channel decoding disabled: %s must contain a list or {'channels': [...]} object", path)
        return

    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        secret = derive_channel_secret(str(item.get("secret", "")))
        if not name or secret is None:
            log.warning("Skipping invalid public channel entry: %s", item)
            continue
        add_public_channel(name, secret)

    log.info("Loaded %d public channel key(s) from %s", len(public_channels), path)


VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z._-]+)?")
FIRMWARE_RELEASE_TAG_RE = re.compile(
    r"^(?:v|bridge-tcp-v|bridge-tcp-ble-v)(\d+\.\d+\.\d+)(?:[-+].*)?$"
)


def parse_semver(value: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(value or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def version_is_older(current: str, latest: str) -> bool:
    current_version = parse_semver(current)
    latest_version = parse_semver(latest)
    if current_version is None or latest_version is None:
        return False
    return current_version < latest_version


def firmware_update_status(current_version: str) -> dict:
    latest = latest_firmware_info.get("latest_version", "")
    if not current_version:
        state = "unknown"
    elif latest and version_is_older(current_version, latest):
        state = "available"
    elif latest and parse_semver(current_version):
        state = "current"
    else:
        state = "unknown"
    return {
        "state": state,
        "current_version": current_version,
        "latest_version": latest,
        "latest_tag": latest_firmware_info.get("latest_tag", ""),
        "latest_url": latest_firmware_info.get("latest_url", ""),
        "asset_count": latest_firmware_info.get("asset_count", 0),
        "bin_count": latest_firmware_info.get("bin_count", 0),
        "merged_bin_count": latest_firmware_info.get("merged_bin_count", 0),
        "checked_at": latest_firmware_info.get("checked_at", 0),
        "check_status": latest_firmware_info.get("status", "disabled"),
        "error": latest_firmware_info.get("error", ""),
    }


def fetch_latest_firmware_info(repo: str, timeout: int) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "MeshCoreNG TCP Bridge Server",
    })
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    best: tuple[int, int, int] | None = None
    best_tag = ""
    best_url = ""
    best_asset_count = 0
    best_bin_count = 0
    best_merged_bin_count = 0
    for item in payload:
        tag = str(item.get("tag_name", ""))
        match = FIRMWARE_RELEASE_TAG_RE.match(tag)
        if not match:
            continue
        assets = item.get("assets") or []
        asset_names = [str(asset.get("name", "")) for asset in assets if isinstance(asset, dict)]
        bin_count = sum(1 for name in asset_names if name.endswith(".bin"))
        if bin_count == 0:
            continue
        version = parse_semver(match.group(1))
        if version is None:
            continue
        prefer_tag = best_tag.startswith("v") and not tag.startswith("v")
        if best is None or version > best or (version == best and prefer_tag):
            best = version
            best_tag = tag
            best_url = str(item.get("html_url", "")) or f"https://github.com/{repo}/releases/tag/{tag}"
            best_asset_count = len(asset_names)
            best_bin_count = bin_count
            best_merged_bin_count = sum(1 for name in asset_names if name.endswith("-merged.bin"))

    if best is None:
        raise ValueError(f"no firmware releases with .bin assets found in {repo}")

    latest_version = f"v{best[0]}.{best[1]}.{best[2]}"
    return {
        "enabled": True,
        "checked_at": int(time.time()),
        "latest_version": latest_version,
        "latest_tag": best_tag,
        "latest_url": best_url,
        "asset_count": best_asset_count,
        "bin_count": best_bin_count,
        "merged_bin_count": best_merged_bin_count,
        "status": "ok",
        "error": "",
    }


async def firmware_update_task(repo: str, interval_secs: int, timeout_secs: int) -> None:
    latest_firmware_info.update({
        "enabled": bool(repo and interval_secs > 0),
        "status": "disabled" if not repo or interval_secs <= 0 else "pending",
        "error": "",
    })
    if not repo or interval_secs <= 0:
        return

    while True:
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(None, fetch_latest_firmware_info, repo, timeout_secs)
            latest_firmware_info.update(info)
            log.info("Latest firmware release: %s (%s)", info["latest_version"], info["latest_tag"])
        except (URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
            latest_firmware_info.update({
                "enabled": True,
                "checked_at": int(time.time()),
                "status": "error",
                "error": str(exc),
            })
            log.warning("Firmware update check failed: %s", exc)
        await asyncio.sleep(interval_secs)


def aes_ecb_decrypt(secret: bytes, data: bytes) -> bytes | None:
    if Cipher is None or len(data) == 0 or len(data) % CIPHER_BLOCK_SIZE:
        return None
    cipher = Cipher(algorithms.AES(secret[:16]), modes.ECB())
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


def mac_then_decrypt(secret: bytes, data: bytes) -> bytes | None:
    if len(data) <= CIPHER_MAC_SIZE:
        return None
    expected = hmac.new(secret, data[CIPHER_MAC_SIZE:], hashlib.sha256).digest()[:CIPHER_MAC_SIZE]
    if not hmac.compare_digest(expected, data[:CIPHER_MAC_SIZE]):
        return None
    return aes_ecb_decrypt(secret, data[CIPHER_MAC_SIZE:])


def trim_c_string(data: bytes) -> str:
    data = data.split(b"\x00", 1)[0]
    return data.decode("utf-8", errors="replace").strip()


def decrypt_public_channel_plain(parsed: dict) -> tuple[str, bytes] | None:
    app_payload = parsed.get("app_payload") or b""
    if not public_channels or len(app_payload) <= PATH_HASH_SIZE + CIPHER_MAC_SIZE:
        return None
    packet_hash = app_payload[:PATH_HASH_SIZE]
    encrypted = app_payload[PATH_HASH_SIZE:]
    for channel in (ch for ch in public_channels if ch["hash"] == packet_hash):
        plain = mac_then_decrypt(channel["secret"], encrypted)
        if plain:
            return channel["name"], plain
    return None


def decode_group_data_plain(plain: bytes) -> tuple[int, bytes] | None:
    if len(plain) < 3:
        return None
    data_type = plain[0] | (plain[1] << 8)
    data_len = plain[2]
    data = plain[3:3 + data_len]
    if len(data) < data_len:
        return None
    return data_type, data


def decode_public_channel_payload(parsed: dict) -> dict:
    result = {
        "decoded_channel": "",
        "decoded_status": "",
        "decoded_text": "",
        "decoded_data_type": None,
        "decoded_data_len": None,
    }
    payload_type = parsed.get("payload_type")
    app_payload = parsed.get("app_payload") or b""
    if payload_type not in (PAYLOAD_TYPE_GRP_TXT, PAYLOAD_TYPE_GRP_DATA):
        return result
    if not public_channels:
        result["decoded_status"] = "channel-keys-not-loaded"
        return result
    if len(app_payload) <= PATH_HASH_SIZE + CIPHER_MAC_SIZE:
        result["decoded_status"] = "short-group-payload"
        return result

    if not any(ch["hash"] == app_payload[:PATH_HASH_SIZE] for ch in public_channels):
        result["decoded_status"] = "unknown-channel"
        return result

    decoded = decrypt_public_channel_plain(parsed)
    if decoded is None:
        result["decoded_status"] = "mac-failed"
        return result
    channel_name, plain = decoded
    result["decoded_channel"] = channel_name
    if payload_type == PAYLOAD_TYPE_GRP_TXT:
        if len(plain) < 5:
            result["decoded_status"] = "short-group-text"
            return result
        txt_type = plain[4]
        if (txt_type >> 2) != 0:
            result["decoded_status"] = f"unsupported-text-type-{txt_type}"
            return result
        result["decoded_status"] = "decoded"
        result["decoded_text"] = trim_c_string(plain[5:])
        return result

    group_data = decode_group_data_plain(plain)
    if group_data is None:
        result["decoded_status"] = "short-group-data"
        return result
    data_type, data = group_data
    result["decoded_status"] = "decoded"
    result["decoded_data_type"] = data_type
    result["decoded_data_len"] = len(data)
    result["decoded_text"] = f"data_type=0x{data_type:04x} len={len(data)} preview={payload_preview(data)}"
    return result



def describe_peer_encrypted_payload(parsed: dict) -> dict:
    payload_type = parsed["payload_type"]
    app_payload = parsed["app_payload"]
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
        result["decoded_status"] = "short-peer-payload"
        result["decoded_text"] = f"encrypted peer payload too short ({len(app_payload)}B)"
        return result

    dest_hash = app_payload[0]
    src_hash = app_payload[1]
    encrypted = app_payload[4:]
    result["peer_dest_hash"] = f"{dest_hash:02x}"
    result["peer_src_hash"] = f"{src_hash:02x}"
    result["peer_mac"] = binascii.hexlify(app_payload[2:4]).decode("ascii")
    result["peer_encrypted_len"] = len(encrypted)
    result["peer_encrypted_preview"] = payload_preview(encrypted)
    result["decoded_status"] = "encrypted-peer-payload"
    result["decoded_text"] = (
        f"encrypted peer payload dest={dest_hash:02x} src={src_hash:02x} "
        f"mac={result['peer_mac']} enc={len(encrypted)}B"
    )
    return result


def describe_packet(payload: bytes) -> dict:
    envelope = parse_bridge_packet_envelope(payload)
    mesh_payload = envelope["mesh_payload"] if envelope is not None else payload
    description = {
        "kind": "mesh",
        "type": "unknown",
        "type_id": None,
        "route": "unknown",
        "route_id": None,
        "hops": None,
        "app_len": None,
        "bridge_v2": envelope is not None,
        "ttl": envelope["ttl"] if envelope is not None else None,
        "origin_id": f"0x{envelope['origin_id']:08x}" if envelope is not None else "",
        "flags": f"0x{envelope['flags']:02x}" if envelope is not None else "",
        "mesh_len": len(mesh_payload),
        "decoded_channel": "",
        "decoded_status": "",
        "decoded_text": "",
        "decoded_data_type": None,
        "decoded_data_len": None,
        "peer_dest_hash": "",
        "peer_src_hash": "",
        "peer_mac": "",
        "peer_encrypted_len": None,
        "peer_encrypted_preview": "",
    }

    if envelope is None and payload.startswith(CONTROL_PREFIX):
        control_type = payload[4] if len(payload) > 4 else None
        description.update({
            "kind": "control",
            "type": CONTROL_TYPE_NAMES.get(control_type, f"control-0x{control_type:02x}" if control_type is not None else "control"),
            "type_id": control_type,
            "route": "",
            "route_id": None,
            "hops": None,
            "app_len": max(0, len(payload) - 5),
            "mesh_len": 0,
        })
        return description

    parsed = parse_mesh_payload(mesh_payload)
    if parsed is None:
        return description

    payload_type = parsed["payload_type"]
    route_type = parsed["route_type"]
    public_decode = decode_public_channel_payload(parsed)
    peer_info = describe_peer_encrypted_payload(parsed)
    description.update({
        "type": packet_type_name(payload_type),
        "type_id": payload_type,
        "route": route_type_name(route_type),
        "route_id": route_type,
        "hops": parsed["path_hash_count"],
        "app_len": len(parsed["app_payload"]),
        **public_decode,
    })
    if not description.get("decoded_status"):
        description.update(peer_info)
    else:
        description.update({
            "peer_dest_hash": peer_info["peer_dest_hash"],
            "peer_src_hash": peer_info["peer_src_hash"],
            "peer_mac": peer_info["peer_mac"],
            "peer_encrypted_len": peer_info["peer_encrypted_len"],
            "peer_encrypted_preview": peer_info["peer_encrypted_preview"],
        })
    return description


def format_packet_description(description: dict) -> str:
    parts = [f"type={description['type']}"]
    if description.get("route"):
        parts.append(f"route={description['route']}")
    if description.get("decoded_channel"):
        parts.append(f"channel={description['decoded_channel']}")
    if description.get("hops") is not None:
        parts.append(f"hops={description['hops']}")
    if description.get("app_len") is not None:
        parts.append(f"app={description['app_len']}B")
    if description.get("bridge_v2"):
        parts.append(f"bridge-v2 ttl={description['ttl']} origin={description['origin_id']}")
    if description.get("source_short_id"):
        parts.append(f"sid={description['source_short_id']}")
    return " ".join(parts)


def record_packet_log(
    direction: str,
    client: "BridgeClient",
    payload: bytes,
    source: str = "",
    target: str = "",
) -> dict:
    global packet_log_total

    description = describe_packet(payload)
    mesh_payload = mesh_payload_for_parsing(payload)
    short_id = source_short_id(payload)
    source = source or ("server" if direction == "TX" else client.display_name)
    target = target or (client.display_name if direction == "TX" else "server")
    entry = {
        "time": int(time.time()),
        "direction": direction,
        "client": client.display_name,
        "client_id": client.client_id,
        "node_id": client.node_id,
        "source": source,
        "target": target,
        "flow": f"{source} -> {target}",
        "size": len(payload),
        "preview": payload_preview(mesh_payload if description.get("bridge_v2") else payload),
        "source_short_id": short_id_label(short_id) if short_id is not None else "",
        **description,
    }
    packet_log_total += 1
    recent_packets.appendleft(entry)
    return entry


def parse_heartbeat(payload: bytes) -> dict | None:
    if len(payload) < 5:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_HEARTBEAT:
        return None
    heartbeat = {
        "uptime_ms": struct.unpack(">I", payload[5:9])[0] if len(payload) >= 9 else 0,
    }
    if len(payload) >= 28 and payload[9:11] == b"RF" and payload[11] in (1, 2):
        used_ms = struct.unpack(">I", payload[12:16])[0]
        max_ms = struct.unpack(">I", payload[16:20])[0]
        window_ms = struct.unpack(">I", payload[20:24])[0]
        duty_limit_centi_pct = struct.unpack(">H", payload[24:26])[0]
        used_centi_pct = struct.unpack(">H", payload[26:28])[0]
        heartbeat["rf_duty"] = {
            "tx_used_ms": used_ms,
            "tx_max_ms": max_ms,
            "window_ms": window_ms,
            "duty_limit_pct": duty_limit_centi_pct / 100.0,
            "tx_used_pct": min(100.0, used_centi_pct / 100.0),
        }
        if payload[11] >= 2 and len(payload) >= 32:
            heartbeat["rf_duty"]["tx_total_ms"] = struct.unpack(">I", payload[28:32])[0]
        if window_ms > 0:
            heartbeat["rf_duty"]["actual_window_pct"] = (used_ms * 100.0) / window_ms
    if len(payload) >= 40 and payload[32:34] == b"RS" and payload[34] >= 1:
        noise_floor = struct.unpack(">h", payload[35:37])[0]
        last_rssi = struct.unpack(">h", payload[37:39])[0]
        last_snr_qdb = struct.unpack("b", payload[39:40])[0]
        heartbeat["radio_stats"] = {
            "noise_floor": noise_floor,
            "last_rssi": last_rssi,
            "last_snr": last_snr_qdb / 4.0,
            "last_snr_qdb": last_snr_qdb,
        }
    if len(payload) >= 45 and payload[40:42] == b"NB" and payload[42] >= 1:
        heartbeat["neighbor_count"] = struct.unpack(">H", payload[43:45])[0]
    if len(payload) >= 52 and payload[45:47] == b"HL" and payload[47] >= 1:
        heartbeat["flood_hop_limit_drops"] = struct.unpack(">I", payload[48:52])[0]
    return heartbeat


def parse_node_info(payload: bytes) -> tuple[str, str, str] | None:
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

    name = raw_name.decode("utf-8", errors="replace").strip()[:32]

    firmware = ""
    version_len = 0
    version_pos = 6 + name_len
    if len(payload) > version_pos:
        version_len = payload[version_pos]
        raw_version = payload[version_pos + 1:version_pos + 1 + version_len]
        if len(raw_version) == version_len:
            firmware = raw_version.decode("utf-8", errors="replace").strip()[:32]
    node_id = ""
    node_id_pos = version_pos + 1 + version_len
    if len(payload) > node_id_pos:
        node_id_len = payload[node_id_pos]
        raw_node_id = payload[node_id_pos + 1:node_id_pos + 1 + node_id_len]
        if node_id_len in (4, 8, 32) and len(raw_node_id) == node_id_len:
            node_id = binascii.hexlify(raw_node_id).decode("ascii")

    return name, firmware, node_id


def parse_auth(payload: bytes) -> str | None:
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

    return raw_password.decode("utf-8", errors="replace")


def parse_caps(payload: bytes) -> dict | None:
    if len(payload) < 7:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_CAPS:
        return None
    caps = {
        "version": payload[5],
        "flags": payload[6],
        "bridge_v2": bool(payload[6] & 0x01),
        "bridge_proto_ver": 1,
        "group": "",
        "rf_inject_budget_enabled": False,
        "rf_inject_max_per_min": 0,
        "rf_inject_max_airtime_ms_per_hour": 0,
        "rf_inject_block_duty_above_pct": 0.0,
        "bridge_id": 0,
    }
    if len(payload) >= 9:
        caps["bridge_proto_ver"] = payload[7] or 1
        group_len = min(payload[8], 15)
        group_start = 9
        group_end = group_start + group_len
        if len(payload) >= group_end:
            caps["group"] = payload[group_start:group_end].decode("utf-8", errors="replace").strip()
            pos = group_end
            if len(payload) >= pos + 9:
                caps["rf_inject_budget_enabled"] = payload[pos] != 0
                caps["rf_inject_max_per_min"] = struct.unpack(">H", payload[pos + 1:pos + 3])[0]
                caps["rf_inject_max_airtime_ms_per_hour"] = struct.unpack(">I", payload[pos + 3:pos + 7])[0]
                duty_centi = struct.unpack(">H", payload[pos + 7:pos + 9])[0]
                caps["rf_inject_block_duty_above_pct"] = duty_centi / 100.0
                pos += 9
            if len(payload) >= pos + 4:
                caps["bridge_id"] = struct.unpack(">I", payload[pos:pos + 4])[0]
    return caps


def parse_command_reply(payload: bytes) -> tuple[int, str] | None:
    if len(payload) < 10:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_COMMAND_REPLY:
        return None

    request_id = struct.unpack(">I", payload[5:9])[0]
    reply_len = payload[9]
    raw_reply = payload[10:10 + reply_len]
    if len(raw_reply) != reply_len:
        return None

    return request_id, raw_reply.decode("utf-8", errors="replace")


def command_timeout_for(command: str) -> int:
    command = (command or "").strip().lower()
    if command.startswith("ota.update"):
        return OTA_UPDATE_COMMAND_TIMEOUT_SECS
    if command.startswith("ota.check"):
        return OTA_CHECK_COMMAND_TIMEOUT_SECS
    return COMMAND_TIMEOUT_SECS


def is_ota_update_command(command: str) -> bool:
    return (command or "").strip().lower().startswith("ota.update")


def parse_bridge_packet_envelope(payload: bytes) -> dict | None:
    if len(payload) < BRIDGE_V2_OVERHEAD:
        return None
    if not payload.startswith(CONTROL_PREFIX):
        return None
    if payload[4] != CONTROL_TYPE_BRIDGE_PACKET:
        return None
    if payload[5] not in SUPPORTED_BRIDGE_PACKET_VERSIONS:
        return None

    packet_len = struct.unpack(">H", payload[12:14])[0]
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
    if not payload.startswith(CONTROL_PREFIX) or len(payload) < 5 or payload[4] != CONTROL_TYPE_BRIDGE_PACKET:
        return ""
    if len(payload) < BRIDGE_V2_OVERHEAD:
        return "short bridge packet envelope"
    if payload[5] not in SUPPORTED_BRIDGE_PACKET_VERSIONS:
        return f"unsupported bridge packet version {payload[5]}"
    packet_len = struct.unpack(">H", payload[12:14])[0]
    if packet_len == 0:
        return "zero mesh payload length"
    if len(payload) < BRIDGE_V2_OVERHEAD + packet_len:
        return f"truncated mesh payload {len(payload) - BRIDGE_V2_OVERHEAD}/{packet_len}"
    return ""


def mesh_payload_for_parsing(payload: bytes) -> bytes:
    envelope = parse_bridge_packet_envelope(payload)
    if envelope is not None:
        return envelope["mesh_payload"]
    return payload


def fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def packet_identity_for_dedupe(payload: bytes) -> bytes:
    mesh = mesh_payload_for_parsing(payload)
    parsed = parse_mesh_payload(mesh)
    if not parsed:
        return mesh

    identity = bytes([parsed["payload_type"]])
    if parsed["payload_type"] == PAYLOAD_TYPE_TRACE:
        identity += bytes([parsed["path_len"]])
    return identity + parsed["app_payload"]


def packet_fingerprint(payload: bytes) -> int:
    return fnv1a64(packet_identity_for_dedupe(payload))


def fingerprint_hex(fingerprint: int) -> str:
    return f"{fingerprint:016x}"


def prune_packet_fingerprint_cache(now: float | None = None) -> None:
    now = now or time.time()
    if not BRIDGE_DEDUPE_ENABLED:
        packet_fingerprint_cache.clear()
        return
    cutoff = now - max(1, BRIDGE_DEDUPE_TTL_SECS)
    for fingerprint, record in list(packet_fingerprint_cache.items()):
        if record.get("last_seen", 0.0) >= cutoff and len(packet_fingerprint_cache) <= BRIDGE_DEDUPE_MAX_ENTRIES:
            break
        packet_fingerprint_cache.pop(fingerprint, None)
    while len(packet_fingerprint_cache) > max(1, BRIDGE_DEDUPE_MAX_ENTRIES):
        packet_fingerprint_cache.popitem(last=False)


def packet_fingerprint_record(fingerprint: int, now: float | None = None) -> dict:
    now = now or time.time()
    prune_packet_fingerprint_cache(now)
    record = packet_fingerprint_cache.get(fingerprint)
    if record is None:
        record = {
            "fingerprint": fingerprint,
            "first_seen": now,
            "last_seen": now,
            "seen_from": {},
            "sent_to": {},
        }
        packet_fingerprint_cache[fingerprint] = record
    else:
        record["last_seen"] = now
        packet_fingerprint_cache.move_to_end(fingerprint)
    return record


def source_short_id(payload: bytes) -> int | None:
    mesh = mesh_payload_for_parsing(payload)
    location = parse_mesh_location_payload(mesh)
    if location and location.get("node_id"):
        try:
            return int(str(location["node_id"])[:2], 16)
        except ValueError:
            return None

    advert = parse_node_advert_payload(mesh)
    if advert and advert.get("node_id"):
        try:
            return int(str(advert["node_id"])[:2], 16)
        except ValueError:
            return None

    parsed = parse_mesh_payload(mesh)
    if parsed and parsed.get("app_payload"):
        return parsed["app_payload"][0]
    return None


def short_id_label(short_id: int | None) -> str:
    return f"0x{short_id:02x}" if short_id is not None else "unknown"


def prune_short_id_quarantine(now: float | None = None) -> None:
    now = now or time.time()
    for short_id, record in list(short_id_quarantine.items()):
        if record.get("until", 0.0) > now:
            continue
        short_id_quarantine.pop(short_id, None)
        inc_bridge_guard_counter("short_id_quarantine_end")
        log.warning("short_id_quarantine_end id=%s", short_id_label(short_id))


def short_id_quarantine_active(short_id: int | None, now: float | None = None) -> bool:
    if not SHORT_ID_QUARANTINE_ENABLED or short_id is None:
        return False
    prune_short_id_quarantine(now)
    record = short_id_quarantine.get(short_id)
    return bool(record and record.get("until", 0.0) > (now or time.time()))


def record_short_id_bad_hit(short_id: int | None, reason: str, client: "BridgeClient", now: float | None = None) -> None:
    if not SHORT_ID_QUARANTINE_ENABLED or short_id is None:
        return
    now = now or time.time()
    window = max(1, SHORT_ID_QUARANTINE_WINDOW_SECS)
    hits = short_id_bad_hits.setdefault(short_id, deque())
    while hits and hits[0] < now - window:
        hits.popleft()
    hits.append(now)
    inc_bridge_guard_counter("short_id_bad_hit")
    if len(hits) < max(1, SHORT_ID_QUARANTINE_THRESHOLD):
        return

    until = now + max(1, SHORT_ID_QUARANTINE_SECS)
    current = short_id_quarantine.get(short_id, {})
    if current.get("until", 0.0) < until:
        short_id_quarantine[short_id] = {
            "id": short_id,
            "until": until,
            "reason": reason,
            "source": client.display_name,
            "addr": client.addr,
            "hits": len(hits),
            "started_at": now,
        }
        inc_bridge_guard_counter("short_id_quarantine_start")
        log.warning(
            "%s: short_id_quarantine_start id=%s reason=%s hits=%d ttl=%ds",
            client.addr,
            short_id_label(short_id),
            reason,
            len(hits),
            max(1, SHORT_ID_QUARANTINE_SECS),
        )
    hits.clear()


def short_id_quarantine_snapshot(now: float | None = None) -> list[dict]:
    now = now or time.time()
    prune_short_id_quarantine(now)
    items = []
    for short_id, record in sorted(short_id_quarantine.items()):
        items.append({
            "id": short_id_label(short_id),
            "raw": short_id,
            "reason": record.get("reason", ""),
            "source": record.get("source", ""),
            "seconds_left": max(0, int(record.get("until", 0.0) - now)),
            "hits": int(record.get("hits") or 0),
        })
    return items


def decrement_bridge_ttl(payload: bytes) -> bytes | None:
    envelope = parse_bridge_packet_envelope(payload)
    if envelope is None:
        return payload
    ttl = envelope["ttl"]
    if ttl <= 1:
        return None
    forwarded = bytearray(payload)
    forwarded[6] = ttl - 1
    return bytes(forwarded)


def parse_location_report(payload: bytes) -> dict | None:
    if len(payload) < 32:
        return None
    if payload[:4] != b"MCL1" or payload[4] != 1:
        return None

    name_len = payload[31]
    if name_len > 24 or len(payload) < 32 + name_len:
        return None

    lat_microdeg = struct.unpack(">i", payload[10:14])[0]
    lon_microdeg = struct.unpack(">i", payload[14:18])[0]
    altitude_m = struct.unpack(">h", payload[18:20])[0]
    speed_cms = struct.unpack(">H", payload[20:22])[0]
    heading_cdeg = struct.unpack(">H", payload[22:24])[0]
    battery_mv = struct.unpack(">H", payload[25:27])[0]
    timestamp = struct.unpack(">I", payload[27:31])[0]
    name = payload[32:32 + name_len].decode("utf-8", errors="replace").strip()

    return {
        "version": payload[4],
        "flags": payload[5],
        "node_id": binascii.hexlify(payload[6:10]).decode("ascii"),
        "lat": lat_microdeg / 1000000.0,
        "lon": lon_microdeg / 1000000.0,
        "altitude_m": altitude_m,
        "speed_cms": speed_cms,
        "speed_kmh": round(speed_cms * 0.036, 2),
        "heading_cdeg": heading_cdeg,
        "heading_deg": round(heading_cdeg / 100.0, 2),
        "satellites": payload[24],
        "battery_mv": battery_mv,
        "timestamp": timestamp,
        "name": name,
    }


def parse_mesh_payload(frame_payload: bytes) -> dict | None:
    if len(frame_payload) < 2:
        return None

    header = frame_payload[0]
    payload_type = (header >> PH_TYPE_SHIFT) & 0x0F
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


def is_transport_or_message_packet(parsed: dict | None) -> bool:
    if not parsed:
        return False
    if parsed["route_type"] in (ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_TRANSPORT_DIRECT):
        return True
    return parsed["payload_type"] in (
        PAYLOAD_TYPE_REQ,
        PAYLOAD_TYPE_RESPONSE,
        PAYLOAD_TYPE_TXT_MSG,
        PAYLOAD_TYPE_GRP_TXT,
        PAYLOAD_TYPE_GRP_DATA,
        PAYLOAD_TYPE_ANON_REQ,
        PAYLOAD_TYPE_MULTIPART,
    )


def prune_rate_window(times: deque[float], now: float, window_secs: int) -> None:
    cutoff = now - max(1, window_secs)
    while times and times[0] < cutoff:
        times.popleft()


def allow_transport_packet(client: "BridgeClient", parsed: dict | None, now: float) -> bool:
    global transport_rate_dropped

    if not TRANSPORT_RATE_LIMIT_ENABLE or not is_transport_or_message_packet(parsed):
        return True

    prune_rate_window(client.transport_rx_times, now, TRANSPORT_RATE_LIMIT_WINDOW_SECS)
    prune_rate_window(transport_rx_times, now, TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS)

    if TRANSPORT_RATE_LIMIT_MAX > 0 and len(client.transport_rx_times) >= TRANSPORT_RATE_LIMIT_MAX:
        client.transport_rate_dropped += 1
        transport_rate_dropped += 1
        return False
    if TRANSPORT_GLOBAL_RATE_LIMIT_MAX > 0 and len(transport_rx_times) >= TRANSPORT_GLOBAL_RATE_LIMIT_MAX:
        client.transport_rate_dropped += 1
        transport_rate_dropped += 1
        return False

    client.transport_rx_times.append(now)
    transport_rx_times.append(now)
    return True


def parse_mesh_location_payload(frame_payload: bytes) -> dict | None:
    parsed = parse_mesh_payload(frame_payload)
    if not parsed:
        return None

    report = None
    if parsed["payload_type"] == PAYLOAD_TYPE_LOCATION:
        report = parse_location_report(parsed["app_payload"])
    elif parsed["payload_type"] == PAYLOAD_TYPE_GRP_DATA:
        decoded = decrypt_public_channel_plain(parsed)
        if decoded is not None:
            _channel_name, plain = decoded
            group_data = decode_group_data_plain(plain)
            if group_data is not None:
                data_type, data = group_data
                if data_type == DATA_TYPE_MESHCORENG_TRACKER:
                    report = parse_location_report(data)
    if report is None:
        return None
    report["hops"] = parsed["path_hash_count"]
    report["payload_type"] = parsed["payload_type"]
    return report


def parse_advert_app_data(app_data: bytes) -> dict | None:
    if not app_data:
        return None

    flags = app_data[0]
    pos = 1
    advert = {
        "advert_type": flags & 0x0F,
        "name": "",
        "lat": None,
        "lon": None,
        "feat1": None,
        "feat2": None,
    }

    if flags & ADV_LATLON_MASK:
        if len(app_data) < pos + 8:
            return None
        advert["lat"] = struct.unpack("<i", app_data[pos:pos + 4])[0] / 1000000.0
        pos += 4
        advert["lon"] = struct.unpack("<i", app_data[pos:pos + 4])[0] / 1000000.0
        pos += 4
    if flags & ADV_FEAT1_MASK:
        if len(app_data) < pos + 2:
            return None
        advert["feat1"] = struct.unpack("<H", app_data[pos:pos + 2])[0]
        pos += 2
    if flags & ADV_FEAT2_MASK:
        if len(app_data) < pos + 2:
            return None
        advert["feat2"] = struct.unpack("<H", app_data[pos:pos + 2])[0]
        pos += 2
    if flags & ADV_NAME_MASK:
        name_len = min(len(app_data) - pos, MAX_ADVERT_DATA_SIZE - pos)
        advert["name"] = app_data[pos:pos + name_len].decode("utf-8", errors="replace").strip()

    return advert


def parse_sensor_advert_payload(frame_payload: bytes) -> dict | None:
    parsed = parse_mesh_payload(frame_payload)
    if not parsed or parsed["payload_type"] != PAYLOAD_TYPE_ADVERT:
        return None

    payload = parsed["app_payload"]
    advert_header_len = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE
    if len(payload) < advert_header_len:
        return None

    pub_key = payload[:PUB_KEY_SIZE]
    timestamp = struct.unpack("<I", payload[PUB_KEY_SIZE:PUB_KEY_SIZE + 4])[0]
    app_data = payload[advert_header_len:advert_header_len + MAX_ADVERT_DATA_SIZE]
    advert = parse_advert_app_data(app_data)
    if not advert or advert["advert_type"] != ADV_TYPE_SENSOR:
        return None

    return {
        "node_id": binascii.hexlify(pub_key).decode("ascii"),
        "pubkey_prefix": binascii.hexlify(pub_key[:8]).decode("ascii"),
        "timestamp": timestamp,
        "name": advert["name"],
        "lat": advert["lat"],
        "lon": advert["lon"],
        "feat1": advert["feat1"],
        "feat2": advert["feat2"],
        "hops": parsed["path_hash_count"],
        "route_type": parsed["route_type"],
    }


def parse_node_advert_payload(frame_payload: bytes) -> dict | None:
    parsed = parse_mesh_payload(frame_payload)
    if not parsed or parsed["payload_type"] != PAYLOAD_TYPE_ADVERT:
        return None

    payload = parsed["app_payload"]
    advert_header_len = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE
    if len(payload) < advert_header_len:
        return None

    pub_key = payload[:PUB_KEY_SIZE]
    timestamp = struct.unpack("<I", payload[PUB_KEY_SIZE:PUB_KEY_SIZE + 4])[0]
    app_data = payload[advert_header_len:advert_header_len + MAX_ADVERT_DATA_SIZE]
    advert = parse_advert_app_data(app_data)
    if not advert:
        return None

    return {
        "node_id": binascii.hexlify(pub_key).decode("ascii"),
        "pubkey_prefix": binascii.hexlify(pub_key[:8]).decode("ascii"),
        "timestamp": timestamp,
        "name": advert["name"],
        "advert_type": advert["advert_type"],
        "hops": parsed["path_hash_count"],
    }


def location_track_path(node_id: str) -> Path:
    safe = re.sub(r"[^0-9a-fA-F_-]", "_", node_id or "unknown")
    return LOCATION_TRACKS_DIR / f"{safe}.jsonl"


def location_track_point(report: dict) -> dict:
    return {
        "node_id": report.get("node_id"),
        "name": report.get("name", ""),
        "lat": report["lat"],
        "lon": report["lon"],
        "altitude_m": report.get("altitude_m"),
        "speed_kmh": report.get("speed_kmh"),
        "heading_deg": report.get("heading_deg"),
        "satellites": report.get("satellites"),
        "battery_mv": report.get("battery_mv"),
        "timestamp": report.get("timestamp"),
        "received_at": report.get("received_at"),
    }


def location_point_time(point: dict) -> int:
    value = point.get("timestamp") or point.get("received_at") or int(time.time())
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(time.time())


def location_distance_m(a: dict, b: dict) -> float:
    lat1 = math.radians(float(a["lat"]))
    lat2 = math.radians(float(b["lat"]))
    dlat = lat2 - lat1
    dlon = math.radians(float(b["lon"]) - float(a["lon"]))
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.atan2(math.sqrt(hav), math.sqrt(max(0.0, 1.0 - hav)))


def seed_stationary_state(node_id: str, track: list[dict]) -> None:
    if not track:
        return
    anchor = track[-1]
    latest_location_stationary[node_id] = {
        "anchor": anchor,
        "since": location_point_time(anchor),
        "closed": bool(anchor.get("route_break_after")),
    }


def mark_route_breaks(track: list[dict]) -> None:
    anchor = None
    stationary_since = 0
    closed = False
    for point in track:
        if anchor is None:
            anchor = point
            stationary_since = location_point_time(point)
            closed = bool(point.get("route_break_after"))
            continue

        if location_distance_m(anchor, point) <= LOCATION_ROUTE_STATIONARY_METERS:
            point_time = location_point_time(point)
            if not closed and point_time - stationary_since >= LOCATION_ROUTE_STATIONARY_SECS:
                point["route_break_after"] = True
                closed = True
            continue

        anchor = point
        stationary_since = location_point_time(point)
        closed = False


def update_stationary_route_state(node_id: str, track: list[dict], point: dict) -> None:
    state = latest_location_stationary.get(node_id)
    if state is None:
        state = {"anchor": point, "since": location_point_time(point), "closed": False}
        latest_location_stationary[node_id] = state
        return

    anchor = state["anchor"]
    point_time = location_point_time(point)
    if location_distance_m(anchor, point) <= LOCATION_ROUTE_STATIONARY_METERS:
        if not state.get("closed") and point_time - int(state.get("since", point_time)) >= LOCATION_ROUTE_STATIONARY_SECS:
            point["route_break_after"] = True
            state["closed"] = True
            log.info(
                "Closed tracker route for %s after %d minutes within %dm",
                node_id,
                LOCATION_ROUTE_STATIONARY_SECS // 60,
                LOCATION_ROUTE_STATIONARY_METERS,
            )
        return

    state["anchor"] = point
    state["since"] = point_time
    state["closed"] = False


def append_location_track_point(node_id: str, point: dict) -> None:
    try:
        LOCATION_TRACKS_DIR.mkdir(parents=True, exist_ok=True)
        with location_track_path(node_id).open("a", encoding="utf-8") as file:
            file.write(json.dumps(point, separators=(",", ":")) + "\n")
    except OSError as exc:
        log.warning("Could not persist location track for %s: %s", node_id, exc)


def load_location_tracks() -> None:
    latest_location_tracks.clear()
    latest_locations.clear()
    latest_location_stationary.clear()
    if not LOCATION_TRACKS_DIR.exists():
        return
    loaded_points = 0
    for path in sorted(LOCATION_TRACKS_DIR.glob("*.jsonl")):
        node_id = path.stem
        track: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as file:
                for lineno, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        point = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("Skipping invalid location track line %s:%d", path, lineno)
                        continue
                    if not isinstance(point, dict):
                        continue
                    lat = point.get("lat")
                    lon = point.get("lon")
                    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                        continue
                    point.setdefault("node_id", node_id)
                    track.append(point)
        except OSError as exc:
            log.warning("Could not load location track %s: %s", path, exc)
            continue
        if not track:
            continue
        mark_route_breaks(track)
        latest = dict(track[-1])
        actual_node_id = latest.get("node_id") or node_id
        latest["node_id"] = actual_node_id
        latest.setdefault("name", "")
        latest.setdefault("received_at", int(latest.get("timestamp") or time.time()))
        latest["age_seconds"] = 0
        latest["source"] = "track-file"
        latest_location_tracks[actual_node_id] = track
        latest_locations[actual_node_id] = latest
        seed_stationary_state(actual_node_id, track)
        loaded_points += len(track)
    if loaded_points:
        log.info("Loaded %d persisted location track point(s) for %d tracker node(s)",
                 loaded_points, len(latest_location_tracks))


def record_location(report: dict, client: "BridgeClient") -> None:
    now = time.time()
    report = dict(report)
    report["received_at"] = int(now)
    report["age_seconds"] = 0
    report["source"] = client.display_name
    client.learn_node_id(report.get("node_id", ""), report.get("name", ""))
    latest_locations[report["node_id"]] = report
    track = latest_location_tracks.setdefault(report["node_id"], [])
    point = location_track_point(report)
    if not track or any(track[-1].get(key) != point.get(key) for key in ("lat", "lon", "timestamp")):
        update_stationary_route_state(report["node_id"], track, point)
        track.append(point)
        append_location_track_point(report["node_id"], point)


def record_sensor_advert(report: dict, client: "BridgeClient") -> None:
    now = time.time()
    report = dict(report)
    existing = latest_sensors.get(report["node_id"], {})
    report["received_at"] = int(now)
    report["age_seconds"] = 0
    report["source"] = client.display_name
    report["seen_count"] = int(existing.get("seen_count", 0)) + 1
    client.learn_node_id(report.get("node_id", ""), report.get("name", ""))
    latest_sensors[report["node_id"]] = report


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def parse_block_duration_seconds(value: str) -> int:
    value = (value or "").strip().lower()
    match = re.fullmatch(r"(\d+)([smhd]?)", value)
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return amount * 86400
    if unit == "h":
        return amount * 3600
    if unit == "m":
        return amount * 60
    return amount


def parse_block_stats_reply(kind: str, reply: str) -> list[dict]:
    """Parse firmware block list replies from MyMesh or the TCPBridge mirror."""
    text = (reply or "").strip()
    if not text or text.lower().startswith("error"):
        return []
    if text.startswith(">"):
        text = text[1:].strip()
    for prefix in (f"{kind}.block", kind):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    if not text or text.lower() in ("empty", "none", "ok"):
        return []

    entries: list[dict] = []
    for raw_entry in text.split(";"):
        entry = raw_entry.strip()
        if not entry:
            continue
        value = ""
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
        elif len(parts) == 1 and ":" in parts[0]:
            value, ttl = parts[0].split(":", 1)
            value = value.strip().lower()
            seconds_left = parse_block_duration_seconds(ttl)
        elif len(parts) >= 2:
            value = parts[0].strip().lower()
            seconds_left = parse_block_duration_seconds(parts[1])
        if not value:
            continue
        entries.append({
            "kind": kind,
            "value": value,
            "seconds_left": seconds_left,
            "drops": drops,
        })
    return entries


def new_block_drop_counter_state(now: float | None = None) -> dict:
    now = now or time.time()
    return {
        "window_started_at": now,
        "last": {"node": {}, "path": {}},
        "totals": {"node": 0, "path": 0},
    }


def update_block_drop_counters(state: dict, block_stats: dict, now: float | None = None) -> dict:
    now = now or time.time()
    if not state:
        state = new_block_drop_counter_state(now)
    if now - float(state.get("window_started_at") or now) >= BLOCK_STATS_COUNTER_WINDOW_SECS:
        state = new_block_drop_counter_state(now)

    last_by_kind = state.setdefault("last", {"node": {}, "path": {}})
    totals = state.setdefault("totals", {"node": 0, "path": 0})
    for kind in ("node", "path"):
        previous = last_by_kind.setdefault(kind, {})
        for entry in block_stats.get(kind) or []:
            value = str(entry.get("value") or "").strip().lower()
            if not value:
                continue
            drops = max(0, int(entry.get("drops") or 0))
            old_drops = previous.get(value)
            if old_drops is None:
                increment = drops
            elif drops >= old_drops:
                increment = drops - old_drops
            else:
                # Firmware-side counters can reset on reconnect/restart; keep the website 24h counter monotonic.
                increment = drops
            if increment > 0:
                totals[kind] = int(totals.get(kind) or 0) + increment
            previous[value] = drops

    block_stats["node_drops_24h"] = int(totals.get("node") or 0)
    block_stats["path_drops_24h"] = int(totals.get("path") or 0)
    block_stats["drop_window_started_at"] = state["window_started_at"]
    block_stats["drop_window_resets_in_seconds"] = max(
        0,
        int(float(state["window_started_at"]) + BLOCK_STATS_COUNTER_WINDOW_SECS - now),
    )
    return state


def block_stats_totals(block_stats: dict) -> dict:
    node_entries = block_stats.get("node") or []
    path_entries = block_stats.get("path") or []
    node_drops = int(
        block_stats["node_drops_24h"]
        if "node_drops_24h" in block_stats
        else sum(int(entry.get("drops") or 0) for entry in node_entries)
    )
    path_drops = int(
        block_stats["path_drops_24h"]
        if "path_drops_24h" in block_stats
        else sum(int(entry.get("drops") or 0) for entry in path_entries)
    )
    return {
        "node_active": len(node_entries),
        "path_active": len(path_entries),
        "node_drops": node_drops,
        "path_drops": path_drops,
        "total_drops": node_drops + path_drops,
        "resets_in_seconds": int(block_stats.get("drop_window_resets_in_seconds") or 0),
    }


def format_sockaddrs(sockets) -> str:
    return ", ".join(
        f"{sock.getsockname()[0]}:{sock.getsockname()[1]}"
        for sock in sockets or []
    )


def prune_packet_times(packet_times: deque[float], now: float) -> None:
    cutoff = now - PACKET_COUNTER_WINDOW_SECS
    while packet_times and packet_times[0] < cutoff:
        packet_times.popleft()


def make_node_stats_key(client: "BridgeClient") -> str:
    if client.node_id:
        return f"node:{client.node_id.strip().lower()}"
    if client.node_name:
        return f"name:{client.node_name.strip().lower()}"
    return f"host:{client.host}"


def new_node_stats(key: str) -> dict:
    now = time.time()
    return {
        "key": key,
        "rx_times": deque(),
        "tx_times": deque(),
        "packets_rx": 0,
        "packets_tx": 0,
        "heartbeats_rx": 0,
        "first_seen": now,
        "last_seen": now,
        "last_connected": now,
        "last_disconnect": 0.0,
        "last_heartbeat": 0.0,
        "last_heartbeat_uptime_ms": 0,
        "rf_duty": {},
        "radio_stats": {},
        "neighbor_count": None,
        "flood_hop_limit_drops": 0,
        "rf_tx_total_baseline_ms": None,
        "rf_tx_total_baseline_at": 0.0,
        "rf_tx_hour_baseline_ms": None,
        "rf_tx_hour_started_at": 0.0,
        "node_name": "",
        "node_id": "",
        "firmware_version": "",
        "supports_bridge_v2": False,
        "bridge_proto_ver": 1,
        "bridge_group": BRIDGE_GROUP,
        "node_rf_inject_budget": {},
        "block_stats": {"path": [], "node": [], "updated_at": 0.0, "error": ""},
        "connected": False,
        "client_id": "",
        "addr": "",
    }


def merge_node_stats(target: dict, source: dict) -> None:
    target["rx_times"].extend(source["rx_times"])
    target["tx_times"].extend(source["tx_times"])
    target["rx_times"] = deque(sorted(target["rx_times"]))
    target["tx_times"] = deque(sorted(target["tx_times"]))
    target["packets_rx"] += source.get("packets_rx", 0)
    target["packets_tx"] += source.get("packets_tx", 0)
    target["heartbeats_rx"] += source.get("heartbeats_rx", 0)
    target["first_seen"] = min(target.get("first_seen", time.time()), source.get("first_seen", time.time()))
    for field in ("last_seen", "last_connected", "last_disconnect", "last_heartbeat"):
        target[field] = max(target.get(field, 0.0), source.get(field, 0.0))
    for field in ("rf_tx_total_baseline_ms", "rf_tx_total_baseline_at", "rf_tx_hour_baseline_ms", "rf_tx_hour_started_at"):
        if target.get(field) is None or not target.get(field):
            target[field] = source.get(field)
    for field in ("last_heartbeat_uptime_ms",):
        if source.get(field):
            target[field] = source[field]
    for field in ("rf_duty", "radio_stats", "node_name", "node_id", "firmware_version", "client_id", "addr", "bridge_group", "node_rf_inject_budget", "block_stats"):
        if source.get(field):
            target[field] = source[field]
    if source.get("neighbor_count") is not None:
        target["neighbor_count"] = source["neighbor_count"]
    target["flood_hop_limit_drops"] = max(
        int(target.get("flood_hop_limit_drops") or 0),
        int(source.get("flood_hop_limit_drops") or 0),
    )
    target["supports_bridge_v2"] = target.get("supports_bridge_v2", False) or source.get("supports_bridge_v2", False)
    target["bridge_proto_ver"] = max(target.get("bridge_proto_ver", 1), source.get("bridge_proto_ver", 1))
    target["connected"] = target.get("connected", False) or source.get("connected", False)


def get_node_stats(client: "BridgeClient") -> dict:
    key = make_node_stats_key(client)
    old_key = getattr(client, "_stats_key", "")
    if old_key and old_key != key and old_key in node_traffic_stats:
        old_stats = node_traffic_stats.pop(old_key)
        stats = node_traffic_stats.setdefault(key, new_node_stats(key))
        merge_node_stats(stats, old_stats)
    else:
        stats = node_traffic_stats.setdefault(key, new_node_stats(key))
    client._stats_key = key
    stats.update({
        "key": key,
        "node_name": client.node_name,
        "node_id": client.node_id,
        "firmware_version": client.firmware_version,
        "supports_bridge_v2": client.supports_bridge_v2,
        "bridge_proto_ver": client.bridge_proto_ver,
        "bridge_group": client.bridge_group,
        "node_rf_inject_budget": dict(client.node_rf_inject_budget),
        "block_stats": dict(client.block_stats),
        "connected": client in connected_clients,
        "client_id": client.client_id,
        "addr": client.addr,
        "last_connected": client._connect_time,
    })
    return stats


def touch_node_stats(client: "BridgeClient", now: float | None = None) -> dict:
    now = now or time.time()
    stats = get_node_stats(client)
    stats["last_seen"] = now
    return stats


def record_node_packet(client: "BridgeClient", direction: str, now: float | None = None) -> None:
    now = now or time.time()
    stats = touch_node_stats(client, now)
    if direction == "RX":
        stats["packets_rx"] += 1
        stats["rx_times"].append(now)
        prune_packet_times(stats["rx_times"], now)
    elif direction == "TX":
        stats["packets_tx"] += 1
        stats["tx_times"].append(now)
        prune_packet_times(stats["tx_times"], now)


def record_node_heartbeat(client: "BridgeClient", heartbeat: dict, now: float | None = None) -> None:
    now = now or time.time()
    stats = touch_node_stats(client, now)
    stats["heartbeats_rx"] += 1
    stats["last_heartbeat"] = now
    stats["last_heartbeat_uptime_ms"] = int(heartbeat.get("uptime_ms") or 0)
    if "rf_duty" in heartbeat:
        rf = dict(heartbeat["rf_duty"])
        max_ms = int(rf.get("tx_max_ms") or 0)
        window_ms = int(rf.get("window_ms") or 0)
        firmware_used_ms = int(rf.get("tx_used_ms") or 0)
        rf["tx_left_ms"] = max(0, max_ms - firmware_used_ms) if max_ms > 0 else 0
        rf["measured_from_server_start"] = False
        total_ms = rf.get("tx_total_ms")
        if isinstance(total_ms, int):
            baseline = stats.get("rf_tx_total_baseline_ms")
            if baseline is None or total_ms < baseline:
                baseline = total_ms
                stats["rf_tx_total_baseline_ms"] = baseline
                stats["rf_tx_total_baseline_at"] = now
            since_server_ms = max(0, total_ms - baseline)
            rf["tx_since_server_ms"] = since_server_ms
            rf["tx_since_server_pct"] = min(100.0, (since_server_ms * 100.0) / max_ms) if max_ms > 0 else 0.0

            hour_started_at = int(now // 3600) * 3600
            hour_baseline = stats.get("rf_tx_hour_baseline_ms")
            if stats.get("rf_tx_hour_started_at") != hour_started_at or hour_baseline is None or total_ms < hour_baseline:
                hour_baseline = total_ms
                stats["rf_tx_hour_baseline_ms"] = hour_baseline
                stats["rf_tx_hour_started_at"] = hour_started_at
            hour_used_ms = max(0, total_ms - hour_baseline)
            hour_used_ms = min(hour_used_ms, max_ms) if max_ms > 0 else hour_used_ms
            rf["tx_hour_used_ms"] = hour_used_ms
            rf["tx_hour_left_ms"] = max(0, max_ms - hour_used_ms) if max_ms > 0 else 0
            rf["tx_hour_used_pct"] = min(100.0, (hour_used_ms * 100.0) / max_ms) if max_ms > 0 else 0.0
            rf["tx_hour_started_at"] = hour_started_at
            rf["tx_hour_resets_in_seconds"] = max(0, int(hour_started_at + 3600 - now))
        stats["rf_duty"] = rf
    if "radio_stats" in heartbeat:
        stats["radio_stats"] = dict(heartbeat["radio_stats"])
    if "neighbor_count" in heartbeat:
        stats["neighbor_count"] = int(heartbeat["neighbor_count"])
    if "flood_hop_limit_drops" in heartbeat:
        stats["flood_hop_limit_drops"] = int(heartbeat["flood_hop_limit_drops"])


def mark_node_disconnected(client: "BridgeClient", now: float | None = None) -> None:
    now = now or time.time()
    stats = get_node_stats(client)
    stats["connected"] = False
    stats["last_disconnect"] = now
    stats["last_seen"] = max(stats.get("last_seen", 0.0), client.last_seen)


def has_node_identity(stats: dict) -> bool:
    return bool((stats.get("node_name") or "").strip() or (stats.get("node_id") or "").strip())


def node_stats_status_dict(stats: dict, now: float) -> dict:
    prune_packet_times(stats["rx_times"], now)
    prune_packet_times(stats["tx_times"], now)
    display_name = stats.get("node_name") or "unnamed bridge node"
    connected = bool(stats.get("connected"))
    heartbeat_age = int(now - stats["last_heartbeat"]) if stats.get("last_heartbeat") else None
    return {
        "name": stats.get("node_name", ""),
        "id": stats.get("client_id") or stats["key"],
        "node_id": stats.get("node_id", ""),
        "firmware_version": stats.get("firmware_version", ""),
        "firmware_update": firmware_update_status(stats.get("firmware_version", "")),
        "display_name": display_name,
        "connected": connected,
        "connected_seconds": int(now - stats["last_connected"]) if connected else 0,
        "connected_for": format_duration(now - stats["last_connected"]) if connected else "offline",
        "idle_seconds": int(now - stats["last_seen"]) if stats.get("last_seen") else None,
        "heartbeat_age_seconds": heartbeat_age,
        "heartbeat_uptime_ms": stats.get("last_heartbeat_uptime_ms", 0),
        "rf_duty": dict(stats.get("rf_duty") or {}),
        "radio_stats": dict(stats.get("radio_stats") or {}),
        "neighbor_count": stats.get("neighbor_count"),
        "flood_hop_limit_drops": stats.get("flood_hop_limit_drops", 0),
        "packets_rx": stats.get("packets_rx", 0),
        "packets_tx": stats.get("packets_tx", 0),
        "packets_rx_24h": len(stats["rx_times"]),
        "packets_tx_24h": len(stats["tx_times"]),
        "transport_rx_window": 0,
        "transport_rate_dropped": 0,
        "tx_queue_depth": 0,
        "tx_queue_max": CLIENT_TX_QUEUE_MAX,
        "tx_queue_high_water": 0,
        "tx_queued": 0,
        "tx_queue_dropped": 0,
        "tx_send_errors": 0,
        "tx_skipped_duplicates": 0,
        "skipped_dup_total": 0,
        "skipped_dup_by_reason": {},
        "loop_score": 0,
        "quarantine_active": False,
        "quarantine_seconds_left": 0,
        "group": stats.get("bridge_group") or BRIDGE_GROUP,
        "bridge_proto_ver": stats.get("bridge_proto_ver", 1),
        "node_rf_inject_budget": dict(stats.get("node_rf_inject_budget") or {}),
        "block_stats": dict(stats.get("block_stats") or {"path": [], "node": [], "updated_at": 0.0, "error": ""}),
        "block_stats_totals": block_stats_totals(stats.get("block_stats") or {}),
        "last_fingerprint": "",
        "last_fingerprint_age_seconds": None,
        "rf_inject_budget_remaining_ms": None,
        "rf_budget_drops": 0,
        "bridge_quality_score": 0,
        "last_tx_age_seconds": None,
        "last_tx_queue_drop_age_seconds": None,
        "last_tx_error": "",
        "heartbeats_rx": stats.get("heartbeats_rx", 0),
        "authenticated": connected,
        "supports_bridge_v2": stats.get("supports_bridge_v2", False),
        "bridge_proto_ver": stats.get("bridge_proto_ver", 1),
        "last_seen_seconds": int(now - stats["last_seen"]) if stats.get("last_seen") else None,
    }


def prune_disconnected_node_stats(now: float) -> None:
    for key, stats in list(node_traffic_stats.items()):
        prune_packet_times(stats["rx_times"], now)
        prune_packet_times(stats["tx_times"], now)
        if stats.get("connected"):
            continue
        if not has_node_identity(stats):
            node_traffic_stats.pop(key, None)
            continue
        last_seen = stats.get("last_seen", 0.0)
        if not stats["rx_times"] and not stats["tx_times"] and last_seen < now - PACKET_COUNTER_WINDOW_SECS:
            node_traffic_stats.pop(key, None)


class BridgeClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        global next_client_id

        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info("peername")
        self.host = addr[0] if addr else "unknown"
        self.port = addr[1] if addr else 0
        self.addr = f"{self.host}:{self.port}" if addr else "unknown"
        self._client_id = f"client-{next_client_id}"
        next_client_id += 1
        self.packets_rx = 0
        self.packets_tx = 0
        self.packet_rx_times: deque[float] = deque()
        self.packet_tx_times: deque[float] = deque()
        self.transport_rx_times: deque[float] = deque()
        self.transport_rate_dropped = 0
        self._stats_key = ""
        self.heartbeats_rx = 0
        self._connect_time = time.time()
        self.last_seen = self._connect_time
        self.last_heartbeat = 0.0
        self.last_heartbeat_uptime_ms = 0
        self.rf_duty: dict = {}
        self.radio_stats: dict = {}
        self.neighbor_count: int | None = None
        self.flood_hop_limit_drops = 0
        self.node_name = ""
        self.node_id = ""
        self.firmware_version = ""
        self.supports_bridge_v2 = False
        self.bridge_proto_ver = 1
        self.bridge_id = 0
        self.authenticated = not BRIDGE_PASSWORD
        self.bridge_group = BRIDGE_GROUP
        self.node_rf_inject_budget: dict = {}
        self._seen_hash_seen_at: dict[bytes, float] = {}
        self._seen_hash_deque: deque[tuple[bytes, float]] = deque(maxlen=256)
        self.tx_queue: asyncio.Queue[tuple[bytes, str, bytes | None]] = asyncio.Queue(maxsize=CLIENT_TX_QUEUE_MAX)
        self.tx_queue_task: asyncio.Task | None = None
        self.tx_queued = 0
        self.tx_queue_dropped = 0
        self.tx_queue_high_water = 0
        self.tx_send_errors = 0
        self.tx_skipped_duplicates = 0
        self.skip_reasons: dict[str, int] = {}
        self.loop_score = 0
        self.loop_last_hit = 0.0
        self.quarantined_until = 0.0
        self.rf_inject_minute_times: deque[float] = deque()
        self.rf_inject_airtime_times: deque[tuple[float, int]] = deque()
        self.rf_budget_drops = 0
        self.block_stats: dict = {"path": [], "node": [], "updated_at": 0.0, "error": ""}
        self.block_drop_counter_state: dict = new_block_drop_counter_state(self._connect_time)
        self._block_stats_polling = False
        self._last_block_stats_poll = 0.0
        self.last_fingerprint = 0
        self.last_fingerprint_at = 0.0
        self.last_tx_queue_drop = 0.0
        self.last_tx_send = 0.0
        self.last_tx_error = ""

    @property
    def display_name(self) -> str:
        return self.node_name or "unnamed bridge node"

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def bridge_key(self) -> str:
        if self.node_id:
            return f"node:{self.node_id.strip().lower()}"
        if self.node_name:
            return f"name:{self.node_name.strip().lower()}"
        return self.client_id

    def record_skip(self, reason: str) -> None:
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if reason.startswith("skipped_dup") and reason != "skipped_dup_loopback":
            self.tx_skipped_duplicates += 1

    def quarantine_active(self, now: float | None = None) -> bool:
        now = now or time.time()
        if self.quarantined_until > now:
            return True
        if self.quarantined_until:
            self.quarantined_until = 0.0
            self.record_skip("bridge_quarantine_end")
            log.warning("%s: bridge_quarantine_end", self.addr)
        return False

    def record_loopguard_hit(self, now: float | None = None) -> None:
        now = now or time.time()
        if self.loop_last_hit and now - self.loop_last_hit > max(1, BRIDGE_LOOPGUARD_WINDOW_SECS):
            self.loop_score = 0
        self.loop_last_hit = now
        self.loop_score += 1
        self.record_skip("loopguard_hit")
        log.warning("%s: loopguard_hit score=%d", self.addr, self.loop_score)
        if self.loop_score >= max(1, BRIDGE_LOOPGUARD_THRESHOLD):
            self.quarantined_until = now + max(1, BRIDGE_LOOPGUARD_QUARANTINE_SECS)
            self.record_skip("bridge_quarantine_start")
            log.warning("%s: bridge_quarantine_start %ds", self.addr, BRIDGE_LOOPGUARD_QUARANTINE_SECS)

    def estimated_airtime_ms(self, payload: bytes) -> int:
        # Conservative bounded estimate for budgeting only; firmware reports real duty separately.
        return max(1, len(mesh_payload_for_parsing(payload)) * 10)

    def prune_rf_inject_budget(self, now: float | None = None) -> None:
        now = now or time.time()
        while self.rf_inject_minute_times and self.rf_inject_minute_times[0] < now - 60:
            self.rf_inject_minute_times.popleft()
        while self.rf_inject_airtime_times and self.rf_inject_airtime_times[0][0] < now - 3600:
            self.rf_inject_airtime_times.popleft()

    def rf_inject_airtime_used_ms(self, now: float | None = None) -> int:
        self.prune_rf_inject_budget(now)
        return sum(ms for _, ms in self.rf_inject_airtime_times)

    def rf_inject_budget_remaining_ms(self, now: float | None = None) -> int | None:
        if BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR <= 0:
            return None
        return max(0, BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR - self.rf_inject_airtime_used_ms(now))

    def can_accept_rf_inject(self, payload: bytes, now: float | None = None) -> tuple[bool, str]:
        if not BRIDGE_RF_INJECT_ENABLED:
            return True, ""
        now = now or time.time()
        self.prune_rf_inject_budget(now)
        if BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT > 0:
            used_pct = float((self.rf_duty or {}).get("tx_used_pct") or 0.0)
            if used_pct >= BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT:
                return False, "skipped_rf_inject_budget"
        if BRIDGE_RF_INJECT_MAX_PER_MIN > 0 and len(self.rf_inject_minute_times) >= BRIDGE_RF_INJECT_MAX_PER_MIN:
            return False, "skipped_rf_inject_budget"
        estimate = self.estimated_airtime_ms(payload)
        remaining = self.rf_inject_budget_remaining_ms(now)
        if remaining is not None and estimate > remaining:
            return False, "skipped_rf_inject_budget"
        return True, ""

    def record_rf_inject(self, payload: bytes, now: float | None = None) -> None:
        if not BRIDGE_RF_INJECT_ENABLED:
            return
        now = now or time.time()
        self.prune_rf_inject_budget(now)
        self.rf_inject_minute_times.append(now)
        self.rf_inject_airtime_times.append((now, self.estimated_airtime_ms(payload)))

    def has_seen_payload(self, payload: bytes) -> bool:
        """Return True if this client has already received this mesh payload recently."""
        self.prune_seen_payloads()
        h = hashlib.sha256(packet_identity_for_dedupe(payload)).digest()[:8]
        return h in self._seen_hash_seen_at

    def mark_seen_payload(self, payload: bytes) -> None:
        now = time.time()
        self.prune_seen_payloads(now)
        h = hashlib.sha256(packet_identity_for_dedupe(payload)).digest()[:8]
        if h in self._seen_hash_seen_at:
            self._seen_hash_seen_at[h] = now
            return
        if len(self._seen_hash_deque) >= self._seen_hash_deque.maxlen:
            oldest, _ = self._seen_hash_deque.popleft()
            self._seen_hash_seen_at.pop(oldest, None)
        self._seen_hash_deque.append((h, now))
        self._seen_hash_seen_at[h] = now

    def prune_seen_payloads(self, now: float | None = None) -> None:
        now = now or time.time()
        cutoff = now - max(1, BRIDGE_DEDUPE_TTL_SECS)
        while self._seen_hash_deque and self._seen_hash_deque[0][1] < cutoff:
            oldest, seen_at = self._seen_hash_deque.popleft()
            if self._seen_hash_seen_at.get(oldest) == seen_at:
                self._seen_hash_seen_at.pop(oldest, None)

    def learn_node_id(self, node_id: str, name: str = "") -> None:
        node_id = (node_id or "").strip().lower()
        if not node_id:
            return
        name = (name or "").strip()
        if self.node_name and name and name != self.node_name:
            return
        if not self.node_id or len(node_id) > len(self.node_id) or name == self.node_name:
            self.node_id = node_id

    def status_dict(self, now: float) -> dict:
        stats = get_node_stats(self)
        prune_packet_times(stats["rx_times"], now)
        prune_packet_times(stats["tx_times"], now)
        prune_packet_times(self.packet_rx_times, now)
        prune_packet_times(self.packet_tx_times, now)
        prune_rate_window(self.transport_rx_times, now, TRANSPORT_RATE_LIMIT_WINDOW_SECS)
        status = node_stats_status_dict(stats, now)
        status.update({
            "name": self.node_name,
            "id": self.client_id,
            "node_id": self.node_id,
            "firmware_version": self.firmware_version,
            "firmware_update": firmware_update_status(self.firmware_version),
            "display_name": self.display_name,
            "connected": True,
            "connected_seconds": int(now - self._connect_time),
            "connected_for": format_duration(now - self._connect_time),
            "idle_seconds": int(now - self.last_seen),
            "heartbeat_age_seconds": int(now - self.last_heartbeat) if self.last_heartbeat else None,
            "heartbeat_uptime_ms": self.last_heartbeat_uptime_ms,
            "rf_duty": dict(self.rf_duty),
            "radio_stats": dict(self.radio_stats),
            "neighbor_count": self.neighbor_count if self.neighbor_count is not None else stats.get("neighbor_count"),
            "flood_hop_limit_drops": self.flood_hop_limit_drops or stats.get("flood_hop_limit_drops", 0),
            "packets_rx": self.packets_rx,
            "packets_tx": self.packets_tx,
            "packets_rx_24h": len(stats["rx_times"]),
            "packets_tx_24h": len(stats["tx_times"]),
            "transport_rx_window": len(self.transport_rx_times),
            "transport_rate_dropped": self.transport_rate_dropped,
            "tx_queue_depth": self.tx_queue.qsize(),
            "tx_queue_max": CLIENT_TX_QUEUE_MAX,
            "tx_queue_high_water": self.tx_queue_high_water,
            "tx_queued": self.tx_queued,
            "tx_queue_dropped": self.tx_queue_dropped,
            "tx_send_errors": self.tx_send_errors,
            "tx_skipped_duplicates": self.tx_skipped_duplicates,
            "skipped_dup_total": self.tx_skipped_duplicates,
            "skipped_dup_by_reason": dict(self.skip_reasons),
            "loop_score": self.loop_score,
            "quarantine_active": self.quarantine_active(now),
            "quarantine_seconds_left": max(0, int(self.quarantined_until - now)) if self.quarantined_until else 0,
            "group": self.bridge_group,
            "bridge_proto_ver": self.bridge_proto_ver,
            "bridge_id": f"0x{self.bridge_id:08x}" if self.bridge_id else "",
            "node_rf_inject_budget": dict(self.node_rf_inject_budget),
            "block_stats": dict(self.block_stats),
            "block_stats_totals": block_stats_totals(self.block_stats),
            "last_fingerprint": fingerprint_hex(self.last_fingerprint) if self.last_fingerprint else "",
            "last_fingerprint_age_seconds": int(now - self.last_fingerprint_at) if self.last_fingerprint_at else None,
            "rf_inject_budget_remaining_ms": self.rf_inject_budget_remaining_ms(now),
            "rf_budget_drops": self.rf_budget_drops,
            "bridge_quality_score": max(
                0,
                100
                - min(60, self.loop_score * 10)
                - min(25, self.tx_queue_dropped)
                - min(25, self.tx_send_errors * 5)
                - min(25, self.rf_budget_drops * 2),
            ),
            "last_tx_age_seconds": int(now - self.last_tx_send) if self.last_tx_send else None,
            "last_tx_queue_drop_age_seconds": int(now - self.last_tx_queue_drop) if self.last_tx_queue_drop else None,
            "last_tx_error": self.last_tx_error,
            "heartbeats_rx": self.heartbeats_rx,
            "authenticated": self.authenticated,
            "supports_bridge_v2": self.supports_bridge_v2,
            "bridge_proto_ver": self.bridge_proto_ver,
        })
        return status

    async def read_packet(self) -> bytes | None:
        """Read one framed packet from the stream. Returns raw payload bytes or None on error."""
        # Find magic header
        buf = bytearray()
        while True:
            b = await self.reader.readexactly(1)
            buf.append(b[0])
            if len(buf) >= 2:
                if buf[-2] == (BRIDGE_MAGIC >> 8) & 0xFF and buf[-1] == BRIDGE_MAGIC & 0xFF:
                    break
                # Keep only last byte as potential first magic byte
                buf = bytearray([buf[-1]])

        # Read 2-byte length
        raw_len = await self.reader.readexactly(2)
        length = struct.unpack(">H", raw_len)[0]

        if length == 0 or length > MAX_PAYLOAD:
            log.warning("%s: invalid payload length %d, discarding", self.addr, length)
            return None

        # Read payload + 2-byte checksum
        payload = await self.reader.readexactly(length)
        raw_csum = await self.reader.readexactly(2)
        received_csum = struct.unpack(">H", raw_csum)[0]

        calculated_csum = fletcher16(payload)
        if received_csum != calculated_csum:
            log.warning(
                "%s: checksum mismatch (got 0x%04x, expected 0x%04x)",
                self.addr, received_csum, calculated_csum,
            )
            return None

        self.last_seen = time.time()
        return bytes(payload)

    def build_frame(self, payload: bytes) -> bytes:
        """Wrap a payload in the bridge framing."""
        length = len(payload)
        csum = fletcher16(payload)
        return (
            struct.pack(">H", BRIDGE_MAGIC)
            + struct.pack(">H", length)
            + payload
            + struct.pack(">H", csum)
        )

    def start_writer(self) -> None:
        if self.tx_queue_task is None or self.tx_queue_task.done():
            self.tx_queue_task = asyncio.create_task(self._tx_writer_loop())

    def enqueue_payload(self, payload: bytes, source: str = "", seen_payload: bytes | None = None) -> bool:
        if self.writer.is_closing():
            self.tx_send_errors += 1
            self.last_tx_error = "writer closing"
            return False
        item = (bytes(payload), source, bytes(seen_payload) if seen_payload is not None else None)
        if self.tx_queue.full():
            try:
                self.tx_queue.get_nowait()
                self.tx_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self.tx_queue_dropped += 1
            self.last_tx_queue_drop = time.time()
        try:
            self.tx_queue.put_nowait(item)
        except asyncio.QueueFull:
            self.tx_queue_dropped += 1
            self.last_tx_queue_drop = time.time()
            return False
        self.tx_queued += 1
        self.tx_queue_high_water = max(self.tx_queue_high_water, self.tx_queue.qsize())
        return True

    async def _tx_writer_loop(self) -> None:
        try:
            while True:
                payload, source, seen_payload = await self.tx_queue.get()
                try:
                    ok = await self._send_payload_now(payload, source=source, seen_payload=seen_payload)
                    if not ok:
                        await disconnect(self, reason="send error")
                        return
                finally:
                    self.tx_queue.task_done()
        except asyncio.CancelledError:
            return

    async def send_payload(self, payload: bytes, source: str = "") -> bool:
        return self.enqueue_payload(payload, source=source)

    async def send_control_payload(self, payload: bytes) -> bool:
        if self.writer.is_closing():
            self.tx_send_errors += 1
            self.last_tx_error = "writer closing"
            return False
        try:
            self.writer.write(self.build_frame(payload))
            await self.writer.drain()
            self.last_tx_error = ""
            return True
        except Exception as exc:
            self.tx_send_errors += 1
            self.last_tx_error = str(exc)
            return False

    async def _send_payload_now(self, payload: bytes, source: str = "", seen_payload: bytes | None = None) -> bool:
        try:
            self.writer.write(self.build_frame(payload))
            await self.writer.drain()
            if seen_payload is not None:
                self.mark_seen_payload(seen_payload)
                fingerprint = packet_fingerprint(seen_payload)
                record = packet_fingerprint_record(fingerprint)
                record["sent_to"][self.bridge_key] = time.time()
                self.last_fingerprint = fingerprint
                self.last_fingerprint_at = time.time()
                self.record_rf_inject(seen_payload)
            self.packets_tx += 1
            now = time.time()
            self.last_tx_send = now
            self.last_tx_error = ""
            self.packet_tx_times.append(now)
            prune_packet_times(self.packet_tx_times, now)
            record_node_packet(self, "TX", now)
            packet_log = record_packet_log("TX", self, payload, source=source, target=self.display_name)
            if LOG_PACKETS:
                log.info(
                    "%s -> %s: TX %d bytes %s: %s",
                    packet_log["source"],
                    packet_log["target"],
                    len(payload),
                    format_packet_description(packet_log),
                    packet_log["preview"],
                )
            return True
        except Exception as exc:
            self.tx_send_errors += 1
            self.last_tx_error = str(exc)
            return False

    async def send_command(
        self,
        command: str,
        password: str,
        timeout: int | None = None,
        wait_reply: bool = True,
        count_stats: bool = True,
    ) -> str:
        global next_command_id

        command = command.strip()
        raw_command = command.encode("utf-8")[:96]
        raw_password = password.encode("utf-8")[:32]
        if not raw_command:
            raise ValueError("empty command")

        request_id = next_command_id
        next_command_id = (next_command_id + 1) & 0xFFFFFFFF
        if next_command_id == 0:
            next_command_id = 1

        payload = (
            CONTROL_PREFIX
            + bytes([CONTROL_TYPE_COMMAND])
            + struct.pack(">I", request_id)
            + bytes([len(raw_password)])
            + bytes([len(raw_command)])
            + raw_password
            + raw_command
        )

        if not wait_reply:
            ok = await self.send_payload(payload) if count_stats else await self.send_control_payload(payload)
            if not ok:
                raise RuntimeError("send failed")
            return "OK - command sent"

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending_commands[request_id] = future
        try:
            ok = await self.send_payload(payload) if count_stats else await self.send_control_payload(payload)
            if not ok:
                raise RuntimeError("send failed")
            return await asyncio.wait_for(future, timeout=timeout or COMMAND_TIMEOUT_SECS)
        finally:
            pending_commands.pop(request_id, None)

    def close(self):
        if self.tx_queue_task and not self.tx_queue_task.done() and self.tx_queue_task is not asyncio.current_task():
            self.tx_queue_task.cancel()
        try:
            self.writer.close()
        except Exception:
            pass


async def broadcast(payload: bytes, sender: "BridgeClient"):
    """Forward payload to every connected client except the sender."""
    now = time.time()
    short_id = source_short_id(payload)
    if short_id_quarantine_active(short_id, now):
        sender.record_skip("skipped_short_id_quarantine")
        inc_bridge_guard_counter("skipped_short_id_quarantine")
        log.warning("%s: skipped_short_id_quarantine source_id=%s", sender.addr, short_id_label(short_id))
        return

    forwarded_payload = decrement_bridge_ttl(payload)
    if forwarded_payload is None:
        sender.record_skip("skipped_ttl_expired")
        inc_bridge_guard_counter("skipped_ttl_expired")
        record_short_id_bad_hit(short_id, "ttl_expired", sender, now)
        log.debug("%s: skipped_ttl_expired", sender.addr)
        return

    # Mark the sender as having seen this payload so it is skipped if it reconnects
    # and the same packet arrives again before the deque ages out.
    sender.mark_seen_payload(forwarded_payload)
    fingerprint = packet_fingerprint(forwarded_payload)
    record = packet_fingerprint_record(fingerprint, now)
    record["seen_from"][sender.bridge_key] = now
    sender.last_fingerprint = fingerprint
    sender.last_fingerprint_at = now

    envelope = parse_bridge_packet_envelope(forwarded_payload)
    for client in connected_clients:
        if client is sender:
            client.record_skip("skipped_dup_loopback")
            inc_bridge_guard_counter("skipped_duplicate")
            continue
        if envelope is not None and envelope["origin_id"] and client.bridge_id == envelope["origin_id"]:
            client.record_skip("skipped_own_origin")
            inc_bridge_guard_counter("skipped_own_origin")
            log.debug("%s: skipped_own_origin target=%s origin=0x%08x",
                      sender.addr, client.addr, envelope["origin_id"])
            continue
        if BRIDGE_REQUIRE_GROUP_MATCH and client.bridge_group != sender.bridge_group:
            client.record_skip("skipped_group_mismatch")
            log.debug("%s: skipped_group_mismatch target=%s", sender.addr, client.addr)
            continue
        if client.quarantine_active(now):
            client.record_skip("skipped_quarantine")
            inc_bridge_guard_counter("skipped_bridge_loop")
            record_short_id_bad_hit(short_id, "target_quarantine", sender, now)
            log.warning("%s: skipped_quarantine target=%s", sender.addr, client.addr)
            continue
        if BRIDGE_DEDUPE_ENABLED and client.bridge_key in record["seen_from"]:
            client.record_skip("skipped_dup_seen_by_target")
            inc_bridge_guard_counter("skipped_duplicate")
            log.debug("%s: skipped_dup_seen_by_target target=%s fp=%s", sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if BRIDGE_DEDUPE_ENABLED and client.bridge_key in record["sent_to"]:
            client.record_skip("skipped_dup_seen_by_target")
            inc_bridge_guard_counter("skipped_duplicate")
            log.debug("%s: skipped_dup_seen_by_target target=%s fp=%s", sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if client.has_seen_payload(forwarded_payload):
            client.record_skip("skipped_dup_fingerprint")
            inc_bridge_guard_counter("skipped_duplicate")
            log.debug("%s: skipped_dup_fingerprint target=%s fp=%s", sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        client_payload = forwarded_payload
        if envelope is not None and not client.supports_bridge_v2:
            client_payload = envelope["mesh_payload"]
        ok_budget, reason = client.can_accept_rf_inject(forwarded_payload, now)
        if not ok_budget:
            client.rf_budget_drops += 1
            client.record_skip(reason or "skipped_rf_inject_budget")
            inc_bridge_guard_counter("skipped_rf_inject_budget")
            log.warning("%s: skipped_rf_inject_budget target=%s fp=%s", sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if not client.enqueue_payload(client_payload, source=sender.display_name, seen_payload=forwarded_payload):
            log.warning("%s: queueing TX to %s failed", sender.addr, client.addr)


async def disconnect(client: "BridgeClient", reason: str = "EOF"):
    if client in connected_clients:
        connected_clients.discard(client)
        mark_node_disconnected(client)
        client.close()
        uptime = int(time.time() - client._connect_time)
        log.info(
            "Disconnected %s [%s] (%s) — rx=%d tx=%d hb=%d uptime=%ds",
            client.addr, client.display_name, reason, client.packets_rx, client.packets_tx, client.heartbeats_rx, uptime,
        )


async def refresh_client_block_stats(client: "BridgeClient", force: bool = False) -> None:
    now = time.time()
    if client not in connected_clients or not client.authenticated or client.writer.is_closing():
        return
    if client._block_stats_polling:
        return
    if not force and now - client._last_block_stats_poll < max(1, BLOCK_STATS_POLL_INTERVAL_SECS):
        return

    client._block_stats_polling = True
    client._last_block_stats_poll = now
    try:
        path_reply, node_reply = await asyncio.gather(
            client.send_command("get path.block", "", timeout=BLOCK_STATS_COMMAND_TIMEOUT_SECS, count_stats=False),
            client.send_command("get node.block", "", timeout=BLOCK_STATS_COMMAND_TIMEOUT_SECS, count_stats=False),
        )
        client.block_stats = {
            "path": parse_block_stats_reply("path", path_reply),
            "node": parse_block_stats_reply("node", node_reply),
            "updated_at": time.time(),
            "error": "",
        }
        client.block_drop_counter_state = update_block_drop_counters(
            client.block_drop_counter_state,
            client.block_stats,
            now=time.time(),
        )
        touch_node_stats(client)
    except asyncio.TimeoutError:
        client.block_stats = dict(client.block_stats)
        client.block_stats["error"] = "timeout"
        client.block_stats["updated_at"] = time.time()
    except Exception as exc:
        client.block_stats = dict(client.block_stats)
        client.block_stats["error"] = str(exc)
        client.block_stats["updated_at"] = time.time()
    finally:
        client._block_stats_polling = False


async def status_task():
    while True:
        if STATUS_INTERVAL_SECS <= 0:
            return
        await asyncio.sleep(STATUS_INTERVAL_SECS)
        now = time.time()
        for client in list(connected_clients):
            idle = int(now - client.last_seen)
            if CLIENT_TIMEOUT_SECS > 0 and idle > CLIENT_TIMEOUT_SECS:
                await disconnect(client, reason=f"idle timeout {idle}s")
                continue
            asyncio.create_task(refresh_client_block_stats(client))

        if connected_clients:
            summaries = []
            for client in sorted(connected_clients, key=lambda c: c.addr):
                prune_packet_times(client.packet_rx_times, now)
                prune_packet_times(client.packet_tx_times, now)
                summaries.append(
                    f"{client.display_name}@{client.addr} connected={format_duration(now - client._connect_time)} "
                    f"idle={int(now - client.last_seen)}s "
                    f"hb_age={str(int(now - client.last_heartbeat)) + 's' if client.last_heartbeat else 'never'} "
                    f"rx24h={len(client.packet_rx_times)} tx24h={len(client.packet_tx_times)} "
                    f"rx={client.packets_rx} tx={client.packets_tx} "
                    f"q={client.tx_queue.qsize()}/{CLIENT_TX_QUEUE_MAX} qdrop={client.tx_queue_dropped} "
                    f"qskip={client.tx_skipped_duplicates} serr={client.tx_send_errors} hb={client.heartbeats_rx}"
                )
            summary = ", ".join(summaries)
            log.info("Connected clients (%d): %s", len(connected_clients), summary)
        else:
            log.info("Connected clients: none")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    client = BridgeClient(reader, writer)
    if REPLACE_SAME_IP:
        for existing in list(connected_clients):
            if existing.host == client.host:
                await disconnect(existing, reason=f"replaced by new connection from {client.addr}")

    connected_clients.add(client)
    client.start_writer()
    touch_node_stats(client)
    log.info("Connected %s (total=%d)", client.addr, len(connected_clients))

    try:
        while True:
            payload = await client.read_packet()
            if payload is None:
                continue
            auth_password = parse_auth(payload)
            if auth_password is not None:
                if not BRIDGE_PASSWORD or auth_password == BRIDGE_PASSWORD:
                    client.authenticated = True
                    log.info("%s: bridge auth ok", client.addr)
                else:
                    log.warning("%s: bridge auth failed", client.addr)
                    await disconnect(client, reason="auth failed")
                    return
                continue
            if not client.authenticated:
                log.warning("%s: missing bridge auth", client.addr)
                await disconnect(client, reason="missing auth")
                return
            caps = parse_caps(payload)
            if caps is not None:
                client.supports_bridge_v2 = caps["bridge_v2"]
                client.bridge_proto_ver = int(caps.get("bridge_proto_ver") or 1)
                client.bridge_id = int(caps.get("bridge_id") or 0)
                group = (caps.get("group") or "").strip()
                if group:
                    client.bridge_group = group
                client.node_rf_inject_budget = {
                    "enabled": bool(caps.get("rf_inject_budget_enabled")),
                    "max_per_min": int(caps.get("rf_inject_max_per_min") or 0),
                    "max_airtime_ms_per_hour": int(caps.get("rf_inject_max_airtime_ms_per_hour") or 0),
                    "block_duty_above_pct": float(caps.get("rf_inject_block_duty_above_pct") or 0.0),
                }
                touch_node_stats(client)
                log.info("%s: bridge capabilities v%d proto=%d flags=0x%02x bridge_v2=%s group=%s bridge_id=%s",
                         client.addr, caps["version"], client.bridge_proto_ver, caps["flags"],
                         client.supports_bridge_v2, client.bridge_group,
                         f"0x{client.bridge_id:08x}" if client.bridge_id else "unknown")
                continue
            heartbeat = parse_heartbeat(payload)
            if heartbeat is not None:
                client.heartbeats_rx += 1
                now = time.time()
                client.last_heartbeat = now
                client.last_heartbeat_uptime_ms = int(heartbeat.get("uptime_ms") or 0)
                record_node_heartbeat(client, heartbeat, now)
                if "rf_duty" in heartbeat:
                    client.rf_duty = dict(get_node_stats(client).get("rf_duty") or {})
                if "radio_stats" in heartbeat:
                    client.radio_stats = dict(heartbeat["radio_stats"])
                if "neighbor_count" in heartbeat:
                    client.neighbor_count = int(heartbeat["neighbor_count"])
                if "flood_hop_limit_drops" in heartbeat:
                    client.flood_hop_limit_drops = int(heartbeat["flood_hop_limit_drops"])
                log.debug("%s: heartbeat uptime=%dms", client.addr, client.last_heartbeat_uptime_ms)
                continue
            command_reply = parse_command_reply(payload)
            if command_reply is not None:
                request_id, reply = command_reply
                future = pending_commands.get(request_id)
                if future and not future.done():
                    future.set_result(reply)
                else:
                    log.debug("%s: stale command reply id=%d", client.addr, request_id)
                continue
            node_info = parse_node_info(payload)
            if node_info is not None:
                client.node_name, client.firmware_version, node_id = node_info
                if node_id:
                    client.node_id = node_id
                touch_node_stats(client)
                if client.firmware_version:
                    log.info("%s: node name is %s firmware=%s node_id=%s",
                             client.addr, client.node_name, client.firmware_version, client.node_id or "unknown")
                else:
                    log.info("%s: node name is %s node_id=%s", client.addr, client.node_name, client.node_id or "unknown")
                continue
            bridge_packet_error = parse_bridge_packet_error(payload)
            if bridge_packet_error:
                client.record_skip("skipped_bridge_parse_error")
                inc_bridge_guard_counter("skipped_bridge_parse_error")
                log.warning("%s: skipped_bridge_parse_error %s", client.addr, bridge_packet_error)
                continue
            short_id = source_short_id(payload)
            if short_id_quarantine_active(short_id, now=time.time()):
                client.record_skip("skipped_short_id_quarantine")
                inc_bridge_guard_counter("skipped_short_id_quarantine")
                log.warning(
                    "%s: skipped_short_id_quarantine source=%s id=%s",
                    client.addr,
                    client.display_name,
                    short_id_label(short_id),
                )
                continue
            client.packets_rx += 1
            inc_bridge_guard_counter("accepted_tcp_packet")
            now = time.time()
            client.packet_rx_times.append(now)
            prune_packet_times(client.packet_rx_times, now)
            record_node_packet(client, "RX", now)
            mesh_payload = mesh_payload_for_parsing(payload)
            fingerprint = packet_fingerprint(payload)
            record = packet_fingerprint_record(fingerprint, now)
            client.last_fingerprint = fingerprint
            client.last_fingerprint_at = now
            sent_at = record["sent_to"].get(client.bridge_key)
            if BRIDGE_LOOPGUARD_ENABLED and sent_at and now - sent_at <= max(1, BRIDGE_LOOPGUARD_WINDOW_SECS):
                client.record_loopguard_hit(now)
                inc_bridge_guard_counter("skipped_bridge_loop")
                record_short_id_bad_hit(short_id, "loopguard", client, now)
                if client.quarantine_active(now):
                    client.record_skip("skipped_quarantine")
                    log.warning("%s: skipped_quarantine source=%s fp=%s",
                                client.addr, client.display_name, fingerprint_hex(fingerprint))
                    continue
            if BRIDGE_DEDUPE_ENABLED and client.bridge_key in record["seen_from"]:
                client.record_skip("skipped_dup_fingerprint")
                inc_bridge_guard_counter("skipped_duplicate")
                record_short_id_bad_hit(short_id, "duplicate", client, now)
                log.debug("%s: skipped_dup_fingerprint source=%s fp=%s",
                          client.addr, client.display_name, fingerprint_hex(fingerprint))
                continue
            if client.quarantine_active(now):
                client.record_skip("skipped_quarantine")
                inc_bridge_guard_counter("skipped_bridge_loop")
                record_short_id_bad_hit(short_id, "source_quarantine", client, now)
                log.warning("%s: skipped_quarantine source=%s fp=%s",
                            client.addr, client.display_name, fingerprint_hex(fingerprint))
                continue
            record["seen_from"][client.bridge_key] = now
            client.mark_seen_payload(payload)
            parsed_payload = parse_mesh_payload(mesh_payload)
            if not allow_transport_packet(client, parsed_payload, now):
                inc_bridge_guard_counter("skipped_rate_limited")
                record_short_id_bad_hit(short_id, "rate_limited", client, now)
                packet_log = record_packet_log("DROP", client, payload, target="rate-limit")
                log.warning(
                    "%s: dropping transport flood packet from %s (%d/%ds client, %d/%ds global): %s",
                    client.addr,
                    client.display_name,
                    len(client.transport_rx_times),
                    TRANSPORT_RATE_LIMIT_WINDOW_SECS,
                    len(transport_rx_times),
                    TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS,
                    format_packet_description(packet_log),
                )
                continue
            location_report = parse_mesh_location_payload(mesh_payload)
            if location_report is not None:
                record_location(location_report, client)
            node_advert = parse_node_advert_payload(mesh_payload)
            if node_advert is not None and node_advert.get("advert_type") == ADV_TYPE_REPEATER:
                client.learn_node_id(node_advert.get("node_id", ""), node_advert.get("name", ""))
            sensor_report = parse_sensor_advert_payload(mesh_payload)
            if sensor_report is not None:
                record_sensor_advert(sensor_report, client)
            packet_log = record_packet_log("RX", client, payload)
            if LOG_PACKETS:
                envelope = parse_bridge_packet_envelope(payload)
                if envelope is not None:
                    log.info(
                        "%s -> server: RX bridge-v2 mesh=%d bytes %s: %s",
                        packet_log["source"],
                        envelope["packet_len"],
                        format_packet_description(packet_log),
                        packet_log["preview"],
                    )
                else:
                    log.info(
                        "%s -> server: RX %d bytes %s: %s",
                        packet_log["source"],
                        len(payload),
                        format_packet_description(packet_log),
                        packet_log["preview"],
                    )
            log.debug("%s: RX %d bytes → broadcasting to %d peers",
                      client.addr, len(payload), len(connected_clients) - 1)
            await broadcast(payload, sender=client)
    except asyncio.IncompleteReadError:
        await disconnect(client, reason="EOF")
    except Exception as exc:
        await disconnect(client, reason=str(exc))


def status_snapshot(include_disconnected: bool = True) -> dict:
    now = time.time()
    prune_disconnected_node_stats(now)
    try:
        asyncio.get_running_loop()
        for client in list(connected_clients):
            if now - client._last_block_stats_poll >= max(1, BLOCK_STATS_POLL_INTERVAL_SECS):
                asyncio.create_task(refresh_client_block_stats(client))
    except RuntimeError:
        pass
    clients = [
        client.status_dict(now)
        for client in sorted(connected_clients, key=lambda c: (c.display_name.lower(), c.addr))
    ]
    if include_disconnected:
        active_keys = {getattr(client, "_stats_key", "") for client in connected_clients}
        offline_clients = [
            node_stats_status_dict(stats, now)
            for key, stats in node_traffic_stats.items()
            if key not in active_keys and has_node_identity(stats) and (
                stats.get("last_seen", 0) >= now - PACKET_COUNTER_WINDOW_SECS
                or stats["rx_times"]
                or stats["tx_times"]
            )
        ]
        clients.extend(sorted(
            offline_clients,
            key=lambda c: (c["display_name"].lower(), -(c.get("last_seen_seconds") or 0)),
        ))
    node_block_drops = sum(int((c.get("block_stats_totals") or {}).get("node_drops") or 0) for c in clients)
    path_block_drops = sum(int((c.get("block_stats_totals") or {}).get("path_drops") or 0) for c in clients)
    node_block_active = sum(int((c.get("block_stats_totals") or {}).get("node_active") or 0) for c in clients)
    path_block_active = sum(int((c.get("block_stats_totals") or {}).get("path_active") or 0) for c in clients)
    return {
        "generated_at": int(now),
        "server": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "started_at": int(SERVER_STARTED_AT),
            "uptime_seconds": int(now - SERVER_STARTED_AT),
        },
        "connected_count": len(connected_clients),
        "online_count": len(connected_clients),
        "known_count": len(clients),
        "transport_rate_limit": {
            "enabled": TRANSPORT_RATE_LIMIT_ENABLE,
            "client_max": TRANSPORT_RATE_LIMIT_MAX,
            "client_window_secs": TRANSPORT_RATE_LIMIT_WINDOW_SECS,
            "global_max": TRANSPORT_GLOBAL_RATE_LIMIT_MAX,
            "global_window_secs": TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS,
            "global_count": len(transport_rx_times),
            "dropped": transport_rate_dropped,
        },
        "bridge_guards": {
            "dedupe_enabled": BRIDGE_DEDUPE_ENABLED,
            "dedupe_ttl_sec": BRIDGE_DEDUPE_TTL_SECS,
            "dedupe_max_entries": BRIDGE_DEDUPE_MAX_ENTRIES,
            "dedupe_entries": len(packet_fingerprint_cache),
            "counters": dict(bridge_guard_counters),
            "loopguard_enabled": BRIDGE_LOOPGUARD_ENABLED,
            "loopguard_window_sec": BRIDGE_LOOPGUARD_WINDOW_SECS,
            "loopguard_threshold": BRIDGE_LOOPGUARD_THRESHOLD,
            "loopguard_quarantine_sec": BRIDGE_LOOPGUARD_QUARANTINE_SECS,
            "rf_inject_enabled": BRIDGE_RF_INJECT_ENABLED,
            "rf_inject_max_per_min": BRIDGE_RF_INJECT_MAX_PER_MIN,
            "rf_inject_max_airtime_ms_per_hour": BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR,
            "rf_inject_block_duty_above_pct": BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT,
            "group": BRIDGE_GROUP,
            "require_group_match": BRIDGE_REQUIRE_GROUP_MATCH,
            "short_id_quarantine_enabled": SHORT_ID_QUARANTINE_ENABLED,
            "short_id_quarantine_window_sec": SHORT_ID_QUARANTINE_WINDOW_SECS,
            "short_id_quarantine_threshold": SHORT_ID_QUARANTINE_THRESHOLD,
            "short_id_quarantine_sec": SHORT_ID_QUARANTINE_SECS,
            "short_id_quarantine": short_id_quarantine_snapshot(now),
            "block_stats_poll_interval_sec": BLOCK_STATS_POLL_INTERVAL_SECS,
            "node_block_active": node_block_active,
            "path_block_active": path_block_active,
            "node_block_drops": node_block_drops,
            "path_block_drops": path_block_drops,
            "block_drops": node_block_drops + path_block_drops,
        },
        "firmware_release": dict(latest_firmware_info),
        "clients": redact_public_value(clients),
    }


def locations_snapshot() -> dict:
    now = int(time.time())
    locations = []
    for report in latest_locations.values():
        item = dict(report)
        item["age_seconds"] = max(0, now - item["received_at"])
        item["track"] = list(latest_location_tracks.get(item["node_id"], ()))
        locations.append(item)
    locations.sort(key=lambda item: (item.get("name") or item["node_id"]).lower())
    return {
        "generated_at": now,
        "location_count": len(locations),
        "locations": redact_public_value(locations),
    }


def sensors_snapshot() -> dict:
    now = int(time.time())
    sensors = []
    for report in latest_sensors.values():
        item = dict(report)
        item["age_seconds"] = max(0, now - item["received_at"])
        item["node_id_short"] = item.get("node_id", "")[:2]
        sensors.append(item)
    sensors.sort(key=lambda item: (item.get("name") or item["node_id"]).lower())
    return {
        "generated_at": now,
        "sensor_count": len(sensors),
        "sensors": redact_public_value(sensors),
    }


def packets_snapshot() -> dict:
    now = int(time.time())
    packets = []
    for entry in recent_packets:
        item = dict(entry)
        item["age_seconds"] = max(0, now - item["time"])
        packets.append(redact_public_value(item))
    return {
        "generated_at": now,
        "packet_count": len(packets),
        "packet_capacity": recent_packets.maxlen or len(packets),
        "packet_total": packet_log_total,
        "packets": packets,
    }


def normalize_base_path(path: str) -> str:
    path = (path or "").strip()
    if not path or path == "/":
        return ""
    return "/" + path.strip("/")


def request_base_path(headers: dict[str, str]) -> str:
    forwarded_prefix = headers.get("x-forwarded-prefix", "") or headers.get("x-script-name", "")
    if forwarded_prefix:
        return normalize_base_path(forwarded_prefix)
    return STATUS_BASE_PATH


def prefixed_url(base_path: str, route: str) -> str:
    base_path = normalize_base_path(base_path)
    if route == "/":
        return base_path or "/"
    if not route.startswith("/"):
        route = "/" + route
    return f"{base_path}{route}"


def strip_base_path(path: str, base_path: str) -> str:
    route = urlsplit(path or "/").path or "/"
    base_path = normalize_base_path(base_path)
    if base_path:
        if route == base_path:
            return "/"
        if route.startswith(base_path + "/"):
            return route[len(base_path):] or "/"
    return route


def build_status_html(base_path: str = "") -> str:
    manage_url = prefixed_url(base_path, "/manage")
    map_url = prefixed_url(base_path, "/map")
    status_json_url = prefixed_url(base_path, "/status.json")
    packets_json_url = prefixed_url(base_path, "/packets.json")
    sensors_json_url = prefixed_url(base_path, "/sensors.json")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG TCP Bridge Status</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #050806;
      --panel: rgba(8, 18, 12, .88);
      --panel-2: rgba(13, 28, 19, .82);
      --line: rgba(97, 255, 154, .28);
      --line-strong: rgba(97, 255, 154, .55);
      --green: #68ff9d;
      --green-soft: #a1ffc4;
      --amber: #ffd166;
      --red: #ff5f6d;
      --muted: #8fb99e;
      --text: #dfffe9;
      --shadow: 0 18px 60px rgba(0, 0, 0, .45);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: var(--bg); }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 12%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(180deg, rgba(2, 10, 6, .7), rgba(2, 5, 3, .98)),
        var(--bg);
      overflow-x: hidden;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(104, 255, 157, .04) 50%, rgba(0, 0, 0, .13) 50%),
        linear-gradient(90deg, rgba(255, 0, 0, .025), rgba(0, 255, 95, .018), rgba(0, 120, 255, .025));
      background-size: 100% 4px, 7px 100%;
      mix-blend-mode: screen;
      opacity: .42;
      z-index: 3;
    }}
    main {{
      width: min(1480px, 100%);
      margin: 0 auto;
      padding: 24px;
      position: relative;
      z-index: 1;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      padding: 18px 0 22px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      color: var(--green);
      font-size: clamp(1.5rem, 3vw, 2.8rem);
      letter-spacing: 0;
      text-transform: uppercase;
      text-shadow: 0 0 16px rgba(104, 255, 157, .45);
    }}
    .subtitle {{ margin: 8px 0 0; color: var(--muted); max-width: 820px; line-height: 1.45; }}
    .server-version {{
      display: inline-block;
      margin-top: 8px;
      color: var(--green-soft);
      font-size: .78rem;
      letter-spacing: 0;
    }}
    nav {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }}
    a {{
      color: var(--green-soft);
      text-decoration: none;
      border: 1px solid var(--line);
      background: rgba(104, 255, 157, .07);
      padding: 9px 12px;
      border-radius: 4px;
    }}
    a:hover {{ border-color: var(--green); box-shadow: 0 0 18px rgba(104, 255, 157, .22); }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px;
      margin: 20px 0;
    }}
    .metric, .panel {{
      background: linear-gradient(180deg, var(--panel), rgba(3, 10, 6, .92));
      border: 1px solid var(--line);
      box-shadow: var(--shadow), inset 0 0 24px rgba(104, 255, 157, .035);
      border-radius: 6px;
    }}
    .metric {{ padding: 14px; min-height: 96px; }}
    .label {{ color: var(--muted); font-size: .76rem; text-transform: uppercase; }}
    .value {{ margin-top: 8px; color: var(--green); font-size: clamp(1.4rem, 2.4vw, 2.2rem); font-weight: 800; }}
    .value.small {{ font-size: 1rem; overflow-wrap: anywhere; }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(360px, .85fr);
      gap: 16px;
      align-items: start;
    }}
    .panel {{ overflow: hidden; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(104, 255, 157, .06);
    }}
    h2 {{ margin: 0; font-size: .95rem; color: var(--green-soft); text-transform: uppercase; }}
    .pulse {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: .78rem;
      white-space: nowrap;
    }}
    .pulse::before {{
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 14px var(--green);
    }}
    .pulse.warn::before {{ background: var(--amber); box-shadow: 0 0 14px var(--amber); }}
    .pulse.error::before {{ background: var(--red); box-shadow: 0 0 14px var(--red); }}
    .table-wrap {{ overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{
      padding: 6px 8px;
      text-align: left;
      border-bottom: 1px solid rgba(97, 255, 154, .14);
      white-space: normal;
      vertical-align: top;
      font-size: .78rem;
      line-height: 1.25;
    }}
    th {{ color: var(--muted); background: rgba(0, 0, 0, .24); text-transform: uppercase; font-size: .64rem; }}
    td {{ color: #dfffe9; }}
    tr.hot td {{ color: var(--green-soft); background: rgba(104, 255, 157, .055); }}
    .badge {{
      display: inline-block;
      min-width: 42px;
      text-align: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 6px;
      font-size: .7rem;
      color: var(--green-soft);
      background: rgba(104, 255, 157, .08);
    }}
    .badge.rx {{ color: #86c5ff; border-color: rgba(134, 197, 255, .45); }}
    .badge.tx {{ color: var(--amber); border-color: rgba(255, 209, 102, .45); }}
    .badge.offline {{ color: var(--amber); border-color: rgba(255, 209, 102, .45); }}
    .badge.update {{ color: #ff8f8f; border-color: rgba(255, 143, 143, .55); background: rgba(255, 91, 91, .12); }}
    .badge.current {{ color: var(--green-soft); border-color: rgba(104, 255, 157, .42); background: rgba(104, 255, 157, .08); }}
    .badge.pending {{ color: var(--amber); border-color: rgba(255, 209, 102, .42); background: rgba(255, 209, 102, .08); }}
    .badge.error {{ color: #ff8f8f; border-color: rgba(255, 143, 143, .55); background: rgba(255, 91, 91, .12); }}
    .packet-age {{ width: 54px; }}
    .packet-dir {{ width: 40px; }}
    .packet-flow {{ width: 24%; }}
    .packet-kind {{ width: 23%; }}
    .packet-data {{ width: auto; }}
    .packet-main {{ color: #dfffe9; overflow-wrap: anywhere; }}
    .packet-sub {{ color: var(--muted); font-size: .7rem; margin-top: 2px; overflow-wrap: anywhere; }}
    .preview {{ max-width: 100%; white-space: normal; overflow-wrap: anywhere; color: var(--muted); }}
    .empty {{ text-align: center; color: var(--muted); padding: 28px; }}
    .feed {{
      padding: 8px 10px;
      height: 430px;
      overflow: auto;
      background: rgba(0, 0, 0, .24);
    }}
    .feed-line {{
      display: grid;
      grid-template-columns: 48px 28px minmax(88px, 1fr);
      gap: 7px;
      padding: 3px 0;
      border-bottom: 1px dashed rgba(97, 255, 154, .14);
      font-size: .74rem;
      line-height: 1.25;
    }}
    .feed-line .meta {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .feed-line .dir-rx {{ color: #86c5ff; font-weight: 800; }}
    .feed-line .dir-tx {{ color: var(--amber); font-weight: 800; }}
    .feed-line .packet {{ overflow-wrap: anywhere; }}
    .stack {{ display: grid; gap: 16px; }}
    .node-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 8px;
      padding: 10px;
    }}
    .node-card {{
      border: 1px solid rgba(97, 255, 154, .22);
      background: var(--panel-2);
      border-radius: 6px;
      padding: 9px;
      min-height: 0;
    }}
    .node-card.offline {{
      border-color: rgba(255, 209, 102, .2);
      background: rgba(15, 18, 18, .7);
    }}
    .node-title {{ color: var(--green-soft); font-size: .92rem; font-weight: 800; overflow-wrap: anywhere; }}
    .node-meta {{ margin-top: 5px; color: var(--muted); font-size: .72rem; line-height: 1.25; overflow-wrap: anywhere; }}
    .node-stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(68px, 1fr));
      gap: 5px;
      margin-top: 8px;
    }}
    .mini {{
      min-width: 0;
      border: 1px solid rgba(97, 255, 154, .14);
      background: rgba(0, 0, 0, .16);
      padding: 5px 6px;
      border-radius: 4px;
      overflow: hidden;
    }}
    .mini .label {{ display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .mini b {{
      display: block;
      min-width: 0;
      color: var(--green);
      font-size: .82rem;
      font-variant-numeric: tabular-nums;
      line-height: 1.15;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 980px) {{
      main {{ padding: 16px 12px; }}
      .topbar {{ display: block; }}
      nav {{ justify-content: flex-start; margin-top: 14px; }}
      .status-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid {{ grid-template-columns: 1fr; }}
      .feed {{ height: 340px; }}
    }}
    @media (max-width: 560px) {{
      .status-strip {{ grid-template-columns: 1fr; }}
      .node-grid {{ grid-template-columns: 1fr; }}
      .node-stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table, thead, tbody, tr, th, td {{ display: block; width: 100%; }}
      thead {{ display: none; }}
      tr {{
        padding: 7px 8px;
        border-bottom: 1px solid rgba(97, 255, 154, .18);
      }}
      th, td {{
        border-bottom: 0;
        padding: 2px 0;
      }}
      td.packet-age {{
        color: var(--muted);
        font-size: .7rem;
      }}
      td:nth-child(2) {{
        margin: 2px 0;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>TCP Bridge Tactical Console</h1>
        <div class="server-version">{html.escape(SERVER_NAME)} v{html.escape(SERVER_VERSION)}</div>
        <p class="subtitle">MeshCoreNG live bridge telemetry, packet flow and nearby sensor adverts. Polling the bridge server every 2 seconds.</p>
      </div>
      <nav>
        <a href="{manage_url}">Remote management</a>
        <a href="{map_url}">Tracker map</a>
      </nav>
    </header>

    <section class="status-strip" aria-label="Live counters">
      <div class="metric"><div class="label">Bridge nodes online</div><div id="metricConnected" class="value">--</div></div>
      <div class="metric"><div class="label">Packet history</div><div id="metricPackets" class="value">--</div></div>
      <div class="metric"><div class="label">Packets total</div><div id="metricPacketsTotal" class="value">--</div></div>
      <div class="metric"><div class="label">Total dedups</div><div id="metricDedups" class="value">--</div></div>
      <div class="metric"><div class="label">Short-ID quarantine</div><div id="metricShortQ" class="value">--</div></div>
      <div class="metric"><div class="label">ID/path block drops</div><div id="metricBlockDrops" class="value">--</div></div>
      <div class="metric"><div class="label">Nearby sensors</div><div id="metricSensors" class="value">--</div></div>
      <div class="metric" title="TCP bridge packets seen by this webserver, not repeater RF RT/TX counters"><div class="label">Bridge RX / TX 24h</div><div id="metricTraffic" class="value small">-- / --</div></div>
      <div class="metric"><div class="label">Last sync</div><div id="metricSync" class="value small">booting</div></div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Bridge nodes</h2>
        <span id="nodeStatus" class="pulse warn">connecting</span>
      </div>
      <div id="nodeCards" class="node-grid"></div>
    </section>

    <div class="grid" style="margin-top:16px">
      <section class="panel">
        <div class="panel-head">
          <h2>Packet log</h2>
          <span id="packetStatus" class="pulse warn">waiting</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="packet-age">Age</th>
                <th class="packet-dir">Dir</th>
                <th class="packet-flow">Flow</th>
                <th class="packet-kind">Packet</th>
                <th class="packet-data">Data</th>
              </tr>
            </thead>
            <tbody id="packetRows">
              <tr><td colspan="5" class="empty">Loading packet telemetry</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="stack">
        <section class="panel">
          <div class="panel-head">
            <h2>Live terminal feed</h2>
            <span id="feedStatus" class="pulse warn">arming</span>
          </div>
          <div id="packetFeed" class="feed"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>Sensor nodes nearby</h2>
            <span id="sensorStatus" class="pulse warn">scanning</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Node ID</th>
                  <th>Last seen</th>
                  <th>Seen</th>
                  <th>Hops</th>
                  <th>Via bridge</th>
                  <th>Location</th>
                </tr>
              </thead>
              <tbody id="sensorRows">
                <tr><td colspan="7" class="empty">Scanning for sensor adverts</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const urls = {{
      status: "{status_json_url}",
      packets: "{packets_json_url}",
      sensors: "{sensors_json_url}"
    }};
    const state = {{
      seenPacketKeys: new Set(),
      firstPacketLoad: true
    }};

    const text = (value, fallback = "") => value === null || value === undefined || value === "" ? fallback : String(value);
    const age = (seconds) => seconds === null || seconds === undefined ? "never" : `${{seconds}}s`;
    const yesNo = (value) => value ? "yes" : "no";

    function escapeHtml(value) {{
      return text(value).replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function setStatus(id, label, mode = "ok") {{
      const el = document.getElementById(id);
      el.textContent = label;
      el.className = mode === "error" ? "pulse error" : mode === "warn" ? "pulse warn" : "pulse";
    }}

    async function getJson(url) {{
      const response = await fetch(url, {{ cache: "no-store" }});
      if (!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
      return response.json();
    }}

    function renderMetrics(status, packets, sensors) {{
      const rx24 = status.clients.reduce((sum, client) => sum + (client.packets_rx_24h || 0), 0);
      const tx24 = status.clients.reduce((sum, client) => sum + (client.packets_tx_24h || 0), 0);
      const guardCounters = (status.bridge_guards && status.bridge_guards.counters) || {{}};
      const clientDedups = status.clients.reduce((sum, client) => sum + (client.skipped_dup_total || 0), 0);
      const totalDedups = guardCounters.skipped_duplicate || clientDedups;
      const shortQuarantine = (status.bridge_guards && status.bridge_guards.short_id_quarantine) || [];
      const blockDrops = (status.bridge_guards && status.bridge_guards.block_drops) || 0;
      const nodeBlockDrops = (status.bridge_guards && status.bridge_guards.node_block_drops) || 0;
      const pathBlockDrops = (status.bridge_guards && status.bridge_guards.path_block_drops) || 0;
      const nodeBlockActive = (status.bridge_guards && status.bridge_guards.node_block_active) || 0;
      const pathBlockActive = (status.bridge_guards && status.bridge_guards.path_block_active) || 0;
      document.getElementById("metricConnected").textContent = status.connected_count;
      document.getElementById("metricPackets").textContent = `${{packets.packet_count}}/${{packets.packet_capacity || 200}}`;
      document.getElementById("metricPacketsTotal").textContent = packets.packet_total || packets.packet_count || 0;
      document.getElementById("metricDedups").textContent = totalDedups;
      document.getElementById("metricShortQ").textContent = shortQuarantine.length;
      document.getElementById("metricShortQ").title = shortQuarantine.length
        ? shortQuarantine.map((item) => `${{item.id}} ${{item.seconds_left}}s ${{item.reason || ""}}`).join(", ")
        : ((status.bridge_guards && status.bridge_guards.short_id_quarantine_enabled) ? "enabled, no blocked short IDs" : "disabled");
      document.getElementById("metricBlockDrops").textContent = blockDrops;
      document.getElementById("metricBlockDrops").title = `ID blocks: ${{nodeBlockActive}} active, ${{nodeBlockDrops}} drops. Path blocks: ${{pathBlockActive}} active, ${{pathBlockDrops}} drops.`;
      document.getElementById("metricSensors").textContent = sensors.sensor_count;
      document.getElementById("metricTraffic").textContent = `${{rx24}} / ${{tx24}}`;
      document.getElementById("metricSync").textContent = new Date().toLocaleTimeString();
    }}

    function pct(value) {{
      return Number.isFinite(value) ? `${{value.toFixed(value >= 10 ? 1 : 2)}}%` : "--";
    }}

    function seconds(ms) {{
      return Number.isFinite(ms) ? `${{Math.round(ms / 1000)}}s` : "--";
    }}

    function duration(ms) {{
      if (!Number.isFinite(ms)) return "--";
      const total = Math.max(0, Math.round(ms / 1000));
      const minutes = Math.floor(total / 60);
      const seconds = total % 60;
      return minutes > 0 ? `${{minutes}}m ${{String(seconds).padStart(2, "0")}}s` : `${{seconds}}s`;
    }}

    function formatBlockEntries(entries) {{
      if (!entries || !entries.length) return "none";
      return entries.map((entry) => `${{entry.value}} ${{entry.seconds_left || 0}}s drops=${{entry.drops || 0}}`).join(", ");
    }}

    function dbm(value) {{
      return Number.isFinite(value) ? `${{Math.round(value)}} dBm` : "--";
    }}

    function snr(value) {{
      return Number.isFinite(value) ? `${{value.toFixed(1)}} dB` : "--";
    }}

    function renderNodes(status) {{
      const target = document.getElementById("nodeCards");
      if (!status.clients.length) {{
        target.innerHTML = '<div class="empty">No bridge nodes seen in the last 24h</div>';
        setStatus("nodeStatus", "no nodes", "warn");
        return;
      }}
      setStatus("nodeStatus", `${{status.connected_count}} online / ${{status.known_count || status.clients.length}} known`, status.connected_count ? "ok" : "warn");
      target.innerHTML = status.clients.map((client) => {{
        const heartbeat = client.heartbeat_age_seconds === null ? "never" : `${{client.heartbeat_age_seconds}}s ago`;
        const isOnline = client.connected !== false;
        const update = client.firmware_update || {{}};
        const updateAvailable = update.state === "available";
        const updateTitle = updateAvailable
          ? `new firmware available: ${{update.latest_version || update.latest_tag}} (${{update.bin_count || 0}} bin files)`
          : update.check_status === "error" ? `firmware update check failed: ${{update.error || "unknown error"}}`
          : update.state === "current" ? `firmware current${{update.latest_version ? ": " + update.latest_version : ""}}`
          : update.check_status === "pending" ? "firmware update check pending"
          : update.check_status === "disabled" ? "firmware update check disabled"
          : "firmware update state unknown";
        const updateBadgeClass = updateAvailable ? "update" : update.check_status === "error" ? "error" : update.state === "current" ? "current" : "pending";
        const updateBadgeText = updateAvailable ? `update ${{update.latest_version || ""}}` : update.state === "current" ? "current" : update.check_status === "error" ? "check error" : "unknown";
        const updateBadge = updateAvailable
          ? `<a class="badge ${{updateBadgeClass}}" title="${{escapeHtml(updateTitle)}}" href="${{escapeHtml(update.latest_url || "#")}}" target="_blank" rel="noopener">${{escapeHtml(updateBadgeText)}}</a>`
          : `<span class="badge ${{updateBadgeClass}}" title="${{escapeHtml(updateTitle)}}">${{escapeHtml(updateBadgeText)}}</span>`;
        const rf = client.rf_duty || {{}};
        const rfFirmwareUsedMs = Number.isFinite(rf.tx_used_ms) ? rf.tx_used_ms : NaN;
        const rfUsedMs = Number.isFinite(rf.tx_hour_used_ms) ? rf.tx_hour_used_ms : rfFirmwareUsedMs;
        const rfMaxMs = Number.isFinite(rf.tx_max_ms) ? rf.tx_max_ms : NaN;
        const rfLeftMs = Number.isFinite(rf.tx_hour_left_ms)
          ? rf.tx_hour_left_ms
          : Number.isFinite(rfUsedMs) && Number.isFinite(rfMaxMs) ? Math.max(0, rfMaxMs - rfUsedMs) : NaN;
        const rfReset = Number.isFinite(rf.tx_hour_resets_in_seconds) ? duration(rf.tx_hour_resets_in_seconds * 1000) : "unknown";
        const rfSinceServer = Number.isFinite(rf.tx_since_server_ms) ? duration(rf.tx_since_server_ms) : "unknown";
        const rfTitle = Number.isFinite(rf.tx_used_pct)
          ? `Current website hour: used ${{duration(rfUsedMs)}} and left ${{duration(rfLeftMs)}} from the ${{pct(rf.duty_limit_pct)}} hourly dutycycle budget (${{duration(rfMaxMs)}} total). Resets in ${{rfReset}}. Firmware current-window used ${{duration(rfFirmwareUsedMs)}}. Since server saw node: ${{rfSinceServer}}.`
          : "firmware update needed";
        const radio = client.radio_stats || {{}};
        const radioTitle = Number.isFinite(radio.noise_floor)
          ? `Radio stats from last bridge heartbeat: noise floor ${{dbm(radio.noise_floor)}}, last RSSI ${{dbm(radio.last_rssi)}}, last SNR ${{snr(radio.last_snr)}}.`
          : "firmware update needed";
        const neighborCount = Number.isFinite(client.neighbor_count) ? client.neighbor_count : NaN;
        const neighborTitle = Number.isFinite(neighborCount)
          ? `Neighbours heard locally by this bridge node: ${{neighborCount}}.`
          : "firmware update needed";
        const floodHopLimitDrops = Number.isFinite(client.flood_hop_limit_drops) ? client.flood_hop_limit_drops : NaN;
        const floodHopLimitTitle = Number.isFinite(floodHopLimitDrops)
          ? "Flood packets this bridge node did not retransmit because their path had reached the configured hop limit."
          : "firmware update needed";
        const footer = isOnline
          ? `connected ${{escapeHtml(client.connected_for)}} · idle ${{client.idle_seconds}}s · heartbeat ${{heartbeat}}`
          : `offline · last seen ${{age(client.last_seen_seconds)}} ago · heartbeat ${{heartbeat}}`;
        const lastTx = client.last_tx_age_seconds === null || client.last_tx_age_seconds === undefined ? "never" : `${{client.last_tx_age_seconds}}s ago`;
        const skipReasons = client.skipped_dup_by_reason || {{}};
        const skipReasonText = Object.entries(skipReasons).map(([key, value]) => `${{key}}=${{value}}`).join(", ") || "none";
        const bridgeTrafficTitle = "TCP bridge packets seen by this webserver for this node, not repeater RF RT/TX counters.";
        const queueTitle = `queued total ${{client.tx_queued || 0}}, high water ${{client.tx_queue_high_water || 0}}, skipped duplicates ${{client.skipped_dup_total || 0}}, reasons: ${{skipReasonText}}, send errors ${{client.tx_send_errors || 0}}${{client.last_tx_error ? ", last error: " + client.last_tx_error : ""}}`;
        const guardTitle = `group ${{client.group || "default"}}, loop score ${{client.loop_score || 0}}, quarantine ${{client.quarantine_active ? (client.quarantine_seconds_left || 0) + "s" : "no"}}, last fingerprint ${{client.last_fingerprint || "none"}}`;
        const budgetLeft = client.rf_inject_budget_remaining_ms === null || client.rf_inject_budget_remaining_ms === undefined
          ? "off"
          : duration(client.rf_inject_budget_remaining_ms);
        const blockStats = client.block_stats || {{}};
        const blockTotals = client.block_stats_totals || {{}};
        const nodeBlockTitle = `${{formatBlockEntries(blockStats.node)}}${{blockStats.error ? " | poll error: " + blockStats.error : ""}}`;
        const pathBlockTitle = `${{formatBlockEntries(blockStats.path)}}${{blockStats.error ? " | poll error: " + blockStats.error : ""}}`;
        return `
          <article class="node-card${{isOnline ? "" : " offline"}}">
            <div class="node-title">
              <span>${{escapeHtml(client.display_name)}}</span>
            </div>
            <div class="node-meta">node id ${{escapeHtml(client.node_id || "unknown")}}<br>${{escapeHtml(client.firmware_version || "firmware unknown")}} ${{updateBadge}}</div>
            <div class="node-stats">
              <div class="mini" title="${{escapeHtml(bridgeTrafficTitle)}}"><span class="label">Bridge RX 24h</span><b>${{client.packets_rx_24h}}</b></div>
              <div class="mini" title="${{escapeHtml(bridgeTrafficTitle)}}"><span class="label">Bridge TX 24h</span><b>${{client.packets_tx_24h}}</b></div>
              <div class="mini" title="${{escapeHtml(neighborTitle)}}"><span class="label">Neighbours</span><b>${{Number.isFinite(neighborCount) ? neighborCount : "--"}}</b></div>
              <div class="mini" title="${{escapeHtml(floodHopLimitTitle)}}"><span class="label">Hop-limit drops</span><b>${{Number.isFinite(floodHopLimitDrops) ? floodHopLimitDrops : "--"}}</b></div>
              <div class="mini" title="${{escapeHtml(rfTitle)}}"><span class="label">Duty used</span><b>${{duration(rfUsedMs)}}</b></div>
              <div class="mini" title="${{escapeHtml(rfTitle)}}"><span class="label">Duty left</span><b>${{duration(rfLeftMs)}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Queue</span><b>${{client.tx_queue_depth || 0}}/${{client.tx_queue_max || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Q drops</span><b>${{client.tx_queue_dropped || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Dedup</span><b>${{client.skipped_dup_total || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Loop</span><b>${{client.loop_score || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Group</span><b>${{escapeHtml(client.group || "default")}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Quality</span><b>${{client.bridge_quality_score ?? 0}}</b></div>
              <div class="mini" title="RF inject budget remaining"><span class="label">RF budget</span><b>${{budgetLeft}}</b></div>
              <div class="mini" title="${{escapeHtml(nodeBlockTitle)}}"><span class="label">ID block</span><b>${{blockTotals.node_drops || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(pathBlockTitle)}}"><span class="label">Path block</span><b>${{blockTotals.path_drops || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">Noise</span><b>${{dbm(radio.noise_floor)}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">RSSI</span><b>${{dbm(radio.last_rssi)}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">SNR</span><b>${{snr(radio.last_snr)}}</b></div>
              <div class="mini"><span class="label">HB</span><b>${{client.heartbeats_rx}}</b></div>
            </div>
            <div class="node-meta">${{footer}} · last tx ${{lastTx}} · dedup ${{client.skipped_dup_total || 0}} · ${{client.quarantine_active ? "quarantine " + (client.quarantine_seconds_left || 0) + "s" : "not quarantined"}}</div>
          </article>
        `;
      }}).join("");
    }}

    function packetKey(packet) {{
      return [packet.time, packet.direction, packet.client, packet.size, packet.preview].join("|");
    }}

    function packetFeedText(packet) {{
      const flow = escapeHtml(packet.flow || packet.client || "");
      const typeRoute = `${{escapeHtml(packet.type || "unknown")}}/${{escapeHtml(packet.route || "-")}}`;
      const decoded = packet.decoded_channel
        ? ` | ${{escapeHtml(packet.decoded_channel)}} ${{escapeHtml(packet.decoded_text || packet.decoded_status || "")}}`
        : "";
      return `${{flow}} | ${{typeRoute}} | ${{packet.size}}B${{decoded}} | ${{escapeHtml(packet.preview)}}`;
    }}

    function renderPackets(packetData) {{
      const rows = document.getElementById("packetRows");
      const packets = packetData.packets.slice(0, 50);
      if (!packets.length) {{
        rows.innerHTML = '<tr><td colspan="5" class="empty">No packets seen yet</td></tr>';
        document.getElementById("packetFeed").innerHTML = '<div class="empty">Awaiting mesh traffic</div>';
        setStatus("packetStatus", "no traffic", "warn");
        setStatus("feedStatus", "quiet", "warn");
        return;
      }}
      setStatus("packetStatus", `${{packets.length}} buffered`, "ok");
      setStatus("feedStatus", "live", "ok");
      rows.innerHTML = packets.map((packet, index) => {{
        const dirClass = packet.direction === "RX" ? "rx" : "tx";
        const source = packet.source || packet.client || "";
        const target = packet.target || "";
        const flow = target ? `${{escapeHtml(source)}} -> ${{escapeHtml(target)}}` : escapeHtml(source);
        const routeBits = [
          packet.route || "",
          packet.hops === null || packet.hops === undefined ? "" : `${{packet.hops}} hop`,
          packet.source_short_id ? `sid ${{packet.source_short_id}}` : "",
          `${{packet.size}}B`,
          packet.bridge_v2 ? `ttl ${{text(packet.ttl, "-")}}` : "",
        ].filter(Boolean).join(" | ");
        const decoded = packet.decoded_text || packet.decoded_status || "";
        const decodedLine = packet.decoded_channel
          ? `${{escapeHtml(packet.decoded_channel)}}: ${{escapeHtml(decoded)}}`
          : escapeHtml(decoded);
        return `
          <tr class="${{index < 3 ? "hot" : ""}}">
            <td class="packet-age">${{age(packet.age_seconds)}}</td>
            <td><span class="badge ${{dirClass}}">${{escapeHtml(packet.direction)}}</span></td>
            <td>
              <div class="packet-main">${{flow}}</div>
              <div class="packet-sub">${{escapeHtml(packet.client || "")}}</div>
            </td>
            <td>
              <div class="packet-main">${{escapeHtml(packet.type || "unknown")}}</div>
              <div class="packet-sub">${{escapeHtml(routeBits)}}</div>
            </td>
            <td>
              <div class="packet-main preview">${{decodedLine}}</div>
              <div class="packet-sub preview">${{escapeHtml(packet.preview)}}</div>
            </td>
          </tr>
        `;
      }}).join("");

      const feed = document.getElementById("packetFeed");
      if (state.firstPacketLoad) {{
        state.seenPacketKeys = new Set(packets.map(packetKey));
        state.firstPacketLoad = false;
      }} else {{
        for (const packet of packets.slice().reverse()) {{
          const key = packetKey(packet);
          if (state.seenPacketKeys.has(key)) continue;
          state.seenPacketKeys.add(key);
          const line = document.createElement("div");
          const dirClass = packet.direction === "RX" ? "dir-rx" : "dir-tx";
          line.className = "feed-line";
          line.innerHTML = `
            <span class="meta">${{age(packet.age_seconds)}}</span>
            <span class="${{dirClass}}">${{escapeHtml(packet.direction)}}</span>
            <span class="packet">${{packetFeedText(packet)}}</span>
          `;
          feed.prepend(line);
        }}
      }}
      if (!feed.children.length) {{
        feed.innerHTML = packets.slice(0, 24).map((packet) => `
          <div class="feed-line">
            <span class="meta">${{age(packet.age_seconds)}}</span>
            <span class="${{packet.direction === "RX" ? "dir-rx" : "dir-tx"}}">${{escapeHtml(packet.direction)}}</span>
            <span class="packet">${{packetFeedText(packet)}}</span>
          </div>
        `).join("");
      }}
      while (feed.children.length > 80) feed.removeChild(feed.lastChild);
    }}

    function renderSensors(sensorData) {{
      const rows = document.getElementById("sensorRows");
      if (!sensorData.sensors.length) {{
        rows.innerHTML = '<tr><td colspan="7" class="empty">No sensor node adverts seen yet</td></tr>';
        setStatus("sensorStatus", "no adverts", "warn");
        return;
      }}
      setStatus("sensorStatus", `${{sensorData.sensors.length}} detected`, "ok");
      rows.innerHTML = sensorData.sensors.map((sensor) => {{
        const location = sensor.lat !== null && sensor.lon !== null ? `${{Number(sensor.lat).toFixed(6)}}, ${{Number(sensor.lon).toFixed(6)}}` : "not shared";
        const nodeId = sensor.node_id || "";
        const shortNodeId = sensor.node_id_short || nodeId.slice(0, 2) || "--";
        return `
          <tr>
            <td>${{escapeHtml(sensor.name || "unknown")}}</td>
            <td title="${{escapeHtml(nodeId)}}">${{escapeHtml(shortNodeId)}}</td>
            <td>${{age(sensor.age_seconds)}} ago</td>
            <td>${{sensor.seen_count}}</td>
            <td>${{text(sensor.hops, "")}}</td>
            <td>${{escapeHtml(sensor.source)}}</td>
            <td>${{escapeHtml(location)}}</td>
          </tr>
        `;
      }}).join("");
    }}

    async function refresh() {{
      try {{
        const [status, packets, sensors] = await Promise.all([
          getJson(urls.status),
          getJson(urls.packets),
          getJson(urls.sensors)
        ]);
        renderMetrics(status, packets, sensors);
        renderNodes(status);
        renderPackets(packets);
        renderSensors(sensors);
      }} catch (error) {{
        document.getElementById("metricSync").textContent = "link error";
        setStatus("nodeStatus", "link error", "error");
        setStatus("packetStatus", "link error", "error");
        setStatus("feedStatus", "link error", "error");
        setStatus("sensorStatus", "link error", "error");
        console.error(error);
      }}
    }}

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def build_manage_html(command_result: str = "", base_path: str = "") -> str:
    snapshot = status_snapshot(include_disconnected=False)
    options = []
    for client in snapshot["clients"]:
        label = client["display_name"]
        if client.get("node_id"):
            label += f" [{client['node_id']}]"
        if client["firmware_version"]:
            label += f" ({client['firmware_version']})"
        options.append(
            f'<option value="{html.escape(client["id"], quote=True)}">{html.escape(label)}</option>'
        )

    options_html = "\n".join(options) if options else (
        '<option value="">No bridge nodes connected</option>'
    )
    path_options_html = (
        '<option value="__all__">All connected bridge nodes</option>\n' + "\n".join(options)
        if options else
        '<option value="">No bridge nodes connected</option>'
    )
    disabled = " disabled" if not options else ""
    path_block_enabled = ALLOW_PATH_BLOCK_ADMIN and bool(ADMIN_PASSWORD)
    path_disabled = "" if options and path_block_enabled else " disabled"
    admin_note = (
        "Remote management protected by server admin password; node password still required"
        if ADMIN_PASSWORD else
        "Remote management enabled; enter the selected node's admin password"
    )
    path_note = (
        "Path quarantine is enabled for bridge admins and does not require the node password"
        if path_block_enabled else
        "Path quarantine is disabled; start the server with --admin-password and --allow-path-block-admin"
    )
    node_note = (
        "Node quarantine blocks a 1-byte source id on the selected bridge node; matching packets are dropped locally"
        if path_block_enabled else
        "Node quarantine is disabled; start the server with --admin-password and --allow-path-block-admin"
    )
    result_html = (
        f'<pre class="command-result">{html.escape(redact_public_text(command_result))}</pre>'
        if command_result else
        '<pre class="command-result empty">No command sent yet</pre>'
    )

    status_url = prefixed_url(base_path, "/")
    command_url = prefixed_url(base_path, "/command")
    connected_count = snapshot.get("connected_count", len(snapshot["clients"]))
    node_count = len(snapshot["clients"])
    auth_state = "protected" if ADMIN_PASSWORD else "node password"
    quarantine_state = "enabled" if path_block_enabled else "disabled"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG Remote Management</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #070908;
      --panel: #101514;
      --panel-2: #141b19;
      --line: rgba(97, 255, 154, .22);
      --green: #68ff9d;
      --green-soft: #b6ffd0;
      --muted: #8aa596;
      --amber: #ffd166;
      --red: #ff5b5b;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: #eafff0;
      background:
        radial-gradient(circle at 15% -10%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(135deg, #070908 0%, #0d1311 48%, #050706 100%);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px 18px 32px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }}
    h1 {{ margin: 0; color: var(--green-soft); font-size: clamp(1.5rem, 3vw, 2.25rem); letter-spacing: 0; }}
    .summary {{ margin: 6px 0 0; max-width: 760px; color: var(--muted); line-height: 1.45; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
    nav a {{
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--green-soft);
      padding: 8px 11px;
      text-decoration: none;
      background: rgba(104, 255, 157, .06);
      font-weight: 750;
      font-size: .82rem;
    }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      background: rgba(16, 21, 20, .82);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .label {{ color: var(--muted); font-size: .7rem; text-transform: uppercase; }}
    .metric b {{ display: block; margin-top: 4px; color: var(--green); font-size: 1.08rem; font-variant-numeric: tabular-nums; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, .85fr); gap: 14px; align-items: start; }}
    .stack {{ display: grid; gap: 14px; }}
    .panel {{
      border: 1px solid var(--line);
      background: rgba(16, 21, 20, .86);
      border-radius: 6px;
      overflow: hidden;
      box-shadow: 0 18px 42px rgba(0, 0, 0, .24);
    }}
    .panel-header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(104, 255, 157, .06);
    }}
    h2 {{ margin: 0; color: var(--green-soft); font-size: .92rem; text-transform: uppercase; }}
    .panel-body {{ padding: 14px; }}
    .panel p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.4; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .form-wide {{ grid-column: 1 / -1; }}
    label {{ display: block; color: var(--muted); font-size: .72rem; font-weight: 750; margin: 0 0 5px; text-transform: uppercase; }}
    select, input {{
      width: 100%;
      min-width: 0;
      border: 1px solid rgba(97, 255, 154, .18);
      border-radius: 4px;
      padding: 9px 10px;
      background: rgba(0, 0, 0, .22);
      color: #eafff0;
      font: inherit;
      outline: none;
    }}
    select:focus, input:focus {{ border-color: rgba(104, 255, 157, .65); box-shadow: 0 0 0 2px rgba(104, 255, 157, .1); }}
    select:disabled, input:disabled {{ color: #65786e; border-color: rgba(138, 165, 150, .14); cursor: not-allowed; }}
    .actions {{ display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }}
    button {{
      border: 1px solid rgba(104, 255, 157, .42);
      border-radius: 4px;
      padding: 9px 12px;
      background: rgba(104, 255, 157, .12);
      color: var(--green-soft);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    button:hover:not(:disabled) {{ background: rgba(104, 255, 157, .18); }}
    button:disabled {{ color: #65786e; border-color: rgba(138, 165, 150, .16); cursor: not-allowed; }}
    .command-result {{
      margin: 0;
      min-height: 224px;
      max-height: 520px;
      overflow: auto;
      border: 1px solid rgba(97, 255, 154, .16);
      border-radius: 6px;
      background: rgba(0, 0, 0, .28);
      color: #dfffe9;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: .82rem;
      line-height: 1.38;
    }}
    .empty {{ color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(97, 255, 154, .22);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--muted);
      font-size: .72rem;
      white-space: nowrap;
    }}
    @media (max-width: 920px) {{
      main {{ padding: 18px 12px 26px; }}
      .topbar {{ display: block; }}
      nav {{ justify-content: flex-start; margin-top: 12px; }}
      .status-strip {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .form-grid {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: stretch; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>Remote Management</h1>
        <p class="summary">{html.escape(admin_note)}.</p>
      </div>
      <nav>
        <a href="{status_url}">Bridge status</a>
      </nav>
    </header>
    <section class="status-strip">
      <div class="metric"><span class="label">Online nodes</span><b>{connected_count}</b></div>
      <div class="metric"><span class="label">Command auth</span><b>{html.escape(auth_state)}</b></div>
      <div class="metric"><span class="label">Bridge quarantine</span><b>{html.escape(quarantine_state)}</b></div>
    </section>
    <section class="grid">
      <div class="stack">
        <div class="panel">
          <div class="panel-header">
            <h2>Remote CLI</h2>
            <span class="pill">{node_count} node{'' if node_count == 1 else 's'}</span>
          </div>
          <div class="panel-body">
            <form method="post" action="{command_url}">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="target">Bridge node</label>
                  <select id="target" name="target"{disabled}>
                    {options_html}
                  </select>
                </div>
                <div>
                  <label for="node_password">Node password</label>
                  <input id="node_password" name="node_password" type="password" autocomplete="current-password" maxlength="32"{disabled}>
                </div>
                <div>
                  <label for="command">Command</label>
                  <input id="command" name="command" placeholder="get bridge.status" maxlength="96"{disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{disabled}>Send command</button></div>
            </form>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Path Quarantine</h2>
            <span class="pill">{html.escape(quarantine_state)}</span>
          </div>
          <div class="panel-body">
            <p>{html.escape(path_note)}</p>
            <form method="post" action="{command_url}">
              <input type="hidden" name="mode" value="path_block">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="path_target">Bridge node</label>
                  <select id="path_target" name="target"{path_disabled}>
                    {path_options_html}
                  </select>
                </div>
                <div>
                  <label for="path_action">Action</label>
                  <select id="path_action" name="path_action"{path_disabled}>
                    <option value="add">Add block</option>
                    <option value="del">Remove block</option>
                    <option value="get">Show blocks</option>
                    <option value="clear">Clear all</option>
                  </select>
                </div>
                <div>
                  <label for="path_duration">Duration</label>
                  <input id="path_duration" name="path_duration" placeholder="1h" maxlength="8"{path_disabled}>
                </div>
                <div class="form-wide">
                  <label for="path_block">Path</label>
                  <input id="path_block" name="path_block" placeholder="aa/bb/cc" maxlength="20"{path_disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{path_disabled}>Apply quarantine</button></div>
            </form>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Node-ID Quarantine</h2>
            <span class="pill">{html.escape(quarantine_state)}</span>
          </div>
          <div class="panel-body">
            <p>{html.escape(node_note)}</p>
            <form method="post" action="{command_url}">
              <input type="hidden" name="mode" value="node_block">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="node_target">Bridge node</label>
                  <select id="node_target" name="target"{path_disabled}>
                    {path_options_html}
                  </select>
                </div>
                <div>
                  <label for="node_action">Action</label>
                  <select id="node_action" name="node_action"{path_disabled}>
                    <option value="add">Add block</option>
                    <option value="del">Remove block</option>
                    <option value="get">Show blocks</option>
                    <option value="clear">Clear all</option>
                  </select>
                </div>
                <div>
                  <label for="node_duration">Duration</label>
                  <input id="node_duration" name="node_duration" placeholder="15m" maxlength="8"{path_disabled}>
                </div>
                <div class="form-wide">
                  <label for="node_block">1-byte source node id</label>
                  <input id="node_block" name="node_block" placeholder="a7" maxlength="2"{path_disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{path_disabled}>Apply quarantine</button></div>
            </form>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>Command Result</h2>
          <span class="pill">console</span>
        </div>
        <div class="panel-body">
          {result_html}
        </div>
      </div>
    </section>
  </main>
</body>
</html>
"""


def build_location_map_html(base_path: str = "") -> str:
    status_url = prefixed_url(base_path, "/")
    locations_url = prefixed_url(base_path, "/locations.json")
    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG Tracker Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {
      color-scheme: dark;
      --bg: #050806;
      --panel: rgba(8, 18, 12, .88);
      --line: rgba(97, 255, 154, .32);
      --line-strong: rgba(97, 255, 154, .58);
      --green: #68ff9d;
      --green-soft: #a1ffc4;
      --amber: #ffd166;
      --red: #ff5f6d;
      --muted: #8fb99e;
      --text: #dfffe9;
      --shadow: 0 18px 60px rgba(0, 0, 0, .48);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    * { box-sizing: border-box; }
    html, body, #map { height: 100%; margin: 0; }
    html { background: var(--bg); }
    body {
      color: var(--text);
      background:
        radial-gradient(circle at 18% 12%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(180deg, rgba(2, 10, 6, .7), rgba(2, 5, 3, .98)),
        var(--bg);
      overflow: hidden;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(104, 255, 157, .04) 50%, rgba(0, 0, 0, .13) 50%),
        linear-gradient(90deg, rgba(255, 0, 0, .025), rgba(0, 255, 95, .018), rgba(0, 120, 255, .025));
      background-size: 100% 4px, 7px 100%;
      mix-blend-mode: screen;
      opacity: .42;
      z-index: 1200;
    }
    #map {
      background: #071009;
    }
    #map.layout-tactical .leaflet-tile,
    #map.layout-night .leaflet-tile {
      filter: invert(1) hue-rotate(95deg) saturate(.75) brightness(.58) contrast(1.25);
    }
    #map.layout-standard .leaflet-tile,
    #map.layout-humanitarian .leaflet-tile,
    #map.layout-topo .leaflet-tile {
      filter: none;
    }
    .leaflet-control-attribution,
    .leaflet-control-zoom a,
    .leaflet-control-layers {
      background: rgba(8, 18, 12, .92) !important;
      border-color: var(--line) !important;
      color: var(--green-soft) !important;
      font-family: inherit;
    }
    .leaflet-control-layers-expanded {
      padding: 10px 12px;
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .leaflet-control-layers label {
      color: var(--text);
      font-size: 12px;
      line-height: 1.9;
    }
    .leaflet-control-layers-selector {
      accent-color: var(--green);
    }
    .leaflet-control-zoom {
      border: 1px solid var(--line) !important;
      box-shadow: var(--shadow);
    }
    .leaflet-bottom {
      bottom: 74px;
    }
    .leaflet-popup-content-wrapper,
    .leaflet-popup-tip {
      background: rgba(5, 12, 8, .96);
      color: var(--text);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .leaflet-popup-content {
      font-family: inherit;
      font-size: 12px;
      line-height: 1.55;
    }
    .leaflet-popup-content strong {
      color: var(--green);
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .leaflet-container a.leaflet-popup-close-button {
      color: var(--green-soft);
    }
    .topbar {
      position: absolute;
      z-index: 1000;
      top: 14px;
      left: 14px;
      right: 14px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 14px;
      align-items: center;
      background: linear-gradient(180deg, rgba(11, 26, 17, .94), rgba(5, 12, 8, .88));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .topbar::before {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      border-radius: 8px;
      background: linear-gradient(90deg, rgba(104,255,157,.14), transparent 18%, transparent 82%, rgba(104,255,157,.12));
    }
    .topbar h1 {
      margin: 0;
      min-width: 0;
      color: var(--green);
      font-size: clamp(.9rem, 2.4vw, 1.08rem);
      font-weight: 780;
      letter-spacing: .08em;
      text-transform: uppercase;
      text-shadow: 0 0 18px rgba(104,255,157,.42);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .topbar a {
      color: var(--green-soft);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      text-decoration: none;
      text-transform: uppercase;
      font-size: .78rem;
      font-weight: 760;
      background: rgba(104, 255, 157, .08);
    }
    .topbar a:hover { border-color: var(--line-strong); color: var(--green); }
    .muted {
      color: var(--muted);
      font-size: .84rem;
      white-space: nowrap;
    }
    .tracker-icon {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: radial-gradient(circle, var(--marker-core, var(--green-soft)), var(--marker-fill, var(--green)) 55%, var(--marker-edge, #0c4f2a) 100%);
      border: 2px solid var(--marker-border, rgba(223,255,233,.9));
      box-shadow:
        0 0 0 3px var(--marker-ring, rgba(104,255,157,.18)),
        0 0 22px var(--marker-glow, rgba(104,255,157,.54)),
        0 3px 9px rgba(0,0,0,.5);
      position: relative;
    }
    .tracker-icon::before {
      content: "";
      position: absolute;
      left: 50%;
      top: -11px;
      transform: translateX(-50%);
      border-left: 7px solid transparent;
      border-right: 7px solid transparent;
      border-bottom: 14px solid var(--marker-fill, var(--green));
      filter: drop-shadow(0 -1px 5px var(--marker-glow, rgba(104,255,157,.55)));
    }
    .tracker-icon.stationary::before { display: none; }
    .tracker-label {
      margin-left: 32px;
      margin-top: -28px;
      padding: 3px 6px;
      border-radius: 4px;
      background: var(--marker-label-bg, rgba(5,12,8,.9));
      border: 1px solid var(--marker-label-border, var(--line));
      color: var(--marker-label-color, var(--green-soft));
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
      box-shadow: 0 0 12px rgba(104,255,157,.16), 0 1px 6px rgba(0,0,0,.42);
    }
    .replaybar {
      position: absolute;
      z-index: 1000;
      left: 14px;
      right: 14px;
      bottom: 14px;
      display: grid;
      grid-template-columns: auto auto minmax(160px, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      background: linear-gradient(180deg, rgba(11, 26, 17, .94), rgba(5, 12, 8, .88));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .replaybar button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(104, 255, 157, .08);
      color: var(--green-soft);
      cursor: pointer;
      font: inherit;
      font-size: .78rem;
      font-weight: 760;
      padding: 7px 10px;
      text-transform: uppercase;
    }
    .replaybar button:hover { border-color: var(--line-strong); color: var(--green); }
    .replaybar input[type="range"] {
      width: 100%;
      accent-color: var(--green);
    }
    .replaybar .time {
      color: var(--green-soft);
      font-size: .84rem;
      white-space: nowrap;
    }
    .replaybar .hint {
      color: var(--muted);
      font-size: .76rem;
      text-transform: uppercase;
      white-space: nowrap;
    }
    @media (max-width: 720px) {
      .topbar {
        grid-template-columns: 1fr;
        align-items: start;
        gap: 8px;
      }
      .topbar h1 { white-space: normal; }
      .muted { white-space: normal; }
      .topbar a { width: max-content; }
      .replaybar {
        grid-template-columns: 1fr;
        bottom: 10px;
      }
      .leaflet-bottom { bottom: 150px; }
      .replaybar .hint { white-space: normal; }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>MeshCoreNG Tracker Tactical Map</h1>
    <span class="muted" id="summary">Loading...</span>
    <a href="__STATUS_URL__">Bridge status</a>
  </div>
  <div id="map"></div>
  <div class="replaybar">
    <button id="replayToggle" type="button">Replay 24h</button>
    <button id="replayPlay" type="button">Play</button>
    <input id="replaySlider" type="range" min="0" max="1440" value="1440" step="1">
    <span class="time" id="replayTime">live</span>
    <span class="hint" id="replayHint">Live tracking</span>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const mapEl = document.getElementById('map');
    const savedLayout = localStorage.getItem('meshcore_tracker_map_layout') || 'Tactical';
    const map = L.map('map', { zoomControl: false }).setView([52.2, 5.3], 8);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    const osmAttrib = '&copy; OpenStreetMap contributors';
    const topoAttrib = '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap';
    const baseLayers = {
      Tactical: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Night: L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Standard: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Humanitarian: L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Topo: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
        maxZoom: 17,
        attribution: topoAttrib
      })
    };
    const layoutClasses = {
      Tactical: 'layout-tactical',
      Night: 'layout-night',
      Standard: 'layout-standard',
      Humanitarian: 'layout-humanitarian',
      Topo: 'layout-topo'
    };
    const routeStyles = {
      Tactical: { color: '#68ff9d', weight: 4, opacity: 0.82 },
      Night: { color: '#ffd166', weight: 4, opacity: 0.88 },
      Standard: { color: '#d0006f', weight: 5, opacity: 0.92 },
      Humanitarian: { color: '#0057ff', weight: 5, opacity: 0.9 },
      Topo: { color: '#d0006f', weight: 5, opacity: 0.92 }
    };
    const markerStyles = {
      Tactical: {
        core: '#dfffe9', fill: '#68ff9d', edge: '#0c4f2a', border: '#f4fff8',
        ring: 'rgba(104,255,157,.2)', glow: 'rgba(104,255,157,.58)',
        labelBg: 'rgba(5,12,8,.92)', labelBorder: 'rgba(97,255,154,.46)', labelColor: '#a1ffc4'
      },
      Night: {
        core: '#fff3c2', fill: '#ffd166', edge: '#6f4c00', border: '#fff8dc',
        ring: 'rgba(255,209,102,.22)', glow: 'rgba(255,209,102,.62)',
        labelBg: 'rgba(18,12,4,.94)', labelBorder: 'rgba(255,209,102,.5)', labelColor: '#ffe59a'
      },
      Standard: {
        core: '#ffffff', fill: '#d0006f', edge: '#3b001f', border: '#ffffff',
        ring: 'rgba(208,0,111,.24)', glow: 'rgba(208,0,111,.58)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#d0006f', labelColor: '#3b001f'
      },
      Humanitarian: {
        core: '#ffffff', fill: '#0057ff', edge: '#001d54', border: '#ffffff',
        ring: 'rgba(0,87,255,.24)', glow: 'rgba(0,87,255,.55)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#0057ff', labelColor: '#001d54'
      },
      Topo: {
        core: '#ffffff', fill: '#d0006f', edge: '#3b001f', border: '#ffffff',
        ring: 'rgba(208,0,111,.24)', glow: 'rgba(208,0,111,.58)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#d0006f', labelColor: '#3b001f'
      }
    };
    let currentLayout = 'Tactical';

    function routeStyle() {
      return {
        ...(routeStyles[currentLayout] || routeStyles.Tactical),
        lineJoin: 'round'
      };
    }

    function markerStyleVars() {
      const style = markerStyles[currentLayout] || markerStyles.Tactical;
      return [
        `--marker-core:${style.core}`,
        `--marker-fill:${style.fill}`,
        `--marker-edge:${style.edge}`,
        `--marker-border:${style.border}`,
        `--marker-ring:${style.ring}`,
        `--marker-glow:${style.glow}`,
        `--marker-label-bg:${style.labelBg}`,
        `--marker-label-border:${style.labelBorder}`,
        `--marker-label-color:${style.labelColor}`
      ].join(';');
    }

    function setMapLayout(name) {
      currentLayout = baseLayers[name] ? name : 'Tactical';
      for (const cls of Object.values(layoutClasses)) mapEl.classList.remove(cls);
      mapEl.classList.add(layoutClasses[currentLayout] || layoutClasses.Tactical);
      localStorage.setItem('meshcore_tracker_map_layout', currentLayout);
      for (const track of tracks.values()) {
        track.eachLayer((layer) => layer.setStyle(routeStyle()));
      }
      for (const [nodeId, marker] of markers) {
        const loc = latestLocationByNode.get(nodeId);
        if (loc) marker.setIcon(trackerIcon(loc));
      }
    }

    const markers = new Map();
    const tracks = new Map();
    const latestLocationByNode = new Map();
    let latestData = null;
    let replayMode = false;
    let replayTimer = null;
    let replayStart = 0;
    let replayEnd = 0;
    let replayCursor = 0;
    const replayToggle = document.getElementById('replayToggle');
    const replayPlay = document.getElementById('replayPlay');
    const replaySlider = document.getElementById('replaySlider');
    const replayTimeLabel = document.getElementById('replayTime');
    const replayHint = document.getElementById('replayHint');
    const initialLayout = baseLayers[savedLayout] ? savedLayout : 'Tactical';
    setMapLayout(initialLayout);
    baseLayers[initialLayout].addTo(map);
    L.control.layers(baseLayers, null, { position: 'bottomleft', collapsed: false }).addTo(map);
    map.on('baselayerchange', (event) => setMapLayout(event.name));

    function fmtAge(seconds) {
      if (seconds < 60) return `${seconds}s`;
      if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
      return `${Math.floor(seconds / 3600)}h`;
    }

    function fmtReplayTime(epochSeconds) {
      if (!epochSeconds) return 'live';
      return new Date(epochSeconds * 1000).toLocaleString();
    }

    function pointTime(point) {
      const value = Number(point.timestamp || point.received_at || 0);
      return Number.isFinite(value) ? value : 0;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function fmtNumber(value, decimals = 1) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(decimals) : '';
    }

    function fmtSpeed(value) {
      const speed = fmtNumber(value, 1);
      return speed ? `${speed} km/h` : 'unknown';
    }

    function fmtHeading(value) {
      const heading = fmtNumber(value, 0);
      return heading ? `${heading}&deg;` : 'unknown';
    }

    function trackerIcon(loc) {
      const heading = Number(loc.heading_deg);
      const speed = Number(loc.speed_kmh);
      const moving = Number.isFinite(speed) && speed >= 1;
      const rotation = Number.isFinite(heading) ? heading : 0;
      const labelSpeed = Number.isFinite(speed) ? `${speed.toFixed(0)} km/h` : '';
      return L.divIcon({
        className: '',
        iconSize: [110, 34],
        iconAnchor: [14, 14],
        popupAnchor: [0, -16],
        html: `<div style="${markerStyleVars()}">` +
          `<div class="tracker-icon ${moving ? '' : 'stationary'}" style="transform: rotate(${rotation}deg)"></div>` +
          `<div class="tracker-label">${escapeHtml(labelSpeed || '0 km/h')} ${Number.isFinite(heading) ? Math.round(heading) + '&deg;' : ''}</div>` +
          `</div>`
      });
    }

    function trackSegments(loc) {
      const points = Array.isArray(loc.track) ? loc.track : [];
      const segments = [];
      let segment = [];
      for (const point of points) {
        const lat = Number(point.lat);
        const lon = Number(point.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        segment.push([lat, lon]);
        if (point.route_break_after) {
          if (segment.length) segments.push(segment);
          segment = [];
        }
      }
      if (segment.length) segments.push(segment);
      return segments;
    }

    function trackLatLngs(loc) {
      return trackSegments(loc).flat();
    }

    function routeDistanceKm(segments) {
      let km = 0;
      for (const latlngs of segments) {
        for (let i = 1; i < latlngs.length; i++) {
          km += map.distance(latlngs[i - 1], latlngs[i]) / 1000;
        }
      }
      return km;
    }

    function fitRenderedLocations(locations) {
      if (!locations.length || refresh.didFit) return;
      const bounds = locations.flatMap(loc => {
        const latlngs = trackLatLngs(loc);
        return latlngs.length ? latlngs : [[loc.lat, loc.lon]];
      });
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 13 });
      refresh.didFit = true;
    }

    function renderLocations(locations, labelPrefix = '') {
      document.getElementById('summary').textContent = `${labelPrefix}${locations.length} tracker node(s)`;
      const seen = new Set();
      for (const loc of locations) {
        seen.add(loc.node_id);
        latestLocationByNode.set(loc.node_id, loc);
        const label = loc.name || loc.node_id;
        const segments = trackSegments(loc);
        const latlngs = segments.flat();
        const routeKm = routeDistanceKm(segments);
        const popup = `<strong>${escapeHtml(label)}</strong><br>` +
          `Node: ${escapeHtml(loc.node_id)}<br>` +
          `Age: ${fmtAge(loc.age_seconds)}<br>` +
          `Speed: ${fmtSpeed(loc.speed_kmh)}<br>` +
          `Heading: ${fmtHeading(loc.heading_deg)}<br>` +
          `Track: ${latlngs.length} point(s), ${routeKm.toFixed(2)} km<br>` +
          `Sats: ${loc.satellites}<br>` +
          `Battery: ${loc.battery_mv} mV<br>` +
          `Alt: ${loc.altitude_m} m`;
        let track = tracks.get(loc.node_id);
        const drawableSegments = segments.filter(segment => segment.length >= 2);
        if (drawableSegments.length) {
          if (track) {
            track.remove();
          }
          track = L.layerGroup(
            drawableSegments.map(segment => L.polyline(segment, routeStyle()))
          ).addTo(map);
          tracks.set(loc.node_id, track);
        } else if (track) {
          track.remove();
          tracks.delete(loc.node_id);
        }
        let marker = markers.get(loc.node_id);
        if (!marker) {
          marker = L.marker([loc.lat, loc.lon], { icon: trackerIcon(loc) }).addTo(map);
          markers.set(loc.node_id, marker);
        } else {
          marker.setLatLng([loc.lat, loc.lon]);
          marker.setIcon(trackerIcon(loc));
        }
        marker.bindPopup(popup);
      }
      for (const [nodeId, marker] of markers) {
        if (!seen.has(nodeId)) {
          marker.remove();
          markers.delete(nodeId);
          latestLocationByNode.delete(nodeId);
        }
      }
      for (const [nodeId, track] of tracks) {
        if (!seen.has(nodeId)) {
          track.remove();
          tracks.delete(nodeId);
        }
      }
      fitRenderedLocations(locations);
    }

    function updateReplayWindow(data) {
      replayEnd = Number(data.generated_at || Math.floor(Date.now() / 1000));
      replayStart = replayEnd - 24 * 60 * 60;
      if (!replayMode) {
        replayCursor = replayEnd;
        replaySlider.value = replaySlider.max;
        replayTimeLabel.textContent = 'live';
        replayHint.textContent = 'Live tracking';
      }
    }

    function replayLocationsAt(data, cursor) {
      const locations = [];
      for (const loc of data.locations || []) {
        const points = (Array.isArray(loc.track) ? loc.track : [])
          .filter(point => {
            const t = pointTime(point);
            return t >= replayStart && t <= cursor;
          });
        if (!points.length) continue;
        const last = points[points.length - 1];
        locations.push({
          ...loc,
          ...last,
          age_seconds: Math.max(0, replayEnd - pointTime(last)),
          track: points
        });
      }
      return locations;
    }

    function renderReplay() {
      if (!latestData) return;
      const minutes = Number(replaySlider.value);
      replayCursor = replayStart + minutes * 60;
      const locations = replayLocationsAt(latestData, replayCursor);
      renderLocations(locations, 'Replay: ');
      replayTimeLabel.textContent = fmtReplayTime(replayCursor);
      replayHint.textContent = `Last 24h replay | ${locations.length} active`;
    }

    function stopReplay() {
      replayMode = false;
      if (replayTimer) {
        clearInterval(replayTimer);
        replayTimer = null;
      }
      replayToggle.textContent = 'Replay 24h';
      replayPlay.textContent = 'Play';
      replaySlider.value = replaySlider.max;
      replayTimeLabel.textContent = 'live';
      replayHint.textContent = 'Live tracking';
      if (latestData) renderLocations(latestData.locations || []);
    }

    function pauseReplayPlayback() {
      if (replayTimer) clearInterval(replayTimer);
      replayTimer = null;
      replayPlay.textContent = 'Play';
    }

    function playReplayFromCurrent() {
      if (!latestData) return;
      replayMode = true;
      replayToggle.textContent = 'Live';
      pauseReplayPlayback();
      replayPlay.textContent = 'Pause';
      replayTimer = setInterval(() => {
        let next = Number(replaySlider.value) + 5;
        if (next > Number(replaySlider.max)) next = 0;
        replaySlider.value = next;
        renderReplay();
      }, 700);
    }

    function startReplay() {
      if (!latestData) return;
      replayMode = true;
      replayToggle.textContent = 'Live';
      replaySlider.value = 0;
      renderReplay();
      playReplayFromCurrent();
    }

    async function refresh() {
      const res = await fetch('__LOCATIONS_URL__', { cache: 'no-store' });
      const data = await res.json();
      latestData = data;
      updateReplayWindow(data);
      if (replayMode) renderReplay();
      else renderLocations(data.locations || []);
    }

    replayToggle.addEventListener('click', () => {
      if (replayMode) stopReplay();
      else startReplay();
    });
    replayPlay.addEventListener('click', () => {
      if (!replayMode) {
        replayMode = true;
        replayToggle.textContent = 'Live';
        renderReplay();
      }
      if (replayTimer) pauseReplayPlayback();
      else playReplayFromCurrent();
    });
    replaySlider.addEventListener('input', () => {
      if (!replayMode) {
        replayMode = true;
        replayToggle.textContent = 'Live';
      }
      pauseReplayPlayback();
      renderReplay();
    });

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""
    return page.replace("__STATUS_URL__", status_url).replace("__LOCATIONS_URL__", locations_url)


def is_admin_authorized(headers: dict[str, str]) -> bool:
    if not ADMIN_PASSWORD:
        return True
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip()).decode("utf-8", errors="replace")
    except Exception:
        return False
    _user, sep, password = decoded.partition(":")
    return bool(sep) and password == ADMIN_PASSWORD


def admin_auth_response() -> tuple[str, str, bytes, list[tuple[str, str]]]:
    return (
        "401 Unauthorized",
        "text/plain",
        b"Admin authentication required\n",
        [("WWW-Authenticate", 'Basic realm="MeshCoreNG Bridge Admin"')],
    )


def find_client(client_id: str) -> BridgeClient | None:
    for client in connected_clients:
        if client.client_id == client_id:
            return client
    return None


PATH_BLOCK_RE = re.compile(r"^[0-9a-fA-F]{2}(?:/[0-9a-fA-F]{2}){0,2}$|^[0-9a-fA-F]{4}(?:/[0-9a-fA-F]{4}){0,2}$|^[0-9a-fA-F]{6}(?:/[0-9a-fA-F]{6}){0,2}$")
PATH_BLOCK_DURATION_RE = re.compile(r"^[1-9][0-9]*(?:[mhd])?$")
NODE_BLOCK_RE = re.compile(r"^[0-9a-fA-F]{2}$")


def build_path_block_command(form: dict[str, list[str]]) -> str:
    action = (form.get("path_action") or [""])[0].strip().lower()
    path = (form.get("path_block") or [""])[0].strip()
    duration = (form.get("path_duration") or [""])[0].strip().lower()

    if action == "get":
        return "get path.block"
    if action == "clear":
        return "clear path.block"
    if action not in ("add", "del"):
        raise ValueError("invalid path.block action")
    if not PATH_BLOCK_RE.fullmatch(path):
        raise ValueError("path must be aa, aa/bb, aa/bb/cc, or same-width 2/3-byte hops")
    if action == "del":
        return f"set path.block del {path}"
    if duration and not PATH_BLOCK_DURATION_RE.fullmatch(duration):
        raise ValueError("duration must be seconds, Nm, Nh, or Nd")
    return f"set path.block add {path}" + (f" {duration}" if duration else "")


def build_node_block_command(form: dict[str, list[str]]) -> str:
    action = (form.get("node_action") or [""])[0].strip().lower()
    node_id = (form.get("node_block") or [""])[0].strip().lower()
    duration = (form.get("node_duration") or [""])[0].strip().lower()

    if action == "get":
        return "get node.block"
    if action == "clear":
        return "clear node.block"
    if action not in ("add", "del"):
        raise ValueError("invalid node.block action")
    if not NODE_BLOCK_RE.fullmatch(node_id):
        raise ValueError("node id must be one hex byte, e.g. a7")
    if action == "del":
        return f"set node.block del {node_id}"
    if duration and not PATH_BLOCK_DURATION_RE.fullmatch(duration):
        raise ValueError("duration must be seconds, Nm, Nh, or Nd")
    return f"set node.block add {node_id}" + (f" {duration}" if duration else "")


async def send_admin_bridge_command(target: str, command: str, label: str) -> str:
    async def send_one(client: BridgeClient) -> str:
        try:
            reply = await client.send_command(command, "")
            asyncio.create_task(refresh_client_block_stats(client, force=True))
            log.info("%s: bridge-admin %s command: %s", client.addr, label, command)
            return f"{client.display_name}> {command}\n{reply}"
        except asyncio.TimeoutError:
            log.warning("%s: %s command timed out", client.addr, label)
            return f"{client.display_name}> {command}\nError: command timed out waiting for bridge reply"
        except Exception as exc:
            return f"{client.display_name}> {command}\nError: {exc}"

    if target == "__all__":
        targets = sorted(list(connected_clients), key=lambda c: (c.display_name.lower(), c.client_id))
        if not targets:
            return "Error: no bridge nodes connected"
        return "\n\n".join(await asyncio.gather(*(send_one(c) for c in targets)))
    client = find_client(target)
    if client is None:
        return "Error: selected bridge node is no longer connected"
    return await send_one(client)


def make_http_response(
    status: str,
    content_type: str,
    body: bytes,
    extra_headers: list[tuple[str, str]] | None = None,
) -> HttpResponse:
    return status, content_type, body, extra_headers or []


def html_response(page: str) -> HttpResponse:
    return make_http_response("200 OK", "text/html; charset=utf-8", page.encode("utf-8"))


def json_response(payload: dict) -> HttpResponse:
    return make_http_response("200 OK", "application/json", json.dumps(payload, indent=2).encode("utf-8"))


def text_response(status: str, text: str) -> HttpResponse:
    return make_http_response(status, "text/plain", text.encode("utf-8"))


def parse_content_length(headers: dict[str, str]) -> int:
    try:
        return max(0, int(headers.get("content-length", "0") or "0"))
    except ValueError:
        return 0


async def read_http_request(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
    request_line = await reader.readline()
    parts = request_line.decode("ascii", errors="ignore").strip().split()
    method = parts[0] if len(parts) >= 1 else ""
    path = parts[1] if len(parts) >= 2 else ""
    headers: dict[str, str] = {}

    while True:
        line = await reader.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        name, sep, value = line.decode("iso-8859-1", errors="ignore").partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()

    return method, path, headers


async def handle_command_post(
    reader: asyncio.StreamReader,
    headers: dict[str, str],
    base_path: str,
) -> HttpResponse:
    if not is_admin_authorized(headers):
        return admin_auth_response()

    content_length = parse_content_length(headers)
    raw_body = await reader.readexactly(content_length) if content_length > 0 else b""
    form = parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)
    target = (form.get("target") or [""])[0]
    node_password = (form.get("node_password") or [""])[0]
    command = (form.get("command") or [""])[0].strip()
    mode = (form.get("mode") or [""])[0].strip()

    if mode in ("path_block", "node_block"):
        result = await handle_quarantine_post(form, target, mode)
    else:
        result = await handle_remote_cli_post(target, node_password, command)

    return html_response(build_manage_html(result, base_path))


async def handle_quarantine_post(form: dict[str, list[str]], target: str, mode: str) -> str:
    if not (ALLOW_PATH_BLOCK_ADMIN and ADMIN_PASSWORD):
        return "Error: bridge quarantine admin is disabled"

    client = find_client(target)
    try:
        if mode == "path_block":
            command = build_path_block_command(form)
            label = "path quarantine"
        else:
            command = build_node_block_command(form)
            label = "node quarantine"
        return await send_admin_bridge_command(target, command, label)
    except asyncio.TimeoutError:
        log.warning("%s: bridge quarantine command timed out", client.addr if client else target)
        return "Error: command timed out waiting for bridge reply"
    except Exception as exc:
        return f"Error: {exc}"


async def handle_remote_cli_post(target: str, node_password: str, command: str) -> str:
    client = find_client(target)
    if client is None:
        return "Error: selected bridge node is no longer connected"
    if not command:
        return "Error: empty command"
    if not node_password:
        return "Error: node admin password required"

    try:
        timeout = command_timeout_for(command)
        if is_ota_update_command(command):
            await client.send_command(command, node_password, wait_reply=False)
            return (
                f"{client.display_name}> {command}\n"
                "OK - OTA update command sent. The node may disconnect, reconnect WiFi, "
                "download firmware, reboot, and return online without sending a CLI reply."
            )
        reply = await client.send_command(command, node_password, timeout=timeout)
        return f"{client.display_name}> {command}\n{reply}"
    except asyncio.TimeoutError:
        log.warning("%s: remote command timed out: %s", client.addr, command)
        return f"Error: command timed out waiting for bridge reply after {command_timeout_for(command)}s"
    except Exception as exc:
        return f"Error: {exc}"


def route_get_request(
    route: str,
    headers: dict[str, str],
    base_path: str,
) -> HttpResponse:
    if route == "/status.json":
        return json_response(status_snapshot())
    if route == "/locations.json":
        return json_response(locations_snapshot())
    if route == "/sensors.json":
        return json_response(sensors_snapshot())
    if route == "/packets.json":
        return json_response(packets_snapshot())
    if route == "/map":
        return html_response(build_location_map_html(base_path))
    if route == "/manage":
        if not is_admin_authorized(headers):
            return admin_auth_response()
        return html_response(build_manage_html(base_path=base_path))
    if route in ("/", "/status"):
        return html_response(build_status_html(base_path))
    return text_response("404 Not Found", "Not found\n")


def build_http_headers(
    status: str,
    content_type: str,
    body: bytes,
    extra_headers: list[tuple[str, str]],
) -> bytes:
    header_lines = [
        f"HTTP/1.1 {status}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Connection: close",
        "Cache-Control: no-store",
    ]
    header_lines.extend(f"{name}: {value}" for name, value in extra_headers)
    return ("\r\n".join(header_lines) + "\r\n\r\n").encode("ascii")


async def handle_http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        method, path, request_headers = await read_http_request(reader)
        base_path = request_base_path(request_headers)
        route = strip_base_path(path, base_path)

        if not method or not path:
            response = text_response("405 Method Not Allowed", "Method not allowed\n")
        elif method == "POST" and route == "/command":
            response = await handle_command_post(reader, request_headers, base_path)
        elif method != "GET":
            response = text_response("405 Method Not Allowed", "Method not allowed\n")
        else:
            response = route_get_request(route, request_headers, base_path)

        status, content_type, body, extra_headers = response
        writer.write(build_http_headers(status, content_type, body, extra_headers) + body)
        await writer.drain()
    except Exception as exc:
        log.debug("HTTP status request failed: %s", exc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main(host: str, port: int, status_host: str, status_port: int) -> None:
    log.info("%s v%s starting", SERVER_NAME, SERVER_VERSION)
    server = await asyncio.start_server(handle_client, host, port)
    addrs = format_sockaddrs(server.sockets)
    log.info("MeshCore TCP bridge server listening on %s", addrs)
    http_server = None
    if status_port > 0:
        try:
            http_server = await asyncio.start_server(handle_http_client, status_host, status_port)
            http_addrs = format_sockaddrs(http_server.sockets)
            log.info("Status page listening on http://%s", http_addrs)
        except OSError as exc:
            log.warning(
                "Status page disabled: could not bind %s:%d (%s)",
                status_host,
                status_port,
                exc,
            )
    log.info("Press Ctrl+C to stop")
    status = asyncio.create_task(status_task()) if STATUS_INTERVAL_SECS > 0 else None
    firmware_updates = asyncio.create_task(
        firmware_update_task(
            FIRMWARE_UPDATE_REPO,
            FIRMWARE_UPDATE_CHECK_INTERVAL_SECS,
            FIRMWARE_UPDATE_TIMEOUT_SECS,
        )
    )

    try:
        if http_server:
            async with server, http_server:
                await server.serve_forever()
        else:
            async with server:
                await server.serve_forever()
    finally:
        if http_server:
            http_server.close()
            await http_server.wait_closed()
        if status:
            status.cancel()
        firmware_updates.cancel()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MeshCore TCP bridge server")

    add_network_args(parser)
    add_bridge_guard_args(parser)
    add_status_page_args(parser)
    add_firmware_update_args(parser)
    add_packet_decode_args(parser)
    add_rate_limit_args(parser)
    add_admin_args(parser)
    return parser


def add_network_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", action="version", version=f"%(prog)s {SERVER_VERSION}")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=4200, help="TCP port (default: 4200)")
    parser.add_argument(
        "--client-timeout",
        type=int,
        default=180,
        help="Disconnect clients after this many seconds without packets or heartbeat (default: 180, 0 disables)",
    )
    parser.add_argument(
        "--client-tx-queue-max",
        type=int,
        default=CLIENT_TX_QUEUE_MAX,
        help="Max queued outbound TCP packets per bridge client (default: 250)",
    )
    parser.add_argument(
        "--replace-same-ip",
        action="store_true",
        help="When a new client connects, disconnect older clients from the same IP",
    )
    parser.add_argument("--password", default="", help="Optional TCP bridge password required from clients")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")


def add_bridge_guard_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bridge-dedupe",
        choices=("on", "off"),
        default="on",
        help="Enable TCP bridge packet fingerprint dedupe (default: on)",
    )
    parser.add_argument(
        "--bridge-dedupe-ttl",
        type=int,
        default=BRIDGE_DEDUPE_TTL_SECS,
        help="Fingerprint dedupe TTL in seconds (default: 300)",
    )
    parser.add_argument(
        "--bridge-dedupe-max-entries",
        type=int,
        default=BRIDGE_DEDUPE_MAX_ENTRIES,
        help="Max fingerprint dedupe entries (default: 4096)",
    )
    parser.add_argument(
        "--bridge-loopguard",
        choices=("on", "off"),
        default="on",
        help="Enable bridge loop quarantine guard (default: on)",
    )
    parser.add_argument(
        "--bridge-loopguard-window",
        type=int,
        default=BRIDGE_LOOPGUARD_WINDOW_SECS,
        help="Loopguard window in seconds (default: 60)",
    )
    parser.add_argument(
        "--bridge-loopguard-threshold",
        type=int,
        default=BRIDGE_LOOPGUARD_THRESHOLD,
        help="Loopguard hits before quarantine (default: 5)",
    )
    parser.add_argument(
        "--bridge-loopguard-quarantine",
        type=int,
        default=BRIDGE_LOOPGUARD_QUARANTINE_SECS,
        help="Loopguard quarantine seconds (default: 120)",
    )
    parser.add_argument(
        "--bridge-rf-inject",
        choices=("on", "off"),
        default="off",
        help="Enable server-side RF inject budget before forwarding to bridges (default: off)",
    )
    parser.add_argument(
        "--bridge-rf-inject-max-per-min",
        type=int,
        default=BRIDGE_RF_INJECT_MAX_PER_MIN,
        help="Max TCP-to-RF inject packets per bridge per minute, 0 disables count cap",
    )
    parser.add_argument(
        "--bridge-rf-inject-max-airtime-ms-hour",
        type=int,
        default=BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR,
        help="Estimated max TCP-to-RF inject airtime per bridge per hour, 0 disables airtime cap",
    )
    parser.add_argument(
        "--bridge-rf-inject-block-duty-above-pct",
        type=float,
        default=BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT,
        help="Block TCP-to-RF inject when reported duty used percent is at/above this, 0 disables",
    )
    parser.add_argument(
        "--bridge-group",
        default=BRIDGE_GROUP,
        help="Bridge group name assigned to legacy/default clients (default: default)",
    )
    parser.add_argument(
        "--bridge-require-group-match",
        action="store_true",
        help="Only forward packets between bridges with matching group",
    )
    parser.add_argument(
        "--short-id-quarantine",
        choices=("on", "off"),
        default="off",
        help="Automatically quarantine spammy 1-byte source IDs (default: off)",
    )
    parser.add_argument(
        "--short-id-quarantine-window",
        type=int,
        default=SHORT_ID_QUARANTINE_WINDOW_SECS,
        help="Seconds to count bad short-id hits before quarantine (default: 60)",
    )
    parser.add_argument(
        "--short-id-quarantine-threshold",
        type=int,
        default=SHORT_ID_QUARANTINE_THRESHOLD,
        help="Bad short-id hits before quarantine (default: 5)",
    )
    parser.add_argument(
        "--short-id-quarantine-secs",
        type=int,
        default=SHORT_ID_QUARANTINE_SECS,
        help="Seconds to block a quarantined 1-byte source ID (default: 900)",
    )


def add_status_page_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--status-interval",
        type=int,
        default=60,
        help="Log connected clients every N seconds (default: 60, 0 disables)",
    )
    parser.add_argument(
        "--status-host",
        default="0.0.0.0",
        help="Bind address for the HTTP status page (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--status-port",
        type=int,
        default=8080,
        help="HTTP status page port (default: 8080, 0 disables)",
    )
    parser.add_argument(
        "--status-base-path",
        default=STATUS_BASE_PATH,
        help="Public URL prefix for status pages behind a reverse proxy, e.g. /meshbridgestatus",
    )


def add_firmware_update_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--firmware-update-repo",
        default=FIRMWARE_UPDATE_REPO,
        help="GitHub owner/repo used for firmware update checks (default: MichTronics/MeshCoreNG, empty disables)",
    )
    parser.add_argument(
        "--firmware-update-interval",
        type=int,
        default=FIRMWARE_UPDATE_CHECK_INTERVAL_SECS,
        help="Seconds between firmware update checks (default: 3600, 0 disables)",
    )
    parser.add_argument(
        "--firmware-update-timeout",
        type=int,
        default=FIRMWARE_UPDATE_TIMEOUT_SECS,
        help="HTTP timeout in seconds for firmware update checks (default: 5)",
    )


def add_packet_decode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-packets", action="store_true", help="Log every received and forwarded mesh payload")
    parser.add_argument(
        "--log-hex-bytes",
        type=int,
        default=32,
        help="Number of payload bytes to show in packet logs (default: 32)",
    )
    parser.add_argument(
        "--public-channels-file",
        default="",
        help="JSON file with public channel names/secrets for optional group packet decoding",
    )
    parser.add_argument(
        "--location-tracks-dir",
        default=str(LOCATION_TRACKS_DIR),
        help="Directory for persistent tracker route JSONL files (default: logs/location_tracks)",
    )


def add_rate_limit_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--transport-rate-limit",
        choices=("on", "off"),
        default="on",
        help="Rate-limit DM/group/transport packets before bridge broadcast (default: on)",
    )
    parser.add_argument(
        "--transport-rate-max",
        type=int,
        default=TRANSPORT_RATE_LIMIT_MAX,
        help="Max transport packets per bridge client per window (default: 20)",
    )
    parser.add_argument(
        "--transport-rate-window",
        type=int,
        default=TRANSPORT_RATE_LIMIT_WINDOW_SECS,
        help="Per-client transport rate window in seconds (default: 120)",
    )
    parser.add_argument(
        "--transport-global-rate-max",
        type=int,
        default=TRANSPORT_GLOBAL_RATE_LIMIT_MAX,
        help="Max transport packets globally per window, 0 disables global cap (default: 80)",
    )
    parser.add_argument(
        "--transport-global-rate-window",
        type=int,
        default=TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS,
        help="Global transport rate window in seconds (default: 120)",
    )


def add_admin_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--admin-password",
        default="",
        help="Optional Basic auth password protecting the HTTP remote management page",
    )
    parser.add_argument(
        "--allow-path-block-admin",
        action="store_true",
        help="Allow HTTP bridge admins to send path.block quarantine commands without node passwords",
    )


def apply_args(args: argparse.Namespace) -> None:
    global LOG_PACKETS, LOG_HEX_BYTES, CLIENT_TIMEOUT_SECS, CLIENT_TX_QUEUE_MAX
    global BRIDGE_DEDUPE_ENABLED, BRIDGE_DEDUPE_TTL_SECS, BRIDGE_DEDUPE_MAX_ENTRIES
    global BRIDGE_LOOPGUARD_ENABLED, BRIDGE_LOOPGUARD_WINDOW_SECS, BRIDGE_LOOPGUARD_THRESHOLD
    global BRIDGE_LOOPGUARD_QUARANTINE_SECS, BRIDGE_RF_INJECT_ENABLED, BRIDGE_RF_INJECT_MAX_PER_MIN
    global BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR, BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT
    global BRIDGE_GROUP, BRIDGE_REQUIRE_GROUP_MATCH, SHORT_ID_QUARANTINE_ENABLED
    global SHORT_ID_QUARANTINE_WINDOW_SECS, SHORT_ID_QUARANTINE_THRESHOLD, SHORT_ID_QUARANTINE_SECS
    global STATUS_INTERVAL_SECS, REPLACE_SAME_IP, BRIDGE_PASSWORD, ADMIN_PASSWORD
    global ALLOW_PATH_BLOCK_ADMIN, STATUS_BASE_PATH, FIRMWARE_UPDATE_REPO
    global FIRMWARE_UPDATE_CHECK_INTERVAL_SECS, FIRMWARE_UPDATE_TIMEOUT_SECS
    global PUBLIC_CHANNELS_FILE, LOCATION_TRACKS_DIR, TRANSPORT_RATE_LIMIT_ENABLE
    global TRANSPORT_RATE_LIMIT_MAX, TRANSPORT_RATE_LIMIT_WINDOW_SECS
    global TRANSPORT_GLOBAL_RATE_LIMIT_MAX, TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS

    if args.verbose:
        log.setLevel(logging.DEBUG)

    LOG_PACKETS = args.log_packets
    LOG_HEX_BYTES = max(0, args.log_hex_bytes)
    CLIENT_TIMEOUT_SECS = max(0, args.client_timeout)
    CLIENT_TX_QUEUE_MAX = max(1, args.client_tx_queue_max)

    BRIDGE_DEDUPE_ENABLED = args.bridge_dedupe == "on"
    BRIDGE_DEDUPE_TTL_SECS = max(1, args.bridge_dedupe_ttl)
    BRIDGE_DEDUPE_MAX_ENTRIES = max(1, args.bridge_dedupe_max_entries)
    BRIDGE_LOOPGUARD_ENABLED = args.bridge_loopguard == "on"
    BRIDGE_LOOPGUARD_WINDOW_SECS = max(1, args.bridge_loopguard_window)
    BRIDGE_LOOPGUARD_THRESHOLD = max(1, args.bridge_loopguard_threshold)
    BRIDGE_LOOPGUARD_QUARANTINE_SECS = max(1, args.bridge_loopguard_quarantine)
    BRIDGE_RF_INJECT_ENABLED = args.bridge_rf_inject == "on"
    BRIDGE_RF_INJECT_MAX_PER_MIN = max(0, args.bridge_rf_inject_max_per_min)
    BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR = max(0, args.bridge_rf_inject_max_airtime_ms_hour)
    BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT = max(0.0, args.bridge_rf_inject_block_duty_above_pct)
    BRIDGE_GROUP = (args.bridge_group or "default").strip() or "default"
    BRIDGE_REQUIRE_GROUP_MATCH = bool(args.bridge_require_group_match)
    SHORT_ID_QUARANTINE_ENABLED = args.short_id_quarantine == "on"
    SHORT_ID_QUARANTINE_WINDOW_SECS = max(1, args.short_id_quarantine_window)
    SHORT_ID_QUARANTINE_THRESHOLD = max(1, args.short_id_quarantine_threshold)
    SHORT_ID_QUARANTINE_SECS = max(1, args.short_id_quarantine_secs)

    STATUS_INTERVAL_SECS = max(0, args.status_interval)
    STATUS_BASE_PATH = normalize_base_path(args.status_base_path)
    REPLACE_SAME_IP = args.replace_same_ip
    BRIDGE_PASSWORD = args.password
    ADMIN_PASSWORD = args.admin_password
    ALLOW_PATH_BLOCK_ADMIN = args.allow_path_block_admin

    FIRMWARE_UPDATE_REPO = args.firmware_update_repo.strip()
    FIRMWARE_UPDATE_CHECK_INTERVAL_SECS = max(0, args.firmware_update_interval)
    FIRMWARE_UPDATE_TIMEOUT_SECS = max(1, args.firmware_update_timeout)

    PUBLIC_CHANNELS_FILE = args.public_channels_file
    LOCATION_TRACKS_DIR = Path(args.location_tracks_dir)

    TRANSPORT_RATE_LIMIT_ENABLE = args.transport_rate_limit == "on"
    TRANSPORT_RATE_LIMIT_MAX = max(0, args.transport_rate_max)
    TRANSPORT_RATE_LIMIT_WINDOW_SECS = max(1, args.transport_rate_window)
    TRANSPORT_GLOBAL_RATE_LIMIT_MAX = max(0, args.transport_global_rate_max)
    TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS = max(1, args.transport_global_rate_window)


def run_cli() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    apply_args(args)

    load_public_channels(PUBLIC_CHANNELS_FILE)
    load_location_tracks()

    try:
        asyncio.run(main(args.host, args.port, args.status_host, max(0, args.status_port)))
    except KeyboardInterrupt:
        log.info("Server stopped")


if __name__ == "__main__":
    run_cli()
