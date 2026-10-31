
## Supply voltage monitoring

The control page displays the median, range and count of valid servo input-voltage readings. MCU feedback reads registers 0x38–0x3E (seven bytes); the final byte is interpreted in 0.1 V units. A separate 25-byte DQV1 packet carries the MCU timestamp, current reply mask and 15 raw readings over `duck_voltage` at approximately 10 Hz, preserving the DQS1 motion ABI. `/api/status.supply_voltage` exposes volts, min_volts, max_volts, per_servo (hardware motor order), valid_count, age_ms, source and available.

Zero/255 readings and missing replies are invalid. The API hides readings when voltage updates exceed one second or motion feedback exceeds 150 ms; pages clear values on request failure. The value measures the servo supply, including wiring voltage drop, not calibrated battery-terminal voltage or state of charge. No new motion commands or automatic low-voltage actions are introduced.
