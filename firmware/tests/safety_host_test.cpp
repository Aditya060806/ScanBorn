// Compile the production Safety class; replace only the hardware API boundary.
#include <cstdio>
#include <cstring>
#include "safety.h"
#include "config.h"
uint32_t test_now = 0, test_battery_mv = 3000;
int test_button = 1;
bool test_tof_init = true, test_tof_ready = false, test_tof_timeout = false;
uint16_t test_range = 1000;
int checks = 0, failures = 0;
void check(bool ok, const char* label) {
  checks++; if (!ok) { failures++; std::printf("FAIL %s\n", label); }
}
bool reason(const char* actual, const char* expected) {
  return actual && std::strcmp(actual, expected) == 0;
}
Safety reset() {
  test_now = 0; test_battery_mv = 3000; test_button = 1;
  test_tof_init = true; test_tof_ready = false; test_tof_timeout = false; test_range = 1000;
  Safety safety; safety.begin(); return safety;
}
void sample() { test_tof_ready = true; }
int main() {
  auto safety = reset();
  check(!safety.tof_ok(), "initialization alone cannot admit motion");
  check(reason(safety.check(true), "tof_fault"), "no first range fails closed");
  sample(); check(safety.check(true) == nullptr && safety.tof_ok(), "fresh clear range admits");
  test_now = 250; check(safety.check(true) == nullptr, "freshness inclusive boundary");
  test_now++; check(reason(safety.check(true), "tof_fault"), "missing interrupt expires range");
  sample(); check(safety.check(true) == nullptr, "new measurement restores health");
  test_range = 149; sample(); check(reason(safety.check(true), "obstacle"), "near obstacle stops");
  test_range = 150; sample(); check(safety.check(true) == nullptr, "stop distance boundary");
  test_range = 8190; sample();
  check(safety.check(true) == nullptr && safety.range_m() < 0 && safety.tof_ok(), "out of range is not an obstacle");
  test_tof_timeout = true; sample();
  check(reason(safety.check(true), "tof_fault") && !safety.tof_ok(), "sensor timeout invalidates");
  safety = reset(); test_tof_init = false; safety.begin();
  check(reason(safety.check(true), "tof_fault"), "sensor initialization failure refuses");
  safety = reset(); test_now = UINT32_MAX - 10; sample(); safety.check(false);
  test_now = 20; check(safety.tof_ok(), "freshness crosses millis rollover");
  test_now = 241; check(!safety.tof_ok(), "rollover range still expires");

  safety = reset(); test_button = 0;
  check(safety.check(false) == nullptr, "button debounce begins");
  test_now = config::BUTTON_DEBOUNCE_MS - 1;
  check(safety.check(false) == nullptr, "button bounce does not latch");
  test_now++; check(reason(safety.check(false), "button"), "button debounced press stops");
  check(safety.check(false) == nullptr, "held button does not repeatedly relatch");
  test_button = 1; safety.check(false); test_now += 30; safety.check(false);
  test_button = 0; safety.check(false); test_now += 30;
  check(reason(safety.check(false), "button"), "release permits next press");

  safety = reset(); test_battery_mv = 2200; sample();
  check(safety.check(true) == nullptr, "battery dip starts grace period");
  test_now = config::BATTERY_LOW_MS - 1; sample();
  check(safety.check(true) == nullptr, "battery grace boundary");
  test_now++; sample(); check(reason(safety.check(true), "battery_low"), "sustained low battery stops");
  safety = reset(); test_battery_mv = 0;
  safety.check(false); test_now = 5000;
  check(safety.check(false) == nullptr, "USB bench idle does not latch battery fault");
  sample(); safety.check(true); test_now += config::BATTERY_LOW_MS; sample();
  check(reason(safety.check(true), "battery_low"), "missing battery during drive stops");
  std::printf("%d production safety checks; %d failures\n", checks, failures);
  return failures ? 1 : 0;
}
