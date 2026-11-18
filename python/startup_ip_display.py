"""Send the host's Wi-Fi address suffix to the UNO Q's startup display."""
import asyncio
from ipaddress import IPv4Address
import os


def send_octet(octet):
    from arduino.router_bridge import Bridge, DEFAULT_ADDRESS

    bridge = Bridge(os.environ.get('APP_SOCKET', DEFAULT_ADDRESS))
    try:
        return bridge.connect(timeout=.25) and bridge.call('duck_show_ip', octet, timeout=.25) is True
    finally:
        bridge.disconnect()


async def display_startup_ip(read_ipv4):
    # Router may come up after Wi-Fi. Keep retries outside the onboarding loop.
    for _ in range(10):
        ip = read_ipv4()
        if ip:
            try:
                octet = IPv4Address(ip).packed[-1]
                if await asyncio.to_thread(send_octet, octet):
                    print('[startup-display] Wi-Fi IP suffix accepted by STM32', flush=True)
                    return
            except ImportError:
                return
            except (OSError, ValueError, RuntimeError):
                pass
        await asyncio.sleep(2)
