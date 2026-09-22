// The rover firmware's arithmetic, run on the laptop against the golden vectors.
//
//   nox -s firmware_host
//
// which builds this with g++ -std=c++14 -msse2 -mfpmath=sse -I firmware/scanborn_rover and runs
// it. The same checks run on the rover in the SELFTEST build (selftest.h).
#include <cstdio>

#include "selftest.h"
#include "range_health.h"

static void print_line(const char* line) { std::printf("%s\n", line); }

int main() {
  int failures = selftest::run(print_line);
  if (range_health::fresh(true, false, 0, 0) ||
      range_health::fresh(false, true, 1, 0) ||
      !range_health::fresh(true, true, 250, 0) ||
      range_health::fresh(true, true, 251, 0) ||
      !range_health::fresh(true, true, 20, UINT32_MAX - 20) ||
      range_health::fresh(true, true, 300, UINT32_MAX - 20)) {
    print_line("FAIL range freshness admission/expiry/rollover"); failures++;
  }
  const rover::Point square[] = {{0,0},{1,0},{1,1},{0,1},{0,0}};
  rover::Policy policy = {};
  rover::Command command = {};
  int index = 0;
  if (!rover::control_step({0,0,0}, square, 5, &index, {0,0}, policy, 0.35, 0.02, &command)) {
    print_line("FAIL closed route completed before traversing path"); failures++;
  }
  index = 4;
  if (rover::control_step({0,0,0}, square, 5, &index, {0,0}, policy, 0.35, 0.02, &command)) {
    print_line("FAIL closed route did not stop on final path segment"); failures++;
  }
  std::printf("%s\n", failures == 0 ? "PASS" : "FAIL");
  return failures == 0 ? 0 : 1;
}
