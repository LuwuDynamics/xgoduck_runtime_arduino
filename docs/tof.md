# Optional Qwiic ToF: SparkFun VL53L5CX

Connect the SparkFun VL53L5CX to the **UNO Q board's Qwiic connector** (3.3 V).
This is STM32 `Wire1` / I2C4, separate from the carrier IMU's `Wire` bus.
The fixed seven-bit sensor address is `0x29` (`0x52` in eight-bit notation).
Connect it before starting the App; the current driver probes once at MCU boot.

The STM32 runs SparkFun's pinned 1.0.3 driver in a separate priority-8 task,
below the priority-4 motor/IMU loop and priority-6 feedback task. It initializes
the sensor, selects **8 × 8 zones at 5 frames/s**, and keeps the latest snapshot.
Initialization can take several seconds. This optional sensor never enables
motors or changes motion commands. It currently provides measurements only;
obstacle avoidance and voice/MCP tools have not been connected to it.

On the UNO Q, run the following inside the existing App container:

```sh
docker ps --format '{{.Names}}'
docker exec <app-container> python /app/tools/read-tof.py --seconds 10
docker exec <app-container> python /app/tools/read-tof.py --seconds 30 --json
```

The read-only `duck_tof` Bridge RPC returns a 292-byte versioned snapshot.
It copies memory and does no I2C operations. The tool displays millimetres,
each zone's raw status and target count, frame sequence, age and read time.
As in SparkFun's example, each row is reversed for display; the module's
physical mounting still determines which direction is up/left.

Only fresh frames (at most 1 second old), with a detected target, positive
distance and target status **5 or 9**, contribute to the valid grid and distance
summary. Other zones display `-`; their raw values remain in JSON for diagnosis.
The center value is the median of the valid four middle zones.
These checks prevent missing/old data from being reported as a close obstacle.

States are `starting`, `absent`, `initializing`, `ranging`, `error` and `offline`.
The tool exits unsuccessfully if no valid frame arrives, the module is absent,
or initialization/communication fails. This is the **read tool's** exit status;
the App, voice/media services and motor/IMU tasks continue. A module absent at
boot skips initialization. A module disconnected while ranging stops only its
own low-priority task and marks the old frame invalid (`offline`). Reconnect
with power off and restart the App to re-probe. Sensor failure does not alter
the robot's torque state.

Version 0.2.27 also adds an App-daemon startup cache check. An interrupted
compile can leave an empty `.o`/`.d` pair that the builder reuses, causing
`undefined reference to vl53l5cx_init` before Python starts. The check removes
only invalid ARM object/dependency files, keeping valid cached objects, source,
firmware images, Python dependencies and saved settings. The default App then
rebuilds missing objects through the normal App Lab path. This is independent
of whether the optional sensor is physically connected.

After updating, stop the App and rerun `bash tools/install-ai-support.sh` once
over Wi-Fi/SSH to install the new startup hook. Existing users who update only
the App ZIP retain ToF's optional behavior, but do not yet have the host-side
cache check. The App daemon runs the check as the `arduino` user.

The vendor sources and original licenses ship in `sketch/src/vl53l5cx`.
No extra Arduino library download is required. `provenance/vl53l5cx.json`
records the upstream commit and source hashes. Local transport fixes bound
ULD polling timeouts, reject partial I2C reads, and explicitly address read
chunks. The Arduino core's own I2C driver determines transaction timeouts.

Official references:

- [UNO Q Qwiic / Wire1](https://docs.arduino.cc/tutorials/uno-q/user-manual/)
- [SparkFun VL53L5CX library and examples](https://github.com/sparkfun/SparkFun_VL53L5CX_Arduino_Library)

## Device check, 2026-10-22

On the connected UNO Q, device/revision IDs were `0xF0 / 0x02`. A two-minute
capture returned 612 consecutive frames (about 5.10 Hz), with no skipped frame
sequences, stale frames or sensor errors. The central four-zone median varied
from 30 to 36 mm in the scene present during the capture; a typical frame had
47 valid zones. This confirms live ranging/transport, not calibrated distance
accuracy. A controlled near/far target comparison remains to be performed.

While ToF was active, the existing MCU feedback stayed near 100 Hz, IMU data
remained valid, and the MCU reported no control overruns or report drops.
Motors were not enabled for this check. The deployed firmware uses 209724 bytes
of program storage and 71664 bytes of reported global/static RAM.
The original web/voice code and saved settings were retained.

Regression: 311 cases across Mac (274 passed, 37 target-platform skips) and
Linux ARM64 (308 passed, three host C++-compiler skips). The skip sets do not
overlap. The I2C short-read/NACK test compiles and executes the actual driver
transport. The STM32 image compiled on the UNO Q and passed ELF relocation
verification before flashing; the read tool was then checked against the
installed App's actual `duck_tof` RPC.

## 中文说明

传感器插 **UNO Q 主板的 Qwiic 口**，使用 STM32 的 `Wire1`，地址 `0x29`。
驱动开机探测一次，以独立低优先级任务读取 **8×8、5 Hz** 的距离阵列。
上面的命令只读数据；无须额外安装 Arduino 库。接了传感器时，初始化需要数秒。
不插 ToF 正常启动；运行中拔出仅把 ToF 标为离线，不停止其他功能。

输出单位为毫米，`-` 表示无有效目标或数据过期。中央距离取中间四格
有效值的中位数；保留全部原始距离、质量状态和目标数，便于排查。
目前只完成测距采集，还没有加入自动避障或语音工具。
新增驱动不修改原网页、运动策略、IMU 总线或舵机使能逻辑。
