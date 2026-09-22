# Third-party components and licences

ScanBorn's own code is MIT-licensed (see [`LICENSE`](../LICENSE)). This lists what the code actually
uses. Each licence was read on 2026-09-16 from the component's own metadata on the development
machine — Python package metadata, `package.json`, Maven POMs in the Gradle cache, or the licence
file shipped with the library — unless the row says otherwise. Versions are the ones installed
there. Check the upstream terms before redistributing a build.

## Orchestrator and pipeline (Python)

| Component | Version | Licence | Used for |
|---|---|---|---|
| FastAPI | 0.115.6 | MIT | REST API |
| Uvicorn | 0.34.0 | BSD-3-Clause | ASGI server |
| python-multipart | 0.0.20 | Apache-2.0 | chunked uploads |
| websockets | 15.0.1 | BSD-3-Clause | `/ws/status`, live deployments |
| HTTPX | 0.28.1 | BSD-3-Clause | SDK, rover adapter, remote planners |
| NumPy | 2.2.1 | BSD | everything numeric |
| SciPy | 1.15.2 | BSD | navmesh gap fill, clearance field |
| PyYAML | 6.0.3 | MIT | rules and rover spec |
| Open3D | not installed | see upstream | optional TSDF fusion |
| Ultralytics (YOLO-World) | not installed | see upstream (AGPL-3.0 or an Ultralytics enterprise licence, per Ultralytics) | optional 2D labelling |
| qai-hub, torch | qai-hub not installed; torch 2.5.1 | torch: BSD-3-Clause; qai-hub and the AI Hub service: Qualcomm's terms, see upstream | optional AI Hub export (`requirements-npu.txt`) |

## Dashboard (JavaScript)

| Component | Version | Licence | Used for |
|---|---|---|---|
| React, React DOM | 18.3.1 | MIT | UI |
| three.js | 0.185.1 | MIT | 3D views |
| GSAP | 3.15.0 | **GSAP Standard "no charge" licence** (<https://gsap.com/standard-license>), not an open-source licence | landing page animation |
| Vite, @vitejs/plugin-react, Vitest | 8.3.0, 6.1.1, 5.0.0 | MIT | build and tests (development only) |
| Archivo, JetBrains Mono | loaded from Google Fonts | see upstream (both are published under the SIL Open Font License) | type |

## Android app

| Component | Version | Licence | Used for |
|---|---|---|---|
| llama.cpp / ggml (vendored, `MOBILE-APP/app/src/main/cpp/llama`) | upstream commit not recorded | MIT (licence file in that folder) | on-device inference |
| Qwen2.5-1.5B-Instruct GGUF (not in the repository) | Q4_K_M | Apache-2.0 (model card on Hugging Face) | on-device model |
| ARCore (`com.google.ar:core`) | 1.44.0 | ARCore Additional Terms of Service | scanning |
| ML Kit text recognition (`com.google.mlkit:text-recognition`) | 16.0.1 | ML Kit Terms of Service | OCR |
| OkHttp | 4.12.0 | Apache-2.0 | uploads to the orchestrator |
| AndroidX (Core, Compose, Material 3, Navigation, Lifecycle, DataStore, Room, AppCompat) | see `gradle/libs.versions.toml` | Apache-2.0 (read from the Room, Material 3 and Core POMs) | app framework |

## Rover firmware

| Component | Version | Licence | Used for |
|---|---|---|---|
| Arduino core for ESP32 (Espressif) | 3.3.11 | LGPL-2.1-or-later (core headers) | WiFi, mDNS, HTTP server, UDP, flash settings |
| ArduinoJson | 7.4.3 | MIT | protocol messages |
| ESP32Encoder | 5.0.0 | BSD-style, with an acknowledgment clause: redistributions must retain "This product includes software developed by the Universidad de Palermo, Argentina (http://www.palermo.edu/)" | wheel encoders |
| Adafruit MPU6050 | 2.2.9 | BSD | gyro |
| Adafruit BusIO | 1.17.4 | MIT | I²C (Adafruit MPU6050 dependency) |
| Adafruit Unified Sensor | 1.1.15 | Apache-2.0 | Adafruit MPU6050 dependency |
| VL53L0X (Pololu) | 1.3.1 | MIT-style (Pololu; see its `LICENSE.txt`) | front range sensor |

## Tools used but not shipped

Espressif's QEMU (ESP32 emulator, for `nox -s firmware_qemu`), arduino-cli, MinGW GCC, pytest,
flake8, mypy, nox, Android Gradle Plugin and Kotlin. They run on the development machine and are
not part of any build output.

## Hackathon experiment

`experiments/rc_buggy_snapdragon/` needs ONNX Runtime with its QNN execution provider and pyserial
on a Snapdragon device; none of them are installed here, so their licences were not read.
