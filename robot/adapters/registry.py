from .rover import RoverRobot
from .sim import SimRobot

# `rover` is any robot speaking docs/robot/rover-protocol.md — the ESP32 firmware or the fake
# rover; it needs an `address`. An Arduino UNO Q adapter lived here until Phase 7: no firmware for
# it existed and it could not drive, so it was removed. Older databases may still hold 'unoq'
# robots and runs; the schema accepts the value and /deploy refuses it.
ADAPTERS = {"sim": SimRobot, "rover": RoverRobot}


def get_robot(kind: str = "sim", **kwargs):
    if kind not in ADAPTERS:
        raise ValueError(f"unknown robot kind {kind!r}; expected one of {sorted(ADAPTERS)}")
    return ADAPTERS[kind](**kwargs)
