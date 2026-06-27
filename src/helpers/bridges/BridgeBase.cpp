#include "BridgeBase.h"

#include "helpers/FloodLimits.h"

#include <Arduino.h>
#include <string.h>

namespace {

uint8_t bridgeLocalInjectPriority(const mesh::Packet *packet) {
  if (packet->isRouteFlood()) {
    return packet->getPayloadType() == PAYLOAD_TYPE_ADVERT ? 3 : 1;
  }
  return 0;
}

void prepareBridgeLocalInject(mesh::Packet *packet, const uint8_t *self_hash, bool has_self_hash) {
  if (packet->isRouteFlood()) {
    uint8_t hash_size = packet->getPathHashSize();
    if (hash_size == 0 || hash_size > 3) hash_size = 1;
    uint8_t max_count = MAX_PATH_SIZE / hash_size;
    uint8_t current_count = packet->getPathHashCount();
    if (has_self_hash && current_count < max_count) {
      memcpy(&packet->path[current_count * hash_size], self_hash, hash_size);
      current_count++;
    }
    while (current_count < max_count) {
      memset(&packet->path[current_count * hash_size], 0, hash_size);
      current_count++;
    }
    packet->setPathHashCount(max_count);
  } else if (packet->isRouteDirect()) {
    packet->setPathHashCount(0);
  }
}

}  // namespace

bool BridgeBase::isRunning() const {
  return _initialized;
}

bool BridgeBase::exceedsFloodMaxPath(const NodePrefs *prefs, const mesh::Packet *packet) {
  return mesh::exceedsFloodMaxHopLimit(*prefs, packet);
}

const char *BridgeBase::getLogDateTime() {
  static char tmp[32];
  uint32_t now = _rtc->getCurrentTime();
  DateTime dt = DateTime(now);
  sprintf(tmp, "%02d:%02d:%02d - %d/%d/%d U", dt.hour(), dt.minute(), dt.second(), dt.day(), dt.month(),
          dt.year());
  return tmp;
}

uint16_t BridgeBase::fletcher16(const uint8_t *data, size_t len) {
  uint8_t sum1 = 0, sum2 = 0;

  for (size_t i = 0; i < len; i++) {
    sum1 = (sum1 + data[i]) % 255;
    sum2 = (sum2 + sum1) % 255;
  }

  return (sum2 << 8) | sum1;
}

bool BridgeBase::validateChecksum(const uint8_t *data, size_t len, uint16_t received_checksum) {
  uint16_t calculated_checksum = fletcher16(data, len);
  return received_checksum == calculated_checksum;
}

void BridgeBase::handleReceivedPacket(mesh::Packet *packet) {
  // Guard against uninitialized state
  if (_initialized == false) {
    BRIDGE_DEBUG_PRINTLN("RX packet received before initialization\n");
    _mgr->free(packet);
    return;
  }

  if (!_seen_packets.hasSeen(packet)) {
    packet->markReceivedFromBridge();
    // local mode injects bridge traffic once on RF and prevents normal multi-hop forwarding.
    if (_prefs->bridge_rf == BRIDGE_RF_LOCAL) {
      prepareBridgeLocalInject(packet, _self_hash, _has_self_hash);
      _mgr->queueOutbound(packet, bridgeLocalInjectPriority(packet), millis() + _prefs->bridge_delay);
    } else {
      // bridge_delay provides a buffer to prevent immediate processing conflicts in the mesh network.
      _mgr->queueInbound(packet, millis() + _prefs->bridge_delay);
    }
  } else {
    BRIDGE_DEBUG_PRINTLN("BridgeBase: duplicate bridge packet dropped\n");
    _mgr->free(packet);
  }
}
