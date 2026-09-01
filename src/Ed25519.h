#pragma once

#include <stddef.h>
#include <stdint.h>

extern "C" {
#include <ed_25519.h>
}

namespace Ed25519 {

inline int verify(const uint8_t* signature,
                  const uint8_t* public_key,
                  const uint8_t* message,
                  size_t message_len) {
  return ed25519_verify(signature, message, message_len, public_key);
}

}  // namespace Ed25519
