"""Use the existing restricted hardware worker; never run the App as root."""
import os
from app_paths import DATA


def verify_usb_ready():
    import httpx
    path = os.environ.get('DUCK_GAMEPAD_SOCKET', str(DATA / 'gamepad.sock'))
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=path), base_url='http://gamepad',
                          timeout=httpx.Timeout(25, connect=1), trust_env=False) as client:
            response = client.post('/prepare-usb', json={})
            response.raise_for_status()
        print('[startup] USB role verified.', flush=True)
    except (OSError, httpx.HTTPError) as exc:
        raise SystemExit('[startup] USB role is not ready. Stop the App and run tools/install-ai-support.sh over Wi-Fi/SSH.') from exc
