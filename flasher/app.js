// ── Firmware type definitions ─────────────────────────────────────────────
const TYPES = [
  { id: 'repeater',      name: 'Repeater',         desc: 'Standard mesh repeater' },
  { id: 'bridge_tcp',    name: 'TCP Bridge',        desc: 'Internet bridge via WiFi' },
  { id: 'bridge_rs232',  name: 'RS232 Bridge',      desc: 'Internet bridge via USB cable' },
  { id: 'bridge_espnow', name: 'ESPNow Bridge',     desc: 'Local bridge via ESPNow' },
  { id: 'companion_ble', name: 'Companion (BLE)',   desc: 'Companion radio, Bluetooth' },
  { id: 'companion_usb', name: 'Companion (USB)',   desc: 'Companion radio, USB serial' },
  { id: 'companion_wifi',name: 'Companion (WiFi)',  desc: 'Companion radio, WiFi' },
  { id: 'room_server',   name: 'Room Server',       desc: 'Shared BBS / room server' },
  { id: 'sensor',        name: 'Sensor',            desc: 'Remote sensor node' },
  { id: 'kiss_modem',    name: 'KISS Modem',        desc: 'Serial KISS protocol bridge' },
  { id: 'terminal_chat', name: 'Terminal Chat',     desc: 'Secure terminal chat' },
  { id: 'other',         name: 'Other',             desc: 'Other firmware variants' },
];

// ── Category detection from env name ─────────────────────────────────────
function getCategory(env) {
  const n = env.replace(/_+$/, '').toLowerCase();
  if (n.endsWith('_repeater_bridge_tcp'))    return 'bridge_tcp';
  if (n.endsWith('_repeater_bridge_rs232'))  return 'bridge_rs232';
  if (n.endsWith('_repeater_bridge_espnow')) return 'bridge_espnow';
  if (n.includes('_logging_repeater'))       return 'repeater';
  if (n.endsWith('_repeater'))               return 'repeater';
  if (n.endsWith('_repeatr'))                return 'repeater';
  if (n.includes('_companion_radio_ble') || n.endsWith('_companion_ble')) return 'companion_ble';
  if (n.includes('_companion_radio_usb') || n.endsWith('_companion_usb') || n.endsWith('_comp_radio_usb')) return 'companion_usb';
  if (n.includes('_companion_radio_wifi'))   return 'companion_wifi';
  if (n.endsWith('_room_server') || n.endsWith('_room_svr')) return 'room_server';
  if (n.endsWith('_sensor'))                 return 'sensor';
  if (n.endsWith('_kiss_modem'))             return 'kiss_modem';
  if (n.endsWith('_terminal_chat'))          return 'terminal_chat';
  return 'other';
}

// ── State ─────────────────────────────────────────────────────────────────
let allBoards = [];
let selectedType = null;

const typeGrid    = document.getElementById('type-grid');
const boardSelect = document.getElementById('board-select');
const boardHint   = document.getElementById('board-hint');
const boardDesc   = document.getElementById('board-desc');
const versionSelect = document.getElementById('version-select');
const versionHint   = document.getElementById('version-hint');
const versionDesc   = document.getElementById('version-desc');
const installBtn  = document.getElementById('install-btn');
const installAct  = document.getElementById('install-activate');

// ── Load boards ───────────────────────────────────────────────────────────
async function loadBoards() {
  try {
    const r = await fetch('./boards.json', { cache: 'no-store' });
    if (!r.ok) throw new Error(`boards.json: ${r.status}`);
    allBoards = await r.json();
    allBoards = allBoards.map(b => ({ ...b, category: getCategory(b.env) }));
    buildTypeGrid();
  } catch (e) {
    boardHint.textContent = `Could not load firmware list: ${e.message}`;
  }
}

// ── Build firmware type grid ──────────────────────────────────────────────
function buildTypeGrid() {
  const available = new Set(allBoards.map(b => b.category));

  TYPES.filter(t => available.has(t.id)).forEach((type, i) => {
    const label = document.createElement('label');
    label.className = 'type-card';
    label.innerHTML = `
      <input type="radio" name="fwtype" value="${type.id}" ${i === 0 ? 'checked' : ''} />
      <div class="type-body">
        <div class="type-name">${type.name}</div>
        <div class="type-desc">${type.desc}</div>
      </div>`;
    label.querySelector('input').addEventListener('change', () => {
      document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
      label.classList.add('selected');
      selectType(type.id);
    });
    if (i === 0) label.classList.add('selected');
    typeGrid.appendChild(label);
  });

  // Auto-select first type
  const firstType = TYPES.find(t => available.has(t.id));
  if (firstType) selectType(firstType.id);
}

// ── Filter boards by type ─────────────────────────────────────────────────
function selectType(typeId) {
  selectedType = typeId;
  const filtered = allBoards.filter(b => b.category === typeId);

  boardSelect.innerHTML = '';
  resetVersionSelect('Select a board first.');
  if (filtered.length === 0) {
    boardHint.textContent = 'No firmware available for this type yet.';
    boardHint.style.display = 'block';
    boardSelect.style.display = 'none';
    boardDesc.textContent = '';
    setInstallManifest('');
    return;
  }

  boardHint.style.display = 'none';
  boardSelect.style.display = 'block';

  filtered.forEach((board, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = board.name;
    boardSelect.appendChild(opt);
  });

  boardSelect.value = '0';
  selectBoard(filtered[0]);

  boardSelect.onchange = () => {
    selectBoard(filtered[Number(boardSelect.value)]);
  };
}

// ── Select board ──────────────────────────────────────────────────────────
function selectBoard(board) {
  boardDesc.textContent = board.description || '';
  const releases = getBoardReleases(board);

  versionSelect.innerHTML = '';
  if (releases.length === 0) {
    resetVersionSelect('Firmware not yet released for this board.');
    boardDesc.textContent = 'Firmware not yet released for this board.';
    setInstallManifest('');
    return;
  }

  versionHint.style.display = 'none';
  versionSelect.style.display = 'block';

  releases.forEach((release, i) => {
    const opt = document.createElement('option');
    opt.value = String(i);
    opt.textContent = formatReleaseLabel(release, i === 0);
    versionSelect.appendChild(opt);
  });

  versionSelect.value = '0';
  selectRelease(releases[0]);

  versionSelect.onchange = () => {
    selectRelease(releases[Number(versionSelect.value)]);
  };
}

function getBoardReleases(board) {
  if (Array.isArray(board.releases) && board.releases.length > 0) {
    return board.releases;
  }
  if (board.manifest) {
    return [{
      version: board.version || 'latest',
      manifest: board.manifest,
      published_at: board.published_at || '',
      prerelease: false,
    }];
  }
  return [];
}

function selectRelease(release) {
  setInstallManifest(release.manifest || '');
  versionDesc.textContent = formatReleaseDetails(release);
}

function resetVersionSelect(message) {
  versionSelect.innerHTML = '';
  versionSelect.style.display = 'none';
  versionHint.textContent = message;
  versionHint.style.display = 'block';
  versionDesc.textContent = '';
  setInstallManifest('');
}

function setInstallManifest(manifest) {
  installBtn.setAttribute('manifest', manifest || '');
  installAct.disabled = !manifest;
}

function formatReleaseLabel(release, isLatest) {
  const bits = [release.version || release.name || 'release'];
  if (isLatest) bits.push('latest');
  if (release.prerelease) bits.push('pre-release');
  return bits.join(' - ');
}

function formatReleaseDetails(release) {
  const parts = [];
  if (release.published_at) {
    parts.push(`Published ${release.published_at.slice(0, 10)}`);
  }
  if (release.firmware) {
    parts.push(release.firmware);
  }
  return parts.join(' - ');
}

loadBoards();
