import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('DUCK_DATA_DIR', ROOT / 'data')).resolve()
PRIVATE = DATA / 'voice-state'
VENDOR = ROOT / 'python/vendor/luwu_os'


def private_directory():
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    PRIVATE.chmod(0o700)
    return PRIVATE
