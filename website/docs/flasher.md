# Web flasher

## Overview

MeshCoreNG publishes a GitHub Pages web flasher at:

- [MeshCoreNG web flasher](/flasher/)
- [Heltec V3/V4 prerelease flasher](/flasher/heltec-prerelease/)
- [GitHub Releases](https://github.com/MichTronics/MeshCoreNG/releases)

The flasher is a browser frontend for release firmware assets. It does not build firmware in the browser. It lists known board environments from `website/public/flasher/boards.json`, finds matching release files, and either flashes them directly or offers the correct download.

The Heltec V3/V4 prerelease flasher uses the same Web Serial flow, but its CI-generated manifest only contains GitHub prerelease assets for Heltec V3, Heltec V3 433, Heltec V4 and Heltec V4 TFT board variants.

## Browser support

Use Chrome or Edge on a desktop operating system. Web Serial is required for direct flashing and console access.

Linux users may need permission for the serial device, usually by adding the user to the `dialout` group and reconnecting the USB cable.

## Firmware asset types

| Device family | Release asset | Flasher behavior |
|---|---|---|
| ESP32 | merged `.bin` | Direct Web Serial flashing. |
| nRF52 | serial DFU `.zip` | Serial DFU flashing when supported by the bootloader and asset. |
| RP2040 | `.uf2` or release download | Download-only unless a future browser flow is added. |
| STM32/Wio-E5 | `.bin`, `.hex` or release download | Download-only; use the normal vendor or DFU workflow. |
| Other download targets | release asset | Download-only with board-specific instructions. |

`Download` in `boards.json` means the firmware is made available through the flasher page, but the board cannot be flashed through the same Web Serial flow.

## Release asset naming

The flasher matches assets by PlatformIO environment name:

```text
<env>-<version>-<commit>-merged.bin
<env>-<version>-<commit>.zip
<env>-<version>-<commit>.uf2
```

The exact version and commit parts can change, but the environment name must match the `env` field in `boards.json`.

## Release tag build patterns

| Tag pattern | Builds |
|---|---|
| `repeater-*` | All `_repeater` variants. |
| `companion-*` | All `_companion_radio_ble` and `_companion_radio_usb` variants. |
| `room-server-*` | All `_room_server` variants. |
| `bridge-tcp-*` | All `_repeater_bridge_tcp` variants. |
| `bridge-rs232-*` | All `_repeater_bridge_rs232` variants. |
| `bridge-ble-*` | All `_repeater_bridge_ble` variants. |
| `bridge-tcp-ble-*` | All `_repeater_bridge_tcp_ble` variants. |

## Adding a board

Add a new entry to `website/public/flasher/boards.json`:

```json
{
  "env": "Board_env_name_repeater",
  "name": "Board Name Repeater",
  "chipFamily": "ESP32",
  "description": "MeshCoreNG Repeater firmware for Board Name."
}
```

Use `ESP32`, `nRF52`, or `Download` for the current web flasher paths. Keep the `env` exactly the same as the PlatformIO build environment and release asset prefix.

## Wio Tracker L1 notes

Wio Tracker L1 and Wio Tracker L1 E-Ink/L1 Pro firmware entries are included so companion, repeater and room-server variants can be found from the flasher page when release assets exist.

These boards are nRF52-based. If the bootloader supports serial DFU and the release provides a `.zip`, the web flasher can use that path. If the device is in a vendor DFU mode or needs a bootloader update first, download the asset and use the board-specific DFU workflow.

## GitHub Pages publishing

The Pages workflow mirrors release assets into `/flasher/firmware/`. This avoids browser CORS problems that occur when trying to fetch GitHub Release asset bytes directly.

The same workflow also publishes `/flasher/heltec-prerelease/` from prerelease-only assets for all Heltec V3/V4 variants and mirrors the newest flashable prerelease asset per board under `/flasher/heltec-prerelease/firmware/`.

When a release is published, the website should be rebuilt so the flasher uses the exact firmware files from that release. Manual Pages runs use the latest published release.
