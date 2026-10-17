"""Generates a 2D Minecraft world GIF out of a GitHub user's contribution graph.

The contribution graph is the underground: the surface row is dirt and grass,
everything below is stone, and squares with commits are tinted green (the more
commits, the darker). Steve follows the real crafting chain:

    chop a tree -> wooden pickaxe -> 3 cobblestone -> stone pickaxe
    -> mine an iron vein (3 ores) -> iron pickaxe
    -> mine a diamond vein (10 ores) -> diamond armor + diamond sword
    -> a mob shows up and Steve fights it off: a creeper by day, a zombie by night

Day or night follows the clock in Brazil (America/Sao_Paulo; override with
MINECRAFT_TIME=day|night): the sky and the mob change accordingly.
Ore veins grow from random commits (the pick changes daily, override it with
MINECRAFT_SEED). Reuses the contribution fetching from generate_snake.py.
"""

import math
import os
import random
import sys
import zlib
from collections import deque
from datetime import date, datetime
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from generate_snake import build_grid, fetch_contribution_calendar, level_for_count

B = 20  # block size in pixels
PAD = B  # extra column of plain ground on each side of the contribution grid
SKY = 130  # sky height above the ground line
ROWS = 7
SCALE = 2  # Steve's pixel size
FRAME_MS = 70
LOOP_PAUSE_FRAMES = 18
WALK_STEP = 32

IRON_VEIN = 3  # iron pickaxe: 3 ingots
DIAMOND_VEIN = 10  # chestplate: 8 diamonds + sword: 2 diamonds

try:
    FONT = ImageFont.load_default(size=10)
except Exception:  # older Pillow without a sized default font
    FONT = None

# --- Steve ----------------------------------------------------------------
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
SKIN = (199, 147, 108)
COLORS = {
    "H": (84, 52, 28),
    "S": SKIN,
    "E": (60, 70, 170),
    "M": (120, 78, 48),
    "T": (0, 167, 167),
}
PANTS = (62, 62, 170)
SHOES = (100, 100, 100)
ARMOR = (72, 222, 210)
ARMOR_SHADE = (40, 165, 165)
ARMOR_LIGHT = (200, 255, 250)
STICK = (130, 90, 45)

ZOMBIE = {
    "H": (48, 92, 44),
    "S": (92, 146, 68),
    "E": (20, 24, 20),
    "M": (52, 100, 48),
    "T": (0, 150, 160),
}
CREEPER = {"body": (84, 170, 70), "dark": (52, 120, 46), "face": (14, 24, 14)}
ZOMBIE_PANTS = (48, 48, 140)
ZOMBIE_SHOES = (70, 70, 76)

# --- Items ----------------------------------------------------------------
PLANK = (170, 125, 70)
COBBLE = (150, 150, 150)
IRON = (232, 232, 232)
DIAMOND = (92, 235, 225)
STICK_ITEM = (140, 100, 50)
HEADS = {"wood": (166, 124, 70), "stone": (150, 150, 155), "iron": (235, 235, 235)}
HEAD_EDGE = (60, 60, 66)

RECIPES = {
    "pickaxe": ["XXX", ".S.", ".S."],
    "chest": ["X.X", "XXX", "XXX"],
    "sword": [".X.", ".X.", ".S."],
}

# --- Terrain --------------------------------------------------------------
DIRT = (134, 96, 67)
STONE = (128, 128, 128)
CAVE = (78, 62, 50)
LEAVES = (54, 138, 56)
LOG = (102, 81, 51)
GRASS = (95, 168, 62)
ORE_COLORS = {"iron": (222, 184, 150), "diamond": (92, 235, 225)}
MOSS = {
    "light": [(155, 233, 168), (64, 196, 99), (48, 161, 78), (33, 110, 57)],
    "dark": [(14, 68, 41), (0, 109, 50), (38, 166, 65), (57, 211, 83)],
}
SKY_COLOR = {"light": (126, 192, 238), "dark": (14, 20, 44)}


def shade(color, amount):
    return tuple(max(0, min(255, int(c + amount))) for c in color)


def tone(color, dark, k=0.6):
    return tuple(int(c * k) for c in color) if dark else color


def col_x(w):
    """Horizontal center of grid column w."""
    return PAD + w * B + B // 2


def top_y(d):
    """Y of the top edge of grid row d."""
    return SKY + d * B


def layout(n_weeks):
    """Grid columns of the tree, the crafting table and the cobblestone shaft."""
    return 2, 6, 9


# --- Tiles ----------------------------------------------------------------
def noise_tile(base, rng, amp=10, px=2, edge=True):
    img = Image.new("RGB", (B, B), base)
    draw = ImageDraw.Draw(img)
    for gx in range(0, B, px):
        for gy in range(0, B, px):
            draw.rectangle([gx, gy, gx + px - 1, gy + px - 1], fill=shade(base, rng.randint(-amp, amp)))
    if edge:
        draw.line([(0, B - 1), (B - 1, B - 1)], fill=shade(base, -22))
        draw.line([(B - 1, 0), (B - 1, B - 1)], fill=shade(base, -22))
    return img


def add_grass_top(img, rng, dark):
    draw = ImageDraw.Draw(img)
    green = tone(GRASS, dark)
    for gx in range(0, B, 2):
        h = 4 + rng.choice([0, 2, 4])
        draw.rectangle([gx, 0, gx + 1, h - 1], fill=shade(green, rng.randint(-12, 12)))


def ore_tile(kind, rng, dark):
    img = noise_tile(tone(STONE, dark), rng, amp=8)
    draw = ImageDraw.Draw(img)
    colors = {
        "iron": [(222, 184, 150), (196, 150, 118)],
        "diamond": [(92, 235, 225), (170, 255, 250)],
    }[kind]
    for _ in range(8):
        sx, sy = rng.randrange(1, B - 5, 2), rng.randrange(1, B - 5, 2)
        w, h = rng.choice([(4, 4), (4, 2), (2, 4)])
        draw.rectangle([sx, sy, sx + w - 1, sy + h - 1], fill=rng.choice(colors))
    return img


def ground_tile(level, d, rng, dark):
    if level > 0:
        base = MOSS["dark" if dark else "light"][level - 1]
    elif d == 0:
        base = tone(DIRT, dark)
    else:
        base = tone(STONE, dark)
    img = noise_tile(base, rng, amp=9 if level == 0 and d > 0 else 10)
    if d == 0:
        add_grass_top(img, rng, dark)
    return img


def log_tile(rng, dark):
    base = tone(LOG, dark, 0.8)
    img = noise_tile(base, rng, amp=6, edge=False)
    draw = ImageDraw.Draw(img)
    for gx in range(2, B, 6):
        draw.line([(gx, 0), (gx, B)], fill=shade(base, -26))
    return img


# --- Sprites --------------------------------------------------------------
def draw_body(draw, cx, feet, walk, face, color_for, pants, shoes, skip_arms=False):
    """Draws the blocky body (mirrored when face < 0). Returns (x0, y_top)."""
    x0 = int(cx - len(BODY[0]) * SCALE // 2)
    y_top = int(feet - SPRITE_H)
    last = len(BODY[0]) - 1
    for r, row in enumerate(BODY):
        for c, ch in enumerate(row):
            if ch == "." or (skip_arms and r >= 8 and (c <= 1 or c >= 10)):
                continue
            col = c if face > 0 else last - c
            px, py = x0 + col * SCALE, y_top + r * SCALE
            draw.rectangle([px, py, px + SCALE - 1, py + SCALE - 1], fill=color_for(r, c, ch))

    legs_y = y_top + (BODY_H - 2) * SCALE
    for i, lx in enumerate((x0 + 2 * SCALE, x0 + 6 * SCALE)):
        lift = SCALE if (walk and (walk + i) % 2 == 0) else 0
        draw.rectangle([lx, legs_y, lx + 4 * SCALE - 1, legs_y + 4 * SCALE - 1 - lift], fill=pants)
        draw.rectangle(
            [lx, legs_y + 4 * SCALE - lift, lx + 4 * SCALE - 1, legs_y + 6 * SCALE - 1 - lift * 2],
            fill=shoes,
        )
    return x0, y_top


def steve_colors(armor):
    def color_for(r, c, ch):
        color = COLORS[ch]
        if armor:
            if ch in ("H", "T"):
                color = ARMOR
            elif ch == "S" and r in (8, 9) and (c <= 1 or c >= 10):
                color = ARMOR_SHADE
            if ch == "T" and (r, c) in ((7, 3), (7, 4), (8, 4), (8, 3)):
                color = ARMOR_LIGHT
            if ch == "H" and r == 0 and c in (3, 4, 5):
                color = ARMOR_LIGHT
        return color

    return color_for


def arch(tip, ux, uy, lat, fwd_center, fwd_end):
    """Points of a pickaxe head: an arc across the handle tip, ends bent back."""
    px, py = -uy, ux
    pts = []
    for s in (-1, -0.5, 0, 0.5, 1):
        f = fwd_center - (fwd_center - fwd_end) * s * s
        pts.append((tip[0] + ux * f + px * lat * s, tip[1] + uy * f + py * lat * s))
    return pts


def tool_angle(swing, aim, tool):
    if swing is None:
        return -25
    angles = {"side": (-60, 10), "down": (-35, 80), "up": (10, -80)}
    if tool == "sword":
        angles["side"] = (-85, 15)
    return angles[aim][swing]


def draw_tool(draw, hand, tool, swing, aim, face):
    angle = math.radians(tool_angle(swing, aim, tool))
    if face < 0:
        angle = math.pi - angle
    ux, uy = math.cos(angle), math.sin(angle)
    px, py = -uy, ux

    if tool is None:
        if swing is not None:  # bare-handed punch
            tip = (hand[0] + ux * 9, hand[1] + uy * 9)
            draw.line([hand, tip], fill=SKIN, width=5)
        return

    if tool == "sword":
        guard = (hand[0] + ux * 6, hand[1] + uy * 6)
        tip = (hand[0] + ux * 30, hand[1] + uy * 30)
        draw.line([hand, guard], fill=STICK, width=3)
        draw.line([(guard[0] - px * 5, guard[1] - py * 5), (guard[0] + px * 5, guard[1] + py * 5)],
                  fill=(90, 70, 40), width=3)
        draw.line([guard, tip], fill=(30, 140, 150), width=5)
        draw.line([guard, tip], fill=DIAMOND, width=3)
        draw.line([(guard[0] + ux * 3, guard[1] + uy * 3), (tip[0] - ux, tip[1] - uy)],
                  fill=(225, 255, 252), width=1)
        return

    tip = (hand[0] + ux * 19, hand[1] + uy * 19)
    draw.line([hand, tip], fill=STICK, width=3)
    pts = arch(tip, ux, uy, 9, 3, -5)
    draw.line(pts, fill=HEAD_EDGE, width=5, joint="curve")
    draw.line(pts, fill=HEADS[tool], width=3, joint="curve")


def draw_steve(draw, cx, feet, walk, face, swing, aim, tool, armor):
    """swing: None (idle) or 0 (raised) / 1 (strike). aim: 'side', 'down' or 'up'."""
    pants = ARMOR_SHADE if armor else PANTS
    shoes = (30, 115, 125) if armor else SHOES
    x0, y_top = draw_body(draw, cx, feet, walk, face, steve_colors(armor), pants, shoes)
    hand = (x0 + (11 if face > 0 else 1) * SCALE, y_top + 11 * SCALE)
    draw_tool(draw, hand, tool, swing, aim, face)


def draw_zombie(draw, cx, feet, walk, face, hurt):
    def tint(c):
        return tuple((a + b) // 2 for a, b in zip(c, (225, 40, 40))) if hurt else c

    x0, y_top = draw_body(
        draw, cx, feet, walk, face, lambda r, c, ch: tint(ZOMBIE[ch]),
        tint(ZOMBIE_PANTS), tint(ZOMBIE_SHOES), skip_arms=True,
    )
    y = y_top + 8 * SCALE
    if face < 0:
        draw.rectangle([x0 - 14, y, x0 + 4, y + 6], fill=tint(ZOMBIE["S"]))
        draw.rectangle([x0, y, x0 + 4, y + 6], fill=tint(ZOMBIE["T"]))
    else:
        w = len(BODY[0]) * SCALE
        draw.rectangle([x0 + w - 4, y, x0 + w + 14, y + 6], fill=tint(ZOMBIE["S"]))
        draw.rectangle([x0 + w - 4, y, x0 + w, y + 6], fill=tint(ZOMBIE["T"]))


def draw_creeper(draw, cx, feet, walk, face, hurt):
    def tint(c):
        return tuple((a + b) // 2 for a, b in zip(c, (225, 225, 225))) if hurt else c

    body, dark, eye = (tint(CREEPER[k]) for k in ("body", "dark", "face"))
    u = SCALE * 2  # one creeper "pixel"
    x0 = int(cx) - 4 * u // 2 * 1 - u  # 6u wide head centered on cx
    head_w, head_h = 6 * u, 6 * u
    leg_h, torso_h = 3 * u, 5 * u
    top = int(feet) - leg_h - torso_h - head_h
    # legs: two pairs, front and back, with a little walk bob
    for i, lx in enumerate((x0, x0 + head_w - 2 * u)):
        lift = SCALE if (walk and (walk + i) % 2 == 0) else 0
        draw.rectangle([lx, int(feet) - leg_h - lift, lx + 2 * u - 1, int(feet) - 1 - lift], fill=dark)
    # torso and head
    tx = x0 + u
    draw.rectangle([tx, top + head_h, tx + 4 * u - 1, top + head_h + torso_h - 1], fill=body)
    draw.rectangle([x0, top, x0 + head_w - 1, top + head_h - 1], fill=body)
    for k in range(3):  # leafy speckles
        draw.rectangle([x0 + (1 + 2 * k) * u - 2, top + (k % 2) * 2 * u + 2, x0 + (1 + 2 * k) * u, top + (k % 2) * 2 * u + 4], fill=dark)
    # the famous face
    draw.rectangle([x0 + u, top + u, x0 + 2 * u - 1, top + 2 * u - 1], fill=eye)
    draw.rectangle([x0 + 4 * u, top + u, x0 + 5 * u - 1, top + 2 * u - 1], fill=eye)
    draw.rectangle([x0 + 2 * u, top + 2 * u, x0 + 4 * u - 1, top + 4 * u - 1], fill=eye)
    draw.rectangle([x0 + u, top + 3 * u, x0 + 2 * u - 1, top + 5 * u - 1], fill=eye)
    draw.rectangle([x0 + 4 * u, top + 3 * u, x0 + 5 * u - 1, top + 5 * u - 1], fill=eye)


def draw_alert(draw, cx, feet):
    x, y = int(cx), int(feet - SPRITE_H - 20)
    draw.rectangle([x - 2, y, x + 2, y + 10], fill=(230, 40, 40))
    draw.rectangle([x - 2, y + 13, x + 2, y + 16], fill=(230, 40, 40))


def draw_table(draw, x, y):
    draw.rectangle([x, y, x + B - 1, y + B - 1], fill=(150, 102, 56))
    draw.rectangle([x, y, x + B - 1, y + 6], fill=(193, 142, 82))
    for gx in (x + 6, x + 13):
        draw.line([(gx, y), (gx, y + 6)], fill=(110, 70, 35))
    draw.line([(x, y + 6), (x + B - 1, y + 6)], fill=(110, 70, 35))
    draw.rectangle([x + 3, y + 10, x + 10, y + 12], fill=(175, 175, 185))  # saw
    draw.rectangle([x + 12, y + 9, x + 16, y + 11], fill=(95, 60, 30))  # hammer
    draw.rectangle([x + 14, y + 9, x + 17, y + 10], fill=(120, 120, 130))
    draw.rectangle([x, y, x + B - 1, y + B - 1], outline=(90, 58, 28))


def icon(draw, name, x, y):
    """16x16 inventory icons."""
    if name == "log":
        draw.rectangle([x + 1, y + 1, x + 14, y + 14], fill=(102, 81, 51), outline=(70, 52, 30))
        draw.rectangle([x + 4, y + 4, x + 11, y + 11], fill=(160, 126, 78))
        draw.rectangle([x + 6, y + 6, x + 9, y + 9], fill=(120, 92, 55))
    elif name == "cobble":
        draw.rectangle([x + 1, y + 1, x + 14, y + 14], fill=(140, 140, 140), outline=(70, 70, 74))
        for dx, dy in ((3, 3), (9, 4), (5, 9), (10, 10), (3, 12)):
            draw.rectangle([x + dx, y + dy, x + dx + 2, y + dy + 1], fill=(100, 100, 104))
    elif name in ("wood_pick", "stone_pick", "iron_pick"):
        head = HEADS[name.split("_")[0]]
        draw.line([(x + 3, y + 14), (x + 11, y + 6)], fill=STICK, width=2)
        pts = arch((x + 11, y + 6), 0.707, -0.707, 6, 2, -3)
        draw.line(pts, fill=HEAD_EDGE, width=4, joint="curve")
        draw.line(pts, fill=head, width=2, joint="curve")
    elif name == "sword":
        draw.line([(x + 2, y + 14), (x + 5, y + 11)], fill=STICK, width=2)
        draw.line([(x + 3, y + 9), (x + 7, y + 13)], fill=(90, 70, 40), width=2)
        draw.line([(x + 5, y + 11), (x + 14, y + 2)], fill=(30, 140, 150), width=4)
        draw.line([(x + 5, y + 11), (x + 14, y + 2)], fill=DIAMOND, width=2)
    elif name == "iron":
        draw.rectangle([x + 2, y + 5, x + 13, y + 11], fill=IRON, outline=(120, 120, 125))
        draw.line([(x + 3, y + 6), (x + 12, y + 6)], fill=(255, 255, 255))
    elif name == "diamond":
        poly = [(x + 8, y + 1), (x + 14, y + 7), (x + 8, y + 14), (x + 2, y + 7)]
        draw.polygon(poly, fill=DIAMOND, outline=(30, 140, 150))
        draw.polygon([(x + 8, y + 3), (x + 11, y + 7), (x + 8, y + 7)], fill=(200, 255, 252))
    elif name == "armor":
        draw.rectangle([x + 1, y + 2, x + 5, y + 7], fill=ARMOR, outline=ARMOR_SHADE)
        draw.rectangle([x + 10, y + 2, x + 14, y + 7], fill=ARMOR, outline=ARMOR_SHADE)
        draw.rectangle([x + 3, y + 2, x + 12, y + 14], fill=ARMOR, outline=ARMOR_SHADE)
        draw.rectangle([x + 6, y + 2, x + 9, y + 4], fill=(40, 50, 60))
        draw.line([(x + 5, y + 6), (x + 5, y + 11)], fill=ARMOR_LIGHT)


def draw_count(draw, x, y, n):
    text = str(n)
    try:
        draw.text((x + 1, y + 1), text, fill=(20, 20, 20), font=FONT, anchor="rb")
        draw.text((x, y), text, fill=(255, 255, 255), font=FONT, anchor="rb")
    except Exception:  # bitmap fallback font has no anchors
        draw.text((x - 8, y - 10), text, fill=(255, 255, 255))


def draw_hotbar(draw, width, inv):
    slot = 28
    x0 = width - 12 - 5 * (slot + 2)
    for i in range(5):
        sx = x0 + i * (slot + 2)
        draw.rectangle([sx, 10, sx + slot - 1, 10 + slot - 1], fill=(58, 58, 64), outline=(18, 18, 22))
        draw.rectangle([sx + 1, 11, sx + slot - 2, 11 + slot - 2], outline=(130, 130, 140))
        if i < len(inv):
            name, count = inv[i]
            icon(draw, name, sx + 6, 16)
            if count > 1:
                draw_count(draw, sx + slot - 3, 10 + slot - 2, count)


def draw_craft(draw, cx, ground_top, craft):
    """Little crafting-grid popup above the table."""
    pw, ph = 74, 40
    x0, y0 = cx - pw // 2, ground_top - ph - 12
    draw.rectangle([x0, y0, x0 + pw, y0 + ph], fill=(70, 70, 80), outline=(20, 20, 26))
    draw.rectangle([x0 + 1, y0 + 1, x0 + pw - 1, y0 + ph - 1], outline=(150, 150, 160))
    recipe = RECIPES[craft["recipe"]]
    cells = [(r, c) for r in range(3) for c in range(3) if recipe[r][c] != "."]
    mat = craft["mat"]
    for r in range(3):
        for c in range(3):
            sx, sy = x0 + 5 + c * 10, y0 + 5 + r * 10
            draw.rectangle([sx, sy, sx + 8, sy + 8], fill=(105, 105, 118))
    for r, c in cells[: craft["filled"]]:
        sx, sy = x0 + 5 + c * 10, y0 + 5 + r * 10
        color = STICK_ITEM if recipe[r][c] == "S" else mat
        draw.rectangle([sx + 1, sy + 1, sx + 7, sy + 7], fill=color, outline=shade(color, -70))
    ax, ay = x0 + 40, y0 + ph // 2
    draw.polygon([(ax, ay - 4), (ax + 8, ay), (ax, ay + 4)], fill=(210, 210, 215))
    rx, ry = x0 + 53, y0 + 12
    draw.rectangle([rx, ry, rx + 17, ry + 17], fill=(105, 105, 118), outline=(20, 20, 26))
    if craft["result"]:
        icon(draw, craft["result"], rx + 1, ry + 1)
        draw_sparkles(draw, x0 + pw // 2, y0 + ph // 2, craft["spark"], spread=44)


def draw_sparkles(draw, cx, cy, phase, spread=26):
    sizes = [3, 6, 9, 6, 3]
    for (ox, oy), shift in (((-1, -1), 0), ((1, -1), 2), ((0, -2), 1), ((-1, 1), 3), ((1, 1), 4)):
        s = sizes[(phase + shift) % len(sizes)]
        x, y = cx + ox * spread * 0.7, cy + oy * spread * 0.55
        draw.line([(x - s, y), (x + s, y)], fill=(255, 245, 140), width=2)
        draw.line([(x, y - s), (x, y + s)], fill=(255, 245, 140), width=2)
        draw.line([(x - s // 2, y), (x + s // 2, y)], fill=(255, 255, 255), width=1)


# --- Script ---------------------------------------------------------------
NEIGHBORS = ((1, 0), (-1, 0), (0, -1), (0, 1))


class Director:
    """Records the animation as frame descriptors, tracking world state.

    Steve always stands in an open cell (the sky row above the grid is d = -1,
    mined cells become open) and walks, climbs or drops between open cells.
    """

    def __init__(self, n_weeks, table_x):
        self.n = n_weeks
        self.table_x = table_x
        self.frames = []
        self.x = -20.0
        self.feet = float(SKY)
        self.cell = (-1, -1)
        self.face = 1
        self.tool = None
        self.armor = False
        self.destroyed = set()
        self.ores = {}
        self.logs = 3
        self.leaves = True
        self.inv = []
        self.mob = None

    # --- bookkeeping ---
    def add(self, **extra):
        frame = dict(
            x=self.x, feet=self.feet, face=self.face, walk=0, swing=None, aim="side",
            tool=self.tool, armor=self.armor, crack=None, burst=None, sparkle=None,
            craft=None, jump=0, alert=False,
            mob=dict(self.mob) if self.mob else None,
            destroyed=set(self.destroyed), ores=dict(self.ores), logs=self.logs,
            leaves=self.leaves, inv=[tuple(i) for i in self.inv],
        )
        frame.update(extra)
        self.frames.append(frame)

    def give(self, name, n=1):
        for item in self.inv:
            if item[0] == name:
                item[1] += n
                return
        self.inv.append([name, n])

    def take(self, name, n):
        for item in self.inv:
            if item[0] == name:
                item[1] -= n
        self.inv = [i for i in self.inv if i[1] > 0]

    def set_pick(self, name):
        self.inv = [i for i in self.inv if not i[0].endswith("_pick")]
        self.inv.insert(0, [name, 1])
        self.tool = name.split("_")[0]

    # --- movement ---
    def cell_of_x(self, x):
        return max(-1, min(self.n, round((x - PAD - B / 2) / B)))

    def walk_to(self, target_x):
        phase = 0
        if abs(target_x - self.x) > 0.5:
            self.face = 1 if target_x > self.x else -1
        while abs(target_x - self.x) > 0.5:
            self.x += max(-WALK_STEP, min(WALK_STEP, target_x - self.x))
            phase += 1
            self.add(walk=phase)
        if self.cell[1] == -1:
            self.cell = (self.cell_of_x(self.x), -1)

    def is_open(self, cell):
        w, d = cell
        if d < -1:
            return False
        if d == -1:
            return -1 <= w <= self.n
        return 0 <= w < self.n and d < ROWS and cell in self.destroyed

    def route(self, goals):
        """Shortest walk/climb/drop path through open cells to any goal cell."""
        start = self.cell
        if start in goals:
            return []
        prev = {start: None}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for dw, dd in NEIGHBORS:
                nxt = (cur[0] + dw, cur[1] + dd)
                if nxt in prev or not self.is_open(nxt):
                    continue
                prev[nxt] = cur
                if nxt in goals:
                    path = [nxt]
                    while prev[path[-1]] != start:
                        path.append(prev[path[-1]])
                    return path[::-1]
                queue.append(nxt)
        return None

    def follow(self, path):
        i = 0
        while i < len(path):
            cw, cd = self.cell
            nw, nd = path[i]
            if nd == cd:  # merge a straight run into one walk
                step = nw - cw
                j = i
                while j + 1 < len(path) and path[j + 1][1] == cd and path[j + 1][0] - path[j][0] == step:
                    j += 1
                self.walk_to(col_x(path[j][0]))
                self.cell = path[j]
                i = j + 1
            else:
                if abs(self.x - col_x(cw)) > 0.5:
                    self.walk_to(col_x(cw))
                self.feet += (nd - cd) * B
                self.cell = path[i]
                self.add()
                i += 1

    def go_surface(self, col=None):
        goals = {(col if col is not None else w, -1) for w in range(-1, self.n + 1)}
        self.follow(self.route(goals))

    def go_table(self):
        left = self.x < self.table_x
        self.walk_to(self.table_x + (-30 if left else 30))
        self.face = 1 if left else -1
        self.add()

    # --- actions ---
    def mine(self, target):
        w, d = target
        above, left, right, below = (w, d - 1), (w - 1, d), (w + 1, d), (w, d + 1)
        side_ok = [c for c in (above, left, right) if self.is_open(c)]
        path = self.route(set(side_ok)) if side_ok else None
        if path is None and self.is_open(below):
            path = self.route({below})
        if path is None:
            raise RuntimeError(f"no way to reach {target}")
        self.follow(path)

        cw, cd = self.cell
        if (cw, cd) == above:
            aim = "down"
        elif (cw, cd) == below:
            aim = "up"
        else:
            aim = "side"
            self.face = 1 if w > cw else -1
        if abs(self.x - col_x(cw)) > 0.5:
            self.walk_to(col_x(cw))
            if aim == "side":
                self.face = 1 if w > cw else -1

        x, y = PAD + w * B, top_y(d)
        for k, swing in enumerate((0, 1, 0)):
            self.add(swing=swing, aim=aim, crack=(k + 1, x, y))
        kind = self.ores.pop(target, "stone" if d >= 1 else "dirt")
        self.destroyed.add(target)
        burst = ORE_COLORS[kind] if kind in ORE_COLORS else target
        self.add(burst=(x + B // 2, y + B // 2, burst))
        if kind in ORE_COLORS:
            self.give(kind)
        elif kind == "stone":
            self.give("cobble")
        if aim == "down":  # step down into the hole
            self.follow([target])

    def craft(self, recipe, mat, result, steps):
        for filled in steps:
            self.add(craft=dict(recipe=recipe, mat=mat, filled=filled, result=None, spark=0))
        for spark in range(5):
            self.add(craft=dict(recipe=recipe, mat=mat, filled=steps[-1], result=result, spark=spark))

    def mine_vein(self, cluster, kind):
        """Walk to the vein, turn the commits into ore, dig down and mine it all."""
        entry = min(cluster, key=lambda c: (c[1], c[0]))
        self.go_surface(entry[0])
        cx = sum(col_x(w) for w, _ in cluster) / len(cluster)
        cy = sum(top_y(d) + B / 2 for _, d in cluster) / len(cluster)
        for phase in range(5):
            if phase == 2:
                for cell in cluster:
                    self.ores[cell] = kind
            self.add(sparkle=(cx, cy, phase))
        for dd in range(entry[1]):
            self.mine((entry[0], dd))
        order, seen, queue = [entry], {entry}, deque([entry])
        while queue:
            w, dd = queue.popleft()
            for dw, ddd in NEIGHBORS:
                nb = (w + dw, dd + ddd)
                if nb in cluster and nb not in seen:
                    seen.add(nb)
                    order.append(nb)
                    queue.append(nb)
        for cell in order:
            self.mine(cell)

    def mob_fight(self, width, color):
        self.face = 1
        for _ in range(3):
            self.add(alert=True)
        self.mob = dict(x=float(width + 20), walk=0, face=-1, hurt=False)
        gap = 46
        phase = 0
        while self.mob["x"] - self.x > gap + 0.5:
            phase += 1
            self.mob["x"] = max(self.x + gap, self.mob["x"] - 30)
            self.mob["walk"] = phase
            self.add()
        for hit in range(3):
            self.mob["walk"] = 0
            self.add(swing=0)
            self.mob.update(hurt=True, x=self.mob["x"] + 12)
            self.add(swing=1)
            self.mob["hurt"] = False
            if hit < 2:  # the mob keeps coming
                self.mob.update(walk=hit + 1, x=max(self.x + gap, self.mob["x"] - 12))
                self.add()
        self.mob["hurt"] = True
        self.add(swing=1)
        zx = self.mob["x"]
        self.mob = None
        self.add(burst=(zx, SKY - SPRITE_H // 2, color))


def pick_cell(counts, rows, rng, avoid_cols, min_dist):
    """A random commit square in one of the wanted rows, away from the given columns."""
    def far(w):
        return all(abs(w - a) >= min_dist for a in avoid_cols)

    cands = [(w, d) for (w, d), c in counts.items() if c > 0 and d in rows and far(w)]
    if not cands:  # no commit fits: any square will do
        cands = [(w, d) for (w, d) in counts if d in rows and far(w)]
    return rng.choice(sorted(cands))


def grow_cluster(counts, anchor, size, rows, rng, n_weeks, avoid_cols, min_dist=2):
    """An ore vein: grows from a commit square, preferring other commit squares."""
    def ok(cell):
        w, d = cell
        return 0 <= w < n_weeks and d in rows and all(abs(w - a) >= min_dist for a in avoid_cols)

    cluster = {anchor}
    for _ in range(1000):
        if len(cluster) >= size:
            break
        base = rng.choice(sorted(cluster))
        nbs = [(base[0] + a, base[1] + b) for a, b in NEIGHBORS]
        nbs = [c for c in nbs if c not in cluster and ok(c)]
        if nbs:
            cluster.add(rng.choices(nbs, [3 if counts.get(c, 0) > 0 else 1 for c in nbs])[0])
    return cluster


def build_script(counts, n_weeks, night):
    width = PAD * 2 + n_weeks * B
    tree_w, table_w, cobble_w = layout(n_weeks)
    tree_x, table_x = col_x(tree_w), col_x(table_w)
    d = Director(n_weeks, table_x)

    # veins grow from random commits; the pick changes daily (MINECRAFT_SEED fixes it)
    rng = random.Random(os.environ.get("MINECRAFT_SEED") or date.today().toordinal())
    keep_away = [tree_w, table_w, cobble_w]
    iron = grow_cluster(counts, pick_cell(counts, [1, 2, 3], rng, keep_away, 3),
                        IRON_VEIN, [1, 2, 3], rng, n_weeks, keep_away)
    diamonds = grow_cluster(counts, pick_cell(counts, [4, 5, 6], rng, keep_away, 3),
                            DIAMOND_VEIN, [4, 5, 6], rng, n_weeks, keep_away)

    # 1. chop the tree, bottom log first; the rest falls down a block each time
    d.walk_to(tree_x - 30)
    for _ in range(3):
        for swing in (0, 1, 0):
            d.add(swing=swing, aim="side", crack=(1 + swing, tree_x - B // 2, SKY - B))
        d.logs -= 1
        d.give("log")
        d.add(burst=(tree_x, SKY - B // 2, LOG))
    d.leaves = False
    d.add(burst=(tree_x, SKY - B, LEAVES))

    # 2. wooden pickaxe: 3 planks + 2 sticks
    d.go_table()
    d.craft("pickaxe", PLANK, "wood_pick", [1, 2, 3, 4, 5])
    d.take("log", 3)
    d.set_pick("wood_pick")
    d.add()

    # 3. mine 3 cobblestone (the surface row is only dirt)
    d.go_surface(cobble_w)
    for dd in range(4):
        d.mine((cobble_w, dd))
    d.go_surface()

    # 4. stone pickaxe: 3 cobblestone + 2 sticks
    d.go_table()
    d.craft("pickaxe", COBBLE, "stone_pick", [1, 2, 3, 4, 5])
    d.take("cobble", 3)
    d.set_pick("stone_pick")
    d.add()

    # 5. iron vein (3 ores) -> iron pickaxe
    d.mine_vein(iron, "iron")
    d.go_surface()
    d.go_table()
    d.craft("pickaxe", IRON, "iron_pick", [1, 2, 3, 4, 5])
    d.take("iron", IRON_VEIN)
    d.set_pick("iron_pick")
    d.add()

    # 6. diamond vein (10 ores) -> chestplate (8) and sword (2)
    d.mine_vein(diamonds, "diamond")
    d.go_surface()
    d.go_table()
    d.craft("chest", DIAMOND, "armor", [2, 4, 6, 8])
    d.take("diamond", 8)
    d.give("armor")
    d.armor = True
    d.add()
    d.craft("sword", DIAMOND, "sword", [1, 2, 3])
    d.take("diamond", 2)
    d.give("sword")
    d.tool = "sword"
    d.add()

    # 7. a mob chases Steve, who fights it off (creeper by day, zombie by night)
    d.mob_fight(width, ZOMBIE["S"] if night else CREEPER["body"])

    # 8. celebrate
    for _ in range(2):
        for j in (0, 8, 14, 18, 14, 8, 0):
            d.add(jump=j, sparkle=(d.x, d.feet - 30 - j, len(d.frames)))
    d.add()
    return d.frames


# --- Rendering ------------------------------------------------------------
class World:
    """Static layer and tile cache for one theme."""

    def __init__(self, counts, n_weeks, theme, night):
        self.night = night
        self.counts, self.n_weeks, self.theme = counts, n_weeks, theme
        self.dark = theme == "dark"
        self.width = PAD * 2 + n_weeks * B
        self.height = SKY + ROWS * B
        self.tiles = {}
        self.base = self._build_base()

    def cell_pos(self, w, d):
        return PAD + w * B, top_y(d)

    def tile(self, key, factory):
        if key not in self.tiles:
            self.tiles[key] = factory(random.Random(zlib.crc32(repr(key).encode())))
        return self.tiles[key]

    def level(self, w, d):
        return level_for_count(self.counts.get((w, d), 0))

    def ground(self, w, d):
        level = self.level(w, d)
        return self.tile(("ground", w, d, level), lambda rng: ground_tile(level, d, rng, self.dark))

    def ore(self, w, d, kind):
        return self.tile(("ore", w, d, kind), lambda rng: ore_tile(kind, rng, self.dark))

    def cave(self, w, d):
        return self.tile(("cave", w, d), lambda rng: noise_tile(tone(CAVE, self.dark, 0.45), rng, amp=5, edge=False))

    def leaf(self, i):
        return self.tile(("leaf", i), lambda rng: noise_tile(tone(LEAVES, self.dark), rng, amp=14, edge=False))

    def log(self, i):
        return self.tile(("log", i), lambda rng: log_tile(rng, self.dark))

    def block_color(self, w, d):
        level = self.level(w, d)
        if level == 0:
            return tone(DIRT if d == 0 else STONE, self.dark)
        return MOSS[self.theme][level - 1]

    def _build_base(self):
        img = Image.new("RGB", (self.width, self.height), SKY_COLOR["dark" if self.night else "light"])
        draw = ImageDraw.Draw(img)
        rng = random.Random(7)
        if self.night:
            for _ in range(70):
                sx, sy = rng.randrange(self.width), rng.randrange(SKY - 20)
                draw.point((sx, sy), fill=rng.choice([(200, 200, 230), (120, 130, 170)]))
            draw.rectangle([640, 22, 664, 46], fill=(232, 232, 208))
            draw.rectangle([648, 28, 654, 34], fill=(206, 206, 184))
        else:
            draw.rectangle([640, 20, 668, 48], fill=(255, 226, 90), outline=(255, 190, 60))
            for cx, cy in ((170, 30), (460, 18), (790, 44)):
                draw.rectangle([cx, cy + 8, cx + 76, cy + 18], fill=(255, 255, 255))
                draw.rectangle([cx + 14, cy, cx + 52, cy + 10], fill=(255, 255, 255))
        for gw in range(-1, self.n_weeks + 1):
            for gd in range(ROWS):
                if 0 <= gw < self.n_weeks:
                    tile = self.ground(gw, gd)
                else:
                    tile = self.tile(("edge", gw, gd), lambda r, gd=gd: ground_tile(0, gd, r, self.dark))
                img.paste(tile, (PAD + gw * B, top_y(gd)))
        return img


def render(frames, counts, n_weeks, theme, night, out_path):
    world = World(counts, n_weeks, theme, night)
    dark = world.dark
    rng = random.Random(3)
    tree_w, table_w, _ = layout(n_weeks)
    tree_x, table_x = col_x(tree_w), col_x(table_w)
    images = []

    for f in frames:
        img = world.base.copy()
        draw = ImageDraw.Draw(img)

        for (w, d) in f["destroyed"]:
            img.paste(world.cave(w, d), world.cell_pos(w, d))
        for (w, d), kind in f["ores"].items():
            img.paste(world.ore(w, d, kind), world.cell_pos(w, d))

        # tree: logs stack on the ground, leaves rest on top of them
        for i in range(f["logs"]):
            img.paste(world.log(i), (tree_x - B // 2, SKY - (i + 1) * B))
        if f["leaves"]:
            base_y = SKY - f["logs"] * B
            leaf_cells = [(dx, r) for r in range(2) for dx in (-1, 0, 1)] + [(0, 2)]
            for k, (dx, r) in enumerate(leaf_cells):
                img.paste(world.leaf(k), (tree_x - B // 2 + dx * B, base_y - (r + 1) * B))
        draw_table(draw, table_x - B // 2, SKY - B)

        if f["crack"] is not None:
            stage, x, y = f["crack"]
            c = (20, 20, 20) if not dark else (240, 240, 240)
            segs = [
                [(x + 10, y + 10), (x + 6, y + 5), (x + 4, y + 6)],
                [(x + 10, y + 10), (x + 15, y + 12), (x + 16, y + 17)],
                [(x + 10, y + 10), (x + 14, y + 4), (x + 17, y + 3)],
            ]
            for seg in segs[: max(1, min(3, stage))]:
                draw.line(seg, fill=c, width=1)

        if f["burst"] is not None:
            bx, by, bc = f["burst"]
            if len(bc) == 2:  # (w, d) of a grid block
                bc = world.block_color(*bc)
            for k in range(9):
                ang = (k / 9) * math.tau + rng.random() * 0.6
                dist = 6 + rng.random() * 11
                sx, sy = bx + math.cos(ang) * dist, by + math.sin(ang) * dist - 2
                s = 2 + int(rng.random() * 2)
                draw.rectangle([sx, sy, sx + s, sy + s], fill=shade(bc, rng.choice([-30, 0, 30])))

        if f["craft"] is not None:
            draw_craft(draw, table_x, SKY - B, f["craft"])

        z = f["mob"]
        if z is not None:
            (draw_zombie if night else draw_creeper)(draw, z["x"], SKY, z["walk"], z["face"], z["hurt"])
        draw_steve(draw, f["x"], f["feet"] - f["jump"], f["walk"], f["face"], f["swing"],
                   f["aim"], f["tool"], f["armor"])
        if f["alert"]:
            draw_alert(draw, f["x"], f["feet"])

        if f["sparkle"] is not None:
            draw_sparkles(draw, *f["sparkle"][:2], f["sparkle"][2])
        draw_hotbar(draw, world.width, f["inv"])
        images.append(img)

    images.extend([images[-1]] * LOOP_PAUSE_FRAMES)
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )
    print(f"wrote {out_path} ({len(images)} frames)")


def is_night():
    forced = os.environ.get("MINECRAFT_TIME")
    if forced:
        return forced.lower() == "night"
    hour = datetime.now(ZoneInfo("America/Sao_Paulo")).hour
    return not 6 <= hour < 18


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    os.makedirs(out_dir, exist_ok=True)

    if os.environ.get("MINECRAFT_DEMO"):
        rng = random.Random(1)
        n_weeks = 53
        counts = {(w, d): rng.choice([0, 0, 1, 3, 7, 12]) for w in range(n_weeks) for d in range(7)}
    else:
        weeks = fetch_contribution_calendar(
            os.environ["GITHUB_REPOSITORY_OWNER"], os.environ["GH_TOKEN"]
        )
        counts, n_weeks = build_grid(weeks)

    night = is_night()
    print("night" if night else "day")
    frames = build_script(counts, n_weeks, night)
    render(frames, counts, n_weeks, "light", night, os.path.join(out_dir, "minecraft.gif"))
    render(frames, counts, n_weeks, "dark", night, os.path.join(out_dir, "minecraft-dark.gif"))


if __name__ == "__main__":
    main()
