"""Location and public-channel helpers."""

import json
import logging
import re
import time
from pathlib import Path

import bridge_server.config as config
import bridge_server.state as state
from bridge_server.constants import (
    ADV_TYPE_REPEATER,
    CIPHER_MAC_SIZE,
    DATA_TYPE_MESHCORENG_TRACKER,
    DEFAULT_PUBLIC_CHANNEL_SECRET,
    DEFAULT_TRACKER_CHANNEL_SECRET,
    PAYLOAD_TYPE_GRP_DATA,
    PAYLOAD_TYPE_GRP_TXT,
    PAYLOAD_TYPE_LOCATION,
    PATH_HASH_SIZE,
)
from bridge_server.protocol import (
    Cipher,
    channel_hash,
    decode_group_data_plain,
    derive_channel_secret,
    location_distance_m,
    location_point_time,
    location_track_point,
    mac_then_decrypt,
    mark_route_breaks,
    mesh_payload_for_parsing,
    parse_location_report,
    parse_mesh_payload,
    parse_node_advert_payload,
    payload_preview,
    trim_c_string,
)

log = logging.getLogger("tcp_bridge")


def decrypt_public_channel_plain(parsed: dict) -> tuple[str, bytes] | None:
    """Decrypt a public-channel payload when a key matches."""
    app_payload = parsed.get('app_payload') or b''
    if not state.public_channels or len(app_payload) <= PATH_HASH_SIZE + CIPHER_MAC_SIZE:
        return None
    packet_hash = app_payload[:PATH_HASH_SIZE]
    encrypted = app_payload[PATH_HASH_SIZE:]
    for channel in (ch for ch in state.public_channels if ch['hash'] == packet_hash):
        plain = mac_then_decrypt(channel['secret'], encrypted)
        if plain:
            return (channel['name'], plain)
    return None


def decode_public_channel_payload(parsed: dict) -> dict:
    """Decode a public-channel group payload summary."""
    result = {
        "decoded_channel": "",
        "decoded_status": "",
        "decoded_text": "",
        "decoded_data_type": None,
        "decoded_data_len": None,
    }
    payload_type = parsed.get('payload_type')
    app_payload = parsed.get('app_payload') or b''
    if payload_type not in (PAYLOAD_TYPE_GRP_TXT, PAYLOAD_TYPE_GRP_DATA):
        return result
    if not state.public_channels:
        result['decoded_status'] = 'channel-keys-not-loaded'
        return result
    if len(app_payload) <= PATH_HASH_SIZE + CIPHER_MAC_SIZE:
        result['decoded_status'] = 'short-group-payload'
        return result
    if not any((ch['hash'] == app_payload[:PATH_HASH_SIZE] for ch in state.public_channels)):
        result['decoded_status'] = 'unknown-channel'
        return result
    decoded = decrypt_public_channel_plain(parsed)
    if decoded is None:
        result['decoded_status'] = 'mac-failed'
        return result
    channel_name, plain = decoded
    result['decoded_channel'] = channel_name
    if payload_type == PAYLOAD_TYPE_GRP_TXT:
        if len(plain) < 5:
            result['decoded_status'] = 'short-group-text'
            return result
        txt_type = plain[4]
        if txt_type >> 2 != 0:
            result['decoded_status'] = f'unsupported-text-type-{txt_type}'
            return result
        result['decoded_status'] = 'decoded'
        result['decoded_text'] = trim_c_string(plain[5:])
        return result
    group_data = decode_group_data_plain(plain)
    if group_data is None:
        result['decoded_status'] = 'short-group-data'
        return result
    data_type, data = group_data
    result['decoded_status'] = 'decoded'
    result['decoded_data_type'] = data_type
    result['decoded_data_len'] = len(data)
    result['decoded_text'] = f'data_type=0x{data_type:04x} len={len(data)} preview={payload_preview(data)}'
    return result


def parse_mesh_location_payload(frame_payload: bytes) -> dict | None:
    """Parse a location report from a mesh payload."""
    parsed = parse_mesh_payload(frame_payload)
    if not parsed:
        return None
    report = None
    if parsed['payload_type'] == PAYLOAD_TYPE_LOCATION:
        report = parse_location_report(parsed['app_payload'])
    elif parsed['payload_type'] == PAYLOAD_TYPE_GRP_DATA:
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
    report['hops'] = parsed['path_hash_count']
    report['payload_type'] = parsed['payload_type']
    return report


def source_short_id(payload: bytes) -> int | None:
    """Extract the best available short source ID from a payload."""
    mesh = mesh_payload_for_parsing(payload)
    location = parse_mesh_location_payload(mesh)
    if location and location.get('node_id'):
        try:
            return int(str(location['node_id'])[:2], 16)
        except ValueError:
            return None
    advert = parse_node_advert_payload(mesh)
    if advert and advert.get('node_id'):
        try:
            return int(str(advert['node_id'])[:2], 16)
        except ValueError:
            return None
    parsed = parse_mesh_payload(mesh)
    if parsed and parsed.get('app_payload'):
        return parsed['app_payload'][0]
    return None


def add_public_channel(name: str, secret: bytes) -> None:
    """Add a public channel key if it is not already loaded."""
    if any((ch['name'] == name and ch['secret'] == secret for ch in state.public_channels)):
        return
    state.public_channels.append(
        {"name": name, "secret": secret, "hash": channel_hash(secret)}
    )


def load_public_channels(path: str) -> None:
    """Load built-in and configured public channel keys."""
    state.public_channels.clear()
    add_public_channel('Public', DEFAULT_PUBLIC_CHANNEL_SECRET)
    add_public_channel('Trackers', DEFAULT_TRACKER_CHANNEL_SECRET)
    if not path:
        return
    if Cipher is None:
        log.warning("Public channel decoding disabled: install python package 'cryptography'")
        return
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:
        log.warning('Public channel decoding disabled: cannot read %s (%s)', path, exc)
        return
    if isinstance(data, dict):
        data = data.get('channels', [])
    if not isinstance(data, list):
        log.warning("Public channel decoding disabled: %s must contain a list or {'channels': [...]} object", path)
        return
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name', '')).strip()
        secret = derive_channel_secret(str(item.get('secret', '')))
        if not name or secret is None:
            log.warning('Skipping invalid public channel entry: %s', item)
            continue
        add_public_channel(name, secret)
    log.info('Loaded %d public channel key(s) from %s', len(state.public_channels), path)


def location_track_path(node_id: str) -> Path:
    """Return the JSONL path for a tracker node."""
    safe = re.sub('[^0-9a-fA-F_-]', '_', node_id or 'unknown')
    return config.LOCATION_TRACKS_DIR / f'{safe}.jsonl'


def seed_stationary_state(node_id: str, track: list[dict]) -> None:
    """Seed stationary route tracking from a loaded track."""
    if not track:
        return
    anchor = track[-1]
    state.latest_location_stationary[node_id] = {
        "anchor": anchor,
        "since": location_point_time(anchor),
        "closed": bool(anchor.get("route_break_after")),
    }


def update_stationary_route_state(node_id: str, track: list[dict], point: dict) -> None:
    """Update stationary route state for a tracker node."""
    state = state.latest_location_stationary.get(node_id)
    if state is None:
        state = {"anchor": point, "since": location_point_time(point), "closed": False}
        state.latest_location_stationary[node_id] = state
        return
    anchor = state['anchor']
    point_time = location_point_time(point)
    if location_distance_m(anchor, point) <= LOCATION_ROUTE_STATIONARY_METERS:
        if not state.get('closed') and point_time - int(state.get('since', point_time)) >= LOCATION_ROUTE_STATIONARY_SECS:
            point['route_break_after'] = True
            state['closed'] = True
            log.info('Closed tracker route for %s after %d minutes within %dm', node_id, LOCATION_ROUTE_STATIONARY_SECS // 60, LOCATION_ROUTE_STATIONARY_METERS)
        return
    state['anchor'] = point
    state['since'] = point_time
    state['closed'] = False


def append_location_track_point(node_id: str, point: dict) -> None:
    """Append a tracker point to persistent storage."""
    try:
        config.LOCATION_TRACKS_DIR.mkdir(parents=True, exist_ok=True)
        with location_track_path(node_id).open('a', encoding='utf-8') as file:
            file.write(json.dumps(point, separators=(',', ':')) + '\n')
    except OSError as exc:
        log.warning('Could not persist location track for %s: %s', node_id, exc)


def load_location_tracks() -> None:
    """Load persisted tracker routes into memory."""
    state.latest_location_tracks.clear()
    state.latest_locations.clear()
    state.latest_location_stationary.clear()
    if not config.LOCATION_TRACKS_DIR.exists():
        return
    loaded_points = 0
    for path in sorted(config.LOCATION_TRACKS_DIR.glob('*.jsonl')):
        node_id = path.stem
        track: list[dict] = []
        try:
            with path.open('r', encoding='utf-8') as file:
                for lineno, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        point = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning('Skipping invalid location track line %s:%d', path, lineno)
                        continue
                    if not isinstance(point, dict):
                        continue
                    lat = point.get('lat')
                    lon = point.get('lon')
                    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
                        continue
                    point.setdefault('node_id', node_id)
                    track.append(point)
        except OSError as exc:
            log.warning('Could not load location track %s: %s', path, exc)
            continue
        if not track:
            continue
        mark_route_breaks(track)
        latest = dict(track[-1])
        actual_node_id = latest.get('node_id') or node_id
        latest['node_id'] = actual_node_id
        latest.setdefault('name', '')
        latest.setdefault('received_at', int(latest.get('timestamp') or time.time()))
        latest['age_seconds'] = 0
        latest['source'] = 'track-file'
        state.latest_location_tracks[actual_node_id] = track
        state.latest_locations[actual_node_id] = latest
        seed_stationary_state(actual_node_id, track)
        loaded_points += len(track)
    if loaded_points:
        log.info('Loaded %d persisted location track point(s) for %d tracker node(s)', loaded_points, len(state.latest_location_tracks))


def record_location(report: dict, client: 'BridgeClient') -> None:
    """Record the latest location report for a node."""
    now = time.time()
    report = dict(report)
    report['received_at'] = int(now)
    report['age_seconds'] = 0
    report['source'] = client.display_name
    client.learn_node_id(report.get('node_id', ''), report.get('name', ''))
    state.latest_locations[report['node_id']] = report
    track = state.latest_location_tracks.setdefault(report['node_id'], [])
    point = location_track_point(report)
    if not track or any((track[-1].get(key) != point.get(key) for key in ('lat', 'lon', 'timestamp'))):
        update_stationary_route_state(report['node_id'], track, point)
        track.append(point)
        append_location_track_point(report['node_id'], point)


def record_sensor_advert(report: dict, client: 'BridgeClient') -> None:
    """Record the latest sensor advert for a node."""
    now = time.time()
    report = dict(report)
    existing = state.latest_sensors.get(report['node_id'], {})
    report['received_at'] = int(now)
    report['age_seconds'] = 0
    report['source'] = client.display_name
    report['seen_count'] = int(existing.get('seen_count', 0)) + 1
    client.learn_node_id(report.get('node_id', ''), report.get('name', ''))
    state.latest_sensors[report['node_id']] = report
