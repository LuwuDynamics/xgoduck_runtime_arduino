# XGO Duck AI

English · [简体中文](README_CN.md) · [XGO Duck Wiki](https://wiki.xgorobot.com/kb/the-xgo-series-robot/models/duck) · [FAQ](https://wiki.xgorobot.com/kb/the-xgo-series-robot/duck-troubleshooting?model=duck) · [Changelog](docs/CHANGELOG.md)

Voice chat, motion control, USB audio/video and ToF ranging for XGO Duck on Arduino UNO Q. Use a browser for everyday operation; Duck does not need a display.

Prepare an Arduino UNO Q running official Debian 13, a carrier board, a USB data cable, a battery and servos. Voice chat needs a USB microphone and speaker. A USB camera and a VL53L5CX ToF sensor on the onboard Qwiic port are optional.

The first seven steps follow the official developer guide, with this repository’s XGO Duck AI packages and startup commands.

**Use the prompt below to have AI complete steps 1–4 for you.** Once the assistant verifies the installation, go straight to step 5.

Connect the UNO Q to your computer with **a phone USB cable that can transfer files**. A charge-only cable cannot carry data. Once connected, copy the following into an AI assistant with access to your computer’s terminal.

```text
My UNO Q is connected to my computer by USB. Please use this guide to install the latest XGO Duck AI app.
https://github.com/LuwuDynamics/xgoduck_runtime_arduino#ai-assisted-install
```

## 1. Set up Wi-Fi

Connect the UNO Q to your computer with a USB data cable. In Arduino App Lab, choose the keyboard layout, name the board, join Wi-Fi and set the board’s Linux password. Let the update check finish. Disconnect USB, fit the carrier as shown in the guide, then reopen App Lab and reconnect. [Illustrated guide](https://www.xgorobot.com/duck-developer-guide/index.html)

## 2. Find the board’s IP address

In Arduino App Lab, click the Wi-Fi icon in the lower-right corner, open **Network Info**, and note the board’s IP address. Connect your computer and the board to the same local network for installation.

## 3. Import XGO Duck AI

| Package | When to use it | What it includes |
| --- | --- | --- |
| Full package · [GitHub ZIP][duck-full] ([SHA-256][duck-full-sha]) · [ModelScope ZIP][duck-full-ms] ([SHA-256][duck-full-ms-sha]) | **Default for first installs and version upgrades** | About 470 MB. Includes the precompiled App, ARM64 Docker base image, board dependencies and STM32 upload tools. Once the package is on the board, installation can run over the local network. |
| [Precompiled .ard (Release)][duck-ard] · [SHA-256][duck-ard-sha] | **Updates** or a first install on a board with the runtime environment ready | About 80 MB. Includes STM32 firmware and the built Python environment. Startup uploads the bundled firmware. The board may still need internet access for missing system packages, container images or upload tools. |
| [App Lab source ZIP (repository)][duck-zip] · [SHA-256][duck-zip-sha] | **Boards that cannot install .ard** or editing the App | About 35 MB. Uses the existing `packages/xgoduck_ai.zip` link. Import it in App Lab, then prepare the dependencies and build the STM32 sketch on the board. |

For the full package, use ModelScope first on mainland China networks and GitHub first elsewhere. If the download fails, get both the ZIP and its SHA-256 file from the other source. For an older version, download the files from the matching [GitHub Release][duck-releases].

### Full first-install ZIP

Complete UNO Q initial setup and connect over Wi-Fi SSH to the official Debian 13 ARM64 image. Allow at least **2.5 GiB** of free space on the reference system. Verify the ZIP with its checksum, transfer it to UNO Q with SCP and extract it into a new directory on the board. If the servos are wired, support Duck and confirm torque is off. Stop any running Apps, then follow `INSTALL.txt` and run `bash install.sh` as `arduino`. The installer configures the 2 Mbps Bridge, enables autostart and launches the App. Continue with the checks in step 4. On the reference image, the board can install over the local network once it has the ZIP. Account binding and cloud voice need internet access.

The full package installs the App and its dependencies without reflashing the OS. Its extraction directory is separate from the fixed App destination: `/var/lib/arduino-app-cli/releases/xgoduck-ai-0-3-7-unoq` for 0.3.7. If that destination exists, the installer reuses it only when `.xgoduck-full-install.sha256` exactly matches the SHA-256 of the bundled `.ard`. A separately installed `.ard`, or a repacked file with the same version, may not meet this condition. Keep a verified working installation, or handle its directory and data under an explicit reinstall request before proceeding. Do not fabricate a receipt or overwrite the directory. [Full-package instructions](docs/full-install.txt)

To retain settings, first save this Duck's persistent data using the update procedure. After the full installer finishes, stop the new App and `xgoduck-ai-hardware.service`, restore the saved settings with their permissions and ownership, then start the hardware service and the new App before verification. [Migration and rollback notes (Chinese)](docs/deployment.md#更新与回退)

### Precompiled .ard

The board needs **Arduino App CLI and daemon 0.14 or newer**. In App Lab 0.11 or newer, select **Create new App ＋ → Import or install App** and choose the `.ard`. If your desktop App Lab is older and the board supports `arduino-app-cli app install`, install it over Wi-Fi SSH. [Arduino App Lab installation guide](https://docs.arduino.cc/software/app-lab/apps/manage-apps)

Transfer the `.ard` and `.ard.sha256` files to the same directory on the board. Run the commands as `arduino`, using the actual downloaded filenames.

```sh
sha256sum -c xgoduck-ai-unoq.ard.sha256 && \
  arduino-app-cli app install --yes xgoduck-ai-unoq.ard
```

Use the new App directory printed by the installer. For an update, migrate the settings for this Duck, then continue with step 4. If the board CLI or daemon is too old, update the board components or use the source ZIP below.

### App Lab source ZIP

Download and verify `xgoduck_ai.zip`. In App Lab, select **Apps → ＋ → Import App** or **Import or install App** in newer versions, then choose the ZIP. Locate the imported **XGO Duck AI** directory and leave the App stopped for step 4. [Illustrated import guide](https://www.xgorobot.com/duck-developer-guide/03-import-app.html)

<img src="docs/images/applab-import.jpg" alt="Arduino App Lab's ZIP import dialog" width="760">

## 4. Configure the board and start the App

**Full first-install ZIP.** The installer handles configuration and startup. Continue with the checks below.

**Precompiled .ard and source ZIP.** Use Wi-Fi SSH for the configuration below because USB detection can interrupt the computer’s USB/ADB connection.

1. For an update, support Duck and unload the servos. Stop the old App and back up its directory and `data/`. With both Apps stopped, migrate this Duck’s persistent settings, including calibration, binding and service region. [Deployment notes (Chinese)](docs/deployment.md#更新与回退)
2. Make sure the other Apps are stopped and the board has internet access, then run the commands below as `arduino` from the actual new App directory. Use the installer output or the App CLI list to find the directory.

   ```sh
   bash tools/install-ai-support.sh && \
     sudo bash tools/router-2m.sh && \
     arduino-app-cli app start "$PWD"
   ```

   The first script sets up hardware support, the cockpit and autostart. The second configures the shared Router for the firmware in that package. The development build starts the handshake at 115200 baud and negotiates **2 Mbaud** over the reliable protocol. Startup uploads the matching STM32 firmware. The source ZIP builds it on the board, and the `.ard` uses its bundled firmware. Once configured, you can also start the App with **Run** in App Lab.

**Verify the installation.** Open `http://<board-ip>/` and `http://<board-ip>:9527/` and check the running version and default App. After dependency preparation and the first startup finish, keep servo torque off and reboot the board once. Reconnect over Wi-Fi SSH, wait for the web pages/API, and confirm that the default App starts automatically with the correct version. Check servo and IMU feedback for connected hardware and preserve existing calibration and settings. Record unwired or uncalibrated hardware as unfinished setup.

A first installation may not have an account binding or a USB microphone and speaker yet, so **online voice is not a prerequisite for a successful installation**. Check `/api/voice/status` and the logs: record pending network, binding or audio setup, while still investigating process crashes and dependency errors. Require voice cloud connectivity and audio readiness only when the account is bound, audio hardware is connected and internet access is available. Continue with steps 5–8. An offline cold-boot test can follow once provisioning and the required setup are complete.

<a name="ai-assisted-install"></a>

### Set up Wi-Fi, install and verify with AI (steps 1–4)

The source ZIP installation workflow has been tested with Codex, DeepSeek Harness and Cursor. Allow 30–60 minutes for a first source install, depending on download speed. The full package includes dependencies, so board setup time mostly depends on transfer, extraction and local installation.

These are the instructions the AI assistant follows for steps 1–4. Enter passwords in the local window when prompted. If you know the board’s IP, want a specific version or already have a package, include that information in your message to the assistant.

```text
Connect my Arduino UNO Q to Wi-Fi, install or update XGO Duck AI, then start and verify it.
Detect whether this is a first install or an update. Use the latest AI release by default.
Use automatic region selection on a new board and keep the existing region on updates.
Use any board IP, version, region or local package path I supply.
Project repository: https://github.com/LuwuDynamics/xgoduck_runtime_arduino

1. Use the arduino account. If I supplied an IP, try SSH from my computer first, using an existing
   key if available. Once logged in, continue with the board checks. If you need to find the IP
   or set up Wi-Fi, connect over USB and find the UNO Q with adb devices -l. If several boards
   match, let me choose the serial number. Check Wi-Fi over ADB. If connected, read the wireless IPv4 address.
   If Wi-Fi setup is needed, show my computer’s current network name and let me confirm or choose
   another. Ask me for the name if it cannot be read. Open an interactive window on my computer
   for hidden password entry (PowerShell on Windows, Terminal on Mac). On Windows, after I confirm
   the network, reuse its saved password locally if permissions allow. Prompt me if it is unavailable.
   On Mac, prompt me for the Wi-Fi password. Skip the Wi-Fi password for open networks.
   Connect using the board’s NetworkManager over ADB. Verify the selected network and its wireless IPv4
   address. Handle passwords in the local interactive process and keep them out of chat, command arguments,
   shell history and logs. If you cannot open a visible window, give me one command to start the same
   interactive setup in my terminal, then continue after I enter the credentials.
   If the arduino account on a new board needs a password, ask me to set one and enter it again
   to confirm. On a configured board, use the existing password or SSH key. If SSH needs enabling,
   run arduino-app-cli system network-mode enable over ADB. Log in from my computer as arduino
   at the board’s IP and continue installation over that SSH connection. Use the local window
   for any later sudo or login prompts.
   Confirm UNO Q, official Debian 13 ARM64, free space, the arduino account (UID 1000), and
   the App CLI and daemon versions. Use arduino-app-cli app list for installed Apps,
   app details <App ID> for the actual path, app ps for running status, and
   arduino-app-cli properties get default for the startup App.
   Check command help on older CLIs. Resolve the target version first. For “latest XGO Duck AI”, read the
   version from the latest GitHub Release with a five-second timeout. If the lookup fails, verify the
   version in the ModelScope full ZIP as in item 2, use it for the checks below, and report that the latest
   release is unconfirmed. If I requested the master walking pose, compare the installed python/rl_core.py
   SHA-256 with the target build-manifest.json before reusing an App. Both builds can report 0.3.6.
   If an App is running, check the cockpit, /api/version and /api/status without changing its state.
   If the requested version and build are running normally, tell me the board is already up to date.
   Give me the running version, board IP, cockpit URL and port 9527 control URL, then finish here.
   If the target version is installed but unhealthy, inspect its App and service logs before deciding how to repair it.
   For an installation, keep Wi-Fi SSH available. USB detection can interrupt USB/ADB.

2. Use the full ZIP for the target version by default for both first installs and upgrades from an older
   version (item 4). Reuse a verified copy on my computer when available.
   - When a download is needed, check the public IP region on the computer that will download the full ZIP,
     using the same proxy settings as the download. Read only the loc field with this command.
     curl -fsS --max-time 5 https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^loc=//p'
     In Windows PowerShell, use curl.exe -fsS --max-time 5 https://www.cloudflare.com/cdn-cgi/trace | Select-String '^loc='
     For CN, try ModelScope first. For other known regions, try GitHub first. If the lookup fails or a
     split proxy makes the region unclear, briefly probe both .sha256 links in parallel and use the first
     valid checksum response. The board's LAN address and voice service region do not determine the download source.
     ModelScope full ZIP and checksum
     https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip
     https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip.sha256
     GitHub full ZIP and checksum
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip.sha256
     Download the pair from the selected source. On timeout, a missing file, a checksum failure or a version
     mismatch, fetch and verify both files for the target version from the other source. The full ZIP includes
     the .ard, runtime image and dependencies.
   - If I request a lightweight install or the target version has no full package, use its precompiled
     .ard on a board that supports Releases (item 5).
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard
   - Use the source ZIP (item 6) for editing, for a board that cannot install Releases, or when only the
     source ZIP is available for the target version. If I request the master walking pose, use
     packages/xgoduck_ai.zip and its .sha256 from the master checkout. The AI branch source ZIP is here.
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
   For a specific version or missing direct links, look for the matching XGO Duck AI version here.
   https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases
   Get each GitHub package and its .sha256 from the same Release. For the source ZIP, get both from the
   selected branch. Verify on my computer, transfer the files with SCP, and run sha256sum -c again on the board.
   If a checksum fails, download a fresh copy and verify it. Check version.txt and the UNO Q target.
   For the full ZIP, inspect the bundled .ard. If the requested version is missing or the candidate is older than the
   installed version, keep the existing installation and report the versions.

3. If the servos are wired, have me support Duck and confirm torque is off. For an update, stop the old
   App, record its path and the default App, and back up the old App and data/. Keep the old App for
   rollback. Install a .ard or source ZIP into a separate directory. With both Apps stopped,
   migrate this Duck's persistent IMU calibration, servo center records, identity, binding, region and
   gamepad settings. Follow the legacy center migration in docs/ai-chat-en.md. Skip old .cache,
   data/native/ and runtime sockets. Confirm that other Apps can stop and have stopped before running the
   installer or configuring the shared Router. The full package starts automatically: follow item 4
   to stop services before migrating data and handle any existing destination first. Use only one route in items 4–6.

4. For the full ZIP, allow at least 2.5 GiB for a first install on the reference image. Extract on the board
   into a new directory. Read INSTALL.txt and the fixed app_root in install.sh; the extraction directory
   does not change that destination. If it exists, compare .xgoduck-full-install.sha256 with the exact
   SHA-256 of the bundled .ard. Only a match permits stopping the App and rerunning the installer.
   If the receipt is missing or differs, retain a verified working installation and report the conflict.
   If I have explicitly requested a reinstall, save the data to retain, stop the related services and
   handle the old directory first. Do not fabricate a receipt or overwrite the directory.
   Run bash install.sh as arduino, using sudo when prompted. It uses bundled system packages, ARM64
   images and upload tools, installs the App, sets the 2 Mbps Bridge and autostart, then launches it.
   When upgrading or restoring saved settings, stop the new App, then run sudo systemctl stop xgoduck-ai-hardware.service.
   Migrate the persistent settings from item 3 and check their content, permissions and ownership;
   retain the new native files. Run sudo systemctl start xgoduck-ai-hardware.service, then
   arduino-app-cli app start --no-prepare <new-directory>. The full package does not reflash the OS.
   Complete migration and the region handling in item 7 before verification.
   If this board image needs a component outside the bundle, identify it before trying to supply it.

5. For a .ard, check arduino-app-cli version and arduino-app-cli app install --help. The CLI and daemon
   need Release support (0.14+). Run arduino-app-cli app install --yes <verified-file.ard> over SSH.
   App Lab 0.11+ can also install it via Import or install App. An older desktop App Lab can use the SSH
   route when the board supports it. Find the actual path in the installer output or App list.
   If install reports a dependency download failure or an existing release, check the path, version and
   logs before retrying. Reuse the matching installed release and supply any missing Docker image or
   STM32 upload tools. Use the bundled Python environment and firmware for normal startup. If the
   firmware is missing or fails verification, follow “Precompiled UNO Q release” in docs/ai-chat-en.md.
   If the board is too old and I want to use the .ard, check with me before updating board components.

6. For the source ZIP, check arduino-app-cli app import --help. Run arduino-app-cli app import <ZIP>
   if supported, or guide me through the App Lab import. Locate the imported directory.
   Read sketch/sketch.yaml, provenance/toolchain.json and python/requirements.txt. Run arduino-cli core list
   as arduino, install the core pinned in sketch/sketch.yaml if it is missing or differs, and verify it.
   For 0.3.6, use arduino-cli core install arduino:zephyr@1.0.0. Use the RouterBridge and RPClite dependencies listed in the package’s sketch.yaml.
   Local directory entries require Arduino CLI 1.3.0 or later and select the bundled libraries. Use the Linux ARM64
   arm-zephyr-eabi 1.0.1 toolchain. The upload also needs the matching core. If the toolchain download
   fails, download the official archive from provenance/toolchain.json on my computer, verify its size
   and SHA-256 and transfer it with SCP. From the App directory, run
   python3 tools/prepare-ai-sketch.py --toolchain-archive <official-archive-on-board>.
   For slow Python downloads in China, keep any existing custom index. If needed, run
   python3 tools/configure-ai-python.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple/
   If board-side Python downloads remain slow, use a container on my computer matching the board's
   container image, Linux ARM64 architecture and Python version. Download or build all wheels and
   dependencies from python/requirements.txt, verify them, transfer them with SCP and install offline
   in the App's Python environment. Check pinned versions and SDK imports before continuing. Do not
   use native macOS wheels or write an installation-success marker before validation.
   For failed core, Docker image or system package downloads, use the logs to identify the missing
   dependency. Temporarily use my computer's existing proxy if needed and restore the original settings afterward.

7. For automatic region selection, leave the region unset on a fresh install and keep the saved region
   or custom endpoint on an update. If I select a region, set it in the cockpit after startup, or run
   python3 tools/configure-ai.py --region cn or python3 tools/configure-ai.py --region global
   before starting the .ard or source App.
   For the .ard and source ZIP, keep internet access and use the actual new App directory after
   migrating data. Run bash tools/install-ai-support.sh, then sudo bash tools/router-2m.sh, then
   arduino-app-cli app start "$PWD". Continue after each command succeeds. Startup uploads the STM32
   firmware. The reference full bundle installs system Router 0.11.0, App CLI 0.14.1, Arduino CLI
   1.5.1 and ARM64 Python base image 0.13.1. Reliable-protocol packages run their own bundled Router binary.
   Check the selected package and reuse compatible components.
   Confirm the default App points to the new directory. For a reliable-protocol package, check that
   the Router runs with --serial-protocol auto and --serial-max-baudrate 2000000. After App startup,
   sudo stty -F /dev/ttyHS1 speed should report 2000000. For an older fixed-baud Duck, run its own Router script.

8. Keep the servos unloaded during verification. Check the cockpit, the port 9527 control page and
   /api/version. After preparation and the first startup finish, run sudo systemctl reboot. Reconnect
   over Wi-Fi SSH and wait for the web pages/API. Confirm a changed boot ID, automatic startup of the
   default App and the correct version. After an upgrade, check the servo centers, IMU calibration,
   device identity and any existing account binding against the old App.
   Check Bridge in /api/status and servo/IMU feedback for connected hardware. Record unwired or
   uncalibrated hardware as unfinished setup. Check /api/voice/status and logs. On an unbound first
   install, with no USB audio, or without internet, do not require bound, cloud_connected and
   audio_ready to all be true. Record the missing setup and still investigate crashes or dependency errors.
   Require voice cloud connectivity and audio readiness when binding, audio hardware and internet
   access are all available. Pending binding is not an installation failure. Keep internet available
   for this normal reboot if it was available before; offline cold boot can be tested after setup. If ToF is attached,
   poll /api/tof/status for state=ranging, fresh=true, increasing seq and valid_zones > 0.
   If needed, compare against python /app/tools/read-tof.py --seconds 4 inside the App container.
   If the connected ToF sensor is still undetected after the verification reboot, mark it as pending a retest.
   Just tell me, “Turn Duck’s power switch off and back on once. After Duck starts, check /api/tof/status again.”
   If voice reports a libopus.so.0 permission error, check the container user's access to the native
   library and its parent directories. On failure, inspect App, Router and hardware service logs.
   To roll back, stop the new App, follow docs/deployment.md to restore hardware support and the default
   App from the old directory, then run the old version and keep the backup. Report the chosen package,
   checksum, running version, path and checks.
   Report the IP confirmed after reboot and the two working URLs, http://<board-ip>/ and
   http://<board-ip>:9527/, with the board’s actual IP.
   Once installed, send me back to README steps 5–8: number the servos, assemble Duck, calibrate all
   15 servo centers together, set ID 30 separately, calibrate the IMU, then check Default pose, bind an
   account and select an agent. Point me to the next unfinished step.
```

[duck-releases]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases
[duck-full-ms]: https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip
[duck-full-ms-sha]: https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip.sha256
[duck-full]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip
[duck-full-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip.sha256
[duck-ard]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard
[duck-ard-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard.sha256
[duck-zip]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
[duck-zip-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip.sha256

## 5. Configure and number the servos

Connect one servo at a time. Set its ID for the intended joint, verify it and label the servo. [Servo setup guide](https://wiki.xgorobot.com/kb/the-xgo-series-robot/duck-guide-05?model=duck)

## 6. Assemble XGO Duck

Match each servo ID and orientation to the assembly diagrams, then assemble the body and connect the audio and video hardware. [Assembly and wiring guide](https://wiki.xgorobot.com/kb/the-xgo-series-robot/duck-guide-06?model=duck)

## 7. IMU and servo calibration

Follow the calibration page to calibrate the servos and IMU. Check the default pose before taking the first steps. [Calibration and first walk guide](https://wiki.xgorobot.com/kb/the-xgo-series-robot/duck-guide-07?model=duck)

## 8. Bind an account and select an agent

Choose your service region, bind Duck to your account and select an agent. Try the wake word for your region to start a conversation. [Account binding and voice setup guide](https://wiki.xgorobot.com/kb/the-xgo-series-robot/duck-ai-voice?model=duck)

---

Based on Pollen Robotics' [Microduck](https://github.com/pollen-robotics/microduck). [Hardware](https://github.com/LuwuDynamics/xgoduck_hardware) · [Training](https://github.com/LuwuDynamics/xgoduck_rl)
