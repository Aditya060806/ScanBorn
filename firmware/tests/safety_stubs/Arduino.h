#pragma once
#include <cstdint>
constexpr int INPUT_PULLUP = 2;
constexpr int LOW = 0;
extern uint32_t test_now;
extern int test_button;
extern uint32_t test_battery_mv;
inline uint32_t millis() { return test_now; }
inline void pinMode(int, int) {}
inline int digitalRead(int) { return test_button; }
inline uint32_t analogReadMilliVolts(int) { return test_battery_mv; }
