#pragma once
#include <cstdint>

// A successful initialization is not a measurement. Allow several 33 ms sensor periods,
// then fail closed if interrupts stop arriving. Unsigned age handles millis rollover.
namespace range_health {
constexpr uint32_t MAX_AGE_MS = 250;
inline bool fresh(bool initialized, bool measured, uint32_t now, uint32_t last) {
  return initialized && measured && uint32_t(now - last) <= MAX_AGE_MS;
}
}
