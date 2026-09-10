#!/usr/bin/env python3
"""
Build the MeshCoreNG web flasher firmware index.

Searches ALL GitHub releases for firmware assets and matches them to the
boards listed in website/public/flasher/boards.json. Boards without a matching
release asset are silently skipped (firmware not yet released).

Writes boards.json to website/.vitepress/dist/flasher/ and mirrors only the
latest flashable firmware asset for each board under /flasher/firmware/. It also
publishes a Heltec V3/V4 prerelease-only flasher under /flasher/heltec-prerelease/.
Web Serial needs browser-readable bytes, and GitHub Release asset URLs do not
provide CORS headers for fetch(). Older releases stay as direct GitHub download
links to keep the Pages artifact small. Run this script AFTER 'vitepress build'.
"""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBFLASHER_SRC  = ROOT / "website" / "public" / "flasher"
BOARDS_FILE     = WEBFLASHER_SRC / "boards.json"
SITE_FLASHER    = ROOT / "website" / ".vitepress" / "dist" / "flasher"
HELTEC_PRERELEASE_FLASHER = SITE_FLASHER / "heltec-prerelease"
HELTEC_V3_V4_PATTERN = re.compile(r"^heltec_v[34](?:_|$)", re.IGNORECASE)


def load_boards():
    with BOARDS_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_pio_env_names():
    env_names = []
    for config_file in [ROOT / "platformio.ini", *sorted((ROOT / "variants").glob("*/platformio.ini"))]:
        if not config_file.exists():
            continue
        section_pattern = re.compile(r"^\s*\[env:([^\]]+)\]")
        with config_file.open("r", encoding="utf-8") as f:
            for line in f:
                match = section_pattern.match(line)
                if match:
                    env_names.append(match.group(1).strip())
    return env_names


def format_env_name(env_name):
    words = re.sub(r"_+", " ", env_name).strip().split()
    return " ".join(word.upper() if word.lower() in ("ble", "usb", "wifi", "tcp", "rs232", "tft", "gps", "mqtt") else word for word in words)


def make_heltec_board(env_name):
    board_name = format_env_name(env_name)
    return {
        "env": env_name,
        "name": board_name,
        "chipFamily": "ESP32",
        "description": f"MeshCoreNG prerelease firmware for {board_name}.",
    }


def github_request(url, token=None, accept="application/vnd.github+json"):
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MeshCoreNG-WebFlasher",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def load_all_release_assets(repo, token):
    """Return a list of release asset metadata across ALL releases."""
    assets = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        try:
            releases = json.loads(github_request(url, token).decode("utf-8"))
        except Exception as e:
            print(f"Warning: could not fetch releases page {page}: {e}", file=sys.stderr)
            break
        if not releases:
            break
        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                name = asset.get("name", "")
                item = dict(asset)
                item["release_tag"] = release.get("tag_name", "")
                item["release_name"] = release.get("name", "")
                item["release_prerelease"] = bool(release.get("prerelease"))
                item["release_published_at"] = release.get("published_at") or asset.get("updated_at", "")
                assets.append(item)
        page += 1
    print(f"Collected {len(assets)} release assets total.", file=sys.stderr)
    return assets


def get_device_type(board):
    family = board.get("chipFamily", "").lower()
    if family.startswith("esp32"):
        return "esp32"
    if family == "nrf52" or "nrf528" in family:
        return "nrf52"
    return "download"


def find_assets_for_board(all_assets, board):
    """Find release assets matching the board, newest first."""
    env_name = board["env"]
    device_type = get_device_type(board)
    if device_type == "esp32":
        pattern = re.compile(rf"^{re.escape(env_name)}-.+-merged\.bin$")
    elif device_type == "nrf52":
        pattern = re.compile(rf"^{re.escape(env_name)}-.+\.zip$")
    else:
        pattern = re.compile(rf"^{re.escape(env_name)}-.+\.(uf2|hex|zip|bin)$")
    board_category = get_category(env_name)
    matches = [
        a for a in all_assets
        if pattern.match(a.get("name", "")) and release_category_matches(a, board_category)
    ]
    matches.sort(key=lambda a: a.get("release_published_at") or a.get("updated_at", ""), reverse=True)
    return matches


def find_ota_asset_for_board(all_assets, board):
    """Find the newest ESP32 app binary for device-pulled OTA."""
    if get_device_type(board) != "esp32":
        return None
    env_name = board["env"]
    pattern = re.compile(rf"^{re.escape(env_name)}-.+\.bin$")
    board_category = get_category(env_name)
    matches = [
        a for a in all_assets
        if pattern.match(a.get("name", "")) and not a.get("name", "").endswith("-merged.bin")
        and release_category_matches(a, board_category)
    ]
    matches.sort(key=lambda a: a.get("release_published_at") or a.get("updated_at", ""), reverse=True)
    return matches[0] if matches else None


def download_asset(asset, destination, token):
    try:
        data = github_request(asset["url"], token, accept="application/octet-stream")
    except urllib.error.HTTPError as api_error:
        browser_url = asset.get("browser_download_url")
        if not browser_url:
            raise
        try:
            data = github_request(browser_url, None, accept="application/octet-stream")
        except urllib.error.HTTPError as browser_error:
            raise RuntimeError(
                f"{asset.get('name', 'asset')} download failed: "
                f"api HTTP {api_error.code}, browser HTTP {browser_error.code}"
            ) from browser_error
    with destination.open("wb") as f:
        f.write(data)


def release_files_for_asset(board, asset, mirrored=True):
    firmware_name = asset["name"]
    firmware_url = asset.get("browser_download_url") or firmware_name
    if not mirrored:
        return [{
            "type": "download",
            "name": firmware_url,
            "title": firmware_name,
        }]

    device_type = get_device_type(board)
    if device_type == "esp32":
        return [{
            "type": "flash",
            "name": firmware_name,
            "title": firmware_name,
        }]
    if device_type == "nrf52":
        return [{
            "type": "flash",
            "name": firmware_name,
            "title": firmware_name,
        }]
    return [{
        "type": "download",
        "name": firmware_name,
        "title": firmware_name,
    }]


def get_category(env_name):
    n = env_name.rstrip("_").lower()
    if n.endswith("_repeater_bridge_tcp_ble"): return "bridge_tcp_ble"
    if n.endswith("_repeater_bridge_ble"):     return "bridge_ble"
    if n.endswith("_repeater_bridge_tcp"):    return "bridge_tcp"
    if n.endswith("_repeater_bridge_rs232"):  return "bridge_rs232"
    if n.endswith("_repeater_bridge_espnow"): return "bridge_espnow"
    if "_logging_repeater" in n:              return "repeater"
    if n.endswith("_repeater"):               return "repeater"
    if n.endswith("_repeatr"):                return "repeater"
    if "_companion_radio_ble" in n or n.endswith("_companion_ble"): return "companion_ble"
    if "_companion_radio_usb" in n or n.endswith("_companion_usb") or n.endswith("_comp_radio_usb"): return "companion_usb"
    if "_companion_radio_wifi" in n:          return "companion_wifi"
    if n.endswith("_room_server") or n.endswith("_room_svr"): return "room_server"
    if n.endswith("_sensor"):                 return "sensor"
    if n.endswith("_kiss_modem"):             return "kiss_modem"
    if n.endswith("_terminal_chat"):          return "terminal_chat"
    return "other"


def get_release_category(asset):
    """Return the firmware category implied by a category-specific release name/tag."""
    text = f"{asset.get('release_tag', '')} {asset.get('release_name', '')}".lower()
    text = text.replace("_", "-")

    # Check longer names first so bridge-tcp-ble is not treated as bridge-tcp.
    release_categories = [
        ("bridge-tcp-ble", "bridge_tcp_ble"),
        ("bridge-espnow", "bridge_espnow"),
        ("bridge-rs232", "bridge_rs232"),
        ("bridge-ble", "bridge_ble"),
        ("bridge-tcp", "bridge_tcp"),
        ("companion-ble", "companion_ble"),
        ("companion-usb", "companion_usb"),
        ("companion-wifi", "companion_wifi"),
        ("room-server", "room_server"),
        ("kiss-modem", "kiss_modem"),
        ("terminal-chat", "terminal_chat"),
        ("repeater", "repeater"),
        ("sensor", "sensor"),
    ]
    for marker, category in release_categories:
        if marker in text:
            return category
    return None


def release_category_matches(asset, board_category):
    release_category = get_release_category(asset)
    return release_category is None or release_category == board_category


def ota_manifest_sort_key(item):
    board, _asset = item
    target = board["env"]
    category = get_category(target)
    category_order = {
        "bridge_tcp": 0,
        "bridge_tcp_ble": 1,
        "bridge_ble": 2,
        "bridge_espnow": 3,
        "bridge_rs232": 4,
        "repeater": 5,
        "companion_ble": 6,
        "companion_usb": 7,
        "companion_wifi": 8,
        "room_server": 9,
    }
    hot_targets = {
        "Heltec_v3_repeater_bridge_tcp": -1,
        "heltec_v3_433_repeater_bridge_tcp": -1,
    }
    return (
        hot_targets.get(target, category_order.get(category, 50)),
        target.lower(),
    )


def is_heltec_v3_v4_board(board):
    return bool(HELTEC_V3_V4_PATTERN.match(board.get("env", "")))


def get_heltec_v3_v4_boards(boards):
    by_env = {board["env"]: board for board in boards if is_heltec_v3_v4_board(board)}
    for env_name in load_pio_env_names():
        if HELTEC_V3_V4_PATTERN.match(env_name) and env_name not in by_env:
            by_env[env_name] = make_heltec_board(env_name)
    return sorted(by_env.values(), key=lambda board: board["env"].lower())


def build_flasher(boards, all_assets, site_flasher=SITE_FLASHER, write_ota_manifest=True, label="Flasher"):
    firmware_dir = site_flasher / "firmware"
    if firmware_dir.exists():
        shutil.rmtree(firmware_dir)
    firmware_dir.mkdir(parents=True, exist_ok=True)

    published = []
    skipped = []

    for board in boards:
        env_name = board["env"]
        device_type = get_device_type(board)
        assets = find_assets_for_board(all_assets, board)
        if not assets:
            skipped.append(env_name)
            continue

        board_dir = firmware_dir / env_name
        board_dir.mkdir(parents=True, exist_ok=True)

        releases = []
        for index, asset in enumerate(assets):
            firmware_name = asset["name"]
            version = asset.get("release_tag") or asset.get("updated_at", "release")[:10]
            mirror_asset = index == 0 and device_type in ("esp32", "nrf52")
            if mirror_asset:
                print(f"  Downloading latest {firmware_name} ...", file=sys.stderr)
                try:
                    download_asset(asset, board_dir / firmware_name, args_token)
                except Exception as e:
                    print(f"Warning: latest mirror failed for {firmware_name}: {e}", file=sys.stderr)
                    mirror_asset = False

            releases.append({
                "version": version,
                "name": asset.get("release_name") or version,
                "published_at": asset.get("release_published_at") or asset.get("updated_at", ""),
                "prerelease": bool(asset.get("release_prerelease")),
                "firmware": firmware_name,
                "files": release_files_for_asset(board, asset, mirrored=mirror_asset),
            })

        if not releases:
            shutil.rmtree(board_dir, ignore_errors=True)
            skipped.append(env_name)
            continue

        latest = releases[0]

        published.append({
            **board,
            "category": get_category(env_name),
            "type": device_type,
            "version": latest["version"],
            "releases": releases,
        })

    site_flasher.mkdir(parents=True, exist_ok=True)
    with (site_flasher / "boards.json").open("w", encoding="utf-8") as f:
        json.dump(published, f, indent=2)
        f.write("\n")

    if not write_ota_manifest:
        print(f"\n{label} built: {len(published)} boards published, {len(skipped)} skipped (no release asset).", file=sys.stderr)
        return published

    ota_assets = []
    for board in published:
        asset = find_ota_asset_for_board(all_assets, board)
        if not asset:
            continue
        ota_assets.append((board, asset))

    ota_lines = [
        "# target|version|size|url|name",
    ]
    for board, asset in sorted(ota_assets, key=ota_manifest_sort_key):
        ota_lines.append("|".join([
            board["env"],
            asset.get("release_tag") or asset.get("updated_at", "release")[:10],
            str(asset.get("size") or 0),
            asset.get("browser_download_url") or "",
            "",
        ]))

    with (site_flasher / "ota-manifest.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(ota_lines))
        f.write("\n")

    print(f"\n{label} built: {len(published)} boards published, {len(skipped)} skipped (no release asset).", file=sys.stderr)
    return published


args_token = None  # set in main()


def parse_args():
    parser = argparse.ArgumentParser(description="Build MeshCoreNG web flasher firmware manifests.")
    parser.add_argument("--repo",  default=os.environ.get("GITHUB_REPOSITORY"),
                        help="GitHub repository in owner/name form.")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"),
                        help="GitHub token for reading release assets.")
    return parser.parse_args()


def main():
    global args_token
    args = parse_args()
    args_token = args.token

    if not args.repo:
        print("Repository is required. Set GITHUB_REPOSITORY or pass --repo owner/name.", file=sys.stderr)
        return 1

    if not SITE_FLASHER.exists():
        print(f"VitePress dist not found at {SITE_FLASHER}. Run 'vitepress build' first.", file=sys.stderr)
        return 1

    boards = load_boards()
    if not boards:
        print(f"{BOARDS_FILE} is empty.", file=sys.stderr)
        return 1

    print(f"Loading release assets from {args.repo} ...", file=sys.stderr)
    all_assets = load_all_release_assets(args.repo, args_token)

    build_flasher(boards, all_assets, label="Flasher")

    heltec_prerelease_boards = get_heltec_v3_v4_boards(boards)
    prerelease_assets = [asset for asset in all_assets if asset.get("release_prerelease")]
    build_flasher(
        heltec_prerelease_boards,
        prerelease_assets,
        site_flasher=HELTEC_PRERELEASE_FLASHER,
        write_ota_manifest=False,
        label="Heltec V3/V4 prerelease flasher",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
