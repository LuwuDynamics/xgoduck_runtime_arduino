# UNO Q Linux Router

The arm64 Debian package in this directory is the original Arduino Router from the [testing.rc installer](https://github.com/cmaglie/arduino-router/releases/tag/testing.rc). Its [corresponding source and build files](https://github.com/cmaglie/arduino-router/tree/45cccf1c9a22b305d629615e7497f17412e0a952) are at commit `45cccf1c9a22b305d629615e7497f17412e0a952`.

| File | SHA-256 |
| --- | --- |
| Upstream `installer.run` | `e164fea4acd347b09544a628be45b9c99915d751ab47133d6a33df720c1dfe02` |
| Bundled `arduino-router_0.0.0-45cccf1-1_arm64.deb` | `30e5b17d81bbded62a2c8e59eca8bd45edcecd35a613f2b217a515cb481cf3b0` |
| Extracted `usr/bin/arduino-router` | `4fec6f237b3bccd8eea8e55732c725cac9567823405bfe29f377f959625424a0` |

Arduino distributes this Router under GPL-3.0-or-later. The Debian package contains the upstream copyright, license and third-party notices in `usr/share/doc/arduino-router/copyright`.

Run `sudo bash tools/router-2m.sh` with the Duck App stopped on the UNO Q. It uses the bundled binary, the board's systemd service and GPIO hooks. The MCU and Router start at 115200 baud, then negotiate up to 2 Mbaud. Repeating the installation with the same configuration leaves the running Router in place.

For a previous Duck firmware that uses fixed 2 Mbaud, stop the App, run the previous App's `tools/router-2m.sh`, then start that App. To restore the board's stock service defaults, stop the App, remove `/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf`, run `sudo systemctl daemon-reload`, then `sudo systemctl restart arduino-router`.
