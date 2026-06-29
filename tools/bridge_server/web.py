"""HTTP status and admin handlers."""

import asyncio
import json
import logging
import re
import secrets
import time
from typing import TypeAlias
from urllib.parse import parse_qs

import bridge_server.config as config
import bridge_server.state as state
from bridge_server.client import BridgeClient
from bridge_server.pages import (
    build_location_map_html,
    build_login_html,
    build_manage_html,
    build_status_html,
    prefixed_url,
    request_base_path,
    strip_base_path,
)
from bridge_server.protocol import command_timeout_for, is_ota_update_command
from bridge_server.server import (
    locations_snapshot,
    packets_snapshot,
    refresh_client_block_stats,
    sensors_snapshot,
    status_snapshot,
)

log = logging.getLogger("tcp_bridge")
HttpResponse: TypeAlias = tuple[str, str, bytes, list[tuple[str, str]]]
PATH_BLOCK_RE = re.compile(r"^[0-9a-fA-F]{2}(?:/[0-9a-fA-F]{2}){0,2}$|^[0-9a-fA-F]{4}(?:/[0-9a-fA-F]{4}){0,2}$|^[0-9a-fA-F]{6}(?:/[0-9a-fA-F]{6}){0,2}$")
PATH_BLOCK_DURATION_RE = re.compile(r"^[1-9][0-9]*(?:[mhd])?$")
NODE_BLOCK_RE = re.compile(r"^[0-9a-fA-F]{2}$")

SESSION_DURATION_SECS = 24 * 60 * 60  # 24 hours


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def get_cookie_token(headers: dict[str, str]) -> str:
    """Extract the session token from the Cookie header."""
    for part in headers.get("cookie", "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name.strip() == "session":
            return value.strip()
    return ""


def is_session_valid(token: str) -> bool:
    """Return True if the session token exists and has not expired."""
    if not token:
        return False
    entry = state.web_sessions.get(token)
    if entry is None:
        return False
    expiry, _is_admin = entry
    if time.time() > expiry:
        state.web_sessions.pop(token, None)
        return False
    return True


def is_session_admin(token: str) -> bool:
    """Return True if the session token belongs to an admin user."""
    entry = state.web_sessions.get(token)
    if entry is None:
        return False
    expiry, is_admin = entry
    if time.time() > expiry:
        state.web_sessions.pop(token, None)
        return False
    return is_admin


def create_session(is_admin: bool = False) -> str:
    """Create a new session token, store it, and return it."""
    token = secrets.token_hex(32)
    state.web_sessions[token] = (time.time() + SESSION_DURATION_SECS, is_admin)
    return token


def revoke_session(token: str) -> None:
    """Remove a session token."""
    state.web_sessions.pop(token, None)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _decode_basic_auth(headers: dict[str, str]) -> tuple[str, str]:
    """Decode a Basic auth header into (username, password), or ('', '') on failure."""
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return ("", "")
    try:
        decoded = base64.b64decode(auth[6:].strip()).decode("utf-8", errors="replace")
    except Exception:
        return ("", "")
    user, sep, password = decoded.partition(":")
    return (user, password) if sep else ("", "")


def is_web_authorized(headers: dict[str, str]) -> bool:
    """Return True if the request carries a valid web session (or no password is set)."""
    if not config.WEB_PASSWORD and not config.ADMIN_PASSWORD:
        return True
    return is_session_valid(get_cookie_token(headers))


def is_admin_authorized(headers: dict[str, str]) -> bool:
    """Return True if the request carries a valid admin session."""
    if not config.ADMIN_PASSWORD:
        # No admin account configured → web session is enough
        return is_web_authorized(headers)
    return is_session_admin(get_cookie_token(headers))


def _login_redirect(base_path: str) -> HttpResponse:
    """Redirect an unauthenticated request to the login page."""
    return ("302 Found", "text/plain", b"", [("Location", prefixed_url(base_path, "/login"))])


def _admin_login_redirect(base_path: str) -> HttpResponse:
    """Redirect a non-admin session to the login page with an error hint."""
    return ("302 Found", "text/plain", b"", [("Location", prefixed_url(base_path, "/login?err=admin"))])


def find_client(client_id: str) -> BridgeClient | None:
    """Find a connected client by client ID."""
    for client in state.connected_clients:
        if client.client_id == client_id:
            return client
    return None


def build_path_block_command(form: dict[str, list[str]]) -> str:
    """Build a path block command from form data."""
    action = (form.get('path_action') or [''])[0].strip().lower()
    path = (form.get('path_block') or [''])[0].strip()
    duration = (form.get('path_duration') or [''])[0].strip().lower()
    if action == 'get':
        return 'get path.block'
    if action == 'clear':
        return 'clear path.block'
    if action not in ('add', 'del'):
        raise ValueError('invalid path.block action')
    if not PATH_BLOCK_RE.fullmatch(path):
        raise ValueError('path must be aa, aa/bb, aa/bb/cc, or same-width 2/3-byte hops')
    if action == 'del':
        return f'set path.block del {path}'
    if duration and (not PATH_BLOCK_DURATION_RE.fullmatch(duration)):
        raise ValueError('duration must be seconds, Nm, Nh, or Nd')
    return f'set path.block add {path}' + (f' {duration}' if duration else '')


def build_node_block_command(form: dict[str, list[str]]) -> str:
    """Build a node block command from form data."""
    action = (form.get('node_action') or [''])[0].strip().lower()
    node_id = (form.get('node_block') or [''])[0].strip().lower()
    duration = (form.get('node_duration') or [''])[0].strip().lower()
    if action == 'get':
        return 'get node.block'
    if action == 'clear':
        return 'clear node.block'
    if action not in ('add', 'del'):
        raise ValueError('invalid node.block action')
    if not NODE_BLOCK_RE.fullmatch(node_id):
        raise ValueError('node id must be one hex byte, e.g. a7')
    if action == 'del':
        return f'set node.block del {node_id}'
    if duration and (not PATH_BLOCK_DURATION_RE.fullmatch(duration)):
        raise ValueError('duration must be seconds, Nm, Nh, or Nd')
    return f'set node.block add {node_id}' + (f' {duration}' if duration else '')


async def send_admin_bridge_command(target: str, command: str, label: str) -> str:

    """Send an admin bridge command to one or more nodes."""
    async def send_one(client: BridgeClient) -> str:
        try:
            reply = await client.send_command(command, '')
            asyncio.create_task(refresh_client_block_stats(client, force=True))
            log.info('%s: bridge-admin %s command: %s', client.addr, label, command)
            return f'{client.display_name}> {command}\n{reply}'
        except asyncio.TimeoutError:
            log.warning('%s: %s command timed out', client.addr, label)
            return f'{client.display_name}> {command}\nError: command timed out waiting for bridge reply'
        except Exception as exc:
            return f'{client.display_name}> {command}\nError: {exc}'
    if target == '__all__':
        targets = sorted(list(state.connected_clients), key=lambda c: (c.display_name.lower(), c.client_id))
        if not targets:
            return 'Error: no bridge nodes connected'
        return '\n\n'.join(await asyncio.gather(*(send_one(c) for c in targets)))
    client = find_client(target)
    if client is None:
        return 'Error: selected bridge node is no longer connected'
    return await send_one(client)


def make_http_response(status: str, content_type: str, body: bytes, extra_headers: list[tuple[str, str]] | None=None) -> HttpResponse:
    """Build a normalized HTTP response tuple."""
    return (status, content_type, body, extra_headers or [])


def html_response(page: str) -> HttpResponse:
    """Build an HTML HTTP response."""
    return make_http_response('200 OK', 'text/html; charset=utf-8', page.encode('utf-8'))


def json_response(payload: dict) -> HttpResponse:
    """Build a JSON HTTP response."""
    return make_http_response('200 OK', 'application/json', json.dumps(payload, indent=2).encode('utf-8'))


def text_response(status: str, text: str) -> HttpResponse:
    """Build a plain-text HTTP response."""
    return make_http_response(status, 'text/plain', text.encode('utf-8'))


def parse_content_length(headers: dict[str, str]) -> int:
    """Parse a request Content-Length header."""
    try:
        return max(0, int(headers.get('content-length', '0') or '0'))
    except ValueError:
        return 0


async def read_http_request(reader: asyncio.StreamReader) -> tuple[str, str, str, dict[str, str]]:
    """Read the request line and headers from a client.

    Returns (method, path, query_string, headers).
    """
    request_line = await reader.readline()
    parts = request_line.decode('ascii', errors='ignore').strip().split()
    method = parts[0] if len(parts) >= 1 else ''
    raw_path = parts[1] if len(parts) >= 2 else ''
    path, _, query = raw_path.partition('?')
    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line or line in (b'\r\n', b'\n'):
            break
        name, sep, value = line.decode('iso-8859-1', errors='ignore').partition(':')
        if sep:
            headers[name.strip().lower()] = value.strip()
    return (method, path, query, headers)


async def handle_command_post(reader: asyncio.StreamReader, headers: dict[str, str], base_path: str) -> HttpResponse:
    """Handle the remote management command form."""
    if not is_admin_authorized(headers):
        return _admin_login_redirect(base_path)
    content_length = parse_content_length(headers)
    raw_body = await reader.readexactly(content_length) if content_length > 0 else b''
    form = parse_qs(raw_body.decode('utf-8', errors='replace'), keep_blank_values=True)
    target = (form.get('target') or [''])[0]
    node_password = (form.get('node_password') or [''])[0]
    command = (form.get('command') or [''])[0].strip()
    mode = (form.get('mode') or [''])[0].strip()
    if mode in ('path_block', 'node_block'):
        result = await handle_quarantine_post(form, target, mode)
    else:
        result = await handle_remote_cli_post(target, node_password, command)
    return html_response(build_manage_html(result, base_path))


async def handle_quarantine_post(form: dict[str, list[str]], target: str, mode: str) -> str:
    """Handle a quarantine management request."""
    # Quarantine is available to any authenticated admin session.
    # The --allow-path-block-admin flag is kept for backward compatibility but
    # is no longer required when an admin is logged in via the web interface.
    client = find_client(target)
    try:
        if mode == 'path_block':
            command = build_path_block_command(form)
            label = 'path quarantine'
        else:
            command = build_node_block_command(form)
            label = 'node quarantine'
        return await send_admin_bridge_command(target, command, label)
    except asyncio.TimeoutError:
        log.warning('%s: bridge quarantine command timed out', client.addr if client else target)
        return 'Error: command timed out waiting for bridge reply'
    except Exception as exc:
        return f'Error: {exc}'


async def handle_remote_cli_post(target: str, node_password: str, command: str) -> str:
    """Handle a remote CLI command request."""
    client = find_client(target)
    if client is None:
        return 'Error: selected bridge node is no longer connected'
    if not command:
        return 'Error: empty command'
    if not node_password:
        return 'Error: node admin password required'
    try:
        timeout = command_timeout_for(command)
        if is_ota_update_command(command):
            await client.send_command(command, node_password, wait_reply=False)
            return f'{client.display_name}> {command}\nOK - OTA update command sent. The node may disconnect, reconnect WiFi, download firmware, reboot, and return online without sending a CLI reply.'
        reply = await client.send_command(command, node_password, timeout=timeout)
        return f'{client.display_name}> {command}\n{reply}'
    except asyncio.TimeoutError:
        log.warning('%s: remote command timed out: %s', client.addr, command)
        return f'Error: command timed out waiting for bridge reply after {command_timeout_for(command)}s'
    except Exception as exc:
        return f'Error: {exc}'


def route_get_request(route: str, headers: dict[str, str], base_path: str, query: str = "") -> HttpResponse:
    """Route an HTTP GET request to a response builder."""
    if route == "/login":
        if is_web_authorized(headers):
            return ("302 Found", "text/plain", b"", [("Location", prefixed_url(base_path, "/"))])
        err = "Admin login required to access the management page" if "err=admin" in query else ""
        return html_response(build_login_html(base_path, error=err))
    if route == "/logout":
        token = get_cookie_token(headers)
        revoke_session(token)
        return (
            "302 Found",
            "text/plain",
            b"",
            [
                ("Location", prefixed_url(base_path, "/login")),
                ("Set-Cookie", "session=deleted; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),
            ],
        )
    if not is_web_authorized(headers):
        return _login_redirect(base_path)
    if route == "/status.json":
        return json_response(status_snapshot())
    if route == "/locations.json":
        return json_response(locations_snapshot())
    if route == "/sensors.json":
        return json_response(sensors_snapshot())
    if route == "/packets.json":
        return json_response(packets_snapshot())
    if route == "/map":
        return html_response(build_location_map_html(base_path))
    if route == "/manage":
        if not is_admin_authorized(headers):
            return _admin_login_redirect(base_path)
        return html_response(build_manage_html(base_path=base_path))
    if route in ("/", "/status"):
        return html_response(build_status_html(base_path))
    return text_response("404 Not Found", "Not found\n")


def build_http_headers(status: str, content_type: str, body: bytes, extra_headers: list[tuple[str, str]]) -> bytes:
    """Build raw HTTP response headers."""
    header_lines = [f'HTTP/1.1 {status}', f'Content-Type: {content_type}', f'Content-Length: {len(body)}', 'Connection: close', 'Cache-Control: no-store']
    header_lines.extend((f'{name}: {value}' for name, value in extra_headers))
    return ('\r\n'.join(header_lines) + '\r\n\r\n').encode('ascii')


async def handle_login_post(reader: asyncio.StreamReader, headers: dict[str, str], base_path: str) -> HttpResponse:
    """Handle a login form submission, create a session on success."""
    content_length = parse_content_length(headers)
    raw_body = await reader.readexactly(content_length) if content_length > 0 else b""
    form = parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)
    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]

    # Check admin credentials first
    expected_admin = config.ADMIN_USERNAME or "admin"
    if config.ADMIN_PASSWORD and username == expected_admin and password == config.ADMIN_PASSWORD:
        token = create_session(is_admin=True)
        log.info("Admin web login successful for user %r", username)
        return (
            "302 Found", "text/plain", b"",
            [
                ("Location", prefixed_url(base_path, "/manage")),
                ("Set-Cookie", f"session={token}; HttpOnly; SameSite=Strict; Path=/"),
            ],
        )

    # Check regular web credentials
    expected_user = config.WEB_USERNAME or "user"
    if config.WEB_PASSWORD and username == expected_user and password == config.WEB_PASSWORD:
        token = create_session(is_admin=False)
        log.info("Web login successful for user %r", username)
        return (
            "302 Found", "text/plain", b"",
            [
                ("Location", prefixed_url(base_path, "/")),
                ("Set-Cookie", f"session={token}; HttpOnly; SameSite=Strict; Path=/"),
            ],
        )

    log.warning("Web login failed for user %r", username)
    return html_response(build_login_html(base_path, error="Invalid username or password"))


async def handle_http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle one HTTP status client connection."""
    try:
        method, path, query, request_headers = await read_http_request(reader)
        base_path = request_base_path(request_headers)
        route = strip_base_path(path, base_path)
        if not method or not path:
            response = text_response("405 Method Not Allowed", "Method not allowed\n")
        elif method == "POST" and route == "/login":
            response = await handle_login_post(reader, request_headers, base_path)
        elif method == "POST" and route == "/command":
            response = await handle_command_post(reader, request_headers, base_path)
        elif method != "GET":
            response = text_response("405 Method Not Allowed", "Method not allowed\n")
        else:
            response = route_get_request(route, request_headers, base_path, query)
        status, content_type, body, extra_headers = response
        writer.write(build_http_headers(status, content_type, body, extra_headers) + body)
        await writer.drain()
    except Exception as exc:
        log.warning("HTTP request handler error: %s", exc, exc_info=True)
        try:
            err_body = b"<h1>500 Internal Server Error</h1>"
            writer.write(
                build_http_headers("500 Internal Server Error", "text/html", err_body, [])
                + err_body
            )
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
