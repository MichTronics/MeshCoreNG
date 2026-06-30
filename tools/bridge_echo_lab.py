#!/usr/bin/env python3
"""
MeshCoreNG Bridge Echo Lab

Connects to tools/tcp_bridge_server.py as a bridge client and behaves like a
small diagnostic MeshCore service. It is intentionally room-server-like: it
advertises itself, accepts logins, and answers diagnostic text commands without
using public group chat.

Usage:
    python3 tools/bridge_echo_lab.py --server 127.0.0.1 --port 4200 \
        --bridge-password bridgeSecret --name "Echo Lab" --password secret

Requires:
    pip install cryptography
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from python_room_server import (
    ADV_NAME_MASK,
    ADV_TYPE_ROOM,
    BRIDGE_MAGIC,
    CIPHER_MAC_SIZE,
    CONTROL_TYPE_AUTH,
    CONTROL_TYPE_HEARTBEAT,
    CONTROL_TYPE_NODE_INFO,
    FIRMWARE_VER_LEVEL,
    MAX_ADVERT_DATA_SIZE,
    MAX_PAYLOAD,
    OUT_PATH_UNKNOWN,
    PATH_HASH_SIZE,
    PAYLOAD_TYPE_ACK,
    PAYLOAD_TYPE_ADVERT,
    PAYLOAD_TYPE_ANON_REQ,
    PAYLOAD_TYPE_PATH,
    PAYLOAD_TYPE_REQ,
    PAYLOAD_TYPE_RESPONSE,
    PAYLOAD_TYPE_TXT_MSG,
    PUB_KEY_SIZE,
    REQ_TYPE_GET_STATUS,
    REQ_TYPE_KEEP_ALIVE,
    RESP_SERVER_LOGIN_OK,
    ROUTE_TYPE_DIRECT,
    ROUTE_TYPE_FLOOD,
    ROUTE_TYPE_TRANSPORT_FLOOD,
    TXT_TYPE_CLI_DATA,
    TXT_TYPE_PLAIN,
    Packet,
    ed_pub_to_x25519_u,
    encrypt_then_mac,
    fletcher16,
    hash_prefix,
    mac_then_decrypt,
    now_unique,
    transport_key_for_name,
    trunc_c_string,
)


log = logging.getLogger("bridge_echo_lab")

ECHO_LAB_VERSION = "bridge-echo-lab"
HEARTBEAT_INTERVAL_SECS = 30
CLIENT_TIMEOUT_SECS = 900
MAX_REPLY_TEXT_LEN = 151
MAX_LOSS_TEST_PACKETS = 8


@dataclass
class LabClient:
    pub: bytes
    secret: bytes
    last_timestamp: int
    out_path_len: int = OUT_PATH_UNKNOWN
    out_path: bytes = b""
    last_activity: float = field(default_factory=time.time)
    commands: int = 0


class BridgeEchoLab:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.state_path = Path(args.state)
        self.seed, self.pub = self.load_or_create_identity()
        self.ed_private = ed25519.Ed25519PrivateKey.from_private_bytes(self.seed)
        self.private_scalar = self.derive_private_scalar(self.seed)
        self.scope_key = transport_key_for_name(args.scope) if args.scope else None
        self.clients: dict[bytes, LabClient] = {}
        self.seen: list[bytes] = []
        self.last_unique = [0]
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.started_at = time.monotonic()
        self.rx_packets = 0
        self.tx_packets = 0
        self.command_count = 0

    def derive_private_scalar(self, seed: bytes) -> bytes:
        h = bytearray(hashlib.sha512(seed).digest()[:32])
        h[0] &= 248
        h[31] &= 63
        h[31] |= 64
        return bytes(h)

    def load_or_create_identity(self) -> tuple[bytes, bytes]:
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            seed = bytes.fromhex(data["identity_seed"])
            private = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
            pub = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            return seed, pub
        seed = os.urandom(32)
        private = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        pub = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return seed, pub

    def save_state(self) -> None:
        data = {
            "identity_seed": self.seed.hex(),
            "public_key": self.pub.hex(),
            "name": self.args.name,
        }
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.state_path)

    def shared_secret(self, other_pub: bytes) -> bytes:
        private = x25519.X25519PrivateKey.from_private_bytes(self.private_scalar)
        public = x25519.X25519PublicKey.from_public_bytes(ed_pub_to_x25519_u(other_pub))
        return private.exchange(public)

    async def connect_loop(self) -> None:
        while True:
            try:
                log.info("Connecting to bridge server %s:%d", self.args.server, self.args.port)
                self.reader, self.writer = await asyncio.open_connection(self.args.server, self.args.port)
                log.info("Connected. Echo Lab pubkey: %s", self.pub.hex().upper())
                await self.send_auth()
                await self.send_node_info()
                await asyncio.gather(self.read_loop(), self.advert_loop(), self.heartbeat_loop())
            except (OSError, asyncio.IncompleteReadError, ValueError) as exc:
                log.warning("Bridge connection lost: %s", exc)
            finally:
                if self.writer:
                    self.writer.close()
                    try:
                        await self.writer.wait_closed()
                    except Exception:
                        pass
                self.reader = None
                self.writer = None
            await asyncio.sleep(self.args.reconnect_delay)

    async def read_frame(self) -> bytes:
        assert self.reader is not None
        buf = bytearray()
        while True:
            b = await self.reader.readexactly(1)
            buf.append(b[0])
            if len(buf) >= 2:
                if buf[-2] == (BRIDGE_MAGIC >> 8) & 0xFF and buf[-1] == BRIDGE_MAGIC & 0xFF:
                    break
                buf = bytearray([buf[-1]])
        raw_len = await self.reader.readexactly(2)
        length = struct.unpack(">H", raw_len)[0]
        if length == 0 or length > MAX_PAYLOAD:
            raise ValueError(f"invalid bridge payload length {length}")
        payload = await self.reader.readexactly(length)
        raw_csum = await self.reader.readexactly(2)
        received_csum = struct.unpack(">H", raw_csum)[0]
        if received_csum != fletcher16(payload):
            raise ValueError("bridge checksum mismatch")
        return payload

    async def send_payload(self, payload: bytes) -> None:
        if not self.writer:
            return
        csum = fletcher16(payload)
        frame = struct.pack(">HH", BRIDGE_MAGIC, len(payload)) + payload + struct.pack(">H", csum)
        self.writer.write(frame)
        await self.writer.drain()

    async def send_packet(self, pkt: Packet) -> None:
        await self.send_payload(pkt.encode())
        self.tx_packets += 1

    async def send_node_info(self) -> None:
        name = self.args.name.encode("utf-8")[:32]
        version = ECHO_LAB_VERSION.encode("utf-8")[:32]
        await self.send_payload(
            b"MCNG" + bytes([CONTROL_TYPE_NODE_INFO, len(name)]) + name + bytes([len(version)]) + version
        )

    async def send_auth(self) -> None:
        if not self.args.bridge_password:
            return
        password = self.args.bridge_password.encode("utf-8")[:255]
        await self.send_payload(b"MCNG" + bytes([CONTROL_TYPE_AUTH, len(password)]) + password)

    async def send_heartbeat(self) -> None:
        uptime_ms = int((time.monotonic() - self.started_at) * 1000) & 0xFFFFFFFF
        await self.send_payload(b"MCNG" + bytes([CONTROL_TYPE_HEARTBEAT]) + struct.pack(">I", uptime_ms))

    async def heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECS)
            await self.send_heartbeat()
            self.prune_clients()

    async def read_loop(self) -> None:
        while True:
            raw = await self.read_frame()
            if raw.startswith(b"MCNG"):
                continue
            pkt = Packet.parse(raw)
            if not pkt:
                continue
            key = pkt.key()
            if key in self.seen:
                continue
            self.seen.append(key)
            self.seen = self.seen[-128:]
            self.rx_packets += 1
            await self.handle_packet(pkt)

    async def advert_loop(self) -> None:
        while True:
            await self.send_advert()
            await asyncio.sleep(self.args.advert_interval)

    def prune_clients(self) -> None:
        now = time.time()
        for pub, client in list(self.clients.items()):
            if now - client.last_activity > CLIENT_TIMEOUT_SECS:
                del self.clients[pub]

    def calc_transport_codes(self, pkt: Packet) -> tuple[int, int]:
        if not self.scope_key:
            return (0, 0)
        data = bytes([pkt.payload_type]) + pkt.payload
        code = hmac.new(self.scope_key, data, hashlib.sha256).digest()[:2]
        value = struct.unpack("<H", code)[0]
        if value == 0:
            value = 1
        elif value == 0xFFFF:
            value = 0xFFFE
        return (value, 0)

    def flood_packet(self, payload_type: int, payload: bytes, path_hash_size: int = 1) -> Packet:
        route = ROUTE_TYPE_TRANSPORT_FLOOD if self.scope_key else ROUTE_TYPE_FLOOD
        pkt = Packet((payload_type << 2) | route, ((path_hash_size - 1) << 6), b"", payload)
        if self.scope_key:
            pkt.transport_codes = self.calc_transport_codes(pkt)
        return pkt

    def direct_packet(self, payload_type: int, payload: bytes, path_len: int, path: bytes) -> Packet:
        return Packet((payload_type << 2) | ROUTE_TYPE_DIRECT, path_len, path, payload)

    async def send_advert(self) -> None:
        name = self.args.name.encode("utf-8")[: MAX_ADVERT_DATA_SIZE - 1]
        app_data = bytes([ADV_TYPE_ROOM | ADV_NAME_MASK]) + name
        ts = struct.pack("<I", int(time.time()))
        message = self.pub + ts + app_data
        sig = self.ed_private.sign(message)
        payload = self.pub + ts + sig + app_data
        pkt = self.flood_packet(PAYLOAD_TYPE_ADVERT, payload, self.args.path_hash_size)
        await self.send_packet(pkt)
        log.info("Advert sent name=%s id=%s", self.args.name, self.pub.hex()[:8].upper())

    async def handle_packet(self, pkt: Packet) -> None:
        if pkt.payload_type == PAYLOAD_TYPE_ANON_REQ:
            await self.handle_anon_req(pkt)
        elif pkt.payload_type in (PAYLOAD_TYPE_TXT_MSG, PAYLOAD_TYPE_REQ, PAYLOAD_TYPE_PATH):
            await self.handle_peer_packet(pkt)

    async def handle_anon_req(self, pkt: Packet) -> None:
        if len(pkt.payload) < 1 + PUB_KEY_SIZE + CIPHER_MAC_SIZE:
            return
        if pkt.payload[0:1] != hash_prefix(self.pub):
            return
        sender_pub = pkt.payload[1:33]
        secret = self.shared_secret(sender_pub)
        data = mac_then_decrypt(secret, pkt.payload[33:])
        if data is None or len(data) < 8:
            return
        sender_ts = struct.unpack_from("<I", data, 0)[0]
        password = trunc_c_string(data[8:]).decode("utf-8", errors="replace")
        if password != self.args.password and password != self.args.admin_password:
            log.info("Login rejected from %s: bad password", sender_pub.hex()[:12])
            return

        client = self.clients.get(sender_pub)
        if client and sender_ts <= client.last_timestamp:
            return
        client = LabClient(sender_pub, secret, sender_ts)
        self.clients[sender_pub] = client
        log.info("Login ok from %s", sender_pub.hex()[:12])

        now = now_unique(self.last_unique)
        reply = (
            struct.pack("<I", now)
            + bytes([RESP_SERVER_LOGIN_OK, 0, 1, 0x03])
            + os.urandom(4)
            + bytes([FIRMWARE_VER_LEVEL])
        )
        if pkt.is_flood:
            response = self.create_path_return(
                sender_pub,
                secret,
                pkt.path_len,
                pkt.path,
                PAYLOAD_TYPE_RESPONSE,
                reply,
            )
            await self.send_flood_reply(pkt, response)
        else:
            response = self.create_datagram(PAYLOAD_TYPE_RESPONSE, sender_pub, secret, reply)
            await self.send_to_client(client, response, fallback_request=pkt)

    async def handle_peer_packet(self, pkt: Packet) -> None:
        if len(pkt.payload) < 2 + CIPHER_MAC_SIZE:
            return
        if pkt.payload[0:1] != hash_prefix(self.pub):
            return
        src_hash = pkt.payload[1:2]
        for client in list(self.clients.values()):
            if hash_prefix(client.pub) != src_hash:
                continue
            data = mac_then_decrypt(client.secret, pkt.payload[2:])
            if data is None:
                continue
            client.last_activity = time.time()
            if pkt.payload_type == PAYLOAD_TYPE_TXT_MSG:
                await self.handle_text(pkt, client, data)
            elif pkt.payload_type == PAYLOAD_TYPE_REQ:
                await self.handle_request(pkt, client, data)
            elif pkt.payload_type == PAYLOAD_TYPE_PATH:
                await self.handle_path(client, data)
            return

    async def handle_text(self, pkt: Packet, client: LabClient, data: bytes) -> None:
        if len(data) <= 5:
            return
        sender_ts = struct.unpack_from("<I", data, 0)[0]
        flags = data[4] >> 2
        if sender_ts < client.last_timestamp:
            return
        client.last_timestamp = sender_ts
        if flags not in (TXT_TYPE_PLAIN, TXT_TYPE_CLI_DATA):
            return
        cmd = trunc_c_string(data[5:]).decode("utf-8", errors="replace").strip()
        ack_hash = hashlib.sha256(data[:5 + len(cmd.encode("utf-8"))] + client.pub).digest()[:4]
        if flags == TXT_TYPE_PLAIN:
            await self.send_ack(client, ack_hash, pkt)
        log.info("Command from %s: %s", client.pub.hex()[:12], cmd)
        await self.handle_command(pkt, client, cmd)

    async def handle_request(self, pkt: Packet, client: LabClient, data: bytes) -> None:
        if len(data) < 5:
            return
        sender_ts = struct.unpack_from("<I", data, 0)[0]
        if sender_ts < client.last_timestamp:
            return
        client.last_timestamp = sender_ts
        req_type = data[4]
        if req_type == REQ_TYPE_KEEP_ALIVE:
            ack_hash = hashlib.sha256(data[:9].ljust(9, b"\x00") + client.pub).digest()[:4]
            await self.send_to_client(
                client,
                Packet((PAYLOAD_TYPE_ACK << 2) | ROUTE_TYPE_DIRECT, 0, b"", ack_hash + b"\x00"),
                fallback_request=pkt,
            )
        elif req_type == REQ_TYPE_GET_STATUS:
            reply = struct.pack("<I", sender_ts) + self.status_line().encode("utf-8")[:MAX_REPLY_TEXT_LEN]
            payload = self.create_datagram(PAYLOAD_TYPE_RESPONSE, client.pub, client.secret, reply)
            await self.send_to_client(client, payload, fallback_request=pkt)

    async def handle_path(self, client: LabClient, data: bytes) -> None:
        if not data:
            return
        path_len = data[0]
        path_hash_size = (path_len >> 6) + 1
        path_bytes = (path_len & 63) * path_hash_size
        if len(data) < 1 + path_bytes:
            return
        client.out_path_len = path_len
        client.out_path = data[1:1 + path_bytes]
        log.info("Path to %s learned: len=0x%02x", client.pub.hex()[:12], path_len)

    async def handle_command(self, pkt: Packet, client: LabClient, cmd: str) -> None:
        client.commands += 1
        self.command_count += 1
        started = time.monotonic()
        lower = cmd.lower()
        force_flood = False
        force_direct = False

        if not cmd or lower in ("help", "?"):
            reply = self.help_text()
        elif lower in ("status", "stats"):
            reply = self.status_line()
        elif lower == "id":
            reply = self.pub.hex().upper()
        elif lower.startswith("ping"):
            tag = cmd[4:].strip() or str(client.commands)
            uptime = int(time.monotonic() - self.started_at)
            reply = (
                f"pong tag={tag} route={self.route_label(pkt)} seq={client.commands} "
                f"uptime={uptime}s"
            )
        elif lower.startswith("trace flood"):
            force_flood = True
            reply = self.trace_text(pkt, client, forced="flood")
        elif lower.startswith("trace"):
            reply = self.trace_text(pkt, client, forced="auto")
        elif lower.startswith("echo direct"):
            force_direct = True
            text = cmd[len("echo direct"):].strip() or "direct echo"
            reply = f"direct echo: {text}"
        elif lower.startswith("echo flood"):
            force_flood = True
            text = cmd[len("echo flood"):].strip() or "flood echo"
            reply = f"flood echo: {text}"
        elif lower.startswith("echo"):
            text = cmd[4:].strip() or "echo"
            reply = f"echo: {text}"
        elif lower.startswith("delay test"):
            delay_ms = self.parse_delay_ms(cmd[len("delay test"):].strip())
            await asyncio.sleep(delay_ms / 1000.0)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            reply = (
                f"delay ok requested={delay_ms}ms elapsed={elapsed_ms}ms "
                f"route={self.route_label(pkt)}"
            )
        elif lower.startswith("loss test"):
            count = self.parse_count(cmd[len("loss test"):].strip())
            await self.send_loss_test(pkt, client, count)
            return
        else:
            reply = (
                "unknown command; try: help, ping bridge, trace flood, echo direct <txt>, "
                "echo flood <txt>, delay test <ms>, loss test <n>"
            )

        await self.send_text_reply(
            pkt,
            client,
            reply,
            force_flood=force_flood,
            force_direct=force_direct,
        )

    def help_text(self) -> str:
        return (
            "Echo Lab: ping bridge | trace flood | echo direct <txt> | echo flood <txt> | "
            "delay test <ms> | loss test <n> | status | id"
        )

    def status_line(self) -> str:
        uptime = int(time.monotonic() - self.started_at)
        return (
            f"Echo Lab clients={len(self.clients)} rx={self.rx_packets} tx={self.tx_packets} "
            f"cmds={self.command_count} uptime={uptime}s id={self.pub.hex()[:8].upper()}"
        )

    def route_label(self, pkt: Packet) -> str:
        if pkt.route_type == ROUTE_TYPE_FLOOD:
            return "flood"
        if pkt.route_type == ROUTE_TYPE_TRANSPORT_FLOOD:
            return "transport-flood"
        if pkt.route_type == ROUTE_TYPE_DIRECT:
            return "direct"
        return f"route-{pkt.route_type}"

    def trace_text(self, pkt: Packet, client: LabClient, forced: str) -> str:
        path_bytes = pkt.path[:pkt.path_byte_len].hex() or "none"
        out_path = client.out_path.hex() if client.out_path_len != OUT_PATH_UNKNOWN else "unknown"
        return (
            f"trace reply={forced} rx={self.route_label(pkt)} path_hash_size={pkt.path_hash_size} "
            f"hops={pkt.path_hash_count} path={path_bytes} out_path_len={client.out_path_len} "
            f"out_path={out_path} lab={self.pub.hex()[:8].upper()}"
        )

    def parse_delay_ms(self, value: str) -> int:
        try:
            delay_ms = int(value or self.args.default_delay_ms)
        except ValueError:
            delay_ms = self.args.default_delay_ms
        return max(0, min(self.args.max_delay_ms, delay_ms))

    def parse_count(self, value: str) -> int:
        try:
            count = int(value or 3)
        except ValueError:
            count = 3
        return max(1, min(MAX_LOSS_TEST_PACKETS, count))

    async def send_loss_test(self, pkt: Packet, client: LabClient, count: int) -> None:
        test_id = now_unique(self.last_unique)
        for i in range(count):
            reply = f"loss test id={test_id} packet={i + 1}/{count} route={self.route_label(pkt)}"
            await self.send_text_reply(pkt, client, reply)
            await asyncio.sleep(self.args.loss_spacing_ms / 1000.0)

    def create_datagram(self, payload_type: int, dest_pub: bytes, secret: bytes, data: bytes) -> Packet:
        payload = hash_prefix(dest_pub) + hash_prefix(self.pub) + encrypt_then_mac(secret, data)
        return Packet(payload_type << 2, 0, b"", payload)

    def create_path_return(
        self,
        dest_pub: bytes,
        secret: bytes,
        path_len: int,
        path: bytes,
        extra_type: int,
        extra: bytes,
    ) -> Packet:
        path_bytes = (path_len & 63) * ((path_len >> 6) + 1)
        body = bytes([path_len]) + path[:path_bytes] + bytes([extra_type]) + extra
        payload = hash_prefix(dest_pub) + hash_prefix(self.pub) + encrypt_then_mac(secret, body)
        return Packet(PAYLOAD_TYPE_PATH << 2, 0, b"", payload)

    async def send_flood_reply(self, request: Packet, reply: Packet) -> None:
        path_hash_size = (
            request.path_hash_size if request.path_hash_size in (1, 2, 3) else self.args.path_hash_size
        )
        pkt = self.flood_packet(reply.payload_type, reply.payload, path_hash_size)
        await self.send_packet(pkt)

    async def send_to_client(
        self,
        client: LabClient,
        pkt: Packet,
        fallback_request: Packet | None = None,
        force_flood: bool = False,
        force_direct: bool = False,
    ) -> bool:
        if force_flood:
            request = fallback_request or Packet((pkt.payload_type << 2) | ROUTE_TYPE_FLOOD, 0, b"", b"")
            await self.send_flood_reply(request, pkt)
            return True
        if force_direct and client.out_path_len == OUT_PATH_UNKNOWN:
            return False
        if client.out_path_len != OUT_PATH_UNKNOWN:
            direct = self.direct_packet(pkt.payload_type, pkt.payload, client.out_path_len, client.out_path)
            await self.send_packet(direct)
            return True
        if fallback_request is not None:
            await self.send_flood_reply(fallback_request, pkt)
            return True
        flood = self.flood_packet(pkt.payload_type, pkt.payload, self.args.path_hash_size)
        await self.send_packet(flood)
        return True

    async def send_ack(self, client: LabClient, ack_hash: bytes, request: Packet) -> None:
        pkt = Packet((PAYLOAD_TYPE_ACK << 2), 0, b"", ack_hash)
        await self.send_to_client(client, pkt, fallback_request=request)

    async def send_text_reply(
        self,
        request: Packet,
        client: LabClient,
        text: str,
        force_flood: bool = False,
        force_direct: bool = False,
    ) -> None:
        if force_direct and client.out_path_len == OUT_PATH_UNKNOWN:
            text = "direct unavailable: no path learned yet; try a flood login/request first"
            force_direct = False
        body = (
            struct.pack("<I", now_unique(self.last_unique))
            + bytes([TXT_TYPE_CLI_DATA << 2])
            + text.encode("utf-8")[:MAX_REPLY_TEXT_LEN]
        )
        payload = self.create_datagram(PAYLOAD_TYPE_TXT_MSG, client.pub, client.secret, body)
        await self.send_to_client(
            client,
            payload,
            fallback_request=request,
            force_flood=force_flood,
            force_direct=force_direct,
        )
        log.info("Reply sent to %s: %s", client.pub.hex()[:12], text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MeshCoreNG Bridge Echo Lab over TCP bridge")
    parser.add_argument("--server", default="127.0.0.1", help="TCP bridge server host")
    parser.add_argument("--port", type=int, default=4200, help="TCP bridge server port")
    parser.add_argument("--bridge-password", default="", help="optional TCP bridge server password")
    parser.add_argument("--name", default="Echo Lab", help="service name advertised to clients")
    parser.add_argument("--password", default="password", help="service password")
    parser.add_argument("--admin-password", default="password", help="admin password")
    parser.add_argument(
        "--scope",
        default="",
        help="optional public region/scope name for transport-flood packets",
    )
    parser.add_argument(
        "--state",
        default="bridge_echo_lab_state.json",
        help="state file for service identity",
    )
    parser.add_argument("--advert-interval", type=int, default=180, help="advert interval in seconds")
    parser.add_argument(
        "--path-hash-size",
        type=int,
        choices=(1, 2, 3),
        default=1,
        help="flood path hash size",
    )
    parser.add_argument(
        "--default-delay-ms",
        type=int,
        default=1000,
        help="delay used by delay test without value",
    )
    parser.add_argument("--max-delay-ms", type=int, default=30000, help="maximum accepted delay test value")
    parser.add_argument("--loss-spacing-ms", type=int, default=700, help="spacing between loss test replies")
    parser.add_argument("--reconnect-delay", type=int, default=5, help="reconnect delay in seconds")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    server = BridgeEchoLab(args)
    server.save_state()
    asyncio.run(server.connect_loop())


if __name__ == "__main__":
    main()
