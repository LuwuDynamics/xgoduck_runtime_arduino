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

The ordinary walking home pose in `python/rl_core.py` is hip/ankle ±22°, knee 0°, hip roll ±5°, neck and head pitch 20°, mouth 0°. Its `action_scale` is 1. Roller reads its own home from ONNX metadata and uses scale 0.7; it is off at startup and selectable using the stable page's Roller switch. Use it only with the corresponding roller-equipped robot. MCU angles use each servo's hardware center at encoder position 2047.

Hardware centers are stored in the servos. Legacy `data/zero_pos.json` files are preserved as backups and no longer applied. Nondefault legacy software zeros require the hardware-centering workflow before arming; `data/hardware-centers.json` records acknowledged progress for this robot.

## First-time servo setup

**Scan all IDs** checks every address from **0 through 253** at the configured servo bus rate (1 Mbps). **Stop scan** cancels the scan and keeps the IDs already found. Click a result, or type any valid address into **Current / target ID**. **Read** also accepts any address in this range. Addresses 254 (broadcast) and 255 are not assignable servo IDs.

To rename a servo, enter its current ID and the desired **Change ID to** value, then click **Change ID**. The app checks that the source responds and the destination is free, writes the ID, and verifies replies at the new and old addresses. A Bridge timeout aborts scanning; it is not reported as a missing servo. The normal Control page still monitors the robot's configured 15 joint IDs.

Deploy `assets/`, `python/`, and `sketch/` together for this feature; updating only the page or Python is insufficient. Restart the app through App Lab / App CLI so the updated sketch is compiled and uploaded.

Do this before the servos are installed in the frame. Connect **one servo at a time**. Two servos with the same ID on the bus will both accept a write.

1. Open **Servo setup** (`/servo.html`) and enter setup.
2. Assign IDs **10–14** and **20–24** for the legs. Assign **30–34** for the neck, head, and mouth. The ID write unlocks register `0x37` and then writes `0x05`.
3. With torque off, turn the joint by hand to its mechanical center and click **Reset Mid**. It remains unloaded.
4. Write stored KP/KD. The page defaults are **KP = 5** and **KD = 10**. The write unlocks `0x37` and stores both bytes at `0x15`.
5. Disconnect that servo and repeat until every ID exists exactly once.
6. Install the servos in the frame and finish the wiring: servo bus on D0/D1 (`Serial1`, 1 Mbps), QMI8658 on D20/D21 (`Wire`, 400 kHz, address `0x6A` then `0x6B`).

Startup, arming and calibration preserve the leg/head servos’ existing KP/KD. Only mouth ID 34 receives temporary KP = 10 and KD = 20 (addresses 50 and 51). Explicit Set KpKd still writes the selected servo. Previously applied temporary gains are not restored automatically; a servo power cycle reloads its saved values. Torque off clears torque (address 40), and goal-position writes are suppressed while disarmed.

## Calibration

Do this after wiring and confirming every servo ID answers.

1. Open **Calibration** (`/calibrate.html`). All servos unload.
2. Turn each joint by hand to its mechanical center, then click **Set center & hold**. Only that joint is centered and held at 2048; uncalibrated joints remain unloaded.
3. Complete all 15 joints, then exit to unload them all. Check Default pose with the robot supported before Walk / get up.

Legacy software-center migration is described above and in the AI guide. Do not replace old zeros with 2047 to skip calibration. The page also offers Roller walking, independent model amplitude/action filters, IMU filtering, and separate all-servo protection-switch/current broadcasts; protection settings are changed only when explicitly requested from the page.

## Running

Pinned stack: App CLI with Arduino Zephyr core 1.0.0, RouterBridge 0.4.3, RPClite 0.3.1, MsgPack 0.4.2. `sketch/sketch.yaml` lists those libraries, including the transitive ones. Python packages are installed by the App build from `python/requirements.txt`.

Router: 0.8.0 or newer (the 2 Mbps drop-in below passes `--after-ready`). Since 0.2.41 the Bridge supervisor accepts the `RouteAlreadyExists` answer that Routers 0.5.4–0.10.x give to a repeated registration; 0.2.37–0.2.40 reconnected every 2.26 s on those Routers and effectively needed 0.11.0. Verified on a board with Router 0.9.0.

On the Uno Q, from this directory:

```bash
python3 tools/prepare-bridge.py
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
```

`prepare-bridge.py` patches the resolved library cache and keeps the original headers as `.h.original`:

- RouterBridge 0.4.3: RPC thread stack 500 → 4096 bytes, bounded RPC waits, nonblocking notifications and retry-safe initialization/reconnection (implemented in `tools/bridge_patch.py`).
- RPClite 0.3.1: a bounded MessagePack scanner accepts a complete frame before `Unpacker::feed()`, and rejects a frame larger than the RPC buffer.

Re-run the script after a library-cache wipe or on a new board. If it cannot find the libraries, let the App CLI resolve `sketch.yaml` once, then run it again.

`router-2m.sh` sets the router serial rate to 2 Mbps in `/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf` and leaves the board GPIO ready-hook in place. That rate is global. Before starting another app that calls `Bridge.begin()` at 115200, move the drop-in aside, reload systemd, and restart `arduino-router`. Both ends must use the same rate. Restarting the router recreates its socket and resets the MCU, so stop this app first, restart the router, then start the app again. A normal app restart does not need a router restart.

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
| Sit / Stand | Holds sit; Stand runs for 1 s, then returns to the selected walking policy. |
| left_kick / right_kick | Runs the selected kick for 0.4 s, then returns to the selected walking policy. |
| Torque off | Disables position control. |

Closing/backgrounding the page sends zero travel. One second without input clears travel while keeping balance; valid voice actions renew input only within their original deadline. Normal MCU commands do not expire. The MCU disarms if Bridge is unavailable or its IMU sample is older than 500 ms; Linux disarms on feedback older than 150 ms, invalid IMU, or total IMU age above 500 ms. App exit, gamepad disconnect and explicit Off unload. Calibration/servo setup has a separate 250 ms MCU host-heartbeat guard and a two-second host UI-heartbeat guard. Reconnection requires explicit arming and does not resume expired motion.

The web joystick uses vx -0.18 to +0.2, vy ±0.3 and yaw ±1.0 for ordinary walking; Roller uses vx ±0.4, vy 0 and yaw ±0.25. Head channels are four radians in ±1. Keyboard: W/S, A/D, Q/E, Space.

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

- Tilt above 55° for 0.15 s → get-up model, action forced to 0 for 1 s, then the get-up action. Twist and head are zero.
- Tilt below 15° for 1 s → walk, and the web twist/head/mouth commands apply again.
- Sit/stand uses scale 1.1, with command `[1, 0, 0]` for sit and `[0, 0, 0]` for stand. Stand lasts 1 s.
- Left/right kick uses scale 0.8 and α = 0 for 0.4 s; velocity/head/body commands stay zero. A fall cancels sit/stand or kick and enters recovery.
- Pick, only from upright walk: command `[cos(2πφ), sin(2πφ), 0]` plus ten zeros, `φ` advances by `dt/4` (a 4 s cycle). The mouth opens to 30° and closes when φ ≥ 0.4.

Ordinary action smoothing uses leg α = 0.35 on `[0:5]` and `[9:14]`, and head α = 0.45 on `[5:9]`. Roller uses leg/head α = 0.4/0.5; kicks use 0/0. `prev_action` stores the raw network output and is cleared on a policy switch. The 1 s fallen hold sends the home pose directly. IMU fusion uses source timestamps from the independent MCU IMU task. Gyro smoothing α = 0.5, joint-velocity α = 0.4, and gravity complementary-filter τ = 0.3 s. Accelerometer correction falls off when the norm leaves 1 g by more than about 20%; there is no gyro bias subtraction. QMI axes are mapped once in firmware and the host undoes the reference device mapping before fusion.

The matching master protocol includes MCU-timestamped IMU data and separate loop/Bridge diagnostics. Normal command TTL is retained in the packet ABI but disabled. Bridge loss or a stale IMU disarms the MCU; Linux also returns to inference-only on stale/invalid feedback. Explicit Stand/Policy/Hold with fresh feedback can arm again. The protocol uses DISARM/ARM/POLICY/HOLD; calibration and servo setup retain their independent heartbeat guards described above.

## Layout

```text
app.yaml                 App Lab manifest
assets/                  Web UI
python/                  Host policy, Bridge client, ONNX models
sketch/                  STM32 firmware
data/hardware-centers.json  Per-robot calibration migration record (not in git)
tools/                   Bridge patch, router rate, ELF check, read-only capture
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
