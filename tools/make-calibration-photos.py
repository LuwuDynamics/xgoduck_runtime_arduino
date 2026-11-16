#!/usr/bin/env python3
"""Cut the servo-ID photos of the calibration page out of the official assembly guide picture.

The guide (https://www.xgorobot.com/duck-developer-guide/06-assembly.html, step 6.1) has one slide, "Servo ID setup",
with three photos of the real Duck, the white ID tags already on the servos:

    https://www.xgorobot.com/duck-developer-guide/assets/06-assembly-01.png      (2560 x 1440)

This script crops the photos, checks every tag (a pure white box with dark digits) and writes

    assets/cal-photo-legs.webp   both legs, IDs 10-14 and 20-24
    assets/cal-photo-head.webp   neck photo and head photo side by side, IDs 30-34

and prints each tag's centre for source verification. The page uses the original white tags in each zone photo.

    pip install numpy pillow scipy
    python3 tools/make-calibration-photos.py /path/to/06-assembly-01.png
"""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import scipy.ndimage as ndi

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
# photo rectangles inside the 2560 x 1440 slide: left, top, right, bottom
LEGS, NECK, HEAD = (96, 432, 797, 1143), (844, 432, 1759, 1143), (1806, 432, 2465, 1143)
GAP = 20


def tags(photo):
    """Centres (x, y) of the white ID tags in an RGB array."""
    white = photo.min(axis=2) >= 250
    labels, _ = ndi.label(white)
    found = []
    for index, region in enumerate(ndi.find_objects(labels), 1):
        h, w = region[0].stop - region[0].start, region[1].stop - region[1].start
        if not (38 <= w <= 110 and 30 <= h <= 80):
            continue
        mask = ndi.binary_fill_holes(labels[region] == index)
        dark = ((photo[region].max(axis=2) < 90) & mask).sum()
        if dark > 40 and mask.sum() / (w * h) > 0.8:
            found.append(((region[1].start + region[1].stop) / 2, (region[0].start + region[0].stop) / 2))
    return found


def assign(found, order):
    """Pair tags with IDs. `order` is the top-to-bottom ID sequence of the group; tags are sorted by y."""
    ordered = sorted(found, key=lambda p: p[1])
    if len(ordered) != len(order):
        raise SystemExit(f'expected {len(order)} tags, found {len(ordered)}')
    return dict(zip(order, ordered))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('slide', type=Path, help='06-assembly-01.png from the official guide')
    parser.add_argument('--out', type=Path, default=ASSETS)
    args = parser.parse_args()
    slide = Image.open(args.slide).convert('RGB')
    if slide.size != (2560, 1440):
        raise SystemExit(f'unexpected slide size {slide.size}, expected 2560x1440')
    legs, neck, head = (slide.crop(box) for box in (LEGS, NECK, HEAD))
    pos = {}
    legs_tags = tags(np.asarray(legs).astype(int))
    # the right leg (IDs 20-24) is on the left of the photo, the left leg (10-14) on the right
    pos.update(assign([p for p in legs_tags if p[0] < 350], [20, 22, 21, 23, 24]))
    pos.update(assign([p for p in legs_tags if p[0] >= 350], [10, 12, 11, 13, 14]))
    pos.update(assign(tags(np.asarray(neck).astype(int)), [31, 30]))
    head_pos = assign(tags(np.asarray(head).astype(int)), [32, 33, 34])
    offset = neck.width + GAP
    pos.update({sid: (x + offset, y) for sid, (x, y) in head_pos.items()})

    combined = Image.new('RGB', (neck.width + GAP + head.width, neck.height), (245, 247, 250))
    combined.paste(neck, (0, 0))
    combined.paste(head, (offset, 0))
    args.out.mkdir(parents=True, exist_ok=True)
    legs.save(args.out / 'cal-photo-legs.webp', 'WEBP', quality=88, method=6)
    combined.save(args.out / 'cal-photo-head.webp', 'WEBP', quality=88, method=6)
    table = {}
    for sid, (x, y) in sorted(pos.items()):
        photo = legs if sid < 30 else combined
        table[str(sid)] = dict(photo='cal-photo-legs.webp' if sid < 30 else 'cal-photo-head.webp',
                               x=round(x / photo.width, 4), y=round(y / photo.height, 4))
    print(json.dumps(dict(legs=legs.size, head=combined.size, tags=table), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
