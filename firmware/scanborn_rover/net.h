// The rover on the network: WiFi, mDNS, the HTTP routes and the two UDP streams
// (docs/robot/rover-protocol.md §3-4).
#pragma once

// WiFi from secrets.h, the HTTP routes and the heartbeat socket. mDNS starts once WiFi connects.
void net_begin();
// Serve HTTP requests and read heartbeats. Call every loop.
void net_poll();
// One telemetry datagram to the host that loaded the mission (nothing while idle).
void net_send_telemetry();
