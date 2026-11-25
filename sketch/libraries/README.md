# UNO Q reliable transport

The sketch profile uses these local libraries with Arduino CLI 1.3.0 or later. Both come from the [Arduino Router testing.rc installer](https://github.com/cmaglie/arduino-router/releases/download/testing.rc/installer.run), SHA-256 `e164fea4acd347b09544a628be45b9c99915d751ab47133d6a33df720c1dfe02`. Each directory includes the upstream license.

- **Arduino_RouterBridge 0.4.4** includes the Duck changes tested at 2 Mbaud. It restores the hardware FIFO at startup and after baud negotiation, retries the initial Router reset, and queues telemetry packets only when the whole notification fits.
- **Arduino_RPClite 0.4.0** is the source shipped in that installer. The upstream installer uses a cache directory named for 0.3.1. The version in its library metadata is 0.4.0.

`tools/prepare-bridge.py` verifies both source trees. The Linux board also needs the matching reliable Router.
