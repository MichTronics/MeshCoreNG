#!/usr/bin/env bash

set -eo pipefail

global_usage() {
  cat - <<EOF
Usage:
bash build.sh <command> [target]

Commands:
  help|usage|-h|--help: Shows this message.
  list|-l: List firmwares available to build.
  build-firmware <target>: Build the firmware for the given build target.
  build-firmwares: Build all firmwares for all targets.
  build-matching-firmwares <build-match-spec>: Build all firmwares for build targets containing the string given for <build-match-spec>.
  build-companion-firmwares: Build all companion firmwares for all build targets.
  build-repeater-firmwares: Build all repeater firmwares for all build targets.
  build-room-server-firmwares: Build all chat room server firmwares for all build targets.
  build-gps-tracker-firmwares: Build all GPS tracker firmwares for all build targets.
  build-bridge-tcp-firmwares: Build all TCP internet bridge repeater firmwares (ESP32 with WiFi).
  build-bridge-rs232-firmwares: Build all RS232/USB bridge repeater firmwares (all platforms).
  build-bridge-espnow-firmwares: Build all ESPNow bridge repeater firmwares (ESP32 with ESPNow).
  build-bridge-ble-firmwares: Build all BLE bridge repeater firmwares (nRF52/Bluefruit and ESP32 BLE).
  build-bridge-tcp-ble-firmwares: Build all combined TCP+BLE bridge repeater firmwares (selected ESP32 WiFi+BLE boards).
  build-heltec-v3-v4-firmwares: Build all Heltec V3/V4 variants for the prerelease web flasher.

Examples:
Build firmware for the "RAK_4631_repeater" device target
$ bash build.sh build-firmware RAK_4631_repeater

Build all firmwares for device targets containing the string "RAK_4631"
$ bash build.sh build-matching-firmwares <build-match-spec>

Build all companion firmwares
$ bash build.sh build-companion-firmwares

Build all repeater firmwares
$ bash build.sh build-repeater-firmwares

Build all chat room server firmwares
$ bash build.sh build-room-server-firmwares

Build all GPS tracker firmwares
$ bash build.sh build-gps-tracker-firmwares

Build all ESPNow bridge firmwares
$ bash build.sh build-bridge-espnow-firmwares

Build all BLE bridge firmwares
$ bash build.sh build-bridge-ble-firmwares

Build all combined TCP+BLE bridge firmwares
$ bash build.sh build-bridge-tcp-ble-firmwares

Build all Heltec V3/V4 prerelease web flasher firmwares
$ bash build.sh build-heltec-v3-v4-firmwares

Environment Variables:
  REGION_PROFILE=nl|de|border|none:
                   Selects the default region profile compiled into fresh repeater builds.
                   nl is the default and includes the Dutch region lookup database.
                   de follows German MeshCore region names and disables the Dutch database.
                   border includes NL/DE border scopes and keeps the Dutch lookup database.
  DISABLE_DEBUG=1: Disables all debug logging flags (MESH_DEBUG, MESH_PACKET_LOGGING, etc.)
                   If not set, debug flags from variant platformio.ini files are used.

Examples:
Build without debug logging:
$ export FIRMWARE_VERSION=v1.0.0
$ export DISABLE_DEBUG=1
$ bash build.sh build-firmware RAK_4631_repeater

Build German region profile firmware:
$ export FIRMWARE_VERSION=v1.0.0
$ export REGION_PROFILE=de
$ bash build.sh build-repeater-firmwares

Build with debug logging (default, uses flags from variant files):
$ export FIRMWARE_VERSION=v1.0.0
$ bash build.sh build-firmware RAK_4631_repeater
EOF
}

# get a list of pio env names that start with "env:"
get_pio_envs() {
  pio project config | grep 'env:' | sed 's/env://'
}

# Catch cries for help before doing anything else.
case $1 in
  help|usage|-h|--help)
    global_usage
    exit 1
    ;;
  list|-l)
    get_pio_envs
    exit 0
    ;;
esac

# cache project config json for use in get_platform_for_env()
PIO_CONFIG_JSON=$(pio project config --json-output)
BASE_PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS}"

# $1 should be the string to find (case insensitive)
get_pio_envs_containing_string() {
  shopt -s nocasematch
  envs=($(get_pio_envs))
  for env in "${envs[@]}"; do
      if [[ "$env" == *${1}* ]]; then
        echo $env
      fi
  done
}

# $1 should be the string to find (case insensitive)
get_pio_envs_ending_with_string() {
  shopt -s nocasematch
  envs=($(get_pio_envs))
  for env in "${envs[@]}"; do
    if [[ "$env" == *${1} ]]; then
      echo $env
    fi
  done
}

# get platform flag for a given environment
# $1 should be the environment name
get_platform_for_env() {
  local env_name=$1
  echo "$PIO_CONFIG_JSON" | python3 -c "
import sys, json, re
data = json.load(sys.stdin)
for section, options in data:
    if section == 'env:$env_name':
        for key, value in options:
            if key == 'build_flags':
                for flag in value:
                    match = re.search(r'(ESP32_PLATFORM|NRF52_PLATFORM|STM32_PLATFORM|RP2040_PLATFORM)', flag)
                    if match:
                        print(match.group(1))
                        sys.exit(0)
"
}

# disable all debug logging flags if DISABLE_DEBUG=1 is set
disable_debug_flags() {
  if [ "$DISABLE_DEBUG" == "1" ]; then
    export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UMESH_DEBUG -UBLE_DEBUG_LOGGING -UWIFI_DEBUG_LOGGING -UBRIDGE_DEBUG -UGPS_NMEA_DEBUG -UCORE_DEBUG_LEVEL -UESPNOW_DEBUG_LOGGING -UDEBUG_RP2040_WIRE -UDEBUG_RP2040_SPI -UDEBUG_RP2040_CORE -UDEBUG_RP2040_PORT -URADIOLIB_DEBUG_SPI -UCFG_DEBUG -URADIOLIB_DEBUG_BASIC -URADIOLIB_DEBUG_PROTOCOL"
  fi
}

configure_region_profile_flags() {
  local profile="${REGION_PROFILE:-nl}"
  REGION_PROFILE_SUFFIX=""

  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -UREGION_PROFILE_NL -UREGION_PROFILE_DE -UREGION_PROFILE_NL_DE_BORDER"

  case "$profile" in
    nl)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DREGION_PROFILE_NL=1 -DREGION_PROFILE_DE=0 -DREGION_PROFILE_NL_DE_BORDER=0 -UWITH_DUTCH_REGION_DB -DWITH_DUTCH_REGION_DB=1"
      REGION_PROFILE_SUFFIX="-nl"
      ;;
    de)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DREGION_PROFILE_NL=0 -DREGION_PROFILE_DE=1 -DREGION_PROFILE_NL_DE_BORDER=0 -UWITH_DUTCH_REGION_DB -DWITH_DUTCH_REGION_DB=0"
      REGION_PROFILE_SUFFIX="-de"
      ;;
    border|nl-de-border)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DREGION_PROFILE_NL=0 -DREGION_PROFILE_DE=0 -DREGION_PROFILE_NL_DE_BORDER=1 -UWITH_DUTCH_REGION_DB -DWITH_DUTCH_REGION_DB=1"
      REGION_PROFILE_SUFFIX="-nl-de-border"
      ;;
    none)
      export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DREGION_PROFILE_NL=0 -DREGION_PROFILE_DE=0 -DREGION_PROFILE_NL_DE_BORDER=0 -UWITH_DUTCH_REGION_DB -DWITH_DUTCH_REGION_DB=0"
      REGION_PROFILE_SUFFIX="-none"
      ;;
    *)
      echo "REGION_PROFILE must be one of: nl, de, border, none"
      exit 1
      ;;
  esac
}

# build firmware for the provided pio env in $1
build_firmware() {
  export PLATFORMIO_BUILD_FLAGS="${BASE_PLATFORMIO_BUILD_FLAGS}"
  configure_region_profile_flags

  # get env platform for post build actions
  ENV_PLATFORM=($(get_platform_for_env $1))

  # get git commit sha
  COMMIT_HASH=$(git rev-parse --short HEAD)

  # set firmware build date
  FIRMWARE_BUILD_DATE=$(date '+%d-%b-%Y')

  # get FIRMWARE_VERSION, which should be provided by the environment
  if [ -z "$FIRMWARE_VERSION" ]; then
    echo "FIRMWARE_VERSION must be set in environment"
    exit 1
  fi

  # set firmware version string
  # e.g: v1.0.0-abcdef
  FIRMWARE_VERSION_STRING="${FIRMWARE_VERSION}-${COMMIT_HASH}"

  # craft filename
  # e.g: RAK_4631_Repeater-v1.0.0-SHA
  FIRMWARE_FILENAME="$1${REGION_PROFILE_SUFFIX}-${FIRMWARE_VERSION_STRING}"

  # add firmware version info to end of existing platformio build flags in environment vars
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS} -DFIRMWARE_BUILD_DATE='\"${FIRMWARE_BUILD_DATE}\"' -DFIRMWARE_VERSION='\"${FIRMWARE_VERSION_STRING}\"'"

  # disable debug flags if requested
  disable_debug_flags

  # build firmware target
  pio run -e $1

  # build merge-bin for esp32 fresh install, copy .bins to out folder (e.g: Heltec_v3_room_server-v1.0.0-SHA.bin)
  if [ "$ENV_PLATFORM" == "ESP32_PLATFORM" ]; then
    pio run -t mergebin -e $1
    cp .pio/build/$1/firmware.bin out/${FIRMWARE_FILENAME}.bin 2>/dev/null || true
    cp .pio/build/$1/firmware-merged.bin out/${FIRMWARE_FILENAME}-merged.bin 2>/dev/null || true
  fi

  # build .uf2 for nrf52 boards, copy .uf2 and .zip to out folder (e.g: RAK_4631_Repeater-v1.0.0-SHA.uf2)
  if [ "$ENV_PLATFORM" == "NRF52_PLATFORM" ]; then
    python3 bin/uf2conv/uf2conv.py .pio/build/$1/firmware.hex -c -o .pio/build/$1/firmware.uf2 -f 0xADA52840
    cp .pio/build/$1/firmware.uf2 out/${FIRMWARE_FILENAME}.uf2 2>/dev/null || true
    cp .pio/build/$1/firmware.zip out/${FIRMWARE_FILENAME}.zip 2>/dev/null || true
  fi

  # for stm32, copy .bin and .hex to out folder
  if [ "$ENV_PLATFORM" == "STM32_PLATFORM" ]; then
    cp .pio/build/$1/firmware.bin out/${FIRMWARE_FILENAME}.bin 2>/dev/null || true
    cp .pio/build/$1/firmware.hex out/${FIRMWARE_FILENAME}.hex 2>/dev/null || true
  fi

  # for rp2040, copy .bin and .uf2 to out folder
  if [ "$ENV_PLATFORM" == "RP2040_PLATFORM" ]; then
    cp .pio/build/$1/firmware.bin out/${FIRMWARE_FILENAME}.bin 2>/dev/null || true
    cp .pio/build/$1/firmware.uf2 out/${FIRMWARE_FILENAME}.uf2 2>/dev/null || true
  fi

}

# firmwares containing $1 will be built
build_all_firmwares_matching() {
  envs=($(get_pio_envs_containing_string "$1"))
  for env in "${envs[@]}"; do
      build_firmware $env
  done
}

# firmwares ending with $1 will be built
build_all_firmwares_by_suffix() {
  envs=($(get_pio_envs_ending_with_string "$1"))
  for env in "${envs[@]}"; do
    build_firmware $env
  done
}

build_repeater_firmwares() {

#  # build specific repeater firmwares
#  build_firmware "Heltec_v2_repeater"
#  build_firmware "Heltec_v3_repeater"
#  build_firmware "Xiao_C3_Repeater_sx1262"
#  build_firmware "Xiao_S3_WIO_Repeater"
#  build_firmware "LilyGo_T3S3_sx1262_Repeater"
#  build_firmware "RAK_4631_Repeater"

  # build all repeater firmwares
  build_all_firmwares_by_suffix "_repeater"

}

build_companion_firmwares() {

#  # build specific companion firmwares
#  build_firmware "Heltec_v2_companion_radio_usb"
#  build_firmware "Heltec_v2_companion_radio_ble"
#  build_firmware "Heltec_v3_companion_radio_usb"
#  build_firmware "Heltec_v3_companion_radio_ble"
#  build_firmware "Xiao_S3_WIO_companion_radio_ble"
#  build_firmware "LilyGo_T3S3_sx1262_companion_radio_usb"
#  build_firmware "LilyGo_T3S3_sx1262_companion_radio_ble"
#  build_firmware "RAK_4631_companion_radio_usb"
#  build_firmware "RAK_4631_companion_radio_ble"
#  build_firmware "t1000e_companion_radio_ble"

  # build all companion firmwares
  build_all_firmwares_by_suffix "_companion_radio_usb"
  build_all_firmwares_by_suffix "_companion_radio_ble"

}

build_room_server_firmwares() {

#  # build specific room server firmwares
#  build_firmware "Heltec_v3_room_server"
#  build_firmware "RAK_4631_room_server"

  # build all room server firmwares
  build_all_firmwares_by_suffix "_room_server"

}

build_gps_tracker_firmwares() {

  # build all GPS tracker firmwares
  build_all_firmwares_by_suffix "_gps_tracker"

}

build_bridge_tcp_firmwares() {

  # build all TCP internet bridge repeater firmwares (ESP32 with WiFi only)
  build_all_firmwares_by_suffix "_repeater_bridge_tcp"

}

build_bridge_rs232_firmwares() {

  # build all RS232/USB bridge repeater firmwares (all platforms)
  build_all_firmwares_by_suffix "_repeater_bridge_rs232"

}

build_bridge_espnow_firmwares() {

  # build all ESPNow bridge repeater firmwares (ESP32 with ESPNow)
  build_all_firmwares_by_suffix "_repeater_bridge_espnow"
  build_all_firmwares_by_suffix "_repeater_bridge_espnow_"

}

build_bridge_ble_firmwares() {

  # build all BLE bridge repeater firmwares (nRF52/Bluefruit and ESP32 BLE)
  build_all_firmwares_by_suffix "_repeater_bridge_ble"

}

build_bridge_tcp_ble_firmwares() {

  # build combined TCP+BLE bridge repeater firmwares (selected ESP32 WiFi+BLE boards)
  build_all_firmwares_by_suffix "_repeater_bridge_tcp_ble"

}

build_heltec_v3_v4_firmwares() {

  # build all Heltec V3/V4 variants, including V3 433 and V4 TFT targets
  build_all_firmwares_matching "heltec_v3"
  build_all_firmwares_matching "heltec_v4"

}

build_firmwares() {
  build_companion_firmwares
  build_repeater_firmwares
  build_room_server_firmwares
  build_gps_tracker_firmwares
  build_bridge_tcp_firmwares
  build_bridge_rs232_firmwares
  build_bridge_espnow_firmwares
  build_bridge_ble_firmwares
}

# clean build dir
rm -rf out
mkdir -p out

# handle script args
if [[ $1 == "build-firmware" ]]; then
  TARGETS=${@:2}
  if [ "$TARGETS" ]; then
    for env in $TARGETS; do
      build_firmware $env
    done
  else
    echo "usage: $0 build-firmware <target>"
    exit 1
  fi
elif [[ $1 == "build-matching-firmwares" ]]; then
  if [ "$2" ]; then
     build_all_firmwares_matching $2
  else
     echo "usage: $0 build-matching-firmwares <build-match-spec>"
    exit 1
  fi
elif [[ $1 == "build-firmwares" ]]; then
  build_firmwares
elif [[ $1 == "build-companion-firmwares" ]]; then
  build_companion_firmwares
elif [[ $1 == "build-repeater-firmwares" ]]; then
  build_repeater_firmwares
elif [[ $1 == "build-room-server-firmwares" ]]; then
  build_room_server_firmwares
elif [[ $1 == "build-gps-tracker-firmwares" ]]; then
  build_gps_tracker_firmwares
elif [[ $1 == "build-bridge-tcp-firmwares" ]]; then
  build_bridge_tcp_firmwares
elif [[ $1 == "build-bridge-rs232-firmwares" ]]; then
  build_bridge_rs232_firmwares
elif [[ $1 == "build-bridge-espnow-firmwares" ]]; then
  build_bridge_espnow_firmwares
elif [[ $1 == "build-bridge-ble-firmwares" ]]; then
  build_bridge_ble_firmwares
elif [[ $1 == "build-bridge-tcp-ble-firmwares" ]]; then
  build_bridge_tcp_ble_firmwares
elif [[ $1 == "build-heltec-v3-v4-firmwares" ]]; then
  build_heltec_v3_v4_firmwares
fi
