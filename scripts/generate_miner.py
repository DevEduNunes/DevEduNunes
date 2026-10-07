"""Generates a GIF where a blocky pixel-art miner walks over a GitHub user's
contribution graph and digs up every square with a pickaxe.

Reuses the contribution fetching and palettes from generate_snake.py.
"""

import math
import os
import random
import sys

from PIL import Image, ImageDraw

from generate_snake import (
    CELL,
    GAP,
    MARGIN,
    PALETTES,
    build_grid,
    fetch_contribution_calendar,
    level_for_count,
)

FRAME_DURATION_MS = 80
LOOP_PAUSE_FRAMES = 14
SCALE = 2
SKY = 60  # room above the graph for the miner
WALK_COLS_PER_FRAME = 2
CLIMB_CELLS_PER_FRAME = 3

# 12x15 upper body (head, torso, arms); legs are drawn separately to animate.
# H hair, S skin, E eye, T shirt, M mouth/beard
BODY = [
    "..HHHHHHHH..",
    "..HHHHHHHH..",
    "..HSSSSSSH..",
    "..SEESSEES..",
    "..SSSSSSSS..",
    "..SSMMMMSS..",
    "..SSSSSSSS..",
    "..TTTTTTTT..",
    "SSTTTTTTTTSS",
    "SSTTTTTTTTSS",
    "SSTTTTTTTTSS",
    "SSTTTTTTTTSS",
    "SS.TTTTTT.SS",
    "...TTTTTT...",
    "............",
]
BODY_H = len(BODY)
LEG_H = 6
SPRITE_H = (BODY_H + LEG_H) * SCALE - 2 * SCALE  # body last row is empty padding
COLORS = {
    "H": (84, 52, 28),
    "S": (199, 147, 108),
    "E": (60, 70, 170),
    "M": (120, 78, 48),
    "T": (0, 167, 167),
}
PANTS = (62, 62, 170)
SHOES = (100, 100, 100)
STICK = (130, 90, 45)
PICK_HEAD = (170, 170, 175)
PICK_EDGE = (90, 90, 95)
SPARK_FALLBACK = (130, 130, 130)


def block(draw, x, y, fill, hi, lo):
    """Minecraft-ish block: flat fill with a light top-left and dark bottom-right edge."""
    draw.rectangle([x, y, x + CELL, y + CELL], fill=fill)
    draw.line([(x, y), (x + CELL, y)], fill=hi)
    draw.line([(x, y), (x, y + CELL)], fill=hi)
    draw.line([(x, y + CELL), (x + CELL, y + CELL)], fill=lo)
    draw.line([(x + CELL, y), (x + CELL, y + CELL)], fill=lo)


def shade(color, amount):
    return tuple(max(0, min(255, int(c + amount))) for c in color)


def draw_miner(draw, x_center, feet_y, walk_phase, swing):
    """swing: None (not mining) or 0 (raised) / 1 (down)."""
    sw = len(BODY[0]) * SCALE
    x0 = int(x_center - sw // 2)
    y_top = int(feet_y - SPRITE_H)

    for r, row in enumerate(BODY):
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            # right arm (cols 10-11) is hidden behind the pickaxe pose when swinging
            px, py = x0 + c * SCALE, y_top + r * SCALE
            draw.rectangle([px, py, px + SCALE - 1, py + SCALE - 1], fill=COLORS[ch])

    legs_y = y_top + (BODY_H - 2) * SCALE
    for i, lx in enumerate((x0 + 2 * SCALE, x0 + 6 * SCALE)):
        lift = SCALE if (walk_phase and (walk_phase + i) % 2 == 0) else 0
        draw.rectangle([lx, legs_y, lx + 4 * SCALE - 1, legs_y + 4 * SCALE - 1 - lift], fill=PANTS)
        draw.rectangle(
            [lx, legs_y + 4 * SCALE - lift, lx + 4 * SCALE - 1, legs_y + 6 * SCALE - 1 - lift * 2],
            fill=SHOES,
        )

    # pickaxe held in the right hand
    hand = (x0 + 11 * SCALE, y_top + 11 * SCALE)
    angle = math.radians(-65 if swing in (None, 0) else 35)
    if swing is None:
        angle = math.radians(-25)
    length = 20
    tip = (hand[0] + math.cos(angle) * length, hand[1] + math.sin(angle) * length)
    draw.line([hand, tip], fill=STICK, width=4)
    px, py = math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2)
    a = (tip[0] + px * 8, tip[1] + py * 8)
    b = (tip[0] - px * 8, tip[1] - py * 8)
    draw.line([a, b], fill=PICK_HEAD, width=5)
    draw.line([a, b], fill=PICK_EDGE, width=1)
    return tip


def cell_origin(w, d):
    return MARGIN + w * (CELL + GAP), MARGIN + SKY + d * (CELL + GAP)


def build_script(counts, n_weeks):
    """Frame descriptors: dict(x, feet, walk, swing, crack, target, particles, destroyed)."""
    destroyed = set()
    frames = []
    sky_feet = MARGIN + SKY - 2

    def add(x, feet, walk=0, swing=None, crack=0, target=None, burst=None):
        frames.append(
            dict(x=x, feet=feet, walk=walk, swing=swing, crack=crack, target=target,
                 burst=burst, destroyed=set(destroyed))
        )

    def stand_x(w):
        return cell_origin(w, 0)[0] - 14

    x, feet = -20.0, float(sky_feet)
    cols = sorted({w for (w, d), c in counts.items() if c > 0})

    for w in cols:
        target_x = stand_x(w)
        rows = sorted(d for (ww, d), c in counts.items() if ww == w and c > 0)
        first_feet = cell_origin(w, rows[0])[1]

        # climb back to the surface, walk over, then drop to the first block
        step = CLIMB_CELLS_PER_FRAME * (CELL + GAP)
        while feet > sky_feet + 1:
            feet = max(sky_feet, feet - step)
            add(x, feet)
        step = WALK_COLS_PER_FRAME * (CELL + GAP)
        phase = 0
        while abs(target_x - x) > 1:
            x += max(-step, min(step, target_x - x))
            phase += 1
            add(x, feet, walk=phase)
        step = CLIMB_CELLS_PER_FRAME * (CELL + GAP)
        while feet < first_feet - 1:
            feet = min(first_feet, feet + step)
            add(x, feet)

        for d in rows:
            feet = cell_origin(w, d)[1]
            add(x, feet, swing=0, crack=0, target=(w, d))
            add(x, feet, swing=1, crack=1, target=(w, d))
            add(x, feet, swing=0, crack=2, target=(w, d))
            add(x, feet, swing=1, crack=3, target=(w, d))
            destroyed.add((w, d))
            add(x, feet, burst=(w, d))

    add(x, feet)
    return frames


def render(frames, counts, n_weeks, palette_name, out_path):
    palette = PALETTES[palette_name]
    width = MARGIN * 2 + n_weeks * (CELL + GAP)
    height = MARGIN * 2 + SKY + 7 * (CELL + GAP)
    rng = random.Random(3)
    dark = palette_name == "dark"

    images = []
    for f in frames:
        img = Image.new("RGB", (width, height), palette["bg"])
        draw = ImageDraw.Draw(img)

        for (w, d), count in counts.items():
            x, y = cell_origin(w, d)
            level = 0 if (w, d) in f["destroyed"] else level_for_count(count)
            fill = palette["levels"][level]
            if level == 0:
                draw.rounded_rectangle([x, y, x + CELL, y + CELL], radius=3, fill=fill)
            else:
                block(draw, x, y, fill, shade(fill, 28), shade(fill, -34))

        t = f["target"]
        if t is not None and f["crack"]:
            x, y = cell_origin(*t)
            crack = (20, 20, 20) if not dark else (240, 240, 240)
            segs = [
                [(x + 8, y + 8), (x + 5, y + 4), (x + 3, y + 5)],
                [(x + 8, y + 8), (x + 12, y + 10), (x + 13, y + 14)],
                [(x + 8, y + 8), (x + 11, y + 3), (x + 14, y + 2)],
            ]
            for seg in segs[: f["crack"]]:
                draw.line(seg, fill=crack, width=1)

        if f["burst"] is not None:
            x, y = cell_origin(*f["burst"])
            base = palette["levels"][level_for_count(counts[f["burst"]])]
            cx, cy = x + CELL / 2, y + CELL / 2
            for k in range(9):
                ang = (k / 9) * math.tau + rng.random() * 0.6
                dist = 6 + rng.random() * 10
                sx, sy = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist - 2
                s = 2 + int(rng.random() * 2)
                draw.rectangle([sx, sy, sx + s, sy + s], fill=shade(base, rng.choice([-30, 0, 30])))

        draw_miner(draw, f["x"], f["feet"], f["walk"], f["swing"])
        images.append(img)

    images.extend([images[-1]] * LOOP_PAUSE_FRAMES)
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        duration=FRAME_DURATION_MS,
        loop=0,
        optimize=True,
    )
    print(f"wrote {out_path} ({len(images)} frames)")


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    os.makedirs(out_dir, exist_ok=True)

    if os.environ.get("MINER_DEMO"):
        rng = random.Random(1)
        n_weeks = 53
        counts = {(w, d): rng.choice([0, 0, 1, 3, 7, 12]) for w in range(n_weeks) for d in range(7)}
    else:
        weeks = fetch_contribution_calendar(
            os.environ["GITHUB_REPOSITORY_OWNER"], os.environ["GH_TOKEN"]
        )
        counts, n_weeks = build_grid(weeks)

    frames = build_script(counts, n_weeks)
    render(frames, counts, n_weeks, "light", os.path.join(out_dir, "miner.gif"))
    render(frames, counts, n_weeks, "dark", os.path.join(out_dir, "miner-dark.gif"))


if __name__ == "__main__":
    main()
