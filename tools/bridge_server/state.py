"""In-memory bridge server state."""

from collections import OrderedDict, deque
import time

next_command_id = 1
next_client_id = 1
SERVER_STARTED_AT = time.time()
connected_clients: set = set()
node_traffic_stats: dict = {}
latest_locations: dict = {}
latest_location_tracks: dict = {}
latest_location_stationary: dict = {}
latest_sensors: dict = {}
transport_rx_times: deque = deque()
transport_rate_dropped = 0
recent_packets: deque = deque(maxlen=200)
pending_commands: dict = {}
packet_fingerprint_cache: OrderedDict = OrderedDict()
bridge_guard_counters: dict = {}
short_id_quarantine: dict = {}
short_id_bad_hits: dict = {}
packet_log_total = 0
public_channels: list = []
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
