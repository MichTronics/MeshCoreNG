# CLI Commands

This document provides an overview of CLI commands that can be sent to MeshCore Repeaters, Room Servers and Sensors.

## Navigation

- [Operational](#operational)
- [Neighbors](#neighbors-repeater-only)
- [Statistics](#statistics)
- [Logging](#logging)
- [Information](#info)
  - [Configuration](#configuration)
  - [Radio](#radio)
  - [System](#system)
  - [Low Battery Boot Guard](#low-battery-boot-guard)
  - [Runtime Low Battery Guard](#runtime-low-battery-guard)
  - [Routing](#routing)
  - [ACL](#acl)
  - [Region Management](#region-management-v110)
  - [Dutch Region Database](#dutch-region-database)
  - [Region Examples](#region-examples)
  - [GPS](#gps-when-gps-support-is-compiled-in)
  - [Sensors](#sensors-when-sensor-support-is-compiled-in)
  - [Bridge](#bridge-when-bridge-support-is-compiled-in)

---

## Operational

### Reboot the node
**Usage:** 
- `reboot`

**Note:** No reply is sent.

---

### Power-off the node
**Usage:**
- `poweroff`, or
- `shutdown`

**Note:** No reply is sent.

---

### Reset the clock and reboot
**Usage:**
- `clkreboot`

**Note:** No reply is sent.

---

### Sync the clock with the remote device
**Usage:** 
- `clock sync`

---

### Display current time in UTC
**Usage:**
- `clock`

---

### Set the time to a specific timestamp
**Usage:** 
- `time <epoch_seconds>`

**Parameters:**
- `epoch_seconds`: Unix epoch time

---

### Send a flood advert
**Usage:** 
- `advert`

---

### Send a zero-hop advert
**Usage:**
- `advert.zerohop`

---

### Start an Over-The-Air (OTA) firmware update
**Usage:**
- `start ota`

Starts the existing manual OTA mode:
- ESP32: starts a `MeshCore-OTA` WiFi access point and web uploader at `/update`
- nRF52: starts BLE DFU advertising for the nRF DFU app

---

### Check or install ESP32 online OTA update
**Usage:**
- `ota.check`
- `ota.update`

ESP32-only. Uses the saved WiFi credentials from `set wifi.ssid` and `set wifi.password`, downloads the OTA manifest from the flasher site, finds the firmware matching this PlatformIO environment, and installs the newest non-merged `.bin`.

`ota.check` only reports whether an update is available. `ota.update` downloads and installs it, then reports `reboot` when the new image is ready. Use `reboot` to boot into the updated firmware.

This does not apply to nRF52, STM32 or RP2040 builds.

---

### Erase/Factory Reset
**Usage:**
- `erase`

**Serial Only:** Yes

**Warning:** _**This is destructive!**_

---

## Neighbors (Repeater Only)

### List nearby neighbors
**Usage:** 
- `neighbors`

**Note:** The output of this command is limited to the 8 most recent adverts.

**Note:** Each line is encoded as `{pubkey-prefix}:{timestamp}:{snr*4}`

---

### Remove a neighbor
**Usage:** 
- `neighbor.remove <pubkey_prefix>`

**Parameters:** 
- `pubkey_prefix`: The public key of the node to remove from the neighbors list. This can be a short prefix or the full key. All neighbors matching the provided prefix will be removed.

**Note:** You can remove all neighbors by sending a space character as the prefix. The space indicates an empty prefix, which matches all existing neighbors.

---

### Discover zero hop neighbors

**Usage:** 
- `discover.neighbors`

---

## Statistics

### Clear Stats
**Usage:** `clear stats`

---

### Dense Mesh Stats
**Usage:**
- `get dense.stats`
- `clear dense.stats`

Shows repeater dense-mesh counters in one place: flood adverts received, forwarded and dropped, CAD busy/timeout events, and flood/direct duplicate counters.

MeshCoreNG v2 also includes a rolling window for neighbor count, unique/duplicate flood RX, suppressed flood TX, RX/TX airtime, congestion level, and density level. These counters are RAM-only and do not change the packet protocol.

**Serial Only:** `get dense.stats`

---

### Atlas Export
**Usage:**
- `atlas enable <on|off>`
- `atlas.enable <on|off>`
- `atlas position <on|off>`
- `atlas.position <on|off>`
- `atlas neighbors <on|off>`
- `atlas.neighbors <on|off>`
- `atlas pathsample <on|off|0-10>`
- `atlas.pathsample <on|off|0-10>`
- `atlas export <on|off>`
- `atlas.export <on|off>`
- `atlas export status`
- `atlas export test`
- `get atlas.stats`
- `observer export json`

All Atlas features are disabled by default. Phase 1 exports information already available inside the firmware and does not change normal routing or increase flood traffic.

`get atlas.stats` returns JSON-like dense-mesh counters. `observer export json` returns Observer JSONL v1 lines over local serial only when Atlas export is enabled. `atlas export test` emits one fake JSONL event of each supported type for Atlas ingest testing.

---

### Daily Reboot
**Usage:**
- `set reboot.daily <on|off>`
- `set reboot.interval <1-168>`
- `get reboot`

Repeater and TCP bridge repeater builds can optionally reboot on an uptime timer. The feature is disabled by default. `set reboot.daily on` enables a 24-hour reboot interval unless changed with `set reboot.interval`.

When the timer expires, the repeater waits for the outbound TX queue to become idle, then reboots the board. RS232 and ESPNow bridge builds do not include this timer.

---

### Malformed/Spam Stats
**Usage:**
- `get spam.stats`
- `clear spam.stats`

Shows repeater counters for inspectable default-public group text: public packets seen, accepted packets, malformed drops, low-confidence spam drops, decrypt failures, and drop reasons such as short payload, unknown text type, empty text, invalid UTF-8 and bad timestamp.

The compact reason fields are `s` short, `t` type, `e` empty, `u` UTF-8 and `tm` timestamp.

These counters are RAM-only and reset on reboot, with `clear spam.stats`, or with `clear stats`.

---

### Repeater Health
**Usage:**
- `get repeater.health`
- `get repeater.status`

Shows a compact human-readable repeater status: health label, score, repeat on/off state, RX error percentage, airtime/congestion level, density level, duplicate RX percentage, spam drops, spam mode, SF and frequency.

The score is diagnostic only. It does not change forwarding behavior.

---

### Power Saving Stats
**Usage:**
- `get power.stats`
- `clear power.stats`

Shows repeater power-saving counters: sleep attempts, sleep skipped because outbound work was pending, sleep skipped because a bridge/WiFi mode was active, wakeups caused by LoRa RX when the board reports that reason, and a compact board support indicator.

These counters are RAM-only and reset on reboot or with `clear power.stats`.

**Serial Only:** `get power.stats`

---

### System Stats - Battery, Uptime, Queue Length and Debug Flags
**Usage:** 
- `stats-core`

**Serial Only:** Yes

---

### Radio Stats - Noise floor, Last RSSI/SNR, Airtime, Receive errors
**Usage:** `stats-radio`

**Serial Only:** Yes

---

### Packet stats - Packet counters: Received, Sent
**Usage:** `stats-packets`

**Serial Only:** Yes

---

## Logging

### Begin capture of rx log to node storage
**Usage:** `log start`

---

### End capture of rx log to node storage
**Usage:** `log stop`

---

### Erase captured log
**Usage:** `log erase`

---

### Print the captured log to the serial terminal
**Usage:** `log`

**Serial Only:** Yes

---

## Info

### Get the Version
**Usage:** `ver`

---

### Show the hardware name
**Usage:** `board`

---

## Configuration

### Radio

#### View or change this node's radio parameters
**Usage:**
- `get radio`
- `set radio <freq>,<bw>,<sf>,<cr>`

**Parameters:**
- `freq`: Frequency in MHz
- `bw`: Bandwidth in kHz
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)

**Set by build flag:** `LORA_FREQ`, `LORA_BW`, `LORA_SF`, `LORA_CR`

**Default:** `869.525,250,11,5`

**Note:** Requires reboot to apply

---

#### View or change this node's transmit power
**Usage:**
- `get tx`
- `set tx <dbm>`

**Parameters:**
- `dbm`: Power level in dBm (1-22)

**Set by build flag:** `LORA_TX_POWER`

**Default:** Varies by board

**Notes:** This setting only controls the power level of the LoRa chip. Some nodes have an additional power amplifier stage which increases the total output. Refer to the node's manual for the correct setting to use. **Setting a value too high may violate the laws in your country.**

---

#### Change the radio parameters for a set duration
**Usage:** 
- `tempradio <freq>,<bw>,<sf>,<cr>,<timeout_mins>`

**Parameters:**
- `freq`: Frequency in MHz (300-2500)
- `bw`: Bandwidth in kHz (7.8-500)
- `sf`: Spreading factor (5-12)
- `cr`: Coding rate (5-8)
- `timeout_mins`: Duration in minutes (must be > 0)

**Note:** This is not saved to preferences and will clear on reboot

---

#### View or change this node's frequency
**Usage:**
- `get freq`
- `set freq <frequency>`

**Parameters:**
- `frequency`: Frequency in MHz

**Default:** `869.525`

**Note:** Requires reboot to apply
**Serial Only:** `set freq <frequency>`

---

#### View or change this node's rx boosted gain mode (SX12xx and LR1110, v1.14.1+)
**Usage:**
- `get radio.rxgain`
- `set radio.rxgain <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** `on`

**Temporary Note:** If you upgraded from an older version to 1.14.1 without erasing flash, this setting is `off` because of [#2118](https://github.com/meshcore-dev/MeshCore/issues/2118)

---

#### View or change this node's external FEM RX gain/LNA mode
**Usage:**
- `get radio.fem.rxgain`
- `set radio.fem.rxgain <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** Board-specific

**Note:** This controls an external front-end-module RX gain/LNA path when the board supports it, such as the Heltec V4.3 KCT8103L FEM. It is separate from `radio.rxgain`, which controls the LoRa radio chip's internal boosted RX gain mode. Unsupported boards return an error.

---

### System

#### View or change this node's name
**Usage:**
- `get name`
- `set name <name>`

**Parameters:**
- `name`: Node name

**Set by build flag:** `ADVERT_NAME`

**Default:** Varies by board

**Note:** Max length varies. If a location is set, the max length is 24 bytes; 32 otherwise. Emoji and unicode characters may take more than one byte.

---

#### View or change this node's latitude
**Usage:**
- `get lat`
- `set lat <degrees>`

**Set by build flag:** `ADVERT_LAT`

**Default:** `0`

**Parameters:**
- `degrees`: Latitude in degrees

---

#### View or change this node's longitude
**Usage:**
- `get lon`
- `set lon <degrees>`

**Set by build flag:** `ADVERT_LON`

**Default:** `0`

**Parameters:**
- `degrees`: Longitude in degrees

---

#### View or change this node's identity (Private Key)
**Usage:**
- `get prv.key`
- `set prv.key <private_key>`

**Parameters:**
- `private_key`: Private key in hex format (64 hex characters)

**Serial Only:**
- `get prv.key`: Yes
- `set prv.key`: No

**Note:** Requires reboot to take effect after setting

---

#### Change this node's admin password
**Usage:**
- `password <new_password>`

**Parameters:**
- `new_password`: New admin password

**Set by build flag:** `ADMIN_PASSWORD`

**Default:** `password`

**Note:** Command reply echoes the updated password for confirmation.

**Note:** Any node using this password will be added to the admin ACL list.

---

#### View or change this node's guest password
**Usage:**
- `get guest.password`
- `set guest.password <password>`

**Parameters:**
- `password`: Guest password

**Set by build flag:** `ROOM_PASSWORD` (Room Server only)

**Default:** `<blank>`

---

#### View or change this node's owner info
**Usage:**
- `get owner.info`
- `set owner.info <text>`

**Parameters:**
- `text`: Owner information text

**Default:** `<blank>`

**Note:** `|` characters are translated to newlines

**Note:** Requires firmware 1.12+

---

#### Fine-tune the battery reading
**Usage:**
- `get adc.multiplier`
- `set adc.multiplier <value>`

**Parameters:**
- `value`: ADC multiplier (0.0-10.0)

**Default:** `0.0` (value defined by board)

**Note:** Returns "Error: unsupported by this board" if hardware doesn't support it

---

### Low Battery Boot Guard

The low-battery boot guard prevents supported battery-powered boards from continuing into full firmware startup while the measured battery voltage is still too low. This helps avoid repeated brownout/reset loops after a deeply discharged battery is connected to a charger.

These commands are available on Repeater, GPS tracker / Sensor, and Room Server builds that use the normal MeshCore CLI. KISS modem, companion radio, and secure chat builds use the build-time defaults.

#### Enable or disable the guard
**Usage:**
- `get boot.lowbat.guard`
- `set boot.lowbat.guard <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

---

#### View or change the low-battery boot threshold
**Usage:**
- `get boot.lowbat.mv`
- `set boot.lowbat.mv <millivolts>`

**Parameters:**
- `millivolts`: `0` or `2500-6000`

**Default:** `3300`

**Note:** `0` disables the threshold, but `set boot.lowbat.guard off` is clearer.

---

#### View or change the minimum valid battery reading
**Usage:**
- `get boot.lowbat.valid_min`
- `set boot.lowbat.valid_min <millivolts>`

**Parameters:**
- `millivolts`: `0-6000`

**Default:** `2500`

**Note:** Readings below this value are treated as invalid or unsupported and do not block boot.

---

#### View or change the retry interval
**Usage:**
- `get boot.lowbat.retry`
- `set boot.lowbat.retry <seconds>`

**Parameters:**
- `seconds`: `5-3600`

**Default:** `60`

**Note:** Changes are stored in node preferences and apply on the next boot after preferences are loaded.

---

### Runtime Low Battery Guard

The runtime low-battery guard protects battery-powered Repeater, GPS tracker / Sensor, and Room Server builds after normal startup. When enabled, the firmware periodically checks battery voltage from the main loop. If the reading is valid, the board is not externally powered, and voltage is below the runtime threshold, the node sleeps before continuing work.

This is separate from the boot guard. The boot guard prevents brownout boot loops; the runtime guard prevents a running node from draining the battery too far.

#### Enable or disable the runtime guard
**Usage:**
- `get runtime.lowbat.guard`
- `set runtime.lowbat.guard <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

---

#### View or change the runtime sleep threshold
**Usage:**
- `get runtime.lowbat.mv`
- `set runtime.lowbat.mv <millivolts>`

**Parameters:**
- `millivolts`: `0` or `2500-6000`

**Default:** `3300`

**Note:** `0` disables the threshold, but `set runtime.lowbat.guard off` is clearer.

---

#### View or change the runtime warning threshold
**Usage:**
- `get runtime.lowbat.warn`
- `set runtime.lowbat.warn <millivolts>`

**Parameters:**
- `millivolts`: `0-6000`

**Default:** `3500`

**Note:** The warning threshold is stored for status/telemetry use. The current guard action is controlled by `runtime.lowbat.mv`.

---

#### View or change the minimum valid runtime battery reading
**Usage:**
- `get runtime.lowbat.valid_min`
- `set runtime.lowbat.valid_min <millivolts>`

**Parameters:**
- `millivolts`: `0-6000`

**Default:** `2500`

---

#### View or change the runtime low-battery sleep interval
**Usage:**
- `get runtime.lowbat.retry`
- `set runtime.lowbat.retry <seconds>`

**Parameters:**
- `seconds`: `5-86400`

**Default:** `1800`

---

#### View this node's public key
**Usage:** `get public.key`

---

#### View this node's firmware version
**Usage:** `ver`

---

#### View this node's OTA firmware target
**Usage:** `get firmware.target`

This is the target name used by `ota.check` and `ota.update` to find a matching entry in the online OTA manifest.

---

#### View this node's configured role
**Usage:** `get role`

---

#### View or change this node's power saving flag (Repeater Only)
**Usage:**
- `powersaving`
- `powersaving on`
- `powersaving off`

**Parameters:** 
- `on`: enable power saving
- `off`: disable power saving

**Default:** `off`

**Note:** Power saving is repeater-only. When enabled, sleep is attempted only when there is no pending outbound work. Bridge/WiFi modes prevent sleep because the bridge must stay awake.

On ESP32 boards with supported LoRa DIO1 wake wiring, sleep can wake by LoRa RX or by timer. On nRF52 boards sleep is event/interrupt driven and the seconds parameter is ignored by the board sleep implementation.

---

### Routing

#### View or change this node's repeat flag
**Usage:**
- `get repeat`
- `set repeat <state>`

**Parameters:**
  - `state`: `on`|`off`

**Default:** `on`

---

#### View or change malformed repeater forwarding
**Usage:**
- `get malformed.drop`
- `set malformed.drop on`
- `set malformed.drop off`

**Parameters:**
- `on`: drop malformed decryptable default-public-channel group text before retransmission
- `off`: leave forwarding behavior unchanged

**Default:** `on`

**Note:** This only applies to human-readable default public group text that the repeater can decrypt and inspect. Binary channel datagrams, raw/custom payloads, requests, responses, private/encrypted packets that cannot be inspected and unknown/future packet types are still handled by the normal forwarding rules. Existing saved preferences are preserved; use `set malformed.drop off` to disable this behavior on a repeater.

#### View or change this node's advert path hash size
**Usage:**
- `get path.hash.mode`
- `set path.hash.mode <value>`

**Parameters:**
- `value`: Path hash size (0-2)
  - `0`: 1 Byte hash size (256 unique ids)[64 max flood]
  - `1`: 2 Byte hash size (65,536 unique ids)[32 max flood]
  - `2`: 3 Byte hash size (16,777,216 unique ids)[21 max flood]
  - `3`: DO NOT USE (Reserved)

**Default:** `0`

**Note:** the 'path.hash.mode' sets the low-level ID/hash encoding size used when the repeater adverts. This setting has no impact on what packet ID/hash size this repeater forwards, all sizes should be forwarded on firmware >= 1.14. This feature was added in firmware 1.14

**Temporary Note:** adverts with ID/hash sizes of 2 or 3 bytes may have limited flood propagation in your network while this feature is new as v1.13.0 firmware and older will drop packets with multibyte path ID/hashes as only 1-byte hashes are supported. Consider your install base of firmware >=1.14 has reached a criticality for effective network flooding before implementing higher ID/hash sizes.

---

#### Temporarily block flood forwarding by packet path
**Usage:**
- `get path.block`
- `set path.block add <path> [duration]`
- `set path.block del <path>`
- `clear path.block`
- `set path.block clear`

**Parameters:**
- `path`: one, two, or three consecutive path hops, separated by `/`
  - `aa`: match any flood packet whose path contains hop `aa`
  - `aa/bb`: match any flood packet whose path contains hop `aa` followed by `bb`
  - `aa/bb/cc`: match any flood packet whose path contains that three-hop sequence
- `duration`: optional temporary block duration. Use seconds, `Nm`, `Nh`, or `Nd`. Default is `1h`; maximum is 7 days.

**Examples:**
```text
set path.block add aa 15m
set path.block add aa/bb 1h
set path.block add aa/bb/cc 6h
set path.block del aa/bb
get path.block
clear path.block
```

**Note:** This is a runtime quarantine list for repeaters. Matching packets are not retransmitted on RF and are not exported to bridge transports. It does not change the packet format and it is not persisted across reboot. The path hop width must match the packet path width: 1-byte paths use `aa`, 2-byte paths use `aa12/bb34`, and 3-byte paths use `aa12fe/bb34aa/cc56d0`.

---

#### Temporarily block TCP bridge packets by 1-byte source node id
**Usage:**
- `get node.block`
- `set node.block add <id> [duration]`
- `set node.block del <id>`
- `clear node.block`
- `set node.block clear`

**Parameters:**
- `id`: one byte in hex, for example `a7`
- `duration`: optional temporary block duration. Use seconds, `Nm`, `Nh`, or `Nd`. Default is `15m`; maximum is 30 days.

**Examples:**
```text
set node.block add a7 15m
set node.block del a7
get node.block
clear node.block
```

**Note:** This is a runtime quarantine list on the bridge/repeater itself. Matching packets are not retransmitted on RF, not exported from RF to TCP, and not injected from TCP to RF on that bridge. Because only one byte is matched, unrelated nodes with the same byte are blocked too.

---

#### View or change this node's loop detection
**Usage:**
- `get loop.detect`
- `set loop.detect <state>`

**Parameters:**
- `state`: 
  - `off`: no loop detection is performed
  - `minimal`: packets are dropped if repeater's ID/hash appears 4 or more times (1-byte), 2 or more (2-byte), 1 or more (3-byte)
  - `moderate`: packets are dropped if repeater's ID/hash appears 2 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  - `strict`: packets are dropped if repeater's ID/hash appears 1 or more times (1-byte), 1 or more (2-byte), 1 or more (3-byte)
  
**Default:** `off`

**Note:** When it is enabled, repeaters will now reject flood packets which look like they are in a loop. This has been happening recently in some meshes when there is just a single 'bad' repeater firmware out there (probably some forked or custom firmware). If the payload is messed with, then forwarded, the same packet ends up causing a packet storm, repeated up to the max 64 hops. This feature was added in firmware 1.14

**Example:** If preference is `loop.detect minimal`, and a 1-byte path size packet is received, the repeater will see if its own ID/hash is already in the path. If it's already encoded 4 times, it will reject the packet.  If the packet uses 2-byte path size, and repeater's own ID/hash is already encoded 2 times, it rejects. If the packet uses 3-byte path size, and the repeater's own ID/hash is already encoded 1 time, it rejects. 

---

#### View or change the retransmit delay factor for flood traffic
**Usage:**
- `get txdelay`
- `set txdelay <value>`

**Parameters:**
- `value`: Transmit delay factor (0-2)

**Default:** `0.5`

**Dense mesh behavior:** This delay is applied before a flood packet is queued for transmit. When `txdelay` is above `0`, MeshCoreNG also adds a small stable per-node offset derived from the node identity. This keeps nearby repeaters from becoming synchronized while preserving the existing random `txdelay` spread.

`set txdelay 0` keeps the previous zero-delay behavior and does not add the node offset. CAD retry is separate: it happens after the radio detects a busy channel, with the current retry window of 120-360 ms.

MeshCoreNG also performs duplicate-hearing suppression for queued flood retransmits. If a repeater hears two duplicate forwards of the same packet before its own scheduled retransmit fires, that pending retransmit is cancelled. The runtime toggle is `flood.dup.suppress`; the compile-time threshold is `MESH_DUP_SUPPRESS_THRESHOLD`.

---

#### View or change stable per-node flood delay offset
**Usage:**
- `get flood.node.delay`
- `set flood.node.delay <state>`

**Aliases:**
- `get node.delay`
- `set node.delay <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Note:** When enabled, a small stable delay derived from the node identity is added to flood retransmits. This helps nearby repeaters avoid retransmitting at the same moment. Turning it off keeps only the random `txdelay` behavior.

---

#### View or change duplicate-hearing flood suppression
**Usage:**
- `get flood.dup.suppress`
- `set flood.dup.suppress <state>`

**Aliases:**
- `get dup.suppress`
- `set dup.suppress <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `on`

**Note:** When enabled, a queued flood retransmit can be cancelled if this node hears enough duplicate forwards before its own transmit slot. This reduces redundant flood traffic in dense meshes without changing the packet protocol.

---

#### View or change nearby client flood suppression
**Usage:**
- `get nearby.client.suppress`
- `set nearby.client.suppress <state>`
- `get nearby.client.rssi`
- `set nearby.client.rssi <dbm>`
- `get nearby.client.hops`
- `set nearby.client.hops <value>`

**Parameters:**
- `state`: `on`|`off`
- `dbm`: RSSI threshold from `-140` to `-10`; packets at or above this RSSI are treated as very nearby
- `value`: maximum flood path hop count to consider nearby client traffic, from `0` to `3`

**Default:** `nearby.client.suppress on`, `nearby.client.rssi -45`, `nearby.client.hops 0`

**Note:** When enabled, repeaters suppress very strong first-hop client/companion flood packets instead of immediately extending them over RF. This targets the pattern where a companion and repeater are placed a few meters apart as a personal range extender. Repeater, room server and sensor adverts are not suppressed by this rule, and packets that already have relay hops continue through the normal forwarding logic.

---

#### View or change the retransmit delay factor for direct traffic
**Usage:**
- `get direct.txdelay`
- `set direct.txdelay <value>`

**Parameters:**
- `value`: Direct transmit delay factor (0-2)

**Default:** `0.2`

**Note:** Same collision-avoidance random window as `txdelay`, but applied to direct (non-flood, routed) traffic. The default is lower because direct packets are addressed to a specific next hop, so far fewer nodes compete to retransmit them.

---

#### [Experimental] View or change the processing delay for received traffic
**Usage:**
- `get rxdelay`
- `set rxdelay <value>`

**Parameters:**
- `value`: Receive delay base (0-20)

**Default:** `0.0`

**Note:** When enabled, repeaters that received a flood packet with a weak signal are held in a delay queue before processing, while those that received it with a strong signal process it immediately. This gives strong-signal paths forwarding priority. By the time weak-signal nodes process their copy, the packet may have already propagated and will be suppressed as a duplicate, reducing redundant retransmissions.

---

#### View or change the duty cycle limit
**Usage:**
- `get dutycycle`
- `set dutycycle <value>`

**Parameters:**
- `value`: Duty cycle percentage (1-100)

**Default:** `50%` (equivalent to airtime factor 1.0)

**Examples:**
- `set dutycycle 100` — no duty cycle limit
- `set dutycycle 50` — 50% duty cycle (default)
- `set dutycycle 10` — 10% duty cycle
- `set dutycycle 1` — 1% duty cycle (strictest EU requirement)

> **Note:** Added in firmware v1.15.0

---

#### View or change the airtime factor (duty cycle limit)
> **Deprecated** as of firmware v1.15.0. Use [`get/set dutycycle`](#view-or-change-the-duty-cycle-limit) instead.

**Usage:**
- `get af`
- `set af <value>`

**Parameters:**
- `value`: Airtime factor (0-9). After each transmission, the repeater enforces a silent period of approximately the on-air transmission time multiplied by the value. This results in a long-term duty cycle of roughly 1 divided by (1 plus the value). For example:
  - `af = 1` → ~50% duty
  - `af = 2` → ~33% duty
  - `af = 3` → ~25% duty
  - `af = 9` → ~10% duty
  You are responsible for choosing a value that is appropriate for your jurisdiction and channel plan (for example EU 868 Mhz 10% duty cycle regulation).

**Default:** `1.0`

---

#### View or change the local interference threshold
**Usage:**
- `get int.thresh`
- `set int.thresh <value>`

**Parameters:**
- `value`: Interference threshold value

**Default:** `1` (Repeater) - `0` (other roles)

---

#### View or change the AGC Reset Interval
**Usage:**
- `get agc.reset.interval`
- `set agc.reset.interval <value>`

**Parameters:**
- `value`: Interval in seconds rounded down to a multiple of 4 (17 becomes 16). 0 to disable.

**Default:** `0.0`

---

#### Enable or disable Multi-Acks support
**Usage:**
- `get multi.acks`
- `set multi.acks <state>`

**Parameters:**
- `state`: `0` (disable) or `1` (enable)

**Default:** `0`

---

#### View or change the flood advert interval
**Usage:**
- `get flood.advert.interval`
- `set flood.advert.interval <hours>`

**Parameters:**
- `hours`: Interval in hours (3-168)

**Default:** `0`

---

#### View or change flood advert forwarding probability
**Usage:**
- `get flood.advert.base`
- `set flood.advert.base <value>`

**Parameters:**
- `value`: Probability base from `0` to `1`. Repeater forwarding probability is `value^(hops - 1)`.

Simple start advice:
- `0`: do not forward received flood adverts.
- `0.308`: dense mesh default; quickly reduces advert noise as hop count grows.
- `1`: forward every flood advert, matching unrestricted forwarding.

**Default:** `0.308` (Repeater)

---

#### View or change flood relay probability
**Usage:**
- `get flood.relay.prob`
- `set flood.relay.prob <value>`

**Parameters:**
- `value`: Integer probability from `0` to `255`, applied to eligible flood forwarding after normal deny, loop, max-hop, and advert-base checks.

Simple start advice:
- `0`: suppress eligible flood forwarding.
- `128`: forward about half of eligible flood packets.
- `255`: normal eligible flood forwarding.

**Default:** `255`

---

#### View or change dense dynamic mode
**Usage:**
- `get flood.dynamic.enable`
- `set flood.dynamic.enable on`
- `set flood.dynamic.enable off`

**Note:** In v2 this is telemetry/recommendation mode only. It does not automatically change advert interval, hop limits, or rebroadcast delay.

**Default:** `off`

---

#### View or change the zero-hop advert interval
**Usage:**
- `get advert.interval`
- `set advert.interval <minutes>`

**Parameters:**
- `minutes`: Interval in minutes rounded down to the nearest multiple of 2 (61 becomes 60) (60-240)

**Default:** `0`

---

#### Limit the number of hops for a flood message
**Usage:**
- `get flood.max`
- `set flood.max <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64)

**Default:** `64`

---

#### Limit the number of hops for an unscoped flood message
**Usage:**
- `get flood.max.unscoped`
- `set flood.max.unscoped <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for a packet without a scope (no region set)

**Default:** `64` - (`0xFF` indicates it hasn't been set, will track flood.max until it is.)

**Note:** An alternative to `region denyf *`, setting `flood.max.unscoped` to a lower value such as `3` would allow for local unscoped messages to propagate, while preventing noisy neighbors from flooding a local region.

---

#### Limit the number of hops for an advert flood message
**Usage:**
- `get flood.max.advert`
- `set flood.max.advert <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for an advert packet

**Default:** `8`

---

#### Limit the number of hops for peer flood messages
**Usage:**
- `get flood.max.messages`
- `set flood.max.messages <value>`

**Parameters:**
- `value`: Maximum flood hop count (0-64) for `REQ`, `RESPONSE`, and `TXT_MSG` packets

**Default:** `64`

**Note:** This only limits peer request/response/text payloads when they are flood-routed. It does not change adverts, ACKs, path returns, trace/control packets, group packets, or direct routing.

---

### ACL

#### Add, update or remove permissions for a companion
**Usage:** 
- `setperm <pubkey> <permissions>`

**Parameters:**
- `pubkey`: Companion public key
- `permissions`: 
  - `0`: Guest
  - `1`: Read-only
  - `2`: Read-write
  - `3`: Admin

**Note:** Removes the entry when `permissions` is omitted

---

#### View the current ACL
**Usage:** 
- `get acl`

**Serial Only:** Yes

---

#### View or change this room server's 'read-only' flag
**Usage:**
- `get allow.read.only`
- `set allow.read.only <state>`

**Parameters:**
- `state`: `on` (enable) or `off` (disable)

**Default:** `off`

---

### Region Management (v1.10.+)

Regions form a local forwarding hierarchy. They let repeaters decide which flood scopes they should carry, so dense meshes can keep local traffic local and reserve wide-area forwarding for repeaters that are meant to act as backbone nodes.

Example hierarchy:

```text
eu
└── nl
    ├── nl-nh
    │   ├── nl-nh-sbc
    │   └── nl-nh-bov
    └── nl-hhw
```

`region allowf <name>` allows flood forwarding for that region on this repeater. `region denyf <name>` blocks flood forwarding for that region on this repeater. Use `region home <name>` to mark the node's own most specific region.

Parent-child regions express scope inheritance: `nl-nh-bov` belongs inside `nl-nh`, which belongs inside `nl`. Forwarding flags are still explicit per region, so allow the parent, child, or both based on the repeater's intended role.

#### Bulk-load region lists
**Usage:** 
- `region load`
- `region load <name> [flood_flag]`

**Parameters:**
- `name`: A name of a region. `*` represents the wildcard region

**Note:** `flood_flag`: Optional `F` to allow flooding

**Note:** Indentation creates parent-child relationships (max 8 levels)

**Note:** `region load` with an empty name will not work remotely (it's interactive)

---

#### Save any changes to regions made since reboot
**Usage:** 
- `region save`

**Purpose:** Persist the current region map, forwarding flags, home region and default region to local storage.

**Behavior:** Region changes are RAM-only until saved. Run this after a working configuration is confirmed.

---

#### Allow a region
**Usage:** 
- `region allowf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Purpose:** Allow this node to forward flood packets for the selected region.

**Example:**
- `region allowf nl-nh-bov`

**Note:** Setting on wildcard `*` allows packets without region transport codes

---

#### Block a region
**Usage:** 
- `region denyf <name>`

**Parameters:** 
- `name`: Region name (or `*` for wildcard)

**Purpose:** Stop this node from forwarding flood packets for the selected region.

**Example:**
- `region denyf eu`

**Note:** Setting on wildcard `*` drops packets without region transport codes

---

#### Show information for a region
**Usage:** 
- `region get <name>`

**Parameters:**
- `name`: Region name (or `*` for wildcard)

---

#### View or change the home region for this node
**Usage:** 
- `region home`
- `region home <name>`

**Parameters:**
- `name`: Region name

**Purpose:** Mark this node's own region. Use the most specific correct region for the repeater's physical location.

**Example:**
- `region home nl-nh-bov`

---

#### View or change the default scope region for this node
**Usage:** 
- `region default`
- `region default {name|<null>}`

**Parameters:**
- `name`: Region name,  or <null> to reset/clear

---

#### Create a new region
**Usage:** 
- `region put <name> [parent_name]`

**Parameters:**
- `name`: Region name
- `parent_name`: Parent region name (optional, defaults to wildcard)

**Purpose:** Add a region to the hierarchy. Parent-child relationships make the tree readable for operators and tools.

**Examples:**
- `region put eu`
- `region put nl eu`
- `region put nl-nh nl`
- `region put nl-nh-bov nl-nh`

---

#### Define region hierarchy (single line)
**Usage:**
- `region def <token> [<token> ...]`

**Parameters (tokens):** Space-separated. A logical **cursor** starts at the wildcard `*`.

- **`name`** — Create `name` as a child of the current cursor (equivalent to `region put name` with the cursor as parent). Cursor moves to `name`.
- **`name|jump`** *(or `name,jump`)* — Create `name` as a child of the current cursor, then move the cursor to `jump` (must already exist on the node, or have been created earlier in this command). `jump` is **not** the parent of `name`; use this form to pop back up and start another branch.

**Behavior:** Each created region defaults to flood-allowed (same as `region put`). The reply is the resulting region tree (same format as bare `region`); review it before running `region save` to persist. On error, the reply is `Err - ...` and any regions placed before the failure remain on the node, just like a partial chain of `region put`.

**Existing regions:** `region def` does not clear the existing tree — if a name already exists, its parent is updated to the current cursor; otherwise a new region is created. To start from scratch, `region remove` the unwanted regions first.

**Limits:** Repeater serial accepts one line up to **160 characters**. For larger trees, split across multiple `region def` commands; the cursor resets to `*` between commands, so lead the next command with `child|ancestor` to reposition. Each token splits at most once on `|` — `region def a|b|c|d` is not a flat-list shorthand; see the flat-list example below.

**Example — linear chain** (each token becomes a child of the previous):
```
region def a b c d e
region save
```

**Example — branched tree** (equivalent to `region put a`, `region put b a`, `region put c b`, `region put d c`, `region put e b`, `region put f e`):
```
region def a b c d|b e f
region save
```

**Example — error and partial state:**
```
region def a b c|nope d
```
The reply is `Err - unknown jump: nope`. `a`, `b`, and `c` were placed before the failure; `d` was not. Run `region` to inspect, then re-run with a corrected jump or repair with `region remove` / `region put`.

**Example — flat list** (each region a child of `*`). Use `|*` after each token to pop the cursor back to the root before the next token:
```
region def a|* b|* c|* d|* e|* f
region save
```

---

#### Remove a region
**Usage:** 
- `region remove <name>`

**Parameters:**
- `name`: Region name

**Note:** Must remove all child regions before the region can be removed 

---

#### View all regions
**Usage:** 
- `region list <filter>`

**Serial Only:** Yes

**Parameters:**
- `filter`: `allowed`|`denied`

**Note:** Requires firmware 1.12+

---

#### Dump all defined regions and flood permissions
**Usage:** 
- `region`
- `region tree`

**Purpose:** Show the configured region hierarchy. `F` means flood forwarding is allowed for that region. `^` marks the home region.

**Serial Only:** For firmware older than 1.12.0

---

#### Example Dutch hierarchy
**Usage:**
```
region put eu
region put nl eu
region put nl-nh nl
region put nl-hhw nl
region put nl-nh-sbc nl-nh
region put nl-nh-bov nl-nh

region allowf eu
region allowf nl
region allowf nl-nh
region allowf nl-hhw
region allowf nl-nh-sbc
region allowf nl-nh-bov

region home nl-nh-bov
region tree
region save
```

**Operational note:** Local repeaters can deny broad regions such as `eu` or `nl` to save airtime. Backbone repeaters can intentionally allow broader regions to connect local meshes.

---

### Dutch Region Database

The Dutch region database is a read-only flash lookup table generated from MeshWiki. It does not consume heap memory and does not change the editable region map until a client chooses to apply a returned code. It is enabled by the `WITH_DUTCH_REGION_DB` build flag. See [Dutch Region Database](./dutch_region_db.md) for the generated format and maintainer workflow.

#### Database metadata
**Usage:**
- `regiondb`
- `regiondb info`

**Example response:**
```
nl-db entries=2484 provinces=12 codes=1611 rev=100 modified=2026-03-23T14:07:28Z
```

#### List province counts
**Usage:**
- `regiondb provinces`

**Example response:**
```
gr:197,fr:413,dr:225,ov:178,fl:19,ge:330,ut:104,nh:238,zh:184,ze:126,nb:279,li:191
```

#### Find a Dutch location by name prefix
**Usage:**
- `regiondb find <prefix> [start_index]`

**Example:**
- `regiondb find gron`

**Example response:**
```
45 Groningen [gr] nl-grq +1
```

Use `start_index` to continue searching after a previous match.

#### Read a Dutch location by index
**Usage:**
- `regiondb get <index>`

**Example response:**
```
45 Groningen [gr] nl-grq,nl-gr-grq
```

#### Resolve an internal code ID
**Usage:**
- `regiondb code <code_id>`

**Example response:**
```
1 nl-grq
```

**Note:** `regiondb` is lookup-only. Use the normal `region` commands to change the runtime region map.

---

### Region Examples

**Example 1: Using F Flag with Named Public Region**
```
region load
#Europe F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a region named `#Europe` with flooding enabled
- Packets from this region will be flooded to other nodes

---

**Example 2: Using Wildcard with F Flag**
```
region load 
* F
<blank line to end region load>
region save
```

**Explanation:**
- Creates a wildcard region `*` with flooding enabled
- Enables flooding for all regions automatically
- Applies only to packets without transport codes

---

**Example 3: Using Wildcard Without F Flag**
```
region load 
*
<blank line to end region load>
region save
```
**Explanation:**
- Creates a wildcard region `*` without flooding
- This region exists but doesn't affect packet distribution
- Used as a default/empty region

---

**Example 4: Nested Public Region with F Flag**
```
region load 
#Europe F
  #UK
    #London
    #Manchester
  #France
    #Paris
    #Lyon
<blank line to end region load>
region save
```

**Explanation:**
- Creates `#Europe` region with flooding enabled
- Adds nested child regions (`#UK`, `#France`)
- The nesting records the intended hierarchy; set `F` or use `region allowf` on child regions that should also forward flood traffic

---

**Example 5: Wildcard with Nested Public Regions**
```
region load 
* F
  #NorthAmerica
    #USA
      #NewYork
      #California
    #Canada
      #Ontario
      #Quebec
<blank line to end region load>
region save
```

**Explanation:**
- Creates wildcard region `*` with flooding enabled
- Adds nested `#NorthAmerica` hierarchy
- Enables flooding for all child regions automatically
- Useful for global networks with specific regional rules

---
### GPS (When GPS support is compiled in)

#### View or change GPS state
**Usage:**
- `gps`
- `gps <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `off`

**Note:** Output format:
- `off` when the GPS hardware is disabled
- `on, {active|deactivated}, {fix|no fix}, {sat count} sats` when the GPS hardware is enabled

---

#### Sync this node's clock with GPS time
**Usage:** 
- `gps sync`

---

#### Set this node's location based on the GPS coordinates
**Usage:** 
- `gps setloc`

---

#### View or change the GPS advert policy
**Usage:**
- `gps advert`
- `gps advert <policy>`

**Parameters:** 
- `policy`: `none`|`share`|`prefs` 
  - `none`: don't include location in adverts
  - `share`: share gps location (from SensorManager)
  - `prefs`: location stored in node's lat and lon settings

**Default:** `prefs`

---

### Sensors (When sensor support is compiled in)

#### View the list of sensors on this node
**Usage:** `sensor list [start]`

**Parameters:**
- `start`: Optional starting index (defaults to 0)

**Note:** Output format: `<var_name>=<value>\n`

---

#### View or change the value of a sensor
**Usage:** 
- `sensor get <key>`
- `sensor set <key> <value>`

**Parameters:**
- `key`: Sensor setting name
- `value`: The value to set the sensor to

---

### Bridge (When bridge support is compiled in)

Four bridge types are available, each compiled in separately:

| Bridge | Build flag | Platform | Use case |
|---|---|---|---|
| **RS232** | `-D WITH_RS232_BRIDGE=Serial2` | All | Wired transport to another local device |
| **ESPNow** | `-D WITH_ESPNOW_BRIDGE=1` | ESP32 | Local wireless transport between ESP32 boards |
| **TCP** | `-D WITH_TCP_BRIDGE=1` | ESP32 | Optional controlled backhaul between selected RF deployments |
| **BLE** | `-D WITH_BLE_BRIDGE=1` | nRF52/Bluefruit, ESP32 BLE | Short-range wireless bridge between BLE-capable repeaters |
| **TCP+BLE** | `-D WITH_TCP_BRIDGE=1 -D WITH_BLE_BRIDGE=1` | Selected 8MB/16MB ESP32 WiFi+BLE | Combined controlled TCP backhaul and short-range BLE bridge transport |

Bridge support is optional and defaults to disabled. MeshCoreNG remains RF-first; bridge transports are intended for controlled deployments such as isolated RF islands, remote RF gateways, temporary backhaul, research setups, and private infrastructure. They are not intended for worldwide uncontrolled flooding or unrestricted packet replication.

Operators are responsible for choosing what should be bridged and for avoiding unnecessary rebroadcast into RF networks. Prefer scoped, private bridge groups and preserve RF locality where possible.

The TCP bridge is a controlled backhaul, not a blind transparent internet mesh. It preserves the MeshCore RF packet format, but TCP-only metadata, duplicate suppression, origin IDs, TTL, export filters, and RF injection controls make it deliberately semi-transparent at the transport boundary.

#### Configure a TCP bridge

The TCP bridge connects bridge-capable repeaters to a selected bridge server. Use it to transport selected traffic between controlled MeshCore RF deployments. A server is required; see `tools/tcp_bridge_server.py`.

**1. Start the server (VPS, Raspberry Pi, or PC):**
```bash
python3 tools/tcp_bridge_server.py --port 4200
```

Optional web-admin path/node quarantine on `/manage`:

```bash
python3 tools/tcp_bridge_server.py --port 4200 \
  --admin-password webAdminSecret \
  --allow-path-block-admin
```

This lets bridge web admins send only the whitelisted `path.block` and `node.block` quarantine commands without entering each repeater's node admin password. The web form can target one selected bridge node or all currently connected bridge nodes. Normal remote CLI commands still require the selected repeater's node admin password.

**2. Configure each intended bridge repeater via CLI:**
```
set wifi.ssid     YourWiFi
set wifi.password secret123
set bridge.server yourserver.example.com
set bridge.port   4200
set bridge.password optionalSecret
set bridge.enabled on
```

**Upgrade note:** MeshCoreNG keeps compatibility with the TCP bridge preferences used before the large merge from the original MeshCore v1.16.0 firmware. When upgrading from older MeshCoreNG bridge builds, saved WiFi and TCP bridge settings are migrated from the legacy layout automatically. If a node was already booted with a build that saved shifted/empty values, re-enter `wifi.ssid`, `wifi.password`, `bridge.server`, `bridge.port`, optionally `bridge.password`, and `bridge.enabled` once.

**3. Available firmware variants** (compile with PlatformIO):
- `Heltec_v3_repeater_bridge_tcp`
- `heltec_v3_433_repeater_bridge_tcp`
- `Heltec_WSL3_repeater_bridge_tcp`
- `heltec_v4_repeater_bridge_tcp`
- `Tbeam_SX1262_repeater_bridge_tcp`
- `Tbeam_SX1276_repeater_bridge_tcp`
- `T_Beam_S3_Supreme_SX1262_repeater_bridge_tcp`
- `LilyGo_TBeam_1W_repeater_bridge_tcp`
- `LilyGo_T3S3_sx1262_repeater_bridge_tcp`
- *(and many others, see the `variants/*/platformio.ini` files)*

---

#### View the compiled bridge type
**Usage:** `get bridge.type`

---

#### View or change the bridge enabled flag
**Usage:**
- `get bridge.enabled`
- `set bridge.enabled <state>`

**Parameters:**
- `state`: `on`|`off`

**Default:** `off`

---

#### Add a delay to packets routed through this bridge
**Usage:**
- `get bridge.delay`
- `set bridge.delay <ms>`

**Parameters:**
- `ms`: Delay in milliseconds (0-10000)

**Default:** `500`

---

#### View or change the source of packets bridged to the external interface
**Usage:**
- `get bridge.source`
- `set bridge.source <source>`

**Parameters:**
- `source`: 
  - `logRx`: bridges received packets
  - `logTx`: bridges transmitted packets
  - `both`: bridges received and transmitted packets

**Default:** `logTx`

---

#### View or change RF forwarding for bridge packets
**Usage:**
- `get bridge.rf`
- `set bridge.rf <state>`

**Parameters:**
- `state`:
  - `off`: do not put bridge-originated packets on RF
  - `on` or `flood`: allow bridge-originated flood packets through the normal repeater forwarding path
  - `local` or `ttl1`: inject bridge-originated packets once on local RF only

In `on`/`flood` mode, flood packets received from the bridge may be forwarded on LoRa RF by the normal repeater forwarding path. Region rules, duplicate checks, loop detection, hop limits, relay probability, retransmit delay, and the normal RF TX queue still apply.

In `local`/`ttl1` mode, packets received from the bridge are transmitted once by this node on local RF. Flood packets are marked with a full path before transmit so other repeaters should process but not re-flood them. Direct packets are transmitted as zero-hop local packets so nearby matching clients can receive them without creating a routed multi-hop bridge loop.

**Default:** `off`

---

#### View or change which packets are exported to the bridge
**Usage:**
- `get bridge.export`
- `set bridge.export <mode>`

**Parameters:**
- `mode`:
  - `all`: export all packets selected by `bridge.source`
  - `flood`: export only flood packets selected by `bridge.source`
  - `channels`: export only channel/group flood packets selected by `bridge.source`
  - `messages`: export DM/chat-related packets and channel/group packets selected by `bridge.source`

`bridge.export` is applied after `bridge.source`. This lets the bridge export RF RX packets independently from the local RF retransmit decision. When a flood packet is exported over the TCP bridge, the exporting bridge-repeater adds its own node hash to the MeshCore packet path if it is not already present and the path still has room.

**Default:** `all`

---

#### Limit exported bridge packets by RF hop count
**Usage:**
- `get bridge.export.maxhops`
- `set bridge.export.maxhops <hops>`

**Parameters:**
- `hops`: Maximum RF path hash count to export, from `0` to `63`. `0` means unlimited.

This is useful for RF island bridges where channel packets heard from several RF hops away should still cross the TCP bridge, but very old floods should stay local.

**Default:** `0`

---

#### View or change TCP bridge envelope TTL
**Usage:**
- `get bridge.tcp.ttl`
- `set bridge.tcp.ttl <ttl>`

**Parameters:**
- `ttl`: TCP bridge envelope TTL, from `1` to `8`.

The TCP bridge v2 envelope carries bridge metadata such as origin bridge ID and TTL outside the MeshCore packet. RF flood packets exported to TCP also carry the exporting bridge-repeater in the MeshCore path, using the packet's existing path hash size and avoiding duplicate entries.

**Default:** `2`

---

#### View or change the TCP bridge group
**Usage:**
- `get bridge.group`
- `set bridge.group <name>`

**Parameters:**
- `name`: Bridge group name, 1-15 characters, without `[]\:,?*`

Bridge clients only interoperate with other bridge clients in the same bridge group when the server requires group matching. The value is also advertised to the TCP bridge server in caps metadata.

**Default:** `default`

---

#### View or set the TCP bridge identity override
**Usage:**
- `get bridge.id`
- `set bridge.id <id>`

**Parameters:**
- `id`: Empty string for automatic identity, or exactly 8 hex characters.

By default the TCP bridge derives a stable bridge ID from the node identity when available, then from device identity fallbacks. Set `bridge.id` only when an operator needs a fixed bridge identity across hardware replacement or a known multi-interface deployment. The active ID is logged at bridge startup and advertised to the TCP bridge server in caps metadata.

**Default:** `auto`

---

#### View or change RF injection budget controls
**Usage:**
- `get bridge.budget`
- `set bridge.budget <state>`
- `set bridge.budget.max_per_min <value>`
- `set bridge.budget.max_airtime_ms_hour <milliseconds>`
- `set bridge.budget.block_duty_above_pct <percent>`

**Parameters:**
- `state`: `on`|`off`
- `value`: Maximum bridge-originated RF injections per minute, from `0` to `10000`
- `milliseconds`: Maximum bridge-originated RF airtime budget per hour
- `percent`: Block bridge RF injection when the node's RF duty use is above this percentage, from `0` to `100`

These settings limit packets injected from the TCP bridge onto local RF. They do not change normal RF-to-RF repeater forwarding.

---

#### View or apply a bridge profile
**Usage:**
- `get bridge.profile`
- `set bridge.profile <profile>`

**Parameters:**
  - `profile`:
  - `default`: conservative defaults (`bridge.source logTx`, `bridge.rf off`, export all, unlimited hops, TCP TTL 2)
  - `island`: RF-island bridge preset (`bridge.source both`, `bridge.rf local`, export message packets up to 4 RF hops, TCP TTL 2)
  - `repeater`: transport-repeater preset (`bridge.source both`, `bridge.rf on`, export all packets, unlimited export hops, TCP TTL 2)

`get bridge.profile` returns the name of the last profile applied (`default`, `island`, or `repeater`). It reflects what was set with `set bridge.profile`, not the current live values of the individual settings. If individual settings have been changed since the last `set bridge.profile`, `get bridge.profile` still returns the last profile name.

The `island` profile is intended for controlled RF islands, for example one bridge node on SF7 and another on SF8. It exports eligible RF RX packets to TCP even when the local repeater policy decides not to retransmit them on RF, and injects packets from TCP once on the receiving RF island.

The `repeater` profile is intended for controlled deployments where the TCP bridge should behave as much like a repeater/backhaul as possible. It exports every packet selected by RF RX/TX and lets bridge-originated flood packets pass through the normal RF repeater forwarding path on the receiving side.

---

#### View or reset bridge runtime statistics
**Usage:**
- `get bridge.status`
- `bridge stats reset`

`get bridge.status` is implemented by bridge builds and returns the current bridge connection/runtime status. `bridge stats reset` clears bridge runtime counters where supported.

---

#### WiFi bridge helper settings
**Usage:**
- `get wifi.ssid`
- `set wifi.ssid <name>`
- `get wifi.pwd`
- `set wifi.pwd <password>`
- `set wifi.password <password>`
- `get wifi.status`
- `get wifi.powersave`
- `set wifi.powersave <mode>`

**Parameters:**
- `mode`: `none`, `min`, or `max`

`wifi.pwd` and `wifi.password` update the same saved WiFi password. WiFi SSID/password changes require a reboot before they are applied by bridge firmware.

---

#### Timezone settings for WiFi/MQTT builds
**Usage:**
- `get timezone`
- `set timezone <name>`
- `get timezone.offset`
- `set timezone.offset <hours>`

**Parameters:**
- `name`: Timezone label such as `UTC`, `Europe/Amsterdam`, `CET`, or `UTC+1`
- `hours`: Fixed UTC offset from `-12` to `+14`

MQTT status formatting uses the timezone string when the MQTT bridge is compiled in.

---

#### MQTT bridge settings
**Build flag:** `WITH_MQTT_BRIDGE`

**Usage:**
- `get mqtt.status`
- `set mqtt.status <state>`
- `get mqtt.packets`
- `set mqtt.packets <state>`
- `get mqtt.raw`
- `set mqtt.raw <state>`
- `get mqtt.tx`
- `set mqtt.tx on|off|advert`
- `get mqtt.rx`
- `set mqtt.rx <state>`
- `get mqtt.interval`
- `set mqtt.interval <minutes>`
- `get mqtt.origin`
- `set mqtt.origin <name>`
- `get mqtt.iata`
- `set mqtt.iata <airport_code>`
- `get mqtt.presets [start]`
- `get mqtt.config.valid`
- `get mqtt.analyzer.us`
- `set mqtt.analyzer.us <state>`
- `get mqtt.analyzer.eu`
- `set mqtt.analyzer.eu <state>`
- `get mqtt.owner`
- `set mqtt.owner <public_key_hex>`
- `get mqtt.email`
- `set mqtt.email <email>`

Slot-specific commands use `mqttN` where `N` is the slot number:

- `get mqttN.preset`
- `set mqttN.preset <preset>`
- `get mqttN.server`
- `set mqttN.server <host>`
- `get mqttN.port`
- `set mqttN.port <port>`
- `get mqttN.username`
- `set mqttN.username <user>`
- `get mqttN.password`
- `set mqttN.password <password>`
- `get mqttN.token`
- `set mqttN.token <token>`
- `get mqttN.topic`
- `set mqttN.topic <template>`
- `get mqttN.audience`
- `set mqttN.audience <audience>`
- `get mqttN.diag`

**Parameters:**
- `state`: `on`|`off`
- `minutes`: MQTT status interval from `1` to `60`
- `preset`: A preset from `get mqtt.presets`, or `custom`, or `none`

The legacy shortcuts `mqtt.analyzer.us` and `mqtt.analyzer.eu` map slot 1 and slot 2 to the analyzer presets.

---

#### SNMP settings
**Build flag:** `WITH_SNMP`

**Usage:**
- `get snmp`
- `set snmp <state>`
- `get snmp.community`
- `set snmp.community <community>`

**Parameters:**
- `state`: `on`|`off`

SNMP changes are saved and take effect after restart.

---

#### Use a Python room server through the TCP bridge

MeshCoreNG includes `tools/python_room_server.py`, a bridge client that can act as a minimal room server from a PC, Raspberry Pi, or VPS.

Run the bridge server:

```bash
python3 tools/tcp_bridge_server.py --port 4200
```

Run the Python room server:

```bash
python3 tools/python_room_server.py --server 127.0.0.1 --port 4200 \
  --bridge-password bridgeSecret \
  --name "Python Room" --password secret
```

On the bridge repeater, enable bridge RF forwarding:

```text
set bridge.enabled on
set bridge.rf on
```

The Python room server stores its identity and recent posts in `python_room_server_state.json` by default. Keep that file if clients should keep recognizing the same room.

---

#### Use Bridge Echo Lab through the TCP bridge

MeshCoreNG includes `tools/bridge_echo_lab.py`, a bridge client that acts as a small diagnostic service for latency, route and packet-loss checks.

Run the bridge server:

```bash
python3 tools/tcp_bridge_server.py --port 4200
```

Run Bridge Echo Lab:

```bash
python3 tools/bridge_echo_lab.py --server 127.0.0.1 --port 4200 \
  --bridge-password bridgeSecret \
  --name "Echo Lab" --password secret
```

On the bridge repeater, enable bridge RF forwarding:

```text
set bridge.enabled on
set bridge.rf on
```

After logging in to Echo Lab, useful commands are `ping bridge`, `trace flood`, `echo direct <text>`, `echo flood <text>`, `delay test <milliseconds>`, `loss test <count>`, `status`, and `id`.

The Echo Lab stores its identity in `bridge_echo_lab_state.json` by default. Keep that file if clients should keep recognizing the same service.

---

#### View or change the speed of the bridge (RS-232 only)
**Usage:**
- `get bridge.baud`
- `set bridge.baud <rate>`

**Parameters:**
- `rate`: Baud rate (`9600`, `19200`, `38400`, `57600`, or `115200`)

**Default:** `115200`

RS232 bridge firmware can be used either through a USB-connected host script or as a direct wired UART bridge between two repeaters:

```text
Repeater A TX  -> Repeater B RX
Repeater A RX  -> Repeater B TX
Repeater A GND -> Repeater B GND
```

Use 3.3V TTL UART levels. Do not connect true +/-12V RS232 directly to board pins.

For Seeed SenseCAP Solar, `SenseCap_Solar_repeater_bridge_rs232` uses `Serial1` on `D6`/`D7`:

```text
D6 = TX = GNSS_TX
D7 = RX = GNSS_RX
```

Connect SenseCAP Solar repeaters as `D6/TX -> D7/RX`, `D7/RX -> D6/TX`, and `GND -> GND`. These pins are shared with the GNSS UART, so GNSS/GPS cannot use that UART at the same time.

---

#### View or change the channel used for bridging (ESPNow only)
**Usage:**
- `get bridge.channel`
- `set bridge.channel <channel>`

**Parameters:**
- `channel`: Channel number (1-14)

---

#### Set the wireless bridge secret
**Usage:** 
- `get bridge.secret`
- `set bridge.secret <secret>`

**Parameters:**
- `secret`: ESP-NOW or BLE bridge secret, up to 15 characters

**Default:** Varies by board

---

#### Set the WiFi SSID (TCP bridge only)
**Usage:**
- `get wifi.ssid`
- `set wifi.ssid <ssid>`

**Parameters:**
- `ssid`: Name of the WiFi network the repeater should join, up to 31 characters

**Note:** Requires `WITH_TCP_BRIDGE` firmware build flag. Reboot to apply.

---

#### Set the WiFi password (TCP bridge only)
**Usage:**
- `set wifi.password <password>`

**Parameters:**
- `password`: WiFi network password, up to 63 characters

**Note:** `get wifi.password` always returns `***` for security.

---

#### Set the TCP bridge server (TCP bridge only)
**Usage:**
- `get bridge.server`
- `set bridge.server <host>`

**Parameters:**
- `host`: Hostname or IP address of the central TCP bridge server

**Note:** Requires `WITH_TCP_BRIDGE` firmware build flag.

---

#### Set the TCP bridge port (TCP bridge only)
**Usage:**
- `get bridge.port`
- `set bridge.port <port>`

**Parameters:**
- `port`: TCP port number (1–65535)

**Default:** 4200

---

#### Set the TCP bridge password (TCP bridge only)
**Usage:**
- `get bridge.password`
- `set bridge.password <password>`

**Parameters:**
- `password`: Optional TCP bridge server password, up to 63 characters

**Note:** `get bridge.password` always returns `***` for security.

---

#### View or enable TCP flood protection (TCP bridge only)
**Usage:**
- `get tcp.flood.limit`
- `set tcp.flood.limit <state>`

**Parameters:**
- `state`: `on`|`off`

Enable or disable TCP flood protection. When enabled, the bridge monitors incoming packet rate from the TCP connection and stops relaying packets when the configured threshold is exceeded within the time window. This prevents mass flooding of the mesh network via the TCP bridge.

**Default:** `off`

**Note:** See [TCP Flood Protection](tcp_flood_protection.md) for detailed information.

---

#### Configure TCP transport flood protection max packets
**Usage:** `set tcp.flood.transport.max <value>`

Configure the maximum number of **transport/message packets** (DMs, group messages, requests) allowed within the transport time window. This applies selective rate limiting to user-generated content while allowing control packets to flow.

**Parameters:**
- `value`: Integer from `1` to `10000`, maximum transport packets in time window

**Default:** `20` packets

**Recommended:** `20` (allows ~20 DMs per 2 minutes)

**Example:**
```
set tcp.flood.transport.max 22
```

---

#### View TCP transport flood protection max packets
**Usage:** `get tcp.flood.transport.max`

---

#### Configure TCP transport flood protection time window
**Usage:** `set tcp.flood.transport.window <value>`

Configure the time window in seconds for transport/message packet rate limiting.

**Parameters:**
- `value`: Integer from `1` to `3600` seconds (1 second to 1 hour)

**Default:** `120` seconds (2 minutes)

**Example:**
```
set tcp.flood.transport.window 120
```

---

#### View TCP transport flood protection time window
**Usage:** `get tcp.flood.transport.window`

---

#### Configure TCP control flood protection max packets
**Usage:** `set tcp.flood.control.max <value>`

Configure the maximum number of **control/admin packets** (discovery, adverts, ACKs, traces) allowed within the control time window. Set to `0` to bypass flood protection for control packets (recommended).

**Parameters:**
- `value`: Integer from `0` to `10000` (`0` = bypass, no limit on control packets)

**Default:** `20` packets

**Recommended:** `20` (same as transport), or `0` to bypass control packets

**Example:**
```
set tcp.flood.control.max 0   # Bypass control packets
```
or
```
set tcp.flood.control.max 500   # Limit to 500 control packets per window
```

---

#### View TCP control flood protection max packets
**Usage:** `get tcp.flood.control.max`

---

#### Configure TCP control flood protection time window
**Usage:** `set tcp.flood.control.window <value>`

Configure the time window in seconds for control/admin packet rate limiting.

**Parameters:**
- `value`: Integer from `1` to `3600` seconds (1 second to 1 hour)

**Default:** `120` seconds (2 minutes)

**Example:**
```
set tcp.flood.control.window 120
```

---

#### View TCP control flood protection time window
**Usage:** `get tcp.flood.control.window`

**Example selective flood protection setup:**
```
set tcp.flood.limit on              # Enable flood protection
set tcp.flood.transport.max 20      # Limit transport (DM/group msg) to 20 per 2 min
set tcp.flood.transport.window 120
set tcp.flood.control.max 20        # Limit control packets to 20 per 2 min (or 0 to bypass)
set tcp.flood.control.window 120
```
This configuration prevents message spam while allowing network control packets to flow freely.

---

#### View the bootloader version (nRF52 only)
**Usage:** `get bootloader.ver`

---

#### View power management support
**Usage:** `get pwrmgt.support`

---

#### View the current power source
**Usage:** `get pwrmgt.source`

**Note:** Returns an error on boards without power management support.

---

#### View the boot reset and shutdown reasons
**Usage:** `get pwrmgt.bootreason`

**Note:** Returns an error on boards without power management support.

---

#### View the boot voltage
**Usage:** `get pwrmgt.bootmv`

**Note:** Returns an error on boards without power management support.

---
