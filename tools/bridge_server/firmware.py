"""Firmware update lookup helpers."""

import asyncio
import json
import logging
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import bridge_server.state as state
from bridge_server.constants import FIRMWARE_RELEASE_TAG_RE
from bridge_server.protocol import parse_semver, version_is_older

log = logging.getLogger("tcp_bridge")

_GITHUB_API_BASE = "https://api.github.com"
_RELEASES_PER_PAGE = 100
_MAX_PAGES = 10          # safety cap: up to 1 000 releases total
_MIN_RETRY_SECS = 60     # first retry delay after an error
_MAX_RETRY_SECS = 30 * 60  # exponential back-off cap: 30 minutes


def _fetch_releases_page(repo: str, page: int, timeout: int) -> list:
    """Fetch one page of GitHub releases and return the JSON list."""
    url = (
        f"{_GITHUB_API_BASE}/repos/{repo}/releases"
        f"?per_page={_RELEASES_PER_PAGE}&page={page}"
    )
    req = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MeshCoreNG TCP Bridge Server",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest_firmware_info(repo: str, timeout: int) -> dict:
    """Fetch the latest firmware release metadata from GitHub.

    Iterates through all release pages so the latest firmware is never
    missed when the repository has more than 100 releases.
    """
    best: tuple[int, int, int] | None = None
    best_tag = ""
    best_url = ""
    best_asset_count = 0
    best_bin_count = 0
    best_merged_bin_count = 0

    for page in range(1, _MAX_PAGES + 1):
        items = _fetch_releases_page(repo, page, timeout)
        if not items:
            break  # no more pages

        for item in items:
            tag = str(item.get("tag_name", ""))
            match = FIRMWARE_RELEASE_TAG_RE.match(tag)
            if not match:
                continue
            assets = item.get("assets") or []
            asset_names = [
                str(a.get("name", "")) for a in assets if isinstance(a, dict)
            ]
            bin_count = sum(1 for name in asset_names if name.endswith(".bin"))
            if bin_count == 0:
                continue
            version = parse_semver(match.group(1))
            if version is None:
                continue
            # When two tags share the same version, prefer the one without a
            # type prefix (plain vX.Y.Z) as the generic release.
            same_version = best is not None and version == best
            prefer_current = tag.startswith("v") and not (best_tag or "").startswith("v")
            if best is None or version > best or (same_version and prefer_current):
                best = version
                best_tag = tag
                best_url = (
                    str(item.get("html_url", ""))
                    or f"https://github.com/{repo}/releases/tag/{tag}"
                )
                best_asset_count = len(asset_names)
                best_bin_count = bin_count
                best_merged_bin_count = sum(
                    1 for name in asset_names if name.endswith("-merged.bin")
                )

        if len(items) < _RELEASES_PER_PAGE:
            break  # last page reached

    if best is None:
        raise ValueError(f"no firmware releases with .bin assets found in {repo}")

    latest_version = f"v{best[0]}.{best[1]}.{best[2]}"
    return {
        "enabled": True,
        "checked_at": int(time.time()),
        "latest_version": latest_version,
        "latest_tag": best_tag,
        "latest_url": best_url,
        "asset_count": best_asset_count,
        "bin_count": best_bin_count,
        "merged_bin_count": best_merged_bin_count,
        "status": "ok",
        "error": "",
    }


def firmware_update_status(current_version: str) -> dict:
    """Build firmware update status for a node version."""
    latest = state.latest_firmware_info.get("latest_version", "")
    if not current_version:
        update_state = "unknown"
    elif latest and version_is_older(current_version, latest):
        update_state = "available"
    elif latest and parse_semver(current_version):
        update_state = "current"
    else:
        update_state = "unknown"
    return {
        "state": update_state,
        "current_version": current_version,
        "latest_version": latest,
        "latest_tag": state.latest_firmware_info.get("latest_tag", ""),
        "latest_url": state.latest_firmware_info.get("latest_url", ""),
        "asset_count": state.latest_firmware_info.get("asset_count", 0),
        "bin_count": state.latest_firmware_info.get("bin_count", 0),
        "merged_bin_count": state.latest_firmware_info.get("merged_bin_count", 0),
        "checked_at": state.latest_firmware_info.get("checked_at", 0),
        "check_status": state.latest_firmware_info.get("status", "disabled"),
        "error": state.latest_firmware_info.get("error", ""),
    }


async def firmware_update_task(repo: str, interval_secs: int, timeout_secs: int) -> None:
    """Periodically refresh cached firmware release data.

    Uses exponential back-off on errors (starting at 60 s, capped at 30 min)
    so that a transient network issue recovers quickly instead of waiting the
    full polling interval.
    """
    state.latest_firmware_info.update({
        "enabled": bool(repo and interval_secs > 0),
        "status": "disabled" if not repo or interval_secs <= 0 else "pending",
        "error": "",
    })
    if not repo or interval_secs <= 0:
        return

    retry_delay = _MIN_RETRY_SECS

    while True:
        try:
            loop = asyncio.get_running_loop()
            info = await loop.run_in_executor(
                None, fetch_latest_firmware_info, repo, timeout_secs
            )
            state.latest_firmware_info.update(info)
            log.info(
                "Latest firmware release: %s (%s)",
                info["latest_version"],
                info["latest_tag"],
            )
            retry_delay = _MIN_RETRY_SECS  # reset back-off after a successful fetch
            await asyncio.sleep(interval_secs)
        except (URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
            state.latest_firmware_info.update({
                "enabled": True,
                "checked_at": int(time.time()),
                "status": "error",
                "error": str(exc),
            })
            log.warning(
                "Firmware update check failed: %s — retrying in %ds", exc, retry_delay
            )
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _MAX_RETRY_SECS)
