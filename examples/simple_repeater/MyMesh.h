#pragma once

#include <Arduino.h>
#include <Mesh.h>
#include "helpers/FloodLimits.h"
#include <RTClib.h>
#include <target.h>

#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  #include <InternalFileSystem.h>
#elif defined(RP2040_PLATFORM)
  #include <LittleFS.h>
#elif defined(ESP32)
  #include <SPIFFS.h>
#endif

#ifdef WITH_RS232_BRIDGE
#include "helpers/bridges/RS232Bridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_ESPNOW_BRIDGE
#include "helpers/bridges/ESPNowBridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_TCP_BRIDGE
#include "helpers/bridges/TCPBridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_BLE_BRIDGE
#include "helpers/bridges/BLEBridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_MQTT_BRIDGE
#include "helpers/bridges/MQTTBridge.h"
#define WITH_BRIDGE
#endif

#ifdef WITH_SNMP
#include "helpers/SNMPAgent.h"
#endif

#include <helpers/AdvertDataHelpers.h>
#include <helpers/ArduinoHelpers.h>
#include <helpers/ClientACL.h>
#include <helpers/CommonCLI.h>
#include <helpers/IdentityStore.h>
#include <helpers/SimpleMeshTables.h>
#include <helpers/StaticPoolPacketManager.h>
#include <helpers/StatsFormatHelper.h>
#include <helpers/TxtDataHelpers.h>
#include <helpers/RegionMap.h>
#include <helpers/RegionProfiles.h>
#include <helpers/RateLimiter.h>

struct RepeaterStats {
  uint16_t batt_milli_volts;
  uint16_t curr_tx_queue_len;
  int16_t  noise_floor;
  int16_t  last_rssi;
  uint32_t n_packets_recv;
  uint32_t n_packets_sent;
  uint32_t total_air_time_secs;
  uint32_t total_up_time_secs;
  uint32_t n_sent_flood, n_sent_direct;
  uint32_t n_recv_flood, n_recv_direct;
  uint16_t err_events;                // was 'n_full_events'
  int16_t  last_snr;   // x 4
  uint16_t n_direct_dups, n_flood_dups;
  uint32_t total_rx_air_time_secs;
  uint32_t n_recv_errors;
};

struct DenseMeshStats {
  uint32_t n_recv_flood_adverts;
  uint32_t n_fwd_flood_adverts;
  uint32_t n_drop_flood_adverts;
};

struct PowerSavingStats {
  uint32_t sleep_attempts;
  uint32_t skip_pending_work;
  uint32_t skip_bridge_active;
  uint32_t wake_rx_packet;
};

struct SpamStats {
  uint32_t public_group_seen;
  uint32_t decrypt_failed;
  uint32_t allowed;
  uint32_t malformed_dropped;
  uint32_t spam_dropped;
  uint32_t short_dropped;
  uint32_t type_dropped;
  uint32_t empty_dropped;
  uint32_t invalid_utf8_dropped;
  uint32_t timestamp_dropped;
  uint8_t last_score;
  uint8_t last_entropy;
  char last_reason[18];
};

#define MAX_PATH_BLOCKS          8
#define PATH_BLOCK_MAX_HOPS      3
#define PATH_BLOCK_MAX_HASH_SIZE 3
#define MAX_NODE_BLOCKS          16

struct PathBlockEntry {
  uint8_t hash_size;
  uint8_t hop_count;
  uint8_t path[PATH_BLOCK_MAX_HOPS * PATH_BLOCK_MAX_HASH_SIZE];
  uint32_t expires_at;
  uint32_t drops;
};

struct NodeBlockEntry {
  uint8_t id;
  uint8_t active;
  uint32_t expires_at;
  uint32_t drops;
};

typedef struct {
  uint16_t neighbors;
  uint16_t dup_rx;
  uint16_t unique_rx;
  uint16_t suppressed_tx;
  uint32_t airtime_rx_ms;
  uint32_t airtime_tx_ms;
  uint8_t congestion_level;
  uint8_t density_level;
} dense_mesh_stats_t;

#define DENSE_MESH_BUCKETS      4
#define DENSE_MESH_BUCKET_MS    15000UL

#ifndef MAX_CLIENTS
  #define MAX_CLIENTS           32
#endif

struct NeighbourInfo {
  mesh::Identity id;
  uint32_t advert_timestamp;
  uint32_t heard_timestamp;
  int8_t snr; // multiplied by 4, user should divide to get float value
};

#ifndef FIRMWARE_BUILD_DATE
  #define FIRMWARE_BUILD_DATE   "6 Jun 2026"
#endif

#ifndef FIRMWARE_VERSION
  #define FIRMWARE_VERSION   "v1.16.0"
#endif

#define FIRMWARE_ROLE "repeater"

#define PACKET_LOG_FILE  "/packet_log"

#if !defined(WITH_BRIDGE) || defined(WITH_TCP_BRIDGE)
#define SUPPORT_DAILY_REBOOT 1
#else
#define SUPPORT_DAILY_REBOOT 0
#endif

class MyMesh : public mesh::Mesh, public CommonCLICallbacks
#if defined(WITH_TCP_BRIDGE)
  , public TCPBridgeCommandHandler
#endif
{
  FILESYSTEM* _fs;
#if defined(WITH_MQTT_BRIDGE) || defined(WITH_SNMP)
  mesh::MainBoard* _board_ref = nullptr;  // saved for MQTT/SNMP stats sources
#endif
  uint32_t last_millis;
  uint64_t uptime_millis;
  unsigned long next_local_advert, next_flood_advert;
  bool _logging;
  NodePrefs _prefs;
  ClientACL  acl;
  CommonCLI _cli;
  uint8_t reply_data[MAX_PACKET_PAYLOAD];
  uint8_t reply_path[MAX_PATH_SIZE];
  int8_t  reply_path_len;
  uint8_t reply_path_hash_size;
  TransportKeyStore key_store;
  RegionMap region_map, temp_map;
  RegionEntry* load_stack[8];
  RegionEntry* recv_pkt_region;
  DenseMeshStats dense_stats;
  PowerSavingStats power_stats;
  SpamStats spam_stats;
  PathBlockEntry path_blocks[MAX_PATH_BLOCKS];
  NodeBlockEntry node_blocks[MAX_NODE_BLOCKS];
  dense_mesh_stats_t dense_buckets[DENSE_MESH_BUCKETS];
  uint8_t dense_bucket_idx;
  unsigned long dense_bucket_started;
  TransportKey default_scope;
  RateLimiter discover_limiter, anon_limiter;
  uint32_t pending_discover_tag;
  unsigned long pending_discover_until;
  bool region_load_active;
  uint64_t next_daily_reboot_uptime_ms;
  bool daily_reboot_pending;
  unsigned long dirty_contacts_expiry;
#if MAX_NEIGHBOURS
  NeighbourInfo neighbours[MAX_NEIGHBOURS];
#endif
  CayenneLPP telemetry;
  unsigned long set_radio_at, revert_radio_at;
  float pending_freq;
  float pending_bw;
  uint8_t pending_sf;
  uint8_t pending_cr;
  int  matching_peer_indexes[MAX_CLIENTS];
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
  TCPBridge tcp_bridge;
  BLEBridge ble_bridge;
#elif defined(WITH_RS232_BRIDGE)
  RS232Bridge bridge;
#elif defined(WITH_ESPNOW_BRIDGE)
  ESPNowBridge bridge;
#elif defined(WITH_TCP_BRIDGE)
  TCPBridge bridge;
#elif defined(WITH_BLE_BRIDGE)
  BLEBridge bridge;
#endif
#ifdef WITH_MQTT_BRIDGE
  // MQTT runs as an additional bridge alongside any of the above (or standalone).
  // Heap-allocated in setBridgeState() to avoid ESP32 static-init crashes.
  MQTTBridge* mqtt_bridge = nullptr;
#endif
#ifdef WITH_SNMP
  MeshSNMPAgent _snmp_agent;
#endif

  void putNeighbour(const mesh::Identity& id, uint32_t timestamp, float snr);
  uint8_t handleLoginReq(const mesh::Identity& sender, const uint8_t* secret, uint32_t sender_timestamp, const uint8_t* data, bool is_flood);
  uint8_t handleAnonRegionsReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  uint8_t handleAnonOwnerReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  uint8_t handleAnonClockReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data);
  int handleRequest(ClientInfo* sender, uint32_t sender_timestamp, uint8_t* payload, size_t payload_len);
  mesh::Packet* createSelfAdvert();

  File openAppend(const char* fname);
  bool isLooped(const mesh::Packet* packet, const uint8_t max_counters[]);
  void rotateDenseStats();
  void clearDenseStatsLocked();
  void recordDenseUniqueRx();
  void recordDenseDupRx();
  void recordDenseSuppressedTx();
  void recordDenseRxAirtime(uint32_t airtime_ms);
  void recordDenseTxAirtime(uint32_t airtime_ms);
  uint16_t getDenseNeighborCount() const;
  void getDenseStats(dense_mesh_stats_t* stats);
  void clearSpamStatsLocked();
  void recordSpamDrop(const char* reason, uint8_t score, uint8_t entropy);
  bool shouldDropMalformedGroupText(mesh::Packet* pkt);
  void clearExpiredPathBlocks();
  bool parsePathBlockSpec(const char* spec, PathBlockEntry* entry) const;
  bool pathBlockMatches(const mesh::Packet* packet, const PathBlockEntry& entry) const;
  bool shouldBlockPath(const mesh::Packet* packet);
  void clearExpiredNodeBlocks();
  bool sourceShortId(const mesh::Packet* packet, uint8_t* id) const;
  bool shouldBlockNode(const mesh::Packet* packet);
  bool shouldBlockBridgeOrRepeater(const mesh::Packet* packet);
  bool isLikelyNearbyClientFlood(const mesh::Packet* packet) const;
  bool shouldSuppressNearbyClientFlood(const mesh::Packet* packet) const;
  void formatNodeBlocksReply(char* reply);
  void handleNodeBlockCommand(char* command, char* reply);
  void formatPathBlocksReply(char* reply);
  void handlePathBlockCommand(char* command, char* reply);
  void scheduleDailyReboot();
  void checkDailyReboot();
protected:
  float getAirtimeBudgetFactor() const override {
    return _prefs.airtime_factor;
  }

  uint8_t getFloodMaxPathCount() const override {
    return _prefs.flood_max;
  }

  uint8_t getEffectiveFloodMaxForRelay(const mesh::Packet* packet) const override {
    return mesh::effectiveFloodMaxHopLimit(_prefs, packet);
  }

  bool allowPacketForward(const mesh::Packet* packet) override;
  const char* getLogDateTime() override;
  void logRxRaw(float snr, float rssi, const uint8_t raw[], int len) override;

  void logRx(mesh::Packet* pkt, int len, float score) override;
  void logTx(mesh::Packet* pkt, int len) override;
  void logTxFail(mesh::Packet* pkt, int len) override;
  void onRxAirTime(uint32_t air_time_ms) override;
  void onTxAirTime(uint32_t air_time_ms) override;
  void onPacketSeen(mesh::Packet* packet, bool duplicate) override;
  bool isDuplicateSuppressionEnabled() const override {
    return _prefs.flood_dup_suppress_enable != 0;
  }
  bool isNodeDelayOffsetEnabled() const override {
    return _prefs.flood_node_delay_enable != 0;
  }
  int calcRxDelay(float score, uint32_t air_time) const override;

  uint32_t getRetransmitDelay(const mesh::Packet* packet) override;
  uint32_t getDirectRetransmitDelay(const mesh::Packet* packet) override;

  int getInterferenceThreshold() const override {
    return _prefs.interference_threshold;
  }
  int getAGCResetInterval() const override {
    return ((int)_prefs.agc_reset_interval) * 4000;   // milliseconds
  }
  uint8_t getExtraAckTransmitCount() const override {
    return _prefs.multi_acks;
  }

#if ENV_INCLUDE_GPS == 1
  void applyGpsPrefs() {
    sensors.setSettingValue("gps", _prefs.gps_enabled?"1":"0");
  }
#endif

  bool filterRecvFloodPacket(mesh::Packet* pkt) override;

  void onAnonDataRecv(mesh::Packet* packet, const uint8_t* secret, const mesh::Identity& sender, uint8_t* data, size_t len) override;
  int searchPeersByHash(const uint8_t* hash) override;
  void getPeerSharedSecret(uint8_t* dest_secret, int peer_idx) override;
  void onAdvertRecv(mesh::Packet* packet, const mesh::Identity& id, uint32_t timestamp, const uint8_t* app_data, size_t app_data_len);
  void onPeerDataRecv(mesh::Packet* packet, uint8_t type, int sender_idx, const uint8_t* secret, uint8_t* data, size_t len) override;
  bool onPeerPathRecv(mesh::Packet* packet, int sender_idx, const uint8_t* secret, uint8_t* path, uint8_t path_len, uint8_t extra_type, uint8_t* extra, uint8_t extra_len) override;
  void onControlDataRecv(mesh::Packet* packet) override;

  void sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size);

public:
  MyMesh(mesh::MainBoard& board, mesh::Radio& radio, mesh::MillisecondClock& ms, mesh::RNG& rng, mesh::RTCClock& rtc, mesh::MeshTables& tables);

  void begin(FILESYSTEM* fs);
  void sendNodeDiscoverReq();
  const char* getFirmwareVer() override { return FIRMWARE_VERSION; }
  const char* getBuildDate() override { return FIRMWARE_BUILD_DATE; }
  const char* getRole() override { return FIRMWARE_ROLE; }
  const char* getNodeName() { return _prefs.node_name; }
  NodePrefs* getNodePrefs() {
    return &_prefs;
  }

  void savePrefs() override {
    _cli.savePrefs(_fs);
  }

  void sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis, uint8_t path_hash_size);

  // CommonCLICallbacks
  void applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) override;
  bool formatFileSystem() override;
  void sendSelfAdvertisement(int delay_millis, bool flood) override;
  void updateAdvertTimer() override;
  void updateFloodAdvertTimer() override;

  void setLoggingOn(bool enable) override { _logging = enable; }

  void eraseLogFile() override {
    _fs->remove(PACKET_LOG_FILE);
  }

  void dumpLogFile() override;
  void setTxPower(int8_t power_dbm) override;
  void formatNeighborsReply(char *reply) override;
  void removeNeighbor(const uint8_t* pubkey, int key_len) override;
  void formatStatsReply(char *reply) override;
  void formatRadioStatsReply(char *reply) override;
  void formatPacketStatsReply(char *reply) override;
  void formatDenseStatsReply(char *reply) override;
  void formatAtlasStatsReply(char *reply) override;
  void formatAtlasObserverReply(char *reply) override;
  void formatSpamStatsReply(char *reply) override;
  void formatRepeaterHealthReply(char *reply) override;
  void formatPowerStatsReply(char *reply) override;
  void startRegionsLoad() override;
  bool saveRegions() override;
  void onDefaultRegionChanged(const RegionEntry* r) override;

  mesh::LocalIdentity& getSelfId() override { return self_id; }

  void saveIdentity(const mesh::LocalIdentity& new_id) override;
  void clearStats() override;
  void clearDenseStats() override;
  void clearSpamStats() override;
  void clearPowerStats() override;

  void handleCommand(uint32_t sender_timestamp, char* command, char* reply);
#if defined(WITH_TCP_BRIDGE)
  void handleTcpBridgeCommand(const char *password, const char *command, char *reply, size_t reply_size) override;
  void configureTcpBridgeNodeIds();
#endif
  void loop();
  void formatDailyRebootReply(char* reply) const;

#ifdef WITH_MQTT_BRIDGE
  // Construct (deferred), configure and start the MQTT bridge. Idempotent.
  void startMqttBridge() {
    if (!mqtt_bridge) {
      mqtt_bridge = new MQTTBridge(&_prefs, _mgr, getRTCClock(), &self_id);
      if (!mqtt_bridge) return;
    }
    if (mqtt_bridge->isRunning()) return;
    char device_id[65];
    mesh::Utils::toHex(device_id, self_id.pub_key, PUB_KEY_SIZE);
    mqtt_bridge->setDeviceID(device_id);
    mqtt_bridge->setFirmwareVersion(getFirmwareVer());
    if (_board_ref) mqtt_bridge->setBoardModel(_board_ref->getManufacturerName());
    mqtt_bridge->setBuildDate(getBuildDate());
    mqtt_bridge->setStatsSources(this, _radio, _board_ref, _ms);
#if defined(WITH_TCP_BRIDGE)
    // The TCP bridge owns the WiFi STA connection; MQTT piggybacks on it.
    mqtt_bridge->setExternalWiFiManagement(true);
#endif
#ifdef WITH_SNMP
    if (_prefs.snmp_enabled) {
      _snmp_agent.setNodeName(_prefs.node_name);
      _snmp_agent.setFirmwareVersion(getFirmwareVer());
      mqtt_bridge->setSNMPAgent(&_snmp_agent);
      _snmp_agent.begin(_prefs.snmp_community);
    }
#endif
    mqtt_bridge->begin();
  }
#endif

#if defined(WITH_BRIDGE)
  void setBridgeState(bool enable) override {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    if (enable != (tcp_bridge.isRunning() && ble_bridge.isRunning())) {
      if (enable) {
        configureTcpBridgeNodeIds();
        tcp_bridge.setCommandHandler(this);
        tcp_bridge.begin();
        ble_bridge.begin();
      } else {
        tcp_bridge.end();
        ble_bridge.end();
      }
    }
#elif defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
    if (enable != bridge.isRunning()) {
      if (enable) {
#if defined(WITH_TCP_BRIDGE)
        configureTcpBridgeNodeIds();
        bridge.setCommandHandler(this);
#endif
        bridge.begin();
      } else {
        bridge.end();
      }
    }
#endif
#ifdef WITH_MQTT_BRIDGE
    if (enable) startMqttBridge();
    else if (mqtt_bridge) mqtt_bridge->end();
#endif
  }

  void restartBridge() override {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    if (tcp_bridge.isRunning() || ble_bridge.isRunning()) {
      tcp_bridge.end();
      ble_bridge.end();
      configureTcpBridgeNodeIds();
      tcp_bridge.setCommandHandler(this);
      tcp_bridge.begin();
      ble_bridge.begin();
    }
#elif defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
    if (bridge.isRunning()) {
      bridge.end();
#if defined(WITH_TCP_BRIDGE)
      configureTcpBridgeNodeIds();
      bridge.setCommandHandler(this);
#endif
      bridge.begin();
    }
#endif
#ifdef WITH_MQTT_BRIDGE
    if (mqtt_bridge && mqtt_bridge->isRunning()) {
      mqtt_bridge->end();
      startMqttBridge();
    }
#endif
  }

#ifdef WITH_MQTT_BRIDGE
  void restartBridgeSlot(int slot) override {
    if (!mqtt_bridge || !mqtt_bridge->isRunning()) return;
    mqtt_bridge->setSlotPreset(slot, _prefs.mqtt_slot_preset[slot]);
  }
#endif
#endif // WITH_BRIDGE

#if defined(WITH_TCP_BRIDGE)
  void formatTcpBridgeStatusReply(char *reply) override {
    uint32_t max_tx_budget = getMaxTxBudget();
    uint32_t remaining_tx_budget = getEffectiveRemainingTxBudget();
    uint32_t used_tx_budget = remaining_tx_budget >= max_tx_budget ? 0 : (max_tx_budget - remaining_tx_budget);
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    tcp_bridge.setRfDutyStats(used_tx_budget, max_tx_budget, getDutyCycleWindowMs(),
                              getDutyCycleLimitCentiPct(), getTxBudgetUsedCentiPct(), getTotalAirTime(),
                              (int16_t)_radio->getNoiseFloor(), (int16_t)radio_driver.getLastRSSI(),
                              (int16_t)(radio_driver.getLastSNR() * 4), getDenseNeighborCount());
    tcp_bridge.getStatusStr(reply);
#else
    bridge.setRfDutyStats(used_tx_budget, max_tx_budget, getDutyCycleWindowMs(),
                          getDutyCycleLimitCentiPct(), getTxBudgetUsedCentiPct(), getTotalAirTime(),
                          (int16_t)_radio->getNoiseFloor(), (int16_t)radio_driver.getLastRSSI(),
                          (int16_t)(radio_driver.getLastSNR() * 4), getDenseNeighborCount());
    bridge.getStatusStr(reply);
#endif
  }

  void resetTcpBridgeStats() override {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    tcp_bridge.resetGuardStats();
#else
    bridge.resetGuardStats();
#endif
  }
#endif

#if defined(WITH_BLE_BRIDGE)
  void formatBleBridgeStatusReply(char *reply) override {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    ble_bridge.getStatusStr(reply);
#else
    bridge.getStatusStr(reply);
#endif
  }
#endif

  // To check if there is pending work
  bool isBridgeActive() const;
  bool hasOutboundWork() const;
  bool hasPendingWork() const;
  void recordSleepAttempt();
  void recordSleepSkipPendingWork();
  void recordSleepSkipBridgeActive();
  void recordStartupWakeReason();

#if defined(USE_SX1262) || defined(USE_SX1268)
  void setRxBoostedGain(bool enable) override;
#endif
};
