# XGO Duck


> **Motion synchronized to master `b401d85` (2026-10-31):** matching host control, eight policies, motion pages, STM32 protocol and Bridge preparation are pinned to [this commit](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/commit/b401d85d20bb5bfd0237242a8a5ab6847fc962a2). Existing AI/audio, mobile, gamepad, calibration migration and independent ToF integrations remain. See [validation](docs/ai-validation.md). Older software centers still require the [hardware-center migration](docs/ai-chat-en.md#matching-web-and-hardware-centers-0233).
[English](README.md) · [简体中文](README_CN.md)

**Motion MCP and forward obstacle checks:** `Duck.Sit`, `Duck.LeftKick`,
`Duck.RightKick` and `Duck.SetWalkingMode` use the existing sit/stand, kick and
roller models. Stand, Move, Pick and Roulade remain available.
Forward motion slows/stops using ToF; absent, stale or insufficient front
measurements stop forward input while retaining balance. Recovery requires a
new command (release the remote direction first); reverse, sideways movement,
turning and chat remain available. Mount the sensor facing forward, clear of
the body and floor. See [model mapping, thresholds and validation](docs/forward-avoidance.md).

`Duck.GetToF` reads front distance, validity, frame age and the 8×8 grid without
starting motion. Obstacles or unavailable ToF produce a normal `blocked`
execution result with a reason; invalid parameters, permissions and actual
motion-communication failures still return MCP errors.

Ordinary forward requests use `Duck.Forward`: 0.1 m/s by default or 0.15 m/s
with an explicit `pace=fast`, for three seconds by default. MCP linear speed
is capped at 0.15 m/s in all directions. Explicit slow/numeric requests use
`Duck.Move` and preserve values within the cap; phone remote mapping is unchanged.

Version `0.2.27` keeps ToF optional: absent at boot or disconnected during
ranging, it reports `absent`/`offline`; voice and media continue. With forward
avoidance enabled, missing measurements pause forward travel.
The startup support checks interrupted MCU object/dependency files so a broken
build cache is rebuilt instead of preventing the default App from starting.
After updating this version, rerun the hardware support installer once.

Version `0.2.26` adds an optional SparkFun VL53L5CX on the UNO Q Qwiic port:
8×8 distances at 5 Hz, sampled independently on STM32 `Wire1` and available
through a read-only Bridge tool. See [ToF connection and reading](docs/tof.md).

**XGO Duck AI App (`ai-chat-audio-video-tof`).** One App with two business processes: the original web/motion runtime and headless AI Chat Pro. It adds autostart, Bluetooth Wi-Fi provisioning, account binding and cloud MCP remote control. Existing web pages, audio/video and Doubao are retained; upload the matching STM32 sketch for torque-preserving pauses. Install it once on your own UNO Q; no display is required.

Version `0.2.25` switches the Chinese wake phrase to “小鸭小鸭” with the bundled final `xiaoya.onnx`, threshold 0.80, a two-second window and 80 ms hop. Training-compatible normalized audio and a bounded cache of identical mel windows reduce device computation. Wake detection plays the same fixed 0.92-second duck call without jaw motion. English wake detection, cloud selection, send/reply-finished cues and saved gains are retained.

Version `0.2.24` keeps the jaw still for send/wake cues and local duck calls; spoken replies continue to animate it. Cue and speech samples remain distinct even within one output frame, without changing speaker audio, gain or DAC timing.

Version `0.2.23` fixes false speaker-busy errors in `Duck.Quack` while the server waits for an MCP result. Capture reference processing now runs outside the sound-device callback; the App requests a 200 ms input buffer and reports actual latency, callback gaps and queue drops. Existing playback buffering and gains are retained. USB disconnection or reduced link speed still requires separate device checks.

Historical `0.2.22` communication diagnostic settings (superseded by the pinned master motion stack above): Its matching STM32 sketch disables the 250 ms command and 100 ms IMU expiry actions. Linux no longer latches a pause for feedback older than 150 ms or invalid IMU, and MCP uses the same bypass. Timeouts remain logged. Missing/invalid samples retain the previous motion target; valid samples may continue the previous motion request instead of jumping to a default pose. The normal web heartbeat watchdog was already removed. **Upload the matching sketch** and confirm `timeout_checks_disabled=true`; Python alone leaves old firmware guards active. Explicit stop/off, initial-enable IMU readiness, packet/sequence validation and required-servo checks remain. Web assets and saved configuration are unchanged.

Version `0.2.21` checks feedback for the joints each MCP action needs, without blocking healthy joints because an unrelated ID is missing. `SetHead` accepts individual axes and preserves omitted goals. Queue entry avoids duplicate feedback checks; execution errors identify the observed problem. Existing web pages, ONNX policies, STM32 and saved configuration are preserved.

Version `0.2.20` adds visible TTS jaw variation using 20/80/320 ms energy windows, relative energy changes and gentle mixed rhythms, with a slightly wider speaking opening. Motion remains within 0–12° and 90°/s, using already enabled body servos. Web pages, movement policies and STM32 firmware are preserved.

Version `0.2.18` plays a `greet` duck call after networking succeeds and a `wheee` call after the voice service actually connects, once each per start. These two cues have an additional 3× software gain with saturation; saved chat volume and normal MCP duck-call gain remain. The networking cue works before account binding, and missing USB audio never blocks startup.

Version `0.2.17` combines MCP `Duck.Quack`, gentle TTS jaw motion and torque-preserving pauses. Feedback/IMU timeouts, process exits and gamepad disconnects stop motion while holding position; explicit TorqueOff/Off releases motors. Resume a paused body explicitly with Stand or Policy/Hold. The jaw uses private nonblocking local messages instead of repeated HTTP round trips. Original pages, models and saved calibration remain. [Motion and media behavior](docs/ai-chat-en.md#runtime-web-and-mcp); [checks and device validation](docs/ai-validation.md).

## First AI App installation

**Choose a cloud locally on the board. The phone never changes it.** Accounts, bindings and agents belong to the selected service. Conversation language is independent of region.

| Board selection | Client | OTA endpoint |
| --- | --- | --- |
| `cn` | Domestic WeChat Mini Program | `https://xl-api.xgorobot.com/xiaolu/ota/` |
| `global` | International Android / iOS App | `https://xl-api.luwudynamics.ai/xiaolu/ota/` |

| WeChat Mini Program (scan with WeChat) | International App (download QR) |
| --- | --- |
| <img src="docs/images/qr_miniprogram.png" alt="Domestic WeChat Mini Program code" width="180"> | <img src="docs/images/qr_app_intl.png" alt="International App download QR" width="180"> |
| Use `cn` | Use `global`; [open download page](https://agent.luwudynamics.ai/download) |

Images reuse the existing Luwu-OS AI Chat Pro assets; [source and hashes](provenance/onboarding-assets.json). Duck remote control requires a client release with the `XGO-Duck` adapter. These public QR destinations do not imply that local client changes have been released.

1. **Initial USB setup.** Install [Arduino App Lab](https://www.arduino.cc/en/uno-q), connect UNO Q with a data cable, and set up Wi-Fi and your Linux password. Verify `ssh arduino@<board-ip>`. First installation requires internet access for dependencies.
2. **Download and package on the computer:**

   ```sh
   git clone --branch ai-chat-audio-video-tof https://github.com/LuwuDynamics/xgoduck_runtime_arduino.git
   cd xgoduck_runtime_arduino
   python3 tools/package-ai-app.py
   ```

   Import `dist/xgoduck_ai.zip` as a **new App** named `xgoduck_ai` in App Lab. Stop the old Duck App. See the [full guide](docs/ai-chat-en.md#first-installation) for direct Git installation on the board and calibration migration.

   An optional **App ZIP with the toolchain included** avoids downloading that large file from GitHub on first installation. The normal ZIP is about 9.5 MB; the bundled ZIP is about 107 MB. System, container and Python dependencies still require internet access. See [toolchain packaging and recovery](docs/ai-chat-en.md#toolchain-and-bundled-app-zip).
3. **Choose region and install support in the UNO Q host shell.** Adjust the path to the imported directory; this example selects the international service:

   ```sh
   cd ~/ArduinoApps/xgoduck_ai
   python3 tools/configure-ai.py --region global --language en
   # Domestic: python3 tools/configure-ai.py --region cn --language cn
   bash tools/install-ai-support.sh
   sudo bash tools/router-2m.sh
   ```

   **Use Wi-Fi/SSH and stop all Apps first.** The installer resolves the pinned MCU toolchain and libraries, compiles and checks the ELF, then configures the default App, services and **USB Host**. Computer USB/ADB access disconnects; use a powered Host hub afterwards. It does not flash the MCU or start the App. Users need not choose toolchain versions. Run `router-2m.sh` with the App stopped; it restarts the MCU communication router.
4. **First Run.** Use App Lab Network and wait for dependencies and both processes. Run may compile/upload the sketch. On subsequent boots, Host preparation completes before the App starts automatically. Chat restarts do not change USB mode.
5. **Add from the phone.** In the matching client, discover a name such as `XGO-DuckABCD`, provision Wi-Fi, bind your account and select an agent. Provisioning and MCP work without a microphone or speaker; conversation resumes when USB audio is connected. Phone Bluetooth is for provisioning; original gamepad support remains.
6. **Verify autostart.** After the first successful online Run, reboot. Further boots need no desktop Run click. Preserve all of `data/` on updates. To change regions, stop the App and explicitly configure the board, then bind in the destination client. No chat cloud is contacted before region selection.
7. **Stand before driving.** After calibration and placement, explicitly enable voice motion and ask the Duck to stand up, or start Policy in the original web page. Direction commands only move an already armed, stable Duck and never start get-up. Check `Duck.GetStatus.movement_ready` before driving; see the [motion flow](docs/ai-chat-en.md#runtime-web-and-mcp).

See the [English installation guide](docs/ai-chat-en.md), [中文说明](docs/ai-chat.md), and [validation record](docs/ai-validation.md) for configuration, recovery, updates and device acceptance limits. The original hardware/web instructions follow.

<img src="docs/images/cockpit-demo.webp" alt="A controller-mounted phone shows Duck's live camera feed while the robot is driven with the gamepad" width="960">

**Duck's-eye view, right on your controller.** Mount a phone between the grips, open the mobile cockpit, and drive Duck with the sticks while watching its onboard camera feed. The real-hardware demo above shows the handheld setup and robot moving together; the animated WebP loops silently.

**Next-version development — audio, video & ToF.** This branch brings together USB camera streaming, the fullscreen mobile cockpit, live listening from Duck's microphone, phone-to-Duck voice clips, gamepad configuration, and the manual USB Host switch. Optional ToF ranging is implemented; automatic obstacle avoidance is not connected. See [ToF setup](docs/tof.md). See the [mobile cockpit guide](docs/mobile-cockpit.md) and [USB camera/audio setup](docs/usb-camera.md).

This project runs walking, get-up, pick, and roulade policies on an XGODuck built around the Arduino Uno Q.

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

Servo KP/KD are user-controlled for every ID, including mouth ID 34. App/voice startup, voice restarts, arming and calibration/servo-setup transitions preserve the existing gains. Use **Set KpKd** in Servo setup to explicitly save values for the selected servo; runtime gain reads remain available. `/api/status.runtime_gains` reports `state: manual` and `auto_apply: false`. The former automatic 5/10 broadcast is removed from Python and STM32; updating only Python cannot remove overrides in an older STM32 sketch. Existing saved settings are not reset. Torque off clears torque (address 40), and goal-position writes are suppressed while disarmed.

## Calibration

Do this after wiring and confirming every servo ID answers.

1. Open **Calibration** (`/calibrate.html`). All servos unload.
2. Turn each joint by hand to its mechanical center, then click **Set center & hold**. Only that joint is centered and held at 2048; uncalibrated joints remain unloaded.
3. Complete all 15 joints, then exit to unload them all. Check Default pose with the robot supported before Walk / get up.

Legacy software-center migration is described above and in the AI guide. Do not replace old zeros with 2047 to skip calibration. The page also offers Roller walking, independent model amplitude/action filters, IMU filtering, and separate all-servo protection-switch/current broadcasts; protection settings are changed only when explicitly requested from the page.

## Running

Pinned stack: App CLI with Arduino Zephyr core 1.0.0, RouterBridge 0.4.3, RPClite 0.3.1, MsgPack 0.4.2. `sketch/sketch.yaml` lists those libraries, including the transitive ones. Python packages are installed by the App build from `python/requirements.txt`.

On the Uno Q, from this directory:

```bash
python3 tools/prepare-ai-sketch.py
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
```

`prepare-ai-sketch.py` resolves the pinned tools and libraries, then calls `prepare-bridge.py` to patch the resolved library cache and keep the original headers as `.h.original`:

- RouterBridge 0.4.3: RPC thread stack 500 → 4096 bytes, bounded RPC waits, nonblocking notifications and retry-safe initialization/reconnection (implemented in `tools/bridge_patch.py`).
- RPClite 0.3.1: a bounded MessagePack scanner accepts a complete frame before `Unpacker::feed()`, and rejects a frame larger than the RPC buffer.

It compiles and checks the ELF without uploading. Re-run it after a library-cache wipe or on a new board; the AI support installer already does this step.

`router-2m.sh` sets the router serial rate to 2 Mbps in `/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf` and leaves the board GPIO ready-hook in place. That rate is global. Before starting another app that calls `Bridge.begin()` at 115200, move the drop-in aside, reload systemd, and restart `arduino-router`. Both ends must use the same rate. Restarting the router recreates its socket and resets the MCU, so stop this app first, restart the router, then start the app again. A normal app restart does not need a router restart.

The preparation script reuses only the exact required toolchain version. For GitHub download failures, use the [bundled toolchain or archive recovery](docs/ai-chat-en.md#toolchain-and-bundled-app-zip).

GCC 14 can tail-call `k_thread_name_set` at the end of `setup()` with a Thumb `JUMP24` from the loadable sketch into flash. The Uno Q loader then returns ENOEXEC (−8) and the MCU never starts. The `asm volatile("nop")` at the end of `setup()` keeps a long call. `python3 tools/verify-elf.py` checks the built ELF for the same class of short branch to an unresolved firmware symbol.

The web UI is served on port 9527. App Lab starts the same app under the name in `app.yaml`.

The app boots in **inference only**: the network runs, torque stays off. From the control page:

| Control | Effect |
| --- | --- |
| Inference only | Policy runs. Torque off. |
| Default pose | Holds the home pose. Requires a fresh MCU and IMU sample. |
| Walk / get up | Enables position control and the recovery state machine. |
| Pick | While upright in walk, runs `xgoduck_pick.onnx` for 4 s, then returns to walk. |
| Roulade | While upright in walk, runs `xgoduck_roulade.onnx` for 1.9 s, then returns to walk. |
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
- Roulade, only from upright walk: runs for 1.9 s with twist, head, and the six trailing command inputs at 0. Gyro, gravity, joint position, joint velocity, and the previous action stay on the usual path. Leg and neck smoothing drop to α = 0.15 for that window, then return to the previous values. Tilt does not select get-up until the 1.9 s ends.

Ordinary action smoothing uses leg α = 0.35 on `[0:5]` and `[9:14]`, and head α = 0.45 on `[5:9]`. Roller uses leg/head α = 0.4/0.5; kicks use 0/0. `prev_action` stores the raw network output and is cleared on a policy switch. The 1 s fallen hold sends the home pose directly. IMU fusion uses source timestamps from the independent MCU IMU task. Gyro smoothing α = 0.5, joint-velocity α = 0.4, and gravity complementary-filter τ = 0.3 s. Accelerometer correction falls off when the norm leaves 1 g by more than about 20%; there is no gyro bias subtraction. QMI axes are mapped once in firmware and the host undoes the reference device mapping before fusion.

The matching master protocol includes MCU-timestamped IMU data and separate loop/Bridge diagnostics. Normal command TTL is retained in the packet ABI but disabled. Bridge loss or a stale IMU disarms the MCU; Linux also returns to inference-only on stale/invalid feedback. Explicit Stand/Policy/Hold with fresh feedback can arm again. The protocol uses DISARM/ARM/POLICY/HOLD; calibration and servo setup retain their independent heartbeat guards described above.

## Layout

```text
app.yaml                 App Lab manifest
assets/                  Web UI
python/                  Host policy, Bridge client, ONNX models
sketch/                  STM32 firmware
data/hardware-centers.json  Local acknowledged calibration migration (not in git)
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
