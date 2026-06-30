# Python Tools

MeshCoreNG includes a few repository tools for bridge operation, generated data, release support and local testing.

Most tools use only the Python standard library. Current scripts use modern type syntax, so use Python 3.10 or newer.

## TCP bridge server

`tools/tcp_bridge_server.py` runs the controlled TCP bridge server and its HTTP status, management and tracker-map pages.

Common usage:

```bash
python3 tools/tcp_bridge_server.py --version
python3 tools/tcp_bridge_server.py --port 4200
python3 tools/tcp_bridge_server.py --port 4200 --password bridgeSecret
python3 tools/tcp_bridge_server.py --port 4200 --admin-password webAdminSecret
```

The server version is also shown on the HTTP status page and exposed in `/status.json`.

Options:

```text
--version
--host <addr>
--port <port>
--password <secret>
--admin-password <secret>
--allow-path-block-admin
--status-host <addr>
--status-port <port>
--status-base-path <path>
--status-interval <seconds>
--client-timeout <seconds>
--client-tx-queue-max <count>
--replace-same-ip
--verbose
--log-packets
--log-hex-bytes <count>
--public-channels-file <path>
--location-tracks-dir <path>
--firmware-update-repo <owner/repo>
--firmware-update-interval <seconds>
--firmware-update-timeout <seconds>
--transport-rate-limit on|off
--transport-rate-max <count>
--transport-rate-window <seconds>
--transport-global-rate-max <count>
--transport-global-rate-window <seconds>
--bridge-dedupe on|off
--bridge-dedupe-ttl <seconds>
--bridge-dedupe-max-entries <count>
--bridge-loopguard on|off
--bridge-loopguard-window <seconds>
--bridge-loopguard-threshold <count>
--bridge-loopguard-quarantine <seconds>
--bridge-rf-inject on|off
--bridge-rf-inject-max-per-min <count>
--bridge-rf-inject-max-airtime-ms-hour <milliseconds>
--bridge-rf-inject-block-duty-above-pct <percent>
--bridge-group <name>
--bridge-require-group-match
--short-id-quarantine on|off
--short-id-quarantine-window <seconds>
--short-id-quarantine-threshold <count>
--short-id-quarantine-secs <seconds>
```

Optional public-channel decoding uses `cryptography`:

```bash
pip install cryptography
```

## USB bridge client

`tools/usb_bridge_client.py` relays RS232 bridge frames between a serial repeater and a TCP bridge server. It needs `pyserial`.

```bash
pip install pyserial
python3 tools/usb_bridge_client.py --serial /dev/ttyUSB0 --baud 115200 \
  --server bridge.example.com --port 4200 --bridge-password bridgeSecret
```

Options:

```text
--serial <port>
--baud <baud>
--server <host>
--port <port>
--bridge-password <secret>
--debug
```

## Python room server

`tools/python_room_server.py` runs a minimal room server through the TCP bridge. It needs `cryptography`.

```bash
pip install cryptography
python3 tools/python_room_server.py --server 127.0.0.1 --port 4200 \
  --bridge-password bridgeSecret --name "Python Room" --password secret
```

Options:

```text
--server <host>
--port <port>
--bridge-password <secret>
--name <room-name>
--password <write-password>
--admin-password <admin-password>
--allow-read-only
--scope <region-name>
--state <path>
--advert-interval <seconds>
--path-hash-size 1|2|3
--max-posts <count>
--max-text-len <bytes>
--reconnect-delay <seconds>
--verbose
```

## Bridge Echo Lab

`tools/bridge_echo_lab.py` runs a diagnostic Echo Lab service through the TCP bridge. It needs `cryptography` and uses the same bridge-client style as the Python room server.

```bash
pip install cryptography
python3 tools/bridge_echo_lab.py --server 127.0.0.1 --port 4200 \
  --bridge-password bridgeSecret --name "Echo Lab" --password secret
```

Commands accepted by logged-in clients:

```text
ping bridge
trace flood
echo direct <text>
echo flood <text>
delay test <milliseconds>
loss test <count>
status
id
```

Options:

```text
--server <host>
--port <port>
--bridge-password <secret>
--name <service-name>
--password <password>
--admin-password <admin-password>
--scope <region-name>
--state <path>
--advert-interval <seconds>
--path-hash-size 1|2|3
--default-delay-ms <milliseconds>
--max-delay-ms <milliseconds>
--loss-spacing-ms <milliseconds>
--reconnect-delay <seconds>
--verbose
```

## Public channel list updater

`tools/update_public_channels.py` refreshes `tools/public_channels.json` from MeshWiki.

```bash
python3 tools/update_public_channels.py
python3 tools/update_public_channels.py --url <wiki-url> --output tools/public_channels.json
```

## Dutch region database generator

`tools/generate_dutch_region_db.py` regenerates the compiled Dutch region lookup data.

```bash
python3 tools/generate_dutch_region_db.py
python3 tools/generate_dutch_region_db.py --html /path/to/Lijst_van_regios.html --out-dir src/helpers
```

## Web flasher manifest builder

`tools/build_webflasher.py` builds web-flasher firmware manifests from GitHub release assets.

```bash
python3 tools/build_webflasher.py --repo owner/repo
python3 tools/build_webflasher.py --repo owner/repo --token "$GITHUB_TOKEN"
```
