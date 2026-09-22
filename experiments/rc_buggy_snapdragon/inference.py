"""Drive the RC buggy from Buggy.onnx through ONNX Runtime's QNN execution provider.

A July 2026 hackathon experiment on a Snapdragon device, kept for the record; it is not part of the
ScanBorn pipeline (README.md in this folder). Importing this module has no side effects: the QNN
provider is registered and the serial port opened only in main().
"""
import time

import numpy as np

MODEL_PATH = "Buggy.onnx"
SERIAL_PORT = "/dev/ttyUSB0"
EP_NAME = "QNNExecutionProvider"


def open_session(model_path: str = MODEL_PATH):
    """Register the QNN provider and load the model on it. Returns (ort, session, action index)."""
    import onnxruntime as ort
    import onnxruntime_qnn as qnn_ep

    ort.register_execution_provider_library(EP_NAME, qnn_ep.get_library_path())
    devices = [d for d in ort.get_ep_devices() if d.ep_name == EP_NAME]
    if not devices:
        raise RuntimeError("QNN EP device not found")
    print(f"Found {len(devices)} QNN EP device(s)")

    options = ort.SessionOptions()
    options.add_provider_for_devices(devices, {
        "backend_path": qnn_ep.get_qnn_htp_path(),
        "htp_performance_mode": "burst",
    })
    session = ort.InferenceSession(model_path, sess_options=options)
    print("Session created successfully on QNN EP.")

    output_names = [o.name for o in session.get_outputs()]
    return ort, session, output_names.index("deterministic_continuous_actions")


def build_observation(swivel_angle_rad, prev_steer, prev_throttle, local_target_pos, forward_dot,
                      right_dot, distance_to_target, local_velocity, curriculum_progress):
    """The model's 14-value observation, in the order it was trained with."""
    return np.array([[
        np.sin(swivel_angle_rad),
        np.cos(swivel_angle_rad),
        prev_steer,
        prev_throttle,
        local_target_pos[0],
        local_target_pos[1],
        local_target_pos[2],
        forward_dot,
        right_dot,
        distance_to_target,
        local_velocity[0],
        local_velocity[1],
        local_velocity[2],
        curriculum_progress,
    ]], dtype=np.float32)


def run_inference(session, action_index: int, obs) -> tuple:
    result = session.run(None, {
        "obs_0": obs,
        "action_masks": np.array([[1.0]], dtype=np.float32),
    })
    steer, throttle = result[action_index][0]
    return float(steer), float(throttle)


def main(iterations: int = 200) -> None:
    """Debug run: a fixed observation, sent to the Arduino as `steer,throttle` lines."""
    import serial

    ort, session, action_index = open_session()
    arduino = serial.Serial(SERIAL_PORT, 115200, timeout=0.1)
    time.sleep(2)  # the Arduino resets when the port opens

    steer = throttle = 0.0
    start = time.time()
    for _ in range(iterations):
        obs = build_observation(
            swivel_angle_rad=0.0, prev_steer=0.0, prev_throttle=0.0,
            local_target_pos=(0.0, 0.0, 5.0), forward_dot=1.0, right_dot=0.0,
            distance_to_target=5.0, local_velocity=(0.0, 0.0, 0.0), curriculum_progress=0.0,
        )
        steer, throttle = run_inference(session, action_index, obs)
        arduino.write(f"{steer:.4f},{throttle:.4f}\n".encode())

    elapsed = time.time() - start
    print(f"\n{iterations} inferences in {elapsed:.3f}s")
    print(f"Average: {elapsed / iterations * 1000:.2f} ms/inference")
    print(f"Max control frequency: {iterations / elapsed:.1f} Hz")
    print(f"\nLast output -> Steer: {steer:.4f}, Throttle: {throttle:.4f}")

    del session
    ort.unregister_execution_provider_library(EP_NAME)


if __name__ == "__main__":
    main()
