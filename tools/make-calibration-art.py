#!/usr/bin/env python3
"""Render the "hold it like this" picture of every servo ID (assets/cal-<ID>-pose.svg) from the XGO Duck URDF.

The calibration page shows these next to its "Set center" buttons. The robot is drawn from CAD meshes at the
URDF zero pose (every link vertical or horizontal). Check the pictured pose against the assembly guide and hardware;
this is a reference drawing, not a measurement of an assembled Duck. ID 34 is a closed-mouth reference only, because
the URDF has no moving jaw joint. Each SVG holds one WebP render plus vector overlays: the servo to move in amber with a ring on
its axis and an ID badge, dashed teal lines for the straight position, and, for the joints that can be seen being turned
away from zero, a translucent red copy of the part in a wrong position with a red arrow back to the dashed lines.

Which servo sits where on the real Duck is shown by the official photos instead (tools/make-calibration-photos.py).

    pip install numpy pillow trimesh
    python3 tools/make-calibration-art.py                      # rewrite assets/cal-*-pose.svg
    python3 tools/make-calibration-art.py --out /tmp/art       # write them elsewhere, plus a contact sheet
    python3 tools/make-calibration-art.py --model DIR          # DIR holds urdf/XGODUCK.urdf and assets/*.stl

The model lives in the training repository (xgoduck_rl/src/mjlab_microduck/robot/xgoduck); the images are generated
once and committed, so the runtime never needs the meshes or these libraries.
"""
import argparse
import base64
import copy
import io
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parents[1] / 'assets'
DEFAULT_MODEL = Path(__file__).resolve().parents[2] / 'xgoduck_rl/src/mjlab_microduck/robot/xgoduck'
VW, VH = 340, 330            # SVG viewBox
PX = 2                       # raster pixels per viewBox unit
SS = 3                       # supersampling for anti-aliasing
BG = (238, 242, 246)
GRAY, AMBER, GHOST, OUTLINE = (214, 220, 228), (255, 176, 32), (226, 82, 59), (96, 108, 124)
MOUTH_TIP = (0.0, -0.01234994, -0.07399628)   # in the head_roll link frame, from the simulation model

# servo ID -> URDF child link of its joint (ID 34, the mouth, is not a URDF joint)
LINK = {10: 'left_hip_yaw', 11: 'left_hip_roll', 12: 'left_hip_pitch', 13: 'left_knee', 14: 'left_ankle',
        20: 'right_hip_yaw', 21: 'right_hip_roll', 22: 'right_hip_pitch', 23: 'right_knee', 24: 'right_ankle',
        30: 'neck_pitch', 31: 'head_pitch', 32: 'head_yaw', 33: 'head_roll'}
HEAD_PARTS = ('head_roll', 'head_yaw', 'head_pitch', 'neck_pitch')
LEGS = {'left': ('left_hip_yaw', 'left_hip_roll', 'left_hip_pitch', 'left_knee', 'left_ankle'),
        'right': ('right_hip_yaw', 'right_hip_roll', 'right_hip_pitch', 'right_knee', 'right_ankle')}


# ---------------------------------------------------------------------------------------------- kinematics
def rpy_matrix(r, p, y):
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    return (np.array([[cy, -sy, 0], [sy, cy, 0], [0, 0, 1]]) @ np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]]) @
            np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]]))


def pose_matrix(xyz, rpy):
    t = np.eye(4)
    t[:3, :3], t[:3, 3] = rpy_matrix(*rpy), xyz
    return t


def rodrigues(axis, degrees):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    a = math.radians(degrees)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(a) * k + (1 - math.cos(a)) * k @ k


class Robot:
    def __init__(self, model):
        import trimesh
        root = ET.parse(model / 'urdf/XGODUCK.urdf').getroot()
        self.mesh = {link.get('name'): trimesh.load(model / 'assets' / (link.get('name') + '.stl'), force='mesh')
                     for link in root.findall('link') if link.find('visual/geometry/mesh') is not None}
        self.joints = [dict(child=j.find('child').get('link'), parent=j.find('parent').get('link'),
                            xyz=[float(v) for v in j.find('origin').get('xyz').split()],
                            rpy=[float(v) for v in j.find('origin').get('rpy').split()]) for j in root.findall('joint')]

    def fk(self, q=None):
        q = q or {}
        world = {'trunk_base': np.eye(4)}
        for j in self.joints:
            c, s = math.cos(math.radians(q.get(j['child'], 0))), math.sin(math.radians(q.get(j['child'], 0)))
            rz = np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
            world[j['child']] = world[j['parent']] @ pose_matrix(j['xyz'], j['rpy']) @ rz
        return world

    def subtree(self, link):
        out, todo = {link}, [link]
        while todo:
            node = todo.pop()
            for j in self.joints:
                if j['parent'] == node and j['child'] not in out:
                    out.add(j['child'])
                    todo.append(j['child'])
        return out

    def joint_frame(self, link):
        t = self.fk()[link]
        return t[:3, 3].copy(), t[:3, :3] @ np.array([0.0, 0.0, 1.0])   # pivot, rotation axis (world, zero pose)

    def world_vertices(self, link, world):
        m = self.mesh[link]
        return (np.c_[m.vertices, np.ones(len(m.vertices))] @ world[link].T)[:, :3]


# ---------------------------------------------------------------------------------------------- cameras
class Camera:
    """Orthographic camera. az/el in degrees; az 0 looks at the face, +90 sees the Duck's left side."""

    def __init__(self, robot, az=0.0, el=0.0, mirror=False, fill=0.80):
        self.w = np.array([math.cos(math.radians(el)) * math.cos(math.radians(az)),
                           math.cos(math.radians(el)) * math.sin(math.radians(az)), math.sin(math.radians(el))])
        if abs(el) > 89:
            self.r, self.u = np.array([0.0, -1.0, 0.0]), np.array([1.0, 0.0, 0.0])
        else:
            self.r = np.cross([0, 0, 1.0], self.w)
            self.r /= np.linalg.norm(self.r)
            self.u = np.cross(self.w, self.r)
        if mirror:
            self.r = -self.r
        world = robot.fk()
        pts = np.concatenate([robot.world_vertices(name, world) for name in robot.mesh])
        x, y = pts @ self.r, pts @ self.u
        self.scale = fill * min(VW / (x.max() - x.min()), VH / (y.max() - y.min()))        # viewBox units per metre
        self.cx, self.cy = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2

    def zoomed(self, centre, factor):
        """Same view, magnified about a world point (keeps every projection consistent)."""
        other = copy.copy(self)
        other.cx, other.cy = float(np.asarray(centre) @ self.r), float(np.asarray(centre) @ self.u)
        other.scale = self.scale * factor
        return other

    def uv(self, p):
        """World point(s) -> viewBox coordinates."""
        p = np.asarray(p, float)
        return (VW / 2 + (p @ self.r - self.cx) * self.scale, VH / 2 - (p @ self.u - self.cy) * self.scale)


# ---------------------------------------------------------------------------------------------- rasteriser
def layer(robot, cam, q, links, colors, default=GRAY):
    """RGBA picture of the given links (painter's algorithm, flat shading, supersampled)."""
    world = robot.fk(q)
    tris, nrm, col = [], [], []
    for name in links:
        m = robot.mesh[name]
        v = robot.world_vertices(name, world)
        tris.append(v[m.faces])
        nrm.append(m.face_normals @ world[name][:3, :3].T)
        col.append(np.tile(np.array(colors.get(name, default), float), (len(m.faces), 1)))
    tri, nrm, col = np.concatenate(tris), np.concatenate(nrm), np.concatenate(col)
    x, y = cam.uv(tri.reshape(-1, 3))
    x, y = x.reshape(-1, 3) * PX * SS, y.reshape(-1, 3) * PX * SS
    depth = (tri @ cam.w).mean(axis=1)
    light = 0.70 * cam.w + 0.55 * cam.u - 0.40 * cam.r
    light /= np.linalg.norm(light)
    shade = 0.46 + 0.54 * np.clip(nrm @ light, 0, 1)
    facing = nrm @ cam.w
    img = Image.new('RGBA', (VW * PX * SS, VH * PX * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in np.argsort(depth):
        if facing[i] < -0.02:
            continue
        c = tuple(int(v) for v in np.clip(col[i] * shade[i], 0, 255)) + (255,)
        draw.polygon([(x[i, 0], y[i, 0]), (x[i, 1], y[i, 1]), (x[i, 2], y[i, 2])], fill=c, outline=c)
    return img


def outlined(img, color=OUTLINE, width=1.6):
    k = int(width * SS) * 2 + 1
    halo = img.getchannel('A').filter(ImageFilter.MaxFilter(k))
    base = Image.new('RGBA', img.size, color + (255,))
    base.putalpha(halo)
    base.alpha_composite(img)
    return base


def compose(layers):
    canvas = Image.new('RGBA', layers[0][0].size, BG + (255,))
    for img, alpha in layers:
        if alpha < 1:
            img = img.copy()
            img.putalpha(img.getchannel('A').point(lambda a: int(a * alpha)))
        canvas.alpha_composite(img)
    return canvas.convert('RGB').resize((VW * PX, VH * PX), Image.LANCZOS)


def webp_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, 'WEBP', quality=90, method=6)
    return 'data:image/webp;base64,' + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------------------------- vector overlays
STYLE = """
.ring{fill:#ffb02033;stroke:#e08a00;stroke-width:2.5}
.ring2{fill:none;stroke:#ffb020;stroke-width:2;stroke-dasharray:4 3}
.guide{stroke:#0d9a85;stroke-width:2.5;stroke-dasharray:8 5;fill:none}
.arrow{stroke:#e2523b;stroke-width:3.5;fill:none;stroke-linecap:round}
.lead{stroke:#1f2a37;stroke-width:1.5;fill:none}
.dot{fill:#1f2a37}
.badge{fill:#1f2a37}
.badget{fill:#fff;font:700 13px system-ui,sans-serif;text-anchor:middle}
.tag{fill:#4a5663;font:700 13px system-ui,sans-serif}
.tagc{fill:#4a5663;font:700 13px system-ui,sans-serif;text-anchor:middle}
"""
DEFS = ('<defs><marker id="ah" markerUnits="userSpaceOnUse" markerWidth="15" markerHeight="15" refX="11" refY="7.5" '
        'orient="auto"><path d="M0 0L15 7.5L0 15z" fill="#e2523b"/></marker>'
        f'<clipPath id="c"><rect width="{VW}" height="{VH}" rx="14"/></clipPath></defs>')


def ring(x, y, r=13):
    return (f'<circle class="ring2" cx="{x:.1f}" cy="{y:.1f}" r="{r + 5}"/>'
            f'<circle class="ring" cx="{x:.1f}" cy="{y:.1f}" r="{r}"/>')


def badge(sid, bx, by, tx, ty):
    return (f'<path class="lead" d="M{bx:.1f} {by:.1f}L{tx:.1f} {ty:.1f}"/><circle class="dot" cx="{tx:.1f}" cy="{ty:.1f}" r="3"/>'
            f'<rect class="badge" x="{bx - 24:.1f}" y="{by - 10:.1f}" width="48" height="20" rx="10"/>'
            f'<text class="badget" x="{bx:.1f}" y="{by + 4.5:.1f}">ID {sid}</text>')


def stack_badges(items, side):
    """items: [(sid, x, y)] -> badges in a column on one side, spread so they never overlap."""
    if not items:
        return ''
    items = sorted(items, key=lambda t: t[2])
    ys, out = [], ''
    for k, (sid, x, y) in enumerate(items):
        ys.append(max(y, (ys[-1] + 25) if ys else 22))
    top = max(0, ys[-1] - (VH - 24))
    for (sid, x, y), by in zip(items, ys):
        out += badge(sid, 40 if side == 'L' else VW - 40, by - top, x, y)
    return out


def side_for(x):
    return 'L' if x < VW / 2 else 'R'


def line3(cam, p0, p1, cls='guide'):
    (x0, x1), (y0, y1) = cam.uv([p0, p1])
    return f'<line class="{cls}" x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}"/>'


def arc3(cam, pivot, axis, start_dir, radius, ghost, steps=14):
    """Red arrow from the offset pose back to zero, drawn on a circle around the joint axis (true 3-D motion path)."""
    pts = []
    for k in range(steps + 1):
        a = ghost * (1 - k / steps)
        p = pivot + radius * (rodrigues(axis, a) @ start_dir)
        x, y = cam.uv(p)
        pts.append(f'{x:.1f} {y:.1f}')
    return f'<path class="arrow" d="M{"L".join(pts)}" marker-end="url(#ah)"/>'


# ---------------------------------------------------------------------------------------------- pictures
class Art:
    def __init__(self, model):
        self.robot = Robot(model)
        self.cams = {'front': Camera(self.robot, 0, 0), 'right': Camera(self.robot, -90, 0),
                     'left': Camera(self.robot, 90, 0, mirror=True), 'top': Camera(self.robot, 0, 90, fill=0.66)}

    def svg(self, title, base, overlay):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {VW} {VH}" '
                f'role="img"><title>{title}</title>{DEFS}<style>{STYLE}</style><g clip-path="url(#c)">'
                f'<image width="{VW}" height="{VH}" href="{webp_data_uri(base)}"/>{overlay}</g></svg>\n')

    def render(self, cam, q=None, highlight=(), ghost_of=None, hide=(), ghost_q=None, dim=()):
        links = [n for n in self.robot.mesh if n not in hide and n not in dim]
        colors = {n: AMBER for n in highlight}
        layers = []
        context = [n for n in dim if n not in hide]
        if context:
            layers.append((outlined(layer(self.robot, cam, q, context, {}), (170, 180, 192)), 0.45))
        if ghost_of:
            moving = [n for n in self.robot.subtree(ghost_of) if n not in hide]
            layers.append((outlined(layer(self.robot, cam, ghost_q, moving, {n: GHOST for n in moving}), GHOST), 0.55))
        layers.append((outlined(layer(self.robot, cam, q, links, colors)), 1.0))
        return compose(layers)

    # --- generic helpers
    def pivot(self, sid):
        if sid == 34:
            return (self.robot.fk()['head_roll'] @ np.array([*MOUTH_TIP, 1.0]))[:3], None
        p, axis = self.robot.joint_frame(LINK[sid])
        return p, axis


# camera, hidden links, ghost angle (degrees), arrow radius (mm), arrow start direction (None = towards the moving part),
# dashed target lines as world segments in mm. Lines lie in the plane that faces the camera.
POSE = {
    10: dict(cam='top', hide=HEAD_PARTS, dim=('trunk_base',) + LEGS['right'], ghost=30, radius=58, start=None,
             guides=[((-70, 0, 0), (118, 0, 0)), ((-70, 49, 0), (40, 49, 0))]),
    20: dict(cam='top', hide=HEAD_PARTS, dim=('trunk_base',) + LEGS['left'], ghost=-30, radius=58, start=None,
             guides=[((-70, 0, 0), (118, 0, 0)), ((-70, -49, 0), (40, -49, 0))]),
    11: dict(cam='front', hide=(), ghost=24, radius=64, start=None,
             guides=[((0, -72, -40.5), (0, 72, -40.5)), ((0, 48.5, -48), (0, 48.5, -134))]),
    21: dict(cam='front', hide=(), ghost=-24, radius=64, start=None,
             guides=[((0, -72, -40.5), (0, 72, -40.5)), ((0, -48.5, -48), (0, -48.5, -134))]),
    12: dict(cam='left', hide=(), ghost=-30, radius=50, start=None,
             guides=[((-46, 40, -40.5), (40, 40, -40.5)), ((-17.7, 40, -48), (-17.7, 40, -128))]),
    22: dict(cam='right', hide=(), ghost=30, radius=50, start=None,
             guides=[((-46, -40, -40.5), (40, -40, -40.5)), ((-17.7, -40, -48), (-17.7, -40, -128))]),
    13: dict(cam='left', hide=(), ghost=-32, radius=46, start=None, guides=[((-17.7, 40, -50), (-17.7, 40, -130))]),
    23: dict(cam='right', hide=(), ghost=32, radius=46, start=None, guides=[((-17.7, -40, -50), (-17.7, -40, -130))]),
    14: dict(cam='left', hide=(), ghost=28, radius=40, start=None, guides=[((-50, 50, -128), (30, 50, -128))]),
    24: dict(cam='right', hide=(), ghost=-28, radius=40, start=None, guides=[((-50, -50, -128), (30, -50, -128))]),
    30: dict(cam='right', hide=(), ghost=22, radius=64, start=None, guides=[((41, 0, -6), (41, 0, 100))]),
    31: dict(cam='right', hide=(), ghost=24, radius=70, start=None, guides=[((-30, 0, 76), (114, 0, 76))]),
    32: dict(cam='top', hide=(), dim=('trunk_base',) + LEGS['left'] + LEGS['right'], ghost=28, radius=80, start=(1, 0, 0),
             guides=[((-58, 0, 0), (122, 0, 0))]),
    33: dict(cam='front', hide=(), ghost=20, radius=68, start=(0, 0, 1),
             guides=[((0, -64, 76), (0, 64, 76)), ((0, 0, 70), (0, 0, 150))]),
    34: dict(cam='right', hide=(), ghost=0, radius=0, start=None, guides=[((38, 0, 80.9), (112, 0, 80.9))]),
}
POSE_ZOOM = {11: 1.25, 21: 1.25, 13: 1.35, 23: 1.35, 14: 1.9, 24: 1.9, 31: 1.35, 33: 1.7, 34: 1.5}


def direction_tag(art, cam_name, cam):
    """Which way is front: 左 L / 右 R under the feet in the front view, an arrow in the side and top views."""
    if cam_name == 'front':
        world = art.robot.fk()
        low = min(float((art.robot.world_vertices(n, world) @ cam.u).min()) for n in art.robot.mesh)
        y = VH / 2 - (low - cam.cy) * cam.scale + 17
        return ''.join(f'<text class="tagc" x="{cam.uv(world[link][:3, 3])[0]:.1f}" y="{y:.1f}">{text}</text>'
                       for link, text in (('left_ankle', '左 L'), ('right_ankle', '右 R')))
    if cam_name == 'top':
        return f'<text class="tagc" x="{VW / 2}" y="22">前 Front ▲</text>'
    return f'<text class="tag" x="{VW - 94}" y="{VH - 12}">前 Front ▶</text>'


def pose_svg(art, sid):
    spec = POSE[sid]
    pivot, axis = art.pivot(sid)
    cam = art.cams[spec['cam']]
    if sid in POSE_ZOOM:
        cam = cam.zoomed(pivot, POSE_ZOOM[sid])
    link = LINK.get(sid)
    ghosting = bool(link and spec['ghost'])
    base = art.render(cam, highlight=[link] if link else (), hide=spec['hide'], dim=spec.get('dim', ()),
                      ghost_of=link if ghosting else None, ghost_q={link: spec['ghost']} if ghosting else None)
    mm = 1e-3
    overlay = ''.join(line3(cam, np.array(a) * mm, np.array(b) * mm) for a, b in spec['guides'])
    if ghosting:
        if spec['start'] is not None:
            start = np.array(spec['start'], float)
        else:
            world = art.robot.fk()
            pts = np.concatenate([art.robot.world_vertices(n, world) for n in art.robot.subtree(link)])
            start = pts.mean(axis=0) - pivot
            start -= axis * (start @ axis)
        overlay += arc3(cam, pivot, axis, start / np.linalg.norm(start), spec['radius'] * mm, spec['ghost'])
    x, y = cam.uv(pivot)
    overlay += ring(x, y) + stack_badges([(sid, x, y)], side_for(x)) + direction_tag(art, spec['cam'], cam)
    return art.svg(f'Zero pose of servo ID {sid}', base, overlay)


def build(model):
    art = Art(model)
    return {f'cal-{sid}-pose.svg': pose_svg(art, sid) for sid in POSE}


def contact_sheet(files):
    cells = ''.join(f'<figure><img src="{n}" width="300"><figcaption>{n}</figcaption></figure>' for n in sorted(files))
    return ('<!doctype html><meta charset="utf-8"><style>body{background:#1d242c;color:#cbd5df;font:13px system-ui;margin:12px}'
            'main{display:grid;grid-template-columns:repeat(auto-fill,300px);gap:14px}figure{margin:0}</style>'
            f'<main>{cells}</main>')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', type=Path, default=DEFAULT_MODEL, help='folder with urdf/XGODUCK.urdf and assets/*.stl')
    parser.add_argument('--out', type=Path, help='write the pictures and a contact sheet here instead of assets/')
    args = parser.parse_args()
    if not (args.model / 'urdf/XGODUCK.urdf').is_file():
        parser.error(f'robot model not found at {args.model}; pass --model')
    files = build(args.model)
    target = args.out or ASSETS
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (target / name).write_text(text)
    if args.out:
        (target / 'contact.html').write_text(contact_sheet(files))
    print(f'wrote {len(files)} pictures to {target} ({sum(len(t) for t in files.values()) / 1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
