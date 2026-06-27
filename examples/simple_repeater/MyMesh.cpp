#include "MyMesh.h"
#include <algorithm>
#include <helpers/LowBatteryBootGuard.h>

/* ------------------------------ Config -------------------------------- */

#ifndef LORA_FREQ
  #define LORA_FREQ 915.0
#endif
#ifndef LORA_BW
  #define LORA_BW 250
#endif
#ifndef LORA_SF
  #define LORA_SF 10
#endif
#ifndef LORA_CR
  #define LORA_CR 5
#endif
#ifndef LORA_TX_POWER
  #define LORA_TX_POWER 20
#endif

#ifndef ADVERT_NAME
  #define ADVERT_NAME "repeater"
#endif
#ifndef ADVERT_LAT
  #define ADVERT_LAT 0.0
#endif
#ifndef ADVERT_LON
  #define ADVERT_LON 0.0
#endif

#ifndef ADMIN_PASSWORD
  #define ADMIN_PASSWORD "password"
#endif

#ifndef SERVER_RESPONSE_DELAY
  #define SERVER_RESPONSE_DELAY 300
#endif

#ifndef TXT_ACK_DELAY
  #define TXT_ACK_DELAY 200
#endif

#define FIRMWARE_VER_LEVEL       2

#define REQ_TYPE_GET_STATUS         0x01 // same as _GET_STATS
#define REQ_TYPE_KEEP_ALIVE         0x02
#define REQ_TYPE_GET_TELEMETRY_DATA 0x03
#define REQ_TYPE_GET_ACCESS_LIST    0x05
#define REQ_TYPE_GET_NEIGHBOURS     0x06
#define REQ_TYPE_GET_OWNER_INFO     0x07     // FIRMWARE_VER_LEVEL >= 2

#define RESP_SERVER_LOGIN_OK        0 // response to ANON_REQ

#define ANON_REQ_TYPE_REGIONS      0x01
#define ANON_REQ_TYPE_OWNER        0x02
#define ANON_REQ_TYPE_BASIC        0x03   // just remote clock

#define CLI_REPLY_DELAY_MILLIS      600

#define LAZY_CONTACTS_WRITE_DELAY    5000
#define DAILY_REBOOT_DEFAULT_HOURS   24
#define DAILY_REBOOT_MIN_HOURS       1
#define DAILY_REBOOT_MAX_HOURS       168
#define DAILY_REBOOT_GRACE_MILLIS    3000UL

#if defined(ESP32)
static portMUX_TYPE dense_stats_mux = portMUX_INITIALIZER_UNLOCKED;
#define DENSE_STATS_LOCK() portENTER_CRITICAL(&dense_stats_mux)
#define DENSE_STATS_UNLOCK() portEXIT_CRITICAL(&dense_stats_mux)
#else
#define DENSE_STATS_LOCK() noInterrupts()
#define DENSE_STATS_UNLOCK() interrupts()
#endif

static void addSaturating(uint16_t* value, uint16_t amount = 1) {
  uint16_t remaining = 0xFFFF - *value;
  *value = amount > remaining ? 0xFFFF : *value + amount;
}

static uint8_t calcDensityLevel(uint16_t neighbors, uint16_t dup_rx, uint16_t unique_rx) {
  uint32_t total = (uint32_t)dup_rx + unique_rx;
  uint8_t dup_pct = total == 0 ? 0 : (uint8_t)(((uint32_t)dup_rx * 100) / total);
  if (neighbors >= 24 || dup_pct >= 70) return 3;
  if (neighbors >= 12 || dup_pct >= 50) return 2;
  if (neighbors >= 4 || dup_pct >= 25) return 1;
  return 0;
}

static uint8_t calcCongestionLevel(uint32_t airtime_rx_ms, uint32_t airtime_tx_ms, uint16_t suppressed_tx) {
  uint32_t airtime_ms = airtime_rx_ms + airtime_tx_ms;
  if (airtime_ms >= 20000 || suppressed_tx >= 96) return 3;
  if (airtime_ms >= 8000 || suppressed_tx >= 32) return 2;
  if (airtime_ms >= 2000 || suppressed_tx >= 8) return 1;
  return 0;
}

static const char* levelName(uint8_t level) {
  if (level >= 3) return "high";
  if (level == 2) return "moderate";
  if (level == 1) return "low";
  return "ok";
}

static const char* healthName(uint8_t score) {
  if (score >= 80) return "good";
  if (score >= 60) return "fair";
  if (score >= 40) return "poor";
  return "bad";
}

static uint64_t hoursToMillis64(uint8_t hours) {
  return ((uint64_t)hours) * 60ULL * 60ULL * 1000ULL;
}

static const uint8_t public_group_secret[PUB_KEY_SIZE] = {
  0x8B, 0x33, 0x87, 0xE9, 0xC5, 0xCD, 0xEA, 0x6A,
  0xC9, 0xE5, 0xED, 0xBA, 0xA1, 0x15, 0xCD, 0x72,
  0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
};

void MyMesh::putNeighbour(const mesh::Identity &id, uint32_t timestamp, float snr) {
#if MAX_NEIGHBOURS // check if neighbours enabled
  // find existing neighbour, else use least recently updated
  uint32_t oldest_timestamp = 0xFFFFFFFF;
  NeighbourInfo *neighbour = &neighbours[0];
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    // if neighbour already known, we should update it
    if (id.matches(neighbours[i].id)) {
      neighbour = &neighbours[i];
      break;
    }

    // otherwise we should update the least recently updated neighbour
    if (neighbours[i].heard_timestamp < oldest_timestamp) {
      neighbour = &neighbours[i];
      oldest_timestamp = neighbour->heard_timestamp;
    }
  }

  // update neighbour info
  neighbour->id = id;
  neighbour->advert_timestamp = timestamp;
  neighbour->heard_timestamp = getRTCClock()->getCurrentTime();
  neighbour->snr = (int8_t)(snr * 4);
#endif
}

void MyMesh::rotateDenseStats() {
  unsigned long now = _ms->getMillis();
  if (dense_bucket_started == 0) dense_bucket_started = now;

  while (now - dense_bucket_started >= DENSE_MESH_BUCKET_MS) {
    dense_bucket_started += DENSE_MESH_BUCKET_MS;
    dense_bucket_idx = (dense_bucket_idx + 1) % DENSE_MESH_BUCKETS;
    memset(&dense_buckets[dense_bucket_idx], 0, sizeof(dense_buckets[dense_bucket_idx]));
  }
}

void MyMesh::clearDenseStatsLocked() {
  memset(&dense_stats, 0, sizeof(dense_stats));
  memset(dense_buckets, 0, sizeof(dense_buckets));
  dense_bucket_idx = 0;
  dense_bucket_started = _ms->getMillis();
}

void MyMesh::clearSpamStatsLocked() {
  memset(&spam_stats, 0, sizeof(spam_stats));
  StrHelper::strncpy(spam_stats.last_reason, "none", sizeof(spam_stats.last_reason));
}

void MyMesh::recordDenseUniqueRx() {
  DENSE_STATS_LOCK();
  rotateDenseStats();
  addSaturating(&dense_buckets[dense_bucket_idx].unique_rx);
  DENSE_STATS_UNLOCK();
}

void MyMesh::recordDenseDupRx() {
  DENSE_STATS_LOCK();
  rotateDenseStats();
  addSaturating(&dense_buckets[dense_bucket_idx].dup_rx);
  DENSE_STATS_UNLOCK();
}

void MyMesh::recordDenseSuppressedTx() {
  DENSE_STATS_LOCK();
  rotateDenseStats();
  addSaturating(&dense_buckets[dense_bucket_idx].suppressed_tx);
  DENSE_STATS_UNLOCK();
}

void MyMesh::recordDenseRxAirtime(uint32_t airtime_ms) {
  DENSE_STATS_LOCK();
  rotateDenseStats();
  dense_buckets[dense_bucket_idx].airtime_rx_ms += airtime_ms;
  DENSE_STATS_UNLOCK();
}

void MyMesh::recordDenseTxAirtime(uint32_t airtime_ms) {
  DENSE_STATS_LOCK();
  rotateDenseStats();
  dense_buckets[dense_bucket_idx].airtime_tx_ms += airtime_ms;
  DENSE_STATS_UNLOCK();
}

void MyMesh::recordSpamDrop(const char* reason, uint8_t score, uint8_t entropy) {
  DENSE_STATS_LOCK();
  spam_stats.malformed_dropped++;
  if (strcmp(reason, "low_confidence") == 0) {
    spam_stats.spam_dropped++;
  } else if (strcmp(reason, "short") == 0) {
    spam_stats.short_dropped++;
  } else if (strcmp(reason, "type") == 0) {
    spam_stats.type_dropped++;
  } else if (strcmp(reason, "empty_text") == 0) {
    spam_stats.empty_dropped++;
  } else if (strcmp(reason, "invalid_utf8") == 0) {
    spam_stats.invalid_utf8_dropped++;
  } else if (strcmp(reason, "timestamp") == 0) {
    spam_stats.timestamp_dropped++;
  }
  spam_stats.last_score = score;
  spam_stats.last_entropy = entropy;
  StrHelper::strncpy(spam_stats.last_reason, reason, sizeof(spam_stats.last_reason));
  DENSE_STATS_UNLOCK();
}

uint16_t MyMesh::getDenseNeighborCount() const {
#if MAX_NEIGHBOURS
  uint16_t count = 0;
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    if (neighbours[i].heard_timestamp != 0) count++;
  }
  return count;
#else
  return 0;
#endif
}

void MyMesh::getDenseStats(dense_mesh_stats_t* stats) {
  memset(stats, 0, sizeof(*stats));
  DENSE_STATS_LOCK();
  rotateDenseStats();
  for (int i = 0; i < DENSE_MESH_BUCKETS; i++) {
    addSaturating(&stats->dup_rx, dense_buckets[i].dup_rx);
    addSaturating(&stats->unique_rx, dense_buckets[i].unique_rx);
    addSaturating(&stats->suppressed_tx, dense_buckets[i].suppressed_tx);
    stats->airtime_rx_ms += dense_buckets[i].airtime_rx_ms;
    stats->airtime_tx_ms += dense_buckets[i].airtime_tx_ms;
  }
  DENSE_STATS_UNLOCK();

  stats->neighbors = getDenseNeighborCount();
  stats->density_level = calcDensityLevel(stats->neighbors, stats->dup_rx, stats->unique_rx);
  stats->congestion_level = calcCongestionLevel(stats->airtime_rx_ms, stats->airtime_tx_ms, stats->suppressed_tx);
}

uint8_t MyMesh::handleLoginReq(const mesh::Identity& sender, const uint8_t* secret, uint32_t sender_timestamp, const uint8_t* data, bool is_flood) {
  ClientInfo* client = NULL;
  if (data[0] == 0) {   // blank password, just check if sender is in ACL
    client = acl.getClient(sender.pub_key, PUB_KEY_SIZE);
    if (client == NULL) {
    #if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Login, sender not in ACL");
    #endif
    }
  }
  if (client == NULL) {
    uint8_t perms;
    if (strcmp((char *)data, _prefs.password) == 0) { // check for valid admin password
      perms = PERM_ACL_ADMIN;
    } else if (strcmp((char *)data, _prefs.guest_password) == 0) { // check guest password
      perms = PERM_ACL_GUEST;
    } else {
#if MESH_DEBUG
      MESH_DEBUG_PRINTLN("Invalid password: %s", data);
#endif
      return 0;
    }

    client = acl.putClient(sender, 0);  // add to contacts (if not already known)
    if (sender_timestamp <= client->last_timestamp) {
      MESH_DEBUG_PRINTLN("Possible login replay attack!");
      return 0;  // FATAL: client table is full -OR- replay attack
    }

    MESH_DEBUG_PRINTLN("Login success!");
    client->last_timestamp = sender_timestamp;
    client->last_activity = getRTCClock()->getCurrentTime();
    client->permissions &= ~0x03;
    client->permissions |= perms;
    memcpy(client->shared_secret, secret, PUB_KEY_SIZE);

    if (perms != PERM_ACL_GUEST) {   // keep number of FS writes to a minimum
      dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);
    }
  }

  if (is_flood) {
    client->out_path_len = OUT_PATH_UNKNOWN;  // need to rediscover out_path
  }

  uint32_t now = getRTCClock()->getCurrentTimeUnique();
  memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  reply_data[4] = RESP_SERVER_LOGIN_OK;
  reply_data[5] = 0;  // Legacy: was recommended keep-alive interval (secs / 16)
  reply_data[6] = client->isAdmin() ? 1 : 0;
  reply_data[7] = client->permissions;
  getRNG()->random(&reply_data[8], 4);   // random blob to help packet-hash uniqueness
  reply_data[12] = FIRMWARE_VER_LEVEL;  // New field

  return 13;  // reply length
}

uint8_t MyMesh::handleAnonRegionsReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)

    return 8 + region_map.exportNamesTo((char *) &reply_data[8], sizeof(reply_data) - 12, REGION_DENY_FLOOD);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonOwnerReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    sprintf((char *) &reply_data[8], "%s\n%s", _prefs.node_name, _prefs.owner_info);

    return 8 + strlen((char *) &reply_data[8]);   // reply length
  }
  return 0;
}

uint8_t MyMesh::handleAnonClockReq(const mesh::Identity& sender, uint32_t sender_timestamp, const uint8_t* data) {
  if (anon_limiter.allow(rtc_clock.getCurrentTime())) {
    // request data has: {reply-path-len}{reply-path}
    reply_path_len = *data & 63;
    reply_path_hash_size = (*data >> 6) + 1;
    data++;

    memcpy(reply_path, data, ((uint8_t)reply_path_len) * reply_path_hash_size);
    // data += (uint8_t)reply_path_len * reply_path_hash_size;

    memcpy(reply_data, &sender_timestamp, 4);   // prefix with sender_timestamp, like a tag
    uint32_t now = getRTCClock()->getCurrentTime();
    memcpy(&reply_data[4], &now, 4);     // include our clock (for easy clock sync, and packet hash uniqueness)
    reply_data[8] = 0;  // features
#ifdef WITH_RS232_BRIDGE
    reply_data[8] |= 0x01;  // is bridge, type UART
#elif WITH_ESPNOW_BRIDGE
    reply_data[8] |= 0x03;  // is bridge, type ESP-NOW
#endif
    if (_prefs.disable_fwd) {   // is this repeater currently disabled
      reply_data[8] |= 0x80;  // is disabled
    }
    // TODO:  add some kind of moving-window utilisation metric, so can query 'how busy' is this repeater
    return 9;   // reply length
  }
  return 0;
}

int MyMesh::handleRequest(ClientInfo *sender, uint32_t sender_timestamp, uint8_t *payload, size_t payload_len) {
  // uint32_t now = getRTCClock()->getCurrentTimeUnique();
  // memcpy(reply_data, &now, 4);   // response packets always prefixed with timestamp
  memcpy(reply_data, &sender_timestamp, 4); // reflect sender_timestamp back in response packet (kind of like a 'tag')

  if (payload[0] == REQ_TYPE_GET_STATUS) {  // guests can also access this now
    RepeaterStats stats;
    stats.batt_milli_volts = board.getBattMilliVolts();
    stats.curr_tx_queue_len = _mgr->getOutboundTotal();
    stats.noise_floor = (int16_t)_radio->getNoiseFloor();
    stats.last_rssi = (int16_t)radio_driver.getLastRSSI();
    stats.n_packets_recv = radio_driver.getPacketsRecv();
    stats.n_packets_sent = radio_driver.getPacketsSent();
    stats.total_air_time_secs = getTotalAirTime() / 1000;
    stats.total_up_time_secs = uptime_millis / 1000;
    stats.n_sent_flood = getNumSentFlood();
    stats.n_sent_direct = getNumSentDirect();
    stats.n_recv_flood = getNumRecvFlood();
    stats.n_recv_direct = getNumRecvDirect();
    stats.err_events = _err_flags;
    stats.last_snr = (int16_t)(radio_driver.getLastSNR() * 4);
    stats.n_direct_dups = ((SimpleMeshTables *)getTables())->getNumDirectDups();
    stats.n_flood_dups = ((SimpleMeshTables *)getTables())->getNumFloodDups();
    stats.total_rx_air_time_secs = getReceiveAirTime() / 1000;
    stats.n_recv_errors = radio_driver.getPacketsRecvErrors();
    memcpy(&reply_data[4], &stats, sizeof(stats));

    return 4 + sizeof(stats); //  reply_len
  }
  if (payload[0] == REQ_TYPE_GET_TELEMETRY_DATA) {
    uint8_t perm_mask = ~(payload[1]); // NEW: first reserved byte (of 4), is now inverse mask to apply to permissions

    telemetry.reset();
    telemetry.addVoltage(TELEM_CHANNEL_SELF, (float)board.getBattMilliVolts() / 1000.0f);

    // query other sensors -- target specific
    if ((sender->permissions & PERM_ACL_ROLE_MASK) == PERM_ACL_GUEST) {
      perm_mask = 0x00;  // just base telemetry allowed
    }
    sensors.querySensors(perm_mask, telemetry);

	// This default temperature will be overridden by external sensors (if any)
    float temperature = board.getMCUTemperature();
    if(!isnan(temperature)) { // Supported boards with built-in temperature sensor. ESP32-C3 may return NAN
      telemetry.addTemperature(TELEM_CHANNEL_SELF, temperature); // Built-in MCU Temperature
    }

    uint8_t tlen = telemetry.getSize();
    memcpy(&reply_data[4], telemetry.getBuffer(), tlen);
    return 4 + tlen; // reply_len
  }
  if (payload[0] == REQ_TYPE_GET_ACCESS_LIST && sender->isAdmin()) {
    uint8_t res1 = payload[1];   // reserved for future  (extra query params)
    uint8_t res2 = payload[2];
    if (res1 == 0 && res2 == 0) {
      uint8_t ofs = 4;
      for (int i = 0; i < acl.getNumClients() && ofs + 7 <= sizeof(reply_data) - 4; i++) {
        auto c = acl.getClientByIdx(i);
        if (c->permissions == 0) continue;  // skip deleted entries
        memcpy(&reply_data[ofs], c->id.pub_key, 6); ofs += 6;  // just 6-byte pub_key prefix
        reply_data[ofs++] = c->permissions;
      }
      return ofs;
    }
  }
  if (payload[0] == REQ_TYPE_GET_NEIGHBOURS) {
    uint8_t request_version = payload[1];
    if (request_version == 0) {

      // reply data offset (after response sender_timestamp/tag)
      int reply_offset = 4;

      // get request params
      uint8_t count = payload[2]; // how many neighbours to fetch (0-255)
      uint16_t offset;
      memcpy(&offset, &payload[3], 2); // offset from start of neighbours list (0-65535)
      uint8_t order_by = payload[5]; // how to order neighbours. 0=newest_to_oldest, 1=oldest_to_newest, 2=strongest_to_weakest, 3=weakest_to_strongest
      uint8_t pubkey_prefix_length = payload[6]; // how many bytes of neighbour pub key we want
      // we also send a 4 byte random blob in payload[7...10] to help packet uniqueness

      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS count=%d, offset=%d, order_by=%d, pubkey_prefix_length=%d", count, offset, order_by, pubkey_prefix_length);

      // clamp pub key prefix length to max pub key length
      if(pubkey_prefix_length > PUB_KEY_SIZE){
        pubkey_prefix_length = PUB_KEY_SIZE;
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS invalid pubkey_prefix_length=%d clamping to %d", pubkey_prefix_length, PUB_KEY_SIZE);
      }

      // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
      int16_t neighbours_count = 0;
#if MAX_NEIGHBOURS
      NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
      for (int i = 0; i < MAX_NEIGHBOURS; i++) {
        auto neighbour = &neighbours[i];
        if (neighbour->heard_timestamp > 0) {
          sorted_neighbours[neighbours_count] = neighbour;
          neighbours_count++;
        }
      }

      // sort neighbours based on order
      if (order_by == 0) {
        // sort by newest to oldest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting newest to oldest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->heard_timestamp > b->heard_timestamp; // desc
        });
      } else if (order_by == 1) {
        // sort by oldest to newest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting oldest to newest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->heard_timestamp < b->heard_timestamp; // asc
        });
      } else if (order_by == 2) {
        // sort by strongest to weakest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting strongest to weakest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->snr > b->snr; // desc
        });
      } else if (order_by == 3) {
        // sort by weakest to strongest
        MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS sorting weakest to strongest");
        std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
          return a->snr < b->snr; // asc
        });
      }
#endif

      // build results buffer
      int results_count = 0;
      int results_offset = 0;
      uint8_t results_buffer[130];
      for(int index = 0; index < count && index + offset < neighbours_count; index++){
        
        // stop if we can't fit another entry in results
        int entry_size = pubkey_prefix_length + 4 + 1;
        if(results_offset + entry_size > sizeof(results_buffer)){
          MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS no more entries can fit in results buffer");
          break;
        }

#if MAX_NEIGHBOURS
        // add next neighbour to results
        auto neighbour = sorted_neighbours[index + offset];
        uint32_t heard_seconds_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
        memcpy(&results_buffer[results_offset], neighbour->id.pub_key, pubkey_prefix_length); results_offset += pubkey_prefix_length;
        memcpy(&results_buffer[results_offset], &heard_seconds_ago, 4); results_offset += 4;
        memcpy(&results_buffer[results_offset], &neighbour->snr, 1); results_offset += 1;
        results_count++;
#endif

      }

      // build reply
      MESH_DEBUG_PRINTLN("REQ_TYPE_GET_NEIGHBOURS neighbours_count=%d results_count=%d", neighbours_count, results_count);
      memcpy(&reply_data[reply_offset], &neighbours_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_count, 2); reply_offset += 2;
      memcpy(&reply_data[reply_offset], &results_buffer, results_offset); reply_offset += results_offset;

      return reply_offset;
    }
  } else if (payload[0] == REQ_TYPE_GET_OWNER_INFO) {
    sprintf((char *) &reply_data[4], "%s\n%s\n%s", FIRMWARE_VERSION, _prefs.node_name, _prefs.owner_info);
    return 4 + strlen((char *) &reply_data[4]);
  }
  return 0; // unknown command
}

mesh::Packet *MyMesh::createSelfAdvert() {
  uint8_t app_data[MAX_ADVERT_DATA_SIZE];
  uint8_t app_data_len = _cli.buildAdvertData(ADV_TYPE_REPEATER, app_data);

  return createAdvert(self_id, app_data, app_data_len);
}

File MyMesh::openAppend(const char *fname) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return _fs->open(fname, FILE_O_WRITE);
#elif defined(RP2040_PLATFORM)
  return _fs->open(fname, "a");
#else
  return _fs->open(fname, "a", true);
#endif
}

static uint8_t max_loop_minimal[] =  { 0, /* 1-byte */  4, /* 2-byte */  2, /* 3-byte */  1 };
static uint8_t max_loop_moderate[] = { 0, /* 1-byte */  2, /* 2-byte */  1, /* 3-byte */  1 };
static uint8_t max_loop_strict[] =   { 0, /* 1-byte */  1, /* 2-byte */  1, /* 3-byte */  1 };

bool MyMesh::isLooped(const mesh::Packet* packet, const uint8_t max_counters[]) {
  uint8_t hash_size = packet->getPathHashSize();
  uint8_t hash_count = packet->getPathHashCount();
  uint8_t n = 0;
  const uint8_t* path = packet->path;
  while (hash_count > 0) {      // count how many times this node is already in the path
    if (self_id.isHashMatch(path, hash_size)) n++;
    hash_count--;
    path += hash_size;
  }
  return n >= max_counters[hash_size];
}

static bool parseDurationSeconds(const char* text, uint32_t* seconds) {
  if (text == NULL || *text == 0) {
    *seconds = 3600;
    return true;
  }

  char* end = NULL;
  unsigned long value = strtoul(text, &end, 10);
  if (end == text || value == 0) return false;

  if (*end == 'm' && end[1] == 0) value *= 60UL;
  else if (*end == 'h' && end[1] == 0) value *= 3600UL;
  else if (*end == 'd' && end[1] == 0) value *= 86400UL;
  else if (*end != 0) return false;

  if (value > 86400UL * 7UL) value = 86400UL * 7UL;
  *seconds = (uint32_t)value;
  return true;
}

void MyMesh::clearExpiredPathBlocks() {
  uint32_t now = getRTCClock()->getCurrentTime();
  for (int i = 0; i < MAX_PATH_BLOCKS; i++) {
    if (path_blocks[i].hop_count > 0 && path_blocks[i].expires_at != 0 && path_blocks[i].expires_at <= now) {
      memset(&path_blocks[i], 0, sizeof(path_blocks[i]));
    }
  }
}

bool MyMesh::parsePathBlockSpec(const char* spec, PathBlockEntry* entry) const {
  memset(entry, 0, sizeof(*entry));
  if (spec == NULL || *spec == 0) return false;

  uint8_t hash_size = 0;
  uint8_t hop_count = 0;
  const char* pos = spec;

  while (*pos != 0) {
    if (hop_count >= PATH_BLOCK_MAX_HOPS) return false;

    const char* slash = strchr(pos, '/');
    size_t hex_len = slash ? (size_t)(slash - pos) : strlen(pos);
    if (hex_len != 2 && hex_len != 4 && hex_len != 6) return false;

    uint8_t this_hash_size = hex_len / 2;
    if (hash_size == 0) {
      hash_size = this_hash_size;
    } else if (this_hash_size != hash_size) {
      return false;
    }

    char hex[7];
    memcpy(hex, pos, hex_len);
    hex[hex_len] = 0;
    for (size_t i = 0; i < hex_len; i++) {
      if (!mesh::Utils::isHexChar(hex[i])) return false;
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

bool MyMesh::pathBlockMatches(const mesh::Packet* packet, const PathBlockEntry& entry) const {
  if (entry.hop_count == 0) return false;
  if (!packet->isRouteFlood()) return false;
  if (packet->getPathHashSize() != entry.hash_size) return false;
  if (packet->getPathHashCount() < entry.hop_count) return false;

  uint8_t packet_hops = packet->getPathHashCount();
  uint8_t hash_size = packet->getPathHashSize();
  for (uint8_t start = 0; start + entry.hop_count <= packet_hops; start++) {
    bool match = true;
    for (uint8_t hop = 0; hop < entry.hop_count; hop++) {
      const uint8_t* packet_hash = &packet->path[(start + hop) * hash_size];
      const uint8_t* block_hash = &entry.path[hop * PATH_BLOCK_MAX_HASH_SIZE];
      if (memcmp(packet_hash, block_hash, hash_size) != 0) {
        match = false;
        break;
      }
    }
    if (match) return true;
  }
  return false;
}

bool MyMesh::shouldBlockPath(const mesh::Packet* packet) {
  clearExpiredPathBlocks();
  for (int i = 0; i < MAX_PATH_BLOCKS; i++) {
    if (pathBlockMatches(packet, path_blocks[i])) {
      path_blocks[i].drops++;
      MESH_DEBUG_PRINTLN("allowPacketForward: path.block matched entry=%d", i);
      return true;
    }
  }
  return false;
}

void MyMesh::clearExpiredNodeBlocks() {
  uint32_t now = getRTCClock()->getCurrentTime();
  for (int i = 0; i < MAX_NODE_BLOCKS; i++) {
    if (node_blocks[i].active && node_blocks[i].expires_at != 0 && node_blocks[i].expires_at <= now) {
      memset(&node_blocks[i], 0, sizeof(node_blocks[i]));
    }
  }
}

bool MyMesh::sourceShortId(const mesh::Packet* packet, uint8_t* id) const {
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

bool MyMesh::shouldBlockNode(const mesh::Packet* packet) {
  clearExpiredNodeBlocks();
  uint8_t id = 0;
  if (!sourceShortId(packet, &id)) return false;
  for (int i = 0; i < MAX_NODE_BLOCKS; i++) {
    if (node_blocks[i].active && node_blocks[i].id == id) {
      node_blocks[i].drops++;
      MESH_DEBUG_PRINTLN("allowPacketForward: node.block matched id=%02x entry=%d", id, i);
      return true;
    }
  }
  return false;
}

bool MyMesh::shouldBlockBridgeOrRepeater(const mesh::Packet* packet) {
  return shouldBlockNode(packet) || shouldBlockPath(packet);
}

bool MyMesh::isLikelyNearbyClientFlood(const mesh::Packet* packet) const {
  if (!packet || !packet->isRouteFlood()) return false;
  if (packet->wasReceivedFromBridge()) return false;
  if (packet->getPathHashCount() > _prefs.nearby_client_suppress_max_hops) return false;

  uint8_t type = packet->getPayloadType();
  if (type == PAYLOAD_TYPE_ADVERT) {
    const size_t app_data_offset = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE;
    if (packet->payload_len <= app_data_offset) return false;
    uint8_t adv_type = packet->payload[app_data_offset] & 0x0F;
    return adv_type == ADV_TYPE_CHAT;
  }

  return type == PAYLOAD_TYPE_GRP_TXT || type == PAYLOAD_TYPE_GRP_DATA ||
         type == PAYLOAD_TYPE_LOCATION || type == PAYLOAD_TYPE_RAW_CUSTOM;
}

bool MyMesh::shouldSuppressNearbyClientFlood(const mesh::Packet* packet) const {
  if (!_prefs.nearby_client_suppress_enabled) return false;
  if (!isLikelyNearbyClientFlood(packet)) return false;
  return packet->getRSSI() >= _prefs.nearby_client_suppress_rssi_dbm;
}

void MyMesh::formatNodeBlocksReply(char* reply) {
  clearExpiredNodeBlocks();
  uint32_t now = getRTCClock()->getCurrentTime();
  strcpy(reply, "> ");
  size_t used = 2;
  bool any = false;
  for (int i = 0; i < MAX_NODE_BLOCKS && used < 150; i++) {
    const NodeBlockEntry& entry = node_blocks[i];
    if (!entry.active) continue;
    if (any && used < 158) reply[used++] = ';';
    if (any && used < 158) reply[used++] = ' ';
    any = true;
    uint32_t ttl = entry.expires_at > now ? entry.expires_at - now : 0;
    used += snprintf(&reply[used], 160 - used, "%02x %lus %lu",
                     entry.id, (unsigned long)ttl, (unsigned long)entry.drops);
  }
  if (!any) {
    strcpy(reply, "> empty");
  } else {
    if (used > 159) used = 159;
    reply[used] = 0;
  }
}

void MyMesh::handleNodeBlockCommand(char* command, char* reply) {
  clearExpiredNodeBlocks();

  if (strcmp(command, "get node.block") == 0) {
    formatNodeBlocksReply(reply);
    return;
  }
  if (strcmp(command, "clear node.block") == 0 || strcmp(command, "set node.block clear") == 0) {
    memset(node_blocks, 0, sizeof(node_blocks));
    strcpy(reply, "OK");
    return;
  }

  if (memcmp(command, "set node.block add ", 19) == 0) {
    char* spec = &command[19];
    char* ttl_text = strchr(spec, ' ');
    if (ttl_text) {
      *ttl_text++ = 0;
      while (*ttl_text == ' ') ttl_text++;
    }
    if (strlen(spec) != 2 || !mesh::Utils::isHexChar(spec[0]) || !mesh::Utils::isHexChar(spec[1])) {
      strcpy(reply, "Error");
      return;
    }
    uint8_t id = 0;
    if (!mesh::Utils::fromHex(&id, 1, spec)) {
      strcpy(reply, "Error");
      return;
    }
    uint32_t ttl_secs;
    if (!parseDurationSeconds(ttl_text, &ttl_secs)) {
      strcpy(reply, "Error");
      return;
    }
    int slot = -1;
    for (int i = 0; i < MAX_NODE_BLOCKS; i++) {
      if (!node_blocks[i].active && slot < 0) slot = i;
      if (node_blocks[i].active && node_blocks[i].id == id) {
        slot = i;
        break;
      }
    }
    if (slot < 0) {
      strcpy(reply, "Error");
      return;
    }
    node_blocks[slot].id = id;
    node_blocks[slot].active = 1;
    node_blocks[slot].expires_at = getRTCClock()->getCurrentTime() + ttl_secs;
    node_blocks[slot].drops = 0;
    strcpy(reply, "OK");
    return;
  }

  if (memcmp(command, "set node.block del ", 19) == 0) {
    char* spec = &command[19];
    if (strlen(spec) != 2 || !mesh::Utils::isHexChar(spec[0]) || !mesh::Utils::isHexChar(spec[1])) {
      strcpy(reply, "Error");
      return;
    }
    uint8_t id = 0;
    if (!mesh::Utils::fromHex(&id, 1, spec)) {
      strcpy(reply, "Error");
      return;
    }
    for (int i = 0; i < MAX_NODE_BLOCKS; i++) {
      if (node_blocks[i].active && node_blocks[i].id == id) {
        memset(&node_blocks[i], 0, sizeof(node_blocks[i]));
        strcpy(reply, "OK");
        return;
      }
    }
    strcpy(reply, "Error");
    return;
  }

  strcpy(reply, "Error");
}

void MyMesh::formatPathBlocksReply(char* reply) {
  clearExpiredPathBlocks();
  uint32_t now = getRTCClock()->getCurrentTime();
  strcpy(reply, "> ");
  size_t used = 2;
  bool any = false;

  for (int i = 0; i < MAX_PATH_BLOCKS && used < 150; i++) {
    const PathBlockEntry& entry = path_blocks[i];
    if (entry.hop_count == 0) continue;

    if (any && used < 158) reply[used++] = ';';
    if (any && used < 158) reply[used++] = ' ';
    any = true;

    for (uint8_t hop = 0; hop < entry.hop_count && used < 150; hop++) {
      if (hop > 0 && used < 158) reply[used++] = '/';
      mesh::Utils::toHex(&reply[used], &entry.path[hop * PATH_BLOCK_MAX_HASH_SIZE], entry.hash_size);
      used += entry.hash_size * 2;
    }

    uint32_t ttl = entry.expires_at > now ? entry.expires_at - now : 0;
    used += snprintf(&reply[used], 160 - used, " %lus %lu", (unsigned long)ttl, (unsigned long)entry.drops);
  }

  if (!any) {
    strcpy(reply, "> empty");
  } else {
    if (used > 159) used = 159;
    reply[used] = 0;
  }
}

void MyMesh::handlePathBlockCommand(char* command, char* reply) {
  clearExpiredPathBlocks();

  if (strcmp(command, "get path.block") == 0) {
    formatPathBlocksReply(reply);
    return;
  }

  if (strcmp(command, "clear path.block") == 0 || strcmp(command, "set path.block clear") == 0) {
    memset(path_blocks, 0, sizeof(path_blocks));
    strcpy(reply, "OK");
    return;
  }

  if (memcmp(command, "set path.block add ", 19) == 0) {
    char* spec = &command[19];
    char* ttl_text = strchr(spec, ' ');
    if (ttl_text) {
      *ttl_text++ = 0;
      while (*ttl_text == ' ') ttl_text++;
    }

    PathBlockEntry entry;
    uint32_t ttl_secs;
    if (!parsePathBlockSpec(spec, &entry)) {
      strcpy(reply, "Error: expected aa, aa/bb, or aa/bb/cc");
      return;
    }
    if (!parseDurationSeconds(ttl_text, &ttl_secs)) {
      strcpy(reply, "Error");
      return;
    }

    int slot = -1;
    for (int i = 0; i < MAX_PATH_BLOCKS; i++) {
      if (path_blocks[i].hop_count == 0 && slot < 0) slot = i;
      if (path_blocks[i].hop_count == entry.hop_count
          && path_blocks[i].hash_size == entry.hash_size
          && memcmp(path_blocks[i].path, entry.path, entry.hop_count * PATH_BLOCK_MAX_HASH_SIZE) == 0) {
        slot = i;
        break;
      }
    }

    if (slot < 0) {
      strcpy(reply, "Error");
      return;
    }

    entry.expires_at = getRTCClock()->getCurrentTime() + ttl_secs;
    path_blocks[slot] = entry;
    strcpy(reply, "OK");
    return;
  }

  if (memcmp(command, "set path.block del ", 19) == 0) {
    PathBlockEntry entry;
    if (!parsePathBlockSpec(&command[19], &entry)) {
      strcpy(reply, "Error: expected aa, aa/bb, or aa/bb/cc");
      return;
    }

    for (int i = 0; i < MAX_PATH_BLOCKS; i++) {
      if (path_blocks[i].hop_count == entry.hop_count
          && path_blocks[i].hash_size == entry.hash_size
          && memcmp(path_blocks[i].path, entry.path, entry.hop_count * PATH_BLOCK_MAX_HASH_SIZE) == 0) {
        memset(&path_blocks[i], 0, sizeof(path_blocks[i]));
        strcpy(reply, "OK");
        return;
      }
    }
    strcpy(reply, "Error");
    return;
  }

  strcpy(reply, "Error");
}

void MyMesh::sendFloodReply(mesh::Packet* packet, unsigned long delay_millis, uint8_t path_hash_size) {
  if (recv_pkt_region && !recv_pkt_region->isWildcard()) {  // if _request_ packet scope is known, send reply with same scope
    TransportKey scope;
    if (region_map.getTransportKeysFor(*recv_pkt_region, &scope, 1) > 0) {
      sendFloodScoped(scope, packet, delay_millis, path_hash_size);
    } else {
      sendFlood(packet, delay_millis, path_hash_size);  // send un-scoped
    }
  } else {
    sendFlood(packet, delay_millis, path_hash_size);  // send un-scoped
  }
}

bool MyMesh::allowPacketForward(const mesh::Packet *packet) {
  const bool is_flood_advert = packet->isRouteFlood() && packet->getPayloadType() == PAYLOAD_TYPE_ADVERT;
  const bool is_bridge_flood = packet->isRouteFlood() && packet->wasReceivedFromBridge();
  const bool bridge_gateway = _prefs.bridge_enabled && _prefs.bridge_rf != BRIDGE_RF_OFF;
  const bool tcp_to_rf = bridge_gateway && is_bridge_flood;

  if (packet->isRouteFlood() && isFloodPathAtRelayLimit(packet)) {
    if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
    recordDenseSuppressedTx();
    return false;
  }

  // Bridge gateway: no RF->RF mesh repeat — only TCP->RF injection.
  if (bridge_gateway && packet->isRouteFlood() && !packet->wasReceivedFromBridge()) {
    if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
    recordDenseSuppressedTx();
    return false;
  }

  if (is_bridge_flood && _prefs.bridge_rf == BRIDGE_RF_OFF) {
    MESH_DEBUG_PRINTLN("allowPacketForward: bridge RF forwarding is off");
    return false;
  }

  if (shouldBlockBridgeOrRepeater(packet)) {
    if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
    recordDenseSuppressedTx();
    return false;
  }

  if (shouldSuppressNearbyClientFlood(packet)) {
    MESH_DEBUG_PRINTLN("allowPacketForward: nearby client flood suppressed rssi=%d threshold=%d hops=%u",
                       (int)packet->getRSSI(),
                       (int)_prefs.nearby_client_suppress_rssi_dbm,
                       (uint32_t)packet->getPathHashCount());
    if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
    recordDenseSuppressedTx();
    return false;
  }

  if (_prefs.disable_fwd && !tcp_to_rf) {
    if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
    return false;
  }
  if (packet->isRouteFlood() && recv_pkt_region == NULL) {
    if (!tcp_to_rf) {
      MESH_DEBUG_PRINTLN("allowPacketForward: unknown transport code, or wildcard not allowed for FLOOD packet");
      if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
      return false;
    }
  }
  if (is_flood_advert && !tcp_to_rf) {
    uint8_t hops = packet->getPathHashCount();
    float forward_prob = pow(_prefs.flood_advert_base, hops > 0 ? hops - 1 : 0);
    if (getRNG()->nextInt(0, 10000) >= (uint32_t)(forward_prob * 10000.0f)) {
      dense_stats.n_drop_flood_adverts++;
      recordDenseSuppressedTx();
      return false;
    }
  }
  if (packet->isRouteFlood() && _prefs.loop_detect != LOOP_DETECT_OFF && !tcp_to_rf) {
    const uint8_t* maximums;
    if (_prefs.loop_detect == LOOP_DETECT_MINIMAL) {
      maximums = max_loop_minimal;
    } else if (_prefs.loop_detect == LOOP_DETECT_MODERATE) {
      maximums = max_loop_moderate;
    } else {
      maximums = max_loop_strict;
    }
    if (isLooped(packet, maximums)) {
      MESH_DEBUG_PRINTLN("allowPacketForward: FLOOD packet loop detected!");
      if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
      return false;
    }
  }
  if (packet->isRouteFlood() && _prefs.flood_relay_prob < 255 && !tcp_to_rf) {
    if (_prefs.flood_relay_prob == 0 || getRNG()->nextInt(0, 256) >= _prefs.flood_relay_prob) {
      recordDenseSuppressedTx();
      if (is_flood_advert) dense_stats.n_drop_flood_adverts++;
      return false;
    }
  }
  if (is_flood_advert) dense_stats.n_fwd_flood_adverts++;
  return true;
}

const char *MyMesh::getLogDateTime() {
  static char tmp[32];
  uint32_t now = getRTCClock()->getCurrentTime();
  DateTime dt = DateTime(now);
  sprintf(tmp, "%02d:%02d:%02d - %d/%d/%d U", dt.hour(), dt.minute(), dt.second(), dt.day(), dt.month(),
          dt.year());
  return tmp;
}

void MyMesh::logRxRaw(float snr, float rssi, const uint8_t raw[], int len) {
#ifdef WITH_MQTT_BRIDGE
  // Capture raw radio bytes + real SNR/RSSI for the MQTT bridge (packets/raw uplink).
  if (mqtt_bridge) mqtt_bridge->storeRawRadioData(raw, len, snr, rssi);
#endif
#if MESH_PACKET_LOGGING
  Serial.print(getLogDateTime());
  Serial.print(" RAW: ");
  mesh::Utils::printHex(Serial, raw, len);
  Serial.println();
#endif
}

void MyMesh::logRx(mesh::Packet *pkt, int len, float score) {
  bool bridge_blocked = shouldBlockBridgeOrRepeater(pkt);
#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge: always feed RX packets — the bridge decides based on mqtt.rx.
  if (!bridge_blocked && mqtt_bridge) mqtt_bridge->sendPacket(pkt);
#endif
#if defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
  if (!bridge_blocked && (_prefs.bridge_pkt_src == 1 || _prefs.bridge_pkt_src == 2)) {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    tcp_bridge.sendPacket(pkt);
    ble_bridge.sendPacket(pkt);
#else
    bridge.sendPacket(pkt);
#endif
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": RX, len=%d (type=%d, route=%s, payload_len=%d) SNR=%d RSSI=%d score=%d", len,
               pkt->getPayloadType(), pkt->isRouteDirect() ? "D" : "F", pkt->payload_len,
               (int)_radio->getLastSNR(), (int)_radio->getLastRSSI(), (int)(score * 1000));

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTx(mesh::Packet *pkt, int len) {
#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge: always feed TX packets — the bridge decides based on mqtt.tx (on/advert/off).
  if (mqtt_bridge) mqtt_bridge->sendPacket(pkt);
#endif
#if defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
  if (_prefs.bridge_pkt_src == 0 || _prefs.bridge_pkt_src == 2) {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    tcp_bridge.sendPacket(pkt);
    ble_bridge.sendPacket(pkt);
#else
    bridge.sendPacket(pkt);
#endif
  }
#endif

  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX, len=%d (type=%d, route=%s, payload_len=%d)", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);

      if (pkt->getPayloadType() == PAYLOAD_TYPE_PATH || pkt->getPayloadType() == PAYLOAD_TYPE_REQ ||
          pkt->getPayloadType() == PAYLOAD_TYPE_RESPONSE || pkt->getPayloadType() == PAYLOAD_TYPE_TXT_MSG) {
        f.printf(" [%02X -> %02X]\n", (uint32_t)pkt->payload[1], (uint32_t)pkt->payload[0]);
      } else {
        f.printf("\n");
      }
      f.close();
    }
  }
}

void MyMesh::logTxFail(mesh::Packet *pkt, int len) {
  if (_logging) {
    File f = openAppend(PACKET_LOG_FILE);
    if (f) {
      f.print(getLogDateTime());
      f.printf(": TX FAIL!, len=%d (type=%d, route=%s, payload_len=%d)\n", len, pkt->getPayloadType(),
               pkt->isRouteDirect() ? "D" : "F", pkt->payload_len);
      f.close();
    }
  }
}

void MyMesh::onRxAirTime(uint32_t air_time_ms) {
  recordDenseRxAirtime(air_time_ms);
}

void MyMesh::onTxAirTime(uint32_t air_time_ms) {
  recordDenseTxAirtime(air_time_ms);
}

void MyMesh::onPacketSeen(mesh::Packet* packet, bool duplicate) {
  if (!packet->isRouteFlood()) return;
  if (duplicate) {
    recordDenseDupRx();
  } else {
    recordDenseUniqueRx();
  }
}

int MyMesh::calcRxDelay(float score, uint32_t air_time) const {
  if (_prefs.rx_delay_base <= 0.0f) return 0;
  return (int)((pow(_prefs.rx_delay_base, 0.85f - score) - 1.0) * air_time);
}

bool MyMesh::shouldDropMalformedGroupText(mesh::Packet* pkt) {
  if (!pkt->isRouteFlood() || pkt->getPayloadType() != PAYLOAD_TYPE_GRP_TXT) {
    return false;
  }
  if (pkt->payload_len <= PATH_HASH_SIZE + CIPHER_MAC_SIZE) return false;

  uint8_t public_hash[PATH_HASH_SIZE];
  mesh::Utils::sha256(public_hash, sizeof(public_hash), public_group_secret, 16);
  if (memcmp(pkt->payload, public_hash, sizeof(public_hash)) != 0) return false;

  DENSE_STATS_LOCK();
  spam_stats.public_group_seen++;
  DENSE_STATS_UNLOCK();

  if (!_prefs.malformed_drop) {
    return false;
  }

  uint8_t data[MAX_PACKET_PAYLOAD + 1];
  int len = mesh::Utils::MACThenDecrypt(public_group_secret, data, &pkt->payload[PATH_HASH_SIZE], pkt->payload_len - PATH_HASH_SIZE);
  if (len <= 0) {
    DENSE_STATS_LOCK();
    spam_stats.decrypt_failed++;
    DENSE_STATS_UNLOCK();
    return false;
  }
  if (len < 5) {
    recordSpamDrop("short", 0, 0);
    return true;
  }

  uint8_t txt_type = data[4];
  if ((txt_type >> 2) != 0) {
    recordSpamDrop("type", 0, 0);
    return true;
  }

  uint32_t timestamp;
  memcpy(&timestamp, data, 4);
  data[len] = 0;

  TextQualityMetrics metrics;
  uint8_t score = StrHelper::messageConfidenceScore(&data[5], len - 5, timestamp, getRTCClock()->getCurrentTime(), &metrics);
  uint32_t now = getRTCClock()->getCurrentTime();
  const char* reason = NULL;
  if (metrics.text_len == 0) {
    reason = "empty_text";
  } else if (!metrics.valid_utf8) {
    reason = "invalid_utf8";
  } else if (timestamp == 0 || (now >= 1577836800UL && timestamp > now + (2UL * 24UL * 60UL * 60UL))) {
    reason = "timestamp";
  } else if (score < MIN_CHAT_MESSAGE_CONFIDENCE) {
    reason = "low_confidence";
  }

  if (reason) {
    recordSpamDrop(reason, score, metrics.entropy_score);
    MESH_DEBUG_PRINTLN("[MALFORMED] scope=repeater_group reason=%s score=%u entropy=%u payload_len=%u text_len=%u trailing=%u timestamp=%lu",
                       reason,
                       (uint32_t)score,
                       (uint32_t)metrics.entropy_score,
                       (uint32_t)(len - 5),
                       (uint32_t)metrics.text_len,
                       (uint32_t)metrics.trailing_nonzero,
                       (unsigned long)timestamp);
    return true;
  }
  DENSE_STATS_LOCK();
  spam_stats.allowed++;
  spam_stats.last_score = score;
  spam_stats.last_entropy = metrics.entropy_score;
  StrHelper::strncpy(spam_stats.last_reason, "allowed", sizeof(spam_stats.last_reason));
  DENSE_STATS_UNLOCK();
  return false;
}

uint32_t MyMesh::getRetransmitDelay(const mesh::Packet *packet) {
  uint32_t airtime_ms = _radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2);
  uint32_t t = (airtime_ms * _prefs.tx_delay_factor);
  uint32_t random_delay_ms = getRNG()->nextInt(0, 5*t + 1);
  return addNodeDelayOffsetMs(airtime_ms, _prefs.tx_delay_factor, random_delay_ms);
}
uint32_t MyMesh::getDirectRetransmitDelay(const mesh::Packet *packet) {
  uint32_t t = (_radio->getEstAirtimeFor(packet->getPathByteLen() + packet->payload_len + 2) * _prefs.direct_tx_delay_factor);
  return getRNG()->nextInt(0, 5*t + 1);
}

bool MyMesh::filterRecvFloodPacket(mesh::Packet* pkt) {
  if (pkt->isRouteFlood() && pkt->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    dense_stats.n_recv_flood_adverts++;
  }

  const bool bridge_rf_flood = pkt->isRouteFlood() && pkt->wasReceivedFromBridge() && _prefs.bridge_rf == BRIDGE_RF_FLOOD;
  if (shouldBlockBridgeOrRepeater(pkt)) {
    recordDenseSuppressedTx();
    return true;
  }
  if (!bridge_rf_flood && shouldDropMalformedGroupText(pkt)) {
    recordDenseSuppressedTx();
    return true;
  }

  // just try to determine region for packet (apply later in allowPacketForward())
  if (pkt->getRouteType() == ROUTE_TYPE_TRANSPORT_FLOOD) {
    recv_pkt_region = region_map.findMatch(pkt, REGION_DENY_FLOOD);
  } else if (pkt->getRouteType() == ROUTE_TYPE_FLOOD) {
    if ((pkt->getPayloadType() == PAYLOAD_TYPE_GRP_TXT || pkt->getPayloadType() == PAYLOAD_TYPE_GRP_DATA) &&
        region_map.getWildcard().flags & REGION_DENY_FLOOD) {
      recv_pkt_region = NULL;
    } else {
      recv_pkt_region =  &region_map.getWildcard();
    }
  } else {
    recv_pkt_region = NULL;
  }
  // do normal processing
  return false;
}

void MyMesh::onAnonDataRecv(mesh::Packet *packet, const uint8_t *secret, const mesh::Identity &sender,
                            uint8_t *data, size_t len) {
  if (packet->getPayloadType() == PAYLOAD_TYPE_ANON_REQ) { // received an initial request by a possible admin
                                                           // client (unknown at this stage)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    data[len] = 0;  // ensure null terminator
    uint8_t reply_len;

    reply_path_len = -1;
    if (data[4] == 0 || data[4] >= ' ') {   // is password, ie. a login request
      reply_len = handleLoginReq(sender, secret, timestamp, &data[4], packet->isRouteFlood());
    } else if (data[4] == ANON_REQ_TYPE_REGIONS && packet->isRouteDirect()) {
      reply_len = handleAnonRegionsReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_OWNER && packet->isRouteDirect()) {
      reply_len = handleAnonOwnerReq(sender, timestamp, &data[5]);
    } else if (data[4] == ANON_REQ_TYPE_BASIC && packet->isRouteDirect()) {
      reply_len = handleAnonClockReq(sender, timestamp, &data[5]);
    } else {
      reply_len = 0;  // unknown/invalid request type
    }

    if (reply_len == 0) return;   // invalid request

    if (packet->isRouteFlood()) {
      // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
      mesh::Packet* path = createPathReturn(sender, secret, packet->path, packet->path_len,
                                            PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
      if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
    } else if (reply_path_len < 0) {
      mesh::Packet* reply = createDatagram(PAYLOAD_TYPE_RESPONSE, sender, secret, reply_data, reply_len);
      if (reply) sendFloodReply(reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
    } else {
      mesh::Packet* reply = createDatagram(PAYLOAD_TYPE_RESPONSE, sender, secret, reply_data, reply_len);
      uint8_t path_len = ((reply_path_hash_size - 1) << 6) | (reply_path_len & 63);
      if (reply) sendDirect(reply, reply_path,  path_len, SERVER_RESPONSE_DELAY);
    }
  }
}

int MyMesh::searchPeersByHash(const uint8_t *hash) {
  int n = 0;
  for (int i = 0; i < acl.getNumClients(); i++) {
    if (acl.getClientByIdx(i)->id.isHashMatch(hash)) {
      matching_peer_indexes[n++] = i; // store the INDEXES of matching contacts (for subsequent 'peer' methods)
    }
  }
  return n;
}

void MyMesh::getPeerSharedSecret(uint8_t *dest_secret, int peer_idx) {
  int i = matching_peer_indexes[peer_idx];
  if (i >= 0 && i < acl.getNumClients()) {
    // lookup pre-calculated shared_secret
    memcpy(dest_secret, acl.getClientByIdx(i)->shared_secret, PUB_KEY_SIZE);
  } else {
    MESH_DEBUG_PRINTLN("getPeerSharedSecret: Invalid peer idx: %d", i);
  }
}

static bool isShare(const mesh::Packet *packet) {
  if (packet->hasTransportCodes()) {
    return packet->transport_codes[0] == 0 && packet->transport_codes[1] == 0;  // codes { 0, 0 } means 'send to nowhere'
  }
  return false;
}

static void atlasSanitizeJsonString(char* dest, size_t dest_len, const char* src) {
  if (dest_len == 0) return;
  size_t i = 0;
  while (src != NULL && *src && i < dest_len - 1) {
    char c = *src++;
    dest[i++] = ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                 c == '-' || c == '_' || c == ' ') ? c : '_';
  }
  dest[i] = 0;
}

static void atlasFormatCoord(char* dest, size_t dest_len, int32_t micro_degrees) {
  const char* sign = micro_degrees < 0 ? "-" : "";
  uint32_t abs_degrees = micro_degrees < 0 ? (uint32_t)(-micro_degrees) : (uint32_t)micro_degrees;
  snprintf(dest, dest_len, "%s%lu.%06lu",
           sign,
           (unsigned long)(abs_degrees / 1000000UL),
           (unsigned long)(abs_degrees % 1000000UL));
}

static void atlasFormatCent(char* dest, size_t dest_len, int32_t cent_value) {
  const char* sign = cent_value < 0 ? "-" : "";
  uint32_t abs_value = cent_value < 0 ? (uint32_t)(-cent_value) : (uint32_t)cent_value;
  snprintf(dest, dest_len, "%s%lu.%02lu",
           sign,
           (unsigned long)(abs_value / 100UL),
           (unsigned long)(abs_value % 100UL));
}

void MyMesh::onAdvertRecv(mesh::Packet *packet, const mesh::Identity &id, uint32_t timestamp,
                          const uint8_t *app_data, size_t app_data_len) {
  mesh::Mesh::onAdvertRecv(packet, id, timestamp, app_data, app_data_len); // chain to super impl
  if (app_data_len == 0) return;

  AdvertDataParser parser(app_data, app_data_len);

  if (_prefs.atlas.export_enabled && parser.isValid()) {
    uint32_t event_time = timestamp ? timestamp : getRTCClock()->getCurrentTime();
    char observer_id[9];
    char node_id[9];
    char node_name[33];
    char observer_name[17];
    mesh::Utils::toHex(observer_id, self_id.pub_key, 4);
    mesh::Utils::toHex(node_id, id.pub_key, 4);
    atlasSanitizeJsonString(node_name, sizeof(node_name), parser.hasName() ? parser.getName() : "");
    atlasSanitizeJsonString(observer_name, sizeof(observer_name), _prefs.node_name);

    Serial.printf("{\"v\":1,\"type\":\"node_seen\",\"time\":%lu,\"node\":\"%s\",\"node_id\":\"%s\"}\n",
                  (unsigned long)event_time, node_name, node_id);

    if (parser.hasLatLon()) {
      char lat[16];
      char lon[16];
      atlasFormatCoord(lat, sizeof(lat), parser.getIntLat());
      atlasFormatCoord(lon, sizeof(lon), parser.getIntLon());
      Serial.printf("{\"v\":1,\"type\":\"position\",\"time\":%lu,\"node\":\"%s\",\"node_id\":\"%s\",\"lat\":%s,\"lon\":%s}\n",
                    (unsigned long)event_time, node_name, node_id, lat, lon);
    }

    if (packet->path_len == 0 && !isShare(packet)) {
      char snr[12];
      int16_t rssi = (int16_t)radio_driver.getLastRSSI();
      atlasFormatCent(snr, sizeof(snr), (int32_t)(packet->getSNR() * 100.0f));
      Serial.printf("{\"v\":1,\"type\":\"neighbor\",\"time\":%lu,\"node\":\"%s\",\"node_id\":\"%s\",\"neighbors\":[{\"node_id\":\"%s\",\"rssi\":%d,\"snr\":%s,\"last_heard\":%lu}]}\n",
                    (unsigned long)event_time, observer_name, observer_id, node_id, (int)rssi, snr,
                    (unsigned long)event_time);
    }
  }

  // if this a zero hop advert (and not via 'Share'), add it to neighbours
  if (packet->getPathHashCount() == 0 && !isShare(packet)) {
    if (parser.isValid() && parser.getType() == ADV_TYPE_REPEATER) { // just keep neigbouring Repeaters
      putNeighbour(id, timestamp, packet->getSNR());
    }
  }
}

void MyMesh::onPeerDataRecv(mesh::Packet *packet, uint8_t type, int sender_idx, const uint8_t *secret,
                            uint8_t *data, size_t len) {
  int i = matching_peer_indexes[sender_idx];
  if (i < 0 || i >= acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("onPeerDataRecv: invalid peer idx: %d", i);
    return;
  }
  ClientInfo* client = acl.getClientByIdx(i);

  if (type == PAYLOAD_TYPE_REQ) { // request (from a Known admin client!)
    uint32_t timestamp;
    memcpy(&timestamp, data, 4);

    if (timestamp > client->last_timestamp) { // prevent replay attacks
      int reply_len = handleRequest(client, timestamp, &data[4], len - 4);
      if (reply_len == 0) return; // invalid command

      client->last_timestamp = timestamp;
      client->last_activity = getRTCClock()->getCurrentTime();

      if (packet->isRouteFlood()) {
        // let this sender know path TO here, so they can use sendDirect(), and ALSO encode the response
        mesh::Packet *path = createPathReturn(client->id, secret, packet->path, packet->path_len,
                                              PAYLOAD_TYPE_RESPONSE, reply_data, reply_len);
        if (path) sendFloodReply(path, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
      } else {
        mesh::Packet *reply =
            createDatagram(PAYLOAD_TYPE_RESPONSE, client->id, secret, reply_data, reply_len);
        if (reply) {
          if (client->out_path_len != OUT_PATH_UNKNOWN) { // we have an out_path, so send DIRECT
            sendDirect(reply, client->out_path, client->out_path_len, SERVER_RESPONSE_DELAY);
          } else {
            sendFloodReply(reply, SERVER_RESPONSE_DELAY, packet->getPathHashSize());
          }
        }
      }
    } else {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
    }
  } else if (type == PAYLOAD_TYPE_TXT_MSG && len > 5 && client->isAdmin()) { // a CLI command
    uint32_t sender_timestamp;
    memcpy(&sender_timestamp, data, 4); // timestamp (by sender's RTC clock - which could be wrong)
    uint8_t flags = (data[4] >> 2);        // message attempt number, and other flags

    if (!(flags == TXT_TYPE_PLAIN || flags == TXT_TYPE_CLI_DATA)) {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: unsupported text type received: flags=%02x", (uint32_t)flags);
    } else if (sender_timestamp >= client->last_timestamp) { // prevent replay attacks
      bool is_retry = (sender_timestamp == client->last_timestamp);
      client->last_timestamp = sender_timestamp;
      client->last_activity = getRTCClock()->getCurrentTime();

      // len can be > original length, but 'text' will be padded with zeroes
      data[len] = 0; // need to make a C string again, with null terminator

      if (flags == TXT_TYPE_PLAIN) { // for legacy CLI, send Acks
        uint32_t ack_hash; // calc truncated hash of the message timestamp + text + sender pub_key, to prove
                           // to sender that we got it
        mesh::Utils::sha256((uint8_t *)&ack_hash, 4, data, 5 + strlen((char *)&data[5]), client->id.pub_key,
                            PUB_KEY_SIZE);

        mesh::Packet *ack = createAck(ack_hash);
        if (ack) {
          if (client->out_path_len == OUT_PATH_UNKNOWN) {
            sendFloodReply(ack, TXT_ACK_DELAY, packet->getPathHashSize());
          } else {
            sendDirect(ack, client->out_path, client->out_path_len, TXT_ACK_DELAY);
          }
        }
      }

      uint8_t temp[166];
      char *command = (char *)&data[5];
      char *reply = (char *)&temp[5];
      if (is_retry) {
        *reply = 0;
      } else {
        handleCommand(sender_timestamp, command, reply);
      }
      int text_len = strlen(reply);
      if (text_len > 0) {
        uint32_t timestamp = getRTCClock()->getCurrentTimeUnique();
        if (timestamp == sender_timestamp) {
          // WORKAROUND: the two timestamps need to be different, in the CLI view
          timestamp++;
        }
        memcpy(temp, &timestamp, 4);        // mostly an extra blob to help make packet_hash unique
        temp[4] = (TXT_TYPE_CLI_DATA << 2); // NOTE: legacy was: TXT_TYPE_PLAIN

        auto reply = createDatagram(PAYLOAD_TYPE_TXT_MSG, client->id, secret, temp, 5 + text_len);
        if (reply) {
          if (client->out_path_len == OUT_PATH_UNKNOWN) {
            sendFloodReply(reply, CLI_REPLY_DELAY_MILLIS, packet->getPathHashSize());
          } else {
            sendDirect(reply, client->out_path, client->out_path_len, CLI_REPLY_DELAY_MILLIS);
          }
        }
      }
    } else {
      MESH_DEBUG_PRINTLN("onPeerDataRecv: possible replay attack detected");
    }
  }
}

bool MyMesh::onPeerPathRecv(mesh::Packet *packet, int sender_idx, const uint8_t *secret, uint8_t *path,
                            uint8_t path_len, uint8_t extra_type, uint8_t *extra, uint8_t extra_len) {
  // TODO: prevent replay attacks
  int i = matching_peer_indexes[sender_idx];

  if (i >= 0 && i < acl.getNumClients()) { // get from our known_clients table (sender SHOULD already be known in this context)
    MESH_DEBUG_PRINTLN("PATH to client, path_len=%d", (uint32_t)path_len);
    auto client = acl.getClientByIdx(i);

    // store a copy of path, for sendDirect()
    client->out_path_len = mesh::Packet::copyPath(client->out_path, path, path_len);
    client->last_activity = getRTCClock()->getCurrentTime();

    if (_prefs.atlas.export_enabled) {
      char src_id[9];
      char dst_id[9];
      mesh::Utils::toHex(src_id, self_id.pub_key, 4);
      mesh::Utils::toHex(dst_id, client->id.pub_key, 4);

      Serial.printf("{\"v\":1,\"type\":\"path\",\"time\":%lu,\"src\":\"%s\",\"dst\":\"%s\",\"hops\":[\"%s\"",
                    (unsigned long)getRTCClock()->getCurrentTime(), src_id, dst_id, src_id);

      uint8_t hash_size = (path_len >> 6) + 1;
      uint8_t hash_count = path_len & 63;
      for (uint8_t h = 0; h < hash_count; h++) {
        char hop_id[9];
        mesh::Utils::toHex(hop_id, &path[h * hash_size], hash_size);
        Serial.printf(",\"%s\"", hop_id);
      }

      Serial.printf(",\"%s\"]}\n", dst_id);
    }
  } else {
    MESH_DEBUG_PRINTLN("onPeerPathRecv: invalid peer idx: %d", i);
  }

  // NOTE: no reciprocal path send!!
  return false;
}

#define CTL_TYPE_NODE_DISCOVER_REQ   0x80
#define CTL_TYPE_NODE_DISCOVER_RESP  0x90

void MyMesh::onControlDataRecv(mesh::Packet* packet) {
  uint8_t type = packet->payload[0] & 0xF0;    // just test upper 4 bits
  if (type == CTL_TYPE_NODE_DISCOVER_REQ && packet->payload_len >= 6
      && !_prefs.disable_fwd && discover_limiter.allow(rtc_clock.getCurrentTime())
  ) {
    int i = 1;
    uint8_t  filter = packet->payload[i++];
    uint32_t tag;
    memcpy(&tag, &packet->payload[i], 4); i += 4;
    uint32_t since;
    if (packet->payload_len >= i+4) {   // optional since field
      memcpy(&since, &packet->payload[i], 4); i += 4;
    } else {
      since = 0;
    }

    if ((filter & (1 << ADV_TYPE_REPEATER)) != 0 && _prefs.discovery_mod_timestamp >= since) {
      bool prefix_only = packet->payload[0] & 1;
      uint8_t data[6 + PUB_KEY_SIZE];
      data[0] = CTL_TYPE_NODE_DISCOVER_RESP | ADV_TYPE_REPEATER;   // low 4-bits for node type
      data[1] = packet->_snr;   // let sender know the inbound SNR ( x 4)
      memcpy(&data[2], &tag, 4);     // include tag from request, for client to match to
      memcpy(&data[6], self_id.pub_key, PUB_KEY_SIZE);
      auto resp = createControlData(data, prefix_only ? 6 + 8 : 6 + PUB_KEY_SIZE);
      if (resp) {
        sendZeroHop(resp, getRetransmitDelay(resp)*4);  // apply random delay (widened x4), as multiple nodes can respond to this
      }
    }
  } else if (type == CTL_TYPE_NODE_DISCOVER_RESP && packet->payload_len >= 6) {
    uint8_t node_type = packet->payload[0] & 0x0F;
    if (node_type != ADV_TYPE_REPEATER) {
      return;
    }
    if (packet->payload_len < 6 + PUB_KEY_SIZE) {
      MESH_DEBUG_PRINTLN("onControlDataRecv: DISCOVER_RESP pubkey too short: %d", (uint32_t)packet->payload_len);
      return;
    }

    if (pending_discover_tag == 0 || millisHasNowPassed(pending_discover_until)) {
      pending_discover_tag = 0;
      return;
    }
    uint32_t tag;
    memcpy(&tag, &packet->payload[2], 4);
    if (tag != pending_discover_tag) {
      return;
    }

    mesh::Identity id(&packet->payload[6]);
    if (id.matches(self_id)) {
      return;
    }
    putNeighbour(id, rtc_clock.getCurrentTime(), packet->getSNR());
  }
}

void MyMesh::sendNodeDiscoverReq() {
  uint8_t data[10];
  data[0] = CTL_TYPE_NODE_DISCOVER_REQ; // prefix_only=0
  data[1] = (1 << ADV_TYPE_REPEATER);
  getRNG()->random(&data[2], 4); // tag
  memcpy(&pending_discover_tag, &data[2], 4);
  pending_discover_until = futureMillis(60000);
  uint32_t since = 0;
  memcpy(&data[6], &since, 4);

  auto pkt = createControlData(data, sizeof(data));
  if (pkt) {
    sendZeroHop(pkt);
  }
}

MyMesh::MyMesh(mesh::MainBoard &board, mesh::Radio &radio, mesh::MillisecondClock &ms, mesh::RNG &rng,
               mesh::RTCClock &rtc, mesh::MeshTables &tables)
    : mesh::Mesh(radio, ms, rng, rtc, *new StaticPoolPacketManager(32), tables),
      region_map(key_store), temp_map(key_store),
      _cli(board, rtc, sensors, region_map, acl, &_prefs, this),
      telemetry(MAX_PACKET_PAYLOAD - 4),
      discover_limiter(4, 120),  // max 4 every 2 minutes
      anon_limiter(4, 180)   // max 4 every 3 minutes
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
      , tcp_bridge(&_prefs, _mgr, &rtc), ble_bridge(&_prefs, _mgr, &rtc)
#elif defined(WITH_RS232_BRIDGE)
      , bridge(&_prefs, WITH_RS232_BRIDGE, _mgr, &rtc)
#elif defined(WITH_ESPNOW_BRIDGE)
      , bridge(&_prefs, _mgr, &rtc)
#elif defined(WITH_TCP_BRIDGE)
      , bridge(&_prefs, _mgr, &rtc)
#elif defined(WITH_BLE_BRIDGE)
      , bridge(&_prefs, _mgr, &rtc)
#endif
{
#if defined(WITH_MQTT_BRIDGE) || defined(WITH_SNMP)
  _board_ref = &board;
#endif
  last_millis = 0;
  uptime_millis = 0;
  next_daily_reboot_uptime_ms = 0;
  daily_reboot_pending = false;
  next_local_advert = next_flood_advert = 0;
  dirty_contacts_expiry = 0;
  set_radio_at = revert_radio_at = 0;
  _logging = false;
  region_load_active = false;
  memset(path_blocks, 0, sizeof(path_blocks));
  memset(node_blocks, 0, sizeof(node_blocks));
  clearDenseStatsLocked();
  clearSpamStatsLocked();
  clearPowerStats();

#if MAX_NEIGHBOURS
  memset(neighbours, 0, sizeof(neighbours));
#endif

  // defaults
  memset(&_prefs, 0, sizeof(_prefs));
  Atlas::setDefaults(_prefs.atlas);
  _prefs.airtime_factor = 1.0;
  _prefs.rx_delay_base = 0.0f;   // turn off by default, was 10.0;
  _prefs.tx_delay_factor = 0.5f; // was 0.25f
  _prefs.direct_tx_delay_factor = 0.3f; // was 0.2
  StrHelper::strncpy(_prefs.node_name, ADVERT_NAME, sizeof(_prefs.node_name));
  _prefs.node_lat = ADVERT_LAT;
  _prefs.node_lon = ADVERT_LON;
  StrHelper::strncpy(_prefs.password, ADMIN_PASSWORD, sizeof(_prefs.password));
  _prefs.freq = LORA_FREQ;
  _prefs.sf = LORA_SF;
  _prefs.bw = LORA_BW;
  _prefs.cr = LORA_CR;
  _prefs.tx_power_dbm = LORA_TX_POWER;
  _prefs.advert_interval = 1;        // default to 2 minutes for NEW installs
  _prefs.flood_advert_interval = 0;  // disabled
  _prefs.flood_advert_base = 0.308f;
  _prefs.flood_relay_prob = 255;
  _prefs.flood_dynamic_enable = 0;
  _prefs.flood_node_delay_enable = 1;
  _prefs.flood_dup_suppress_enable = 1;
  _prefs.nearby_client_suppress_enabled = 1;
  _prefs.nearby_client_suppress_rssi_dbm = -45;
  _prefs.nearby_client_suppress_max_hops = 0;
  _prefs.daily_reboot_enabled = 0;
  _prefs.daily_reboot_interval_hours = DAILY_REBOOT_DEFAULT_HOURS;
  _prefs.powersaving_enabled = 0;
  _prefs.malformed_drop = 1;     // default on for inspectable malformed public chat
  _prefs.flood_max = 64;
  _prefs.interference_threshold = 1; // non-zero enables hardware CAD before TX
  _prefs.flood_max_unscoped = 64;
  _prefs.flood_max_advert = 8;
  _prefs.flood_max_messages = 64;

  // bridge defaults
#if defined(WITH_TCP_BRIDGE)
  _prefs.bridge_enabled = 0;    // configure WiFi/server before enabling TCP bridge
  _prefs.disable_fwd = 1;       // bridge gateway: no RF->RF mesh repeat
  _prefs.bridge_pkt_src = 2;    // RF RX + TX for RF<->TCP bridge
  _prefs.bridge_rf = BRIDGE_RF_FLOOD; // TCP->RF onto mesh (within flood.max)
#else
  _prefs.bridge_enabled = 1;    // enabled
  _prefs.bridge_pkt_src = 0;    // logTx
  _prefs.bridge_rf = 0;         // do not forward bridge floods to RF by default
#endif
  _prefs.bridge_delay   = 500;  // milliseconds
  _prefs.bridge_export_filter = BRIDGE_EXPORT_ALL;
  _prefs.bridge_export_max_hops = 0; // unlimited
  _prefs.bridge_tcp_ttl = 2;
  StrHelper::strncpy(_prefs.bridge_group, "default", sizeof(_prefs.bridge_group));
  _prefs.bridge_rf_inject_budget_enabled = 0;
  _prefs.bridge_rf_inject_max_per_min = 0;
  _prefs.bridge_rf_inject_max_airtime_ms_hour = 0;
  _prefs.bridge_rf_inject_block_duty_centi_pct = 0;
  _prefs.bridge_id[0] = 0;
  _prefs.bridge_baud = 115200;  // baud rate
  _prefs.bridge_channel = 1;    // channel 1

  StrHelper::strncpy(_prefs.bridge_secret, "LVSITANOS", sizeof(_prefs.bridge_secret));

  // GPS defaults
  _prefs.gps_enabled = 0;
  _prefs.gps_interval = 0;
  _prefs.advert_loc_policy = ADVERT_LOC_PREFS;

  _prefs.adc_multiplier = 0.0f; // 0.0f means use default board multiplier
  _prefs.fem_rx_gain = board.getFemRxGain();
  _prefs.low_bat_boot_guard_enabled = 1;
  _prefs.low_bat_boot_guard_mv = LOW_BAT_BOOT_GUARD_MV;
  _prefs.low_bat_boot_valid_min_mv = LOW_BAT_BOOT_VALID_MIN_MV;
  _prefs.low_bat_boot_retry_secs = LOW_BAT_BOOT_RETRY_SECS;
  _prefs.low_bat_runtime_guard_enabled = 1;
  _prefs.low_bat_runtime_guard_mv = LOW_BAT_RUNTIME_GUARD_MV;
  _prefs.low_bat_runtime_warn_mv = LOW_BAT_RUNTIME_WARN_MV;
  _prefs.low_bat_runtime_valid_min_mv = LOW_BAT_RUNTIME_VALID_MIN_MV;
  _prefs.low_bat_runtime_retry_secs = LOW_BAT_RUNTIME_RETRY_SECS;

#ifdef WITH_MQTT_BRIDGE
  // MQTT observer tuning. Slot/IATA/timezone defaults come from /mqtt_prefs (setMQTTPrefsDefaults);
  // these are NodePrefs fallbacks until that file is loaded/synced.
  _prefs.agc_reset_interval = 7;      // 28s (secs/4) — avoid AGC drift on long-running observers
  _prefs.bridge_enabled = 1;          // MQTT bridge self-gates on WiFi/MQTT config
  _prefs.bridge_pkt_src = 1;          // logRx
  _prefs.mqtt_origin[0] = '\0';
  _prefs.mqtt_status_enabled = 1;
  _prefs.mqtt_packets_enabled = 1;
  _prefs.mqtt_raw_enabled = 0;
  _prefs.mqtt_tx_enabled = 2;         // advert only
  _prefs.mqtt_rx_enabled = 1;
  _prefs.mqtt_status_interval = 300000;
  _prefs.wifi_power_save = 1;         // none
  StrHelper::strncpy(_prefs.timezone_string, "Europe/Amsterdam", sizeof(_prefs.timezone_string));
  _prefs.timezone_offset = 1;
  StrHelper::strncpy(_prefs.mqtt_slot_preset[0], "dutchmeshcore-1", sizeof(_prefs.mqtt_slot_preset[0]));
  StrHelper::strncpy(_prefs.mqtt_slot_preset[1], "dutchmeshcore-2", sizeof(_prefs.mqtt_slot_preset[1]));
  for (int i = 2; i < MAX_MQTT_SLOTS; i++)
    StrHelper::strncpy(_prefs.mqtt_slot_preset[i], "none", sizeof(_prefs.mqtt_slot_preset[i]));
  _prefs.snmp_enabled = 0;
  StrHelper::strncpy(_prefs.snmp_community, "public", sizeof(_prefs.snmp_community));
#endif

#if defined(USE_SX1262) || defined(USE_SX1268)
#ifdef SX126X_RX_BOOSTED_GAIN
  _prefs.rx_boosted_gain = SX126X_RX_BOOSTED_GAIN;
#else
  _prefs.rx_boosted_gain = 1; // enabled by default;
#endif
#endif

  pending_discover_tag = 0;
  pending_discover_until = 0;

  memset(default_scope.key, 0, sizeof(default_scope.key));
}

void MyMesh::begin(FILESYSTEM *fs) {
  mesh::Mesh::begin();
  _fs = fs;
  // load persisted prefs
  _cli.loadPrefs(_fs);
  guardLowBatteryBoot(board, _prefs.low_bat_boot_guard_enabled, _prefs.low_bat_boot_guard_mv, _prefs.low_bat_boot_valid_min_mv, _prefs.low_bat_boot_retry_secs);
  scheduleDailyReboot();
  acl.load(_fs, self_id);
  // TODO: key_store.begin();
  bool regions_loaded = region_map.load(_fs);
  if (!regions_loaded && region_map.getCount() == 0) {
    applyDefaultRegionProfile(region_map);
  }

  // establish default-scope
  {
    RegionEntry* r = region_map.getDefaultRegion();
    if (r) {
      region_map.getTransportKeysFor(*r, &default_scope, 1);
    } else {
#ifdef DEFAULT_FLOOD_SCOPE_NAME
      r = region_map.findByName(DEFAULT_FLOOD_SCOPE_NAME);
      if (r == NULL) {
        r = region_map.putRegion(DEFAULT_FLOOD_SCOPE_NAME, 0);  // auto-create the default scope region
        if (r) { r->flags = 0; }   // Allow-flood
      }
      if (r) {
        region_map.setDefaultRegion(r);
        region_map.getTransportKeysFor(*r, &default_scope, 1);
      }
#endif
    }
  }

  // Start the primary (non-MQTT) bridge first so it can bring up WiFi before MQTT piggybacks.
#if defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
  if (_prefs.bridge_enabled) {
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
    configureTcpBridgeNodeIds();
    tcp_bridge.setCommandHandler(this);
    tcp_bridge.begin();
    ble_bridge.begin();
#else
#if defined(WITH_TCP_BRIDGE)
    configureTcpBridgeNodeIds();
    bridge.setCommandHandler(this);
#endif
    bridge.begin();
#endif
  }
#endif
#if defined(WITH_MQTT_BRIDGE)
  // The MQTT bridge always starts (it self-gates on WiFi/MQTT config); construction is deferred
  // to startMqttBridge() which heap-allocates it and wires metadata. When a TCP bridge is also
  // present it owns WiFi and MQTT runs in external-WiFi mode.
  startMqttBridge();
#endif

  radio_driver.setParams(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr);
  radio_driver.setTxPower(_prefs.tx_power_dbm);

  radio_driver.setRxBoostedGainMode(_prefs.rx_boosted_gain);
  MESH_DEBUG_PRINTLN("RX Boosted Gain Mode: %s",
                     radio_driver.getRxBoostedGainMode() ? "Enabled" : "Disabled");

  updateAdvertTimer();
  updateFloodAdvertTimer();

  board.setAdcMultiplier(_prefs.adc_multiplier);
  board.setFemRxGain(_prefs.fem_rx_gain);

#if ENV_INCLUDE_GPS == 1
  applyGpsPrefs();
#endif

}

void MyMesh::scheduleDailyReboot() {
#if SUPPORT_DAILY_REBOOT
  if (!_prefs.daily_reboot_enabled) {
    next_daily_reboot_uptime_ms = 0;
    daily_reboot_pending = false;
    return;
  }
  uint8_t hours = _prefs.daily_reboot_interval_hours;
  if (hours < DAILY_REBOOT_MIN_HOURS || hours > DAILY_REBOOT_MAX_HOURS) {
    hours = DAILY_REBOOT_DEFAULT_HOURS;
    _prefs.daily_reboot_interval_hours = hours;
  }
  next_daily_reboot_uptime_ms = uptime_millis + hoursToMillis64(hours);
  daily_reboot_pending = false;
#endif
}

void MyMesh::sendFloodScoped(const TransportKey& scope, mesh::Packet* pkt, uint32_t delay_millis, uint8_t path_hash_size) {
  if (scope.isNull()) {
    sendFlood(pkt, delay_millis, path_hash_size);
  } else {
    uint16_t codes[2];
    codes[0] = scope.calcTransportCode(pkt);
    codes[1] = 0;  // REVISIT: set to 'home' Region, for sender/return region?
    sendFlood(pkt, codes, delay_millis, path_hash_size);
  }
}

void MyMesh::applyTempRadioParams(float freq, float bw, uint8_t sf, uint8_t cr, int timeout_mins) {
  set_radio_at = futureMillis(2000); // give CLI reply some time to be sent back, before applying temp radio params
  pending_freq = freq;
  pending_bw = bw;
  pending_sf = sf;
  pending_cr = cr;

  revert_radio_at = futureMillis(2000 + timeout_mins * 60 * 1000); // schedule when to revert radio params
}

bool MyMesh::formatFileSystem() {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  return InternalFS.format();
#elif defined(RP2040_PLATFORM)
  return LittleFS.format();
#elif defined(ESP32)
  return SPIFFS.format();
#else
#error "need to implement file system erase"
  return false;
#endif
}

void MyMesh::sendSelfAdvertisement(int delay_millis, bool flood) {
  mesh::Packet *pkt = createSelfAdvert();
  if (pkt) {
    if (flood) {
      sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);
    } else {
      sendZeroHop(pkt, delay_millis);
    }
  } else {
    MESH_DEBUG_PRINTLN("ERROR: unable to create advertisement packet!");
  }
}

void MyMesh::updateAdvertTimer() {
  if (_prefs.advert_interval > 0) { // schedule local advert timer
    next_local_advert = futureMillis(((uint32_t)_prefs.advert_interval) * 2 * 60 * 1000);
  } else {
    next_local_advert = 0; // stop the timer
  }
}

void MyMesh::updateFloodAdvertTimer() {
  if (_prefs.flood_advert_interval > 0) { // schedule flood advert timer
    next_flood_advert = futureMillis(((uint32_t)_prefs.flood_advert_interval) * 60 * 60 * 1000);
  } else {
    next_flood_advert = 0; // stop the timer
  }
}

void MyMesh::dumpLogFile() {
#if defined(RP2040_PLATFORM)
  File f = _fs->open(PACKET_LOG_FILE, "r");
#else
  File f = _fs->open(PACKET_LOG_FILE);
#endif
  if (f) {
    while (f.available()) {
      int c = f.read();
      if (c < 0) break;
      Serial.print((char)c);
    }
    f.close();
  }
}

void MyMesh::setTxPower(int8_t power_dbm) {
  radio_driver.setTxPower(power_dbm);
}

#if defined(USE_SX1262) || defined(USE_SX1268)
void MyMesh::setRxBoostedGain(bool enable) {
  radio_driver.setRxBoostedGainMode(enable);
}
#endif

void MyMesh::formatNeighborsReply(char *reply) {
  char *dp = reply;

#if MAX_NEIGHBOURS
  // create copy of neighbours list, skipping empty entries so we can sort it separately from main list
  int16_t neighbours_count = 0;
  NeighbourInfo* sorted_neighbours[MAX_NEIGHBOURS];
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    auto neighbour = &neighbours[i];
    if (neighbour->heard_timestamp > 0) {
      sorted_neighbours[neighbours_count] = neighbour;
      neighbours_count++;
    }
  }

  // sort neighbours newest to oldest
  std::sort(sorted_neighbours, sorted_neighbours + neighbours_count, [](const NeighbourInfo* a, const NeighbourInfo* b) {
    return a->heard_timestamp > b->heard_timestamp; // desc
  });

  for (int i = 0; i < neighbours_count && dp - reply < 134; i++) {
    NeighbourInfo *neighbour = sorted_neighbours[i];

    // add new line if not first item
    if (i > 0) *dp++ = '\n';

    char hex[10];
    // get 4 bytes of neighbour id as hex
    mesh::Utils::toHex(hex, neighbour->id.pub_key, 4);

    // add next neighbour
    uint32_t secs_ago = getRTCClock()->getCurrentTime() - neighbour->heard_timestamp;
    sprintf(dp, "%s:%d:%d", hex, secs_ago, neighbour->snr);
    while (*dp)
      dp++; // find end of string
  }
#endif
  if (dp == reply) { // no neighbours, need empty response
    strcpy(dp, "-none-");
    dp += 6;
  }
  *dp = 0; // null terminator
}

void MyMesh::removeNeighbor(const uint8_t *pubkey, int key_len) {
#if MAX_NEIGHBOURS
  for (int i = 0; i < MAX_NEIGHBOURS; i++) {
    NeighbourInfo *neighbour = &neighbours[i];
    if (memcmp(neighbour->id.pub_key, pubkey, key_len) == 0) {
      neighbours[i] = NeighbourInfo(); // clear neighbour entry
    }
  }
#endif
}

void MyMesh::startRegionsLoad() {
  temp_map.resetFrom(region_map);   // rebuild regions in a temp instance
  memset(load_stack, 0, sizeof(load_stack));
  load_stack[0] = &temp_map.getWildcard();
  region_load_active = true;
}

bool MyMesh::saveRegions() {
  return region_map.save(_fs);
}

void MyMesh::onDefaultRegionChanged(const RegionEntry* r) {
  if (r) {
    region_map.getTransportKeysFor(*r, &default_scope, 1);
  } else {
    memset(default_scope.key, 0, sizeof(default_scope.key));
  }
}

void MyMesh::formatStatsReply(char *reply) {
  StatsFormatHelper::formatCoreStats(reply, board, *_ms, _err_flags, _mgr);
}

void MyMesh::formatRadioStatsReply(char *reply) {
  StatsFormatHelper::formatRadioStats(reply, _radio, radio_driver, getTotalAirTime(), getReceiveAirTime());
}

void MyMesh::formatPacketStatsReply(char *reply) {
  StatsFormatHelper::formatPacketStats(reply, radio_driver, getNumSentFlood(), getNumSentDirect(), 
                                       getNumRecvFlood(), getNumRecvDirect());
}

void MyMesh::formatDenseStatsReply(char *reply) {
  SimpleMeshTables *tables = (SimpleMeshTables *)getTables();
  dense_mesh_stats_t stats;
  getDenseStats(&stats);
  snprintf(reply, 160,
          "adv=%lu/%lu/%lu cad=%lu/%lu\nw n=%u u=%u d=%u s=%u fd=%lu dd=%lu\na=%lu/%lu c=%u d=%u p=%u y=%u",
          (unsigned long)dense_stats.n_recv_flood_adverts,
          (unsigned long)dense_stats.n_fwd_flood_adverts,
          (unsigned long)dense_stats.n_drop_flood_adverts,
          (unsigned long)getNumCADBusyEvents(),
          (unsigned long)getNumCADTimeoutEvents(),
          (uint32_t)stats.neighbors,
          (uint32_t)stats.unique_rx,
          (uint32_t)stats.dup_rx,
          (uint32_t)stats.suppressed_tx,
          (unsigned long)tables->getNumFloodDups(),
          (unsigned long)tables->getNumDirectDups(),
          (unsigned long)stats.airtime_rx_ms,
          (unsigned long)stats.airtime_tx_ms,
          (uint32_t)stats.congestion_level,
          (uint32_t)stats.density_level,
          (uint32_t)_prefs.flood_relay_prob,
          (uint32_t)_prefs.flood_dynamic_enable);
}

void MyMesh::formatAtlasStatsReply(char *reply) {
  SimpleMeshTables *tables = (SimpleMeshTables *)getTables();
  dense_mesh_stats_t stats;
  getDenseStats(&stats);
  snprintf(reply, 160,
          "{\"heard\":%u,\"dup\":%lu,\"fwd\":%lu,\"sup\":%u,\"route\":{\"hit\":0,\"miss\":0},\"air\":{\"tx\":%lu,\"rx\":%lu}}",
          (uint32_t)stats.unique_rx,
          (unsigned long)tables->getNumFloodDups() + tables->getNumDirectDups(),
          (unsigned long)getNumSentFlood() + getNumSentDirect(),
          (uint32_t)stats.suppressed_tx,
          (unsigned long)stats.airtime_tx_ms,
          (unsigned long)stats.airtime_rx_ms);
}

void MyMesh::formatAtlasObserverReply(char *reply) {
  if (!_prefs.atlas.export_enabled) {
    reply[0] = 0;
    return;
  }

  SimpleMeshTables *tables = (SimpleMeshTables *)getTables();
  dense_mesh_stats_t stats;
  getDenseStats(&stats);
  char node_id[9];
  char node_name[17];
  mesh::Utils::toHex(node_id, self_id.pub_key, 4);
  uint8_t i = 0;
  for (; i < sizeof(node_name) - 1 && _prefs.node_name[i]; i++) {
    char c = _prefs.node_name[i];
    node_name[i] = ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_') ? c : '_';
  }
  node_name[i] = 0;
  snprintf(reply, 512,
          "{\"v\":1,\"type\":\"dense_stats\",\"time\":%lu,\"node\":\"%s\",\"node_id\":\"%s\",\"heard\":%u,\"duplicates\":%lu,\"forwards\":%lu,\"suppressed\":%u,\"airtime_ms\":%lu}",
          (unsigned long)getRTCClock()->getCurrentTime(),
          node_name,
          node_id,
          (uint32_t)stats.unique_rx,
          (unsigned long)tables->getNumFloodDups() + tables->getNumDirectDups(),
          (unsigned long)getNumSentFlood() + getNumSentDirect(),
          (uint32_t)stats.suppressed_tx,
          (unsigned long)stats.airtime_tx_ms + stats.airtime_rx_ms);
}

void MyMesh::formatSpamStatsReply(char *reply) {
  DENSE_STATS_LOCK();
  SpamStats stats = spam_stats;
  DENSE_STATS_UNLOCK();

  snprintf(reply, 160,
          "seen=%lu ok=%lu drop=%lu spam=%lu fail=%lu\ns=%lu t=%lu e=%lu u=%lu tm=%lu\nlast=%s sc=%u en=%u mode=%s",
          (unsigned long)stats.public_group_seen,
          (unsigned long)stats.allowed,
          (unsigned long)stats.malformed_dropped,
          (unsigned long)stats.spam_dropped,
          (unsigned long)stats.decrypt_failed,
          (unsigned long)stats.short_dropped,
          (unsigned long)stats.type_dropped,
          (unsigned long)stats.empty_dropped,
          (unsigned long)stats.invalid_utf8_dropped,
          (unsigned long)stats.timestamp_dropped,
          stats.last_reason,
          (uint32_t)stats.last_score,
          (uint32_t)stats.last_entropy,
          _prefs.malformed_drop ? "drop" : "off");
}

void MyMesh::formatRepeaterHealthReply(char *reply) {
  dense_mesh_stats_t stats;
  getDenseStats(&stats);

  uint32_t recv = radio_driver.getPacketsRecv();
  uint32_t recv_errors = radio_driver.getPacketsRecvErrors();
  uint32_t recv_total = recv + recv_errors;
  uint8_t err_pct = recv_total == 0 ? 0 : (uint8_t)((recv_errors * 100UL) / recv_total);
  uint32_t total_rx = (uint32_t)stats.unique_rx + stats.dup_rx;
  uint8_t dup_pct = total_rx == 0 ? 0 : (uint8_t)((stats.dup_rx * 100UL) / total_rx);
  DENSE_STATS_LOCK();
  uint32_t spam_dropped = spam_stats.malformed_dropped;
  DENSE_STATS_UNLOCK();
  int score = 100;
  if (_prefs.disable_fwd) score -= 40;
  score -= stats.congestion_level * 10;
  score -= stats.density_level * 5;
  if (err_pct >= 10) score -= 20;
  else if (err_pct >= 3) score -= 8;
  if (!_prefs.malformed_drop) score -= 3;
  score = constrain(score, 0, 100);

  snprintf(reply, 160,
          "health=%s score=%u repeat=%s rxerr=%u%% air=%s density=%s dup=%u%%\nspam_drop=%lu spam_mode=%s sf=%u freq=%s",
          healthName((uint8_t)score),
          (uint32_t)score,
          _prefs.disable_fwd ? "off" : "on",
          (uint32_t)err_pct,
          levelName(stats.congestion_level),
          levelName(stats.density_level),
          (uint32_t)dup_pct,
          (unsigned long)spam_dropped,
          _prefs.malformed_drop ? "drop" : "off",
          (uint32_t)_prefs.sf,
          StrHelper::ftoa(_prefs.freq));
}

static const char* getPowerSavingSupport() {
#if defined(NRF52_PLATFORM)
  return "supported";
#elif defined(CONFIG_IDF_TARGET_ESP32S3) && defined(P_LORA_DIO_1)
  return "limited";
#else
  return "unknown";
#endif
}

void MyMesh::formatPowerStatsReply(char *reply) {
  snprintf(reply, 160,
          "sleep attempts=%lu\nskip pending=%lu bridge=%lu\nwake rx=%lu support=%s",
          (unsigned long)power_stats.sleep_attempts,
          (unsigned long)power_stats.skip_pending_work,
          (unsigned long)power_stats.skip_bridge_active,
          (unsigned long)power_stats.wake_rx_packet,
          getPowerSavingSupport());
}

void MyMesh::saveIdentity(const mesh::LocalIdentity &new_id) {
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  IdentityStore store(*_fs, "");
#elif defined(ESP32)
  IdentityStore store(*_fs, "/identity");
#elif defined(RP2040_PLATFORM)
  IdentityStore store(*_fs, "/identity");
#else
#error "need to define saveIdentity()"
#endif
  store.save("_main", new_id);
}

#if defined(WITH_TCP_BRIDGE)
void MyMesh::configureTcpBridgeNodeIds() {
#if defined(WITH_BLE_BRIDGE)
  tcp_bridge.setNodeId(self_id.pub_key, PUB_KEY_SIZE);
  tcp_bridge.setSelfHash(self_id.pub_key);
#else
  bridge.setNodeId(self_id.pub_key, PUB_KEY_SIZE);
  bridge.setSelfHash(self_id.pub_key);
#endif
}
#endif

void MyMesh::clearStats() {
  radio_driver.resetStats();
  resetStats();
  ((SimpleMeshTables *)getTables())->resetStats();
  DENSE_STATS_LOCK();
  clearDenseStatsLocked();
  clearSpamStatsLocked();
  DENSE_STATS_UNLOCK();
}

void MyMesh::clearDenseStats() {
  DENSE_STATS_LOCK();
  clearDenseStatsLocked();
  DENSE_STATS_UNLOCK();
  resetCADStats();
  ((SimpleMeshTables *)getTables())->resetStats();
}

void MyMesh::clearSpamStats() {
  DENSE_STATS_LOCK();
  clearSpamStatsLocked();
  DENSE_STATS_UNLOCK();
}

void MyMesh::clearPowerStats() {
  memset(&power_stats, 0, sizeof(power_stats));
}

void MyMesh::handleCommand(uint32_t sender_timestamp, char *command, char *reply) {
  if (region_load_active) {
    if (StrHelper::isBlank(command)) {  // empty/blank line, signal to terminate 'load' operation
      region_map = temp_map;  // copy over the temp instance as new current map
      region_load_active = false;

      sprintf(reply, "OK - loaded %d regions", region_map.getCount());
    } else {
      char *np = command;
      while (*np == ' ') np++;   // skip indent
      int indent = np - command;

      char *ep = np;
      while (RegionMap::is_name_char(*ep)) ep++;
      if (*ep) { *ep++ = 0; }  // set null terminator for end of name

      while (*ep && *ep != 'F') ep++;  // look for (optional) flags

      if (indent > 0 && indent < 8 && strlen(np) > 0) {
        auto parent = load_stack[indent - 1];
        if (parent) {
          auto old = region_map.findByName(np);
          auto nw = temp_map.putRegion(np, parent->id, old ? old->id : 0);  // carry-over the current ID (if name already exists)
          if (nw) {
            nw->flags = old ? old->flags : (*ep == 'F' ? 0 : REGION_DENY_FLOOD);   // carry-over flags from curr

            load_stack[indent] = nw;  // keep pointers to parent regions, to resolve parent_id's
          }
        }
      }
      reply[0] = 0;
    }
    return;
  }

  while (*command == ' ') command++; // skip leading spaces

  if (strlen(command) > 4 && command[2] == '|') { // optional prefix (for companion radio CLI)
    memcpy(reply, command, 3);                    // reflect the prefix back
    reply += 3;
    command += 3;
  }

  if (strcmp(command, "get path.block") == 0
      || strcmp(command, "clear path.block") == 0
      || strcmp(command, "set path.block clear") == 0
      || memcmp(command, "set path.block add ", 19) == 0
      || memcmp(command, "set path.block del ", 19) == 0) {
    handlePathBlockCommand(command, reply);
    return;
  }
  if (strcmp(command, "get node.block") == 0
      || strcmp(command, "clear node.block") == 0
      || strcmp(command, "set node.block clear") == 0
      || memcmp(command, "set node.block add ", 19) == 0
      || memcmp(command, "set node.block del ", 19) == 0) {
    handleNodeBlockCommand(command, reply);
    return;
  }

  // handle ACL related commands
  if (memcmp(command, "setperm ", 8) == 0) {   // format:  setperm {pubkey-hex} {permissions-int8}
    char* hex = &command[8];
    char* sp = strchr(hex, ' ');   // look for separator char
    if (sp == NULL) {
      strcpy(reply, "Err - bad params");
    } else {
      *sp++ = 0;   // replace space with null terminator

      uint8_t pubkey[PUB_KEY_SIZE];
      int hex_len = min(sp - hex, PUB_KEY_SIZE*2);
      if (mesh::Utils::fromHex(pubkey, hex_len / 2, hex)) {
        uint8_t perms = atoi(sp);
        if (acl.applyPermissions(self_id, pubkey, hex_len / 2, perms)) {
          dirty_contacts_expiry = futureMillis(LAZY_CONTACTS_WRITE_DELAY);   // trigger acl.save()
          strcpy(reply, "OK");
        } else {
          strcpy(reply, "Err - invalid params");
        }
      } else {
        strcpy(reply, "Err - bad pubkey");
      }
    }
  } else if (sender_timestamp == 0 && strcmp(command, "get acl") == 0) {
    Serial.println("ACL:");
    for (int i = 0; i < acl.getNumClients(); i++) {
      auto c = acl.getClientByIdx(i);
      if (c->permissions == 0) continue;  // skip deleted (or guest) entries

      Serial.printf("%02X ", c->permissions);
      mesh::Utils::printHex(Serial, c->id.pub_key, PUB_KEY_SIZE);
      Serial.printf("\n");
    }
    reply[0] = 0;
  } else if (memcmp(command, "discover.neighbors", 18) == 0) {
    const char* sub = command + 18;
    while (*sub == ' ') sub++;
    if (*sub != 0) {
      strcpy(reply, "Err - discover.neighbors has no options");
    } else {
      sendNodeDiscoverReq();
      strcpy(reply, "OK - Discover sent");
    }
#if SUPPORT_DAILY_REBOOT
  } else if (memcmp(command, "set reboot.daily ", 17) == 0) {
    const char* value = &command[17];
    if (memcmp(value, "on", 2) == 0) {
      _prefs.daily_reboot_enabled = 1;
      scheduleDailyReboot();
      savePrefs();
      strcpy(reply, "OK");
    } else if (memcmp(value, "off", 3) == 0) {
      _prefs.daily_reboot_enabled = 0;
      scheduleDailyReboot();
      savePrefs();
      strcpy(reply, "OK");
    } else {
      strcpy(reply, "Error: expected on or off");
    }
  } else if (memcmp(command, "set reboot.interval ", 20) == 0) {
    int hours = atoi(&command[20]);
    if (hours >= DAILY_REBOOT_MIN_HOURS && hours <= DAILY_REBOOT_MAX_HOURS) {
      _prefs.daily_reboot_interval_hours = (uint8_t)hours;
      scheduleDailyReboot();
      savePrefs();
      strcpy(reply, "OK");
    } else {
      sprintf(reply, "Error: interval must be %u-%u hours", DAILY_REBOOT_MIN_HOURS, DAILY_REBOOT_MAX_HOURS);
    }
  } else if (memcmp(command, "get reboot", 10) == 0) {
    formatDailyRebootReply(reply);
#endif
  } else{
    _cli.handleCommand(sender_timestamp, command, reply);  // common CLI commands
  }
}

#if defined(WITH_TCP_BRIDGE)
static bool isPasswordlessTcpPathBlockCommand(const char *command) {
  return strcmp(command, "get path.block") == 0
      || strcmp(command, "clear path.block") == 0
      || strcmp(command, "set path.block clear") == 0
      || memcmp(command, "set path.block add ", 19) == 0
      || memcmp(command, "set path.block del ", 19) == 0
      || strcmp(command, "get node.block") == 0
      || strcmp(command, "clear node.block") == 0
      || strcmp(command, "set node.block clear") == 0
      || memcmp(command, "set node.block add ", 19) == 0
      || memcmp(command, "set node.block del ", 19) == 0;
}

void MyMesh::handleTcpBridgeCommand(const char *password, const char *command, char *reply, size_t reply_size) {
  char local_command[96];
  char local_reply[768];

  bool passwordless_path_block = (password == NULL || password[0] == 0) && isPasswordlessTcpPathBlockCommand(command);
  if (!passwordless_path_block && (password == NULL || strcmp(password, _prefs.password) != 0)) {
    if (reply_size > 0) {
      strncpy(reply, "Error: invalid node admin password", reply_size);
      reply[reply_size - 1] = 0;
    }
    return;
  }

  size_t command_len = 0;
  while (command_len < sizeof(local_command) - 1 && command[command_len] != 0) {
    command_len++;
  }
  memcpy(local_command, command, command_len);
  local_command[command_len] = 0;

  local_reply[0] = 0;
  handleCommand(0, local_command, local_reply);

  if (reply_size == 0) return;
  strncpy(reply, local_reply, reply_size);
  reply[reply_size - 1] = 0;
}
#endif

void MyMesh::formatDailyRebootReply(char* reply) const {
#if SUPPORT_DAILY_REBOOT
  uint32_t next_secs = 0;
  if (_prefs.daily_reboot_enabled && next_daily_reboot_uptime_ms > uptime_millis) {
    next_secs = (uint32_t)((next_daily_reboot_uptime_ms - uptime_millis) / 1000ULL);
  }
  snprintf(reply, 160, "> enabled=%s interval_hours=%u pending=%s next_secs=%lu",
           _prefs.daily_reboot_enabled ? "on" : "off",
           (uint32_t)_prefs.daily_reboot_interval_hours,
           daily_reboot_pending ? "yes" : "no",
           (unsigned long)next_secs);
#else
  strcpy(reply, "Error: daily reboot not supported by this build");
#endif
}

void MyMesh::checkDailyReboot() {
#if SUPPORT_DAILY_REBOOT
  if (!_prefs.daily_reboot_enabled || next_daily_reboot_uptime_ms == 0) return;
  if (!daily_reboot_pending && uptime_millis >= next_daily_reboot_uptime_ms) {
    daily_reboot_pending = true;
  }
  if (!daily_reboot_pending) return;
  if (hasOutboundWork()) return;
  MESH_DEBUG_PRINTLN("Daily reboot timer elapsed, rebooting");
  delay(DAILY_REBOOT_GRACE_MILLIS);
  board.reboot();
#endif
}

void MyMesh::loop() {
  // Primary (non-MQTT) bridge needs pumping from the main loop.
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
  {
    uint32_t max_tx_budget = getMaxTxBudget();
    uint32_t remaining_tx_budget = getEffectiveRemainingTxBudget();
    uint32_t used_tx_budget = remaining_tx_budget >= max_tx_budget ? 0 : (max_tx_budget - remaining_tx_budget);
    tcp_bridge.setRfDutyStats(used_tx_budget, max_tx_budget, getDutyCycleWindowMs(),
                              getDutyCycleLimitCentiPct(), getTxBudgetUsedCentiPct(), getTotalAirTime(),
                              (int16_t)_radio->getNoiseFloor(), (int16_t)radio_driver.getLastRSSI(),
                              (int16_t)(radio_driver.getLastSNR() * 4), getDenseNeighborCount());
  }
  tcp_bridge.loop();
  ble_bridge.loop();
  if (tcp_bridge.pollJustConnected()) sendSelfAdvertisement(500, true);
#elif defined(WITH_TCP_BRIDGE)
  {
    uint32_t max_tx_budget = getMaxTxBudget();
    uint32_t remaining_tx_budget = getEffectiveRemainingTxBudget();
    uint32_t used_tx_budget = remaining_tx_budget >= max_tx_budget ? 0 : (max_tx_budget - remaining_tx_budget);
    bridge.setRfDutyStats(used_tx_budget, max_tx_budget, getDutyCycleWindowMs(),
                          getDutyCycleLimitCentiPct(), getTxBudgetUsedCentiPct(), getTotalAirTime(),
                          (int16_t)_radio->getNoiseFloor(), (int16_t)radio_driver.getLastRSSI(),
                          (int16_t)(radio_driver.getLastSNR() * 4), getDenseNeighborCount());
  }
  bridge.loop();
  if (bridge.pollJustConnected()) sendSelfAdvertisement(500, true);
#elif defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
  bridge.loop();
#endif
#ifdef WITH_MQTT_BRIDGE
  // MQTT bridge runs in its own FreeRTOS task; nothing to pump from here.
#ifdef WITH_SNMP
  if (_snmp_agent.isRunning()) {
    static unsigned long last_snmp_stats = 0;
    unsigned long now_ms = millis();
    if (now_ms - last_snmp_stats >= 2000) {
      last_snmp_stats = now_ms;
      _snmp_agent.updateRadioStats(
        radio_driver.getPacketsRecv(), radio_driver.getPacketsSent(),
        radio_driver.getPacketsRecvErrors(),
        (int16_t)_radio->getNoiseFloor(),
        (int16_t)radio_driver.getLastRSSI(),
        (int16_t)(radio_driver.getLastSNR() * 4),
        getNumSentFlood(), getNumSentDirect(),
        getNumRecvFlood(), getNumRecvDirect(),
        getTotalAirTime() / 1000, uptime_millis / 1000);
    }
  }
#endif
#endif

  mesh::Mesh::loop();

  if (next_flood_advert && millisHasNowPassed(next_flood_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    uint32_t delay_millis = 0;
    if (pkt) sendFloodScoped(default_scope, pkt, delay_millis, _prefs.path_hash_mode + 1);

    updateFloodAdvertTimer(); // schedule next flood advert
    updateAdvertTimer();      // also schedule local advert (so they don't overlap)
  } else if (next_local_advert && millisHasNowPassed(next_local_advert)) {
    mesh::Packet *pkt = createSelfAdvert();
    if (pkt) sendZeroHop(pkt);

    updateAdvertTimer(); // schedule next local advert
  }

  if (set_radio_at && millisHasNowPassed(set_radio_at)) { // apply pending (temporary) radio params
    set_radio_at = 0;                                     // clear timer
    radio_driver.setParams(pending_freq, pending_bw, pending_sf, pending_cr);
    MESH_DEBUG_PRINTLN("Temp radio params");
  }

  if (revert_radio_at && millisHasNowPassed(revert_radio_at)) { // revert radio params to orig
    revert_radio_at = 0;                                        // clear timer
    radio_driver.setParams(_prefs.freq, _prefs.bw, _prefs.sf, _prefs.cr);
    MESH_DEBUG_PRINTLN("Radio params restored");
  }

  // is pending dirty contacts write needed?
  if (dirty_contacts_expiry && millisHasNowPassed(dirty_contacts_expiry)) {
    acl.save(_fs);
    dirty_contacts_expiry = 0;
  }

  // update uptime
  uint32_t now = millis();
  uptime_millis += now - last_millis;
  last_millis = now;

  checkDailyReboot();
}

bool MyMesh::isBridgeActive() const {
  bool active = false;
#if defined(WITH_TCP_BRIDGE) && defined(WITH_BLE_BRIDGE)
  active = active || tcp_bridge.isRunning() || ble_bridge.isRunning();
#elif defined(WITH_TCP_BRIDGE) || defined(WITH_RS232_BRIDGE) || defined(WITH_ESPNOW_BRIDGE) || defined(WITH_BLE_BRIDGE)
  active = active || bridge.isRunning();
#endif
#ifdef WITH_MQTT_BRIDGE
  active = active || (mqtt_bridge && mqtt_bridge->isRunning());
#endif
  return active;
}

bool MyMesh::hasOutboundWork() const {
  return _mgr->getOutboundTotal() > 0;
}

// To check if there is pending work
bool MyMesh::hasPendingWork() const {
  return isBridgeActive() || hasOutboundWork();
}

void MyMesh::recordSleepAttempt() {
  power_stats.sleep_attempts++;
}

void MyMesh::recordSleepSkipPendingWork() {
  power_stats.skip_pending_work++;
}

void MyMesh::recordSleepSkipBridgeActive() {
  power_stats.skip_bridge_active++;
}

void MyMesh::recordStartupWakeReason() {
  if (board.getStartupReason() == BD_STARTUP_RX_PACKET) {
    power_stats.wake_rx_packet++;
  }
}
