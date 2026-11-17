# Bluetooth gamepad control

Open **Settings → Gamepad** in Duck Cockpit. The panel follows the cockpit language. The standalone page at `http://<duck-host>:9527/gamepad.html` starts in Chinese for Chinese browser locales and in English otherwise, with a language button in the header.

1. Put an Xbox Bluetooth controller or Switch Pro controller into pairing mode.
2. Select **Scan for controllers**. The scan stops after 30 seconds.
3. Select **Pair & connect** beside the intended controller. Existing pairings offer **Connect**, **Disconnect**, and **Forget**.
4. Wait for **Input ready**. Center both sticks, release all buttons, and select **Enable gamepad control**.

Pairing and enabling the input source do not arm motors. Default buttons are Menu/Start for walk, the bottom face button (Xbox A) for default pose, and the right face button (Xbox B) for torque off. Move sticks directly; no LB modifier is required. Centering the sticks zeros movement. In ordinary walking, left Y maps to forward 0.2 / backward 0.18 m/s, left X to strafe ±0.3 m/s, and right X to yaw ±1.0 rad/s. In Roller mode the ranges are forward/backward ±0.4 m/s, strafe 0 and yaw ±0.25 rad/s. The input deadzone is 14% and remaining travel is rescaled to full range.

While gamepad control is enabled, periodic browser movement messages are ignored. Explicit browser mode controls and Torque off remain available. A disconnected/replaced gamepad, stale input snapshot, or failed worker connection disarms and releases the input source. Reconnection requires explicitly enabling control again. Calibration and servo setup release gamepad ownership.

## Button binding

Disable gamepad control, choose **Bind** beside an action, release any held buttons, then press one controller button within 15 seconds. Learning consumes input without executing robot actions. A disconnect, timeout, multiple presses, or duplicate assignment leaves existing bindings unchanged. Use **Clear**, **Cancel binding**, or **Restore defaults** as needed. Keep a button assigned to **Torque off**.

- **Walk / get up** also stands Duck up from its seated posture.
- **Sit**, **Roller mode**, **Left kick**, and **Right kick** start unassigned. Existing custom bindings carry forward.
- **Roller mode** switches between normal walking and roller skating. Centre the sticks after switching to resume movement.
- **Duck calls** offers all seven sounds from the cockpit, each unassigned by default. Press its bound button once to play a call through Duck's speaker.

Mappings are saved atomically in `data/gamepad-bindings.json` and survive app and board restarts. Learning runs on the board. Press counters from evdev retain short presses between web polls.

## Architecture and installation

- `tools/gamepad/server.py`: system service using BlueZ D-Bus for scanning/pairing, with a pairing agent authorized only for the controller explicitly selected in the page. It is not the system default agent.
- `tools/gamepad/input_reader.py`: standard Linux evdev input, requiring both sticks and face buttons. Keyboard and audio-device input nodes are excluded. Device state is sampled using ioctls so dropped input events are resynchronized.
- `python/gamepad.py`: firmware API and 20 Hz input worker. This does not change the 100 Hz servo loop, 50 Hz policy loop, or 2 Mbps Bridge link.
- The privileged worker exposes only `data/gamepad.sock` (root:arduino, 0660), not a network port. Browser mutations require same-origin requests with `X-Duck-Gamepad: 1`.

On this board:

```sh
sudo apt-get install python3-evdev python3-dbus python3-gi
sudo install -m 0644 tools/gamepad/duck-gamepad.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now duck-gamepad
```

The systemd unit uses the existing deployment path `/home/arduino/ArduinoApps/xgoduck_runtime_arduino`. Override `DUCK_GAMEPAD_SOCKET` in other firmware layouts. Restart the Linux app after installing the firmware API; do so with the robot supported and torque disabled.

## Unitree BLE remote

Remotes named `Unitree-*` offer **Connect BLE**. `Unitree-35630A` was verified with FFE0, notification characteristic FFE1 and write characteristic FFE2. The service forces LE/public address through the installed `/usr/bin/gatttool`, discovers handles and the CCCD, subscribes, and writes the eight ASCII bytes `59532b32`. No PIN or OS pairing is needed for this verified device. The BLE protocol reference is https://github.com/legion1581/unitree_ui/blob/main/docs/remote-control.md .

`unitree_remote.py` decodes finite, bounded 20-byte samples into the same input schema as evdev. It preserves button press counters for binding, supports shoulders, triggers, function keys and D-pad, and reports battery level. The app's existing explicit enable, neutral check, identity check and stale-input/disconnect protection apply. Connection selects the remote input but never arms motors. There is no automatic reconnection or silent fallback to another controller after link loss. Select **Connect BLE**, then explicitly enable control again. On other R3 firmware versions, protocol compatibility still needs checking.

## Compatibility

Xbox support requires a Bluetooth-capable controller; older proprietary Xbox Wireless models require a suitable receiver and driver. Input mappings follow Linux gamepad button positions, not Nintendo's printed A/B labels. Switch Pro requires `hid-nintendo`; the stock kernel on this board initially had `CONFIG_HID_NINTENDO` disabled. The Linux v7.0 driver has now been compiled against `7.0.0-g122c2c22d838` and loaded successfully. To reproduce the installation on a matching Linux 7.0 system, run `sudo sh tools/gamepad/install-nintendo-driver.sh`; the installer verifies pinned upstream source checksums. Separate Joy-Cons are not combined into a two-stick controller by this service. Third-party controllers work if Linux exposes the standard two-stick gamepad input capabilities.

Driver source: Linux v7.0 `drivers/hid/hid-nintendo.c` and `hid-ids.h` from https://github.com/torvalds/linux/tree/v7.0/drivers/hid . Compile against the exact running kernel headers; a kernel upgrade requires rebuilding the module. Do not force-load a mismatched module.

## Tests

```sh
python3 tools/test-gamepad.py
python3 tools/test-web-control.py
node tools/test-web-events.js
```

Unit tests cover normalization, deadzone, device filtering, deliberate enable, button edge detection, stop priority, connection loss, input-source handoff, and retained sensor protection. Real Bluetooth pairing and physical button mapping still require a controller in pairing mode.
