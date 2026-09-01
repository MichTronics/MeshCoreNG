#include "BLEBridge.h"

#ifdef WITH_BLE_BRIDGE

#include <string.h>

#if defined(ESP32)
#include <esp_mac.h>
#endif

#ifndef BLE_TX_POWER
#define BLE_TX_POWER 4
#endif

#define BLE_BRIDGE_DEVICE_NAME "MCNG BLE Bridge"
#define BLE_BRIDGE_SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_BRIDGE_RX_UUID "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_BRIDGE_TX_UUID "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
#define BLE_BRIDGE_SCAN_RESTART_MS 3000
#define BLE_BRIDGE_WRITE_CHUNK 180
#define BLE_BRIDGE_WRITE_INTERVAL_MS 20

BLEBridge *BLEBridge::_instance = nullptr;

#if defined(NRF52_PLATFORM)

BLEBridge::BLEBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc)
    : BridgeBase(prefs, mgr, rtc),
      _peripheral_uart(BLE_UART_FIFO_DEPTH),
      _central_uart(BLE_UART_FIFO_DEPTH),
      _peripheral_conn_handle(BLE_CONN_HANDLE_INVALID),
      _central_conn_handle(BLE_CONN_HANDLE_INVALID),
      _central_connected(false),
      _central_discovered(false),
      _peripheral_connected(false),
      _should_connect(false),
      _next_scan_at(0),
      _last_write_at(0),
      _peripheral_rx_buffer_pos(0),
      _central_rx_buffer_pos(0) {
  _instance = this;
}

void BLEBridge::begin() {
  BRIDGE_DEBUG_PRINTLN("Initializing BLE bridge...\n");

  _instance = this;
  _peripheral_conn_handle = BLE_CONN_HANDLE_INVALID;
  _central_conn_handle = BLE_CONN_HANDLE_INVALID;
  _central_connected = false;
  _central_discovered = false;
  _peripheral_connected = false;
  _should_connect = false;
  _peripheral_rx_buffer_pos = 0;
  _central_rx_buffer_pos = 0;
  _tx_frames = 0;
  _rx_frames = 0;
  _rx_bad_checksum = 0;
  _rx_parse_fail = 0;
  _tx_seen_drop = 0;
  _tx_no_link = 0;

  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);
  Bluefruit.configCentralBandwidth(BANDWIDTH_MAX);
  Bluefruit.begin(1, 1);
  Bluefruit.setTxPower(BLE_TX_POWER);
  Bluefruit.setName(BLE_BRIDGE_DEVICE_NAME);
  Bluefruit.Periph.setConnInterval(6, 12);
  Bluefruit.Central.setConnInterval(6, 12);

  Bluefruit.Periph.setConnectCallback(peripheralConnectCallback);
  Bluefruit.Periph.setDisconnectCallback(peripheralDisconnectCallback);
  Bluefruit.Central.setConnectCallback(centralConnectCallback);
  Bluefruit.Central.setDisconnectCallback(centralDisconnectCallback);

  _peripheral_uart.begin();
  _peripheral_uart.setRxCallback(peripheralRxCallback);

  _central_uart.begin();
  _central_uart.setRxCallback(centralRxCallback);

  startScanning();
  startAdvertising();

  _initialized = true;
}

void BLEBridge::end() {
  BRIDGE_DEBUG_PRINTLN("Stopping BLE bridge...\n");

  Bluefruit.Scanner.stop();
  if (_central_conn_handle != BLE_CONN_HANDLE_INVALID) {
    Bluefruit.disconnect(_central_conn_handle);
  }
  if (_peripheral_conn_handle != BLE_CONN_HANDLE_INVALID) {
    Bluefruit.disconnect(_peripheral_conn_handle);
  }

  _peripheral_conn_handle = BLE_CONN_HANDLE_INVALID;
  _central_conn_handle = BLE_CONN_HANDLE_INVALID;
  _central_connected = false;
  _central_discovered = false;
  _peripheral_connected = false;
  _initialized = false;
}

void BLEBridge::loop() {
  if (!_initialized) {
    return;
  }

  readFrom(_peripheral_uart, _peripheral_rx_buffer, _peripheral_rx_buffer_pos);
  readFrom(_central_uart, _central_rx_buffer, _central_rx_buffer_pos);
}

void BLEBridge::startAdvertising() {
  Bluefruit.Advertising.clearData();
  Bluefruit.ScanResponse.clearData();

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(_peripheral_uart);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);
}

void BLEBridge::startScanning() {
  Bluefruit.Scanner.setRxCallback(scanCallback);
  Bluefruit.Scanner.restartOnDisconnect(true);
  Bluefruit.Scanner.setInterval(160, 80);
  Bluefruit.Scanner.filterUuid(_peripheral_uart.uuid);
  Bluefruit.Scanner.useActiveScan(false);
  Bluefruit.Scanner.start(0);
}

bool BLEBridge::connectToAdvertisedDevice() {
  return false;
}

bool BLEBridge::shouldInitiateConnection(const ble_gap_addr_t &peer_addr) {
  if (peer_addr.addr_type == BLE_GAP_ADDR_TYPE_ANONYMOUS) {
    return false;
  }

  ble_gap_addr_t local_addr;
  if (sd_ble_gap_addr_get(&local_addr) != NRF_SUCCESS) {
    return true;
  }

  for (int i = BLE_GAP_ADDR_LEN - 1; i >= 0; i--) {
    if (local_addr.addr[i] < peer_addr.addr[i]) {
      return true;
    }
    if (local_addr.addr[i] > peer_addr.addr[i]) {
      return false;
    }
  }

  return false;
}

void BLEBridge::scanCallback(ble_gap_evt_adv_report_t *report) {
  if (!_instance) {
    return;
  }

  if (_instance->_central_connected || _instance->_central_conn_handle != BLE_CONN_HANDLE_INVALID) {
    Bluefruit.Scanner.resume();
    return;
  }

  if (!shouldInitiateConnection(report->peer_addr)) {
    Bluefruit.Scanner.resume();
    return;
  }

  Bluefruit.Central.connect(report);
}

void BLEBridge::peripheralConnectCallback(uint16_t conn_handle) {
  if (_instance) {
    _instance->_peripheral_conn_handle = conn_handle;
    _instance->_peripheral_connected = true;
    _instance->_peripheral_rx_buffer_pos = 0;
    BRIDGE_DEBUG_PRINTLN("BLE peripheral connected handle=0x%04x\n", conn_handle);
  }
}

void BLEBridge::peripheralDisconnectCallback(uint16_t conn_handle, uint8_t reason) {
  (void)reason;
  if (_instance && _instance->_peripheral_conn_handle == conn_handle) {
    _instance->_peripheral_conn_handle = BLE_CONN_HANDLE_INVALID;
    _instance->_peripheral_connected = false;
    _instance->_peripheral_rx_buffer_pos = 0;
    BRIDGE_DEBUG_PRINTLN("BLE peripheral disconnected handle=0x%04x\n", conn_handle);
  }
}

void BLEBridge::centralConnectCallback(uint16_t conn_handle) {
  if (!_instance) {
    return;
  }

  _instance->_central_conn_handle = conn_handle;
  _instance->_central_connected = true;
  _instance->_central_discovered = false;
  _instance->_central_rx_buffer_pos = 0;

  if (_instance->_central_uart.discover(conn_handle)) {
    _instance->_central_uart.enableTXD();
    _instance->_central_discovered = true;
    BRIDGE_DEBUG_PRINTLN("BLE central connected handle=0x%04x\n", conn_handle);
  } else {
    BRIDGE_DEBUG_PRINTLN("BLE central failed to discover UART handle=0x%04x\n", conn_handle);
    Bluefruit.disconnect(conn_handle);
  }
}

void BLEBridge::centralDisconnectCallback(uint16_t conn_handle, uint8_t reason) {
  (void)reason;
  if (_instance && _instance->_central_conn_handle == conn_handle) {
    _instance->_central_conn_handle = BLE_CONN_HANDLE_INVALID;
    _instance->_central_connected = false;
    _instance->_central_discovered = false;
    _instance->_central_rx_buffer_pos = 0;
    BRIDGE_DEBUG_PRINTLN("BLE central disconnected handle=0x%04x\n", conn_handle);
  }
}

void BLEBridge::peripheralRxCallback(uint16_t conn_handle) {
  (void)conn_handle;
  if (_instance) {
    _instance->readFrom(_instance->_peripheral_uart, _instance->_peripheral_rx_buffer,
                        _instance->_peripheral_rx_buffer_pos);
  }
}

void BLEBridge::centralRxCallback(BLEClientUart &uart) {
  if (_instance) {
    _instance->readFrom(uart, _instance->_central_rx_buffer, _instance->_central_rx_buffer_pos);
  }
}

void BLEBridge::sendFrame(BLEUart &uart, uint16_t conn_handle, const uint8_t *buffer, size_t len) {
  if (conn_handle != BLE_CONN_HANDLE_INVALID && uart.notifyEnabled(conn_handle)) {
    uart.write(conn_handle, buffer, len);
  }
}

void BLEBridge::sendFrame(BLEClientUart &uart, const uint8_t *buffer, size_t len) {
  if (_central_connected && _central_discovered) {
    uart.write(buffer, len);
  }
}

#elif defined(ESP32)

BLEBridge::BLEBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc)
    : BridgeBase(prefs, mgr, rtc),
      _server(nullptr),
      _service(nullptr),
      _server_tx_characteristic(nullptr),
      _server_rx_characteristic(nullptr),
      _client(nullptr),
      _client_rx_characteristic(nullptr),
      _client_tx_characteristic(nullptr),
      _advertised_device(nullptr),
      _central_connected(false),
      _central_discovered(false),
      _peripheral_connected(false),
      _should_connect(false),
      _next_scan_at(0),
      _last_write_at(0),
      _peripheral_rx_buffer_pos(0),
      _central_rx_buffer_pos(0) {
  _instance = this;
}

void BLEBridge::begin() {
  BRIDGE_DEBUG_PRINTLN("Initializing ESP32 BLE bridge...\n");

  _instance = this;
  _central_connected = false;
  _central_discovered = false;
  _peripheral_connected = false;
  _should_connect = false;
  _peripheral_rx_buffer_pos = 0;
  _central_rx_buffer_pos = 0;
  _tx_frames = 0;
  _rx_frames = 0;
  _rx_bad_checksum = 0;
  _rx_parse_fail = 0;
  _tx_seen_drop = 0;
  _tx_no_link = 0;

  BLEDevice::init(BLE_BRIDGE_DEVICE_NAME);
  BLEDevice::setMTU(517);

  _server = BLEDevice::createServer();
  _server->setCallbacks(this);

  _service = _server->createService(BLE_BRIDGE_SERVICE_UUID);
  _server_tx_characteristic =
      _service->createCharacteristic(BLE_BRIDGE_TX_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  _server_tx_characteristic->addDescriptor(new BLE2902());

  _server_rx_characteristic =
      _service->createCharacteristic(BLE_BRIDGE_RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
  _server_rx_characteristic->setCallbacks(this);

  _service->start();

  _server->getAdvertising()->addServiceUUID(BLE_BRIDGE_SERVICE_UUID);
  startAdvertising();
  startScanning();

  _initialized = true;
}

void BLEBridge::end() {
  BRIDGE_DEBUG_PRINTLN("Stopping ESP32 BLE bridge...\n");

  BLEDevice::getScan()->stop();
  if (_client && _client->isConnected()) {
    _client->disconnect();
  }
  if (_server) {
    _server->getAdvertising()->stop();
  }

  delete _advertised_device;
  _advertised_device = nullptr;
  _client_rx_characteristic = nullptr;
  _client_tx_characteristic = nullptr;
  _central_connected = false;
  _central_discovered = false;
  _peripheral_connected = false;
  _should_connect = false;
  _initialized = false;
}

void BLEBridge::loop() {
  if (!_initialized) {
    return;
  }

  if (_should_connect) {
    connectToAdvertisedDevice();
    _should_connect = false;
  }

  if (!_central_connected && !_should_connect && millis() >= _next_scan_at) {
    startScanning();
  }
}

void BLEBridge::startAdvertising() {
  if (_server) {
    _server->getAdvertising()->start();
  }
}

void BLEBridge::startScanning() {
  BLEScan *scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(this);
  scan->setInterval(1349);
  scan->setWindow(449);
  scan->setActiveScan(true);
  scan->start(5, false);
  _next_scan_at = millis() + BLE_BRIDGE_SCAN_RESTART_MS;
}

bool BLEBridge::connectToAdvertisedDevice() {
  if (!_advertised_device || _central_connected) {
    return false;
  }

  if (!_client) {
    _client = BLEDevice::createClient();
    _client->setClientCallbacks(this);
  }

  BRIDGE_DEBUG_PRINTLN("ESP32 BLE connecting to %s\n", _advertised_device->getAddress().toString().c_str());
  if (!_client->connect(_advertised_device)) {
    BRIDGE_DEBUG_PRINTLN("ESP32 BLE connect failed\n");
    _next_scan_at = millis() + BLE_BRIDGE_SCAN_RESTART_MS;
    return false;
  }

  _client->setMTU(517);

  BLERemoteService *remote_service = _client->getService(BLE_BRIDGE_SERVICE_UUID);
  if (!remote_service) {
    BRIDGE_DEBUG_PRINTLN("ESP32 BLE remote UART service not found\n");
    _client->disconnect();
    return false;
  }

  _client_rx_characteristic = remote_service->getCharacteristic(BLE_BRIDGE_RX_UUID);
  _client_tx_characteristic = remote_service->getCharacteristic(BLE_BRIDGE_TX_UUID);
  if (!_client_rx_characteristic || !_client_tx_characteristic) {
    BRIDGE_DEBUG_PRINTLN("ESP32 BLE remote UART characteristics not found\n");
    _client->disconnect();
    return false;
  }

  if (_client_tx_characteristic->canNotify()) {
    _client_tx_characteristic->registerForNotify(clientNotifyCallback);
  }

  _central_connected = true;
  _central_discovered = true;
  _central_rx_buffer_pos = 0;
  BRIDGE_DEBUG_PRINTLN("ESP32 BLE central connected\n");
  return true;
}

void BLEBridge::clientNotifyCallback(BLERemoteCharacteristic *characteristic, uint8_t *data, size_t len, bool notify) {
  (void)characteristic;
  (void)notify;
  if (_instance) {
    _instance->readBytes(data, len, _instance->_central_rx_buffer, _instance->_central_rx_buffer_pos);
  }
}

void BLEBridge::onConnect(BLEServer *server) {
  (void)server;
  _peripheral_connected = true;
  _peripheral_rx_buffer_pos = 0;
  BRIDGE_DEBUG_PRINTLN("ESP32 BLE peripheral connected\n");
}

void BLEBridge::onDisconnect(BLEServer *server) {
  (void)server;
  _peripheral_connected = false;
  _peripheral_rx_buffer_pos = 0;
  startAdvertising();
  BRIDGE_DEBUG_PRINTLN("ESP32 BLE peripheral disconnected\n");
}

void BLEBridge::onConnect(BLEClient *client) {
  (void)client;
}

void BLEBridge::onDisconnect(BLEClient *client) {
  (void)client;
  _central_connected = false;
  _central_discovered = false;
  _client_rx_characteristic = nullptr;
  _client_tx_characteristic = nullptr;
  _central_rx_buffer_pos = 0;
  _next_scan_at = millis() + BLE_BRIDGE_SCAN_RESTART_MS;
  BRIDGE_DEBUG_PRINTLN("ESP32 BLE central disconnected\n");
}

void BLEBridge::onWrite(BLECharacteristic *characteristic) {
  uint8_t *data = characteristic->getData();
  const size_t len = characteristic->getLength();
  readBytes(data, len, _peripheral_rx_buffer, _peripheral_rx_buffer_pos);
}

void BLEBridge::onWrite(BLECharacteristic *characteristic, esp_ble_gatts_cb_param_t *param) {
  (void)param;
  onWrite(characteristic);
}

void BLEBridge::onResult(BLEAdvertisedDevice advertised_device) {
  if (_central_connected || _should_connect) {
    return;
  }

  if (advertised_device.haveServiceUUID() && advertised_device.isAdvertisingService(BLEUUID(BLE_BRIDGE_SERVICE_UUID))) {
    BLEAddress local_address = BLEDevice::getAddress();
    BLEAddress peer_address = advertised_device.getAddress();
    if (!(local_address < peer_address)) {
      BRIDGE_DEBUG_PRINTLN("ESP32 BLE peer %s seen, waiting for peer to connect\n",
                           peer_address.toString().c_str());
      return;
    }

    BLEDevice::getScan()->stop();
    delete _advertised_device;
    _advertised_device = new BLEAdvertisedDevice(advertised_device);
    _should_connect = true;
  }
}

void BLEBridge::sendServerFrame(const uint8_t *buffer, size_t len) {
  if (!_peripheral_connected || !_server_tx_characteristic) {
    return;
  }

  for (size_t offset = 0; offset < len; offset += BLE_BRIDGE_WRITE_CHUNK) {
    const size_t chunk_len = min((size_t)BLE_BRIDGE_WRITE_CHUNK, len - offset);
    _server_tx_characteristic->setValue((uint8_t *)(buffer + offset), chunk_len);
    _server_tx_characteristic->notify();
    delay(BLE_BRIDGE_WRITE_INTERVAL_MS);
  }
}

void BLEBridge::sendClientFrame(const uint8_t *buffer, size_t len) {
  if (!_central_connected || !_central_discovered || !_client_rx_characteristic) {
    return;
  }

  for (size_t offset = 0; offset < len; offset += BLE_BRIDGE_WRITE_CHUNK) {
    const size_t chunk_len = min((size_t)BLE_BRIDGE_WRITE_CHUNK, len - offset);
    _client_rx_characteristic->writeValue((uint8_t *)(buffer + offset), chunk_len,
                                          !_client_rx_characteristic->canWriteNoResponse());
    delay(BLE_BRIDGE_WRITE_INTERVAL_MS);
  }
}

#endif

void BLEBridge::xorCrypt(uint8_t *data, size_t len) {
  const size_t key_len = strlen(_prefs->bridge_secret);
  if (key_len == 0) {
    return;
  }

  for (size_t i = 0; i < len; i++) {
    data[i] ^= _prefs->bridge_secret[i % key_len];
  }
}

void BLEBridge::readFrom(Stream &stream, uint8_t *buffer, uint16_t &buffer_pos) {
  while (stream.available()) {
    int value = stream.read();
    if (value >= 0) {
      handleByte((uint8_t)value, buffer, buffer_pos);
    }
  }
}

void BLEBridge::readBytes(const uint8_t *data, size_t len, uint8_t *buffer, uint16_t &buffer_pos) {
  for (size_t i = 0; i < len; i++) {
    handleByte(data[i], buffer, buffer_pos);
  }
}

void BLEBridge::handleByte(uint8_t b, uint8_t *buffer, uint16_t &buffer_pos) {
  if (buffer_pos < 2) {
    if ((buffer_pos == 0 && b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) ||
        (buffer_pos == 1 && b == (BRIDGE_PACKET_MAGIC & 0xFF))) {
      buffer[buffer_pos++] = b;
    } else {
      buffer_pos = 0;
      if (b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) {
        buffer[buffer_pos++] = b;
      }
    }
    return;
  }

  if (buffer_pos >= MAX_BLE_BRIDGE_PACKET_SIZE) {
    BRIDGE_DEBUG_PRINTLN("BLE RX overflow, resetting\n");
    buffer_pos = 0;
    return;
  }

  buffer[buffer_pos++] = b;

  if (buffer_pos < 4) {
    return;
  }

  uint16_t len = (buffer[2] << 8) | buffer[3];
  if (len > (MAX_TRANS_UNIT + 1)) {
    BRIDGE_DEBUG_PRINTLN("BLE RX invalid length %d, resetting\n", len);
    buffer_pos = 0;
    return;
  }

  if (buffer_pos != len + BLE_BRIDGE_OVERHEAD) {
    return;
  }

  xorCrypt(buffer + 4, len + BRIDGE_CHECKSUM_SIZE);

  uint16_t received_checksum = (buffer[4 + len] << 8) | buffer[5 + len];
  if (validateChecksum(buffer + 4, len, received_checksum)) {
    mesh::Packet *pkt = _mgr->allocNew();
    if (pkt) {
      if (pkt->readFrom(buffer + 4, len)) {
        _rx_frames++;
        BRIDGE_DEBUG_PRINTLN("BLE RX, len=%d crc=0x%04x\n", len, received_checksum);
        onPacketReceived(pkt);
      } else {
        _rx_parse_fail++;
        BRIDGE_DEBUG_PRINTLN("BLE RX failed to parse packet\n");
        _mgr->free(pkt);
      }
    } else {
      BRIDGE_DEBUG_PRINTLN("BLE RX failed to allocate packet\n");
    }
  } else {
    _rx_bad_checksum++;
    BRIDGE_DEBUG_PRINTLN("BLE RX checksum mismatch, rcv=0x%04x\n", received_checksum);
  }

  buffer_pos = 0;
}

void BLEBridge::sendPacket(mesh::Packet *packet) {
  if (!_initialized || !packet) {
    return;
  }

  const bool has_link = _peripheral_connected || (_central_connected && _central_discovered);
  if (!has_link) {
    _tx_no_link++;
    return;
  }

  if (_seen_packets.wasSeen(packet)) {
    _tx_seen_drop++;
    return;
  }

  uint8_t buffer[MAX_BLE_BRIDGE_PACKET_SIZE];
  uint16_t len = packet->writeTo(buffer + 4);
  if (len > (MAX_TRANS_UNIT + 1)) {
    BRIDGE_DEBUG_PRINTLN("BLE TX packet too large (payload=%d, max=%d)\n", len, MAX_TRANS_UNIT + 1);
    return;
  }

  buffer[0] = (BRIDGE_PACKET_MAGIC >> 8) & 0xFF;
  buffer[1] = BRIDGE_PACKET_MAGIC & 0xFF;
  buffer[2] = (len >> 8) & 0xFF;
  buffer[3] = len & 0xFF;

  uint16_t checksum = fletcher16(buffer + 4, len);
  buffer[4 + len] = (checksum >> 8) & 0xFF;
  buffer[5 + len] = checksum & 0xFF;

  xorCrypt(buffer + 4, len + BRIDGE_CHECKSUM_SIZE);

  const size_t frame_len = len + BLE_BRIDGE_OVERHEAD;
#if defined(NRF52_PLATFORM)
  sendFrame(_peripheral_uart, _peripheral_conn_handle, buffer, frame_len);
  sendFrame(_central_uart, buffer, frame_len);
#elif defined(ESP32)
  sendServerFrame(buffer, frame_len);
  sendClientFrame(buffer, frame_len);
#endif

  _tx_frames++;
  BRIDGE_DEBUG_PRINTLN("BLE TX, len=%d crc=0x%04x\n", len, checksum);
}

void BLEBridge::onPacketReceived(mesh::Packet *packet) {
  handleReceivedPacket(packet);
}

void BLEBridge::getStatusStr(char *reply) const {
  sprintf(reply, "> BLE: %s | central: %s | peripheral: %s | tx=%lu rx=%lu badcrc=%lu parse=%lu seen=%lu nolink=%lu",
          _initialized ? "running" : "stopped",
          _central_connected && _central_discovered ? "connected" : "disconnected",
          _peripheral_connected ? "connected" : "disconnected",
          (unsigned long)_tx_frames,
          (unsigned long)_rx_frames,
          (unsigned long)_rx_bad_checksum,
          (unsigned long)_rx_parse_fail,
          (unsigned long)_tx_seen_drop,
          (unsigned long)_tx_no_link);
}

#endif
