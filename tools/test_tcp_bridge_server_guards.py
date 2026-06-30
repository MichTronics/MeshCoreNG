#!/usr/bin/env python3
"""Smoke tests for TCP bridge server dedupe, loopguard, groups, and budgets."""

import asyncio
import hmac
import time

import bridge_server.config as config
import bridge_server.state as state
from collections import OrderedDict, deque
from bridge_server.protocol import (
    Cipher, algorithms, modes, hashlib,
    CONTROL_PREFIX, CONTROL_TYPE_BRIDGE_PACKET, BRIDGE_PACKET_VERSION, BRIDGE_PACKET_FLAG_RF_RX,
    PH_TYPE_SHIFT, ROUTE_TYPE_FLOOD, ROUTE_TYPE_TRANSPORT_FLOOD, PAYLOAD_TYPE_GRP_DATA,
    CIPHER_BLOCK_SIZE, CIPHER_MAC_SIZE,
    packet_identity_for_dedupe, packet_fingerprint, fingerprint_hex,
    short_id_label, decrement_bridge_ttl, parse_bridge_packet_envelope,
    parse_bridge_packet_error, parse_caps, parse_heartbeat,
    new_block_drop_counter_state, update_block_drop_counters, block_stats_totals,
    parse_block_stats_reply,
)
from bridge_server.constants import (
    CONTROL_TYPE_CAPS, CONTROL_TYPE_HEARTBEAT, DATA_TYPE_MESHCORENG_TRACKER,
    PAYLOAD_TYPE_LOCATION, DEFAULT_TRACKER_CHANNEL_SECRET, BLOCK_STATS_COUNTER_WINDOW_SECS,
)
from bridge_server.location import (
    channel_hash, load_public_channels, parse_mesh_location_payload,
    source_short_id,
)
from bridge_server.stats import (
    new_node_stats, get_node_stats, record_node_heartbeat,
    prune_packet_fingerprint_cache, packet_fingerprint_record,
    short_id_quarantine_active, record_short_id_bad_hit, short_id_quarantine_snapshot,
    inc_bridge_guard_counter,
)
from bridge_server.server import broadcast, status_snapshot
from bridge_server.client import BridgeClient


class DummyWriter:
    def __init__(self, name: str):
        self.name = name
        self.sent: list[bytes] = []

    def get_extra_info(self, key: str):
        return (self.name, 1) if key == "peername" else None

    def is_closing(self) -> bool:
        return False

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


def make_client(name: str) -> BridgeClient:
    client = BridgeClient(None, DummyWriter(name))
    client.node_name = name
    client.start_writer()
    return client


async def settle() -> None:
    await asyncio.sleep(0.05)


async def with_clients(*clients):
    old_clients = state.connected_clients
    old_cache = state.packet_fingerprint_cache
    state.connected_clients = set(clients)
    state.packet_fingerprint_cache = OrderedDict()
    try:
        yield
    finally:
        for client in clients:
            client.close()
        state.connected_clients = old_clients
        state.packet_fingerprint_cache = old_cache


def make_bridge_packet(mesh_payload: bytes, ttl: int = 2, origin_id: int = 0x12345678) -> bytes:
    return (
        CONTROL_PREFIX
        + bytes([CONTROL_TYPE_BRIDGE_PACKET, BRIDGE_PACKET_VERSION, ttl & 0xFF])
        + origin_id.to_bytes(4, "big")
        + bytes([BRIDGE_PACKET_FLAG_RF_RX])
        + len(mesh_payload).to_bytes(2, "big")
        + mesh_payload
    )


async def test_basic_dedupe() -> None:
    a, b, c = make_client("A"), make_client("B"), make_client("C")
    async for _ in with_clients(a, b, c):
        payload = b"mesh-one"
        await broadcast(payload, sender=a)
        await settle()
        assert len(a.writer.sent) == 0
        assert len(b.writer.sent) == 1
        assert len(c.writer.sent) == 1

        await broadcast(payload, sender=b)
        await settle()
        assert len(a.writer.sent) == 0
        assert len(c.writer.sent) == 1
        assert a.skip_reasons.get("skipped_dup_seen_by_target", 0) == 1
        assert c.skip_reasons.get("skipped_dup_seen_by_target", 0) == 1


async def test_dedupe_ignores_bridge_export_path() -> None:
    header = (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD
    app_payload = b"same-encrypted-group-data"
    one_hop_path = bytes([header, 1, 0xA1]) + app_payload
    two_hop_path = bytes([header, 2, 0xB2, 0xC3]) + app_payload
    different_payload = bytes([header, 1, 0xA1]) + b"different-group-data"

    assert packet_identity_for_dedupe(one_hop_path) == packet_identity_for_dedupe(two_hop_path)
    assert packet_fingerprint(one_hop_path) == packet_fingerprint(two_hop_path)
    assert packet_fingerprint(one_hop_path) != packet_fingerprint(different_payload)

    client = make_client("seen")
    try:
        client.mark_seen_payload(one_hop_path)
        assert client.has_seen_payload(two_hop_path)
        assert not client.has_seen_payload(different_payload)
    finally:
        client.close()


async def test_dedupe_ignores_bridge_envelope_and_transport_codes() -> None:
    header = (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT) | ROUTE_TYPE_TRANSPORT_FLOOD
    app_payload = b"same-transport-group-data"
    first_codes = b"\x11\x22\x33\x44"
    second_codes = b"\xAA\xBB\xCC\xDD"
    transport_a = bytes([header]) + first_codes + bytes([1, 0xA1]) + app_payload
    transport_b = bytes([header]) + second_codes + bytes([2, 0xB2, 0xC3]) + app_payload

    def bridge_packet(mesh_payload: bytes, ttl: int, origin: int, flags: int) -> bytes:
        return (
            b"MCNG"
            + bytes([CONTROL_TYPE_BRIDGE_PACKET, BRIDGE_PACKET_VERSION, ttl])
            + origin.to_bytes(4, "big")
            + bytes([flags])
            + len(mesh_payload).to_bytes(2, "big")
            + mesh_payload
        )

    wrapped_a = bridge_packet(transport_a, ttl=8, origin=0x01020304, flags=0x01)
    wrapped_b = bridge_packet(transport_b, ttl=3, origin=0xA0B0C0D0, flags=0x00)

    assert packet_identity_for_dedupe(wrapped_a) == packet_identity_for_dedupe(wrapped_b)
    assert packet_fingerprint(wrapped_a) == packet_fingerprint(wrapped_b)
    assert packet_fingerprint(wrapped_a) == packet_fingerprint(transport_a)


async def test_ttl_allows_resend() -> None:
    old_ttl = config.BRIDGE_DEDUPE_TTL_SECS
    config.BRIDGE_DEDUPE_TTL_SECS = 1
    a, b = make_client("A"), make_client("B")
    async for _ in with_clients(a, b):
        payload = b"mesh-two"
        await broadcast(payload, sender=a)
        await settle()
        assert len(b.writer.sent) == 1
        await asyncio.sleep(1.1)
        await broadcast(payload, sender=a)
        await settle()
        assert len(b.writer.sent) == 2
    config.BRIDGE_DEDUPE_TTL_SECS = old_ttl


async def test_group_mismatch() -> None:
    old_require = config.BRIDGE_REQUIRE_GROUP_MATCH
    config.BRIDGE_REQUIRE_GROUP_MATCH = True
    a, b = make_client("A"), make_client("B")
    a.bridge_group = "alpha"
    b.bridge_group = "beta"
    async for _ in with_clients(a, b):
        await broadcast(b"mesh-three", sender=a)
        await settle()
        assert len(b.writer.sent) == 0
        assert b.skip_reasons.get("skipped_group_mismatch", 0) == 1
    config.BRIDGE_REQUIRE_GROUP_MATCH = old_require


async def test_quarantine() -> None:
    a, b = make_client("A"), make_client("B")
    async for _ in with_clients(a, b):
        b.quarantined_until = time.time() + 60
        await broadcast(b"mesh-four", sender=a)
        await settle()
        assert len(b.writer.sent) == 0
        assert b.skip_reasons.get("skipped_quarantine", 0) == 1

        b.quarantined_until = time.time() - 1
        assert not b.quarantine_active()
        assert b.skip_reasons.get("bridge_quarantine_end", 0) == 1


async def test_rf_budget_drop() -> None:
    old_enabled = config.BRIDGE_RF_INJECT_ENABLED
    old_max = config.BRIDGE_RF_INJECT_MAX_PER_MIN
    config.BRIDGE_RF_INJECT_ENABLED = True
    config.BRIDGE_RF_INJECT_MAX_PER_MIN = 1
    a, b = make_client("A"), make_client("B")
    async for _ in with_clients(a, b):
        await broadcast(b"mesh-five-a", sender=a)
        await settle()
        await broadcast(b"mesh-five-b", sender=a)
        await settle()
        assert len(b.writer.sent) == 1
        assert b.skip_reasons.get("skipped_rf_inject_budget", 0) == 1
    config.BRIDGE_RF_INJECT_ENABLED = old_enabled
    config.BRIDGE_RF_INJECT_MAX_PER_MIN = old_max


async def test_bridge_ttl_zero_dropped() -> None:
    a, b = make_client("A"), make_client("B")
    async for _ in with_clients(a, b):
        await broadcast(make_bridge_packet(b"mesh-ttl-zero", ttl=0), sender=a)
        await settle()
        assert len(b.writer.sent) == 0
        assert a.skip_reasons.get("skipped_ttl_expired", 0) == 1


async def test_bridge_origin_not_sent_back_to_origin_id() -> None:
    a, b = make_client("A"), make_client("B")
    a.bridge_id = 0xAABBCCDD
    b.bridge_id = 0x01020304
    async for _ in with_clients(a, b):
        await broadcast(make_bridge_packet(b"mesh-origin", ttl=2, origin_id=a.bridge_id), sender=b)
        await settle()
        assert len(a.writer.sent) == 0
        assert a.skip_reasons.get("skipped_own_origin", 0) == 1


async def test_bridge_packet_does_not_bounce_forever() -> None:
    a, b = make_client("A"), make_client("B")
    a.bridge_id = 0x11111111
    b.bridge_id = 0x22222222
    payload = make_bridge_packet(b"mesh-bounce", ttl=2, origin_id=a.bridge_id)
    async for _ in with_clients(a, b):
        await broadcast(payload, sender=a)
        await settle()
        assert len(b.writer.sent) == 1
        forwarded_payload = decrement_bridge_ttl(payload)
        assert forwarded_payload is not None
        await broadcast(forwarded_payload, sender=b)
        await settle()
        assert len(a.writer.sent) == 0
        assert b.skip_reasons.get("skipped_ttl_expired", 0) == 1


async def test_unknown_bridge_packet_version_is_parse_error() -> None:
    payload = bytearray(make_bridge_packet(b"mesh-future", ttl=2))
    payload[5] = 99
    assert parse_bridge_packet_envelope(bytes(payload)) is None
    assert "unsupported bridge packet version 99" == parse_bridge_packet_error(bytes(payload))


async def test_raw_frame_still_passes_through() -> None:
    a, b = make_client("A"), make_client("B")
    async for _ in with_clients(a, b):
        await broadcast(b"legacy-raw-frame", sender=a)
        await settle()
        assert len(b.writer.sent) == 1


async def test_caps_v2_group_budget() -> None:
    group = b"GWNL"
    payload = (
        b"MCNG"
        + bytes([CONTROL_TYPE_CAPS, 2, 0x07, 2, len(group)])
        + group
        + bytes([1])
        + (120).to_bytes(2, "big")
        + (360000).to_bytes(4, "big")
        + (7500).to_bytes(2, "big")
    )
    caps = parse_caps(payload)
    assert caps is not None
    assert caps["bridge_v2"] is True
    assert caps["bridge_proto_ver"] == 2
    assert caps["group"] == "GWNL"
    assert caps["rf_inject_budget_enabled"] is True
    assert caps["rf_inject_max_per_min"] == 120
    assert caps["rf_inject_max_airtime_ms_per_hour"] == 360000
    assert caps["rf_inject_block_duty_above_pct"] == 75.0


async def test_caps_v3_bridge_id() -> None:
    group = b"GWNL"
    payload = (
        b"MCNG"
        + bytes([CONTROL_TYPE_CAPS, 3, 0x0F, 3, len(group)])
        + group
        + bytes([1])
        + (120).to_bytes(2, "big")
        + (360000).to_bytes(4, "big")
        + (7500).to_bytes(2, "big")
        + (0xAABBCCDD).to_bytes(4, "big")
    )
    caps = parse_caps(payload)
    assert caps is not None
    assert caps["bridge_v2"] is True
    assert caps["bridge_proto_ver"] == 3
    assert caps["bridge_id"] == 0xAABBCCDD


async def test_heartbeat_radio_stats_parse() -> None:
    payload = (
        b"MCNG"
        + bytes([CONTROL_TYPE_HEARTBEAT])
        + (1234).to_bytes(4, "big")
        + b"RF"
        + bytes([2])
        + (1000).to_bytes(4, "big")
        + (360000).to_bytes(4, "big")
        + (3600000).to_bytes(4, "big")
        + (1000).to_bytes(2, "big")
        + (25).to_bytes(2, "big")
        + (5000).to_bytes(4, "big")
        + b"RS"
        + bytes([1])
        + int(-118).to_bytes(2, "big", signed=True)
        + int(-73).to_bytes(2, "big", signed=True)
        + int(31).to_bytes(1, "big", signed=True)
        + b"NB"
        + bytes([1])
        + (12).to_bytes(2, "big")
        + b"HL"
        + bytes([1])
        + (345).to_bytes(4, "big")
    )
    heartbeat = parse_heartbeat(payload)
    assert heartbeat is not None
    assert heartbeat["uptime_ms"] == 1234
    assert heartbeat["radio_stats"]["noise_floor"] == -118
    assert heartbeat["radio_stats"]["last_rssi"] == -73
    assert heartbeat["radio_stats"]["last_snr"] == 7.75
    assert heartbeat["neighbor_count"] == 12
    assert heartbeat["flood_hop_limit_drops"] == 345


async def test_status_hides_unnamed_offline_placeholders() -> None:
    old_clients = state.connected_clients
    old_stats = state.node_traffic_stats
    now = time.time()
    state.connected_clients = set()
    state.node_traffic_stats = {
        "host:placeholder": {
            "key": "host:placeholder",
            "rx_times": deque([now]),
            "tx_times": deque(),
            "packets_rx": 1,
            "packets_tx": 0,
            "heartbeats_rx": 0,
            "first_seen": now,
            "last_seen": now,
            "last_connected": now,
            "last_disconnect": now,
            "last_heartbeat": 0.0,
            "last_heartbeat_uptime_ms": 0,
            "rf_duty": {},
            "rf_tx_total_baseline_ms": None,
            "rf_tx_total_baseline_at": 0.0,
            "node_name": "",
            "node_id": "",
            "firmware_version": "",
            "supports_bridge_v2": False,
            "bridge_proto_ver": 1,
            "bridge_group": "default",
            "node_rf_inject_budget": {},
            "connected": False,
            "client_id": "",
            "addr": "",
        },
        "name:real": {
            **new_node_stats("name:real"),
            "node_name": "real",
            "last_seen": now,
        },
    }
    try:
        snapshot = status_snapshot()
        names = [client["display_name"] for client in snapshot["clients"]]
        assert "unnamed bridge node" not in names
        assert "real" in names
    finally:
        state.connected_clients = old_clients
        state.node_traffic_stats = old_stats


async def test_rf_duty_hour_counter_resets() -> None:
    old_stats = state.node_traffic_stats
    state.node_traffic_stats = {}
    client = make_client("Duty")
    heartbeat = {
        "uptime_ms": 1,
        "rf_duty": {
            "tx_used_ms": 1200,
            "tx_max_ms": 360000,
            "window_ms": 3600000,
            "duty_limit_pct": 10.0,
            "tx_used_pct": 0.33,
            "tx_total_ms": 100000,
        },
    }
    try:
        record_node_heartbeat(client, heartbeat, now=3610)
        rf = get_node_stats(client)["rf_duty"]
        assert rf["tx_used_ms"] == 1200
        assert rf["tx_hour_used_ms"] == 0

        heartbeat["rf_duty"]["tx_used_ms"] = 2400
        heartbeat["rf_duty"]["tx_total_ms"] = 105000
        record_node_heartbeat(client, heartbeat, now=3620)
        rf = get_node_stats(client)["rf_duty"]
        assert rf["tx_used_ms"] == 2400
        assert rf["tx_hour_used_ms"] == 5000

        heartbeat["rf_duty"]["tx_used_ms"] = 3000
        heartbeat["rf_duty"]["tx_total_ms"] = 110000
        record_node_heartbeat(client, heartbeat, now=7205)
        rf = get_node_stats(client)["rf_duty"]
        assert rf["tx_used_ms"] == 3000
        assert rf["tx_hour_used_ms"] == 0
        assert rf["tx_hour_resets_in_seconds"] == 3595
    finally:
        client.close()
        state.node_traffic_stats = old_stats


def encrypt_group_plain(secret: bytes, plain: bytes) -> bytes:
    assert Cipher is not None
    padded_len = ((len(plain) + CIPHER_BLOCK_SIZE - 1) // CIPHER_BLOCK_SIZE) * CIPHER_BLOCK_SIZE
    padded = plain + (b"\x00" * (padded_len - len(plain)))
    cipher = Cipher(algorithms.AES(secret[:16]), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    mac = hmac.new(secret, encrypted, hashlib.sha256).digest()[:CIPHER_MAC_SIZE]
    return mac + encrypted


async def test_group_tracker_location_decode() -> None:
    if Cipher is None:
        return
    old_channels = state.public_channels
    state.public_channels = []
    load_public_channels("")
    try:
        tracker = (
            b"MCL1"
            + bytes([1, 0])
            + bytes.fromhex("01020304")
            + int(52123456).to_bytes(4, "big", signed=True)
            + int(5123456).to_bytes(4, "big", signed=True)
            + int(12).to_bytes(2, "big", signed=True)
            + int(345).to_bytes(2, "big")
            + int(9000).to_bytes(2, "big")
            + bytes([7])
            + int(4100).to_bytes(2, "big")
            + int(1780000000).to_bytes(4, "big")
            + bytes([4])
            + b"bike"
        )
        plain = (
            int(DATA_TYPE_MESHCORENG_TRACKER).to_bytes(2, "little")
            + bytes([len(tracker)])
            + tracker
        )
        encrypted = encrypt_group_plain(DEFAULT_TRACKER_CHANNEL_SECRET, plain)
        payload = bytes([(PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD])
        payload += bytes([0])
        payload += channel_hash(DEFAULT_TRACKER_CHANNEL_SECRET)
        payload += encrypted

        report = parse_mesh_location_payload(payload)
        assert report is not None
        assert report["node_id"] == "01020304"
        assert report["lat"] == 52.123456
        assert report["lon"] == 5.123456
        assert report["altitude_m"] == 12
        assert report["speed_cms"] == 345
        assert report["heading_cdeg"] == 9000
        assert report["satellites"] == 7
        assert report["battery_mv"] == 4100
        assert report["name"] == "bike"
        assert report["payload_type"] == PAYLOAD_TYPE_GRP_DATA
    finally:
        state.public_channels = old_channels


async def test_short_id_quarantine_blocks_broadcast() -> None:
    old_enabled = config.SHORT_ID_QUARANTINE_ENABLED
    old_quarantine = state.short_id_quarantine
    old_hits = state.short_id_bad_hits
    config.SHORT_ID_QUARANTINE_ENABLED = True
    state.short_id_quarantine = {}
    state.short_id_bad_hits = {}
    a, b = make_client("A"), make_client("B")
    header = (PAYLOAD_TYPE_GRP_DATA << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD
    payload = bytes([header, 0, 0xA7, 0x01, 0x02])
    try:
        assert source_short_id(payload) == 0xA7
        state.short_id_quarantine[0xA7] = {
            "id": 0xA7,
            "until": time.time() + 60,
            "reason": "test",
            "source": "test",
            "hits": 1,
        }
        async for _ in with_clients(a, b):
            await broadcast(payload, sender=a)
            await settle()
            assert len(b.writer.sent) == 0
            assert a.skip_reasons.get("skipped_short_id_quarantine", 0) == 1
    finally:
        a.close()
        b.close()
        config.SHORT_ID_QUARANTINE_ENABLED = old_enabled
        state.short_id_quarantine = old_quarantine
        state.short_id_bad_hits = old_hits


async def test_short_id_bad_hits_start_quarantine() -> None:
    old_enabled = config.SHORT_ID_QUARANTINE_ENABLED
    old_threshold = config.SHORT_ID_QUARANTINE_THRESHOLD
    old_window = config.SHORT_ID_QUARANTINE_WINDOW_SECS
    old_secs = config.SHORT_ID_QUARANTINE_SECS
    old_quarantine = state.short_id_quarantine
    old_hits = state.short_id_bad_hits
    client = make_client("Noisy")
    try:
        config.SHORT_ID_QUARANTINE_ENABLED = True
        config.SHORT_ID_QUARANTINE_THRESHOLD = 2
        config.SHORT_ID_QUARANTINE_WINDOW_SECS = 60
        config.SHORT_ID_QUARANTINE_SECS = 30
        state.short_id_quarantine = {}
        state.short_id_bad_hits = {}
        now = time.time()
        record_short_id_bad_hit(0xA7, "duplicate", client, now)
        assert not short_id_quarantine_active(0xA7, now)
        record_short_id_bad_hit(0xA7, "duplicate", client, now + 1)
        assert short_id_quarantine_active(0xA7, now + 1)
        snapshot = short_id_quarantine_snapshot(now + 1)
        assert snapshot[0]["id"] == "0xa7"
    finally:
        client.close()
        config.SHORT_ID_QUARANTINE_ENABLED = old_enabled
        config.SHORT_ID_QUARANTINE_THRESHOLD = old_threshold
        config.SHORT_ID_QUARANTINE_WINDOW_SECS = old_window
        config.SHORT_ID_QUARANTINE_SECS = old_secs
        state.short_id_quarantine = old_quarantine
        state.short_id_bad_hits = old_hits


async def test_block_stats_reply_parser() -> None:
    path_entries = parse_block_stats_reply("path", "> aa/bb 123s 5; cc 2m 7")
    assert path_entries == [
        {"kind": "path", "value": "aa/bb", "seconds_left": 123, "drops": 5},
        {"kind": "path", "value": "cc", "seconds_left": 120, "drops": 7},
    ]
    node_entries = parse_block_stats_reply("node", "> node.block a7:45s; b2:1m")
    assert node_entries == [
        {"kind": "node", "value": "a7", "seconds_left": 45, "drops": 0},
        {"kind": "node", "value": "b2", "seconds_left": 60, "drops": 0},
    ]
    totals = block_stats_totals({"node": node_entries, "path": path_entries})
    assert totals["node_active"] == 2
    assert totals["path_active"] == 2
    assert totals["path_drops"] == 12
    assert totals["total_drops"] == 12


async def test_block_stats_24h_counter_is_monotonic_until_window_reset() -> None:
    state = new_block_drop_counter_state(now=1000)
    stats = {
        "node": [{"kind": "node", "value": "a7", "seconds_left": 45, "drops": 5}],
        "path": [{"kind": "path", "value": "aa/bb", "seconds_left": 123, "drops": 2}],
    }
    state = update_block_drop_counters(state, stats, now=1000)
    totals = block_stats_totals(stats)
    assert totals["node_drops"] == 5
    assert totals["path_drops"] == 2

    stats = {
        "node": [{"kind": "node", "value": "a7", "seconds_left": 30, "drops": 8}],
        "path": [{"kind": "path", "value": "aa/bb", "seconds_left": 90, "drops": 0}],
    }
    state = update_block_drop_counters(state, stats, now=1010)
    totals = block_stats_totals(stats)
    assert totals["node_drops"] == 8
    assert totals["path_drops"] == 2

    stats = {
        "node": [{"kind": "node", "value": "a7", "seconds_left": 20, "drops": 1}],
        "path": [{"kind": "path", "value": "aa/bb", "seconds_left": 80, "drops": 4}],
    }
    state = update_block_drop_counters(state, stats, now=1020)
    totals = block_stats_totals(stats)
    assert totals["node_drops"] == 9
    assert totals["path_drops"] == 6

    stats = {
        "node": [{"kind": "node", "value": "a7", "seconds_left": 20, "drops": 3}],
        "path": [{"kind": "path", "value": "aa/bb", "seconds_left": 80, "drops": 1}],
    }
    state = update_block_drop_counters(
        state,
        stats,
        now=1000 + BLOCK_STATS_COUNTER_WINDOW_SECS + 1,
    )
    totals = block_stats_totals(stats)
    assert totals["node_drops"] == 3
    assert totals["path_drops"] == 1


async def main() -> None:
    await test_basic_dedupe()
    await test_dedupe_ignores_bridge_export_path()
    await test_dedupe_ignores_bridge_envelope_and_transport_codes()
    await test_ttl_allows_resend()
    await test_group_mismatch()
    await test_quarantine()
    await test_rf_budget_drop()
    await test_bridge_ttl_zero_dropped()
    await test_bridge_origin_not_sent_back_to_origin_id()
    await test_bridge_packet_does_not_bounce_forever()
    await test_unknown_bridge_packet_version_is_parse_error()
    await test_raw_frame_still_passes_through()
    await test_caps_v2_group_budget()
    await test_caps_v3_bridge_id()
    await test_heartbeat_radio_stats_parse()
    await test_status_hides_unnamed_offline_placeholders()
    await test_rf_duty_hour_counter_resets()
    await test_group_tracker_location_decode()
    await test_short_id_quarantine_blocks_broadcast()
    await test_short_id_bad_hits_start_quarantine()
    await test_block_stats_reply_parser()
    await test_block_stats_24h_counter_is_monotonic_until_window_reset()
    print("tcp bridge guard smoke tests passed")


if __name__ == "__main__":
    asyncio.run(main())
