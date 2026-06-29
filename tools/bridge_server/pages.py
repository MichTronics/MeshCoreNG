"""HTML page builders for bridge status views."""

import html
from urllib.parse import urlsplit

import bridge_server.config as config
from bridge_server.constants import SERVER_NAME, SERVER_VERSION


def normalize_base_path(path: str) -> str:
    """Normalize a configured status page base path."""
    path = (path or "").strip()
    if not path or path == "/":
        return ""
    return "/" + path.strip("/")


def request_base_path(headers: dict[str, str]) -> str:
    """Return the effective base path for a request."""
    forwarded_prefix = headers.get("x-forwarded-prefix", "") or headers.get("x-script-name", "")
    if forwarded_prefix:
        return normalize_base_path(forwarded_prefix)
    return config.STATUS_BASE_PATH


def prefixed_url(base_path: str, route: str) -> str:
    """Build a URL under the configured base path."""
    base_path = normalize_base_path(base_path)
    if route == "/":
        return base_path or "/"
    if not route.startswith("/"):
        route = "/" + route
    return f"{base_path}{route}"


def strip_base_path(path: str, base_path: str) -> str:
    """Strip the configured base path from a request path."""
    route = urlsplit(path or "/").path or "/"
    base_path = normalize_base_path(base_path)
    if base_path:
        if route == base_path:
            return "/"
        if route.startswith(base_path + "/"):
            return route[len(base_path):] or "/"
    return route


def build_login_html(base_path: str = "", error: str = "") -> str:
    """Generate the login HTML page in the same style as the status page."""
    login_url = prefixed_url(base_path, "/login")
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG Bridge — Login</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #050806;
      --panel: rgba(8, 18, 12, .88);
      --line: rgba(97, 255, 154, .28);
      --line-strong: rgba(97, 255, 154, .55);
      --green: #68ff9d;
      --green-soft: #a1ffc4;
      --red: #ff5f6d;
      --muted: #8fb99e;
      --text: #dfffe9;
      --shadow: 0 18px 60px rgba(0, 0, 0, .45);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: var(--bg); }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 12%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(180deg, rgba(2, 10, 6, .7), rgba(2, 5, 3, .98)),
        var(--bg);
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(104, 255, 157, .04) 50%, rgba(0, 0, 0, .13) 50%),
        linear-gradient(90deg, rgba(255, 0, 0, .025), rgba(0, 255, 95, .018), rgba(0, 120, 255, .025));
      background-size: 100% 4px, 7px 100%;
      mix-blend-mode: screen;
      opacity: .42;
      z-index: 3;
    }}
    .login-wrap {{
      position: relative;
      z-index: 1;
      width: min(420px, 94vw);
      padding: 40px 36px 32px;
      background: linear-gradient(180deg, var(--panel), rgba(3, 10, 6, .92));
      border: 1px solid var(--line);
      box-shadow: var(--shadow), inset 0 0 24px rgba(104, 255, 157, .035);
      border-radius: 8px;
    }}
    .brand {{
      text-align: center;
      margin-bottom: 32px;
    }}
    h1 {{
      margin: 0 0 6px;
      color: var(--green);
      font-size: 1.7rem;
      text-transform: uppercase;
      letter-spacing: .06em;
      text-shadow: 0 0 18px rgba(104, 255, 157, .45);
    }}
    .subtitle {{
      color: var(--muted);
      font-size: .78rem;
      margin: 0;
    }}
    .field {{ margin-bottom: 18px; }}
    label {{
      display: block;
      color: var(--muted);
      font-size: .76rem;
      text-transform: uppercase;
      letter-spacing: .04em;
      margin-bottom: 6px;
    }}
    input[type=text], input[type=password] {{
      width: 100%;
      background: rgba(104, 255, 157, .05);
      border: 1px solid var(--line);
      border-radius: 4px;
      color: var(--text);
      font-family: inherit;
      font-size: .92rem;
      padding: 10px 12px;
      outline: none;
      transition: border-color .15s, box-shadow .15s;
    }}
    input[type=text]:focus, input[type=password]:focus {{
      border-color: var(--green);
      box-shadow: 0 0 14px rgba(104, 255, 157, .18);
    }}
    button[type=submit] {{
      width: 100%;
      margin-top: 8px;
      padding: 11px;
      background: rgba(104, 255, 157, .11);
      border: 1px solid var(--line-strong);
      border-radius: 4px;
      color: var(--green);
      font-family: inherit;
      font-size: .92rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      cursor: pointer;
      transition: border-color .15s, box-shadow .15s, background .15s;
    }}
    button[type=submit]:hover {{
      border-color: var(--green);
      background: rgba(104, 255, 157, .18);
      box-shadow: 0 0 22px rgba(104, 255, 157, .22);
    }}
    .error {{
      margin: 0 0 16px;
      padding: 9px 12px;
      background: rgba(255, 95, 109, .12);
      border: 1px solid rgba(255, 95, 109, .4);
      border-radius: 4px;
      color: var(--red);
      font-size: .84rem;
    }}
    .version {{
      text-align: center;
      margin-top: 24px;
      color: rgba(143, 185, 158, .4);
      font-size: .7rem;
    }}
  </style>
</head>
<body>
  <div class="login-wrap">
    <div class="brand">
      <h1>MeshCore Bridge</h1>
      <p class="subtitle">TCP Bridge Server &mdash; Secure Access</p>
    </div>
    {error_html}
    <form method="post" action="{login_url}">
      <div class="field">
        <label for="username">Username</label>
        <input type="text" id="username" name="username" autocomplete="username" autofocus required>
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" autocomplete="current-password" required>
      </div>
      <button type="submit">Sign In</button>
    </form>
    <p class="version">{SERVER_NAME} v{SERVER_VERSION}</p>
  </div>
</body>
</html>"""


def build_status_html(base_path: str = "") -> str:
    """Generate the status HTML page."""
    manage_url = prefixed_url(base_path, "/manage")
    map_url = prefixed_url(base_path, "/map")
    logout_url = prefixed_url(base_path, "/logout")
    status_json_url = prefixed_url(base_path, "/status.json")
    packets_json_url = prefixed_url(base_path, "/packets.json")
    sensors_json_url = prefixed_url(base_path, "/sensors.json")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG TCP Bridge Status</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #050806;
      --panel: rgba(8, 18, 12, .88);
      --panel-2: rgba(13, 28, 19, .82);
      --line: rgba(97, 255, 154, .28);
      --line-strong: rgba(97, 255, 154, .55);
      --green: #68ff9d;
      --green-soft: #a1ffc4;
      --amber: #ffd166;
      --red: #ff5f6d;
      --muted: #8fb99e;
      --text: #dfffe9;
      --shadow: 0 18px 60px rgba(0, 0, 0, .45);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }}
    * {{ box-sizing: border-box; }}
    html {{ min-height: 100%; background: var(--bg); }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 12%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(180deg, rgba(2, 10, 6, .7), rgba(2, 5, 3, .98)),
        var(--bg);
      overflow-x: hidden;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(104, 255, 157, .04) 50%, rgba(0, 0, 0, .13) 50%),
        linear-gradient(90deg, rgba(255, 0, 0, .025), rgba(0, 255, 95, .018), rgba(0, 120, 255, .025));
      background-size: 100% 4px, 7px 100%;
      mix-blend-mode: screen;
      opacity: .42;
      z-index: 3;
    }}
    main {{
      width: min(1480px, 100%);
      margin: 0 auto;
      padding: 24px;
      position: relative;
      z-index: 1;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
      padding: 18px 0 22px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      color: var(--green);
      font-size: clamp(1.5rem, 3vw, 2.8rem);
      letter-spacing: 0;
      text-transform: uppercase;
      text-shadow: 0 0 16px rgba(104, 255, 157, .45);
    }}
    .subtitle {{ margin: 8px 0 0; color: var(--muted); max-width: 820px; line-height: 1.45; }}
    .server-version {{
      display: inline-block;
      margin-top: 8px;
      color: var(--green-soft);
      font-size: .78rem;
      letter-spacing: 0;
    }}
    nav {{ display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }}
    a {{
      color: var(--green-soft);
      text-decoration: none;
      border: 1px solid var(--line);
      background: rgba(104, 255, 157, .07);
      padding: 9px 12px;
      border-radius: 4px;
    }}
    a:hover {{ border-color: var(--green); box-shadow: 0 0 18px rgba(104, 255, 157, .22); }}
    a.logout {{ border-color: rgba(255, 95, 109, .35); color: var(--red); background: rgba(255, 95, 109, .07); }}
    a.logout:hover {{ border-color: var(--red); box-shadow: 0 0 18px rgba(255, 95, 109, .22); }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(5, minmax(150px, 1fr));
      gap: 12px;
      margin: 20px 0;
    }}
    .metric, .panel {{
      background: linear-gradient(180deg, var(--panel), rgba(3, 10, 6, .92));
      border: 1px solid var(--line);
      box-shadow: var(--shadow), inset 0 0 24px rgba(104, 255, 157, .035);
      border-radius: 6px;
    }}
    .metric {{ padding: 14px; min-height: 96px; }}
    .label {{ color: var(--muted); font-size: .76rem; text-transform: uppercase; }}
    .value {{ margin-top: 8px; color: var(--green); font-size: clamp(1.4rem, 2.4vw, 2.2rem); font-weight: 800; }}
    .value.small {{ font-size: 1rem; overflow-wrap: anywhere; }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(360px, .85fr);
      gap: 16px;
      align-items: start;
    }}
    .panel {{ overflow: hidden; }}
    .panel-head {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(104, 255, 157, .06);
    }}
    h2 {{ margin: 0; font-size: .95rem; color: var(--green-soft); text-transform: uppercase; }}
    .pulse {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: .78rem;
      white-space: nowrap;
    }}
    .pulse::before {{
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 14px var(--green);
    }}
    .pulse.warn::before {{ background: var(--amber); box-shadow: 0 0 14px var(--amber); }}
    .pulse.error::before {{ background: var(--red); box-shadow: 0 0 14px var(--red); }}
    .table-wrap {{ overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{
      padding: 6px 8px;
      text-align: left;
      border-bottom: 1px solid rgba(97, 255, 154, .14);
      white-space: normal;
      vertical-align: top;
      font-size: .78rem;
      line-height: 1.25;
    }}
    th {{ color: var(--muted); background: rgba(0, 0, 0, .24); text-transform: uppercase; font-size: .64rem; }}
    td {{ color: #dfffe9; }}
    tr.hot td {{ color: var(--green-soft); background: rgba(104, 255, 157, .055); }}
    .badge {{
      display: inline-block;
      min-width: 42px;
      text-align: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 1px 6px;
      font-size: .7rem;
      color: var(--green-soft);
      background: rgba(104, 255, 157, .08);
    }}
    .badge.rx {{ color: #86c5ff; border-color: rgba(134, 197, 255, .45); }}
    .badge.tx {{ color: var(--amber); border-color: rgba(255, 209, 102, .45); }}
    .badge.offline {{ color: var(--amber); border-color: rgba(255, 209, 102, .45); }}
    .badge.update {{ color: #ff8f8f; border-color: rgba(255, 143, 143, .55); background: rgba(255, 91, 91, .12); }}
    .badge.current {{ color: var(--green-soft); border-color: rgba(104, 255, 157, .42); background: rgba(104, 255, 157, .08); }}
    .badge.pending {{ color: var(--amber); border-color: rgba(255, 209, 102, .42); background: rgba(255, 209, 102, .08); }}
    .badge.error {{ color: #ff8f8f; border-color: rgba(255, 143, 143, .55); background: rgba(255, 91, 91, .12); }}
    .packet-age {{ width: 54px; }}
    .packet-dir {{ width: 40px; }}
    .packet-flow {{ width: 24%; }}
    .packet-kind {{ width: 23%; }}
    .packet-data {{ width: auto; }}
    .packet-main {{ color: #dfffe9; overflow-wrap: anywhere; }}
    .packet-sub {{ color: var(--muted); font-size: .7rem; margin-top: 2px; overflow-wrap: anywhere; }}
    .preview {{ max-width: 100%; white-space: normal; overflow-wrap: anywhere; color: var(--muted); }}
    .empty {{ text-align: center; color: var(--muted); padding: 28px; }}
    .feed {{
      padding: 8px 10px;
      height: 430px;
      overflow: auto;
      background: rgba(0, 0, 0, .24);
    }}
    .feed-line {{
      display: grid;
      grid-template-columns: 48px 28px minmax(88px, 1fr);
      gap: 7px;
      padding: 3px 0;
      border-bottom: 1px dashed rgba(97, 255, 154, .14);
      font-size: .74rem;
      line-height: 1.25;
    }}
    .feed-line .meta {{ color: var(--muted); font-variant-numeric: tabular-nums; }}
    .feed-line .dir-rx {{ color: #86c5ff; font-weight: 800; }}
    .feed-line .dir-tx {{ color: var(--amber); font-weight: 800; }}
    .feed-line .packet {{ overflow-wrap: anywhere; }}
    .stack {{ display: grid; gap: 16px; }}
    .node-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 8px;
      padding: 10px;
    }}
    .node-card {{
      border: 1px solid rgba(97, 255, 154, .22);
      background: var(--panel-2);
      border-radius: 6px;
      padding: 9px;
      min-height: 0;
    }}
    .node-card.offline {{
      border-color: rgba(255, 209, 102, .2);
      background: rgba(15, 18, 18, .7);
    }}
    .node-title {{ color: var(--green-soft); font-size: .92rem; font-weight: 800; overflow-wrap: anywhere; }}
    .node-meta {{ margin-top: 5px; color: var(--muted); font-size: .72rem; line-height: 1.25; overflow-wrap: anywhere; }}
    .node-stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(68px, 1fr));
      gap: 5px;
      margin-top: 8px;
    }}
    .mini {{
      min-width: 0;
      border: 1px solid rgba(97, 255, 154, .14);
      background: rgba(0, 0, 0, .16);
      padding: 5px 6px;
      border-radius: 4px;
      overflow: hidden;
    }}
    .mini .label {{ display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .mini b {{
      display: block;
      min-width: 0;
      color: var(--green);
      font-size: .82rem;
      font-variant-numeric: tabular-nums;
      line-height: 1.15;
      margin-top: 2px;
      overflow-wrap: anywhere;
    }}
    @media (max-width: 980px) {{
      main {{ padding: 16px 12px; }}
      .topbar {{ display: block; }}
      nav {{ justify-content: flex-start; margin-top: 14px; }}
      .status-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid {{ grid-template-columns: 1fr; }}
      .feed {{ height: 340px; }}
    }}
    @media (max-width: 560px) {{
      .status-strip {{ grid-template-columns: 1fr; }}
      .node-grid {{ grid-template-columns: 1fr; }}
      .node-stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      table, thead, tbody, tr, th, td {{ display: block; width: 100%; }}
      thead {{ display: none; }}
      tr {{
        padding: 7px 8px;
        border-bottom: 1px solid rgba(97, 255, 154, .18);
      }}
      th, td {{
        border-bottom: 0;
        padding: 2px 0;
      }}
      td.packet-age {{
        color: var(--muted);
        font-size: .7rem;
      }}
      td:nth-child(2) {{
        margin: 2px 0;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>TCP Bridge Tactical Console</h1>
        <div class="server-version">{html.escape(SERVER_NAME)} v{html.escape(SERVER_VERSION)}</div>
        <p class="subtitle">MeshCoreNG live bridge telemetry, packet flow and nearby sensor adverts. Polling the bridge server every 2 seconds.</p>
      </div>
      <nav>
        <a href="{manage_url}">Remote management</a>
        <a href="{map_url}">Tracker map</a>
        <a href="{logout_url}" class="logout">Sign out</a>
      </nav>
    </header>

    <section class="status-strip" aria-label="Live counters">
      <div class="metric"><div class="label">Bridge nodes online</div><div id="metricConnected" class="value">--</div></div>
      <div class="metric"><div class="label">Packet history</div><div id="metricPackets" class="value">--</div></div>
      <div class="metric"><div class="label">Packets total</div><div id="metricPacketsTotal" class="value">--</div></div>
      <div class="metric"><div class="label">Total dedups</div><div id="metricDedups" class="value">--</div></div>
      <div class="metric"><div class="label">Short-ID quarantine</div><div id="metricShortQ" class="value">--</div></div>
      <div class="metric"><div class="label">ID/path block drops</div><div id="metricBlockDrops" class="value">--</div></div>
      <div class="metric"><div class="label">Nearby sensors</div><div id="metricSensors" class="value">--</div></div>
      <div class="metric" title="TCP bridge packets seen by this webserver, not repeater RF RT/TX counters"><div class="label">Bridge RX / TX 24h</div><div id="metricTraffic" class="value small">-- / --</div></div>
      <div class="metric"><div class="label">Last sync</div><div id="metricSync" class="value small">booting</div></div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>Bridge nodes</h2>
        <span id="nodeStatus" class="pulse warn">connecting</span>
      </div>
      <div id="nodeCards" class="node-grid"></div>
    </section>

    <div class="grid" style="margin-top:16px">
      <section class="panel">
        <div class="panel-head">
          <h2>Packet log</h2>
          <span id="packetStatus" class="pulse warn">waiting</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="packet-age">Age</th>
                <th class="packet-dir">Dir</th>
                <th class="packet-flow">Flow</th>
                <th class="packet-kind">Packet</th>
                <th class="packet-data">Data</th>
              </tr>
            </thead>
            <tbody id="packetRows">
              <tr><td colspan="5" class="empty">Loading packet telemetry</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="stack">
        <section class="panel">
          <div class="panel-head">
            <h2>Live terminal feed</h2>
            <span id="feedStatus" class="pulse warn">arming</span>
          </div>
          <div id="packetFeed" class="feed"></div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>Sensor nodes nearby</h2>
            <span id="sensorStatus" class="pulse warn">scanning</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Node ID</th>
                  <th>Last seen</th>
                  <th>Seen</th>
                  <th>Hops</th>
                  <th>Via bridge</th>
                  <th>Location</th>
                </tr>
              </thead>
              <tbody id="sensorRows">
                <tr><td colspan="7" class="empty">Scanning for sensor adverts</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  </main>
  <script>
    const urls = {{
      status: "{status_json_url}",
      packets: "{packets_json_url}",
      sensors: "{sensors_json_url}"
    }};
    const state = {{
      seenPacketKeys: new Set(),
      firstPacketLoad: true
    }};

    const text = (value, fallback = "") => value === null || value === undefined || value === "" ? fallback : String(value);
    const age = (seconds) => seconds === null || seconds === undefined ? "never" : `${{seconds}}s`;
    const yesNo = (value) => value ? "yes" : "no";

    function escapeHtml(value) {{
      return text(value).replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }}[char]));
    }}

    function setStatus(id, label, mode = "ok") {{
      const el = document.getElementById(id);
      el.textContent = label;
      el.className = mode === "error" ? "pulse error" : mode === "warn" ? "pulse warn" : "pulse";
    }}

    async function getJson(url) {{
      const response = await fetch(url, {{ cache: "no-store" }});
      if (!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
      return response.json();
    }}

    function renderMetrics(status, packets, sensors) {{
      const rx24 = status.clients.reduce((sum, client) => sum + (client.packets_rx_24h || 0), 0);
      const tx24 = status.clients.reduce((sum, client) => sum + (client.packets_tx_24h || 0), 0);
      const guardCounters = (status.bridge_guards && status.bridge_guards.counters) || {{}};
      const clientDedups = status.clients.reduce((sum, client) => sum + (client.skipped_dup_total || 0), 0);
      const totalDedups = guardCounters.skipped_duplicate || clientDedups;
      const shortQuarantine = (status.bridge_guards && status.bridge_guards.short_id_quarantine) || [];
      const blockDrops = (status.bridge_guards && status.bridge_guards.block_drops) || 0;
      const nodeBlockDrops = (status.bridge_guards && status.bridge_guards.node_block_drops) || 0;
      const pathBlockDrops = (status.bridge_guards && status.bridge_guards.path_block_drops) || 0;
      const nodeBlockActive = (status.bridge_guards && status.bridge_guards.node_block_active) || 0;
      const pathBlockActive = (status.bridge_guards && status.bridge_guards.path_block_active) || 0;
      document.getElementById("metricConnected").textContent = status.connected_count;
      document.getElementById("metricPackets").textContent = `${{packets.packet_count}}/${{packets.packet_capacity || 200}}`;
      document.getElementById("metricPacketsTotal").textContent = packets.packet_total || packets.packet_count || 0;
      document.getElementById("metricDedups").textContent = totalDedups;
      document.getElementById("metricShortQ").textContent = shortQuarantine.length;
      document.getElementById("metricShortQ").title = shortQuarantine.length
        ? shortQuarantine.map((item) => `${{item.id}} ${{item.seconds_left}}s ${{item.reason || ""}}`).join(", ")
        : ((status.bridge_guards && status.bridge_guards.short_id_quarantine_enabled) ? "enabled, no blocked short IDs" : "disabled");
      document.getElementById("metricBlockDrops").textContent = blockDrops;
      document.getElementById("metricBlockDrops").title = `ID blocks: ${{nodeBlockActive}} active, ${{nodeBlockDrops}} drops. Path blocks: ${{pathBlockActive}} active, ${{pathBlockDrops}} drops.`;
      document.getElementById("metricSensors").textContent = sensors.sensor_count;
      document.getElementById("metricTraffic").textContent = `${{rx24}} / ${{tx24}}`;
      document.getElementById("metricSync").textContent = new Date().toLocaleTimeString();
    }}

    function pct(value) {{
      return Number.isFinite(value) ? `${{value.toFixed(value >= 10 ? 1 : 2)}}%` : "--";
    }}

    function seconds(ms) {{
      return Number.isFinite(ms) ? `${{Math.round(ms / 1000)}}s` : "--";
    }}

    function duration(ms) {{
      if (!Number.isFinite(ms)) return "--";
      const total = Math.max(0, Math.round(ms / 1000));
      const minutes = Math.floor(total / 60);
      const seconds = total % 60;
      return minutes > 0 ? `${{minutes}}m ${{String(seconds).padStart(2, "0")}}s` : `${{seconds}}s`;
    }}

    function formatBlockEntries(entries) {{
      if (!entries || !entries.length) return "none";
      return entries.map((entry) => `${{entry.value}} ${{entry.seconds_left || 0}}s drops=${{entry.drops || 0}}`).join(", ");
    }}

    function dbm(value) {{
      return Number.isFinite(value) ? `${{Math.round(value)}} dBm` : "--";
    }}

    function snr(value) {{
      return Number.isFinite(value) ? `${{value.toFixed(1)}} dB` : "--";
    }}

    function renderNodes(status) {{
      const target = document.getElementById("nodeCards");
      if (!status.clients.length) {{
        target.innerHTML = '<div class="empty">No bridge nodes seen in the last 24h</div>';
        setStatus("nodeStatus", "no nodes", "warn");
        return;
      }}
      setStatus("nodeStatus", `${{status.connected_count}} online / ${{status.known_count || status.clients.length}} known`, status.connected_count ? "ok" : "warn");
      target.innerHTML = status.clients.map((client) => {{
        const heartbeat = client.heartbeat_age_seconds === null ? "never" : `${{client.heartbeat_age_seconds}}s ago`;
        const isOnline = client.connected !== false;
        const update = client.firmware_update || {{}};
        const updateAvailable = update.state === "available";
        const updateTitle = updateAvailable
          ? `new firmware available: ${{update.latest_version || update.latest_tag}} (${{update.bin_count || 0}} bin files)`
          : update.check_status === "error" ? `firmware update check failed: ${{update.error || "unknown error"}}`
          : update.state === "current" ? `firmware current${{update.latest_version ? ": " + update.latest_version : ""}}`
          : update.check_status === "pending" ? "firmware update check pending"
          : update.check_status === "disabled" ? "firmware update check disabled"
          : "firmware update state unknown";
        const updateBadgeClass = updateAvailable ? "update" : update.check_status === "error" ? "error" : update.state === "current" ? "current" : "pending";
        const updateBadgeText = updateAvailable ? `update ${{update.latest_version || ""}}` : update.state === "current" ? "current" : update.check_status === "error" ? "check error" : "unknown";
        const updateBadge = updateAvailable
          ? `<a class="badge ${{updateBadgeClass}}" title="${{escapeHtml(updateTitle)}}" href="${{escapeHtml(update.latest_url || "#")}}" target="_blank" rel="noopener">${{escapeHtml(updateBadgeText)}}</a>`
          : `<span class="badge ${{updateBadgeClass}}" title="${{escapeHtml(updateTitle)}}">${{escapeHtml(updateBadgeText)}}</span>`;
        const rf = client.rf_duty || {{}};
        const rfFirmwareUsedMs = Number.isFinite(rf.tx_used_ms) ? rf.tx_used_ms : NaN;
        const rfUsedMs = Number.isFinite(rf.tx_hour_used_ms) ? rf.tx_hour_used_ms : rfFirmwareUsedMs;
        const rfMaxMs = Number.isFinite(rf.tx_max_ms) ? rf.tx_max_ms : NaN;
        const rfLeftMs = Number.isFinite(rf.tx_hour_left_ms)
          ? rf.tx_hour_left_ms
          : Number.isFinite(rfUsedMs) && Number.isFinite(rfMaxMs) ? Math.max(0, rfMaxMs - rfUsedMs) : NaN;
        const rfReset = Number.isFinite(rf.tx_hour_resets_in_seconds) ? duration(rf.tx_hour_resets_in_seconds * 1000) : "unknown";
        const rfSinceServer = Number.isFinite(rf.tx_since_server_ms) ? duration(rf.tx_since_server_ms) : "unknown";
        const rfTitle = Number.isFinite(rf.tx_used_pct)
          ? `Current website hour: used ${{duration(rfUsedMs)}} and left ${{duration(rfLeftMs)}} from the ${{pct(rf.duty_limit_pct)}} hourly dutycycle budget (${{duration(rfMaxMs)}} total). Resets in ${{rfReset}}. Firmware current-window used ${{duration(rfFirmwareUsedMs)}}. Since server saw node: ${{rfSinceServer}}.`
          : "firmware update needed";
        const radio = client.radio_stats || {{}};
        const radioTitle = Number.isFinite(radio.noise_floor)
          ? `Radio stats from last bridge heartbeat: noise floor ${{dbm(radio.noise_floor)}}, last RSSI ${{dbm(radio.last_rssi)}}, last SNR ${{snr(radio.last_snr)}}.`
          : "firmware update needed";
        const neighborCount = Number.isFinite(client.neighbor_count) ? client.neighbor_count : NaN;
        const neighborTitle = Number.isFinite(neighborCount)
          ? `Neighbours heard locally by this bridge node: ${{neighborCount}}.`
          : "firmware update needed";
        const floodHopLimitDrops = Number.isFinite(client.flood_hop_limit_drops) ? client.flood_hop_limit_drops : NaN;
        const floodHopLimitTitle = Number.isFinite(floodHopLimitDrops)
          ? "Flood packets this bridge node did not retransmit because their path had reached the configured hop limit."
          : "firmware update needed";
        const footer = isOnline
          ? `connected ${{escapeHtml(client.connected_for)}} · idle ${{client.idle_seconds}}s · heartbeat ${{heartbeat}}`
          : `offline · last seen ${{age(client.last_seen_seconds)}} ago · heartbeat ${{heartbeat}}`;
        const lastTx = client.last_tx_age_seconds === null || client.last_tx_age_seconds === undefined ? "never" : `${{client.last_tx_age_seconds}}s ago`;
        const skipReasons = client.skipped_dup_by_reason || {{}};
        const skipReasonText = Object.entries(skipReasons).map(([key, value]) => `${{key}}=${{value}}`).join(", ") || "none";
        const bridgeTrafficTitle = "TCP bridge packets seen by this webserver for this node, not repeater RF RT/TX counters.";
        const queueTitle = `queued total ${{client.tx_queued || 0}}, high water ${{client.tx_queue_high_water || 0}}, skipped duplicates ${{client.skipped_dup_total || 0}}, reasons: ${{skipReasonText}}, send errors ${{client.tx_send_errors || 0}}${{client.last_tx_error ? ", last error: " + client.last_tx_error : ""}}`;
        const guardTitle = `group ${{client.group || "default"}}, loop score ${{client.loop_score || 0}}, quarantine ${{client.quarantine_active ? (client.quarantine_seconds_left || 0) + "s" : "no"}}, last fingerprint ${{client.last_fingerprint || "none"}}`;
        const budgetLeft = client.rf_inject_budget_remaining_ms === null || client.rf_inject_budget_remaining_ms === undefined
          ? "off"
          : duration(client.rf_inject_budget_remaining_ms);
        const blockStats = client.block_stats || {{}};
        const blockTotals = client.block_stats_totals || {{}};
        const nodeBlockTitle = `${{formatBlockEntries(blockStats.node)}}${{blockStats.error ? " | poll error: " + blockStats.error : ""}}`;
        const pathBlockTitle = `${{formatBlockEntries(blockStats.path)}}${{blockStats.error ? " | poll error: " + blockStats.error : ""}}`;
        return `
          <article class="node-card${{isOnline ? "" : " offline"}}">
            <div class="node-title">
              <span>${{escapeHtml(client.display_name)}}</span>
            </div>
            <div class="node-meta">node id ${{escapeHtml(client.node_id || "unknown")}}<br>${{escapeHtml(client.firmware_version || "firmware unknown")}} ${{updateBadge}}</div>
            <div class="node-stats">
              <div class="mini" title="${{escapeHtml(bridgeTrafficTitle)}}"><span class="label">Bridge RX 24h</span><b>${{client.packets_rx_24h}}</b></div>
              <div class="mini" title="${{escapeHtml(bridgeTrafficTitle)}}"><span class="label">Bridge TX 24h</span><b>${{client.packets_tx_24h}}</b></div>
              <div class="mini" title="${{escapeHtml(neighborTitle)}}"><span class="label">Neighbours</span><b>${{Number.isFinite(neighborCount) ? neighborCount : "--"}}</b></div>
              <div class="mini" title="${{escapeHtml(floodHopLimitTitle)}}"><span class="label">Hop-limit drops</span><b>${{Number.isFinite(floodHopLimitDrops) ? floodHopLimitDrops : "--"}}</b></div>
              <div class="mini" title="${{escapeHtml(rfTitle)}}"><span class="label">Duty used</span><b>${{duration(rfUsedMs)}}</b></div>
              <div class="mini" title="${{escapeHtml(rfTitle)}}"><span class="label">Duty left</span><b>${{duration(rfLeftMs)}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Queue</span><b>${{client.tx_queue_depth || 0}}/${{client.tx_queue_max || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Q drops</span><b>${{client.tx_queue_dropped || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(queueTitle)}}"><span class="label">Dedup</span><b>${{client.skipped_dup_total || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Loop</span><b>${{client.loop_score || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Group</span><b>${{escapeHtml(client.group || "default")}}</b></div>
              <div class="mini" title="${{escapeHtml(guardTitle)}}"><span class="label">Quality</span><b>${{client.bridge_quality_score ?? 0}}</b></div>
              <div class="mini" title="RF inject budget remaining"><span class="label">RF budget</span><b>${{budgetLeft}}</b></div>
              <div class="mini" title="${{escapeHtml(nodeBlockTitle)}}"><span class="label">ID block</span><b>${{blockTotals.node_drops || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(pathBlockTitle)}}"><span class="label">Path block</span><b>${{blockTotals.path_drops || 0}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">Noise</span><b>${{dbm(radio.noise_floor)}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">RSSI</span><b>${{dbm(radio.last_rssi)}}</b></div>
              <div class="mini" title="${{escapeHtml(radioTitle)}}"><span class="label">SNR</span><b>${{snr(radio.last_snr)}}</b></div>
              <div class="mini"><span class="label">HB</span><b>${{client.heartbeats_rx}}</b></div>
            </div>
            <div class="node-meta">${{footer}} · last tx ${{lastTx}} · dedup ${{client.skipped_dup_total || 0}} · ${{client.quarantine_active ? "quarantine " + (client.quarantine_seconds_left || 0) + "s" : "not quarantined"}}</div>
          </article>
        `;
      }}).join("");
    }}

    function packetKey(packet) {{
      return [packet.time, packet.direction, packet.client, packet.size, packet.preview].join("|");
    }}

    function packetFeedText(packet) {{
      const flow = escapeHtml(packet.flow || packet.client || "");
      const typeRoute = `${{escapeHtml(packet.type || "unknown")}}/${{escapeHtml(packet.route || "-")}}`;
      const decoded = packet.decoded_channel
        ? ` | ${{escapeHtml(packet.decoded_channel)}} ${{escapeHtml(packet.decoded_text || packet.decoded_status || "")}}`
        : "";
      return `${{flow}} | ${{typeRoute}} | ${{packet.size}}B${{decoded}} | ${{escapeHtml(packet.preview)}}`;
    }}

    function renderPackets(packetData) {{
      const rows = document.getElementById("packetRows");
      const packets = packetData.packets.slice(0, 50);
      if (!packets.length) {{
        rows.innerHTML = '<tr><td colspan="5" class="empty">No packets seen yet</td></tr>';
        document.getElementById("packetFeed").innerHTML = '<div class="empty">Awaiting mesh traffic</div>';
        setStatus("packetStatus", "no traffic", "warn");
        setStatus("feedStatus", "quiet", "warn");
        return;
      }}
      setStatus("packetStatus", `${{packets.length}} buffered`, "ok");
      setStatus("feedStatus", "live", "ok");
      rows.innerHTML = packets.map((packet, index) => {{
        const dirClass = packet.direction === "RX" ? "rx" : "tx";
        const source = packet.source || packet.client || "";
        const target = packet.target || "";
        const flow = target ? `${{escapeHtml(source)}} -> ${{escapeHtml(target)}}` : escapeHtml(source);
        const routeBits = [
          packet.route || "",
          packet.hops === null || packet.hops === undefined ? "" : `${{packet.hops}} hop`,
          packet.source_short_id ? `sid ${{packet.source_short_id}}` : "",
          `${{packet.size}}B`,
          packet.bridge_v2 ? `ttl ${{text(packet.ttl, "-")}}` : "",
        ].filter(Boolean).join(" | ");
        const decoded = packet.decoded_text || packet.decoded_status || "";
        const decodedLine = packet.decoded_channel
          ? `${{escapeHtml(packet.decoded_channel)}}: ${{escapeHtml(decoded)}}`
          : escapeHtml(decoded);
        return `
          <tr class="${{index < 3 ? "hot" : ""}}">
            <td class="packet-age">${{age(packet.age_seconds)}}</td>
            <td><span class="badge ${{dirClass}}">${{escapeHtml(packet.direction)}}</span></td>
            <td>
              <div class="packet-main">${{flow}}</div>
              <div class="packet-sub">${{escapeHtml(packet.client || "")}}</div>
            </td>
            <td>
              <div class="packet-main">${{escapeHtml(packet.type || "unknown")}}</div>
              <div class="packet-sub">${{escapeHtml(routeBits)}}</div>
            </td>
            <td>
              <div class="packet-main preview">${{decodedLine}}</div>
              <div class="packet-sub preview">${{escapeHtml(packet.preview)}}</div>
            </td>
          </tr>
        `;
      }}).join("");

      const feed = document.getElementById("packetFeed");
      if (state.firstPacketLoad) {{
        state.seenPacketKeys = new Set(packets.map(packetKey));
        state.firstPacketLoad = false;
      }} else {{
        for (const packet of packets.slice().reverse()) {{
          const key = packetKey(packet);
          if (state.seenPacketKeys.has(key)) continue;
          state.seenPacketKeys.add(key);
          const line = document.createElement("div");
          const dirClass = packet.direction === "RX" ? "dir-rx" : "dir-tx";
          line.className = "feed-line";
          line.innerHTML = `
            <span class="meta">${{age(packet.age_seconds)}}</span>
            <span class="${{dirClass}}">${{escapeHtml(packet.direction)}}</span>
            <span class="packet">${{packetFeedText(packet)}}</span>
          `;
          feed.prepend(line);
        }}
      }}
      if (!feed.children.length) {{
        feed.innerHTML = packets.slice(0, 24).map((packet) => `
          <div class="feed-line">
            <span class="meta">${{age(packet.age_seconds)}}</span>
            <span class="${{packet.direction === "RX" ? "dir-rx" : "dir-tx"}}">${{escapeHtml(packet.direction)}}</span>
            <span class="packet">${{packetFeedText(packet)}}</span>
          </div>
        `).join("");
      }}
      while (feed.children.length > 80) feed.removeChild(feed.lastChild);
    }}

    function renderSensors(sensorData) {{
      const rows = document.getElementById("sensorRows");
      if (!sensorData.sensors.length) {{
        rows.innerHTML = '<tr><td colspan="7" class="empty">No sensor node adverts seen yet</td></tr>';
        setStatus("sensorStatus", "no adverts", "warn");
        return;
      }}
      setStatus("sensorStatus", `${{sensorData.sensors.length}} detected`, "ok");
      rows.innerHTML = sensorData.sensors.map((sensor) => {{
        const location = sensor.lat !== null && sensor.lon !== null ? `${{Number(sensor.lat).toFixed(6)}}, ${{Number(sensor.lon).toFixed(6)}}` : "not shared";
        const nodeId = sensor.node_id || "";
        const shortNodeId = sensor.node_id_short || nodeId.slice(0, 2) || "--";
        return `
          <tr>
            <td>${{escapeHtml(sensor.name || "unknown")}}</td>
            <td title="${{escapeHtml(nodeId)}}">${{escapeHtml(shortNodeId)}}</td>
            <td>${{age(sensor.age_seconds)}} ago</td>
            <td>${{sensor.seen_count}}</td>
            <td>${{text(sensor.hops, "")}}</td>
            <td>${{escapeHtml(sensor.source)}}</td>
            <td>${{escapeHtml(location)}}</td>
          </tr>
        `;
      }}).join("");
    }}

    async function refresh() {{
      try {{
        const [status, packets, sensors] = await Promise.all([
          getJson(urls.status),
          getJson(urls.packets),
          getJson(urls.sensors)
        ]);
        renderMetrics(status, packets, sensors);
        renderNodes(status);
        renderPackets(packets);
        renderSensors(sensors);
      }} catch (error) {{
        document.getElementById("metricSync").textContent = "link error";
        setStatus("nodeStatus", "link error", "error");
        setStatus("packetStatus", "link error", "error");
        setStatus("feedStatus", "link error", "error");
        setStatus("sensorStatus", "link error", "error");
        console.error(error);
      }}
    }}

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def build_manage_html(command_result: str = "", base_path: str = "") -> str:
    """Generate the manage HTML page."""
    snapshot = status_snapshot(include_disconnected=False)
    options = []
    for client in snapshot["clients"]:
        label = client["display_name"]
        if client.get("node_id"):
            label += f" [{client['node_id']}]"
        if client["firmware_version"]:
            label += f" ({client['firmware_version']})"
        options.append(
            f'<option value="{html.escape(client["id"], quote=True)}">{html.escape(label)}</option>'
        )

    options_html = "\n".join(options) if options else (
        '<option value="">No bridge nodes connected</option>'
    )
    path_options_html = (
        '<option value="__all__">All connected bridge nodes</option>\n' + "\n".join(options)
        if options else
        '<option value="">No bridge nodes connected</option>'
    )
    disabled = " disabled" if not options else ""
    path_block_enabled = ALLOW_PATH_BLOCK_ADMIN and bool(ADMIN_PASSWORD)
    path_disabled = "" if options and path_block_enabled else " disabled"
    admin_note = (
        "Remote management protected by server admin password; node password still required"
        if ADMIN_PASSWORD else
        "Remote management enabled; enter the selected node's admin password"
    )
    path_note = (
        "Path quarantine is enabled for bridge admins and does not require the node password"
        if path_block_enabled else
        "Path quarantine is disabled; start the server with --admin-password and --allow-path-block-admin"
    )
    node_note = (
        "Node quarantine blocks a 1-byte source id on the selected bridge node; matching packets are dropped locally"
        if path_block_enabled else
        "Node quarantine is disabled; start the server with --admin-password and --allow-path-block-admin"
    )
    result_html = (
        f'<pre class="command-result">{html.escape(redact_public_text(command_result))}</pre>'
        if command_result else
        '<pre class="command-result empty">No command sent yet</pre>'
    )

    status_url = prefixed_url(base_path, "/")
    command_url = prefixed_url(base_path, "/command")
    logout_url = prefixed_url(base_path, "/logout")
    connected_count = snapshot.get("connected_count", len(snapshot["clients"]))
    node_count = len(snapshot["clients"])
    auth_state = "protected" if ADMIN_PASSWORD else "node password"
    quarantine_state = "enabled" if path_block_enabled else "disabled"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG Remote Management</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #070908;
      --panel: #101514;
      --panel-2: #141b19;
      --line: rgba(97, 255, 154, .22);
      --green: #68ff9d;
      --green-soft: #b6ffd0;
      --muted: #8aa596;
      --amber: #ffd166;
      --red: #ff5b5b;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: #eafff0;
      background:
        radial-gradient(circle at 15% -10%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(135deg, #070908 0%, #0d1311 48%, #050706 100%);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 22px 18px 32px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 16px; }}
    h1 {{ margin: 0; color: var(--green-soft); font-size: clamp(1.5rem, 3vw, 2.25rem); letter-spacing: 0; }}
    .summary {{ margin: 6px 0 0; max-width: 760px; color: var(--muted); line-height: 1.45; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }}
    nav a {{
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--green-soft);
      padding: 8px 11px;
      text-decoration: none;
      background: rgba(104, 255, 157, .06);
      font-weight: 750;
      font-size: .82rem;
    }}
    nav a:hover {{ border-color: var(--green-soft); }}
    nav a.logout {{ border-color: rgba(255, 91, 91, .35); color: var(--red); background: rgba(255, 91, 91, .07); }}
    nav a.logout:hover {{ border-color: var(--red); }}
    .status-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }}
    .metric {{
      border: 1px solid var(--line);
      background: rgba(16, 21, 20, .82);
      border-radius: 6px;
      padding: 10px 12px;
    }}
    .label {{ color: var(--muted); font-size: .7rem; text-transform: uppercase; }}
    .metric b {{ display: block; margin-top: 4px; color: var(--green); font-size: 1.08rem; font-variant-numeric: tabular-nums; }}
    .grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, .85fr); gap: 14px; align-items: start; }}
    .stack {{ display: grid; gap: 14px; }}
    .panel {{
      border: 1px solid var(--line);
      background: rgba(16, 21, 20, .86);
      border-radius: 6px;
      overflow: hidden;
      box-shadow: 0 18px 42px rgba(0, 0, 0, .24);
    }}
    .panel-header {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(104, 255, 157, .06);
    }}
    h2 {{ margin: 0; color: var(--green-soft); font-size: .92rem; text-transform: uppercase; }}
    .panel-body {{ padding: 14px; }}
    .panel p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.4; }}
    .form-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .form-wide {{ grid-column: 1 / -1; }}
    label {{ display: block; color: var(--muted); font-size: .72rem; font-weight: 750; margin: 0 0 5px; text-transform: uppercase; }}
    select, input {{
      width: 100%;
      min-width: 0;
      border: 1px solid rgba(97, 255, 154, .18);
      border-radius: 4px;
      padding: 9px 10px;
      background: rgba(0, 0, 0, .22);
      color: #eafff0;
      font: inherit;
      outline: none;
    }}
    select:focus, input:focus {{ border-color: rgba(104, 255, 157, .65); box-shadow: 0 0 0 2px rgba(104, 255, 157, .1); }}
    select:disabled, input:disabled {{ color: #65786e; border-color: rgba(138, 165, 150, .14); cursor: not-allowed; }}
    .actions {{ display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }}
    button {{
      border: 1px solid rgba(104, 255, 157, .42);
      border-radius: 4px;
      padding: 9px 12px;
      background: rgba(104, 255, 157, .12);
      color: var(--green-soft);
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    button:hover:not(:disabled) {{ background: rgba(104, 255, 157, .18); }}
    button:disabled {{ color: #65786e; border-color: rgba(138, 165, 150, .16); cursor: not-allowed; }}
    .command-result {{
      margin: 0;
      min-height: 224px;
      max-height: 520px;
      overflow: auto;
      border: 1px solid rgba(97, 255, 154, .16);
      border-radius: 6px;
      background: rgba(0, 0, 0, .28);
      color: #dfffe9;
      padding: 12px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-size: .82rem;
      line-height: 1.38;
    }}
    .empty {{ color: var(--muted); }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid rgba(97, 255, 154, .22);
      border-radius: 999px;
      padding: 3px 8px;
      color: var(--muted);
      font-size: .72rem;
      white-space: nowrap;
    }}
    @media (max-width: 920px) {{
      main {{ padding: 18px 12px 26px; }}
      .topbar {{ display: block; }}
      nav {{ justify-content: flex-start; margin-top: 12px; }}
      .status-strip {{ grid-template-columns: 1fr; }}
      .grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 560px) {{
      .form-grid {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: stretch; }}
      button {{ width: 100%; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div>
        <h1>Remote Management</h1>
        <p class="summary">{html.escape(admin_note)}.</p>
      </div>
      <nav>
        <a href="{status_url}">Bridge status</a>
        <a href="{logout_url}" class="logout">Sign out</a>
      </nav>
    </header>
    <section class="status-strip">
      <div class="metric"><span class="label">Online nodes</span><b>{connected_count}</b></div>
      <div class="metric"><span class="label">Command auth</span><b>{html.escape(auth_state)}</b></div>
      <div class="metric"><span class="label">Bridge quarantine</span><b>{html.escape(quarantine_state)}</b></div>
    </section>
    <section class="grid">
      <div class="stack">
        <div class="panel">
          <div class="panel-header">
            <h2>Remote CLI</h2>
            <span class="pill">{node_count} node{'' if node_count == 1 else 's'}</span>
          </div>
          <div class="panel-body">
            <form method="post" action="{command_url}">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="target">Bridge node</label>
                  <select id="target" name="target"{disabled}>
                    {options_html}
                  </select>
                </div>
                <div>
                  <label for="node_password">Node password</label>
                  <input id="node_password" name="node_password" type="password" autocomplete="current-password" maxlength="32"{disabled}>
                </div>
                <div>
                  <label for="command">Command</label>
                  <input id="command" name="command" placeholder="get bridge.status" maxlength="96"{disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{disabled}>Send command</button></div>
            </form>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Path Quarantine</h2>
            <span class="pill">{html.escape(quarantine_state)}</span>
          </div>
          <div class="panel-body">
            <p>{html.escape(path_note)}</p>
            <form method="post" action="{command_url}">
              <input type="hidden" name="mode" value="path_block">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="path_target">Bridge node</label>
                  <select id="path_target" name="target"{path_disabled}>
                    {path_options_html}
                  </select>
                </div>
                <div>
                  <label for="path_action">Action</label>
                  <select id="path_action" name="path_action"{path_disabled}>
                    <option value="add">Add block</option>
                    <option value="del">Remove block</option>
                    <option value="get">Show blocks</option>
                    <option value="clear">Clear all</option>
                  </select>
                </div>
                <div>
                  <label for="path_duration">Duration</label>
                  <input id="path_duration" name="path_duration" placeholder="1h" maxlength="8"{path_disabled}>
                </div>
                <div class="form-wide">
                  <label for="path_block">Path</label>
                  <input id="path_block" name="path_block" placeholder="aa/bb/cc" maxlength="20"{path_disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{path_disabled}>Apply quarantine</button></div>
            </form>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <h2>Node-ID Quarantine</h2>
            <span class="pill">{html.escape(quarantine_state)}</span>
          </div>
          <div class="panel-body">
            <p>{html.escape(node_note)}</p>
            <form method="post" action="{command_url}">
              <input type="hidden" name="mode" value="node_block">
              <div class="form-grid">
                <div class="form-wide">
                  <label for="node_target">Bridge node</label>
                  <select id="node_target" name="target"{path_disabled}>
                    {path_options_html}
                  </select>
                </div>
                <div>
                  <label for="node_action">Action</label>
                  <select id="node_action" name="node_action"{path_disabled}>
                    <option value="add">Add block</option>
                    <option value="del">Remove block</option>
                    <option value="get">Show blocks</option>
                    <option value="clear">Clear all</option>
                  </select>
                </div>
                <div>
                  <label for="node_duration">Duration</label>
                  <input id="node_duration" name="node_duration" placeholder="15m" maxlength="8"{path_disabled}>
                </div>
                <div class="form-wide">
                  <label for="node_block">1-byte source node id</label>
                  <input id="node_block" name="node_block" placeholder="a7" maxlength="2"{path_disabled}>
                </div>
              </div>
              <div class="actions"><button type="submit"{path_disabled}>Apply quarantine</button></div>
            </form>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <h2>Command Result</h2>
          <span class="pill">console</span>
        </div>
        <div class="panel-body">
          {result_html}
        </div>
      </div>
    </section>
  </main>
</body>
</html>
"""


def build_location_map_html(base_path: str = "") -> str:
    """Generate the map HTML page."""
    status_url = prefixed_url(base_path, "/")
    logout_url = prefixed_url(base_path, "/logout")
    locations_url = prefixed_url(base_path, "/locations.json")
    page = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MeshCoreNG Tracker Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
    :root {
      color-scheme: dark;
      --bg: #050806;
      --panel: rgba(8, 18, 12, .88);
      --line: rgba(97, 255, 154, .32);
      --line-strong: rgba(97, 255, 154, .58);
      --green: #68ff9d;
      --green-soft: #a1ffc4;
      --amber: #ffd166;
      --red: #ff5f6d;
      --muted: #8fb99e;
      --text: #dfffe9;
      --shadow: 0 18px 60px rgba(0, 0, 0, .48);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    }
    * { box-sizing: border-box; }
    html, body, #map { height: 100%; margin: 0; }
    html { background: var(--bg); }
    body {
      color: var(--text);
      background:
        radial-gradient(circle at 18% 12%, rgba(104, 255, 157, .12), transparent 28%),
        linear-gradient(180deg, rgba(2, 10, 6, .7), rgba(2, 5, 3, .98)),
        var(--bg);
      overflow: hidden;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(rgba(104, 255, 157, .04) 50%, rgba(0, 0, 0, .13) 50%),
        linear-gradient(90deg, rgba(255, 0, 0, .025), rgba(0, 255, 95, .018), rgba(0, 120, 255, .025));
      background-size: 100% 4px, 7px 100%;
      mix-blend-mode: screen;
      opacity: .42;
      z-index: 1200;
    }
    #map {
      background: #071009;
    }
    #map.layout-tactical .leaflet-tile,
    #map.layout-night .leaflet-tile {
      filter: invert(1) hue-rotate(95deg) saturate(.75) brightness(.58) contrast(1.25);
    }
    #map.layout-standard .leaflet-tile,
    #map.layout-humanitarian .leaflet-tile,
    #map.layout-topo .leaflet-tile {
      filter: none;
    }
    .leaflet-control-attribution,
    .leaflet-control-zoom a,
    .leaflet-control-layers {
      background: rgba(8, 18, 12, .92) !important;
      border-color: var(--line) !important;
      color: var(--green-soft) !important;
      font-family: inherit;
    }
    .leaflet-control-layers-expanded {
      padding: 10px 12px;
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .leaflet-control-layers label {
      color: var(--text);
      font-size: 12px;
      line-height: 1.9;
    }
    .leaflet-control-layers-selector {
      accent-color: var(--green);
    }
    .leaflet-control-zoom {
      border: 1px solid var(--line) !important;
      box-shadow: var(--shadow);
    }
    .leaflet-bottom {
      bottom: 74px;
    }
    .leaflet-popup-content-wrapper,
    .leaflet-popup-tip {
      background: rgba(5, 12, 8, .96);
      color: var(--text);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .leaflet-popup-content {
      font-family: inherit;
      font-size: 12px;
      line-height: 1.55;
    }
    .leaflet-popup-content strong {
      color: var(--green);
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .leaflet-container a.leaflet-popup-close-button {
      color: var(--green-soft);
    }
    .topbar {
      position: absolute;
      z-index: 1000;
      top: 14px;
      left: 14px;
      right: 14px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 14px;
      align-items: center;
      background: linear-gradient(180deg, rgba(11, 26, 17, .94), rgba(5, 12, 8, .88));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .topbar::before {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      border-radius: 8px;
      background: linear-gradient(90deg, rgba(104,255,157,.14), transparent 18%, transparent 82%, rgba(104,255,157,.12));
    }
    .topbar h1 {
      margin: 0;
      min-width: 0;
      color: var(--green);
      font-size: clamp(.9rem, 2.4vw, 1.08rem);
      font-weight: 780;
      letter-spacing: .08em;
      text-transform: uppercase;
      text-shadow: 0 0 18px rgba(104,255,157,.42);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .topbar a {
      color: var(--green-soft);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 7px 9px;
      text-decoration: none;
      text-transform: uppercase;
      font-size: .78rem;
      font-weight: 760;
      background: rgba(104, 255, 157, .08);
    }
    .topbar a:hover { border-color: var(--line-strong); color: var(--green); }
    .topbar a.logout { border-color: rgba(255, 91, 91, .35); color: #ff5b5b; background: rgba(255, 91, 91, .07); }
    .topbar a.logout:hover { border-color: #ff5b5b; }
    .muted {
      color: var(--muted);
      font-size: .84rem;
      white-space: nowrap;
    }
    .tracker-icon {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      background: radial-gradient(circle, var(--marker-core, var(--green-soft)), var(--marker-fill, var(--green)) 55%, var(--marker-edge, #0c4f2a) 100%);
      border: 2px solid var(--marker-border, rgba(223,255,233,.9));
      box-shadow:
        0 0 0 3px var(--marker-ring, rgba(104,255,157,.18)),
        0 0 22px var(--marker-glow, rgba(104,255,157,.54)),
        0 3px 9px rgba(0,0,0,.5);
      position: relative;
    }
    .tracker-icon::before {
      content: "";
      position: absolute;
      left: 50%;
      top: -11px;
      transform: translateX(-50%);
      border-left: 7px solid transparent;
      border-right: 7px solid transparent;
      border-bottom: 14px solid var(--marker-fill, var(--green));
      filter: drop-shadow(0 -1px 5px var(--marker-glow, rgba(104,255,157,.55)));
    }
    .tracker-icon.stationary::before { display: none; }
    .tracker-label {
      margin-left: 32px;
      margin-top: -28px;
      padding: 3px 6px;
      border-radius: 4px;
      background: var(--marker-label-bg, rgba(5,12,8,.9));
      border: 1px solid var(--marker-label-border, var(--line));
      color: var(--marker-label-color, var(--green-soft));
      font-size: 11px;
      font-weight: 700;
      white-space: nowrap;
      box-shadow: 0 0 12px rgba(104,255,157,.16), 0 1px 6px rgba(0,0,0,.42);
    }
    .replaybar {
      position: absolute;
      z-index: 1000;
      left: 14px;
      right: 14px;
      bottom: 14px;
      display: grid;
      grid-template-columns: auto auto minmax(160px, 1fr) auto auto;
      gap: 12px;
      align-items: center;
      background: linear-gradient(180deg, rgba(11, 26, 17, .94), rgba(5, 12, 8, .88));
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }
    .replaybar button {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(104, 255, 157, .08);
      color: var(--green-soft);
      cursor: pointer;
      font: inherit;
      font-size: .78rem;
      font-weight: 760;
      padding: 7px 10px;
      text-transform: uppercase;
    }
    .replaybar button:hover { border-color: var(--line-strong); color: var(--green); }
    .replaybar input[type="range"] {
      width: 100%;
      accent-color: var(--green);
    }
    .replaybar .time {
      color: var(--green-soft);
      font-size: .84rem;
      white-space: nowrap;
    }
    .replaybar .hint {
      color: var(--muted);
      font-size: .76rem;
      text-transform: uppercase;
      white-space: nowrap;
    }
    @media (max-width: 720px) {
      .topbar {
        grid-template-columns: 1fr;
        align-items: start;
        gap: 8px;
      }
      .topbar h1 { white-space: normal; }
      .muted { white-space: normal; }
      .topbar a { width: max-content; }
      .replaybar {
        grid-template-columns: 1fr;
        bottom: 10px;
      }
      .leaflet-bottom { bottom: 150px; }
      .replaybar .hint { white-space: normal; }
    }
  </style>
</head>
<body>
  <div class="topbar">
    <h1>MeshCoreNG Tracker Tactical Map</h1>
    <span class="muted" id="summary">Loading...</span>
    <a href="__STATUS_URL__">Bridge status</a>
    <a href="__LOGOUT_URL__" class="logout">Sign out</a>
  </div>
  <div id="map"></div>
  <div class="replaybar">
    <button id="replayToggle" type="button">Replay 24h</button>
    <button id="replayPlay" type="button">Play</button>
    <input id="replaySlider" type="range" min="0" max="1440" value="1440" step="1">
    <span class="time" id="replayTime">live</span>
    <span class="hint" id="replayHint">Live tracking</span>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const mapEl = document.getElementById('map');
    const savedLayout = localStorage.getItem('meshcore_tracker_map_layout') || 'Tactical';
    const map = L.map('map', { zoomControl: false }).setView([52.2, 5.3], 8);
    L.control.zoom({ position: 'bottomright' }).addTo(map);
    const osmAttrib = '&copy; OpenStreetMap contributors';
    const topoAttrib = '&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap';
    const baseLayers = {
      Tactical: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Night: L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Standard: L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Humanitarian: L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: osmAttrib
      }),
      Topo: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
        maxZoom: 17,
        attribution: topoAttrib
      })
    };
    const layoutClasses = {
      Tactical: 'layout-tactical',
      Night: 'layout-night',
      Standard: 'layout-standard',
      Humanitarian: 'layout-humanitarian',
      Topo: 'layout-topo'
    };
    const routeStyles = {
      Tactical: { color: '#68ff9d', weight: 4, opacity: 0.82 },
      Night: { color: '#ffd166', weight: 4, opacity: 0.88 },
      Standard: { color: '#d0006f', weight: 5, opacity: 0.92 },
      Humanitarian: { color: '#0057ff', weight: 5, opacity: 0.9 },
      Topo: { color: '#d0006f', weight: 5, opacity: 0.92 }
    };
    const markerStyles = {
      Tactical: {
        core: '#dfffe9', fill: '#68ff9d', edge: '#0c4f2a', border: '#f4fff8',
        ring: 'rgba(104,255,157,.2)', glow: 'rgba(104,255,157,.58)',
        labelBg: 'rgba(5,12,8,.92)', labelBorder: 'rgba(97,255,154,.46)', labelColor: '#a1ffc4'
      },
      Night: {
        core: '#fff3c2', fill: '#ffd166', edge: '#6f4c00', border: '#fff8dc',
        ring: 'rgba(255,209,102,.22)', glow: 'rgba(255,209,102,.62)',
        labelBg: 'rgba(18,12,4,.94)', labelBorder: 'rgba(255,209,102,.5)', labelColor: '#ffe59a'
      },
      Standard: {
        core: '#ffffff', fill: '#d0006f', edge: '#3b001f', border: '#ffffff',
        ring: 'rgba(208,0,111,.24)', glow: 'rgba(208,0,111,.58)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#d0006f', labelColor: '#3b001f'
      },
      Humanitarian: {
        core: '#ffffff', fill: '#0057ff', edge: '#001d54', border: '#ffffff',
        ring: 'rgba(0,87,255,.24)', glow: 'rgba(0,87,255,.55)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#0057ff', labelColor: '#001d54'
      },
      Topo: {
        core: '#ffffff', fill: '#d0006f', edge: '#3b001f', border: '#ffffff',
        ring: 'rgba(208,0,111,.24)', glow: 'rgba(208,0,111,.58)',
        labelBg: 'rgba(255,255,255,.94)', labelBorder: '#d0006f', labelColor: '#3b001f'
      }
    };
    let currentLayout = 'Tactical';

    function routeStyle() {
      return {
        ...(routeStyles[currentLayout] || routeStyles.Tactical),
        lineJoin: 'round'
      };
    }

    function markerStyleVars() {
      const style = markerStyles[currentLayout] || markerStyles.Tactical;
      return [
        `--marker-core:${style.core}`,
        `--marker-fill:${style.fill}`,
        `--marker-edge:${style.edge}`,
        `--marker-border:${style.border}`,
        `--marker-ring:${style.ring}`,
        `--marker-glow:${style.glow}`,
        `--marker-label-bg:${style.labelBg}`,
        `--marker-label-border:${style.labelBorder}`,
        `--marker-label-color:${style.labelColor}`
      ].join(';');
    }

    function setMapLayout(name) {
      currentLayout = baseLayers[name] ? name : 'Tactical';
      for (const cls of Object.values(layoutClasses)) mapEl.classList.remove(cls);
      mapEl.classList.add(layoutClasses[currentLayout] || layoutClasses.Tactical);
      localStorage.setItem('meshcore_tracker_map_layout', currentLayout);
      for (const track of tracks.values()) {
        track.eachLayer((layer) => layer.setStyle(routeStyle()));
      }
      for (const [nodeId, marker] of markers) {
        const loc = latestLocationByNode.get(nodeId);
        if (loc) marker.setIcon(trackerIcon(loc));
      }
    }

    const markers = new Map();
    const tracks = new Map();
    const latestLocationByNode = new Map();
    let latestData = null;
    let replayMode = false;
    let replayTimer = null;
    let replayStart = 0;
    let replayEnd = 0;
    let replayCursor = 0;
    const replayToggle = document.getElementById('replayToggle');
    const replayPlay = document.getElementById('replayPlay');
    const replaySlider = document.getElementById('replaySlider');
    const replayTimeLabel = document.getElementById('replayTime');
    const replayHint = document.getElementById('replayHint');
    const initialLayout = baseLayers[savedLayout] ? savedLayout : 'Tactical';
    setMapLayout(initialLayout);
    baseLayers[initialLayout].addTo(map);
    L.control.layers(baseLayers, null, { position: 'bottomleft', collapsed: false }).addTo(map);
    map.on('baselayerchange', (event) => setMapLayout(event.name));

    function fmtAge(seconds) {
      if (seconds < 60) return `${seconds}s`;
      if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
      return `${Math.floor(seconds / 3600)}h`;
    }

    function fmtReplayTime(epochSeconds) {
      if (!epochSeconds) return 'live';
      return new Date(epochSeconds * 1000).toLocaleString();
    }

    function pointTime(point) {
      const value = Number(point.timestamp || point.received_at || 0);
      return Number.isFinite(value) ? value : 0;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function fmtNumber(value, decimals = 1) {
      const num = Number(value);
      return Number.isFinite(num) ? num.toFixed(decimals) : '';
    }

    function fmtSpeed(value) {
      const speed = fmtNumber(value, 1);
      return speed ? `${speed} km/h` : 'unknown';
    }

    function fmtHeading(value) {
      const heading = fmtNumber(value, 0);
      return heading ? `${heading}&deg;` : 'unknown';
    }

    function trackerIcon(loc) {
      const heading = Number(loc.heading_deg);
      const speed = Number(loc.speed_kmh);
      const moving = Number.isFinite(speed) && speed >= 1;
      const rotation = Number.isFinite(heading) ? heading : 0;
      const labelSpeed = Number.isFinite(speed) ? `${speed.toFixed(0)} km/h` : '';
      return L.divIcon({
        className: '',
        iconSize: [110, 34],
        iconAnchor: [14, 14],
        popupAnchor: [0, -16],
        html: `<div style="${markerStyleVars()}">` +
          `<div class="tracker-icon ${moving ? '' : 'stationary'}" style="transform: rotate(${rotation}deg)"></div>` +
          `<div class="tracker-label">${escapeHtml(labelSpeed || '0 km/h')} ${Number.isFinite(heading) ? Math.round(heading) + '&deg;' : ''}</div>` +
          `</div>`
      });
    }

    function trackSegments(loc) {
      const points = Array.isArray(loc.track) ? loc.track : [];
      const segments = [];
      let segment = [];
      for (const point of points) {
        const lat = Number(point.lat);
        const lon = Number(point.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        segment.push([lat, lon]);
        if (point.route_break_after) {
          if (segment.length) segments.push(segment);
          segment = [];
        }
      }
      if (segment.length) segments.push(segment);
      return segments;
    }

    function trackLatLngs(loc) {
      return trackSegments(loc).flat();
    }

    function routeDistanceKm(segments) {
      let km = 0;
      for (const latlngs of segments) {
        for (let i = 1; i < latlngs.length; i++) {
          km += map.distance(latlngs[i - 1], latlngs[i]) / 1000;
        }
      }
      return km;
    }

    function fitRenderedLocations(locations) {
      if (!locations.length || refresh.didFit) return;
      const bounds = locations.flatMap(loc => {
        const latlngs = trackLatLngs(loc);
        return latlngs.length ? latlngs : [[loc.lat, loc.lon]];
      });
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 13 });
      refresh.didFit = true;
    }

    function renderLocations(locations, labelPrefix = '') {
      document.getElementById('summary').textContent = `${labelPrefix}${locations.length} tracker node(s)`;
      const seen = new Set();
      for (const loc of locations) {
        seen.add(loc.node_id);
        latestLocationByNode.set(loc.node_id, loc);
        const label = loc.name || loc.node_id;
        const segments = trackSegments(loc);
        const latlngs = segments.flat();
        const routeKm = routeDistanceKm(segments);
        const popup = `<strong>${escapeHtml(label)}</strong><br>` +
          `Node: ${escapeHtml(loc.node_id)}<br>` +
          `Age: ${fmtAge(loc.age_seconds)}<br>` +
          `Speed: ${fmtSpeed(loc.speed_kmh)}<br>` +
          `Heading: ${fmtHeading(loc.heading_deg)}<br>` +
          `Track: ${latlngs.length} point(s), ${routeKm.toFixed(2)} km<br>` +
          `Sats: ${loc.satellites}<br>` +
          `Battery: ${loc.battery_mv} mV<br>` +
          `Alt: ${loc.altitude_m} m`;
        let track = tracks.get(loc.node_id);
        const drawableSegments = segments.filter(segment => segment.length >= 2);
        if (drawableSegments.length) {
          if (track) {
            track.remove();
          }
          track = L.layerGroup(
            drawableSegments.map(segment => L.polyline(segment, routeStyle()))
          ).addTo(map);
          tracks.set(loc.node_id, track);
        } else if (track) {
          track.remove();
          tracks.delete(loc.node_id);
        }
        let marker = markers.get(loc.node_id);
        if (!marker) {
          marker = L.marker([loc.lat, loc.lon], { icon: trackerIcon(loc) }).addTo(map);
          markers.set(loc.node_id, marker);
        } else {
          marker.setLatLng([loc.lat, loc.lon]);
          marker.setIcon(trackerIcon(loc));
        }
        marker.bindPopup(popup);
      }
      for (const [nodeId, marker] of markers) {
        if (!seen.has(nodeId)) {
          marker.remove();
          markers.delete(nodeId);
          latestLocationByNode.delete(nodeId);
        }
      }
      for (const [nodeId, track] of tracks) {
        if (!seen.has(nodeId)) {
          track.remove();
          tracks.delete(nodeId);
        }
      }
      fitRenderedLocations(locations);
    }

    function updateReplayWindow(data) {
      replayEnd = Number(data.generated_at || Math.floor(Date.now() / 1000));
      replayStart = replayEnd - 24 * 60 * 60;
      if (!replayMode) {
        replayCursor = replayEnd;
        replaySlider.value = replaySlider.max;
        replayTimeLabel.textContent = 'live';
        replayHint.textContent = 'Live tracking';
      }
    }

    function replayLocationsAt(data, cursor) {
      const locations = [];
      for (const loc of data.locations || []) {
        const points = (Array.isArray(loc.track) ? loc.track : [])
          .filter(point => {
            const t = pointTime(point);
            return t >= replayStart && t <= cursor;
          });
        if (!points.length) continue;
        const last = points[points.length - 1];
        locations.push({
          ...loc,
          ...last,
          age_seconds: Math.max(0, replayEnd - pointTime(last)),
          track: points
        });
      }
      return locations;
    }

    function renderReplay() {
      if (!latestData) return;
      const minutes = Number(replaySlider.value);
      replayCursor = replayStart + minutes * 60;
      const locations = replayLocationsAt(latestData, replayCursor);
      renderLocations(locations, 'Replay: ');
      replayTimeLabel.textContent = fmtReplayTime(replayCursor);
      replayHint.textContent = `Last 24h replay | ${locations.length} active`;
    }

    function stopReplay() {
      replayMode = false;
      if (replayTimer) {
        clearInterval(replayTimer);
        replayTimer = null;
      }
      replayToggle.textContent = 'Replay 24h';
      replayPlay.textContent = 'Play';
      replaySlider.value = replaySlider.max;
      replayTimeLabel.textContent = 'live';
      replayHint.textContent = 'Live tracking';
      if (latestData) renderLocations(latestData.locations || []);
    }

    function pauseReplayPlayback() {
      if (replayTimer) clearInterval(replayTimer);
      replayTimer = null;
      replayPlay.textContent = 'Play';
    }

    function playReplayFromCurrent() {
      if (!latestData) return;
      replayMode = true;
      replayToggle.textContent = 'Live';
      pauseReplayPlayback();
      replayPlay.textContent = 'Pause';
      replayTimer = setInterval(() => {
        let next = Number(replaySlider.value) + 5;
        if (next > Number(replaySlider.max)) next = 0;
        replaySlider.value = next;
        renderReplay();
      }, 700);
    }

    function startReplay() {
      if (!latestData) return;
      replayMode = true;
      replayToggle.textContent = 'Live';
      replaySlider.value = 0;
      renderReplay();
      playReplayFromCurrent();
    }

    async function refresh() {
      const res = await fetch('__LOCATIONS_URL__', { cache: 'no-store' });
      const data = await res.json();
      latestData = data;
      updateReplayWindow(data);
      if (replayMode) renderReplay();
      else renderLocations(data.locations || []);
    }

    replayToggle.addEventListener('click', () => {
      if (replayMode) stopReplay();
      else startReplay();
    });
    replayPlay.addEventListener('click', () => {
      if (!replayMode) {
        replayMode = true;
        replayToggle.textContent = 'Live';
        renderReplay();
      }
      if (replayTimer) pauseReplayPlayback();
      else playReplayFromCurrent();
    });
    replaySlider.addEventListener('input', () => {
      if (!replayMode) {
        replayMode = true;
        replayToggle.textContent = 'Live';
      }
      pauseReplayPlayback();
      renderReplay();
    });

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>
"""
    return page.replace("__STATUS_URL__", status_url).replace("__LOCATIONS_URL__", locations_url).replace("__LOGOUT_URL__", logout_url)


