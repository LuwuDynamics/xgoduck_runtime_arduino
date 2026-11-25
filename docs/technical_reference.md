# XGO Duck runtime: technical reference

This page describes motion pinned to upstream master [`b401d85`](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/commit/b401d85d20bb5bfd0237242a8a5ab6847fc962a2) (2026-10-31), with the existing AI/gamepad/calibration/ToF adapters. Historical PAUSE and software-center behavior does not apply. For first build and calibration, start with [README.md](../README.md) or [README_CN.md](../README_CN.md).

This project runs walking, get-up, and pick policies on an XGODuck built around the Arduino Uno Q.

It is based on [Microduck](https://github.com/pollen-robotics/microduck) by [Pollen Robotics](https://pollen-robotics.com/microduck/).

The policies here follow that project: 15 servos, a 50 Hz neural controller, and the same joint order.

## What runs where

The Uno Q is two computers.

| | Host | MCU |
| --- | --- | --- |
| Silicon | Qualcomm Linux | STM32U585, Zephyr |
| Rate | 50 Hz policy | 100 Hz bus loop |
| Work | ONNX Runtime, one thread; web UI on port 9527 | 15-servo bus, QMI8658, Bridge report thread |
| Link | Official Arduino App container | `Bridge` at 2 Mbps |

The MCU loop does not wait for Linux. It publishes a 240-byte `duck_state` snapshot at 100 Hz. The host filters each snapshot on arrival and the 50 Hz thread reads the latest one. A late cycle is skipped. There is no queued backlog of actions.

```text
browser :9527
    |
Linux  50 Hz ONNX  ---- duck_command (80 bytes) ---->  STM32 100 Hz
    ^                                                      |
    +------------- duck_state (240 bytes) -----------------+
                                                           |
                                              Serial1 1 Mbps, IDs 10-14, 20-24, 30-34
                                              Wire, QMI8658
```

Joint angles, rates, and targets are float32 degrees. Servo positions are saturated to the encoder range 0–4095. Non-finite targets are rejected. A finite angle outside ±180° is not rejected.

## Servo map

IDs follow Microduck. The bus always addresses all 15, in this order:

| IDs | Role |
| --- | --- |
| 10–14 | Leg |
| 20–24 | Leg |
| 30–33 | Neck and head. Policy order inside the 14-D action is 10–14, 30–33, 20–24. |
| 34 | Mouth. It is not part of the policy action. The web UI commands it from 0° to 30°. |

The ordinary walking home pose in `python/rl_core.py` is left/right hip pitch −22°/+22°, knee −2°/+2°, ankle +20°/−20°, hip roll −5°/+5°, neck and head pitch 20°, mouth 0°. Its `action_scale` is 1. Roller applies its fixed leg angles over the ONNX home and uses scale 0.6; it is off at startup and selectable using the stable page's Roller switch. Use it only with the corresponding roller-equipped robot. MCU angles use each servo's hardware center at encoder position 2047.

Hardware centers are stored in the servos. Legacy `data/zero_pos.json` files are preserved as backups and no longer applied. Nondefault legacy software zeros require the hardware-centering workflow before arming; `data/hardware-centers.json` records acknowledged progress for this robot.

## First-time servo setup

**Scan all IDs** checks every address from **0 through 253** at the configured servo bus rate (1 Mbps). **Stop scan** cancels the scan and keeps the IDs already found. Click a result, or type any valid address into **Current / target ID**. **Read** also accepts any address in this range. Addresses 254 (broadcast) and 255 are not assignable servo IDs.

To rename a servo, enter its current ID and the desired **Change ID to** value, then click **Change ID**. The app checks that the source responds and the destination is free, writes the ID, and verifies replies at the new and old addresses. A Bridge timeout aborts scanning; it is not reported as a missing servo. The normal Control page still monitors the robot's configured 15 joint IDs.

Deploy `assets/`, `python/`, and `sketch/` together for this feature; updating only the page or Python is insufficient. Restart the app through App Lab / App CLI so the updated sketch is compiled and uploaded.

Do this before the servos are installed in the frame. Connect **one servo at a time**. Two servos with the same ID on the bus will both accept a write.

1. Open **Servo setup** (`/servo.html`) and enter setup.
2. With torque off, turn the joint by hand to its intended mechanical midpoint. Assign IDs **10–14** and **20–24** for the legs, or **30–34** for the neck, head, and mouth.
3. Click **Change ID**. After verifying the new address, the app sets the current position as the hardware midpoint, enables voltage and overcurrent protection on that servo, and verifies the 2 A threshold (308 counts at 6.5 mA per count). To repeat setup after a partial write, keep the joint at the intended midpoint, scan again, and enter its current ID as the new ID. Use **Reset Mid** for a midpoint-only adjustment.
4. Write stored KP/KD. The page defaults are **KP = 5** and **KD = 10**. The write unlocks `0x37` and stores both bytes at `0x15`.
5. Disconnect that servo and repeat until every ID exists exactly once.
6. Install the servos in the frame and finish the wiring: servo bus on D0/D1 (`Serial1`, 1 Mbps), QMI8658 on D20/D21 (`Wire`, 400 kHz, address `0x6A` then `0x6B`).

Startup, arming and calibration preserve the leg/head servos’ existing KP/KD. Only mouth ID 34 receives temporary KP = 10 and KD = 20 (addresses 50 and 51). Explicit Set KpKd still writes the selected servo. Previously applied temporary gains are not restored automatically; a servo power cycle reloads its saved values. Torque off clears torque (address 40), and goal-position writes are suppressed while disarmed.

## Calibration

Do this after wiring and confirming every servo ID answers.

1. Open **Calibration** (`/calibrate.html`). All servos unload.
2. Match the four reference photos and click **Calibrate all 15 servos**. The STM32 confirms both the reset and the reference write for every joint while torque stays off.
3. Position the neck parallel to the torso and click **Calibrate ID 30**. When the center is saved, click **Unload all**.
4. Calibrate the IMU, then use **Default pose** at the bottom of the page with Duck supported before trying Walk / get up.

Legacy software-center migration is described above and in the AI guide. Do not replace old zeros with 2047 to skip calibration. The page also offers Roller walking, independent model amplitude/action filters, IMU filtering, and separate all-servo protection-switch/current broadcasts; protection settings are changed only when explicitly requested from the page.

## Running

The current sketch uses the Arduino Zephyr core 1.0.0 and the RouterBridge and RPClite sources in `sketch/libraries/`. The build profile selects those sources for both App Lab imports and precompiled releases. Python dependencies are listed in `python/requirements.txt`.

Stop the App with Duck supported and torque released. From the installed App directory, run:

```bash
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
sudo stty -F /dev/ttyHS1 speed
```

The installer uses the bundled ARM64 Router from Arduino’s reliable-protocol test build (`45cccf1`). The Router starts at 115200 baud, detects the protocol and negotiates up to 2000000 baud with the paired sketch. After the handshake, the last command should report `2000000`. The shared Router controls the STM32 reset line, so a Router restart interrupts motion. To roll back to a Duck release that starts at a fixed 2 Mbaud, stop the running App, run that older release’s `tools/router-2m.sh` and start its matching firmware. [Arduino Router protocol](https://github.com/arduino/arduino-router/pull/73) · [Arduino RouterBridge protocol](https://github.com/arduino-libraries/Arduino_RouterBridge/pull/81)

If an isolated CLI build tries to re-download a toolchain that is already on the board and GitHub is unreachable, run `python3 tools/reuse-toolchain.py` and start the app again.

GCC 14 can tail-call `k_thread_name_set` at the end of `setup()` with a Thumb `JUMP24` from the loadable sketch into flash. The Uno Q loader then returns ENOEXEC (−8) and the MCU never starts. The `asm volatile("nop")` at the end of `setup()` keeps a long call. `python3 tools/verify-elf.py` checks the built ELF for the same class of short branch to an unresolved firmware symbol.

The web UI is served on port 9527. App Lab starts the same app under the name in `app.yaml`.

The app boots in **inference only**: the network runs, torque stays off. From the control page:

| Control | Effect |
| --- | --- |
| Inference only | Policy runs. Torque off. |
| Default pose | Holds the home pose. Requires a fresh MCU and IMU sample. |
| Walk / get up | Enables position control and the recovery state machine. |
| Pick | While upright in walk, runs `xgoduck_pick.onnx` for 4 s, then returns to walk. |
| Sit / Stand | Holds sit; Stand runs for 1.2 s, then returns to the selected walking policy. |
| left_kick / right_kick | Runs the selected kick for 0.4 s, then returns to the selected walking policy. |
| Torque off | Disables position control. |

Closing or backgrounding the page sends zero travel. One second without input clears travel while keeping balance. Voice actions renew input only within their original deadline. After 150 ms without fresh STM32 feedback, Linux pauses new targets and clears travel. The MCU holds the last targets while Bridge and its IMU are healthy. Fresh feedback with torque still enabled restores the current control mode. The MCU disarms if Bridge is unavailable or its IMU sample is older than 500 ms. Linux also disarms on invalid IMU data or total IMU age above 500 ms. App exit, gamepad disconnect and explicit Off unload. Calibration and servo setup have a separate 250 ms MCU host-heartbeat guard and a two-second host UI-heartbeat guard.

If all six raw accelerometer and gyroscope axes read zero on ten consecutive samples, STM32 releases torque and reinitializes the IMU. Three consecutive valid samples restore its ready state. Duck stays unloaded until a fresh explicit arm command.

The control panel and cockpit both use vx ±0.4, vy 0 and yaw ±0.45 in Roller mode. Both update held inputs every 50 ms and blend 25% of the target per update. For ordinary walking the control panel uses vx ±0.2 and the cockpit uses vx ±0.15, both with vy ±0.3 and yaw ±1.0. Head channels are four radians in ±1. Keyboard: W/S, A/D, Q/E, Space.

On a normal forward or reverse roller release, the board keeps the last submitted travel command, including turn, while it gradually narrows the skating policy's outward hip-roll targets. With live controls this phase has no duration limit. When both measured hips are within 6° of roller home and the body is upright, travel goes to zero and all ten leg targets move smoothly to roller home. The controller holds this stance after fresh feedback confirms all ten joints within 5°, low joint and body speeds, and stable posture for 200 ms. Stick input during the return cannot change its speed or direction. A stick held through completion must return to neutral before a new movement. While Roller is selected, a servo reporting only overcurrent (0x08) continues to report the warning in status and the servo log, even when stationary with a sustained position error. Manual release still starts the normal stance return. Ordinary walking retains its overcurrent stall recovery. Stop, disconnect, detected forward obstacles, other fault bits and the existing fall and power protections still take priority. The status fields `roller.stop_phase` and `roller.glide_result` and the `roller_pose` events in the servo feedback log report the outcome.

## Policies

Models in `python/`:

- `xgoduck_walk.onnx`
- `xgoduck_getup.onnx`
- `xgoduck_pick.onnx`
- `xgoduck_roulade.onnx`
- `xgoduck_roller.onnx` (bundled; ordinary walking remains selected by default)
- `xgoduck_sitstand.onnx`
- `xgoduck_ballkick_left.onnx`
- `xgoduck_ballkick_right.onnx`

Each expects observation `[1, 61]` and emits action `[1, 14]`. All eight are loaded and warmed at startup. Spare time in the 50 Hz loop runs one dummy pass on an idle session so a switch does not pay a cold start.

Observation layout: gyro (rad/s), gravity, 14 joint positions relative to home, 14 joint velocities, previous action, twist (3), head (4), and six zeros.

Recovery, evaluated on the 50 Hz clock:

- Absolute pitch above 55° or absolute roll above 35° for the 0.15 s detection window → get-up model on the detection frame. Twist and head are zero.
- Tilt below 15° for 1 s → walk, and the web twist/head/mouth commands apply again.
- Sit/stand uses command `[1, 0, 0]` for sit and `[0, 0, 0]` for stand. Stand lasts 1.2 s with scale 1.1 and leg/head α = 0.2/0.2. The transition preserves the previous action and filter history from sitting.
- Left/right kick uses scale 1.0 and leg/head α = 0.2/0.2 for 0.4 s. Velocity/head/body commands stay zero. A fall cancels sit/stand or kick and enters recovery.
- Pick, only from upright walk: command `[cos(2πφ), sin(2πφ), 0]` plus ten zeros, `φ` advances by `dt/4` (a 4 s cycle). The mouth opens to 30° and closes when φ ≥ 0.4.

Ordinary action smoothing uses leg α = 0.35 on `[0:5]` and `[9:14]`, and head α = 0.45 on `[5:9]`. Roller uses leg/head α = 0.4/0.5. `prev_action` stores the raw network output and is cleared on a policy switch. IMU fusion uses source timestamps from the independent MCU IMU task. Gyro smoothing α = 0.5, joint-velocity α = 0.4, and gravity complementary-filter τ = 0.3 s. Accelerometer correction falls off when the norm leaves 1 g by more than about 20%; there is no gyro bias subtraction. QMI axes are mapped once in firmware and the host undoes the reference device mapping before fusion.

The matching master protocol includes MCU-timestamped IMU data and separate loop/Bridge diagnostics. The packet ABI retains the disabled normal command TTL field. Following a feedback pause, Linux returns to inference-only if the MCU reports torque off or invalid IMU feedback. Explicit Stand, Policy or Hold with fresh feedback can arm again. The protocol uses DISARM/ARM/POLICY/HOLD. Calibration and servo setup use the independent heartbeat guards described above.

## Layout

```text
app.yaml                 App Lab manifest
assets/                  Web UI
python/                  Host policy, Bridge client, ONNX models
sketch/                  STM32 firmware
data/hardware-centers.json  Per-robot calibration migration record (not in git)
tools/                   Router installer, MCU library and ELF checks, read-only capture
```

## Checks

```bash
for i in $(seq 1 60); do curl -s http://127.0.0.1:9527/api/status; echo; sleep 1; done
```

This loop only reads `http://127.0.0.1:9527/api/status`; it does not enable motors. Redirect it to a file for a JSON-lines capture.

## References

- [Microduck](https://github.com/pollen-robotics/microduck)
- [Arduino RouterBridge](https://github.com/arduino-libraries/Arduino_RouterBridge)
- [Arduino Router](https://github.com/arduino/arduino-router)
