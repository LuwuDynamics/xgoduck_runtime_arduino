# XGO Duck

Control an XGO Duck from a web browser using Arduino Uno Q. Includes walking, roller walking, get-up, sit/stand, pick, roulade, and left/right-kick policies.

Based on [Microduck](https://github.com/pollen-robotics/microduck) by [Pollen Robotics](https://pollen-robotics.com/microduck/).

## Start the app

1. Open the project in **Arduino App Lab** and click **Run**. For a new board, complete the first-time setup below.
2. Open the web interface shown by App Lab, or visit `http://<board-ip>:9527`.
3. Check that **Servos online** shows **15/15** and **IMU** shows **online**.
4. The app starts in **Inference only**, with the motors unloaded. Finish servo setup and calibration before enabling motion.

### First-time board setup

After App Lab has downloaded the board libraries, stop the app. Open a terminal on the Uno Q in this project folder and run:

```bash
python3 tools/prepare-bridge.py
sudo bash tools/router-2m.sh
```

Then click **Run** in App Lab. Repeat this preparation if the board's library cache is replaced. It configures the 2 Mbps connection required by this app.

Connect the servo bus to **D0/D1** and the QMI8658 IMU to **D20/D21**.

The STM32 runs independent 100 Hz servo and IMU threads. IMU initialization and
I²C retries do not block the servo watchdog. Feedback uses the latest complete
snapshots, and Linux integrates IMU samples using their MCU timestamps.

`tools/prepare-bridge.py` also installs bounded RPC waits and retry-safe Bridge
initialization for the pinned libraries. Initialization retries use backoff;
reconnection leaves the robot unloaded until motion is explicitly enabled again.
`/api/status` includes `mcu_runtime` diagnostics for both loops and Bridge retries.

## Servo setup

Connect **one servo at a time** when changing IDs.

1. Open **Servo setup**, click **Scan all IDs**, and select a result. **Stop scan** ends the scan early.
2. To change its ID, enter the new number in **Change ID to** and click **Change ID**. Scan again to select the new ID.
3. To view its settings, click **Read**.
4. To adjust gains, enter KP and KD, then click **Set KpKd**. The inputs start at **5** and **10**; reading keeps your input values.
5. To reset its center, turn the unloaded joint by hand to its mechanical center and click **Reset Mid**. The servo stays unloaded.
6. Repeat for the remaining servos.

| Servo IDs | Joints |
| --- | --- |
| 10–14 | Left leg |
| 20–24 | Right leg |
| 30–33 | Neck and head |
| 34 | Mouth |

### All-servo protection

Under **All-servo voltage / overcurrent protection**:

- Click **Enable** or **Disable** to change the protection switch.
- Enter a current limit in amperes and click **Set protection current**. The field starts at **1.5 A**.

These are separate actions and apply to **all connected servos**. No scan or ID selection is needed.

Leg and head servo KP/KD values are preserved at startup, on arming, and through calibration. The app only applies fixed temporary gains to mouth servo **ID 34**: KP **10**, KD **20**. Explicit **Set KpKd** operations still update the selected servo. Previously applied temporary values are not automatically restored by this change; power-cycle the servo supply to reload its saved power-on values.

## Calibration

1. Open **Calibration**. All servos unload automatically.
2. Turn one joint by hand to its mechanical center, then click **Set center & hold** for that joint. It holds its center while the others stay unloaded.
3. Repeat for every joint.
4. Click **Unload all** to release the joints for another adjustment, or **Exit** to return to Control. Exiting unloads the servos.

## Control

| Button | What it does |
| --- | --- |
| **Inference only** | Runs the policies with motor torque off. |
| **Default pose** | Holds the selected walking model's default pose. |
| **Walk / get up** | Enables walking and automatic recovery after a fall. |
| **Pick** | Plays one pick action, then returns to the selected walking model. |
| **Roulade** | Plays one roll action, then returns to the selected walking model. |
| **Sit / Stand** | Sit enables the sit/stand policy and holds the seated position. Sit/Stand uses fixed action scale 1.1. Stand runs the standing command for 1 s, then returns to walking/recovery. |
| **left_kick / right_kick** | Runs the selected kick policy for 0.4 s with velocity and pose commands at zero, then returns to walking. Kick uses its own scale 0.8 and leg/head α 0. A fall cancels the kick and enters normal recovery. |
| **Zero motion** | Stops travel while keeping the current control mode. |
| **Torque off** | Unloads the motors. |

Use the left pad to move and the right pad to turn. Release a pad to return it to center. Use **Head and mouth** to adjust the head and mouth. **Pick** and **Roulade** are available while walking upright.

Leaving the Control page stops travel but keeps motor torque enabled. Use **Torque off** when you want the robot to rest.

Sit/Stand uses `python/xgoduck_sitstand.onnx` with fixed action scale 1.1; its leg/head α follow the ordinary-model sliders. Stand runs for 1 s before returning to walking/recovery. Its scale is independent of the Action scale slider. The policy command is `[1, 0, 0]` while sitting and `[0, 0, 0]` while standing. Walk, Pick, Roulade and Roller mode are disabled during sit/stand; motion controls cannot replace the sit/stand command. The standing interval also disables the Sit/Stand button. Changing to another control mode cancels sit/stand; Torque off remains available throughout.

A fall cancels sit/stand and enters the existing recovery flow (default-pose hold, then get-up). After recovery the selected walking policy resumes; the robot does not sit down again automatically.

`left_kick` and `right_kick` use `python/xgoduck_ballkick_left.onnx` and `python/xgoduck_ballkick_right.onnx`, respectively, and are available while walking upright, including an unloaded preview in Inference only. The velocity, head and body command observations are all zero throughout each 0.4 s action; browser motion and pose inputs cannot override them. At 0.4 s the controller returns to the selected walking policy. Both kick policies use fixed action scale 0.8 and leg/head α 0. The Action settings sliders and Roulade smoothing do not change kick settings; kick settings do not change the other policies. Other action buttons and Roller mode are unavailable during the kick. Torque off and other control modes can cancel it. The existing fall detection stays active and recovery returns to the selected walking policy without resuming the kick.

## Roller walking

Use this mode with the roller-equipped robot.

1. Turn **Roller mode** **On**.
2. Click **Default pose** to check the stance, then **Walk / get up** to start.
3. Use the left pad for forward/backward motion and the right pad to turn. Sideways motion is disabled.
4. Click **Zero motion** to stop travel, or **Torque off** to unload the motors for a rest.

Roller walking uses `python/xgoduck_roller.onnx`. Its default stance is read automatically from the model. To update it, replace that file with a compatible roller model and restart the app.

Recovery uses the get-up policy and then returns to roller walking. Turn **Roller mode** **Off** to select ordinary walking. Switching modes clears the motion controls; Roller mode starts **Off** after an app restart.

| Joystick range | Ordinary walking | Roller walking |
| --- | --- | --- |
| Forward / backward | -0.18 to +0.2 | -0.4 to +0.4 |
| Sideways | ±0.3 | 0 |
| Turning | ±1.0 | ±0.25 |

Joystick commands use `0.75 × previous + 0.25 × target` smoothing on all motion axes.

**Heat warning:** Holding a toe-out stance for long periods heats up the roll motors. Take regular breaks to let them cool down. A yellow reminder appears above the motion controls while Roller mode is on. Use a level, clear surface and keep **Torque off** within reach when trying a new model or setting.

## Adjusting the sliders

The Action settings sliders follow the Roller mode switch. Each group keeps its own settings until the app restarts. Kick has separate fixed settings: scale 0.8, leg α 0 and head α 0.

| Default setting | Ordinary walking / other models | Roller walking |
| --- | --- | --- |
| Action scale | 1.0 | 0.7 |
| Leg α | 0.35 | 0.4 |
| Head α | 0.45 | 0.5 |

- **Action scale** changes movement amplitude around the default pose. **0** holds the default pose; **1** uses the original amplitude.
- Higher **Leg α** or **Head α** gives smoother, slower responses.
- Under **IMU filtering**, higher **Angular velocity α** gives more smoothing; higher **Gravity projection τ** slows accelerometer correction.

Release a slider to apply its value. Restarting the app restores defaults. Get-up and Pick use the ordinary settings; Roulade keeps its temporary smoothing while it runs.

## If something is not working

- **The page looks outdated:** press **Ctrl+F5** to reload it.
- **A servo is missing:** check power, wiring, and its assigned ID. Use **Scan all IDs** to find it.
- **There is no Bridge feedback:** stop the app in App Lab, run `sudo systemctl restart arduino-router` on the Uno Q, then click **Run** again.
- The system's Router stop script also resets the STM32. After restarting that service, run the app again to reload its firmware and refresh the container's socket mount. Bridge retries recover communication interruptions while the MCU app remains running.
- **Startup tries to download an already installed toolchain:** run `python3 tools/reuse-toolchain.py` on the Uno Q and try again.

## Project files

`assets/` contains the web interface, `python/` the controller and models, `sketch/` the Uno Q firmware, and `tools/` the installation helpers.

See [Program architecture](docs/architecture.md) for the block diagram, thread timing, Bridge recovery, and validation results.

Build caches, local validation scripts, logs, and temporary backups are excluded from the distributed app. ONNX policy files are required runtime assets and remain included.

## Sources and licensing

See [Third-party sources and notices](THIRD_PARTY_NOTICES.md) for upstream attribution and the original third-party license texts. An overall license for this repository and its ONNX policies has not been declared.
