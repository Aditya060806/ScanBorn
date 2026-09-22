# RC buggy on Snapdragon — hackathon experiment (July 2026)

Kept for the record. **Not part of the ScanBorn pipeline, not the project's robot, and not run
since the hackathon.** Moved here from `twin/` in Phase 7.

| File | What it is |
|---|---|
| `Buggy.onnx` | A driving policy for an Ackermann-steered RC buggy. Its tensor names (`obs_0`, `action_masks`, `deterministic_continuous_actions`, `version_number`, `memory_size_vector`) are Unity ML-Agents' export format, so it was trained in a Unity project — that project is not in this repository, and nothing here records how it was trained or how well it drove |
| `inference.py` | Loads the model through ONNX Runtime's QNN execution provider (Qualcomm's Hexagon NPU backend) on a Snapdragon device and sends `steer,throttle` lines to an Arduino over serial. Its `main()` is a 200-step timing loop on a fixed observation. Needs `onnxruntime`, `onnxruntime-qnn` and `pyserial` on that device |
| `buggy_motor_controller.ino` | Arduino sketch: reads `steer,throttle` lines at 115200 baud and drives a steering servo (pin 9) and a throttle ESC (pin 10), going neutral after 500 ms without a command. The Arduino IDE wants it in a folder named `buggy_motor_controller` |

## Why it is not the target robot

- **Different robot.** The buggy steers like a car; the project's robot is a two-wheel
  differential-drive ESP32 rover (master plan §4.3, `robot/specs/rover.yaml`).
- **Different observation.** The model reads 14 values (swivel angle, previous commands, target
  position and velocity in the buggy's frame, a curriculum value). The pipeline's policy reads two:
  `[d·cos e, e]` (`policy/controller.py`).
- **Different hardware.** It needs a Snapdragon NPU. The project's phone is an Exynos Galaxy S24 FE
  and the rover runs its controller on the ESP32.
- **No link to the twin.** Nothing places the buggy in a scanned room; the pipeline's rover is
  placed at the scan's home marker.
