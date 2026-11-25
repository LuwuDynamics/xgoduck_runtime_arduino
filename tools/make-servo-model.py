#!/usr/bin/env python3
"""Export the calibration guide's zero-pose URDF to a compact offline joint picker.

Requires numpy, pillow, trimesh and fast-simplification on the build host only.
"""
import argparse
import json
import runpy
from pathlib import Path

import numpy as np

art = runpy.run_path(str(Path(__file__).with_name("make-calibration-art.py")))


# IDs on the physical cases in assembly guide 06, photo 01. Each row names
# the STL containing the housing; a joint's moving link often contains the NEXT servo.
HOUSINGS = {
    "trunk_base": (10, 20),
    "left_hip_yaw": (11,), "right_hip_yaw": (21,),
    "left_hip_pitch": (12, 13), "right_hip_pitch": (22, 23),
    "left_knee": (14,), "right_knee": (24,),
    "neck_pitch": (30, 31), "head_yaw": (32,), "head_roll": (33, 34),
}


def is_servo_case(piece):
    # Two case variants in the assembly, with and without the second horn.
    size = np.sort(piece.extents)
    return any(np.allclose(size, dims, atol=0.25) for dims in ([20, 30, 34], [20, 29, 34]))


def assign_cases(robot, link, pieces):
    remaining = {i: piece.bounds.mean(axis=0) for i, piece in enumerate(pieces) if is_servo_case(piece)}
    expected = HOUSINGS.get(link, ())
    if len(remaining) != len(expected):
        raise ValueError(f"{link}: expected {len(expected)} physical servo cases, found {len(remaining)}")
    assigned = {}
    for sid in expected:
        if sid == 34:
            # The mouth servo shares the head-roll STL. Its linkage has no URDF joint.
            index, = remaining
        else:
            pivot, _ = robot.joint_frame(art["LINK"][sid])
            matches = [i for i, center in remaining.items() if np.linalg.norm(center - pivot * 1000) < 18]
            if len(matches) != 1:
                raise ValueError(f"{link}: ambiguous housing for ID {sid}: {matches}")
            index, = matches
        assigned[index] = sid
        del remaining[index]
    return assigned


def surface_for(link, piece):
    if link.endswith("_ankle"):
        return "foot"
    if link == "head_roll":
        low, high = piece.bounds
        if low[0] > 90:
            return "lens"
        if low[2] < 77 and high[1] > 42:
            return "beak"
        if high[2] > 130:
            return "head-cover"
    return "shell" if link in ("trunk_base", "left_hip_pitch", "right_hip_pitch") else "frame"


def export(model):
    robot = art["Robot"](model)
    world = robot.fk()
    parts, joints = [], {}
    for link, mesh in robot.mesh.items():
        mesh = mesh.copy().apply_transform(world[link]).apply_scale(1000)
        pieces = list(mesh.split(only_watertight=False))
        cases = assign_cases(robot, link, pieces)
        for index, piece in enumerate(pieces):
            if piece.extents.max() < 3 or len(piece.faces) < 4:
                continue
            sid = cases.get(index)
            surface = "servo" if sid is not None else surface_for(link, piece)
            if sid is not None:
                center = np.rint(piece.bounds.mean(axis=0) * 10).astype(int).tolist()
                # Older cached viewers read "pivot" as their label anchor.
                joints[sid] = {"center": center, "pivot": center}
            count = len(piece.faces)
            target = min(count, max(100, count // 8))
            if target < count:
                piece = piece.simplify_quadric_decimation(face_count=target)
            parts.append({"id": sid, "surface": surface,
                          "vertices": np.rint(piece.vertices * 10).astype(int).ravel().tolist(),
                          "faces": piece.faces.ravel().tolist()})
    if set(joints) != set(art["LINK"]) | {34}:
        raise ValueError("Missing installation ID")
    return {"source": "LuwuDynamics/xgoduck_rl · XGODUCK.urdf · simplified zero pose · Apache-2.0",
            "assembly": "https://wiki.xgorobot.com/xgo/kb/xgo-duck/duck-guide-06", "parts": parts, "joints": joints}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=art["DEFAULT_MODEL"])
    parser.add_argument("--out", type=Path, default=art["ASSETS"] / "servo-model.json")
    args = parser.parse_args()
    args.out.write_text(json.dumps(export(args.model), separators=(",", ":")) + "\n")
