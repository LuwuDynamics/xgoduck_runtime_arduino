# XGO Duck AI

English · [简体中文](README_CN.md)

Voice chat, motion control, USB audio/video and ToF ranging for XGO Duck on Arduino UNO Q. Use a browser for everyday operation; Duck does not need a display.

## Before you start

- An Arduino UNO Q running the official Debian 13 system. Complete Wi-Fi and Linux account setup in [Arduino App Lab](https://www.arduino.cc/en/uno-q), then confirm `ssh arduino@<board-ip>` works.
- Assemble the carrier, power and servos using the [hardware guide](https://github.com/LuwuDynamics/xgoduck_hardware). Set servo IDs and calibrate joint centres before enabling motion.
- USB microphone and speaker for chat; use a powered USB hub when needed. A USB camera and a VL53L5CX ToF on the onboard Qwiic connector are optional. Power Duck independently when using USB accessories.
- Internet access for the first installation to download system packages, build tools and Python/container dependencies.

[XGO Duck Getting Started](https://www.xgorobot.com/duck.html) · [XGO Duck Developer Guide](https://www.xgorobot.com/duck-developer-guide/index.html)

Use the [bilingual assembly guide (PPTX, about 69 MB)](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/assembly/xgo-duck-assembly-zh-en-20261016.pptx) to check servo IDs, mounting orientation, cable routing and screw sizes.

## Install with an AI assistant

After assembling Duck and connecting the UNO Q to Wi-Fi, copy this prompt into an AI assistant with terminal access and fill in the board IP. Sections 1–3 below also cover manual installation.

```text
Install XGO Duck AI on my Arduino UNO Q. Execute the steps and verify each result.
Board IP: [fill in]. Service region: [China cn / International global].
Installation: [first install / update this Duck].
Ask me for missing details. SSH as arduino. I will enter passwords at login or sudo prompts.

Read my copy of the assembly PPTX or download it from GitHub:
https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/assembly/xgo-duck-assembly-zh-en-20261016.pptx
Use slide 1 for servo IDs, slides 3–15 for base, leg and foot orientation and cable routing,
and slides 16–25 for upper assembly and circuit board mounting. Ask me to check unclear
details on the physical Duck. If you cannot read a diagram, ask for a screenshot of that slide.
Use the official calibration tutorial in step 7 to set servo centers.

1. Connect over Wi-Fi SSH to the Debian 13 ARM64 host. Check reconnect access, free space,
   and existing Apps with arduino-app-cli app list. Record the installed App CLI, Arduino CLI
   and Router versions, plus App Lab and the Python base image if available. The recorded board
   reference is Router 0.10.0, App CLI 0.13.0 and Python base image 0.12.0. Reuse compatible
   board components and check compatibility before changing versions.
2. Download the App Lab ZIP and verify it against the adjacent .sha256 file:
   https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
   If the board cannot download it, verify the download on my computer and transfer it with SCP.
   Check arduino-app-cli app import --help. If supported, run arduino-app-cli app import <ZIP path>
   as arduino. Otherwise use Apps → Create new app → Import App → Import from computer in App Lab.
   List the Apps again to find the actual directory. For an update, first ask me to support Duck
   and confirm torque is off. Stop the old App and back up its directory and data/ before
   following the update instructions in docs/ai-chat-en.md. Keep this Duck's
   identity, calibration and settings. On retry, reuse the verified installation.
3. In the installed App directory, follow sketch/sketch.yaml, provenance/toolchain.json and
   python/requirements.txt. This package pins Arduino Zephyr 1.0.0, arm-zephyr-eabi 1.0.1,
   Arduino_RouterBridge 0.4.3, Arduino_RPClite 0.3.1 and MsgPack 0.4.2. Resolve the remaining
   libraries from the same profile. Preserve any existing custom Python package index.
4. Ask me to support Duck and confirm torque is off and any other running Apps can be stopped.
   Keep the Wi-Fi SSH session available, stop all Apps, then run bash tools/install-ai-support.sh
   followed by sudo bash tools/router-2m.sh. The first script installs host dependencies, prepares
   USB Host, the cockpit and autostart, and precompiles the MCU sketch. The second sets the shared
   Router Bridge serial rate to 2 Mbps (2000000) and restarts the Router. Keep the Router backup.
   Diagnose any failed step before moving on.
5. Verify the Router's effective ExecStart and the default App. As arduino, start it from its
   directory with arduino-app-cli app start "$PWD". The first start may upload the STM32 sketch.
   After the build and startup, open http://<board-ip>/ and read /api/status and /api/voice/status
   on the board's port 9527. Check advancing feedback, torque off, and the voice connection state.
6. Guide me to Link AI to select my region and open its platform. I will sign in, grant Bluetooth
   access and bind Duck. Check that voice comes online and ask me to try “小鸭小鸭” (xiǎo yā, xiǎo yā).
   Once I confirm a reboot is OK, check autostart and ask me to listen for both startup duck sounds.
   Report the installation path, component versions, 2 Mbps configuration and any remaining steps.
7. On first commissioning or when investigating unstable walking, have me support Duck and
   follow this order. Reuse valid calibration for the same Duck.
   a. Servo calibration. Follow either method in the official guide. Secure Duck in a
      calibration stand if available, or calibrate by hand using the front and side views.
      https://www.xgorobot.com/duck-developer-guide/07-calibration-control.html
      Confirm all 15 servo IDs respond and open Calibration on port 9527. I will move each
      joint to its mechanical center and click Set center & hold. Verify each result, then
      have me exit Calibration and remove Duck from the stand if I used one.
   b. Gravity alignment. On a level surface, keep Duck upright and still in Default Pose
      for about 30 seconds. Run tools/calibrate-imu.py in the actual App's Python environment
      as described in docs/imu-calibration.md and save data/imu-calibration.json for this Duck.
      Check any existing file and back it up before recalibrating.
   c. Gyro bias. The same stationary samples measure gyro bias. Have me support Duck before
      restarting the motion process to load the calibration. With Duck upright and stationary,
      verify that /api/status shows gravity near [0, 0, -1] and corrected gyro rates near zero.
   d. Gains. With torque off, read all 15 servos in Servo setup. Use KP=5 and KD=10 as the
      baseline. With my confirmation, use Set KpKd on each differing ID and read it back.
   Once all four checks pass, have me clear a level area and confirm the walking test. Check
   Default Pose and standing first, then try a short straight walk at 0.10 m/s. Keep test linear
   speed at or below 0.15 m/s. Stop if Duck shakes or loses balance and recheck the setup.
```

## 1. Import the App Lab package

1. **[Download `xgoduck_ai.zip` for App Lab](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip)** and keep it zipped.
2. Connect to the board in App Lab. Choose **Apps → Create new app → Import App → Import from computer**, then select this ZIP.
3. After importing, stop any running Duck Apps before the one-time setup below.

<img src="docs/images/applab-import.jpg" alt="Arduino App Lab's Import an App dialog accepts a local ZIP" width="760">

The package includes the App, STM32 sketch, models, web pages and setup scripts. It contains no user accounts, Wi-Fi credentials, keys or robot calibration. GitHub's **Code → Download ZIP** is a source archive; use the dedicated package above for App Lab.

## 2. One-time setup and first Run

Run these commands in the **UNO Q host terminal**, using App Lab's board shell or SSH. They assume the imported directory is `~/ArduinoApps/xgoduck_ai`; substitute the actual path if App Lab used another name. `arduino-app-cli app list` shows App paths.

```sh
cd ~/ArduinoApps/xgoduck_ai

bash tools/install-ai-support.sh
sudo bash tools/router-2m.sh
```

Setup asks for the Linux password and prepares hardware support, USB Host, the web entrance and autostart. Switching to USB Host disconnects computer USB/ADB access, so **use Wi-Fi/SSH for setup**. Choose the service region in the cockpit after the first Run. You can switch it there later; phone provisioning and conversation language do not choose a region automatically.

Reconnect through **Network** in App Lab, open **XGO Duck AI** and click **Run**. The first Run downloads dependencies, compiles and uploads the STM32 sketch, and can take several minutes. After a successful first Run, the App starts automatically on later boots.

Later power-ups prepare USB Host and start the cockpit, chat and motion services automatically. **You do not need to open App Lab or click Run each time.** With Wi-Fi, account binding and USB audio ready, two startup duck sounds play in order:

| Order | Meaning | Example |
| --- | --- | --- |
| 1 | Network connected | [Listen to the first duck sound](assets/startup-network.wav?raw=true) |
| 2 | Voice service connected; chat ready | [Listen to the second duck sound](assets/startup-voice.wav?raw=true) |

After both sounds, say **“小鸭小鸭” (xiǎo yā, xiǎo yā)** to wake Duck, then speak after its wake-up duck call. You can also listen under **Link AI → Automatic startup & wake**. Examples play on your phone/computer; real startup sounds play through Duck’s speaker.

## 3. Bind an account and select an agent

1. Connect your computer or phone to Duck's network and open **`http://<board-ip>/`**. No port number or page suffix is needed.

   <img src="docs/images/cockpit-en.jpg" alt="Cockpit home screen example; the Link AI button is near the top" width="380">

2. Tap **Link AI**, tap **China** or **International** to save the region and reconnect voice immediately. The window shows the current region and binding state; open its matching platform:

   | Device region | Binding and agent platform |
   | --- | --- |
   | `global` International | [agent.luwudynamics.ai](https://agent.luwudynamics.ai/) |
   | `cn` China | [agent.xgorobot.com/devices](https://agent.xgorobot.com/devices) |

3. Sign in, open **Devices → Add device**, follow the Bluetooth prompts, and select the device beginning with `XGO-Duck`. Complete Wi-Fi setup and account binding, then select an agent.
4. Return to **Link AI** to check the device server, binding and voice connection. Once voice is online, say **“小鸭小鸭” (xiǎo yā, xiǎo yā)** to start chatting.

<img src="docs/images/ai-binding-en.jpg" alt="The cockpit's Link AI dialog provides China and international binding links" width="760">

Web Bluetooth requires a compatible Chrome / Edge browser. On iPhone / iPad, add the device using the matching Mini Program or App:

| International App | China WeChat Mini Program |
| --- | --- |
| <img src="docs/images/qr_app_intl.png" alt="International App download QR" width="150"> | <img src="docs/images/qr_miniprogram.png" alt="China WeChat Mini Program QR" width="150"> |
| [Open the App download page](https://agent.luwudynamics.ai/download) | Scan with WeChat |

China and international accounts and bindings are separate. Selecting a region saves it and reconnects voice, preserving conversation language, volume and motion settings. Bind separately when switching to another service for the first time. Opening a platform link alone does not switch servers. If the device belongs to another account, unbind it from that account first.

## 4. Everyday use

- **Interface language:** Opening or refreshing follows the accessing browser’s system language: Chinese for Chinese locales, English otherwise. The language button changes only the current page.
- **Audio check:** Under Live audio, Test speaker plays about three seconds of synthesized duck calls; tap again to stop. Listen live plays Duck’s microphone on your phone/computer.
- **Chat:** say “小鸭小鸭” to wake Duck. Manage its agent on the platform; adjust volume in **Settings → Speaker**.
- **Audio/video:** start or stop the camera, control its light, save photos and listen live to Duck's microphone from the cockpit.
- **Walking setup.** Calibrate the servo centers with a calibration stand or by hand. Then calibrate [gravity alignment and stationary gyro bias](docs/imu-calibration.md) and check every servo for KP = 5 and KD = 10. Support Duck for the Default Pose and standing checks. Once it stands steadily, start with a short walk at 0.10 m/s and keep test linear speed at or below 0.15 m/s. Open desktop control and calibration at `http://<board-ip>:9527/`. [Official servo calibration guide](https://www.xgorobot.com/duck-developer-guide/07-calibration-control.html)
- **ToF:** a connected sensor displays an 8×8 live distance map. Default forward avoidance slows within 15 cm and stops at 10 cm; missing or stale data pauses forward travel. Chat still works without ToF; see [avoidance configuration](docs/forward-avoidance.md) to disable the guard.

## Updates and troubleshooting

- Support Duck and turn torque off before updating. **Run may compile and flash the STM32 sketch again.**
- Keep the existing App's `data/` when updating the same robot: it contains identity, volume, calibration and controller settings. Do not copy another robot's identity or calibration. Repeat one-time setup if the App directory or host support scripts change.
- For first-Run download failures, check the board's internet connection. For missing audio, test the speaker and microphone in cockpit settings.
- See the [installation guide](docs/ai-chat-en.md) for migration and toolchain help, and the [hardware guide](https://github.com/LuwuDynamics/xgoduck_hardware) for assembly and calibration.

To build the package yourself, run `python3 tools/package-ai-app.py` in the repository on your computer. Import the resulting `dist/xgoduck_ai.zip` using the same App Lab steps.

Based on Pollen Robotics' [Microduck](https://github.com/pollen-robotics/microduck). [Hardware](https://github.com/LuwuDynamics/xgoduck_hardware) · [Training](https://github.com/LuwuDynamics/xgoduck_rl)
