# XGO Duck AI

English · [简体中文](README_CN.md)

Voice chat, motion control, USB audio/video and ToF ranging for XGO Duck on Arduino UNO Q. Use a browser for everyday operation; Duck does not need a display.

## Before you start

- An Arduino UNO Q running the official Debian 13 system. Complete Wi-Fi and Linux account setup in [Arduino App Lab](https://www.arduino.cc/en/uno-q), then confirm `ssh arduino@<board-ip>` works.
- Assemble the carrier, power and servos using the [hardware guide](https://github.com/LuwuDynamics/xgoduck_hardware). Set servo IDs and calibrate joint centres before enabling motion.
- USB microphone and speaker for chat; use a powered USB hub when needed. A USB camera and a VL53L5CX ToF on the onboard Qwiic connector are optional. Power Duck independently when using USB accessories.
- Internet access for the first installation to download system packages, build tools and Python/container dependencies.

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
- **Motion:** finish servo setup and calibration, support Duck, and test Default pose before standing or walking. Desktop control is at `http://<board-ip>:9527/`; gamepad and calibration are also accessible from cockpit settings. Servo KP/KD remain user-controlled and are not overwritten at startup.
- **ToF:** a connected sensor displays an 8×8 live distance map. Default forward avoidance slows within 15 cm and stops at 10 cm; missing or stale data pauses forward travel. Chat still works without ToF; see [avoidance configuration](docs/forward-avoidance.md) to disable the guard.

## Updates and troubleshooting

- Support Duck and turn torque off before updating. **Run may compile and flash the STM32 sketch again.**
- Keep the existing App's `data/` when updating the same robot: it contains identity, volume, calibration and controller settings. Do not copy another robot's identity or calibration. Repeat one-time setup if the App directory or host support scripts change.
- For first-Run download failures, check the board's internet connection. For missing audio, test the speaker and microphone in cockpit settings.
- See the [installation guide](docs/ai-chat-en.md) for migration and toolchain help, and the [hardware guide](https://github.com/LuwuDynamics/xgoduck_hardware) for assembly and calibration.

To build the package yourself, run `python3 tools/package-ai-app.py` in the repository on your computer. Import the resulting `dist/xgoduck_ai.zip` using the same App Lab steps.

Based on Pollen Robotics' [Microduck](https://github.com/pollen-robotics/microduck). [Hardware](https://github.com/LuwuDynamics/xgoduck_hardware) · [Training](https://github.com/LuwuDynamics/xgoduck_rl)
