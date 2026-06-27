#pragma once

#include "helpers/CommonCLI.h"
#include "Packet.h"

namespace mesh {

inline uint8_t minHopLimit(uint8_t a, uint8_t b) {
  if (a == 0) return b;
  if (b == 0) return a;
  return a < b ? a : b;
}

/**
 * Effective relay hop cap before this node appends its path entry.
 *
 * - flood.max: default cap for all flood packets (0 = no cap).
 * - flood.max.advert: advert cap; may exceed flood.max so neighbours stay visible.
 * - flood.max.unscoped: cap for ROUTE_TYPE_FLOOD (no transport scope); never above flood.max.
 * - ROUTE_TYPE_TRANSPORT_FLOOD (scoped): flood.max.
 */
inline uint8_t effectiveFloodMaxHopLimit(const NodePrefs &prefs, const Packet *packet) {
  if (!packet || !packet->isRouteFlood()) return 0;

  const uint8_t flood_max = prefs.flood_max;

  if (packet->getPayloadType() == PAYLOAD_TYPE_ADVERT) {
    if (prefs.flood_max_advert > 0) return prefs.flood_max_advert;
    return flood_max;
  }

  if (packet->getRouteType() == ROUTE_TYPE_FLOOD) {
    if (prefs.flood_max_unscoped > 0) {
      return minHopLimit(prefs.flood_max_unscoped, flood_max);
    }
    return flood_max;
  }

  return flood_max;
}

inline bool exceedsFloodMaxHopLimit(const NodePrefs &prefs, const Packet *packet) {
  if (!packet || !packet->isRouteFlood()) return false;

  const uint8_t limit = effectiveFloodMaxHopLimit(prefs, packet);
  if (limit == 0) return false;

  return packet->getPathHashCount() >= limit;
}

}  // namespace mesh
