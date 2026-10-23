# XGO Duck

Control an XGO Duck from a web browser using Arduino Uno Q. Includes walking, roller walking, get-up, pick, and roulade policies.

Based on [Microduck](https://github.com/pollen-robotics/microduck) by [Pollen Robotics](https://pollen-robotics.com/microduck/).

## Start the app

1. Open the project in **Arduino App Lab** and click **Run**. For a new board, complete the first-time setup below.
2. Open the web interface shown by App Lab, or visit `http://<board-ip>:9527`. Use **中文 / EN** in the header to switch between Simplified Chinese and English.
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
| **Zero motion** | Stops travel while keeping the current control mode. |
| **Torque off** | Unloads the motors. |

Use the left pad to move and the right pad to turn. Release a pad to return it to center. Use **Head and mouth** to adjust the head and mouth. **Pick** and **Roulade** are available while walking upright.

Leaving the Control page stops travel but keeps motor torque enabled. Use **Torque off** when you want the robot to rest.

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
| Forward / backward | -0.2 to +0.4 | -0.4 to +0.4 |
| Sideways | ±0.3 | 0 |
| Turning | ±1.0 | ±0.25 |

**Heat warning:** Holding a toe-out stance for long periods heats up the roll motors. Take regular breaks to let them cool down. A yellow reminder appears above the motion controls while Roller mode is on. Use a level, clear surface and keep **Torque off** within reach when trying a new model or setting.

## Adjusting the sliders

The Action settings sliders follow the Roller mode switch. Each group keeps its own settings until the app restarts.

| Default setting | Ordinary walking / other models | Roller walking |
| --- | --- | --- |
| Action scale | 1.0 | 0.7 |
| Leg α | 0.45 | 0.4 |
| Head α | 0.2 | 0.5 |

- **Action scale** changes movement amplitude around the default pose. **0** holds the default pose; **1** uses the original amplitude.
- Higher **Leg α** or **Head α** gives smoother, slower responses.
- Under **IMU filtering**, higher **Angular velocity α** gives more smoothing; higher **Gravity projection τ** slows accelerometer correction.

Release a slider to apply its value. Restarting the app restores defaults. Get-up and Pick use the ordinary settings; Roulade keeps its temporary smoothing while it runs.

## If something is not working

- **The page looks outdated:** press **Ctrl+F5** to reload it.
- **A servo is missing:** check power, wiring, and its assigned ID. Use **Scan all IDs** to find it.
- **There is no Bridge feedback:** stop the app in App Lab, run `sudo systemctl restart arduino-router` on the Uno Q, then click **Run** again.
- **Startup tries to download an already installed toolchain:** run `python3 tools/reuse-toolchain.py` on the Uno Q and try again.

## Project files

`assets/` contains the web interface, `python/` the controller and models, `sketch/` the Uno Q firmware, and `tools/` the installation helpers.
