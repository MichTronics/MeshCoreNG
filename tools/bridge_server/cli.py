"""Command-line entry points for the bridge server."""

import argparse
import asyncio
import logging
from pathlib import Path

import bridge_server.config as config
from bridge_server.constants import SERVER_NAME, SERVER_VERSION
from bridge_server.firmware import firmware_update_task
from bridge_server.location import load_location_tracks, load_public_channels
from bridge_server.pages import normalize_base_path
from bridge_server.protocol import format_sockaddrs
from bridge_server.server import handle_client, status_task
from bridge_server.web import handle_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("tcp_bridge")


async def main(host: str, port: int, status_host: str, status_port: int) -> None:
    """Start the TCP bridge server and optional HTTP server."""
    log.info('%s v%s starting', SERVER_NAME, SERVER_VERSION)
    server = await asyncio.start_server(handle_client, host, port)
    addrs = format_sockaddrs(server.sockets)
    log.info('MeshCore TCP bridge server listening on %s', addrs)
    http_server = None
    if status_port > 0:
        try:
            http_server = await asyncio.start_server(handle_http_client, status_host, status_port)
            http_addrs = format_sockaddrs(http_server.sockets)
            log.info('Status page listening on http://%s', http_addrs)
        except OSError as exc:
            log.warning('Status page disabled: could not bind %s:%d (%s)', status_host, status_port, exc)
    log.info('Press Ctrl+C to stop')
    status = asyncio.create_task(status_task()) if config.STATUS_INTERVAL_SECS > 0 else None
    firmware_updates = asyncio.create_task(firmware_update_task(config.FIRMWARE_UPDATE_REPO, config.FIRMWARE_UPDATE_CHECK_INTERVAL_SECS, config.FIRMWARE_UPDATE_TIMEOUT_SECS))
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
    """Build the bridge server argument parser."""
    parser = argparse.ArgumentParser(description='MeshCore TCP bridge server')
    add_network_args(parser)
    add_bridge_guard_args(parser)
    add_status_page_args(parser)
    add_firmware_update_args(parser)
    add_packet_decode_args(parser)
    add_rate_limit_args(parser)
    add_admin_args(parser)
    return parser


def add_network_args(parser: argparse.ArgumentParser) -> None:
    """Add network-related CLI arguments."""
    parser.add_argument('--version', action='version', version=f'%(prog)s {SERVER_VERSION}')
    parser.add_argument('--host', default='0.0.0.0', help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=4200, help='TCP port (default: 4200)')
    parser.add_argument('--client-timeout', type=int, default=180, help='Disconnect clients after this many seconds without packets or heartbeat (default: 180, 0 disables)')
    parser.add_argument('--client-tx-queue-max', type=int, default=config.CLIENT_TX_QUEUE_MAX, help='Max queued outbound TCP packets per bridge client (default: 250)')
    parser.add_argument('--replace-same-ip', action='store_true', help='When a new client connects, disconnect older clients from the same IP')
    parser.add_argument('--password', default='', help='Optional TCP bridge password required from clients')
    parser.add_argument('--verbose', action='store_true', help='Enable debug logging')


def add_bridge_guard_args(parser: argparse.ArgumentParser) -> None:
    """Add bridge guard CLI arguments."""
    parser.add_argument('--bridge-dedupe', choices=('on', 'off'), default='on', help='Enable TCP bridge packet fingerprint dedupe (default: on)')
    parser.add_argument('--bridge-dedupe-ttl', type=int, default=config.BRIDGE_DEDUPE_TTL_SECS, help='Fingerprint dedupe TTL in seconds (default: 300)')
    parser.add_argument('--bridge-dedupe-max-entries', type=int, default=config.BRIDGE_DEDUPE_MAX_ENTRIES, help='Max fingerprint dedupe entries (default: 4096)')
    parser.add_argument('--bridge-loopguard', choices=('on', 'off'), default='on', help='Enable bridge loop quarantine guard (default: on)')
    parser.add_argument('--bridge-loopguard-window', type=int, default=config.BRIDGE_LOOPGUARD_WINDOW_SECS, help='Loopguard window in seconds (default: 60)')
    parser.add_argument('--bridge-loopguard-threshold', type=int, default=config.BRIDGE_LOOPGUARD_THRESHOLD, help='Loopguard hits before quarantine (default: 5)')
    parser.add_argument('--bridge-loopguard-quarantine', type=int, default=config.BRIDGE_LOOPGUARD_QUARANTINE_SECS, help='Loopguard quarantine seconds (default: 120)')
    parser.add_argument('--bridge-rf-inject', choices=('on', 'off'), default='off', help='Enable server-side RF inject budget before forwarding to bridges (default: off)')
    parser.add_argument('--bridge-rf-inject-max-per-min', type=int, default=config.BRIDGE_RF_INJECT_MAX_PER_MIN, help='Max TCP-to-RF inject packets per bridge per minute, 0 disables count cap')
    parser.add_argument('--bridge-rf-inject-max-airtime-ms-hour', type=int, default=config.BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR, help='Estimated max TCP-to-RF inject airtime per bridge per hour, 0 disables airtime cap')
    parser.add_argument('--bridge-rf-inject-block-duty-above-pct', type=float, default=config.BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT, help='Block TCP-to-RF inject when reported duty used percent is at/above this, 0 disables')
    parser.add_argument('--bridge-group', default=config.BRIDGE_GROUP, help='Bridge group name assigned to legacy/default clients (default: default)')
    parser.add_argument('--bridge-require-group-match', action='store_true', help='Only forward packets between bridges with matching group')
    parser.add_argument('--short-id-quarantine', choices=('on', 'off'), default='off', help='Automatically quarantine spammy 1-byte source IDs (default: off)')
    parser.add_argument('--short-id-quarantine-window', type=int, default=config.SHORT_ID_QUARANTINE_WINDOW_SECS, help='Seconds to count bad short-id hits before quarantine (default: 60)')
    parser.add_argument('--short-id-quarantine-threshold', type=int, default=config.SHORT_ID_QUARANTINE_THRESHOLD, help='Bad short-id hits before quarantine (default: 5)')
    parser.add_argument('--short-id-quarantine-secs', type=int, default=config.SHORT_ID_QUARANTINE_SECS, help='Seconds to block a quarantined 1-byte source ID (default: 900)')


def add_status_page_args(parser: argparse.ArgumentParser) -> None:
    """Add HTTP status page CLI arguments."""
    parser.add_argument('--status-interval', type=int, default=60, help='Log connected clients every N seconds (default: 60, 0 disables)')
    parser.add_argument('--status-host', default='0.0.0.0', help='Bind address for the HTTP status page (default: 0.0.0.0)')
    parser.add_argument('--status-port', type=int, default=8080, help='HTTP status page port (default: 8080, 0 disables)')
    parser.add_argument('--status-base-path', default=config.STATUS_BASE_PATH, help='Public URL prefix for status pages behind a reverse proxy, e.g. /meshbridgestatus')


def add_firmware_update_args(parser: argparse.ArgumentParser) -> None:
    """Add firmware update CLI arguments."""
    parser.add_argument('--firmware-update-repo', default=config.FIRMWARE_UPDATE_REPO, help='GitHub owner/repo used for firmware update checks (default: MichTronics/MeshCoreNG, empty disables)')
    parser.add_argument('--firmware-update-interval', type=int, default=config.FIRMWARE_UPDATE_CHECK_INTERVAL_SECS, help='Seconds between firmware update checks (default: 3600, 0 disables)')
    parser.add_argument('--firmware-update-timeout', type=int, default=config.FIRMWARE_UPDATE_TIMEOUT_SECS, help='HTTP timeout in seconds for firmware update checks (default: 5)')


def add_packet_decode_args(parser: argparse.ArgumentParser) -> None:
    """Add packet decoding CLI arguments."""
    parser.add_argument('--log-packets', action='store_true', help='Log every received and forwarded mesh payload')
    parser.add_argument('--log-hex-bytes', type=int, default=32, help='Number of payload bytes to show in packet logs (default: 32)')
    parser.add_argument('--public-channels-file', default='', help='JSON file with public channel names/secrets for optional group packet decoding')
    parser.add_argument('--location-tracks-dir', default=str(config.LOCATION_TRACKS_DIR), help='Directory for persistent tracker route JSONL files (default: logs/location_tracks)')


def add_rate_limit_args(parser: argparse.ArgumentParser) -> None:
    """Add transport rate-limit CLI arguments."""
    parser.add_argument('--transport-rate-limit', choices=('on', 'off'), default='on', help='Rate-limit DM/group/transport packets before bridge broadcast (default: on)')
    parser.add_argument('--transport-rate-max', type=int, default=config.TRANSPORT_RATE_LIMIT_MAX, help='Max transport packets per bridge client per window (default: 20)')
    parser.add_argument('--transport-rate-window', type=int, default=config.TRANSPORT_RATE_LIMIT_WINDOW_SECS, help='Per-client transport rate window in seconds (default: 120)')
    parser.add_argument('--transport-global-rate-max', type=int, default=config.TRANSPORT_GLOBAL_RATE_LIMIT_MAX, help='Max transport packets globally per window, 0 disables global cap (default: 80)')
    parser.add_argument('--transport-global-rate-window', type=int, default=config.TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS, help='Global transport rate window in seconds (default: 120)')


def add_admin_args(parser: argparse.ArgumentParser) -> None:
    """Add HTTP admin CLI arguments."""
    parser.add_argument('--web-username', default='', help='Username required to access the HTTP status web interface (default: user when --web-password is set)')
    parser.add_argument('--web-password', default='', help='Password required to access the HTTP status web interface (empty disables login)')
    parser.add_argument('--admin-password', default='', help='Optional Basic auth password protecting the HTTP remote management page')
    parser.add_argument('--allow-path-block-admin', action='store_true', help='Allow HTTP bridge admins to send path.block quarantine commands without node passwords')


def apply_args(args: argparse.Namespace) -> None:
    """Apply parsed CLI arguments to runtime config."""
    if args.verbose:
        log.setLevel(logging.DEBUG)
    config.LOG_PACKETS = args.log_packets
    config.LOG_HEX_BYTES = max(0, args.log_hex_bytes)
    config.CLIENT_TIMEOUT_SECS = max(0, args.client_timeout)
    config.CLIENT_TX_QUEUE_MAX = max(1, args.client_tx_queue_max)
    config.BRIDGE_DEDUPE_ENABLED = args.bridge_dedupe == 'on'
    config.BRIDGE_DEDUPE_TTL_SECS = max(1, args.bridge_dedupe_ttl)
    config.BRIDGE_DEDUPE_MAX_ENTRIES = max(1, args.bridge_dedupe_max_entries)
    config.BRIDGE_LOOPGUARD_ENABLED = args.bridge_loopguard == 'on'
    config.BRIDGE_LOOPGUARD_WINDOW_SECS = max(1, args.bridge_loopguard_window)
    config.BRIDGE_LOOPGUARD_THRESHOLD = max(1, args.bridge_loopguard_threshold)
    config.BRIDGE_LOOPGUARD_QUARANTINE_SECS = max(1, args.bridge_loopguard_quarantine)
    config.BRIDGE_RF_INJECT_ENABLED = args.bridge_rf_inject == 'on'
    config.BRIDGE_RF_INJECT_MAX_PER_MIN = max(0, args.bridge_rf_inject_max_per_min)
    config.BRIDGE_RF_INJECT_MAX_AIRTIME_MS_PER_HOUR = max(0, args.bridge_rf_inject_max_airtime_ms_hour)
    config.BRIDGE_RF_INJECT_BLOCK_DUTY_ABOVE_PCT = max(0.0, args.bridge_rf_inject_block_duty_above_pct)
    config.BRIDGE_GROUP = (args.bridge_group or 'default').strip() or 'default'
    config.BRIDGE_REQUIRE_GROUP_MATCH = bool(args.bridge_require_group_match)
    config.SHORT_ID_QUARANTINE_ENABLED = args.short_id_quarantine == 'on'
    config.SHORT_ID_QUARANTINE_WINDOW_SECS = max(1, args.short_id_quarantine_window)
    config.SHORT_ID_QUARANTINE_THRESHOLD = max(1, args.short_id_quarantine_threshold)
    config.SHORT_ID_QUARANTINE_SECS = max(1, args.short_id_quarantine_secs)
    config.STATUS_INTERVAL_SECS = max(0, args.status_interval)
    config.STATUS_BASE_PATH = normalize_base_path(args.status_base_path)
    config.REPLACE_SAME_IP = args.replace_same_ip
    config.BRIDGE_PASSWORD = args.password
    config.WEB_USERNAME = args.web_username.strip()
    config.WEB_PASSWORD = args.web_password
    config.ADMIN_PASSWORD = args.admin_password
    config.ALLOW_PATH_BLOCK_ADMIN = args.allow_path_block_admin
    config.FIRMWARE_UPDATE_REPO = args.firmware_update_repo.strip()
    config.FIRMWARE_UPDATE_CHECK_INTERVAL_SECS = max(0, args.firmware_update_interval)
    config.FIRMWARE_UPDATE_TIMEOUT_SECS = max(1, args.firmware_update_timeout)
    config.PUBLIC_CHANNELS_FILE = args.public_channels_file
    config.LOCATION_TRACKS_DIR = Path(args.location_tracks_dir)
    config.TRANSPORT_RATE_LIMIT_ENABLE = args.transport_rate_limit == 'on'
    config.TRANSPORT_RATE_LIMIT_MAX = max(0, args.transport_rate_max)
    config.TRANSPORT_RATE_LIMIT_WINDOW_SECS = max(1, args.transport_rate_window)
    config.TRANSPORT_GLOBAL_RATE_LIMIT_MAX = max(0, args.transport_global_rate_max)
    config.TRANSPORT_GLOBAL_RATE_LIMIT_WINDOW_SECS = max(1, args.transport_global_rate_window)


def run_cli() -> None:
    """Run the bridge server CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()
    apply_args(args)
    load_public_channels(config.PUBLIC_CHANNELS_FILE)
    load_location_tracks()
    try:
        asyncio.run(main(args.host, args.port, args.status_host, max(0, args.status_port)))
    except KeyboardInterrupt:
        log.info('Server stopped')
