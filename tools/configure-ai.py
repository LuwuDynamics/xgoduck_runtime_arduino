#!/usr/bin/env python3
"""Choose a cloud locally on the UNO Q; phone provisioning never changes it."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from app_paths import DATA
from voice_config import VoiceConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--region', choices=('cn', 'global'), help='cn: WeChat Mini Program; global: international App')
    choice.add_argument('--show', action='store_true', help='Show the selected region without writing')
    parser.add_argument('--language', choices=('cn', 'en'), help='Wake-word / conversation language, independent of cloud')
    parser.add_argument('--data-dir', type=Path, default=DATA, help='Persistent App data directory')
    args = parser.parse_args()
    if args.show and args.language:
        parser.error('--language requires --region')
    config = VoiceConfig(args.data_dir / 'voice_config.json')
    # Refuse to replace malformed settings and silently lose the user's choices.
    try:
        if config.path.exists():
            VoiceConfig.validate(json.loads(config.path.read_text()))
        if args.region:
            changes = dict(cloud_region=args.region, ota_url='')
            if args.language:
                changes['language'] = args.language
            config.update(changes)
        settings = config.get()
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'Cannot use {config.path}: {type(exc).__name__}. Fix the settings file first.\n')
    print('Cloud:', 'custom HTTPS endpoint' if settings['ota_url'] else settings['cloud_region'] or 'not selected')
    print('Language:', settings['language'])
    if args.region:
        print('Saved:', config.path)
        print('Restart the App after changing regions; bind using the matching client. Keep data/ intact.')


if __name__ == '__main__':
    main()
