"""Bridge traffic and guard statistics helpers."""

from collections import deque
import logging
import time

import bridge_server.config as config
import bridge_server.state as state
from bridge_server.constants import PACKET_COUNTER_WINDOW_SECS
from bridge_server.firmware import firmware_update_status
from bridge_server.protocol import (
    block_stats_totals,
    format_duration,
    has_node_identity,
    is_transport_or_message_packet,
    make_node_stats_key,
    prune_packet_times,
    prune_rate_window,
    short_id_label,
)

log = logging.getLogger("tcp_bridge")


def inc_bridge_guard_counter(name: str, amount: int=1) -> None:
    """Increment a named bridge guard counter."""
    state.bridge_guard_counters[name] = state.bridge_guard_counters.get(name, 0) + amount


def new_node_stats(key: str) -> dict:
    """Create a fresh node statistics record."""
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
        "bridge_group": config.BRIDGE_GROUP,
        "node_rf_inject_budget": {},
        "block_stats": {
            "path": [],
            "node": [],
            "updated_at": 0.0,
            "error": "",
        },
        "connected": False,
        "client_id": "",
        "addr": "",
    }


def merge_node_stats(target: dict, source: dict) -> None:
    """Merge persisted node statistics into an active record."""
    target['rx_times'].extend(source['rx_times'])
    target['tx_times'].extend(source['tx_times'])
    target['rx_times'] = deque(sorted(target['rx_times']))
    target['tx_times'] = deque(sorted(target['tx_times']))
    target['packets_rx'] += source.get('packets_rx', 0)
    target['packets_tx'] += source.get('packets_tx', 0)
    target['heartbeats_rx'] += source.get('heartbeats_rx', 0)
    target['first_seen'] = min(target.get('first_seen', time.time()), source.get('first_seen', time.time()))
    for field in ('last_seen', 'last_connected', 'last_disconnect', 'last_heartbeat'):
        target[field] = max(target.get(field, 0.0), source.get(field, 0.0))
    for field in ('rf_tx_total_baseline_ms', 'rf_tx_total_baseline_at', 'rf_tx_hour_baseline_ms', 'rf_tx_hour_started_at'):
        if target.get(field) is None or not target.get(field):
            target[field] = source.get(field)
    for field in ('last_heartbeat_uptime_ms',):
        if source.get(field):
            target[field] = source[field]
    for field in ('rf_duty', 'radio_stats', 'node_name', 'node_id', 'firmware_version', 'client_id', 'addr', 'bridge_group', 'node_rf_inject_budget', 'block_stats'):
        if source.get(field):
            target[field] = source[field]
    if source.get('neighbor_count') is not None:
        target['neighbor_count'] = source['neighbor_count']
    target['flood_hop_limit_drops'] = max(int(target.get('flood_hop_limit_drops') or 0), int(source.get('flood_hop_limit_drops') or 0))
    target['supports_bridge_v2'] = target.get('supports_bridge_v2', False) or source.get('supports_bridge_v2', False)
    target['bridge_proto_ver'] = max(target.get('bridge_proto_ver', 1), source.get('bridge_proto_ver', 1))
    target['connected'] = target.get('connected', False) or source.get('connected', False)


def get_node_stats(client: 'BridgeClient') -> dict:
    """Return the statistics record for a client."""
    key = make_node_stats_key(client)
    old_key = getattr(client, '_stats_key', '')
    if old_key and old_key != key and (old_key in state.node_traffic_stats):
        old_stats = state.node_traffic_stats.pop(old_key)
        stats = state.node_traffic_stats.setdefault(key, new_node_stats(key))
        merge_node_stats(stats, old_stats)
    else:
        stats = state.node_traffic_stats.setdefault(key, new_node_stats(key))
    client._stats_key = key
    stats.update({'key': key, 'node_name': client.node_name, 'node_id': client.node_id, 'firmware_version': client.firmware_version, 'supports_bridge_v2': client.supports_bridge_v2, 'bridge_proto_ver': client.bridge_proto_ver, 'bridge_group': client.bridge_group, 'node_rf_inject_budget': dict(client.node_rf_inject_budget), 'block_stats': dict(client.block_stats), 'connected': client in state.connected_clients, 'client_id': client.client_id, 'addr': client.addr, 'last_connected': client._connect_time})
    return stats


def touch_node_stats(client: 'BridgeClient', now: float | None=None) -> dict:
    """Update the last-seen time for a client stats record."""
    now = now or time.time()
    stats = get_node_stats(client)
    stats['last_seen'] = now
    return stats


def record_node_packet(client: 'BridgeClient', direction: str, now: float | None=None) -> None:
    """Record packet timing and counters for a client."""
    now = now or time.time()
    stats = touch_node_stats(client, now)
    if direction == 'RX':
        stats['packets_rx'] += 1
        stats['rx_times'].append(now)
        prune_packet_times(stats['rx_times'], now)
    elif direction == 'TX':
        stats['packets_tx'] += 1
        stats['tx_times'].append(now)
        prune_packet_times(stats['tx_times'], now)


def record_node_heartbeat(client: 'BridgeClient', heartbeat: dict, now: float | None=None) -> None:
    """Record heartbeat details for a client."""
    now = now or time.time()
    stats = touch_node_stats(client, now)
    stats['heartbeats_rx'] += 1
    stats['last_heartbeat'] = now
    stats['last_heartbeat_uptime_ms'] = int(heartbeat.get('uptime_ms') or 0)
    if 'rf_duty' in heartbeat:
        rf = dict(heartbeat['rf_duty'])
        max_ms = int(rf.get('tx_max_ms') or 0)
        window_ms = int(rf.get('window_ms') or 0)
        firmware_used_ms = int(rf.get('tx_used_ms') or 0)
        rf['tx_left_ms'] = max(0, max_ms - firmware_used_ms) if max_ms > 0 else 0
        rf['measured_from_server_start'] = False
        total_ms = rf.get('tx_total_ms')
        if isinstance(total_ms, int):
            baseline = stats.get('rf_tx_total_baseline_ms')
            if baseline is None or total_ms < baseline:
                baseline = total_ms
                stats['rf_tx_total_baseline_ms'] = baseline
                stats['rf_tx_total_baseline_at'] = now
            since_server_ms = max(0, total_ms - baseline)
            rf['tx_since_server_ms'] = since_server_ms
            rf['tx_since_server_pct'] = min(100.0, since_server_ms * 100.0 / max_ms) if max_ms > 0 else 0.0
            hour_started_at = int(now // 3600) * 3600
            hour_baseline = stats.get('rf_tx_hour_baseline_ms')
            if stats.get('rf_tx_hour_started_at') != hour_started_at or hour_baseline is None or total_ms < hour_baseline:
                hour_baseline = total_ms
                stats['rf_tx_hour_baseline_ms'] = hour_baseline
                stats['rf_tx_hour_started_at'] = hour_started_at
            hour_used_ms = max(0, total_ms - hour_baseline)
            hour_used_ms = min(hour_used_ms, max_ms) if max_ms > 0 else hour_used_ms
            rf['tx_hour_used_ms'] = hour_used_ms
            rf['tx_hour_left_ms'] = max(0, max_ms - hour_used_ms) if max_ms > 0 else 0
            rf['tx_hour_used_pct'] = min(100.0, hour_used_ms * 100.0 / max_ms) if max_ms > 0 else 0.0
            rf['tx_hour_started_at'] = hour_started_at
            rf['tx_hour_resets_in_seconds'] = max(0, int(hour_started_at + 3600 - now))
        stats['rf_duty'] = rf
    if 'radio_stats' in heartbeat:
        stats['radio_stats'] = dict(heartbeat['radio_stats'])
    if 'neighbor_count' in heartbeat:
        stats['neighbor_count'] = int(heartbeat['neighbor_count'])
    if 'flood_hop_limit_drops' in heartbeat:
        stats['flood_hop_limit_drops'] = int(heartbeat['flood_hop_limit_drops'])


def mark_node_disconnected(client: 'BridgeClient', now: float | None=None) -> None:
    """Mark a client stats record as disconnected."""
    now = now or time.time()
    stats = get_node_stats(client)
    stats['connected'] = False
    stats['last_disconnect'] = now
    stats['last_seen'] = max(stats.get('last_seen', 0.0), client.last_seen)


def node_stats_status_dict(stats: dict, now: float) -> dict:
    """Build the status payload for a node stats record."""
    prune_packet_times(stats['rx_times'], now)
    prune_packet_times(stats['tx_times'], now)
    display_name = stats.get('node_name') or 'unnamed bridge node'
    connected = bool(stats.get('connected'))
    heartbeat_age = int(now - stats['last_heartbeat']) if stats.get('last_heartbeat') else None
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
        "tx_queue_max": config.CLIENT_TX_QUEUE_MAX,
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
        "group": stats.get("bridge_group") or config.BRIDGE_GROUP,
        "bridge_proto_ver": stats.get("bridge_proto_ver", 1),
        "node_rf_inject_budget": dict(stats.get("node_rf_inject_budget") or {}),
        "block_stats": dict(
            stats.get("block_stats")
            or {"path": [], "node": [], "updated_at": 0.0, "error": ""}
        ),
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
        "last_seen_seconds": int(now - stats["last_seen"]) if stats.get("last_seen") else None,
    }


def prune_disconnected_node_stats(now: float) -> None:
    """Drop stale disconnected node statistics."""
    for key, stats in list(state.node_traffic_stats.items()):
        prune_packet_times(stats['rx_times'], now)
        prune_packet_times(stats['tx_times'], now)
        if stats.get('connected'):
            continue
        if not has_node_identity(stats):
            state.node_traffic_stats.pop(key, None)
            continue
        last_seen = stats.get('last_seen', 0.0)
        if not stats['rx_times'] and (not stats['tx_times']) and (last_seen < now - PACKET_COUNTER_WINDOW_SECS):
            state.node_traffic_stats.pop(key, None)


def prune_packet_fingerprint_cache(now: float | None=None) -> None:
    """Prune expired packet fingerprint entries."""
    now = now or time.time()
    if not config.BRIDGE_DEDUPE_ENABLED:
        state.packet_fingerprint_cache.clear()
        return
    cutoff = now - max(1, config.BRIDGE_DEDUPE_TTL_SECS)
    for fingerprint, record in list(state.packet_fingerprint_cache.items()):
        if record.get('last_seen', 0.0) >= cutoff and len(state.packet_fingerprint_cache) <= config.BRIDGE_DEDUPE_MAX_ENTRIES:
            break
        state.packet_fingerprint_cache.pop(fingerprint, None)
    while len(state.packet_fingerprint_cache) > max(1, config.BRIDGE_DEDUPE_MAX_ENTRIES):
        state.packet_fingerprint_cache.popitem(last=False)


def packet_fingerprint_record(fingerprint: int, now: float | None=None) -> dict:
    """Return the fingerprint record for a packet."""
    now = now or time.time()
    prune_packet_fingerprint_cache(now)
    record = state.packet_fingerprint_cache.get(fingerprint)
    if record is None:
        record = {'fingerprint': fingerprint, 'first_seen': now, 'last_seen': now, 'seen_from': {}, 'sent_to': {}}
        state.packet_fingerprint_cache[fingerprint] = record
    else:
        record['last_seen'] = now
        state.packet_fingerprint_cache.move_to_end(fingerprint)
    return record


def prune_short_id_quarantine(now: float | None=None) -> None:
    """Drop expired short-ID quarantine entries."""
    now = now or time.time()
    for short_id, record in list(state.short_id_quarantine.items()):
        if record.get('until', 0.0) > now:
            continue
        state.short_id_quarantine.pop(short_id, None)
        inc_bridge_guard_counter('short_id_quarantine_end')
        log.warning('short_id_quarantine_end id=%s', short_id_label(short_id))


def short_id_quarantine_active(short_id: int | None, now: float | None=None) -> bool:
    """Return whether a short ID is quarantined."""
    if not config.SHORT_ID_QUARANTINE_ENABLED or short_id is None:
        return False
    prune_short_id_quarantine(now)
    record = state.short_id_quarantine.get(short_id)
    return bool(record and record.get('until', 0.0) > (now or time.time()))


def record_short_id_bad_hit(short_id: int | None, reason: str, client: 'BridgeClient', now: float | None=None) -> None:
    """Record a short-ID guard hit for a client."""
    if not config.SHORT_ID_QUARANTINE_ENABLED or short_id is None:
        return
    now = now or time.time()
    window = max(1, config.SHORT_ID_QUARANTINE_WINDOW_SECS)
    hits = state.short_id_bad_hits.setdefault(short_id, deque())
    while hits and hits[0] < now - window:
        hits.popleft()
    hits.append(now)
    inc_bridge_guard_counter('short_id_bad_hit')
    if len(hits) < max(1, config.SHORT_ID_QUARANTINE_THRESHOLD):
        return
    until = now + max(1, config.SHORT_ID_QUARANTINE_SECS)
    current = state.short_id_quarantine.get(short_id, {})
    if current.get('until', 0.0) < until:
        state.short_id_quarantine[short_id] = {'id': short_id, 'until': until, 'reason': reason, 'source': client.display_name, 'addr': client.addr, 'hits': len(hits), 'started_at': now}
        inc_bridge_guard_counter('short_id_quarantine_start')
        log.warning('%s: short_id_quarantine_start id=%s reason=%s hits=%d ttl=%ds', client.addr, short_id_label(short_id), reason, len(hits), max(1, config.SHORT_ID_QUARANTINE_SECS))
    hits.clear()


def short_id_quarantine_snapshot(now: float | None=None) -> list[dict]:
    """Return the current short-ID quarantine snapshot."""
    now = now or time.time()
    prune_short_id_quarantine(now)
    items = []
    for short_id, record in sorted(state.short_id_quarantine.items()):
        items.append({'id': short_id_label(short_id), 'raw': short_id, 'reason': record.get('reason', ''), 'source': record.get('source', ''), 'seconds_left': max(0, int(record.get('until', 0.0) - now)), 'hits': int(record.get('hits') or 0)})
    return items


def allow_transport_packet(client: 'BridgeClient', parsed: dict | None, now: float) -> bool:
    """Return whether a transport packet is within rate limits."""
    if not config.TRANSPORT_RATE_LIMIT_ENABLE or not is_transport_or_message_packet(parsed):
        return True
    prune_rate_window(client.transport_rx_times, now, config.TRANSPORT_RATE_LIMIT_WINDOW_SECS)
    prune_rate_window(state.transport_rx_times, now, config.TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS)
    if config.TRANSPORT_RATE_LIMIT_MAX > 0 and len(client.transport_rx_times) >= config.TRANSPORT_RATE_LIMIT_MAX:
        client.transport_rate_dropped += 1
        state.transport_rate_dropped += 1
        return False
    if config.TRANSPORT_GLOBAL_RATE_LIMIT_MAX > 0 and len(state.transport_rx_times) >= config.TRANSPORT_GLOBAL_RATE_LIMIT_MAX:
        client.transport_rate_dropped += 1
        state.transport_rate_dropped += 1
        return False
    client.transport_rx_times.append(now)
    state.transport_rx_times.append(now)
    return True
