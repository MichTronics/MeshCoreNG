"""TCP bridge client state and I/O helpers."""

import asyncio
from collections import deque
import hashlib
import logging
import struct
import time

import bridge_server.config as config
import bridge_server.state as state
from bridge_server.constants import BRIDGE_MAGIC, CONTROL_PREFIX, CONTROL_TYPE_COMMAND, MAX_PAYLOAD
from bridge_server.firmware import firmware_update_status
from bridge_server.protocol import (
    block_stats_totals,
    fletcher16,
    fingerprint_hex,
    format_duration,
    mesh_payload_for_parsing,
    new_block_drop_counter_state,
    packet_fingerprint,
    packet_identity_for_dedupe,
    prune_packet_times,
    prune_rate_window,
)
from bridge_server.stats import get_node_stats, node_stats_status_dict, packet_fingerprint_record, record_node_packet

log = logging.getLogger("tcp_bridge")


class BridgeClient:
    """Represents a single connected TCP bridge repeater node.

    Manages per-client packet I/O, a dedicated async TX writer queue,
    RF-inject budgeting, loop-guard scoring, dedupe seen-payload tracking,
    and block statistics polling.
    """
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Initialize the bridge client state."""
        self.reader = reader
        self.writer = writer
        addr = writer.get_extra_info('peername')
        self.host = addr[0] if addr else 'unknown'
        self.port = addr[1] if addr else 0
        self.addr = f'{self.host}:{self.port}' if addr else 'unknown'
        self._client_id = f'client-{state.next_client_id}'
        state.next_client_id += 1
        self.packets_rx = 0
        self.packets_tx = 0
        self.packet_rx_times: deque[float] = deque()
        self.packet_tx_times: deque[float] = deque()
        self.transport_rx_times: deque[float] = deque()
        self.transport_rate_dropped = 0
        self._stats_key = ''
        self.heartbeats_rx = 0
        self._connect_time = time.time()
        self.last_seen = self._connect_time
        self.last_heartbeat = 0.0
        self.last_heartbeat_uptime_ms = 0
        self.rf_duty: dict = {}
        self.radio_stats: dict = {}
        self.neighbor_count: int | None = None
        self.flood_hop_limit_drops = 0
        self.node_name = ''
        self.node_id = ''
        self.firmware_version = ''
        self.supports_bridge_v2 = False
        self.bridge_proto_ver = 1
        self.bridge_id = 0
        self.authenticated = not config.BRIDGE_PASSWORD
        self.bridge_group = config.BRIDGE_GROUP
        self.node_rf_inject_budget: dict = {}
        self._seen_hash_seen_at: dict[bytes, float] = {}
        self._seen_hash_deque: deque[tuple[bytes, float]] = deque(maxlen=256)
        self.tx_queue: asyncio.Queue[tuple[bytes, str, bytes | None]] = asyncio.Queue(maxsize=config.CLIENT_TX_QUEUE_MAX)
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
        self.block_stats: dict = {'path': [], 'node': [], 'updated_at': 0.0, 'error': ''}
        self.block_drop_counter_state: dict = new_block_drop_counter_state(self._connect_time)
        self._block_stats_polling = False
        self._last_block_stats_poll = 0.0
        self.last_fingerprint = 0
        self.last_fingerprint_at = 0.0
        self.last_tx_queue_drop = 0.0
        self.last_tx_send = 0.0
        self.last_tx_error = ''

    @property
    def display_name(self) -> str:
        """Return the display name for the client."""
        return self.node_name or 'unnamed bridge node'

    @property
    def client_id(self) -> str:
        """Return the stable client ID."""
        return self._client_id

    @property
    def bridge_key(self) -> str:
        """Return the dedupe key for the client."""
        if self.node_id:
            return f'node:{self.node_id.strip().lower()}'
        if self.node_name:
            return f'name:{self.node_name.strip().lower()}'
        return self.client_id

    def record_skip(self, reason: str) -> None:
        """Record a skipped forwarding reason."""
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1
        if reason.startswith('skipped_dup') and reason != 'skipped_dup_loopback':
            self.tx_skipped_duplicates += 1

    def quarantine_active(self, now: float | None=None) -> bool:
        """Return whether the client is quarantined."""
        now = now or time.time()
        if self.quarantined_until > now:
            return True
        if self.quarantined_until:
            self.quarantined_until = 0.0
            self.record_skip('bridge_quarantine_end')
            log.warning('%s: bridge_quarantine_end', self.addr)
        return False

    def record_loopguard_hit(self, now: float | None=None) -> None:
        """Record a loopguard hit for the client."""
        now = now or time.time()
        if self.loop_last_hit and now - self.loop_last_hit > max(1, config.BRIDGE_LOOPGUARD_WINDOW_SECS):
            self.loop_score = 0
        self.loop_last_hit = now
        self.loop_score += 1
        self.record_skip('loopguard_hit')
        log.warning('%s: loopguard_hit score=%d', self.addr, self.loop_score)
        if self.loop_score >= max(1, config.BRIDGE_LOOPGUARD_THRESHOLD):
            self.quarantined_until = now + max(1, config.BRIDGE_LOOPGUARD_QUARANTINE_SECS)
            self.record_skip('bridge_quarantine_start')
            log.warning('%s: bridge_quarantine_start %ds', self.addr, config.BRIDGE_LOOPGUARD_QUARANTINE_SECS)

    def estimated_airtime_ms(self, payload: bytes) -> int:
        """Estimate airtime for a forwarded payload."""
        return max(1, len(mesh_payload_for_parsing(payload)) * 10)

    def prune_rf_inject_budget(self, now: float | None=None) -> None:
        """Prune expired RF inject budget samples."""
        now = now or time.time()
        while self.rf_inject_minute_times and self.rf_inject_minute_times[0] < now - 60:
            self.rf_inject_minute_times.popleft()
        while self.rf_inject_airtime_times and self.rf_inject_airtime_times[0][0] < now - 3600:
            self.rf_inject_airtime_times.popleft()

    def rf_inject_airtime_used_ms(self, now: float | None=None) -> int:
        """Return recent RF inject airtime usage."""
        self.prune_rf_inject_budget(now)
        return sum((ms for _, ms in self.rf_inject_airtime_times))

    def rf_inject_budget_remaining_ms(self, now: float | None=None) -> int | None:
        """Return remaining RF inject airtime budget."""
        if config.BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR <= 0:
            return None
        return max(0, config.BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR - self.rf_inject_airtime_used_ms(now))

    def can_accept_rf_inject(self, payload: bytes, now: float | None=None) -> tuple[bool, str]:
        """Return whether the client can accept RF inject traffic."""
        if not config.BRIDGE_RF_INJECT_ENABLED:
            return (True, '')
        now = now or time.time()
        self.prune_rf_inject_budget(now)
        if config.BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT > 0:
            used_pct = float((self.rf_duty or {}).get('tx_used_pct') or 0.0)
            if used_pct >= config.BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT:
                return (False, 'skipped_rf_inject_budget')
        if config.BRIDGE_RF_INJECT_MAX_PER_MIN > 0 and len(self.rf_inject_minute_times) >= config.BRIDGE_RF_INJECT_MAX_PER_MIN:
            return (False, 'skipped_rf_inject_budget')
        estimate = self.estimated_airtime_ms(payload)
        remaining = self.rf_inject_budget_remaining_ms(now)
        if remaining is not None and estimate > remaining:
            return (False, 'skipped_rf_inject_budget')
        return (True, '')

    def record_rf_inject(self, payload: bytes, now: float | None=None) -> None:
        """Record RF inject budget usage for a payload."""
        if not config.BRIDGE_RF_INJECT_ENABLED:
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
        """Mark a payload as recently seen."""
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

    def prune_seen_payloads(self, now: float | None=None) -> None:
        """Prune expired recently seen payload markers."""
        now = now or time.time()
        cutoff = now - max(1, config.BRIDGE_DEDUPE_TTL_SECS)
        while self._seen_hash_deque and self._seen_hash_deque[0][1] < cutoff:
            oldest, seen_at = self._seen_hash_deque.popleft()
            if self._seen_hash_seen_at.get(oldest) == seen_at:
                self._seen_hash_seen_at.pop(oldest, None)

    def learn_node_id(self, node_id: str, name: str='') -> None:
        """Learn a more complete node ID for the client."""
        node_id = (node_id or '').strip().lower()
        if not node_id:
            return
        name = (name or '').strip()
        if self.node_name and name and (name != self.node_name):
            return
        if not self.node_id or len(node_id) > len(self.node_id) or name == self.node_name:
            self.node_id = node_id

    def status_dict(self, now: float) -> dict:
        """Build the status payload for the client."""
        stats = get_node_stats(self)
        prune_packet_times(stats['rx_times'], now)
        prune_packet_times(stats['tx_times'], now)
        prune_packet_times(self.packet_rx_times, now)
        prune_packet_times(self.packet_tx_times, now)
        prune_rate_window(self.transport_rx_times, now, config.TRANSPORT_RATE_LIMIT_WINDOW_SECS)
        status = node_stats_status_dict(stats, now)
        status.update({'name': self.node_name, 'id': self.client_id, 'node_id': self.node_id, 'firmware_version': self.firmware_version, 'firmware_update': firmware_update_status(self.firmware_version), 'display_name': self.display_name, 'connected': True, 'connected_seconds': int(now - self._connect_time), 'connected_for': format_duration(now - self._connect_time), 'idle_seconds': int(now - self.last_seen), 'heartbeat_age_seconds': int(now - self.last_heartbeat) if self.last_heartbeat else None, 'heartbeat_uptime_ms': self.last_heartbeat_uptime_ms, 'rf_duty': dict(self.rf_duty), 'radio_stats': dict(self.radio_stats), 'neighbor_count': self.neighbor_count if self.neighbor_count is not None else stats.get('neighbor_count'), 'flood_hop_limit_drops': self.flood_hop_limit_drops or stats.get('flood_hop_limit_drops', 0), 'packets_rx': self.packets_rx, 'packets_tx': self.packets_tx, 'packets_rx_24h': len(stats['rx_times']), 'packets_tx_24h': len(stats['tx_times']), 'transport_rx_window': len(self.transport_rx_times), 'transport_rate_dropped': self.transport_rate_dropped, 'tx_queue_depth': self.tx_queue.qsize(), 'tx_queue_max': config.CLIENT_TX_QUEUE_MAX, 'tx_queue_high_water': self.tx_queue_high_water, 'tx_queued': self.tx_queued, 'tx_queue_dropped': self.tx_queue_dropped, 'tx_send_errors': self.tx_send_errors, 'tx_skipped_duplicates': self.tx_skipped_duplicates, 'skipped_dup_total': self.tx_skipped_duplicates, 'skipped_dup_by_reason': dict(self.skip_reasons), 'loop_score': self.loop_score, 'quarantine_active': self.quarantine_active(now), 'quarantine_seconds_left': max(0, int(self.quarantined_until - now)) if self.quarantined_until else 0, 'group': self.bridge_group, 'bridge_proto_ver': self.bridge_proto_ver, 'bridge_id': f'0x{self.bridge_id:08x}' if self.bridge_id else '', 'node_rf_inject_budget': dict(self.node_rf_inject_budget), 'block_stats': dict(self.block_stats), 'block_stats_totals': block_stats_totals(self.block_stats), 'last_fingerprint': fingerprint_hex(self.last_fingerprint) if self.last_fingerprint else '', 'last_fingerprint_age_seconds': int(now - self.last_fingerprint_at) if self.last_fingerprint_at else None, 'rf_inject_budget_remaining_ms': self.rf_inject_budget_remaining_ms(now), 'rf_budget_drops': self.rf_budget_drops, 'bridge_quality_score': max(0, 100 - min(60, self.loop_score * 10) - min(25, self.tx_queue_dropped) - min(25, self.tx_send_errors * 5) - min(25, self.rf_budget_drops * 2)), 'last_tx_age_seconds': int(now - self.last_tx_send) if self.last_tx_send else None, 'last_tx_queue_drop_age_seconds': int(now - self.last_tx_queue_drop) if self.last_tx_queue_drop else None, 'last_tx_error': self.last_tx_error, 'heartbeats_rx': self.heartbeats_rx, 'authenticated': self.authenticated, 'supports_bridge_v2': self.supports_bridge_v2, 'bridge_proto_ver': self.bridge_proto_ver})
        return status

    async def read_packet(self) -> bytes | None:
        """Read one framed packet from the stream. Returns raw payload bytes or None on error."""
        buf = bytearray()
        while True:
            b = await self.reader.readexactly(1)
            buf.append(b[0])
            if len(buf) >= 2:
                if buf[-2] == BRIDGE_MAGIC >> 8 & 255 and buf[-1] == BRIDGE_MAGIC & 255:
                    break
                buf = bytearray([buf[-1]])
        raw_len = await self.reader.readexactly(2)
        length = struct.unpack('>H', raw_len)[0]
        if length == 0 or length > MAX_PAYLOAD:
            log.warning('%s: invalid payload length %d, discarding', self.addr, length)
            return None
        payload = await self.reader.readexactly(length)
        raw_csum = await self.reader.readexactly(2)
        received_csum = struct.unpack('>H', raw_csum)[0]
        calculated_csum = fletcher16(payload)
        if received_csum != calculated_csum:
            log.warning('%s: checksum mismatch (got 0x%04x, expected 0x%04x)', self.addr, received_csum, calculated_csum)
            return None
        self.last_seen = time.time()
        return bytes(payload)

    def build_frame(self, payload: bytes) -> bytes:
        """Wrap a payload in the bridge framing."""
        length = len(payload)
        csum = fletcher16(payload)
        return struct.pack('>H', BRIDGE_MAGIC) + struct.pack('>H', length) + payload + struct.pack('>H', csum)

    def start_writer(self) -> None:
        """Start the background transmit task if needed."""
        if self.tx_queue_task is None or self.tx_queue_task.done():
            self.tx_queue_task = asyncio.create_task(self._tx_writer_loop())

    def enqueue_payload(self, payload: bytes, source: str='', seen_payload: bytes | None=None) -> bool:
        """Queue a payload for asynchronous transmit."""
        if self.writer.is_closing():
            self.tx_send_errors += 1
            self.last_tx_error = 'writer closing'
            return False
        item = (bytes(payload), source, bytes(seen_payload) if seen_payload is not None else None)
        if self.tx_queue.full():
            # Drop the oldest queued payload so fresh traffic can still move.
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
        """Drain queued payloads to the socket."""
        try:
            while True:
                payload, source, seen_payload = await self.tx_queue.get()
                try:
                    ok = await self._send_payload_now(payload, source=source, seen_payload=seen_payload)
                    if not ok:
                        from bridge_server.server import disconnect
                        await disconnect(self, reason='send error')
                        return
                finally:
                    self.tx_queue.task_done()
        except asyncio.CancelledError:
            return

    async def send_payload(self, payload: bytes, source: str='') -> bool:
        """Queue a payload for normal bridge delivery."""
        return self.enqueue_payload(payload, source=source)

    async def send_control_payload(self, payload: bytes) -> bool:
        """Send a control payload immediately."""
        if self.writer.is_closing():
            self.tx_send_errors += 1
            self.last_tx_error = 'writer closing'
            return False
        try:
            self.writer.write(self.build_frame(payload))
            await self.writer.drain()
            self.last_tx_error = ''
            return True
        except Exception as exc:
            self.tx_send_errors += 1
            self.last_tx_error = str(exc)
            return False

    async def _send_payload_now(self, payload: bytes, source: str='', seen_payload: bytes | None=None) -> bool:
        """Send a payload immediately from the writer task."""
        try:
            self.writer.write(self.build_frame(payload))
            await self.writer.drain()
            if seen_payload is not None:
                # Track forwarded payloads so loopguard and dedupe share the same view.
                self.mark_seen_payload(seen_payload)
                fingerprint = packet_fingerprint(seen_payload)
                record = packet_fingerprint_record(fingerprint)
                record['sent_to'][self.bridge_key] = time.time()
                self.last_fingerprint = fingerprint
                self.last_fingerprint_at = time.time()
                self.record_rf_inject(seen_payload)
            self.packets_tx += 1
            now = time.time()
            self.last_tx_send = now
            self.last_tx_error = ''
            self.packet_tx_times.append(now)
            prune_packet_times(self.packet_tx_times, now)
            record_node_packet(self, 'TX', now)
            from bridge_server.server import format_packet_description, record_packet_log
            packet_log = record_packet_log('TX', self, payload, source=source, target=self.display_name)
            if config.LOG_PACKETS:
                log.info('%s -> %s: TX %d bytes %s: %s', packet_log['source'], packet_log['target'], len(payload), format_packet_description(packet_log), packet_log['preview'])
            return True
        except Exception as exc:
            self.tx_send_errors += 1
            self.last_tx_error = str(exc)
            return False

    async def send_command(self, command: str, password: str, timeout: int | None=None, wait_reply: bool=True, count_stats: bool=True) -> str:
        """Send a bridge CLI command and wait for the reply."""
        command = command.strip()
        raw_command = command.encode('utf-8')[:96]
        raw_password = password.encode('utf-8')[:32]
        if not raw_command:
            raise ValueError('empty command')
        request_id = state.next_command_id
        state.next_command_id = state.next_command_id + 1 & 4294967295
        if state.next_command_id == 0:
            state.next_command_id = 1
        payload = CONTROL_PREFIX + bytes([CONTROL_TYPE_COMMAND]) + struct.pack('>I', request_id) + bytes([len(raw_password)]) + bytes([len(raw_command)]) + raw_password + raw_command
        if not wait_reply:
            ok = await self.send_payload(payload) if count_stats else await self.send_control_payload(payload)
            if not ok:
                raise RuntimeError('send failed')
            return 'OK - command sent'
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        state.pending_commands[request_id] = future
        try:
            ok = await self.send_payload(payload) if count_stats else await self.send_control_payload(payload)
            if not ok:
                raise RuntimeError('send failed')
            return await asyncio.wait_for(future, timeout=timeout or COMMAND_TIMEOUT_SECS)
        finally:
            state.pending_commands.pop(request_id, None)

    def close(self):
        """Close the client connection and writer task."""
        if self.tx_queue_task and (not self.tx_queue_task.done()) and (self.tx_queue_task is not asyncio.current_task()):
            self.tx_queue_task.cancel()
        try:
            self.writer.close()
        except Exception:
            pass
