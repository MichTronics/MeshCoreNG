"""TCP bridge server packet handling."""

import asyncio
import logging
import time

import bridge_server.config as config
import bridge_server.state as state
from bridge_server.client import BridgeClient
from bridge_server.constants import *
from bridge_server.location import (
    decode_public_channel_payload,
    parse_mesh_location_payload,
    record_location,
    record_sensor_advert,
    source_short_id,
)
from bridge_server.protocol import (
    CONTROL_TYPE_NAMES,
    block_stats_totals,
    decrement_bridge_ttl,
    describe_peer_encrypted_payload,
    fingerprint_hex,
    format_duration,
    mesh_payload_for_parsing,
    packet_fingerprint,
    packet_type_name,
    parse_block_stats_reply,
    parse_bridge_packet_envelope,
    parse_bridge_packet_error,
    parse_caps,
    parse_command_reply,
    parse_heartbeat,
    parse_mesh_payload,
    parse_node_advert_payload,
    parse_node_info,
    parse_auth,
    parse_sensor_advert_payload,
    payload_preview,
    prune_packet_times,
    redact_public_value,
    route_type_name,
    short_id_label,
    update_block_drop_counters,
)
from bridge_server.stats import (
    allow_transport_packet,
    get_node_stats,
    has_node_identity,
    inc_bridge_guard_counter,
    mark_node_disconnected,
    node_stats_status_dict,
    packet_fingerprint_record,
    prune_disconnected_node_stats,
    record_node_heartbeat,
    record_node_packet,
    record_short_id_bad_hit,
    short_id_quarantine_active,
    short_id_quarantine_snapshot,
    touch_node_stats,
)

log = logging.getLogger("tcp_bridge")


def describe_packet(payload: bytes) -> dict:
    """Describe a payload for logging and status views."""
    envelope = parse_bridge_packet_envelope(payload)
    mesh_payload = envelope['mesh_payload'] if envelope is not None else payload
    description = {'kind': 'mesh', 'type': 'unknown', 'type_id': None, 'route': 'unknown', 'route_id': None, 'hops': None, 'app_len': None, 'bridge_v2': envelope is not None, 'ttl': envelope['ttl'] if envelope is not None else None, 'origin_id': f'0x{envelope['origin_id']:08x}' if envelope is not None else '', 'flags': f'0x{envelope['flags']:02x}' if envelope is not None else '', 'mesh_len': len(mesh_payload), 'decoded_channel': '', 'decoded_status': '', 'decoded_text': '', 'decoded_data_type': None, 'decoded_data_len': None, 'peer_dest_hash': '', 'peer_src_hash': '', 'peer_mac': '', 'peer_encrypted_len': None, 'peer_encrypted_preview': ''}
    if envelope is None and payload.startswith(CONTROL_PREFIX):
        control_type = payload[4] if len(payload) > 4 else None
        description.update({'kind': 'control', 'type': CONTROL_TYPE_NAMES.get(control_type, f'control-0x{control_type:02x}' if control_type is not None else 'control'), 'type_id': control_type, 'route': '', 'route_id': None, 'hops': None, 'app_len': max(0, len(payload) - 5), 'mesh_len': 0})
        return description
    parsed = parse_mesh_payload(mesh_payload)
    if parsed is None:
        return description
    payload_type = parsed['payload_type']
    route_type = parsed['route_type']
    public_decode = decode_public_channel_payload(parsed)
    peer_info = describe_peer_encrypted_payload(parsed)
    description.update({'type': packet_type_name(payload_type), 'type_id': payload_type, 'route': route_type_name(route_type), 'route_id': route_type, 'hops': parsed['path_hash_count'], 'app_len': len(parsed['app_payload']), **public_decode})
    if not description.get('decoded_status'):
        description.update(peer_info)
    else:
        description.update({'peer_dest_hash': peer_info['peer_dest_hash'], 'peer_src_hash': peer_info['peer_src_hash'], 'peer_mac': peer_info['peer_mac'], 'peer_encrypted_len': peer_info['peer_encrypted_len'], 'peer_encrypted_preview': peer_info['peer_encrypted_preview']})
    return description


def format_packet_description(description: dict) -> str:
    """Format a packet description for logs."""
    parts = [f'type={description['type']}']
    if description.get('route'):
        parts.append(f'route={description['route']}')
    if description.get('decoded_channel'):
        parts.append(f'channel={description['decoded_channel']}')
    if description.get('hops') is not None:
        parts.append(f'hops={description['hops']}')
    if description.get('app_len') is not None:
        parts.append(f'app={description['app_len']}B')
    if description.get('bridge_v2'):
        parts.append(f'bridge-v2 ttl={description['ttl']} origin={description['origin_id']}')
    if description.get('source_short_id'):
        parts.append(f'sid={description['source_short_id']}')
    return ' '.join(parts)


def record_packet_log(direction: str, client: 'BridgeClient', payload: bytes, source: str='', target: str='') -> dict:
    """Record a packet log entry."""
    description = describe_packet(payload)
    mesh_payload = mesh_payload_for_parsing(payload)
    short_id = source_short_id(payload)
    source = source or ('server' if direction == 'TX' else client.display_name)
    target = target or (client.display_name if direction == 'TX' else 'server')
    entry = {'time': int(time.time()), 'direction': direction, 'client': client.display_name, 'client_id': client.client_id, 'node_id': client.node_id, 'source': source, 'target': target, 'flow': f'{source} -> {target}', 'size': len(payload), 'preview': payload_preview(mesh_payload if description.get('bridge_v2') else payload), 'source_short_id': short_id_label(short_id) if short_id is not None else '', **description}
    state.packet_log_total += 1
    state.recent_packets.appendleft(entry)
    return entry


async def broadcast(payload: bytes, sender: 'BridgeClient'):
    """Forward payload to every connected client except the sender."""
    now = time.time()
    short_id = source_short_id(payload)
    if short_id_quarantine_active(short_id, now):
        sender.record_skip('skipped_short_id_quarantine')
        inc_bridge_guard_counter('skipped_short_id_quarantine')
        log.warning('%s: skipped_short_id_quarantine source_id=%s', sender.addr, short_id_label(short_id))
        return
    forwarded_payload = decrement_bridge_ttl(payload)
    if forwarded_payload is None:
        sender.record_skip('skipped_ttl_expired')
        inc_bridge_guard_counter('skipped_ttl_expired')
        record_short_id_bad_hit(short_id, 'ttl_expired', sender, now)
        log.debug('%s: skipped_ttl_expired', sender.addr)
        return
    sender.mark_seen_payload(forwarded_payload)
    fingerprint = packet_fingerprint(forwarded_payload)
    record = packet_fingerprint_record(fingerprint, now)
    record['seen_from'][sender.bridge_key] = now
    sender.last_fingerprint = fingerprint
    sender.last_fingerprint_at = now
    envelope = parse_bridge_packet_envelope(forwarded_payload)
    for client in state.connected_clients:
        if client is sender:
            client.record_skip('skipped_dup_loopback')
            inc_bridge_guard_counter('skipped_duplicate')
            continue
        if envelope is not None and envelope['origin_id'] and (client.bridge_id == envelope['origin_id']):
            client.record_skip('skipped_own_origin')
            inc_bridge_guard_counter('skipped_own_origin')
            log.debug('%s: skipped_own_origin target=%s origin=0x%08x', sender.addr, client.addr, envelope['origin_id'])
            continue
        if config.BRIDGE_REQUIRE_GROUP_MATCH and client.bridge_group != sender.bridge_group:
            client.record_skip('skipped_group_mismatch')
            log.debug('%s: skipped_group_mismatch target=%s', sender.addr, client.addr)
            continue
        if client.quarantine_active(now):
            client.record_skip('skipped_quarantine')
            inc_bridge_guard_counter('skipped_bridge_loop')
            record_short_id_bad_hit(short_id, 'target_quarantine', sender, now)
            log.warning('%s: skipped_quarantine target=%s', sender.addr, client.addr)
            continue
        if config.BRIDGE_DEDUPE_ENABLED and client.bridge_key in record['seen_from']:
            client.record_skip('skipped_dup_seen_by_target')
            inc_bridge_guard_counter('skipped_duplicate')
            log.debug('%s: skipped_dup_seen_by_target target=%s fp=%s', sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if config.BRIDGE_DEDUPE_ENABLED and client.bridge_key in record['sent_to']:
            client.record_skip('skipped_dup_seen_by_target')
            inc_bridge_guard_counter('skipped_duplicate')
            log.debug('%s: skipped_dup_seen_by_target target=%s fp=%s', sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if client.has_seen_payload(forwarded_payload):
            client.record_skip('skipped_dup_fingerprint')
            inc_bridge_guard_counter('skipped_duplicate')
            log.debug('%s: skipped_dup_fingerprint target=%s fp=%s', sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        client_payload = forwarded_payload
        # Downgrade bridge-v2 envelopes for legacy clients that only speak mesh payloads.
        if envelope is not None and (not client.supports_bridge_v2):
            client_payload = envelope['mesh_payload']
        ok_budget, reason = client.can_accept_rf_inject(forwarded_payload, now)
        if not ok_budget:
            client.rf_budget_drops += 1
            client.record_skip(reason or 'skipped_rf_inject_budget')
            inc_bridge_guard_counter('skipped_rf_inject_budget')
            log.warning('%s: skipped_rf_inject_budget target=%s fp=%s', sender.addr, client.addr, fingerprint_hex(fingerprint))
            continue
        if not client.enqueue_payload(client_payload, source=sender.display_name, seen_payload=forwarded_payload):
            log.warning('%s: queueing TX to %s failed', sender.addr, client.addr)


async def disconnect(client: 'BridgeClient', reason: str='EOF'):
    """Disconnect a bridge client and update stats."""
    if client in state.connected_clients:
        state.connected_clients.discard(client)
        mark_node_disconnected(client)
        client.close()
        uptime = int(time.time() - client._connect_time)
        log.info('Disconnected %s [%s] (%s) — rx=%d tx=%d hb=%d uptime=%ds', client.addr, client.display_name, reason, client.packets_rx, client.packets_tx, client.heartbeats_rx, uptime)


async def refresh_client_block_stats(client: 'BridgeClient', force: bool=False) -> None:
    """Refresh cached path and node block statistics."""
    now = time.time()
    if client not in state.connected_clients or not client.authenticated or client.writer.is_closing():
        return
    if client._block_stats_polling:
        return
    if not force and now - client._last_block_stats_poll < max(1, config.BLOCK_STATS_POLL_INTERVAL_SECS):
        return
    client._block_stats_polling = True
    client._last_block_stats_poll = now
    try:
        path_reply, node_reply = await asyncio.gather(client.send_command('get path.block', '', timeout=config.BLOCK_STATS_COMMAND_TIMEOUT_SECS, count_stats=False), client.send_command('get node.block', '', timeout=config.BLOCK_STATS_COMMAND_TIMEOUT_SECS, count_stats=False))
        client.block_stats = {'path': parse_block_stats_reply('path', path_reply), 'node': parse_block_stats_reply('node', node_reply), 'updated_at': time.time(), 'error': ''}
        client.block_drop_counter_state = update_block_drop_counters(client.block_drop_counter_state, client.block_stats, now=time.time())
        touch_node_stats(client)
    except asyncio.TimeoutError:
        client.block_stats = dict(client.block_stats)
        client.block_stats['error'] = 'timeout'
        client.block_stats['updated_at'] = time.time()
    except Exception as exc:
        client.block_stats = dict(client.block_stats)
        client.block_stats['error'] = str(exc)
        client.block_stats['updated_at'] = time.time()
    finally:
        client._block_stats_polling = False


async def status_task():
    """Periodically log bridge client status."""
    while True:
        if config.STATUS_INTERVAL_SECS <= 0:
            return
        await asyncio.sleep(config.STATUS_INTERVAL_SECS)
        now = time.time()
        for client in list(state.connected_clients):
            idle = int(now - client.last_seen)
            if config.CLIENT_TIMEOUT_SECS > 0 and idle > config.CLIENT_TIMEOUT_SECS:
                await disconnect(client, reason=f'idle timeout {idle}s')
                continue
            asyncio.create_task(refresh_client_block_stats(client))
        if state.connected_clients:
            summaries = []
            for client in sorted(state.connected_clients, key=lambda c: c.addr):
                prune_packet_times(client.packet_rx_times, now)
                prune_packet_times(client.packet_tx_times, now)
                summaries.append(f'{client.display_name}@{client.addr} connected={format_duration(now - client._connect_time)} idle={int(now - client.last_seen)}s hb_age={(str(int(now - client.last_heartbeat)) + 's' if client.last_heartbeat else 'never')} rx24h={len(client.packet_rx_times)} tx24h={len(client.packet_tx_times)} rx={client.packets_rx} tx={client.packets_tx} q={client.tx_queue.qsize()}/{config.CLIENT_TX_QUEUE_MAX} qdrop={client.tx_queue_dropped} qskip={client.tx_skipped_duplicates} serr={client.tx_send_errors} hb={client.heartbeats_rx}')
            summary = ', '.join(summaries)
            log.info('Connected clients (%d): %s', len(state.connected_clients), summary)
        else:
            log.info('Connected clients: none')


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle a connected TCP bridge client."""
    client = BridgeClient(reader, writer)
    if config.REPLACE_SAME_IP:
        for existing in list(state.connected_clients):
            if existing.host == client.host:
                await disconnect(existing, reason=f'replaced by new connection from {client.addr}')
    state.connected_clients.add(client)
    client.start_writer()
    touch_node_stats(client)
    log.info('Connected %s (total=%d)', client.addr, len(state.connected_clients))
    try:
        while True:
            payload = await client.read_packet()
            if payload is None:
                continue
            # Handle control messages before treating the frame as mesh traffic.
            auth_password = parse_auth(payload)
            if auth_password is not None:
                if not config.BRIDGE_PASSWORD or auth_password == config.BRIDGE_PASSWORD:
                    client.authenticated = True
                    log.info('%s: bridge auth ok', client.addr)
                else:
                    log.warning('%s: bridge auth failed', client.addr)
                    await disconnect(client, reason='auth failed')
                    return
                continue
            if not client.authenticated:
                log.warning('%s: missing bridge auth', client.addr)
                await disconnect(client, reason='missing auth')
                return
            caps = parse_caps(payload)
            if caps is not None:
                client.supports_bridge_v2 = caps['bridge_v2']
                client.bridge_proto_ver = int(caps.get('bridge_proto_ver') or 1)
                client.bridge_id = int(caps.get('bridge_id') or 0)
                group = (caps.get('group') or '').strip()
                if group:
                    client.bridge_group = group
                client.node_rf_inject_budget = {'enabled': bool(caps.get('rf_inject_budget_enabled')), 'max_per_min': int(caps.get('rf_inject_max_per_min') or 0), 'max_airtime_ms_per_hour': int(caps.get('rf_inject_max_airtime_ms_per_hour') or 0), 'block_duty_above_pct': float(caps.get('rf_inject_block_duty_above_pct') or 0.0)}
                touch_node_stats(client)
                log.info('%s: bridge capabilities v%d proto=%d flags=0x%02x bridge_v2=%s group=%s bridge_id=%s', client.addr, caps['version'], client.bridge_proto_ver, caps['flags'], client.supports_bridge_v2, client.bridge_group, f'0x{client.bridge_id:08x}' if client.bridge_id else 'unknown')
                continue
            heartbeat = parse_heartbeat(payload)
            if heartbeat is not None:
                client.heartbeats_rx += 1
                now = time.time()
                client.last_heartbeat = now
                client.last_heartbeat_uptime_ms = int(heartbeat.get('uptime_ms') or 0)
                record_node_heartbeat(client, heartbeat, now)
                if 'rf_duty' in heartbeat:
                    client.rf_duty = dict(get_node_stats(client).get('rf_duty') or {})
                if 'radio_stats' in heartbeat:
                    client.radio_stats = dict(heartbeat['radio_stats'])
                if 'neighbor_count' in heartbeat:
                    client.neighbor_count = int(heartbeat['neighbor_count'])
                if 'flood_hop_limit_drops' in heartbeat:
                    client.flood_hop_limit_drops = int(heartbeat['flood_hop_limit_drops'])
                log.debug('%s: heartbeat uptime=%dms', client.addr, client.last_heartbeat_uptime_ms)
                continue
            command_reply = parse_command_reply(payload)
            if command_reply is not None:
                request_id, reply = command_reply
                future = state.pending_commands.get(request_id)
                if future and (not future.done()):
                    future.set_result(reply)
                else:
                    log.debug('%s: stale command reply id=%d', client.addr, request_id)
                continue
            node_info = parse_node_info(payload)
            if node_info is not None:
                client.node_name, client.firmware_version, node_id = node_info
                if node_id:
                    client.node_id = node_id
                touch_node_stats(client)
                if client.firmware_version:
                    log.info('%s: node name is %s firmware=%s node_id=%s', client.addr, client.node_name, client.firmware_version, client.node_id or 'unknown')
                else:
                    log.info('%s: node name is %s node_id=%s', client.addr, client.node_name, client.node_id or 'unknown')
                continue
            bridge_packet_error = parse_bridge_packet_error(payload)
            if bridge_packet_error:
                client.record_skip('skipped_bridge_parse_error')
                inc_bridge_guard_counter('skipped_bridge_parse_error')
                log.warning('%s: skipped_bridge_parse_error %s', client.addr, bridge_packet_error)
                continue
            short_id = source_short_id(payload)
            if short_id_quarantine_active(short_id, now=time.time()):
                client.record_skip('skipped_short_id_quarantine')
                inc_bridge_guard_counter('skipped_short_id_quarantine')
                log.warning('%s: skipped_short_id_quarantine source=%s id=%s', client.addr, client.display_name, short_id_label(short_id))
                continue
            client.packets_rx += 1
            inc_bridge_guard_counter('accepted_tcp_packet')
            now = time.time()
            client.packet_rx_times.append(now)
            prune_packet_times(client.packet_rx_times, now)
            record_node_packet(client, 'RX', now)
            mesh_payload = mesh_payload_for_parsing(payload)
            fingerprint = packet_fingerprint(payload)
            record = packet_fingerprint_record(fingerprint, now)
            client.last_fingerprint = fingerprint
            client.last_fingerprint_at = now
            sent_at = record['sent_to'].get(client.bridge_key)
            if config.BRIDGE_LOOPGUARD_ENABLED and sent_at and (now - sent_at <= max(1, config.BRIDGE_LOOPGUARD_WINDOW_SECS)):
                # A recently re-seen fingerprint suggests the payload looped back from TCP.
                client.record_loopguard_hit(now)
                inc_bridge_guard_counter('skipped_bridge_loop')
                record_short_id_bad_hit(short_id, 'loopguard', client, now)
                if client.quarantine_active(now):
                    client.record_skip('skipped_quarantine')
                    log.warning('%s: skipped_quarantine source=%s fp=%s', client.addr, client.display_name, fingerprint_hex(fingerprint))
                    continue
            if config.BRIDGE_DEDUPE_ENABLED and client.bridge_key in record['seen_from']:
                client.record_skip('skipped_dup_fingerprint')
                inc_bridge_guard_counter('skipped_duplicate')
                record_short_id_bad_hit(short_id, 'duplicate', client, now)
                log.debug('%s: skipped_dup_fingerprint source=%s fp=%s', client.addr, client.display_name, fingerprint_hex(fingerprint))
                continue
            if client.quarantine_active(now):
                client.record_skip('skipped_quarantine')
                inc_bridge_guard_counter('skipped_bridge_loop')
                record_short_id_bad_hit(short_id, 'source_quarantine', client, now)
                log.warning('%s: skipped_quarantine source=%s fp=%s', client.addr, client.display_name, fingerprint_hex(fingerprint))
                continue
            record['seen_from'][client.bridge_key] = now
            client.mark_seen_payload(payload)
            parsed_payload = parse_mesh_payload(mesh_payload)
            if not allow_transport_packet(client, parsed_payload, now):
                inc_bridge_guard_counter('skipped_rate_limited')
                record_short_id_bad_hit(short_id, 'rate_limited', client, now)
                packet_log = record_packet_log('DROP', client, payload, target='rate-limit')
                log.warning('%s: dropping transport flood packet from %s (%d/%ds client, %d/%ds global): %s', client.addr, client.display_name, len(client.transport_rx_times), config.TRANSPORT_RATE_LIMIT_WINDOW_SECS, len(state.transport_rx_times), config.TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS, format_packet_description(packet_log))
                continue
            location_report = parse_mesh_location_payload(mesh_payload)
            if location_report is not None:
                record_location(location_report, client)
            node_advert = parse_node_advert_payload(mesh_payload)
            if node_advert is not None and node_advert.get('advert_type') == ADV_TYPE_REPEATER:
                client.learn_node_id(node_advert.get('node_id', ''), node_advert.get('name', ''))
            sensor_report = parse_sensor_advert_payload(mesh_payload)
            if sensor_report is not None:
                record_sensor_advert(sensor_report, client)
            packet_log = record_packet_log('RX', client, payload)
            if config.LOG_PACKETS:
                envelope = parse_bridge_packet_envelope(payload)
                if envelope is not None:
                    log.info('%s -> server: RX bridge-v2 mesh=%d bytes %s: %s', packet_log['source'], envelope['packet_len'], format_packet_description(packet_log), packet_log['preview'])
                else:
                    log.info('%s -> server: RX %d bytes %s: %s', packet_log['source'], len(payload), format_packet_description(packet_log), packet_log['preview'])
            log.debug('%s: RX %d bytes → broadcasting to %d peers', client.addr, len(payload), len(state.connected_clients) - 1)
            await broadcast(payload, sender=client)
    except asyncio.IncompleteReadError:
        await disconnect(client, reason='EOF')
    except Exception as exc:
        await disconnect(client, reason=str(exc))


def status_snapshot(include_disconnected: bool=True) -> dict:
    """Build the current bridge status snapshot."""
    now = time.time()
    prune_disconnected_node_stats(now)
    try:
        asyncio.get_running_loop()
        for client in list(state.connected_clients):
            if now - client._last_block_stats_poll >= max(1, config.BLOCK_STATS_POLL_INTERVAL_SECS):
                asyncio.create_task(refresh_client_block_stats(client))
    except RuntimeError:
        pass
    clients = [client.status_dict(now) for client in sorted(state.connected_clients, key=lambda c: (c.display_name.lower(), c.addr))]
    if include_disconnected:
        active_keys = {getattr(client, '_stats_key', '') for client in state.connected_clients}
        offline_clients = [node_stats_status_dict(stats, now) for key, stats in state.node_traffic_stats.items() if key not in active_keys and has_node_identity(stats) and (stats.get('last_seen', 0) >= now - PACKET_COUNTER_WINDOW_SECS or stats['rx_times'] or stats['tx_times'])]
        clients.extend(sorted(offline_clients, key=lambda c: (c['display_name'].lower(), -(c.get('last_seen_seconds') or 0))))
    node_block_drops = sum((int((c.get('block_stats_totals') or {}).get('node_drops') or 0) for c in clients))
    path_block_drops = sum((int((c.get('block_stats_totals') or {}).get('path_drops') or 0) for c in clients))
    node_block_active = sum((int((c.get('block_stats_totals') or {}).get('node_active') or 0) for c in clients))
    path_block_active = sum((int((c.get('block_stats_totals') or {}).get('path_active') or 0) for c in clients))
    return {'generated_at': int(now), 'server': {'name': SERVER_NAME, 'version': SERVER_VERSION, 'started_at': int(state.SERVER_STARTED_AT), 'uptime_seconds': int(now - state.SERVER_STARTED_AT)}, 'connected_count': len(state.connected_clients), 'online_count': len(state.connected_clients), 'known_count': len(clients), 'transport_rate_limit': {'enabled': config.TRANSPORT_RATE_LIMIT_ENABLE, 'client_max': config.TRANSPORT_RATE_LIMIT_MAX, 'client_window_secs': config.TRANSPORT_RATE_LIMIT_WINDOW_SECS, 'global_max': config.TRANSPORT_GLOBAL_RATE_LIMIT_MAX, 'global_window_secs': config.TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS, 'global_count': len(state.transport_rx_times), 'dropped': state.transport_rate_dropped}, 'bridge_guards': {'dedupe_enabled': config.BRIDGE_DEDUPE_ENABLED, 'dedupe_ttl_sec': config.BRIDGE_DEDUPE_TTL_SECS, 'dedupe_max_entries': config.BRIDGE_DEDUPE_MAX_ENTRIES, 'dedupe_entries': len(state.packet_fingerprint_cache), 'counters': dict(state.bridge_guard_counters), 'loopguard_enabled': config.BRIDGE_LOOPGUARD_ENABLED, 'loopguard_window_sec': config.BRIDGE_LOOPGUARD_WINDOW_SECS, 'loopguard_threshold': config.BRIDGE_LOOPGUARD_THRESHOLD, 'loopguard_quarantine_sec': config.BRIDGE_LOOPGUARD_QUARANTINE_SECS, 'rf_inject_enabled': config.BRIDGE_RF_INJECT_ENABLED, 'rf_inject_max_per_min': config.BRIDGE_RF_INJECT_MAX_PER_MIN, 'rf_inject_max_airtime_ms_per_hour': config.BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR, 'rf_inject_block_duty_above_pct': config.BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT, 'group': config.BRIDGE_GROUP, 'require_group_match': config.BRIDGE_REQUIRE_GROUP_MATCH, 'short_id_quarantine_enabled': config.SHORT_ID_QUARANTINE_ENABLED, 'short_id_quarantine_window_sec': config.SHORT_ID_QUARANTINE_WINDOW_SECS, 'short_id_quarantine_threshold': config.SHORT_ID_QUARANTINE_THRESHOLD, 'short_id_quarantine_sec': config.SHORT_ID_QUARANTINE_SECS, 'short_id_quarantine': short_id_quarantine_snapshot(now), 'block_stats_poll_interval_sec': config.BLOCK_STATS_POLL_INTERVAL_SECS, 'node_block_active': node_block_active, 'path_block_active': path_block_active, 'node_block_drops': node_block_drops, 'path_block_drops': path_block_drops, 'block_drops': node_block_drops + path_block_drops}, 'firmware_release': dict(state.latest_firmware_info), 'clients': redact_public_value(clients)}


def locations_snapshot() -> dict:
    """Build the latest tracker location snapshot."""
    now = int(time.time())
    locations = []
    for report in state.latest_locations.values():
        item = dict(report)
        item['age_seconds'] = max(0, now - item['received_at'])
        item['track'] = list(state.latest_location_tracks.get(item['node_id'], ()))
        locations.append(item)
    locations.sort(key=lambda item: (item.get('name') or item['node_id']).lower())
    return {'generated_at': now, 'location_count': len(locations), 'locations': redact_public_value(locations)}


def sensors_snapshot() -> dict:
    """Build the latest sensor advert snapshot."""
    now = int(time.time())
    sensors = []
    for report in state.latest_sensors.values():
        item = dict(report)
        item['age_seconds'] = max(0, now - item['received_at'])
        item['node_id_short'] = item.get('node_id', '')[:2]
        sensors.append(item)
    sensors.sort(key=lambda item: (item.get('name') or item['node_id']).lower())
    return {'generated_at': now, 'sensor_count': len(sensors), 'sensors': redact_public_value(sensors)}


def packets_snapshot() -> dict:
    """Build the recent packet snapshot."""
    now = int(time.time())
    packets = []
    for entry in state.recent_packets:
        item = dict(entry)
        item['age_seconds'] = max(0, now - item['time'])
        packets.append(redact_public_value(item))
    return {'generated_at': now, 'packet_count': len(packets), 'packet_capacity': state.recent_packets.maxlen or len(packets), 'packet_total': state.packet_log_total, 'packets': packets}
