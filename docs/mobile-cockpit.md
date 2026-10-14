# Mobile cockpit

Open `http://<duck-host>:9527/mobile.html` in Android Chrome. The mobile view shares the existing firmware and media APIs; it does not change servo/Bridge/policy rates. It stays on local HTTP for testing, as requested. No certificates or new network services are installed.

- Start camera, then enter fullscreen and rotate to landscape. The full camera frame is visible by default; Fill crops the edges. Photo saves a JPEG. Hide controls leaves a restore button.
- Hold **Talk to Duck**, speak, and release. The phone captures at most 30 seconds of mono PCM, uploads a 16-bit WAV, then plays it through the Duck USB speaker. Chrome needs microphone permission and a secure-context test override, described below.
- Tap **Listen live** to continuously hear the Duck USB microphone on the phone. Tap **Stop listening** to stop immediately. This streams mono 24 kHz S16_LE PCM over HTTP, without a recording file or 30-second limit, and requires no phone microphone permission. The browser bounds its playback queue to catch up after a network stall.
- Backgrounding or closing the page stops live listening and cancels phone capture. A disconnected stream releases the USB microphone. Audio operations are serialized: stop listening before sending a phone voice clip or testing the speaker. This is not full-duplex audio.
- Settings include volume, speaker test, audio-file upload, and embedded Gamepad, Servo setup and Calibration pages. Opening/closing an inactive servo/calibration page does not change robot mode. Entering either setup remains explicit; their original safety guards apply.
- **Torque off** stays accessible in the camera view. Physical gamepad control and saved bindings remain in the existing app. No touchscreen driving commands are sent by the cockpit.

## Android Chrome HTTP test setup

The user must set this on their phone; the page cannot change browser security settings:

1. Open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. In **Insecure origins treated as secure**, add the exact origin, e.g. `http://192.168.31.216:9527` (no page path). Set Enabled and Relaunch.
3. Reopen the same origin and allow microphone access when requested. If using `xgo-duck.local`, it is a separate origin and needs its own entry.
4. Remove the exception after testing. It enables APIs for that origin but does not encrypt LAN traffic. If the flag is unavailable in the installed Chrome build, direct HTTP microphone capture cannot be enabled by the page; use Send an audio file or a trusted HTTPS deployment.

Reference: https://www.chromium.org/Home/chromium-security/deprecating-powerful-features-on-insecure-origins/

The Web Audio AudioWorklet capture avoids Android/browser codec differences and needs no ffmpeg conversion on the board. Live playback starts from the Listen live tap to unlock audio. Closing or hiding the page releases the microphone and screen wake lock. Fullscreen/wake lock availability depends on the browser; failure is shown without blocking the rest of the view.

Live audio uses `/api/media/audio/stream` through the existing firmware proxy. It requires the same-origin `X-Device-Test: 1` header, shares the media stream limit, and exclusively owns the USB microphone until the connection closes. Playback/recording requests while listening return busy. No servo or policy timing changes are involved.
