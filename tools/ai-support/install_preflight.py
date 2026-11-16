"""Read App state across CLI versions before changing shared board services."""
import json
from urllib.parse import quote
from urllib.request import urlopen


def require_stopped(apps, fetch=None):
    if fetch is None:
        def fetch(app_id):
            url = 'http://127.0.0.1:8800/v1/apps/' + quote(app_id, safe='')
            with urlopen(url, timeout=5) as response:
                return json.load(response)
    for app in apps:
        # Older CLI catalogs omit status; the detail endpoint includes it.
        status = app.get('status')
        if status is None:
            detail = fetch(app['id'])
            if detail.get('id') != app['id']:
                raise ValueError('App detail identity mismatch')
            status = detail.get('status')
        if status not in ('stopped', 'uninitialized'):
            raise ValueError('Stop all Apps before installing support: USB preparation restarts the shared router. Use Wi-Fi/SSH.')
