#pragma once
#include <cstdint>
extern bool test_tof_init, test_tof_ready, test_tof_timeout;
extern uint16_t test_range;
class VL53L0X {
 public:
  enum { RESULT_INTERRUPT_STATUS = 0x13 };
  void setTimeout(int) {}
  bool init() { return test_tof_init; }
  void startContinuous(uint32_t) {}
  int readReg(int) { return test_tof_ready ? 1 : 0; }
  uint16_t readRangeContinuousMillimeters() { test_tof_ready = false; return test_range; }
  bool timeoutOccurred() { return test_tof_timeout; }
};
