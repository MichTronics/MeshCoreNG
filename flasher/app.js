import { ESPLoader, Transport, HardReset } from './esp32.js';
import { Dfu } from './dfu.js';

const TYPES = [
  { id: 'repeater', name: 'Repeater', desc: 'Standard mesh repeater' },
  { id: 'bridge_tcp', name: 'TCP Bridge', desc: 'Internet bridge via WiFi' },
  { id: 'bridge_rs232', name: 'RS232 Bridge', desc: 'Internet bridge via USB cable' },
  { id: 'bridge_espnow', name: 'ESPNow Bridge', desc: 'Local bridge via ESPNow' },
  { id: 'companion_ble', name: 'Companion (BLE)', desc: 'Companion radio, Bluetooth' },
  { id: 'companion_usb', name: 'Companion (USB)', desc: 'Companion radio, USB serial' },
  { id: 'companion_wifi', name: 'Companion (WiFi)', desc: 'Companion radio, WiFi' },
  { id: 'room_server', name: 'Room Server', desc: 'Shared BBS / room server' },
  { id: 'sensor', name: 'Sensor', desc: 'Remote sensor node' },
  { id: 'kiss_modem', name: 'KISS Modem', desc: 'Serial KISS protocol bridge' },
  { id: 'terminal_chat', name: 'Terminal Chat', desc: 'Secure terminal chat' },
  { id: 'other', name: 'Other', desc: 'Other firmware variants' },
];

const ESP_UPDATE_ADDRESS = 0x10000;
const ESP_WIPE_ADDRESS = 0x0;

function getCategory(env) {
  const n = env.replace(/_+$/, '').toLowerCase();
  if (n.endsWith('_repeater_bridge_tcp')) return 'bridge_tcp';
  if (n.endsWith('_repeater_bridge_rs232')) return 'bridge_rs232';
  if (n.endsWith('_repeater_bridge_espnow')) return 'bridge_espnow';
  if (n.includes('_logging_repeater')) return 'repeater';
  if (n.endsWith('_repeater')) return 'repeater';
  if (n.endsWith('_repeatr')) return 'repeater';
  if (n.includes('_companion_radio_ble') || n.endsWith('_companion_ble')) return 'companion_ble';
  if (n.includes('_companion_radio_usb') || n.endsWith('_companion_usb') || n.endsWith('_comp_radio_usb')) return 'companion_usb';
  if (n.includes('_companion_radio_wifi')) return 'companion_wifi';
  if (n.endsWith('_room_server') || n.endsWith('_room_svr')) return 'room_server';
  if (n.endsWith('_sensor')) return 'sensor';
  if (n.endsWith('_kiss_modem')) return 'kiss_modem';
  if (n.endsWith('_terminal_chat')) return 'terminal_chat';
  return 'other';
}

function getDeviceType(board) {
  const family = (board?.chipFamily || '').toLowerCase();
  if (family.startsWith('esp32')) return 'esp32';
  if (family === 'nrf52' || family.includes('nrf528')) return 'nrf52';
  return 'download';
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function blobToBinaryString(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let out = '';
  for (let i = 0; i < bytes.length; i += 1) {
    out += String.fromCharCode(bytes[i]);
  }
  return out;
}

let allBoards = [];
let selectedType = null;
let selectedBoard = null;
let selectedRelease = null;
let currentTransport = null;

const typeGrid = document.getElementById('type-grid');
const boardSelect = document.getElementById('board-select');
const boardHint = document.getElementById('board-hint');
const boardDesc = document.getElementById('board-desc');
const versionSelect = document.getElementById('version-select');
const versionHint = document.getElementById('version-hint');
const versionDesc = document.getElementById('version-desc');
const flashBtn = document.getElementById('flash-btn');
const noSerialMsg = document.getElementById('no-serial-msg');
const flashStatus = document.getElementById('flash-status');
const flashProgressBar = document.getElementById('flash-progress-bar');
const flashMsg = document.getElementById('flash-msg');
const flashLog = document.getElementById('flash-log');
const flashLogPre = document.getElementById('flash-log-pre');
const flashLogToggle = document.getElementById('flash-log-toggle');
const wipeRow = document.getElementById('wipe-row');
const wipeCheck = document.getElementById('wipe-check');
const dfuBtn = document.getElementById('dfu-btn');
const eraseBtn = document.getElementById('erase-btn');
const downloadList = document.getElementById('download-list');
const customFile = document.getElementById('custom-file');
const deviceNotice = document.getElementById('device-notice');

const serialSupported = 'Serial' in window || 'serial' in navigator;
if (!serialSupported) {
  flashBtn.style.display = 'none';
  noSerialMsg.style.display = 'inline';
}

async function loadBoards() {
  try {
    const r = await fetch('./boards.json', { cache: 'no-store' });
    if (!r.ok) throw new Error(`boards.json: ${r.status}`);
    allBoards = await r.json();
    allBoards = allBoards.map((b) => ({
      ...b,
      category: b.category || getCategory(b.env || ''),
      deviceType: b.type || getDeviceType(b),
    }));
    buildTypeGrid();
  } catch (e) {
    boardHint.textContent = `Could not load firmware list: ${e.message}`;
  }
}

function buildTypeGrid() {
  const available = new Set(allBoards.map((b) => b.category));
  typeGrid.innerHTML = '';

  TYPES.filter((t) => available.has(t.id)).forEach((type, i) => {
    const label = document.createElement('label');
    label.className = 'type-card';
    label.innerHTML = `
      <input type="radio" name="fwtype" value="${type.id}" ${i === 0 ? 'checked' : ''} />
      <div class="type-body">
        <div class="type-name">${type.name}</div>
        <div class="type-desc">${type.desc}</div>
      </div>`;
    label.querySelector('input').addEventListener('change', () => {
      document.querySelectorAll('.type-card').forEach((c) => c.classList.remove('selected'));
      label.classList.add('selected');
      selectType(type.id);
    });
    if (i === 0) label.classList.add('selected');
    typeGrid.appendChild(label);
  });

  const firstType = TYPES.find((t) => available.has(t.id));
  if (firstType) selectType(firstType.id);
}

function selectType(typeId) {
  selectedType = typeId;
  const filtered = allBoards.filter((b) => b.category === typeId);

  boardSelect.innerHTML = '';
  resetVersionSelect('Select a board first.');
  clearSelection();

  if (filtered.length === 0) {
    boardHint.textContent = 'No firmware available for this type yet.';
    boardHint.style.display = 'block';
    boardSelect.style.display = 'none';
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

  boardSelect.onchange = () => selectBoard(filtered[Number(boardSelect.value)]);
  boardSelect.value = '0';
  selectBoard(filtered[0]);
}

function selectBoard(board) {
  selectedBoard = board;
  boardDesc.textContent = board.description || '';
  const releases = getBoardReleases(board);

  versionSelect.innerHTML = '';
  if (releases.length === 0) {
    resetVersionSelect('Firmware not yet released for this board.');
    boardDesc.textContent = 'Firmware not yet released for this board.';
    selectedRelease = null;
    updateActionState();
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

  versionSelect.onchange = () => selectRelease(releases[Number(versionSelect.value)]);
  versionSelect.value = '0';
  selectRelease(releases[0]);
}

function getBoardReleases(board) {
  if (Array.isArray(board.releases) && board.releases.length > 0) return board.releases;
  if (Array.isArray(board.files) && board.files.length > 0) {
    return [{ version: board.version || 'latest', files: board.files }];
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
  selectedRelease = release;
  versionDesc.textContent = formatReleaseDetails(release);
  updateActionState();
}

function resetVersionSelect(message) {
  versionSelect.innerHTML = '';
  versionSelect.style.display = 'none';
  versionHint.textContent = message;
  versionHint.style.display = 'block';
  versionDesc.textContent = '';
}

function clearSelection() {
  selectedBoard = null;
  selectedRelease = null;
  boardDesc.textContent = '';
  updateActionState();
}

function updateActionState() {
  const type = selectedBoard?.deviceType || '';
  const files = getReleaseFiles(selectedRelease);
  const canFlash = Boolean(selectedRelease && serialSupported && (type === 'esp32' || type === 'nrf52'));

  flashBtn.disabled = !canFlash;
  flashBtn.textContent = type === 'nrf52' ? 'Flash nRF52 DFU' : 'Flash MeshCoreNG';
  wipeRow.style.display = type === 'esp32' ? 'block' : 'none';
  dfuBtn.style.display = type === 'nrf52' ? 'inline-flex' : 'none';
  eraseBtn.style.display = type === 'nrf52' && selectedBoard?.erase ? 'inline-flex' : 'none';
  eraseBtn.disabled = !serialSupported;
  dfuBtn.disabled = !serialSupported;

  if (type === 'nrf52') {
    deviceNotice.textContent = 'nRF52 devices use the same serial DFU ZIP flashing flow as the MeshCore flasher.';
  } else if (type === 'download') {
    deviceNotice.textContent = 'This board family is not flashable through Web Serial here. Use the download links below.';
  } else if (type === 'esp32') {
    deviceNotice.textContent = 'ESP32 flashing supports update and full erase files when both are available.';
  } else {
    deviceNotice.textContent = '';
  }

  renderDownloads(files);
}

function getReleaseFiles(release) {
  if (!release) return [];
  if (Array.isArray(release.files)) return release.files;
  if (release.firmware) {
    return [{ type: 'download', name: release.firmware, title: release.firmware }];
  }
  if (release.manifest) {
    return [{ type: 'manifest', name: release.manifest, title: 'ESP Web Tools manifest' }];
  }
  return [];
}

function formatReleaseLabel(release, isLatest) {
  const bits = [release.version || release.name || 'release'];
  if (isLatest) bits.push('latest');
  if (release.prerelease) bits.push('pre-release');
  return bits.join(' - ');
}

function formatReleaseDetails(release) {
  const parts = [];
  if (release?.published_at) parts.push(`Published ${release.published_at.slice(0, 10)}`);
  if (release?.firmware) parts.push(release.firmware);
  if (Array.isArray(release?.files) && release.files[0]?.title) parts.push(release.files[0].title);
  return parts.join(' - ');
}

function getFirmwarePath(file) {
  const name = file?.name || file?.path || '';
  if (file?.file) return '';
  if (name.startsWith('/') || name.startsWith('http://') || name.startsWith('https://') || name.startsWith('./')) {
    return name;
  }
  return `./firmware/${selectedBoard.env}/${name}`;
}

function renderDownloads(files) {
  downloadList.innerHTML = '';
  files.filter((file) => !file.file && file.type !== 'manifest').forEach((file) => {
    const a = document.createElement('a');
    a.href = getFirmwarePath(file);
    a.download = '';
    a.textContent = file.title || file.name || 'Download firmware';
    downloadList.appendChild(a);
  });
}

function logAppend(text) {
  flashLogPre.textContent += text;
  flashLogPre.scrollTop = flashLogPre.scrollHeight;
}

function setProgress(pct) {
  flashProgressBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function setStatus(msg, isError = false) {
  flashMsg.textContent = msg;
  flashMsg.className = `flash-msg${isError ? ' flash-error' : ''}`;
}

function resetStatus() {
  flashLogPre.textContent = '';
  flashLog.style.display = 'none';
  flashLogToggle.textContent = 'Show log';
  flashStatus.classList.add('active');
  setProgress(0);
  setStatus('');
}

async function fetchBlob(url) {
  const resp = await fetch(url);
  if (resp.status !== 200) {
    throw new Error(`Could not download firmware: HTTP ${resp.status}`);
  }
  return await resp.blob();
}

async function loadManifestFirmware(release) {
  const manifestResp = await fetch(release.manifest);
  if (!manifestResp.ok) throw new Error(`Cannot load manifest: ${manifestResp.status}`);
  const manifest = await manifestResp.json();
  const part = manifest.builds?.[0]?.parts?.[0];
  if (!part) throw new Error('No firmware part found in manifest');
  const manifestDir = release.manifest.substring(0, release.manifest.lastIndexOf('/') + 1);
  return {
    blob: await fetchBlob(manifestDir + part.path),
    address: Number(part.offset || 0),
    eraseAll: false,
  };
}

function chooseEspFile(files) {
  const wantsWipe = wipeCheck.checked;
  const type = wantsWipe ? 'flash-wipe' : 'flash-update';
  let file = files.find((f) => f.type === type);
  file ||= files.find((f) => f.type === 'flash');
  file ||= files.find((f) => f.type === 'flash-wipe');
  file ||= files.find((f) => f.type === 'flash-update');
  return file;
}

async function loadEspFirmware() {
  if (selectedRelease?.manifest && !selectedRelease.files) {
    return await loadManifestFirmware(selectedRelease);
  }

  const files = getReleaseFiles(selectedRelease);
  const file = chooseEspFile(files);
  if (!file) throw new Error('Cannot find an ESP32 flash file for this release.');

  const isWipeFile = file.type === 'flash-wipe' || /-merged\.bin$/i.test(file.name || '');
  const address = isWipeFile || wipeCheck.checked ? ESP_WIPE_ADDRESS : ESP_UPDATE_ADDRESS;
  const blob = file.file || await fetchBlob(getFirmwarePath(file));
  return { blob, address, eraseAll: Boolean(wipeCheck.checked || isWipeFile) };
}

async function flashEsp32() {
  const { blob, address, eraseAll } = await loadEspFirmware();
  const port = await navigator.serial.requestPort({});
  currentTransport = new Transport(port, true);

  const terminal = {
    clean: () => { flashLogPre.textContent = ''; },
    writeLine: (line) => logAppend(`${line}\n`),
    write: (text) => logAppend(text),
  };

  const flashOptions = {
    transport: currentTransport,
    terminal,
    compress: true,
    eraseAll,
    flashSize: 'keep',
    flashMode: 'keep',
    flashFreq: 'keep',
    baudrate: 115200,
    romBaudrate: 115200,
    enableTracing: false,
    fileArray: [{ data: await blobToBinaryString(blob), address }],
    reportProgress: async (_, written, total) => {
      setProgress((written / total) * 100);
      setStatus(`Flashing... ${Math.round((written / total) * 100)}%`);
    },
  };

  const esploader = new ESPLoader(flashOptions);
  esploader.hr = new HardReset(currentTransport);

  setStatus('Connecting...');
  await esploader.main();
  await esploader.flashId();

  setStatus('Flashing firmware...');
  await esploader.writeFlash(flashOptions);
  await delay(100);
  await esploader.after('hard_reset');
  await delay(100);
  await espReset(currentTransport);
  await currentTransport.disconnect();
  currentTransport = null;
}

async function espReset(transport) {
  await transport.setRTS(true);
  await delay(100);
  await transport.setRTS(false);
}

function chooseNrfFile(files) {
  return files.find((f) => f.type?.startsWith('flash') && /\.zip$/i.test(f.name || f.title || ''))
    || files.find((f) => /\.zip$/i.test(f.name || f.title || ''));
}

async function flashNrf52() {
  const file = chooseNrfFile(getReleaseFiles(selectedRelease));
  if (!file) throw new Error('Cannot find an nRF52 DFU ZIP for this release.');
  const blob = file.file || await fetchBlob(getFirmwarePath(file));
  const port = await navigator.serial.requestPort({});
  const dfu = new Dfu(port);
  await dfu.dfuUpdate(blob, (progress) => {
    setProgress(progress);
    setStatus(`Flashing... ${progress}%`);
  });
}

async function flashSelected() {
  if (!selectedRelease || !selectedBoard || !serialSupported) return;

  flashBtn.disabled = true;
  resetStatus();

  try {
    if (selectedBoard.deviceType === 'esp32') {
      await flashEsp32();
    } else if (selectedBoard.deviceType === 'nrf52') {
      await flashNrf52();
    } else {
      throw new Error('This board cannot be flashed through Web Serial.');
    }
    setProgress(100);
    setStatus('Flashing complete.');
  } catch (err) {
    if (err.name === 'NotFoundError') {
      setStatus('Port selection cancelled.');
      setProgress(0);
    } else {
      setStatus(`Error: ${err.message || err}`, true);
      flashLog.style.display = 'block';
      flashLogToggle.textContent = 'Hide log';
      console.error(err);
    }
    if (currentTransport) {
      try {
        await espReset(currentTransport);
        await currentTransport.disconnect();
      } catch {}
      currentTransport = null;
    }
  } finally {
    updateActionState();
  }
}

async function enterDfuMode() {
  if (!serialSupported) return;
  resetStatus();
  try {
    setStatus('Select the nRF52 serial port...');
    await Dfu.forceDfuMode(await navigator.serial.requestPort({}));
    setProgress(100);
    setStatus('DFU mode active. You can flash now.');
  } catch (err) {
    setStatus(`DFU mode failed: ${err.message || err}`, true);
  }
}

async function eraseNrf52() {
  if (!selectedBoard?.erase || !serialSupported) return;
  resetStatus();
  try {
    const blob = await fetchBlob(`./firmware/${selectedBoard.env}/${selectedBoard.erase}`);
    const port = await navigator.serial.requestPort({});
    const dfu = new Dfu(port);
    await dfu.dfuUpdate(blob, (progress) => {
      setProgress(progress);
      setStatus(`Flashing erase firmware... ${progress}%`);
    });
    setStatus('Erase firmware flashed.');
  } catch (err) {
    setStatus(`nRF erase failed: ${err.message || err}`, true);
  }
}

function loadCustomFirmware(ev) {
  const firmwareFile = ev.target.files[0];
  if (!firmwareFile) return;
  const type = firmwareFile.name.endsWith('.bin') ? 'esp32' : 'nrf52';
  selectedType = 'other';
  selectedBoard = {
    env: 'custom',
    name: 'Custom device',
    description: firmwareFile.name,
    chipFamily: type === 'esp32' ? 'ESP32' : 'nRF52',
    category: 'other',
    deviceType: type,
  };
  selectedRelease = {
    version: firmwareFile.name,
    files: [{ type: 'flash', name: firmwareFile.name, title: firmwareFile.name, file: firmwareFile }],
  };
  if (firmwareFile.name.endsWith('-merged.bin')) {
    wipeCheck.checked = true;
  }
  boardDesc.textContent = `Custom firmware: ${firmwareFile.name}`;
  versionDesc.textContent = type === 'esp32' ? 'ESP32 custom firmware' : 'nRF52 DFU ZIP';
  updateActionState();
}

flashLogToggle.addEventListener('click', () => {
  const hidden = flashLog.style.display === 'none';
  flashLog.style.display = hidden ? 'block' : 'none';
  flashLogToggle.textContent = hidden ? 'Hide log' : 'Show log';
});

flashBtn.addEventListener('click', flashSelected);
dfuBtn.addEventListener('click', enterDfuMode);
eraseBtn.addEventListener('click', eraseNrf52);
customFile.addEventListener('change', loadCustomFirmware);

loadBoards();
