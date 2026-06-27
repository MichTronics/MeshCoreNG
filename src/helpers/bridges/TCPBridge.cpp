#include "TCPBridge.h"

#ifdef WITH_TCP_BRIDGE

#include "helpers/FloodLimits.h"
#include "Utils.h"
#include <WiFi.h>
#include <WiFiClient.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "unknown"
#endif

static WiFiClient _client;

namespace {

constexpr uint32_t kNtpSyncIntervalMs = 3600UL * 1000UL;
constexpr uint32_t kNtpRetryIntervalMs = 60UL * 1000UL;

uint32_t fnv1a32(const uint8_t *data, size_t len) {
  uint32_t hash = 2166136261UL;
  for (size_t i = 0; i < len; i++) {
    hash ^= data[i];
    hash *= 16777619UL;
  }
  return hash;
}

uint8_t hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return 0xFF;
}

bool parseHex32(const char *hex, uint32_t *value) {
  if (!hex || !value) return false;
  uint32_t parsed = 0;
  for (uint8_t i = 0; i < 8; i++) {
    uint8_t nibble = hexNibble(hex[i]);
    if (nibble > 0x0F) return false;
    parsed = (parsed << 4) | nibble;
  }
  if (hex[8] != 0) return false;
  *value = parsed;
  return true;
}

bool parseHex8(const char *hex, uint8_t *value) {
  if (!hex || !value) return false;
  uint8_t hi = hexNibble(hex[0]);
  uint8_t lo = hexNibble(hex[1]);
  if (hi > 0x0F || lo > 0x0F || hex[2] != 0) return false;
  *value = (hi << 4) | lo;
  return true;
}

uint32_t parseDurationSecs(const char *value, uint32_t fallback_secs) {
  if (!value || value[0] == 0) return fallback_secs;
  char *end = nullptr;
  unsigned long amount = strtoul(value, &end, 10);
  if (amount == 0) return fallback_secs;
  uint32_t mult = 1;
  if (end && *end) {
    if (end[1] != 0) return fallback_secs;
    if (*end == 'm' || *end == 'M') mult = 60;
    else if (*end == 'h' || *end == 'H') mult = 3600;
    else if (*end == 'd' || *end == 'D') mult = 86400;
    else if (*end == 's' || *end == 'S') mult = 1;
    else return fallback_secs;
  }
  unsigned long seconds = amount * mult;
  if (seconds > 86400UL * 30UL) seconds = 86400UL * 30UL;
  return (uint32_t)seconds;
}

}  // namespace

TCPBridge::TCPBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc)
    : BridgeBase(prefs, mgr, rtc), 
      _transport_flood_limiter(20, 120),   // default: 20 transport packets per 2 min
      _control_flood_limiter(20, 120) {}   // default: 20 control packets per 2 min

void TCPBridge::setNodeId(const uint8_t *node_id, size_t len) {
  if (node_id == nullptr || len < sizeof(_node_id)) {
    _has_node_id = false;
    memset(_node_id, 0, sizeof(_node_id));
    return;
  }
  memcpy(_node_id, node_id, sizeof(_node_id));
  _has_node_id = true;
}

void TCPBridge::begin() {
  BRIDGE_DEBUG_PRINTLN("TCP bridge: starting...\n");
  _state = State::IDLE;
  _rx_buffer_pos = 0;
  _reconnect_interval_ms = RECONNECT_INTERVAL_MS;
  _last_reconnect_ms = millis() - RECONNECT_INTERVAL_MS;  // connect on first loop()
  _last_heartbeat_ms = 0;
  _transport_dropped_count = 0;
  _control_dropped_count = 0;
  resetGuardStats();
  _bridge_id = 0;
  _ntp_synced = false;
  _last_ntp_sync_ms = 0;
  
  // Configure selective rate limiters from preferences
  if (_prefs->tcp_flood_limit_enable) {
    _transport_flood_limiter.setLimits(_prefs->tcp_flood_transport_max, _prefs->tcp_flood_transport_window);
    _control_flood_limiter.setLimits(_prefs->tcp_flood_control_max, _prefs->tcp_flood_control_window);
  }
  
  BRIDGE_DEBUG_PRINTLN("TCP bridge: active bridge id=0x%08lx%s\n",
                       (unsigned long)getBridgeId(),
                       _prefs->bridge_id[0] ? " (configured)" : " (derived)");
  _initialized = true;
}

void TCPBridge::end() {
  BRIDGE_DEBUG_PRINTLN("TCP bridge: stopping...\n");
  _client.stop();
  WiFi.disconnect(true);
  _state = State::IDLE;
  _initialized = false;
}

void TCPBridge::loop() {
  if (!_initialized) return;

  uint32_t now = millis();
  refreshNTP(now);

  switch (_state) {

    case State::IDLE:
      if (now - _last_reconnect_ms < _reconnect_interval_ms) return;
      _last_reconnect_ms = now;
      _reconnect_interval_ms = RECONNECT_INTERVAL_MS;
      _rx_buffer_pos = 0;

      if (_prefs->wifi_ssid[0] == '\0') return;

      if (WiFi.status() == WL_CONNECTED) {
        syncTimeWithNTP(false);
        _state = State::SERVER_WAIT;
      } else {
        WiFi.persistent(false);
        WiFi.mode(WIFI_STA);
#if defined(WITH_BLE_BRIDGE)
        WiFi.setSleep(true);
#else
        WiFi.setSleep(false);
#endif
        WiFi.begin(_prefs->wifi_ssid, _prefs->wifi_password);
        _wifi_start_ms = now;
        _state = State::WIFI_WAIT;
        BRIDGE_DEBUG_PRINTLN("TCP bridge: connecting to WiFi '%s'...\n", _prefs->wifi_ssid);
      }
      break;

    case State::WIFI_WAIT:
      if (WiFi.status() == WL_CONNECTED) {
        BRIDGE_DEBUG_PRINTLN("TCP bridge: WiFi connected, IP=%s\n",
                             WiFi.localIP().toString().c_str());        
        syncTimeWithNTP(false);
        _state = State::SERVER_WAIT;
      } else if (now - _wifi_start_ms >= WIFI_CONNECT_TIMEOUT_MS) {
        BRIDGE_DEBUG_PRINTLN("TCP bridge: WiFi connect timeout\n");
        WiFi.disconnect(false);
        _state = State::IDLE;
        _last_reconnect_ms = now;
        _reconnect_interval_ms = RECONNECT_INTERVAL_MS;
      }
      break;

    case State::SERVER_WAIT:
      if (_prefs->bridge_server[0] == '\0') {
        _state = State::IDLE;
        return;
      }
      BRIDGE_DEBUG_PRINTLN("TCP bridge: connecting to %s:%d...\n",
                           _prefs->bridge_server, _prefs->bridge_port);
      if (_client.connect(_prefs->bridge_server, _prefs->bridge_port,
                          SERVER_CONNECT_TIMEOUT_MS)) {
        BRIDGE_DEBUG_PRINTLN("TCP bridge: connected to server\n");
        _last_heartbeat_ms = 0;
        _state = State::RUNNING;
        _just_connected = true;
        sendAuth();
        sendNodeInfo();
        sendCaps();
      } else {
        BRIDGE_DEBUG_PRINTLN("TCP bridge: server connect failed\n");
        _state = State::IDLE;
        _last_reconnect_ms = now;
        _reconnect_interval_ms = SERVER_RECONNECT_INTERVAL_MS;
      }
      break;

    case State::RUNNING:
      if (!_client.connected()) {
        BRIDGE_DEBUG_PRINTLN("TCP bridge: connection lost\n");
        _client.stop();
        _state = State::IDLE;
        _last_reconnect_ms = now - RECONNECT_INTERVAL_MS;  // retry soon
        return;
      }

      if (_last_heartbeat_ms == 0 || now - _last_heartbeat_ms >= HEARTBEAT_INTERVAL_MS) {
        sendHeartbeat();
        _last_heartbeat_ms = now;
      }      
      refreshNTP(now);
      readIncoming();
      break;
  }
}

void TCPBridge::readIncoming() {
  while (_client.available()) {
    uint8_t b = (uint8_t)_client.read();

    if (_rx_buffer_pos < 2) {
      if ((_rx_buffer_pos == 0 && b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) ||
          (_rx_buffer_pos == 1 && b == (BRIDGE_PACKET_MAGIC & 0xFF))) {
        _rx_buffer[_rx_buffer_pos++] = b;
      } else {
        _rx_buffer_pos = 0;
        if (b == ((BRIDGE_PACKET_MAGIC >> 8) & 0xFF)) {
          _rx_buffer[_rx_buffer_pos++] = b;
        }
      }
    } else {
      _rx_buffer[_rx_buffer_pos++] = b;

      if (_rx_buffer_pos >= 4) {
        uint16_t len = (_rx_buffer[2] << 8) | _rx_buffer[3];

        if (len > MAX_TCP_PAYLOAD_SIZE) {
          BRIDGE_DEBUG_PRINTLN("TCP bridge: RX invalid length %d, resetting\n", len);
          _rx_buffer_pos = 0;
          continue;
        }

        if (_rx_buffer_pos == len + TCP_OVERHEAD) {
          uint16_t received_checksum =
              (_rx_buffer[4 + len] << 8) | _rx_buffer[5 + len];

          if (validateChecksum(_rx_buffer + 4, len, received_checksum)) {
            if (isControlPayload(_rx_buffer + 4, len)) {
              BRIDGE_DEBUG_PRINTLN("TCP bridge: RX control len=%d crc=0x%04x\n",
                                   len, received_checksum);
              handleControlPayload(_rx_buffer + 4, len);
              _rx_buffer_pos = 0;
              continue;
            }
            
            // Selective TCP rate limiting by packet category
            if (_prefs->tcp_flood_limit_enable) {
              uint32_t now_secs = _rtc->getCurrentTime();
              bool drop_packet = false;
              
              if (isTransportPacket(_rx_buffer + 4, len)) {
                // Transport/message packets: strict TCP-side rate limit
                if (!_transport_flood_limiter.allow(now_secs)) {
                  _transport_dropped_count++;
                  drop_packet = true;
                  BRIDGE_DEBUG_PRINTLN("TCP bridge: transport flood limit exceeded, dropping (dropped=%lu)\n",
                                       (unsigned long)_transport_dropped_count);
                }
              } else if (isControlPacket(_rx_buffer + 4, len)) {
                // Control/admin packets: higher TCP-side limit or bypass
                if (_prefs->tcp_flood_control_max > 0) {  // 0 = bypass
                  if (!_control_flood_limiter.allow(now_secs)) {
                    _control_dropped_count++;
                    drop_packet = true;
                    BRIDGE_DEBUG_PRINTLN("TCP bridge: control flood limit exceeded, dropping (dropped=%lu)\n",
                                         (unsigned long)_control_dropped_count);
                  }
                }
                // else: control max = 0, bypass TCP rate limiting
              }
              // Other packet types not explicitly classified default to no limit
              
              if (drop_packet) {
                _rx_buffer_pos = 0;
                continue;
              }
            }
            
            BRIDGE_DEBUG_PRINTLN("TCP bridge: RX len=%d crc=0x%04x\n", len,
                                 received_checksum);
            if (!canInjectFromTcp(len)) {
              _rf_inject_dropped_count++;
              _skipped_rf_inject_budget_count++;
              BRIDGE_DEBUG_PRINTLN("TCP bridge: RF inject budget drop raw len=%d dropped=%lu\n",
                                   len, (unsigned long)_rf_inject_dropped_count);
              _rx_buffer_pos = 0;
              continue;
            }
            mesh::Packet *pkt = _mgr->allocNew();
            if (pkt) {
              if (pkt->readFrom(_rx_buffer + 4, len)) {
                if (isBlockedForBridgeRf(pkt)) {
                  _skipped_node_block_count++;
                  BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped node/path block TCP->RF\n");
                  _mgr->free(pkt);
                  _rx_buffer_pos = 0;
                  continue;
                }
                _accepted_tcp_packet_count++;
                recordInjectFromTcp(len);
                _injected_tcp_to_rf_count++;
                onPacketReceived(pkt);
              } else {
                BRIDGE_DEBUG_PRINTLN("TCP bridge: RX failed to parse packet\n");
                _mgr->free(pkt);
              }
            } else {
              BRIDGE_DEBUG_PRINTLN("TCP bridge: RX failed to allocate packet\n");
            }
          } else {
            BRIDGE_DEBUG_PRINTLN("TCP bridge: RX checksum mismatch, rcv=0x%04x\n",
                                 received_checksum);
          }
          _rx_buffer_pos = 0;
        }
      }
    }
  }
}

bool TCPBridge::sendPayloadFrame(const uint8_t *payload, uint16_t len) {
  if (!_initialized || !_client.connected()) return false;
  if (len > MAX_TCP_PAYLOAD_SIZE) return false;

  uint8_t buffer[MAX_TCP_PACKET_SIZE];
  buffer[0] = (BRIDGE_PACKET_MAGIC >> 8) & 0xFF;
  buffer[1] = BRIDGE_PACKET_MAGIC & 0xFF;
  buffer[2] = (len >> 8) & 0xFF;
  buffer[3] = len & 0xFF;
  memcpy(buffer + 4, payload, len);

  uint16_t checksum = fletcher16(buffer + 4, len);
  buffer[4 + len] = (checksum >> 8) & 0xFF;
  buffer[5 + len] = checksum & 0xFF;

  return _client.write(buffer, len + TCP_OVERHEAD) == len + TCP_OVERHEAD;
}

void TCPBridge::sendAuth() {
  if (_prefs->bridge_password[0] == '\0') return;

  uint8_t payload[6 + sizeof(_prefs->bridge_password)];
  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_AUTH;

  size_t password_len = 0;
  while (password_len < sizeof(_prefs->bridge_password) &&
         _prefs->bridge_password[password_len] != '\0') {
    password_len++;
  }
  payload[5] = (uint8_t)password_len;
  memcpy(payload + 6, _prefs->bridge_password, password_len);

  if (sendPayloadFrame(payload, 6 + password_len)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: sent auth\n");
  }
}

void TCPBridge::sendNodeInfo() {
  uint8_t payload[8 + sizeof(_prefs->node_name) + 32 + sizeof(_node_id)];
  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_NODE_INFO;

  size_t name_len = 0;
  while (name_len < sizeof(_prefs->node_name) && _prefs->node_name[name_len] != '\0') {
    name_len++;
  }
  payload[5] = (uint8_t)name_len;
  memcpy(payload + 6, _prefs->node_name, name_len);

  const char *version = FIRMWARE_VERSION;
  size_t version_len = 0;
  while (version_len < 32 && version[version_len] != '\0') {
    version_len++;
  }
  payload[6 + name_len] = (uint8_t)version_len;
  memcpy(payload + 7 + name_len, version, version_len);
  size_t pos = 7 + name_len + version_len;
  payload[pos++] = _has_node_id ? sizeof(_node_id) : 0;
  if (_has_node_id) {
    memcpy(payload + pos, _node_id, sizeof(_node_id));
    pos += sizeof(_node_id);
  }

  if (sendPayloadFrame(payload, pos)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: sent node name '%s' version '%s' node_id=%02x%02x%02x%02x...\n",
                         _prefs->node_name, version,
                         _node_id[0], _node_id[1], _node_id[2], _node_id[3]);
  }
}

void TCPBridge::sendHeartbeat() {
  uint8_t payload[45];
  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_HEARTBEAT;
  uint32_t uptime = millis();
  payload[5] = (uptime >> 24) & 0xFF;
  payload[6] = (uptime >> 16) & 0xFF;
  payload[7] = (uptime >> 8) & 0xFF;
  payload[8] = uptime & 0xFF;
  payload[9] = 'R';
  payload[10] = 'F';
  payload[11] = 2;
  payload[12] = (_rf_tx_used_ms >> 24) & 0xFF;
  payload[13] = (_rf_tx_used_ms >> 16) & 0xFF;
  payload[14] = (_rf_tx_used_ms >> 8) & 0xFF;
  payload[15] = _rf_tx_used_ms & 0xFF;
  payload[16] = (_rf_tx_max_ms >> 24) & 0xFF;
  payload[17] = (_rf_tx_max_ms >> 16) & 0xFF;
  payload[18] = (_rf_tx_max_ms >> 8) & 0xFF;
  payload[19] = _rf_tx_max_ms & 0xFF;
  payload[20] = (_rf_tx_window_ms >> 24) & 0xFF;
  payload[21] = (_rf_tx_window_ms >> 16) & 0xFF;
  payload[22] = (_rf_tx_window_ms >> 8) & 0xFF;
  payload[23] = _rf_tx_window_ms & 0xFF;
  payload[24] = (_rf_duty_limit_centi_pct >> 8) & 0xFF;
  payload[25] = _rf_duty_limit_centi_pct & 0xFF;
  payload[26] = (_rf_tx_used_centi_pct >> 8) & 0xFF;
  payload[27] = _rf_tx_used_centi_pct & 0xFF;
  payload[28] = (_rf_tx_total_ms >> 24) & 0xFF;
  payload[29] = (_rf_tx_total_ms >> 16) & 0xFF;
  payload[30] = (_rf_tx_total_ms >> 8) & 0xFF;
  payload[31] = _rf_tx_total_ms & 0xFF;
  payload[32] = 'R';
  payload[33] = 'S';
  payload[34] = 1;
  payload[35] = (_radio_noise_floor >> 8) & 0xFF;
  payload[36] = _radio_noise_floor & 0xFF;
  payload[37] = (_radio_last_rssi >> 8) & 0xFF;
  payload[38] = _radio_last_rssi & 0xFF;
  payload[39] = (uint8_t)_radio_last_snr_quarter_db;
  payload[40] = 'N';
  payload[41] = 'B';
  payload[42] = 1;
  payload[43] = (_neighbor_count >> 8) & 0xFF;
  payload[44] = _neighbor_count & 0xFF;

  if (sendPayloadFrame(payload, sizeof(payload))) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: heartbeat\n");
  }
}

void TCPBridge::sendCaps() {
  uint8_t group_len = 0;
  while (group_len < sizeof(_prefs->bridge_group) && _prefs->bridge_group[group_len] != '\0') {
    group_len++;
  }
  if (group_len > 15) group_len = 15;

  uint8_t payload[48];
  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_CAPS;
  payload[5] = 3;    // caps version
  payload[6] = 0x0F; // bridge packet envelope, group, RF budget metadata, bridge id
  payload[7] = BRIDGE_PROTO_VERSION;
  payload[8] = group_len;
  memcpy(payload + 9, _prefs->bridge_group, group_len);
  uint8_t pos = 9 + group_len;
  payload[pos++] = _prefs->bridge_rf_inject_budget_enabled ? 1 : 0;
  payload[pos++] = (_prefs->bridge_rf_inject_max_per_min >> 8) & 0xFF;
  payload[pos++] = _prefs->bridge_rf_inject_max_per_min & 0xFF;
  payload[pos++] = (_prefs->bridge_rf_inject_max_airtime_ms_hour >> 24) & 0xFF;
  payload[pos++] = (_prefs->bridge_rf_inject_max_airtime_ms_hour >> 16) & 0xFF;
  payload[pos++] = (_prefs->bridge_rf_inject_max_airtime_ms_hour >> 8) & 0xFF;
  payload[pos++] = _prefs->bridge_rf_inject_max_airtime_ms_hour & 0xFF;
  payload[pos++] = (_prefs->bridge_rf_inject_block_duty_centi_pct >> 8) & 0xFF;
  payload[pos++] = _prefs->bridge_rf_inject_block_duty_centi_pct & 0xFF;
  uint32_t id = getBridgeId();
  payload[pos++] = (id >> 24) & 0xFF;
  payload[pos++] = (id >> 16) & 0xFF;
  payload[pos++] = (id >> 8) & 0xFF;
  payload[pos++] = id & 0xFF;

  if (sendPayloadFrame(payload, pos)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: sent caps proto=%d group=%s budget=%d bridge_id=0x%08lx\n",
                         BRIDGE_PROTO_VERSION, _prefs->bridge_group,
                         _prefs->bridge_rf_inject_budget_enabled,
                         (unsigned long)id);
  }
}

void TCPBridge::handleControlPayload(const uint8_t *payload, uint16_t len) {
  if (len < 5) return;

  if (payload[4] == CONTROL_TYPE_BRIDGE_PACKET) {
    handleBridgePacketPayload(payload, len);
  } else if (payload[4] == CONTROL_TYPE_COMMAND) {
    handleCommandPayload(payload, len);
  }
}

void TCPBridge::handleBridgePacketPayload(const uint8_t *payload, uint16_t len) {
  if (len < BRIDGE_V2_OVERHEAD) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet too short len=%d\n", len);
    return;
  }
  if (payload[5] != BRIDGE_PACKET_VERSION) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet version %d unsupported\n", payload[5]);
    return;
  }

  uint8_t ttl = payload[6];
  uint32_t origin_id = ((uint32_t)payload[7] << 24) |
                       ((uint32_t)payload[8] << 16) |
                       ((uint32_t)payload[9] << 8) |
                       payload[10];
  uint8_t flags = payload[11];
  uint16_t packet_len = ((uint16_t)payload[12] << 8) | payload[13];

  if (ttl == 0) {
    _skipped_ttl_expired_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet TTL expired\n");
    return;
  }
  if (origin_id != 0 && origin_id == getBridgeId()) {
    _skipped_own_origin_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet from self origin=0x%08lx\n", (unsigned long)origin_id);
    return;
  }
  if (packet_len == 0 || len < (uint16_t)(BRIDGE_V2_OVERHEAD + packet_len)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet invalid mesh length %d\n", packet_len);
    return;
  }
  if (!canInjectFromTcp(packet_len)) {
    _rf_inject_dropped_count++;
    _skipped_rf_inject_budget_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RF inject budget drop len=%d dropped=%lu\n",
                         packet_len, (unsigned long)_rf_inject_dropped_count);
    return;
  }

  mesh::Packet *pkt = _mgr->allocNew();
  if (!pkt) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet failed to allocate packet\n");
    return;
  }
  if (pkt->readFrom(payload + BRIDGE_V2_OVERHEAD, packet_len)) {
    if (isBlockedForBridgeRf(pkt)) {
      _skipped_node_block_count++;
      BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped node/path block TCP->RF\n");
      _mgr->free(pkt);
      return;
    }
    _accepted_tcp_packet_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet ttl=%d origin=0x%08lx flags=0x%02x len=%d\n",
                         ttl, (unsigned long)origin_id, flags, packet_len);
    recordInjectFromTcp(packet_len);
    _injected_tcp_to_rf_count++;
    onPacketReceived(pkt);
  } else {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: RX bridge packet failed to parse mesh packet\n");
    _mgr->free(pkt);
  }
}

void TCPBridge::handleCommandPayload(const uint8_t *payload, uint16_t len) {
  if (len < 9) return;

  uint32_t request_id = ((uint32_t)payload[5] << 24) |
                        ((uint32_t)payload[6] << 16) |
                        ((uint32_t)payload[7] << 8) |
                        payload[8];
  if (!_command_handler) {
    sendCommandReply(request_id, "Err - remote command handler unavailable");
    return;
  }
  if (len < 11) {
    sendCommandReply(request_id, "Err - invalid remote command");
    return;
  }
  uint8_t password_len = payload[9];
  uint8_t command_len = payload[10];
  if (command_len == 0 || len < (uint16_t)(11 + password_len + command_len)) {
    sendCommandReply(request_id, "Err - invalid remote command");
    return;
  }

  char password[32];
  size_t password_copy_len = password_len;
  if (password_copy_len >= sizeof(password)) password_copy_len = sizeof(password) - 1;
  memcpy(password, payload + 11, password_copy_len);
  password[password_copy_len] = 0;

  char command[96];
  size_t copy_len = command_len;
  if (copy_len >= sizeof(command)) copy_len = sizeof(command) - 1;
  memcpy(command, payload + 11 + password_len, copy_len);
  command[copy_len] = 0;

  char reply[192];
  reply[0] = 0;
  bool handled_node_block = handleNodeBlockCommand(command, reply, sizeof(reply));
  bool handled_path_block = false;
  if (!handled_node_block) {
    handled_path_block = handlePathBlockCommand(command, reply, sizeof(reply));
  }
  if (handled_node_block || handled_path_block) {
    char mesh_reply[192];
    mesh_reply[0] = 0;
    _command_handler->handleTcpBridgeCommand(password, command, mesh_reply, sizeof(mesh_reply));
    if (mesh_reply[0] != 0) {
      strncpy(reply, mesh_reply, sizeof(reply));
      reply[sizeof(reply) - 1] = 0;
    }
  } else {
    _command_handler->handleTcpBridgeCommand(password, command, reply, sizeof(reply));
  }
  if (reply[0] == 0) {
    strncpy(reply, "OK", sizeof(reply));
    reply[sizeof(reply) - 1] = 0;
  }
  sendCommandReply(request_id, reply);
}

void TCPBridge::sendCommandReply(uint32_t request_id, const char *reply) {
  uint8_t payload[MAX_TRANS_UNIT + 1];
  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_COMMAND_REPLY;
  payload[5] = (request_id >> 24) & 0xFF;
  payload[6] = (request_id >> 16) & 0xFF;
  payload[7] = (request_id >> 8) & 0xFF;
  payload[8] = request_id & 0xFF;

  size_t reply_len = strlen(reply);
  if (reply_len > (MAX_TRANS_UNIT + 1) - 10) {
    reply_len = (MAX_TRANS_UNIT + 1) - 10;
  }
  payload[9] = (uint8_t)reply_len;
  memcpy(payload + 10, reply, reply_len);
  sendPayloadFrame(payload, 10 + reply_len);
}

bool TCPBridge::isControlPayload(const uint8_t *payload, uint16_t len) const {
  return len >= 5 && payload[0] == 'M' && payload[1] == 'C' &&
         payload[2] == 'N' && payload[3] == 'G';
}

bool TCPBridge::isTransportPacket(const uint8_t *payload, uint16_t len) const {
  if (len < 1) return false;
  
  uint8_t header = payload[0];
  uint8_t route_type = header & PH_ROUTE_MASK;
  uint8_t payload_type = (header >> PH_TYPE_SHIFT) & PH_TYPE_MASK;
  
  // Transport packets: transport routes OR message payload types
  bool is_transport_route = (route_type == ROUTE_TYPE_TRANSPORT_FLOOD || 
                             route_type == ROUTE_TYPE_TRANSPORT_DIRECT);
  bool is_message_type = (payload_type == PAYLOAD_TYPE_TXT_MSG ||
                          payload_type == PAYLOAD_TYPE_GRP_TXT ||
                          payload_type == PAYLOAD_TYPE_GRP_DATA ||
                          payload_type == PAYLOAD_TYPE_REQ ||
                          payload_type == PAYLOAD_TYPE_RESPONSE ||
                          payload_type == PAYLOAD_TYPE_ANON_REQ ||
                          payload_type == PAYLOAD_TYPE_MULTIPART);
  
  return is_transport_route || is_message_type;
}

bool TCPBridge::isControlPacket(const uint8_t *payload, uint16_t len) const {
  if (len < 1) return false;
  
  uint8_t header = payload[0];
  uint8_t payload_type = (header >> PH_TYPE_SHIFT) & PH_TYPE_MASK;
  
  // Control/admin packets: discovery, adverts, ACKs, traces, atlas
  return (payload_type == PAYLOAD_TYPE_CONTROL ||
          payload_type == PAYLOAD_TYPE_ADVERT ||
          payload_type == PAYLOAD_TYPE_ACK ||
          payload_type == PAYLOAD_TYPE_TRACE ||
          payload_type == PAYLOAD_TYPE_PATH ||
          payload_type == PAYLOAD_TYPE_ATLAS);
}

void TCPBridge::sendPacket(mesh::Packet *packet) {
  if (!_initialized || !packet) return;
  if (_state != State::RUNNING) return;
  if (!shouldExportPacket(packet)) return;

  if (!_seen_packets.hasSeen(packet)) {
    if (sendBridgePacket(packet)) {
      _exported_rf_to_tcp_count++;
    }
  } else {
    _skipped_duplicate_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped duplicate export\n");
  }
}

bool TCPBridge::sendBridgePacket(mesh::Packet *packet) {
  mesh::Packet export_packet = *packet;
  appendSelfToTcpExportPath(&export_packet);

  uint8_t payload[MAX_TCP_PAYLOAD_SIZE];
  uint16_t mesh_len = export_packet.writeTo(payload + BRIDGE_V2_OVERHEAD);

  if (mesh_len > (MAX_TRANS_UNIT + 1)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: TX packet too large (len=%d)\n", mesh_len);
    return false;
  }

  uint8_t ttl = _prefs->bridge_tcp_ttl;
  if (ttl == 0) ttl = 1;

  payload[0] = 'M';
  payload[1] = 'C';
  payload[2] = 'N';
  payload[3] = 'G';
  payload[4] = CONTROL_TYPE_BRIDGE_PACKET;
  payload[5] = BRIDGE_PACKET_VERSION;
  payload[6] = ttl;
  uint32_t id = getBridgeId();
  payload[7] = (id >> 24) & 0xFF;
  payload[8] = (id >> 16) & 0xFF;
  payload[9] = (id >> 8) & 0xFF;
  payload[10] = id & 0xFF;
  payload[11] = export_packet.wasReceivedFromBridge() ? 0 : BRIDGE_PACKET_FLAG_RF_RX;
  payload[12] = (mesh_len >> 8) & 0xFF;
  payload[13] = mesh_len & 0xFF;

  uint16_t len = mesh_len + BRIDGE_V2_OVERHEAD;
  if (sendPayloadFrame(payload, len)) {
    BRIDGE_DEBUG_PRINTLN("TCP bridge: TX bridge packet ttl=%d origin=0x%08lx len=%d\n",
                         ttl, (unsigned long)id, mesh_len);
    return true;
  }
  return false;
}

bool TCPBridge::appendSelfToTcpExportPath(mesh::Packet *packet) const {
  if (!packet || !packet->isRouteFlood() || !_has_self_hash) return false;
  if (pathContainsSelf(packet)) return false;

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > sizeof(_self_hash)) return false;

  uint8_t hash_count = packet->getPathHashCount();
  if ((hash_count + 1) * hash_size > MAX_PATH_SIZE) return false;

  memcpy(&packet->path[hash_count * hash_size], _self_hash, hash_size);
  packet->setPathHashCount(hash_count + 1);
  return true;
}

bool TCPBridge::pathContainsSelf(const mesh::Packet *packet) const {
  if (!packet || !_has_self_hash) return false;

  uint8_t hash_size = packet->getPathHashSize();
  if (hash_size == 0 || hash_size > sizeof(_self_hash)) return false;

  uint8_t hash_count = packet->getPathHashCount();
  for (uint8_t i = 0; i < hash_count; i++) {
    if (memcmp(&packet->path[i * hash_size], _self_hash, hash_size) == 0) {
      return true;
    }
  }
  return false;
}

bool TCPBridge::shouldExportPacket(const mesh::Packet *packet) {
  if (!packet) return false;
  if (packet->wasReceivedFromBridge()) {
    _skipped_bridge_loop_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped bridge-origin export\n");
    return false;
  }

  uint8_t short_id = 0;
  if (sourceShortId(packet, &short_id) && isNodeBlocked(short_id)) {
    _skipped_node_block_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped node.block RF->TCP id=%02x\n", short_id);
    return false;
  }

  if (BridgeBase::exceedsFloodMaxPath(_prefs, packet)) {
    _skipped_max_hops_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped export flood hop limit hops=%u max=%u\n",
                         (uint32_t)packet->getPathHashCount(),
                         (uint32_t)mesh::effectiveFloodMaxHopLimit(*_prefs, packet));
    return false;
  }
  if (_prefs->bridge_export_max_hops > 0 &&
      packet->getPathHashCount() > _prefs->bridge_export_max_hops) {
    _skipped_max_hops_count++;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: skipped export max hops hops=%u max=%u\n",
                         (uint32_t)packet->getPathHashCount(),
                         (uint32_t)_prefs->bridge_export_max_hops);
    return false;
  }

  switch (_prefs->bridge_export_filter) {
    case BRIDGE_EXPORT_FLOOD:
      if (packet->isRouteFlood()) return true;
      _skipped_export_disabled_count++;
      return false;
    case BRIDGE_EXPORT_CHANNELS:
      if (isChannelPacket(packet)) return true;
      _skipped_export_disabled_count++;
      return false;
    case BRIDGE_EXPORT_MESSAGES:
      if (isMessagePacket(packet)) return true;
      _skipped_export_disabled_count++;
      return false;
    case BRIDGE_EXPORT_ALL:
    default:
      return true;
  }
}

bool TCPBridge::sourceShortId(const mesh::Packet *packet, uint8_t *id) const {
  if (!packet || !id || packet->payload_len == 0) return false;
  uint8_t type = packet->getPayloadType();
  if (type == PAYLOAD_TYPE_ADVERT && packet->payload_len >= 1) {
    *id = packet->payload[0];
    return true;
  }
  if (type == PAYLOAD_TYPE_LOCATION && packet->payload_len >= 10 &&
      packet->payload[0] == 'M' && packet->payload[1] == 'C' &&
      packet->payload[2] == 'L' && packet->payload[3] == '1') {
    *id = packet->payload[6];
    return true;
  }
  if ((type == PAYLOAD_TYPE_PATH || type == PAYLOAD_TYPE_REQ ||
       type == PAYLOAD_TYPE_RESPONSE || type == PAYLOAD_TYPE_TXT_MSG) &&
      packet->payload_len >= 2) {
    *id = packet->payload[1];
    return true;
  }
  *id = packet->payload[0];
  return true;
}

bool TCPBridge::parsePathBlockSpec(const char *spec, PathBlockEntry *entry) const {
  if (!spec || !entry) return false;
  memset(entry, 0, sizeof(*entry));
  uint8_t hash_size = 0;
  uint8_t hop_count = 0;
  const char *pos = spec;
  while (*pos != 0) {
    if (hop_count >= PATH_BLOCK_MAX_HOPS) return false;
    const char *slash = strchr(pos, '/');
    size_t hex_len = slash ? (size_t)(slash - pos) : strlen(pos);
    if (hex_len != 2 && hex_len != 4 && hex_len != 6) return false;
    uint8_t this_hash_size = hex_len / 2;
    if (hash_size == 0) hash_size = this_hash_size;
    else if (this_hash_size != hash_size) return false;

    char hex[7];
    memcpy(hex, pos, hex_len);
    hex[hex_len] = 0;
    for (size_t i = 0; i < hex_len; i++) {
      if (hexNibble(hex[i]) > 0x0F) return false;
    }
    if (!mesh::Utils::fromHex(&entry->path[hop_count * PATH_BLOCK_MAX_HASH_SIZE], hash_size, hex)) return false;
    hop_count++;
    if (!slash) break;
    pos = slash + 1;
    if (*pos == 0) return false;
  }
  entry->hash_size = hash_size;
  entry->hop_count = hop_count;
  return hop_count > 0;
}

bool TCPBridge::pathBlockMatches(const mesh::Packet *packet, const PathBlockEntry &entry) const {
  if (!packet || entry.hop_count == 0) return false;
  if (!packet->isRouteFlood()) return false;
  if (packet->getPathHashSize() != entry.hash_size) return false;
  if (packet->getPathHashCount() < entry.hop_count) return false;
  uint8_t packet_hops = packet->getPathHashCount();
  uint8_t hash_size = packet->getPathHashSize();
  for (uint8_t start = 0; start + entry.hop_count <= packet_hops; start++) {
    bool match = true;
    for (uint8_t hop = 0; hop < entry.hop_count; hop++) {
      const uint8_t *packet_hash = &packet->path[(start + hop) * hash_size];
      const uint8_t *block_hash = &entry.path[hop * PATH_BLOCK_MAX_HASH_SIZE];
      if (memcmp(packet_hash, block_hash, hash_size) != 0) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

void TCPBridge::prunePathBlocks() {
  uint32_t now = millis();
  for (uint8_t i = 0; i < PATH_BLOCK_MAX_ENTRIES; i++) {
    if (_path_blocks[i].hop_count > 0 && _path_blocks[i].until_ms != 0 &&
        (int32_t)(now - _path_blocks[i].until_ms) >= 0) {
      memset(&_path_blocks[i], 0, sizeof(_path_blocks[i]));
    }
  }
}

bool TCPBridge::isPathBlocked(const mesh::Packet *packet) {
  prunePathBlocks();
  for (uint8_t i = 0; i < PATH_BLOCK_MAX_ENTRIES; i++) {
    if (pathBlockMatches(packet, _path_blocks[i])) return true;
  }
  return false;
}

bool TCPBridge::addPathBlock(const PathBlockEntry &entry, uint32_t duration_secs) {
  prunePathBlocks();
  int8_t free_slot = -1;
  for (uint8_t i = 0; i < PATH_BLOCK_MAX_ENTRIES; i++) {
    if (_path_blocks[i].hop_count == 0 && free_slot < 0) free_slot = i;
    if (_path_blocks[i].hop_count == entry.hop_count &&
        _path_blocks[i].hash_size == entry.hash_size &&
        memcmp(_path_blocks[i].path, entry.path, entry.hop_count * PATH_BLOCK_MAX_HASH_SIZE) == 0) {
      free_slot = i;
      break;
    }
  }
  if (free_slot < 0) return false;
  _path_blocks[free_slot] = entry;
  _path_blocks[free_slot].until_ms = millis() + (duration_secs * 1000UL);
  return true;
}

bool TCPBridge::delPathBlock(const PathBlockEntry &entry) {
  bool removed = false;
  for (uint8_t i = 0; i < PATH_BLOCK_MAX_ENTRIES; i++) {
    if (_path_blocks[i].hop_count == entry.hop_count &&
        _path_blocks[i].hash_size == entry.hash_size &&
        memcmp(_path_blocks[i].path, entry.path, entry.hop_count * PATH_BLOCK_MAX_HASH_SIZE) == 0) {
      memset(&_path_blocks[i], 0, sizeof(_path_blocks[i]));
      removed = true;
    }
  }
  return removed;
}

void TCPBridge::clearPathBlocks() {
  memset(_path_blocks, 0, sizeof(_path_blocks));
}

void TCPBridge::formatPathBlocks(char *reply, size_t reply_size) {
  prunePathBlocks();
  size_t pos = 0;
  int written = snprintf(reply, reply_size, "> path.block");
  if (written < 0) return;
  pos = (size_t)written < reply_size ? (size_t)written : reply_size - 1;
  uint32_t now = millis();
  bool any = false;
  for (uint8_t i = 0; i < PATH_BLOCK_MAX_ENTRIES && pos < reply_size - 1; i++) {
    const PathBlockEntry &entry = _path_blocks[i];
    if (entry.hop_count == 0) continue;
    any = true;
    if (pos < reply_size - 1) reply[pos++] = ' ';
    for (uint8_t hop = 0; hop < entry.hop_count && pos < reply_size - 1; hop++) {
      if (hop > 0 && pos < reply_size - 1) reply[pos++] = '/';
      mesh::Utils::toHex(reply + pos, &entry.path[hop * PATH_BLOCK_MAX_HASH_SIZE], entry.hash_size);
      pos += entry.hash_size * 2;
    }
    uint32_t left = 0;
    if (entry.until_ms != 0 && (int32_t)(entry.until_ms - now) > 0) {
      left = (entry.until_ms - now + 999UL) / 1000UL;
    }
    written = snprintf(reply + pos, reply_size - pos, ":%lus", (unsigned long)left);
    if (written < 0) break;
    size_t available = reply_size - pos - 1;
    pos += ((size_t)written < available) ? (size_t)written : available;
  }
  if (!any) snprintf(reply, reply_size, "> path.block empty");
}

bool TCPBridge::handlePathBlockCommand(const char *command, char *reply, size_t reply_size) {
  if (strcmp(command, "get path.block") == 0 || strcmp(command, "path.block") == 0) {
    formatPathBlocks(reply, reply_size);
    return true;
  }
  if (strcmp(command, "clear path.block") == 0 || strcmp(command, "set path.block clear") == 0) {
    clearPathBlocks();
    snprintf(reply, reply_size, "OK");
    return true;
  }
  if (strncmp(command, "set path.block ", 15) != 0) return false;

  char tmp[64];
  strncpy(tmp, command + 15, sizeof(tmp));
  tmp[sizeof(tmp) - 1] = 0;
  const char *parts[4];
  int n = mesh::Utils::parseTextParts(tmp, parts, 4);
  if (n < 1) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  if (strcmp(parts[0], "clear") == 0) {
    clearPathBlocks();
    snprintf(reply, reply_size, "OK");
    return true;
  }
  if ((strcmp(parts[0], "add") != 0 && strcmp(parts[0], "del") != 0) || n < 2) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  PathBlockEntry entry;
  if (!parsePathBlockSpec(parts[1], &entry)) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  if (strcmp(parts[0], "del") == 0) {
    bool removed = delPathBlock(entry);
    snprintf(reply, reply_size, removed ? "OK" : "Error");
    return true;
  }
  uint32_t duration = parseDurationSecs(n >= 3 ? parts[2] : nullptr, 60UL * 60UL);
  if (!addPathBlock(entry, duration)) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  snprintf(reply, reply_size, "OK %lus", (unsigned long)duration);
  return true;
}

bool TCPBridge::isBlockedForBridgeRf(const mesh::Packet *packet) {
  if (BridgeBase::exceedsFloodMaxPath(_prefs, packet)) return true;
  return isNodeBlockedForPacket(packet) || isPathBlocked(packet);
}

void TCPBridge::pruneNodeBlocks() {
  uint32_t now = millis();
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES; i++) {
    if (_node_blocks[i].active && _node_blocks[i].until_ms != 0 &&
        (int32_t)(now - _node_blocks[i].until_ms) >= 0) {
      BRIDGE_DEBUG_PRINTLN("TCP bridge: node.block expired id=%02x\n", _node_blocks[i].id);
      _node_blocks[i].active = false;
    }
  }
}

bool TCPBridge::isNodeBlocked(uint8_t id) {
  pruneNodeBlocks();
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES; i++) {
    if (_node_blocks[i].active && _node_blocks[i].id == id) return true;
  }
  return false;
}

bool TCPBridge::isNodeBlockedForPacket(const mesh::Packet *packet) {
  uint8_t id = 0;
  return sourceShortId(packet, &id) && isNodeBlocked(id);
}

bool TCPBridge::addNodeBlock(uint8_t id, uint32_t duration_secs) {
  pruneNodeBlocks();
  uint32_t now = millis();
  uint32_t duration_ms = duration_secs > 0 ? duration_secs * 1000UL : 0;
  uint32_t until_ms = duration_ms ? now + duration_ms : 0;
  int8_t free_slot = -1;
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES; i++) {
    if (_node_blocks[i].active && _node_blocks[i].id == id) {
      _node_blocks[i].until_ms = until_ms;
      return true;
    }
    if (!_node_blocks[i].active && free_slot < 0) free_slot = i;
  }
  if (free_slot < 0) return false;
  _node_blocks[free_slot].id = id;
  _node_blocks[free_slot].until_ms = until_ms;
  _node_blocks[free_slot].active = true;
  return true;
}

bool TCPBridge::delNodeBlock(uint8_t id) {
  bool removed = false;
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES; i++) {
    if (_node_blocks[i].active && _node_blocks[i].id == id) {
      _node_blocks[i].active = false;
      removed = true;
    }
  }
  return removed;
}

void TCPBridge::clearNodeBlocks() {
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES; i++) {
    _node_blocks[i].active = false;
  }
}

void TCPBridge::formatNodeBlocks(char *reply, size_t reply_size) {
  pruneNodeBlocks();
  size_t pos = 0;
  int written = snprintf(reply, reply_size, "> node.block");
  if (written < 0) return;
  pos = (size_t)written < reply_size ? (size_t)written : reply_size - 1;
  uint32_t now = millis();
  bool any = false;
  for (uint8_t i = 0; i < NODE_BLOCK_MAX_ENTRIES && pos < reply_size - 1; i++) {
    if (!_node_blocks[i].active) continue;
    any = true;
    uint32_t left = 0;
    if (_node_blocks[i].until_ms != 0 && (int32_t)(_node_blocks[i].until_ms - now) > 0) {
      left = (_node_blocks[i].until_ms - now + 999UL) / 1000UL;
    }
    written = snprintf(reply + pos, reply_size - pos, " %02x:%lus",
                       _node_blocks[i].id, (unsigned long)left);
    if (written < 0) break;
    size_t available = reply_size - pos - 1;
    pos += ((size_t)written < available) ? (size_t)written : available;
  }
  if (!any) {
    snprintf(reply, reply_size, "> node.block empty");
  }
}

bool TCPBridge::handleNodeBlockCommand(const char *command, char *reply, size_t reply_size) {
  if (strcmp(command, "get node.block") == 0 || strcmp(command, "node.block") == 0) {
    formatNodeBlocks(reply, reply_size);
    return true;
  }
  if (strcmp(command, "clear node.block") == 0 || strcmp(command, "set node.block clear") == 0) {
    clearNodeBlocks();
    snprintf(reply, reply_size, "OK");
    return true;
  }
  if (strncmp(command, "set node.block ", 15) != 0) return false;

  char tmp[64];
  strncpy(tmp, command + 15, sizeof(tmp));
  tmp[sizeof(tmp) - 1] = 0;
  const char *parts[4];
  int n = mesh::Utils::parseTextParts(tmp, parts, 4);
  if (n < 1) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  if (strcmp(parts[0], "clear") == 0) {
    clearNodeBlocks();
    snprintf(reply, reply_size, "OK");
    return true;
  }
  if ((strcmp(parts[0], "add") != 0 && strcmp(parts[0], "del") != 0) || n < 2) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  uint8_t id = 0;
  if (!parseHex8(parts[1], &id)) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  if (strcmp(parts[0], "del") == 0) {
    bool removed = delNodeBlock(id);
    snprintf(reply, reply_size, removed ? "OK %02x" : "Error %02x", id);
    return true;
  }
  uint32_t duration = parseDurationSecs(n >= 3 ? parts[2] : nullptr, 15UL * 60UL);
  if (!addNodeBlock(id, duration)) {
    snprintf(reply, reply_size, "Error");
    return true;
  }
  snprintf(reply, reply_size, "OK %02x %lus", id, (unsigned long)duration);
  return true;
}

bool TCPBridge::isChannelPacket(const mesh::Packet *packet) const {
  if (!packet || !packet->isRouteFlood()) return false;
  uint8_t type = packet->getPayloadType();
  return type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_GRP_DATA;
}

bool TCPBridge::isMessagePacket(const mesh::Packet *packet) const {
  if (!packet) return false;
  uint8_t type = packet->getPayloadType();
  switch (type) {
    case PAYLOAD_TYPE_REQ:
    case PAYLOAD_TYPE_RESPONSE:
    case PAYLOAD_TYPE_TXT_MSG:
    case PAYLOAD_TYPE_ACK:
    case PAYLOAD_TYPE_PATH:
    case PAYLOAD_TYPE_GRP_TXT:
    case PAYLOAD_TYPE_GRP_DATA:
    case PAYLOAD_TYPE_ANON_REQ:
    case PAYLOAD_TYPE_MULTIPART:
      return true;
    default:
      return false;
  }
}

uint32_t TCPBridge::getBridgeId() {
  if (_bridge_id != 0) return _bridge_id;

  uint32_t configured_id = 0;
  if (_prefs->bridge_id[0] && parseHex32(_prefs->bridge_id, &configured_id) && configured_id != 0) {
    _bridge_id = configured_id;
    return _bridge_id;
  }

  if (_has_node_id) {
    _bridge_id = fnv1a32(_node_id, sizeof(_node_id));
  }

  uint8_t mac[6] = {};
  if (_bridge_id == 0 || _bridge_id == 2166136261UL) {
    WiFi.macAddress(mac);
    _bridge_id = fnv1a32(mac, sizeof(mac));
  }
  if (_bridge_id == 0 || _bridge_id == 2166136261UL) {
    size_t name_len = 0;
    while (name_len < sizeof(_prefs->node_name) && _prefs->node_name[name_len] != '\0') {
      name_len++;
    }
    _bridge_id = fnv1a32((const uint8_t *)_prefs->node_name, name_len);
  }
  if (_bridge_id == 0) _bridge_id = 1;
  return _bridge_id;
}

void TCPBridge::onPacketReceived(mesh::Packet *packet) {
  handleReceivedPacket(packet);
}

uint32_t TCPBridge::estimateInjectAirtimeMs(uint16_t packet_len) const {
  // Budget estimate only. Real duty telemetry is still reported from the radio layer.
  return packet_len == 0 ? 1 : (uint32_t)packet_len * 10UL;
}

bool TCPBridge::canInjectFromTcp(uint16_t packet_len) {
  if (!_prefs->bridge_rf_inject_budget_enabled) return true;

  uint32_t now = millis();
  if (_rf_inject_minute_start_ms == 0 || now - _rf_inject_minute_start_ms >= 60000UL) {
    _rf_inject_minute_start_ms = now;
    _rf_inject_minute_count = 0;
  }
  if (_rf_inject_hour_start_ms == 0 || now - _rf_inject_hour_start_ms >= 3600000UL) {
    _rf_inject_hour_start_ms = now;
    _rf_inject_hour_airtime_ms = 0;
  }

  if (_prefs->bridge_rf_inject_block_duty_centi_pct > 0 &&
      _rf_tx_used_centi_pct >= _prefs->bridge_rf_inject_block_duty_centi_pct) {
    return false;
  }
  if (_prefs->bridge_rf_inject_max_per_min > 0 &&
      _rf_inject_minute_count >= _prefs->bridge_rf_inject_max_per_min) {
    return false;
  }
  uint32_t estimate = estimateInjectAirtimeMs(packet_len);
  if (_prefs->bridge_rf_inject_max_airtime_ms_hour > 0) {
    if (_rf_inject_hour_airtime_ms >= _prefs->bridge_rf_inject_max_airtime_ms_hour) return false;
    if (estimate > (_prefs->bridge_rf_inject_max_airtime_ms_hour - _rf_inject_hour_airtime_ms)) return false;
  }
  return true;
}

void TCPBridge::recordInjectFromTcp(uint16_t packet_len) {
  if (!_prefs->bridge_rf_inject_budget_enabled) return;
  uint32_t now = millis();
  if (_rf_inject_minute_start_ms == 0 || now - _rf_inject_minute_start_ms >= 60000UL) {
    _rf_inject_minute_start_ms = now;
    _rf_inject_minute_count = 0;
  }
  if (_rf_inject_hour_start_ms == 0 || now - _rf_inject_hour_start_ms >= 3600000UL) {
    _rf_inject_hour_start_ms = now;
    _rf_inject_hour_airtime_ms = 0;
  }
  if (_rf_inject_minute_count < 0xFFFF) _rf_inject_minute_count++;
  uint32_t estimate = estimateInjectAirtimeMs(packet_len);
  if (_rf_inject_hour_airtime_ms <= 0xFFFFFFFFUL - estimate) {
    _rf_inject_hour_airtime_ms += estimate;
  } else {
    _rf_inject_hour_airtime_ms = 0xFFFFFFFFUL;
  }
}

void TCPBridge::resetGuardStats() {
  _transport_dropped_count = 0;
  _control_dropped_count = 0;
  _transport_flood_limiter.reset();
  _control_flood_limiter.reset();
  _rf_inject_minute_start_ms = 0;
  _rf_inject_minute_count = 0;
  _rf_inject_hour_start_ms = 0;
  _rf_inject_hour_airtime_ms = 0;
  _rf_inject_dropped_count = 0;
  _skipped_duplicate_count = 0;
  _skipped_own_origin_count = 0;
  _skipped_ttl_expired_count = 0;
  _skipped_bridge_loop_count = 0;
  _skipped_export_disabled_count = 0;
  _skipped_max_hops_count = 0;
  _skipped_rf_inject_budget_count = 0;
  _skipped_node_block_count = 0;
  _accepted_tcp_packet_count = 0;
  _exported_rf_to_tcp_count = 0;
  _injected_tcp_to_rf_count = 0;
}

void TCPBridge::setRfDutyStats(uint32_t used_ms, uint32_t max_ms, uint32_t window_ms, uint16_t limit_centi_pct, uint16_t used_centi_pct, uint32_t total_tx_ms,
                               int16_t noise_floor, int16_t last_rssi, int16_t last_snr_quarter_db, uint16_t neighbor_count) {
  _rf_tx_used_ms = used_ms;
  _rf_tx_max_ms = max_ms;
  _rf_tx_window_ms = window_ms;
  _rf_duty_limit_centi_pct = limit_centi_pct;
  _rf_tx_used_centi_pct = used_centi_pct;
  _rf_tx_total_ms = total_tx_ms;
  _radio_noise_floor = noise_floor;
  _radio_last_rssi = last_rssi;
  _radio_last_snr_quarter_db = last_snr_quarter_db;
  _neighbor_count = neighbor_count;
}

void TCPBridge::getStatusStr(char *reply) const {
  bool wifiOk = (WiFi.status() == WL_CONNECTED);
  bool serverOk = (_state == State::RUNNING);

  if (!wifiOk) {
    const char *stateStr = (_state == State::WIFI_WAIT) ? "connecting..." : "disconnected";
    sprintf(reply, "> WiFi: %s | Server: disconnected | NTP: %s", stateStr, "not synced");
    return;
  }

  char ip[16];
  WiFi.localIP().toString().toCharArray(ip, sizeof(ip));
  const char *serverStr = serverOk ? "connected"
                        : (_state == State::SERVER_WAIT) ? "connecting..."
                        : "disconnected";
  const char *ntpStr = _ntp_synced ? "synced" : "not synced";
  
  char dutyStr[80] = "";
  if (_rf_tx_max_ms > 0) {
    snprintf(dutyStr, sizeof(dutyStr), " | RF duty used: %u.%02u%% of %u.%02u%%/h",
             (uint32_t)(_rf_tx_used_centi_pct / 100),
             (uint32_t)(_rf_tx_used_centi_pct % 100),
             (uint32_t)(_rf_duty_limit_centi_pct / 100),
             (uint32_t)(_rf_duty_limit_centi_pct % 100));
  }
  char guardStr[64] = "";
  if (_prefs->bridge_rf_inject_budget_enabled || _rf_inject_dropped_count > 0) {
    snprintf(guardStr, sizeof(guardStr), " | Group:%s RFbudget:%s drop:%lu",
             _prefs->bridge_group,
             _prefs->bridge_rf_inject_budget_enabled ? "on" : "off",
             (unsigned long)_rf_inject_dropped_count);
  }
  char bridgeStatsStr[96] = "";
  snprintf(bridgeStatsStr, sizeof(bridgeStatsStr),
           " | B:%08lx a:%lu x:%lu i:%lu d:%lu o:%lu t:%lu l:%lu f:%lu h:%lu b:%lu n:%lu",
           (unsigned long)_bridge_id,
           (unsigned long)_accepted_tcp_packet_count,
           (unsigned long)_exported_rf_to_tcp_count,
           (unsigned long)_injected_tcp_to_rf_count,
           (unsigned long)_skipped_duplicate_count,
           (unsigned long)_skipped_own_origin_count,
           (unsigned long)_skipped_ttl_expired_count,
           (unsigned long)_skipped_bridge_loop_count,
           (unsigned long)_skipped_export_disabled_count,
           (unsigned long)_skipped_max_hops_count,
           (unsigned long)_skipped_rf_inject_budget_count,
           (unsigned long)_skipped_node_block_count);

  // Show TCP rate-limit stats if enabled and packets were dropped
  if (_prefs->tcp_flood_limit_enable && (_transport_dropped_count > 0 || _control_dropped_count > 0)) {
    snprintf(reply, 160, "> WiFi: connected | IP: %s | RSSI: %d dBm | Server: %s | NTP: %s%s%s%s | Rate drop:%lu/%lu",
            ip, WiFi.RSSI(), serverStr, ntpStr,
            dutyStr,
            guardStr,
            bridgeStatsStr,
            (unsigned long)_transport_dropped_count,
            (unsigned long)_control_dropped_count);
  } else {
    snprintf(reply, 160, "> WiFi: connected | IP: %s | RSSI: %d dBm | Server: %s | NTP: %s%s%s%s",
             ip, WiFi.RSSI(), serverStr, ntpStr, dutyStr, guardStr, bridgeStatsStr);
  }
}

void TCPBridge::syncTimeWithNTP(bool force) {
  if (WiFi.status() != WL_CONNECTED) return;

  uint32_t now_ms = millis();
  if (!force && _last_ntp_sync_ms != 0 && (now_ms - _last_ntp_sync_ms) < 5000) return;

  const char *server = "nl.pool.ntp.org";
  BRIDGE_DEBUG_PRINTLN("TCP bridge: syncing time with NTP server %s\n", server);

  configTime(0, 0, server, "pool.ntp.org", "time.google.com");

  struct tm timeinfo;
  bool ntp_ok = false;
  const uint32_t kMinValidEpoch = 1767225600UL;  // 2026-01-01 00:00:00 UTC

  for (int i = 0; i < 10; i++) {
    if (getLocalTime(&timeinfo, 500)) {
      uint32_t epoch = (uint32_t)mktime(&timeinfo);
      if (epoch >= kMinValidEpoch) {
        if (_rtc) {
          _rtc->setCurrentTime(epoch);
        }
        _ntp_synced = true;
        _last_ntp_sync_ms = millis();
        BRIDGE_DEBUG_PRINTLN("TCP bridge: NTP synced epoch=%lu\n", (unsigned long)epoch);
        ntp_ok = true;
        break;
      }
    }
  }

  if (!ntp_ok) {
    uint32_t retry_from = millis();
    _last_ntp_sync_ms = retry_from - (kNtpSyncIntervalMs - kNtpRetryIntervalMs);
    _ntp_synced = false;
    BRIDGE_DEBUG_PRINTLN("TCP bridge: NTP sync failed\n");
  }
}

void TCPBridge::refreshNTP(uint32_t now_ms) {
  if (WiFi.status() != WL_CONNECTED) return;

  if (_last_ntp_sync_ms == 0 || (now_ms - _last_ntp_sync_ms) >= kNtpSyncIntervalMs) {
    syncTimeWithNTP(true);
  }
}

#endif
