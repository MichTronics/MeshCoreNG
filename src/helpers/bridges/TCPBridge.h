#pragma once

#include "helpers/bridges/BridgeBase.h"
#include "helpers/RateLimiter.h"
#include <stddef.h>

#ifdef WITH_TCP_BRIDGE

/**
 * @brief Bridge implementation that tunnels mesh packets over a TCP connection
 *
 * Connects to a central TCP server (see tools/tcp_bridge_server.py) and exchanges
 * mesh packets with other repeaters connected to the same server. This allows
 * geographically separated LoRa mesh networks to act as one.
 *
 * The ESP32 connects to a WiFi access point in STA mode, then opens a TCP socket
 * to the configured server. Packets received from the local mesh are forwarded to
 * the server; packets arriving from the server are injected into the local mesh.
 *
 * Packet framing can carry either the legacy raw mesh packet payload or a
 * TCP bridge v2 envelope with bridge metadata:
 *   [2 bytes] Magic header (0xC03E)
 *   [2 bytes] Payload length
 *   [n bytes] Payload
 *   [2 bytes] Fletcher-16 checksum over payload
 *
 * Configuration via CLI:
 *   set wifi.ssid <ssid>         WiFi network to join
 *   set wifi.password <pw>       WiFi password
 *   set bridge.server <host>     TCP server hostname or IP
 *   set bridge.port <port>       TCP server port (default 4200)
 *   set bridge.password <pw>     Optional TCP bridge server password
 *   set bridge.enabled on        Enable the bridge
 *
 * Firmware build flag: -D WITH_TCP_BRIDGE
 */
class TCPBridgeCommandHandler {
public:
  virtual void handleTcpBridgeCommand(const char *password, const char *command, char *reply, size_t reply_size) = 0;
};

class TCPBridge : public BridgeBase {
public:
  TCPBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc);

  void begin() override;
  void end() override;
  void loop() override;
  void sendPacket(mesh::Packet *packet) override;
  void onPacketReceived(mesh::Packet *packet) override;
  void getStatusStr(char *reply) const;
  void setNodeId(const uint8_t *node_id, size_t len);
  void setRfDutyStats(uint32_t used_ms, uint32_t max_ms, uint32_t window_ms, uint16_t limit_centi_pct, uint16_t used_centi_pct, uint32_t total_tx_ms,
                      int16_t noise_floor = 0, int16_t last_rssi = 0, int16_t last_snr_quarter_db = 0, uint16_t neighbor_count = 0);
  void setFloodHopLimitDrops(uint32_t drops) { _flood_hop_limit_drops = drops; }
  void resetGuardStats();
  void setCommandHandler(TCPBridgeCommandHandler *handler) { _command_handler = handler; }

  bool pollJustConnected() {
    if (_just_connected) { _just_connected = false; return true; }
    return false;
  }

private:
  static constexpr uint16_t TCP_OVERHEAD =
      BRIDGE_MAGIC_SIZE + BRIDGE_LENGTH_SIZE + BRIDGE_CHECKSUM_SIZE;
  static constexpr uint16_t BRIDGE_V2_OVERHEAD = 14;
  static constexpr uint16_t MAX_TCP_PAYLOAD_SIZE = (MAX_TRANS_UNIT + 1) + BRIDGE_V2_OVERHEAD;
  static constexpr uint16_t MAX_TCP_PACKET_SIZE = MAX_TCP_PAYLOAD_SIZE + TCP_OVERHEAD;
  static constexpr uint32_t RECONNECT_INTERVAL_MS    = 5000;
  static constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS  = 15000;
  static constexpr uint32_t SERVER_CONNECT_TIMEOUT_MS = 500;
  static constexpr uint32_t SERVER_RECONNECT_INTERVAL_MS = 30000;
  static constexpr uint32_t HEARTBEAT_INTERVAL_MS    = 30000;
  static constexpr uint8_t  CONTROL_TYPE_HEARTBEAT   = 0x01;
  static constexpr uint8_t  CONTROL_TYPE_NODE_INFO   = 0x02;
  static constexpr uint8_t  CONTROL_TYPE_AUTH        = 0x03;
  static constexpr uint8_t  CONTROL_TYPE_CAPS        = 0x04;
  static constexpr uint8_t  CONTROL_TYPE_COMMAND     = 0x10;
  static constexpr uint8_t  CONTROL_TYPE_COMMAND_REPLY = 0x11;
  static constexpr uint8_t  CONTROL_TYPE_BRIDGE_PACKET = 0x20;
  static constexpr uint8_t  BRIDGE_PROTO_VERSION = 3;
  static constexpr uint8_t  BRIDGE_PACKET_VERSION = 1;
  static constexpr uint8_t  BRIDGE_PACKET_FLAG_RF_RX = 0x01;
  static constexpr uint8_t  NODE_BLOCK_MAX_ENTRIES = 16;
  static constexpr uint8_t  PATH_BLOCK_MAX_ENTRIES = 8;
  static constexpr uint8_t  PATH_BLOCK_MAX_HOPS = 3;
  static constexpr uint8_t  PATH_BLOCK_MAX_HASH_SIZE = 3;

  enum class State : uint8_t {
    IDLE,           // waiting for reconnect timer
    WIFI_WAIT,      // WiFi.begin() called, polling for connection
    SERVER_WAIT,    // calling _client.connect() (brief blocking)
    RUNNING,        // fully connected, normal operation
  };

  State    _state          = State::IDLE;
  uint32_t _last_reconnect_ms = 0;
  uint32_t _reconnect_interval_ms = RECONNECT_INTERVAL_MS;
  uint32_t _wifi_start_ms  = 0;
  uint32_t _last_heartbeat_ms = 0;

  uint8_t  _rx_buffer[MAX_TCP_PACKET_SIZE];
  uint16_t _rx_buffer_pos = 0;
  uint32_t _bridge_id = 0;
  uint8_t  _node_id[PUB_KEY_SIZE] = {0};
  bool     _has_node_id = false;
  TCPBridgeCommandHandler *_command_handler = nullptr;

  // Selective TCP rate limiting with separate limiters per packet category
  RateLimiter _transport_flood_limiter;  // for transport/message packets
  RateLimiter _control_flood_limiter;    // for control/admin packets  
  uint32_t    _transport_dropped_count = 0;
  uint32_t    _control_dropped_count = 0;
  bool        _just_connected = false;
  bool        _ntp_synced = false;
  uint32_t    _last_ntp_sync_ms = 0;
  uint32_t    _rf_tx_used_ms = 0;
  uint32_t    _rf_tx_max_ms = 0;
  uint32_t    _rf_tx_window_ms = 0;
  uint32_t    _rf_tx_total_ms = 0;
  uint16_t    _rf_duty_limit_centi_pct = 0;
  uint16_t    _rf_tx_used_centi_pct = 0;
  int16_t     _radio_noise_floor = 0;
  int16_t     _radio_last_rssi = 0;
  int16_t     _radio_last_snr_quarter_db = 0;
  uint16_t    _neighbor_count = 0;
  uint32_t    _flood_hop_limit_drops = 0;
  uint32_t    _rf_inject_minute_start_ms = 0;
  uint16_t    _rf_inject_minute_count = 0;
  uint32_t    _rf_inject_hour_start_ms = 0;
  uint32_t    _rf_inject_hour_airtime_ms = 0;
  uint32_t    _rf_inject_dropped_count = 0;
  uint32_t    _skipped_duplicate_count = 0;
  uint32_t    _skipped_own_origin_count = 0;
  uint32_t    _skipped_ttl_expired_count = 0;
  uint32_t    _skipped_bridge_loop_count = 0;
  uint32_t    _skipped_export_disabled_count = 0;
  uint32_t    _skipped_max_hops_count = 0;
  uint32_t    _skipped_rf_inject_budget_count = 0;
  uint32_t    _skipped_node_block_count = 0;
  uint32_t    _accepted_tcp_packet_count = 0;
  uint32_t    _exported_rf_to_tcp_count = 0;
  uint32_t    _injected_tcp_to_rf_count = 0;
  struct NodeBlockEntry {
    uint8_t id = 0;
    uint32_t until_ms = 0;
    bool active = false;
  };
  struct PathBlockEntry {
    uint8_t hash_size = 0;
    uint8_t hop_count = 0;
    uint8_t path[PATH_BLOCK_MAX_HOPS * PATH_BLOCK_MAX_HASH_SIZE] = {0};
    uint32_t until_ms = 0;
  };
  NodeBlockEntry _node_blocks[NODE_BLOCK_MAX_ENTRIES];
  PathBlockEntry _path_blocks[PATH_BLOCK_MAX_ENTRIES];
  bool sendPayloadFrame(const uint8_t *payload, uint16_t len);
  bool sendBridgePacket(mesh::Packet *packet);
  bool appendSelfToTcpExportPath(mesh::Packet *packet) const;
  bool pathContainsSelf(const mesh::Packet *packet) const;
  bool shouldExportPacket(const mesh::Packet *packet);
  bool sourceShortId(const mesh::Packet *packet, uint8_t *id) const;
  bool parsePathBlockSpec(const char *spec, PathBlockEntry *entry) const;
  bool pathBlockMatches(const mesh::Packet *packet, const PathBlockEntry &entry) const;
  void prunePathBlocks();
  bool isPathBlocked(const mesh::Packet *packet);
  bool addPathBlock(const PathBlockEntry &entry, uint32_t duration_secs);
  bool delPathBlock(const PathBlockEntry &entry);
  void clearPathBlocks();
  void formatPathBlocks(char *reply, size_t reply_size);
  bool handlePathBlockCommand(const char *command, char *reply, size_t reply_size);
  bool isBlockedForBridgeRf(const mesh::Packet *packet);
  void pruneNodeBlocks();
  bool isNodeBlocked(uint8_t id);
  bool isNodeBlockedForPacket(const mesh::Packet *packet);
  bool addNodeBlock(uint8_t id, uint32_t duration_secs);
  bool delNodeBlock(uint8_t id);
  void clearNodeBlocks();
  void formatNodeBlocks(char *reply, size_t reply_size);
  bool handleNodeBlockCommand(const char *command, char *reply, size_t reply_size);
  bool isChannelPacket(const mesh::Packet *packet) const;
  bool isMessagePacket(const mesh::Packet *packet) const;
  uint32_t estimateInjectAirtimeMs(uint16_t packet_len) const;
  bool canInjectFromTcp(uint16_t packet_len);
  void recordInjectFromTcp(uint16_t packet_len);
  uint32_t getBridgeId();
  void sendAuth();
  void sendNodeInfo();
  void sendCaps();
  void sendHeartbeat();
  void handleControlPayload(const uint8_t *payload, uint16_t len);
  void handleBridgePacketPayload(const uint8_t *payload, uint16_t len);
  void handleCommandPayload(const uint8_t *payload, uint16_t len);
  void sendCommandReply(uint32_t request_id, const char *reply);
  bool isControlPayload(const uint8_t *payload, uint16_t len) const;
  bool isTransportPacket(const uint8_t *payload, uint16_t len) const;
  bool isControlPacket(const uint8_t *payload, uint16_t len) const;
  void readIncoming();
  void syncTimeWithNTP(bool force);
  void refreshNTP(uint32_t now_ms);
};

#endif
