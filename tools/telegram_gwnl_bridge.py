#!/usr/bin/env python3
"""
MeshCoreNG <-> Telegram bridge for a public channel (default: GWNL)

Connects to tools/tcp_bridge_server.py as a bridge client, like
usb_bridge_client.py does. Group text messages sent on the configured public
MeshCore channel are decrypted and relayed to a Telegram chat. Messages sent
to that Telegram chat are encrypted and injected back onto the same channel.

This bridges public group text only (PAYLOAD_TYPE_GRP_TXT). It does not
decrypt or forward direct messages, channel data payloads, or private
channels.

Requires:
    pip install cryptography

Usage:
    python3 tools/telegram_gwnl_bridge.py \\
        --server 127.0.0.1 --port 4200 --bridge-password bridgeSecret \\
        --public-channels-file tools/public_channels.json --channel-name GWNL \\
        --telegram-token <bot-token> --telegram-chat-id <chat-id>

    Or supply the channel secret directly instead of a channel file:
    python3 tools/telegram_gwnl_bridge.py --channel-secret <hex> ...

See website/docs/bridge.md for bridge operating guidelines: bridge only the
channels you need, and keep the mesh->chat rate limited to avoid RF airtime
abuse when relaying back onto the mesh.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    Cipher = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("telegram_gwnl_bridge")

BRIDGE_MAGIC = 0xC03E
MAGIC_HIGH = (BRIDGE_MAGIC >> 8) & 0xFF
MAGIC_LOW = BRIDGE_MAGIC & 0xFF
MAX_PAYLOAD = 256
CONTROL_PREFIX = b"MCNG"
CONTROL_TYPE_AUTH = 0x03
RECONNECT_DELAY = 5

PATH_HASH_SIZE = 1
CIPHER_MAC_SIZE = 2
CIPHER_BLOCK_SIZE = 16
MAX_PACKET_PAYLOAD = 184
MAX_GROUP_MESSAGE_BODY = 150  # "sender: text", stays under MAX_PACKET_PAYLOAD after encryption

PAYLOAD_TYPE_GRP_TXT = 0x05
PH_TYPE_SHIFT = 2
PH_ROUTE_MASK = 0x03
ROUTE_TYPE_TRANSPORT_FLOOD = 0x00
ROUTE_TYPE_FLOOD = 0x01
ROUTE_TYPE_TRANSPORT_DIRECT = 0x03
TXT_TYPE_PLAIN = 0

DEDUPE_WINDOW_SECS = 120
TELEGRAM_POLL_TIMEOUT = 25
TELEGRAM_ORIGIN_TAG = "TG"
MESH_ORIGIN_PREFIX = "[GWNL]"


# --- Bridge framing (same as usb_bridge_client.py / tcp_bridge_server.py) ---

def fletcher16(data: bytes) -> int:
    s1, s2 = 0, 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def build_bridge_frame(payload: bytes) -> bytes:
    return (
        struct.pack(">H", BRIDGE_MAGIC)
        + struct.pack(">H", len(payload))
        + payload
        + struct.pack(">H", fletcher16(payload))
    )


def read_frame_from_tcp(sock: socket.socket) -> bytes | None:
    """Read one complete bridge frame payload (mesh packet bytes) from the socket."""
    def recv_exactly(n: int) -> bytes | None:
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    prev = b""
    while True:
        b = sock.recv(1)
        if not b:
            return None
        candidate = prev + b
        if len(candidate) >= 2 and candidate[-2] == MAGIC_HIGH and candidate[-1] == MAGIC_LOW:
            break
        prev = b

    raw_len = recv_exactly(2)
    if raw_len is None:
        return None
    length = struct.unpack(">H", raw_len)[0]
    if length == 0 or length > MAX_PAYLOAD:
        log.warning("RX: invalid frame length %d, discarding", length)
        return None

    rest = recv_exactly(length + 2)
    if rest is None:
        return None
    return rest[:-2]  # strip trailing checksum, caller only needs the payload


def send_auth(sock: socket.socket, password: str) -> None:
    if not password:
        return
    raw_password = password.encode("utf-8")[:255]
    payload = CONTROL_PREFIX + bytes([CONTROL_TYPE_AUTH, len(raw_password)]) + raw_password
    sock.sendall(build_bridge_frame(payload))


# --- MeshCore group-channel crypto (mirrors src/Utils.cpp + src/Mesh.cpp) ---

def derive_channel_secret(secret_hex: str) -> bytes | None:
    try:
        raw = bytes.fromhex(secret_hex.strip())
    except ValueError:
        return None
    if len(raw) == 16:
        return raw + b"\x00" * 16
    if len(raw) == 32:
        return raw
    return None


def channel_hash(secret: bytes) -> bytes:
    key_len = 16 if secret[16:] == b"\x00" * 16 else 32
    return hashlib.sha256(secret[:key_len]).digest()[:PATH_HASH_SIZE]


def aes_ecb_decrypt(secret: bytes, data: bytes) -> bytes | None:
    if Cipher is None or len(data) == 0 or len(data) % CIPHER_BLOCK_SIZE:
        return None
    decryptor = Cipher(algorithms.AES(secret[:16]), modes.ECB()).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def aes_ecb_encrypt(secret: bytes, data: bytes) -> bytes:
    if len(data) % CIPHER_BLOCK_SIZE:
        data = data + b"\x00" * (CIPHER_BLOCK_SIZE - len(data) % CIPHER_BLOCK_SIZE)
    encryptor = Cipher(algorithms.AES(secret[:16]), modes.ECB()).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def mac_then_decrypt(secret: bytes, data: bytes) -> bytes | None:
    if len(data) <= CIPHER_MAC_SIZE:
        return None
    expected = hmac.new(secret, data[CIPHER_MAC_SIZE:], hashlib.sha256).digest()[:CIPHER_MAC_SIZE]
    if not hmac.compare_digest(expected, data[:CIPHER_MAC_SIZE]):
        return None
    return aes_ecb_decrypt(secret, data[CIPHER_MAC_SIZE:])


def encrypt_then_mac(secret: bytes, plain: bytes) -> bytes:
    ciphertext = aes_ecb_encrypt(secret, plain)
    mac = hmac.new(secret, ciphertext, hashlib.sha256).digest()[:CIPHER_MAC_SIZE]
    return mac + ciphertext


def trim_c_string(data: bytes) -> str:
    return data.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def parse_mesh_payload(frame_payload: bytes) -> dict | None:
    if len(frame_payload) < 2:
        return None
    header = frame_payload[0]
    payload_type = (header >> PH_TYPE_SHIFT) & 0x0F
    route_type = header & PH_ROUTE_MASK
    pos = 1
    if route_type in (ROUTE_TYPE_TRANSPORT_FLOOD, ROUTE_TYPE_TRANSPORT_DIRECT):
        if len(frame_payload) < pos + 4:
            return None
        pos += 4
    if len(frame_payload) <= pos:
        return None
    path_len = frame_payload[pos]
    pos += 1
    path_hash_size = (path_len >> 6) + 1
    path_hash_count = path_len & 63
    path_bytes = path_hash_size * path_hash_count
    if len(frame_payload) < pos + path_bytes:
        return None
    pos += path_bytes
    return {"payload_type": payload_type, "route_type": route_type, "app_payload": frame_payload[pos:]}


def decode_group_text(parsed: dict, secret: bytes, expected_hash: bytes) -> str | None:
    """Return the decoded "sender: text" body, or None if not our channel/undecodable."""
    if parsed["payload_type"] != PAYLOAD_TYPE_GRP_TXT:
        return None
    app_payload = parsed["app_payload"]
    if len(app_payload) <= PATH_HASH_SIZE + CIPHER_MAC_SIZE:
        return None
    if app_payload[:PATH_HASH_SIZE] != expected_hash:
        return None
    plain = mac_then_decrypt(secret, app_payload[PATH_HASH_SIZE:])
    if plain is None or len(plain) < 5:
        return None
    txt_type = plain[4]
    if txt_type >> 2 != 0:
        return None  # not plain text (e.g. signed/CLI variants), skip
    return trim_c_string(plain[5:])


def build_group_text_frame_payload(secret: bytes, hash_byte: bytes, sender_name: str, text: str) -> bytes:
    """Build the mesh packet bytes (header+path_len+app_payload) for a GRP_TXT message."""
    body = f"{sender_name}: {text}"
    if len(body) > MAX_GROUP_MESSAGE_BODY:
        overflow = len(body) - MAX_GROUP_MESSAGE_BODY
        text = text[:-overflow] if len(text) > overflow else ""
        body = f"{sender_name}: {text}"

    timestamp = int(time.time()) & 0xFFFFFFFF
    plain = struct.pack("<I", timestamp) + bytes([TXT_TYPE_PLAIN]) + body.encode("utf-8", "replace") + b"\x00"
    encrypted = encrypt_then_mac(secret, plain)
    app_payload = hash_byte + encrypted

    header = (PAYLOAD_TYPE_GRP_TXT << PH_TYPE_SHIFT) | ROUTE_TYPE_FLOOD
    path_len = 0
    return bytes([header, path_len]) + app_payload


# --- Telegram Bot API (stdlib only, no extra dependency) ---

def telegram_call(token: str, method: str, params: dict, timeout: int) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def telegram_get_me(token: str) -> int | None:
    try:
        result = telegram_call(token, "getMe", {}, timeout=15)
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Telegram getMe failed: %s", e)
        return None
    return result.get("result", {}).get("id") if result.get("ok") else None


def telegram_send_message(token: str, chat_id: str, text: str) -> None:
    try:
        result = telegram_call(
            token, "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            timeout=15,
        )
        if not result.get("ok"):
            log.warning("Telegram sendMessage rejected: %s", result)
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Telegram sendMessage failed: %s", e)


def telegram_poll_updates(token: str, offset: int) -> list[dict]:
    try:
        result = telegram_call(
            token, "getUpdates",
            {"offset": offset, "timeout": TELEGRAM_POLL_TIMEOUT, "allowed_updates": json.dumps(["message"])},
            timeout=TELEGRAM_POLL_TIMEOUT + 10,
        )
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Telegram getUpdates failed: %s", e)
        time.sleep(2)
        return []
    if not result.get("ok"):
        log.warning("Telegram getUpdates rejected: %s", result)
        return []
    return result.get("result", [])


class RateLimiter:
    """Simple token-bucket style rate limiter for RF-bound injects."""

    def __init__(self, max_per_min: int):
        self.max_per_min = max_per_min
        self.sent_times: deque[float] = deque()

    def allow(self) -> bool:
        if self.max_per_min <= 0:
            return True
        now = time.monotonic()
        while self.sent_times and self.sent_times[0] < now - 60:
            self.sent_times.popleft()
        if len(self.sent_times) >= self.max_per_min:
            return False
        self.sent_times.append(now)
        return True


class DedupeGuard:
    """Tracks recently sent ciphertexts to avoid re-relaying our own echoes."""

    def __init__(self, window_secs: int = DEDUPE_WINDOW_SECS):
        self.window_secs = window_secs
        self.recent: deque[tuple[float, bytes]] = deque()

    def remember(self, ciphertext: bytes) -> None:
        now = time.monotonic()
        self.recent.append((now, ciphertext))
        while self.recent and self.recent[0][0] < now - self.window_secs:
            self.recent.popleft()

    def seen(self, ciphertext: bytes) -> bool:
        now = time.monotonic()
        while self.recent and self.recent[0][0] < now - self.window_secs:
            self.recent.popleft()
        return any(ct == ciphertext for _, ct in self.recent)


def load_channel_secret(args) -> bytes | None:
    if args.channel_secret:
        return derive_channel_secret(args.channel_secret)
    if not args.public_channels_file:
        return None
    try:
        data = json.loads(Path(args.public_channels_file).read_text(encoding="utf-8"))
    except OSError as e:
        log.error("Cannot read %s: %s", args.public_channels_file, e)
        return None
    channels = data.get("channels", []) if isinstance(data, dict) else data
    for item in channels or []:
        if str(item.get("name", "")).strip().lower() == args.channel_name.strip().lower():
            return derive_channel_secret(str(item.get("secret", "")))
    return None


def run(args) -> int:
    if Cipher is None:
        log.error("Missing dependency: pip install cryptography")
        return 1

    secret = load_channel_secret(args)
    if secret is None:
        log.error(
            "Could not resolve secret for channel '%s'. Pass --channel-secret or "
            "--public-channels-file with a matching entry.", args.channel_name,
        )
        return 1
    hash_byte = channel_hash(secret)
    log.info("Bridging MeshCore channel '%s' (hash=%s) <-> Telegram chat %s",
              args.channel_name, hash_byte.hex(), args.telegram_chat_id)

    bot_id = telegram_get_me(args.telegram_token)
    if bot_id is None:
        log.warning("Could not verify Telegram bot token via getMe; continuing anyway")

    dedupe = DedupeGuard()
    rate_limiter = RateLimiter(args.rate_limit_per_min)

    sock_lock = threading.Lock()
    current_sock: socket.socket | None = None
    stop = threading.Event()

    def get_sock():
        with sock_lock:
            return current_sock

    def set_sock(s):
        nonlocal current_sock
        with sock_lock:
            current_sock = s

    def mesh_reader():
        while not stop.is_set():
            sock = get_sock()
            if sock is None:
                time.sleep(0.5)
                continue
            try:
                frame_payload = read_frame_from_tcp(sock)
            except OSError:
                time.sleep(0.2)
                continue
            if frame_payload is None:
                log.info("TCP connection lost")
                continue
            parsed = parse_mesh_payload(frame_payload)
            if parsed is None:
                continue
            app_payload = parsed.get("app_payload") or b""
            if len(app_payload) > PATH_HASH_SIZE + CIPHER_MAC_SIZE and dedupe.seen(app_payload[PATH_HASH_SIZE:]):
                continue  # our own injected message, echoed back
            body = decode_group_text(parsed, secret, hash_byte)
            if body is None:
                continue
            log.info("mesh -> telegram: %s", body)
            telegram_send_message(args.telegram_token, args.telegram_chat_id, f"{MESH_ORIGIN_PREFIX} {body}")

    def telegram_poller():
        offset = 0
        while not stop.is_set():
            for update in telegram_poll_updates(args.telegram_token, offset):
                offset = max(offset, update.get("update_id", 0) + 1)
                message = update.get("message") or {}
                if str(message.get("chat", {}).get("id", "")) != str(args.telegram_chat_id):
                    continue
                from_user = message.get("from", {})
                if bot_id is not None and from_user.get("id") == bot_id:
                    continue
                text = message.get("text")
                if not text:
                    continue
                sender = from_user.get("username") or from_user.get("first_name") or "telegram"
                sock = get_sock()
                if sock is None:
                    log.warning("Dropping Telegram message, no mesh connection: %s", text)
                    continue
                if not rate_limiter.allow():
                    log.warning("Rate limit reached, dropping Telegram->mesh message: %s", text)
                    continue
                frame_payload = build_group_text_frame_payload(secret, hash_byte, f"{TELEGRAM_ORIGIN_TAG}:{sender}", text)
                ciphertext = frame_payload[2 + PATH_HASH_SIZE:]
                dedupe.remember(ciphertext)
                try:
                    sock.sendall(build_bridge_frame(frame_payload))
                    log.info("telegram -> mesh: %s: %s", sender, text)
                except OSError as e:
                    log.warning("Failed to send to mesh: %s", e)

    threading.Thread(target=mesh_reader, daemon=True).start()
    threading.Thread(target=telegram_poller, daemon=True).start()

    log.info("Connecting to %s:%d ...", args.server, args.port)
    try:
        while not stop.is_set():
            try:
                sock = socket.create_connection((args.server, args.port), timeout=10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                send_auth(sock, args.bridge_password)
                set_sock(sock)
                log.info("Connected to TCP bridge server %s:%d", args.server, args.port)

                while not stop.is_set():
                    try:
                        sock.settimeout(2)
                        data = sock.recv(1, socket.MSG_PEEK)
                        if not data:
                            break
                    except socket.timeout:
                        pass
                    except OSError:
                        break
            except OSError as e:
                log.warning("TCP connect failed: %s - retrying in %ds", e, RECONNECT_DELAY)
            finally:
                set_sock(None)
                try:
                    sock.close()
                except Exception:
                    pass
            if not stop.is_set():
                time.sleep(RECONNECT_DELAY)
    except KeyboardInterrupt:
        stop.set()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge a MeshCore public channel to/from Telegram")
    parser.add_argument("--server", required=True, help="tcp_bridge_server.py host")
    parser.add_argument("--port", type=int, default=4200, help="tcp_bridge_server.py port (default: 4200)")
    parser.add_argument("--bridge-password", default="", help="Bridge auth password, if the server requires one")
    parser.add_argument("--channel-name", default="GWNL", help="Public channel name (default: GWNL)")
    parser.add_argument("--channel-secret", default="", help="Channel secret as hex (16 or 32 bytes)")
    parser.add_argument("--public-channels-file", default="", help="JSON file to look up --channel-name's secret from")
    parser.add_argument("--telegram-token", required=True, help="Telegram bot token")
    parser.add_argument("--telegram-chat-id", required=True, help="Telegram chat/group ID to bridge with")
    parser.add_argument("--rate-limit-per-min", type=int, default=6,
                         help="Max Telegram->mesh injected messages per minute, 0 disables (default: 6)")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
