# XGO Duck

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

The home pose in `sketch/duck_config.h` and `python/rl_core.py` is hip/ankle ±24°, knee 0°, hip roll ±5°, neck and head pitch 20°, mouth 0°. `action_scale` is 1. Home is this table, not an ONNX metadata field.

Factory encoder zeros are `ZERO_POS` in `sketch/duck_config.h`. A calibration overwrites them in `data/zero_pos.json` and the host copies that file into MCU RAM at startup. `data/zero_pos.json` is local to the robot and is gitignored.

## First-time servo setup

Do this before the servos are installed in the frame. Connect **one servo at a time**. Two servos with the same ID on the bus will both accept a write.

1. Open **Servo setup** (`/servo.html`) and enter setup.
2. Assign IDs **10–14** and **20–24** for the legs. Assign **30–34** for the neck, head, and mouth. The ID write unlocks register `0x37` and then writes `0x05`.
3. Center that servo. **Move to** raw position **2047** and write it. Temporary run gains for this move are KP = 6 and KD = 20. ID 34 uses temporary KP = 10.
4. Write stored KP/KD. The page defaults are **KP = 5** and **KD = 20**. The write unlocks `0x37` and stores both bytes at `0x15`.
5. Disconnect that servo and repeat until every ID exists exactly once.
6. Install the servos in the frame and finish the wiring: servo bus on D0/D1 (`Serial1`, 1 Mbps), QMI8658 on D20/D21 (`Wire`, 400 kHz, address `0x6A` then `0x6B`).

After power-up the controller also writes the temporary torque register (address 40) and temporary KP/KD (addresses 50 and 51): KP = 6, KD = 20, and KP = 10 on ID 34. Torque off clears torque and does not write a goal position. Writing a goal after torque-off still drags a connected servo, so position writes are suppressed while disarmed.

## Calibration

Do this after the harness is wired and every ID answers.

1. Open **Calibration** (`/calibrate.html`) and start it. Policy and pose control stop. Temporary gains become KP = 3 and KD = 0, and every servo is commanded to 2047.
2. For each joint, set the slider so the link sits at its mechanical zero, then press **Finish**. The current encoder reading becomes that joint's zero and is saved to `data/zero_pos.json`.
3. Exit calibration. The highlight on a finished row is only for this visit; the saved zero is the encoder value.

## Running

Pinned stack: App CLI with Arduino Zephyr core 1.0.0, RouterBridge 0.4.3, RPClite 0.3.1, MsgPack 0.4.2. `sketch/sketch.yaml` lists those libraries, including the transitive ones. Python packages are installed by the App build from `python/requirements.txt`.

On the Uno Q, from this directory:

```bash
python3 tools/prepare-bridge.py
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
```

`prepare-bridge.py` patches the resolved library cache and keeps the original headers as `.h.original`:

- RouterBridge 0.4.3: RPC thread stack 500 → 4096 bytes.
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
| Torque off | Disables position control. |

Closing the page, or leaving it in the background for more than 1 second, turns position control off. Calibration and servo setup use a 2 second heartbeat and then exit and turn torque off.

Walk commands are vx ±0.4, vy ±0.3, ωz ±1.0, with a joystick dead zone of 0.1. Head channels are four radians in ±1. Keyboard: W/S, A/D, Q/E, Space.

## Policies

Models in `python/`:

- `xgoduck_walk.onnx`
- `xgoduck_getup.onnx`
- `xgoduck_pick.onnx`

Each expects observation `[1, 61]` and emits action `[1, 14]`. All three are loaded and warmed at startup. Spare time in the 50 Hz loop runs one dummy pass on an idle session so a switch does not pay a cold start.

Observation layout: gyro (rad/s), gravity, 14 joint positions relative to home, 14 joint velocities, previous action, twist (3), head (4), and six zeros.

Recovery, evaluated on the 50 Hz clock:

- Tilt above 55° for 0.15 s → get-up model, action forced to 0 for 1 s, then the get-up action. Twist and head are zero.
- Tilt below 15° for 1 s → walk, and the web twist/head/mouth commands apply again.
- Pick, only from upright walk: command `[cos(2πφ), sin(2πφ), 0]` plus ten zeros, `φ` advances by `dt/4` (a 4 s cycle). The mouth opens to 30° and closes when φ ≥ 0.4.

Action smoothing uses α = 0.45 on slices `[0:5]`, `[5:10]`, and `[10:14]`. `prev_action` stores the raw network output and is cleared on a policy switch. The 1 s fallen hold sends the home pose directly and does not keep a filtered walk action. Gyro smoothing α = 0.5, joint-velocity α = 0.4, gravity complementary-filter τ = 0.3 s. Accelerometer correction falls off when the norm leaves 1 g by more than about 20%. There is no gyro bias subtraction. The QMI axes are mapped once, in firmware; the host undoes the reference device mapping so the filter matches the Microduck math without applying that mapping twice.

Position control stops if commands are absent for 250 ms or the IMU is absent for 100 ms. Enabling again takes an explicit mode change. Host feedback older than 150 ms also disarms; that matches the MCU source-age limit.

## Layout

```text
app.yaml                 App Lab manifest
assets/                  Web UI
python/                  Host policy, Bridge client, ONNX models
sketch/                  STM32 firmware
data/zero_pos.json       Created on the robot by calibration (not in git)
tools/                   Bridge patch, router rate, ELF check, read-only capture
```

## Checks

```bash
python tools/measure.py --seconds 60
```

`tools/measure.py` only reads `http://127.0.0.1:9527/api/status`. It does not enable motors. Pass `--output` to save a JSON capture.

## References

- [Microduck](https://github.com/pollen-robotics/microduck)
- [Arduino RouterBridge](https://github.com/arduino-libraries/Arduino_RouterBridge)
- [Arduino Router](https://github.com/arduino/arduino-router)
