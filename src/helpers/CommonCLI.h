#pragma once

#include "Mesh.h"
#include <helpers/IdentityStore.h>
#include <helpers/SensorManager.h>
#include <helpers/ClientACL.h>
#include <helpers/RegionMap.h>
#include <helpers/Atlas.h>
#ifdef WITH_MQTT_BRIDGE
#include <helpers/MQTTPresets.h>  // For MAX_MQTT_SLOTS (used in NodePrefs/MQTTPrefs struct layout)
#endif
#ifndef WITH_DUTCH_REGION_DB
#define WITH_DUTCH_REGION_DB 0
#endif
#if WITH_DUTCH_REGION_DB
#include <helpers/DutchRegionDb.h>
#endif

#if defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_TCP_BRIDGE) || defined(WITH_BLE_BRIDGE) || defined(WITH_MQTT_BRIDGE)
#define WITH_BRIDGE
#endif

#define ADVERT_LOC_NONE       0
#define ADVERT_LOC_SHARE      1
#define ADVERT_LOC_PREFS      2

#define LOOP_DETECT_OFF       0
#define LOOP_DETECT_MINIMAL   1
#define LOOP_DETECT_MODERATE  2
#define LOOP_DETECT_STRICT    3

#define BRIDGE_RF_OFF         0
#define BRIDGE_RF_FLOOD       1
#define BRIDGE_RF_LOCAL       2

#define BRIDGE_EXPORT_ALL       0
#define BRIDGE_EXPORT_FLOOD     1
#define BRIDGE_EXPORT_CHANNELS  2
#define BRIDGE_EXPORT_MESSAGES  3

struct NodePrefs { // persisted to file
  float airtime_factor;
  char node_name[32];
  double node_lat, node_lon;
  char password[16];
  float freq;
  int8_t tx_power_dbm;
  uint8_t disable_fwd;
  uint8_t advert_interval;       // minutes / 2
  uint8_t flood_advert_interval; // hours
  float rx_delay_base;
  float tx_delay_factor;
  char guest_password[16];
  float direct_tx_delay_factor;
  uint32_t guard;
  uint8_t sf;
  uint8_t cr;
  uint8_t allow_read_only;
  uint8_t multi_acks;
  float bw;
  uint8_t flood_max;              // default relay cap for all flood packets (0 = no cap)
  uint8_t flood_max_unscoped;     // cap for ROUTE_TYPE_FLOOD (no scope); min with flood_max
  uint8_t flood_max_advert;       // advert cap; may exceed flood_max for neighbour discovery
  uint8_t flood_max_messages;    // legacy optional stricter cap (CLI only; hierarchy uses flood_max)
  uint8_t interference_threshold;
  uint8_t agc_reset_interval; // secs / 4
  // Bridge settings
  uint8_t bridge_enabled; // boolean
  uint16_t bridge_delay;  // milliseconds (default 500 ms)
  uint8_t bridge_pkt_src; // 0 = logTx, 1 = logRx, 2 = both (default logTx)
  uint8_t bridge_rf;      // BRIDGE_RF_* mode for packets received from a bridge
  uint32_t bridge_baud;   // 9600, 19200, 38400, 57600, 115200 (default 115200)
  uint8_t bridge_channel; // 1-14 (ESP-NOW only)
  char bridge_secret[16]; // for wireless bridge packet isolation (ESP-NOW/BLE)
  // Power setting
  uint8_t powersaving_enabled; // boolean
  // Gps settings
  uint8_t gps_enabled;
  uint32_t gps_interval; // in seconds
  uint8_t advert_loc_policy;
  uint32_t discovery_mod_timestamp;
  float adc_multiplier;
  char owner_info[120];
  uint8_t rx_boosted_gain; // power settings
  uint8_t path_hash_mode;   // which path mode to use when sending
  uint8_t loop_detect;
  float flood_advert_base;
  uint8_t flood_relay_prob;
  uint8_t flood_dynamic_enable;
  // Internet TCP bridge settings (ESP32 only, WITH_TCP_BRIDGE)
  char wifi_ssid[32];
  char wifi_password[64];
  char bridge_server[64];
  uint16_t bridge_port;
  char bridge_password[64];
  uint8_t malformed_drop; // drop malformed decryptable public/group chat instead of forwarding
  uint8_t flood_node_delay_enable;
  uint8_t flood_dup_suppress_enable;
  AtlasConfig atlas;
  uint8_t daily_reboot_enabled;
  uint8_t daily_reboot_interval_hours;
  uint8_t fem_rx_gain; // external FEM/LNA RX gain, board-specific
  // TCP bridge rate-limit settings
  uint8_t tcp_flood_limit_enable; // enable TCP bridge rate limiting
  // Selective rate limiting per packet category
  uint16_t tcp_flood_transport_max; // max transport/message packets (DMs, group msgs)
  uint16_t tcp_flood_transport_window; // transport time window in seconds
  uint16_t tcp_flood_control_max; // max control/admin packets (0 = bypass)
  uint16_t tcp_flood_control_window; // control time window in seconds
  uint8_t low_bat_boot_guard_enabled;
  uint16_t low_bat_boot_guard_mv;
  uint16_t low_bat_boot_valid_min_mv;
  uint16_t low_bat_boot_retry_secs;
  uint8_t low_bat_runtime_guard_enabled;
  uint16_t low_bat_runtime_guard_mv;
  uint16_t low_bat_runtime_warn_mv;
  uint16_t low_bat_runtime_valid_min_mv;
  uint32_t low_bat_runtime_retry_secs;
  uint8_t bridge_export_filter;   // BRIDGE_EXPORT_* packet filter before bridge export
  uint8_t bridge_export_max_hops; // 0 = unlimited, otherwise max RF path hash count to export
  uint8_t bridge_tcp_ttl;         // TCP bridge envelope TTL for multi-bridge loop control
  uint8_t bridge_profile;         // 0=default, 1=island, 2=repeater, 3=gateway (no RF->RF)
  char bridge_group[16];          // TCP bridge scope/group advertised to the server
  uint8_t bridge_rf_inject_budget_enabled;      // locally gate TCP->RF injection
  uint16_t bridge_rf_inject_max_per_min;        // 0 = no packet count cap
  uint32_t bridge_rf_inject_max_airtime_ms_hour; // 0 = no airtime cap
  uint16_t bridge_rf_inject_block_duty_centi_pct; // 0 = no duty threshold
  char bridge_id[9];          // optional TCP bridge identity override, 8 hex chars
  uint8_t nearby_client_suppress_enabled; // suppress very nearby first-hop client floods
  int16_t nearby_client_suppress_rssi_dbm; // RSSI threshold for nearby client suppression
  uint8_t nearby_client_suppress_max_hops; // normally 0: only original sender packets
#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge settings — mirrored in memory from the separate /mqtt_prefs file so the
  // bridge can read everything from NodePrefs. Appended at the end of NodePrefs so the main
  // prefs file layout for non-MQTT builds is unaffected.
  char mqtt_origin[32];          // Device name for MQTT topics
  char mqtt_iata[8];             // IATA code for MQTT topics
  uint8_t mqtt_status_enabled;   // Enable status messages
  uint8_t mqtt_packets_enabled;  // Enable packet messages
  uint8_t mqtt_raw_enabled;      // Enable raw messages
  uint8_t mqtt_tx_enabled;       // TX packet uplinking: 0=off, 1=all, 2=advert (self-originated only)
  uint32_t mqtt_status_interval; // Status publish interval (ms)
  uint8_t mqtt_rx_enabled;       // Enable RX packet uplinking (default: on)
  uint8_t wifi_power_save;       // WiFi power save mode: 0=min, 1=none, 2=max (default: 1=none)
  char timezone_string[32];      // Timezone string (e.g., "America/Los_Angeles")
  int8_t timezone_offset;        // Timezone offset in hours (-12 to +14) - fallback
  char mqtt_slot_preset[MAX_MQTT_SLOTS][24];   // e.g. "analyzer-us", "meshmapper", "custom", "none"
  char mqtt_slot_host[MAX_MQTT_SLOTS][64];     // custom broker host (preset == "custom")
  uint16_t mqtt_slot_port[MAX_MQTT_SLOTS];     // custom broker port
  char mqtt_slot_username[MAX_MQTT_SLOTS][32]; // per-slot username
  char mqtt_slot_password[MAX_MQTT_SLOTS][64]; // per-slot password
  char mqtt_owner_public_key[65];              // Owner public key (hex string)
  char mqtt_email[64];                         // Owner email address
  char mqtt_slot_token[MAX_MQTT_SLOTS][48];    // Per-slot token (e.g., MeshRank account token)
  char mqtt_slot_topic[MAX_MQTT_SLOTS][96];    // Per-slot custom topic template (custom preset only)
  char mqtt_slot_audience[MAX_MQTT_SLOTS][64]; // JWT audience (non-empty enables JWT auth for custom slots)
  uint8_t snmp_enabled;          // SNMP agent: 0=off, 1=on (only used when WITH_SNMP)
  char snmp_community[24];        // SNMP community string (default "public")
#endif
};

#ifdef WITH_MQTT_BRIDGE
// Old MQTT preferences layout (pre-slot firmware) — used only for migration detection
struct OldMQTTPrefs {
  char mqtt_origin[32];
  char mqtt_iata[8];
  uint8_t mqtt_status_enabled;
  uint8_t mqtt_packets_enabled;
  uint8_t mqtt_raw_enabled;
  uint8_t mqtt_tx_enabled;
  uint32_t mqtt_status_interval;
  char wifi_ssid[32];
  char wifi_password[64];
  uint8_t wifi_power_save;
  char timezone_string[32];
  int8_t timezone_offset;
  char mqtt_server[64];
  uint16_t mqtt_port;
  char mqtt_username[32];
  char mqtt_password[64];
  uint8_t mqtt_analyzer_us_enabled;
  uint8_t mqtt_analyzer_eu_enabled;
  char mqtt_owner_public_key[65];
  char mqtt_email[64];
};

// MQTT preferences stored in a separate file to avoid conflicts with upstream NodePrefs changes
struct MQTTPrefs {
  char mqtt_origin[32];
  char mqtt_iata[8];
  uint8_t mqtt_status_enabled;
  uint8_t mqtt_packets_enabled;
  uint8_t mqtt_raw_enabled;
  uint8_t mqtt_tx_enabled;
  uint32_t mqtt_status_interval;
  char wifi_ssid[32];
  char wifi_password[64];
  uint8_t wifi_power_save;
  char timezone_string[32];
  int8_t timezone_offset;
  char mqtt_slot_preset[MAX_MQTT_SLOTS][24];
  char mqtt_slot_host[MAX_MQTT_SLOTS][64];
  uint16_t mqtt_slot_port[MAX_MQTT_SLOTS];
  char mqtt_slot_username[MAX_MQTT_SLOTS][32];
  char mqtt_slot_password[MAX_MQTT_SLOTS][64];
  char mqtt_owner_public_key[65];
  char mqtt_email[64];
  // --- Legacy fields (vestigial, kept for binary compatibility of /mqtt_prefs) ---
  uint8_t _legacy_analyzer_us_enabled;
  uint8_t _legacy_analyzer_eu_enabled;
  char _legacy_mqtt_server[64];
  uint16_t _legacy_mqtt_port;
  char _legacy_mqtt_username[32];
  char _legacy_mqtt_password[64];
  // --- New fields (appended at end for migration safety) ---
  char mqtt_slot_token[MAX_MQTT_SLOTS][48];
  char mqtt_slot_topic[MAX_MQTT_SLOTS][96];
  char mqtt_slot_audience[MAX_MQTT_SLOTS][64];
  uint8_t mqtt_rx_enabled;
  // --- Appended for MeshCoreNG (SNMP persisted alongside MQTT config) ---
  uint8_t snmp_enabled;
  char snmp_community[24];
};

// 3-slot MQTTPrefs layout — used for migrating from 3-slot to 6-slot format.
struct ThreeSlotMQTTPrefs {
  char mqtt_origin[32];
  char mqtt_iata[8];
  uint8_t mqtt_status_enabled;
  uint8_t mqtt_packets_enabled;
  uint8_t mqtt_raw_enabled;
  uint8_t mqtt_tx_enabled;
  uint32_t mqtt_status_interval;
  char wifi_ssid[32];
  char wifi_password[64];
  uint8_t wifi_power_save;
  char timezone_string[32];
  int8_t timezone_offset;
  char mqtt_slot_preset[3][24];
  char mqtt_slot_host[3][64];
  uint16_t mqtt_slot_port[3];
  char mqtt_slot_username[3][32];
  char mqtt_slot_password[3][64];
  char mqtt_owner_public_key[65];
  char mqtt_email[64];
  uint8_t _legacy_analyzer_us_enabled;
  uint8_t _legacy_analyzer_eu_enabled;
  char _legacy_mqtt_server[64];
  uint16_t _legacy_mqtt_port;
  char _legacy_mqtt_username[32];
  char _legacy_mqtt_password[64];
  char mqtt_slot_token[3][48];
  char mqtt_slot_topic[3][96];
};
#endif

class CommonCLICallbacks {
public:
  virtual void savePrefs() = 0;
  virtual const char* getFirmwareVer() = 0;
  virtual const char* getBuildDate() = 0;
  virtual const char* getRole() = 0;
  virtual bool formatFileSystem() = 0;
  virtual void sendSelfAdvertisement(int delay_millis, bool flood) = 0;
  virtual void updateAdvertTimer() = 0;
  virtual void updateFloodAdvertTimer() = 0;
  virtual void setLoggingOn(bool enable) = 0;
  virtual void eraseLogFile() = 0;
  virtual void dumpLogFile() = 0;
  virtual void setTxPower(int8_t power_dbm) = 0;
  virtual void formatNeighborsReply(char *reply) = 0;
  virtual void removeNeighbor(const uint8_t* pubkey, int key_len) {
    // no op by default
  };
  virtual void formatStatsReply(char *reply) = 0;
  virtual void formatRadioStatsReply(char *reply) = 0;
  virtual void formatPacketStatsReply(char *reply) = 0;
  virtual void formatDenseStatsReply(char *reply) {
    reply[0] = 0;
  }
  virtual void formatSpamStatsReply(char *reply) {
    reply[0] = 0;
  }
  virtual void formatRepeaterHealthReply(char *reply) {
    reply[0] = 0;
  }
  virtual void formatPowerStatsReply(char *reply) {
    reply[0] = 0;
  }
  virtual void formatAtlasStatsReply(char *reply) {
    strcpy(reply, "{\"heard\":0,\"dup\":0,\"fwd\":0,\"sup\":0,\"route\":{\"hit\":0,\"miss\":0},\"air\":{\"tx\":0,\"rx\":0}}");
  }
  virtual void formatAtlasObserverReply(char *reply) {
    strcpy(reply, "[]");
  }
  virtual mesh::LocalIdentity& getSelfId() = 0;
  virtual void saveIdentity(const mesh::LocalIdentity& new_id) = 0;
  virtual void clearStats() = 0;
  virtual void clearDenseStats() {
  }
  virtual void clearSpamStats() {
  }
  virtual void clearPowerStats() {
  }
  virtual void applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) = 0;

  virtual void startRegionsLoad() {
    // no op by default
  }
  virtual bool saveRegions() {
    return false;
  }
  virtual void onDefaultRegionChanged(const RegionEntry* r) {
    // no op by default
  }

  virtual void formatTcpBridgeStatusReply(char *reply) {
    reply[0] = 0;
  }

  virtual void resetTcpBridgeStats() {
    // no op by default
  }

  virtual void formatBleBridgeStatusReply(char *reply) {
    reply[0] = 0;
  }

  virtual void setBridgeState(bool enable) {
    // no op by default
  };

  virtual void restartBridge() {
    // no op by default
  };

  virtual void restartBridgeSlot(int slot_index) {
    // no op by default (used by MQTT bridge to reconnect a single slot)
  };

  virtual void setRxBoostedGain(bool enable) {
    // no op by default
  };
};

class CommonCLI {
  mesh::RTCClock* _rtc;
  NodePrefs* _prefs;
  CommonCLICallbacks* _callbacks;
  mesh::MainBoard* _board;
  SensorManager* _sensors;
  RegionMap* _region_map;
  ClientACL* _acl;
  char tmp[PRV_KEY_SIZE*2 + 4];
#ifdef WITH_MQTT_BRIDGE
  MQTTPrefs _mqtt_prefs;
  void loadMQTTPrefs(FILESYSTEM* fs);
  void saveMQTTPrefs(FILESYSTEM* fs);
  void syncMQTTPrefsToNodePrefs();
  void syncNodePrefsToMQTTPrefs();
#endif

  mesh::RTCClock* getRTCClock() { return _rtc; }
  void savePrefs();
  void loadPrefsInt(FILESYSTEM* _fs, const char* filename);

  void handleRegionCmd(char* command, char* reply);
#if WITH_DUTCH_REGION_DB
  void handleDutchRegionDbCmd(char* command, char* reply);
#endif
  void handleGetCmd(uint32_t sender_timestamp, char* command, char* reply);
  void handleSetCmd(uint32_t sender_timestamp, char* command, char* reply);

public:
  CommonCLI(mesh::MainBoard& board, mesh::RTCClock& rtc, SensorManager& sensors, RegionMap& region_map, ClientACL& acl, NodePrefs* prefs, CommonCLICallbacks* callbacks)
      : _board(&board), _rtc(&rtc), _sensors(&sensors), _region_map(&region_map), _acl(&acl), _prefs(prefs), _callbacks(callbacks) { }

  void loadPrefs(FILESYSTEM* _fs);
  void savePrefs(FILESYSTEM* _fs);
  void handleCommand(uint32_t sender_timestamp, char* command, char* reply);
  uint8_t buildAdvertData(uint8_t node_type, uint8_t* app_data);
};
