"""Generates a GIF where a pixel-art robot boy falls from the sky and shoots
every square of a GitHub user's contribution graph.

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

FRAME_DURATION_MS = 70
LOOP_PAUSE_FRAMES = 14
SPRITE_SCALE = 4
SKY = 105  # extra height above the graph where the hero flies
FALL_FRAMES = 12
MAX_COLS_PER_FRAME = 3

# K hair, S skin, W eye white, E pupil, B briefs, R boots, Y boot top, A arm cannon
SPRITE = [
    "....K......K....",
    "...KKK....KKK...",
    "..KKKKK..KKKKK..",
    "..KKKKKKKKKKKK..",
    ".KKKKKKKKKKKKKK.",
    ".KKSSSSSSSSSSKK.",
    ".KSSWWSSSSWWSSK.",
    "..SSWESSSSWESS..",
    "..SSSSSSSSSSSS..",
    "...SSSSSSSSSS...",
    "....SSSSSSSS....",
    "..SS.SSSSSS.SS..",
    ".SSS.SSSSSS.SSA.",
    ".SS..BBBBBB..AA.",
    "......BBBBBB....",
    "......BB..BB....",
    ".....SSS..SSS...",
    ".....YYY..YYY...",
    ".....RRR..RRR...",
    "....RRRR..RRRR..",
]

SPRITE_COLORS = {
    "K": (24, 24, 32),
    "S": (255, 205, 160),
    "W": (255, 255, 255),
    "E": (24, 24, 32),
    "B": (40, 40, 60),
    "R": (214, 40, 40),
    "Y": (245, 245, 245),
    "A": (90, 200, 255),
}

BEAM_COLORS = {"light": ((0, 170, 255), (255, 255, 255)), "dark": ((90, 220, 255), (255, 255, 255))}
SPARK_COLORS = [(255, 240, 120), (255, 160, 40), (255, 90, 60)]


def draw_hero(draw, x_center, y_top, flame_phase):
    sw = len(SPRITE[0]) * SPRITE_SCALE
    x0 = x_center - sw // 2
    for r, row in enumerate(SPRITE):
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            px = x0 + c * SPRITE_SCALE
            py = y_top + r * SPRITE_SCALE
            draw.rectangle(
                [px, py, px + SPRITE_SCALE - 1, py + SPRITE_SCALE - 1],
                fill=SPRITE_COLORS[ch],
            )
    # rocket flames under the boots
    feet_y = y_top + len(SPRITE) * SPRITE_SCALE
    flame_h = 5 + 4 * flame_phase
    for fx in (x0 + 5 * SPRITE_SCALE, x0 + 11 * SPRITE_SCALE):
        draw.polygon(
            [(fx, feet_y), (fx + 3 * SPRITE_SCALE, feet_y), (fx + 1.5 * SPRITE_SCALE, feet_y + flame_h)],
            fill=(255, 150, 30),
        )
        draw.polygon(
            [(fx + 3, feet_y), (fx + 2 * SPRITE_SCALE, feet_y), (fx + 1.5 * SPRITE_SCALE, feet_y + flame_h - 4)],
            fill=(255, 235, 120),
        )


def hand_pos(x_center, y_top):
    sw = len(SPRITE[0]) * SPRITE_SCALE
    return (x_center - sw // 2 + 14 * SPRITE_SCALE, y_top + 13 * SPRITE_SCALE)


def cell_xy(w, d):
    return (
        MARGIN + w * (CELL + GAP) + CELL // 2,
        MARGIN + SKY + d * (CELL + GAP) + CELL // 2,
    )


def build_script(counts, n_weeks):
    """Returns a list of frame descriptors:
    (hero_x, hero_y, beam_target or None, flash_cell or None, sparks_cell or None, destroyed_set)."""
    destroyed = set()
    frames = []
    hover_y = MARGIN - 10
    start_x = cell_xy(0, 0)[0]

    # fall from above the canvas, then settle with a small bounce
    for i in range(FALL_FRAMES):
        t = (i + 1) / FALL_FRAMES
        y = -100 + (hover_y + 100) * t * t
        frames.append((start_x, y, None, None, None, set(destroyed)))
    for dy in (-6, 0, -3, 0):
        frames.append((start_x, hover_y + dy, None, None, None, set(destroyed)))

    hero_x = start_x
    cols = sorted({w for (w, d), c in counts.items() if c > 0})
    for w in cols:
        target_x = cell_xy(w, 0)[0]
        step = MAX_COLS_PER_FRAME * (CELL + GAP)
        while abs(target_x - hero_x) > 1:
            hero_x += max(-step, min(step, target_x - hero_x))
            frames.append((hero_x, hover_y, None, None, None, set(destroyed)))
        for d in sorted(dd for (ww, dd), c in counts.items() if ww == w and c > 0):
            frames.append((hero_x, hover_y, (w, d), (w, d), None, set(destroyed)))
            destroyed.add((w, d))
            frames.append((hero_x, hover_y, None, None, (w, d), set(destroyed)))

    frames.append((hero_x, hover_y, None, None, None, set(destroyed)))
    return frames


def render(frames, counts, n_weeks, palette_name, out_path):
    palette = PALETTES[palette_name]
    beam_outer, beam_core = BEAM_COLORS[palette_name]
    width = MARGIN * 2 + n_weeks * (CELL + GAP)
    height = MARGIN * 2 + SKY + 7 * (CELL + GAP)
    rng = random.Random(7)

    images = []
    for idx, (hx, hy, beam, flash, sparks, destroyed) in enumerate(frames):
        img = Image.new("RGB", (width, height), palette["bg"])
        draw = ImageDraw.Draw(img)

        for (w, d), count in counts.items():
            x = MARGIN + w * (CELL + GAP)
            y = MARGIN + SKY + d * (CELL + GAP)
            level = 0 if (w, d) in destroyed else level_for_count(count)
            fill = (255, 255, 255) if flash == (w, d) else palette["levels"][level]
            draw.rounded_rectangle([x, y, x + CELL, y + CELL], radius=3, fill=fill)

        if beam is not None:
            hx_h, hy_h = hand_pos(hx, hy)
            tx, ty = cell_xy(*beam)
            draw.line([(hx_h, hy_h), (tx, ty)], fill=beam_outer, width=7)
            draw.line([(hx_h, hy_h), (tx, ty)], fill=beam_core, width=3)
            draw.ellipse([tx - 9, ty - 9, tx + 9, ty + 9], fill=beam_outer)
            draw.ellipse([tx - 5, ty - 5, tx + 5, ty + 5], fill=beam_core)

        if sparks is not None:
            cx, cy = cell_xy(*sparks)
            for k in range(8):
                ang = (k / 8) * math.tau + rng.random() * 0.5
                dist = 7 + rng.random() * 9
                sx, sy = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist
                r = 2 + rng.random() * 2
                draw.ellipse(
                    [sx - r, sy - r, sx + r, sy + r], fill=SPARK_COLORS[k % len(SPARK_COLORS)]
                )

        draw_hero(draw, int(hx), int(hy), idx % 2)
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

    if os.environ.get("HERO_DEMO"):
        rng = random.Random(1)
        n_weeks = 53
        counts = {(w, d): rng.choice([0, 0, 1, 3, 7, 12]) for w in range(n_weeks) for d in range(7)}
    else:
        weeks = fetch_contribution_calendar(
            os.environ["GITHUB_REPOSITORY_OWNER"], os.environ["GH_TOKEN"]
        )
        counts, n_weeks = build_grid(weeks)

    frames = build_script(counts, n_weeks)
    render(frames, counts, n_weeks, "light", os.path.join(out_dir, "hero-shooter.gif"))
    render(frames, counts, n_weeks, "dark", os.path.join(out_dir, "hero-shooter-dark.gif"))


if __name__ == "__main__":
    main()
