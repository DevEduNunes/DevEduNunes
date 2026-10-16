"""Generates a 2D Minecraft world GIF out of a GitHub user's contribution graph.

The contribution graph is the underground: every square is a block (dirt when
there are no commits, mossy green when there are, the darker the more commits).
Steve chops a tree, crafts a stone pickaxe at the crafting table, turns a
commit into iron ore and mines it, crafts an iron pickaxe, turns another commit
into diamond ore, mines it and finally crafts diamond armor.

Reuses the contribution fetching from generate_snake.py.
"""

import math
import os
import random
import sys
import zlib

from PIL import Image, ImageDraw

from generate_snake import build_grid, fetch_contribution_calendar, level_for_count

B = 20  # block size in pixels
PAD = B  # extra column of plain ground on each side of the contribution grid
SKY = 130  # sky height above the ground line
ROWS = 7
SCALE = 2  # Steve's pixel size
FRAME_MS = 70
LOOP_PAUSE_FRAMES = 18
WALK_STEP = 26
CLIMB_STEP = 2 * B

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

# --- Items ----------------------------------------------------------------
COBBLE = (150, 150, 150)
IRON = (232, 232, 232)
DIAMOND = (92, 235, 225)
STICK_ITEM = (140, 100, 50)
HEADS = {"stone": (150, 150, 155), "iron": (235, 235, 235)}
HEAD_EDGE = (70, 70, 75)

RECIPES = {
    "pickaxe": ["XXX", ".S.", ".S."],
    "chest": ["X.X", "XXX", "XXX"],
}

# --- Terrain --------------------------------------------------------------
DIRT = (134, 96, 67)
STONE = (128, 128, 128)
CAVE = (78, 62, 50)
LEAVES = (54, 138, 56)
LOG = (102, 81, 51)
GRASS = (95, 168, 62)
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
    base = tone(DIRT, dark) if level == 0 else MOSS["dark" if dark else "light"][level - 1]
    img = noise_tile(base, rng)
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
def draw_steve(draw, cx, feet, walk, swing, aim, tool, armor):
    """swing: None (idle) or 0 (raised) / 1 (down). aim: 'side' or 'down'."""
    sw = len(BODY[0]) * SCALE
    x0 = int(cx - sw // 2)
    y_top = int(feet - SPRITE_H)

    for r, row in enumerate(BODY):
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            color = COLORS[ch]
            if armor:
                if ch == "H" or ch == "T":
                    color = ARMOR
                elif ch == "S" and r in (8, 9) and (c <= 1 or c >= 10):
                    color = ARMOR_SHADE
                if ch == "T" and (r, c) in ((7, 3), (7, 4), (8, 4), (8, 3)):
                    color = ARMOR_LIGHT
                if ch == "H" and r == 0 and c in (3, 4, 5):
                    color = ARMOR_LIGHT
            px, py = x0 + c * SCALE, y_top + r * SCALE
            draw.rectangle([px, py, px + SCALE - 1, py + SCALE - 1], fill=color)

    legs_y = y_top + (BODY_H - 2) * SCALE
    pants = ARMOR_SHADE if armor else PANTS
    shoes = (30, 115, 125) if armor else SHOES
    for i, lx in enumerate((x0 + 2 * SCALE, x0 + 6 * SCALE)):
        lift = SCALE if (walk and (walk + i) % 2 == 0) else 0
        draw.rectangle([lx, legs_y, lx + 4 * SCALE - 1, legs_y + 4 * SCALE - 1 - lift], fill=pants)
        draw.rectangle(
            [lx, legs_y + 4 * SCALE - lift, lx + 4 * SCALE - 1, legs_y + 6 * SCALE - 1 - lift * 2],
            fill=shoes,
        )

    hand = (x0 + 11 * SCALE, y_top + 11 * SCALE)
    if swing is None:
        angle = -25
    elif aim == "down":
        angle = -35 if swing == 0 else 80
    else:
        angle = -60 if swing == 0 else 10
    angle = math.radians(angle)

    if tool is None:
        if swing is not None:  # bare-handed punch
            tip = (hand[0] + math.cos(angle) * 9, hand[1] + math.sin(angle) * 9)
            draw.line([hand, tip], fill=SKIN, width=5)
        return

    length = 20
    tip = (hand[0] + math.cos(angle) * length, hand[1] + math.sin(angle) * length)
    draw.line([hand, tip], fill=STICK, width=4)
    px, py = math.cos(angle + math.pi / 2), math.sin(angle + math.pi / 2)
    a = (tip[0] + px * 8, tip[1] + py * 8)
    b = (tip[0] - px * 8, tip[1] - py * 8)
    draw.line([a, b], fill=HEADS[tool], width=5)
    draw.line([a, b], fill=HEAD_EDGE, width=1)


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
    elif name in ("stone_pick", "iron_pick"):
        head = HEADS["stone" if name == "stone_pick" else "iron"]
        draw.line([(x + 3, y + 14), (x + 11, y + 6)], fill=STICK, width=2)
        draw.line([(x + 3, y + 8), (x + 6, y + 3), (x + 11, y + 2), (x + 14, y + 6)], fill=HEAD_EDGE, width=4)
        draw.line([(x + 3, y + 8), (x + 6, y + 3), (x + 11, y + 2), (x + 14, y + 6)], fill=head, width=2)
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


def draw_hotbar(draw, width, inv):
    slot = 28
    x0 = width - 12 - 5 * (slot + 2)
    for i in range(5):
        sx = x0 + i * (slot + 2)
        draw.rectangle([sx, 10, sx + slot - 1, 10 + slot - 1], fill=(58, 58, 64), outline=(18, 18, 22))
        draw.rectangle([sx + 1, 11, sx + slot - 2, 11 + slot - 2], outline=(130, 130, 140))
        if i < len(inv):
            icon(draw, inv[i], sx + 6, 16)


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
class Director:
    """Records the animation as frame descriptors, tracking world state."""

    def __init__(self):
        self.frames = []
        self.x = -20.0
        self.feet = float(SKY)
        self.tool = None
        self.armor = False
        self.destroyed = set()
        self.ores = {}
        self.logs = 3
        self.leaves = True
        self.inv = []

    def add(self, **extra):
        frame = dict(
            x=self.x, feet=self.feet, walk=0, swing=None, aim="side", tool=self.tool,
            armor=self.armor, crack=None, burst=None, sparkle=None, craft=None, jump=0,
            destroyed=set(self.destroyed), ores=dict(self.ores), logs=self.logs,
            leaves=self.leaves, inv=list(self.inv),
        )
        frame.update(extra)
        self.frames.append(frame)

    def walk_to(self, target_x):
        phase = 0
        while abs(target_x - self.x) > 0.5:
            self.x += max(-WALK_STEP, min(WALK_STEP, target_x - self.x))
            phase += 1
            self.add(walk=phase)

    def climb_out(self):
        while self.feet > SKY + 0.5:
            self.feet = max(float(SKY), self.feet - CLIMB_STEP)
            self.add()

    def craft(self, recipe, mat, result, steps):
        for filled in steps:
            self.add(craft=dict(recipe=recipe, mat=mat, filled=filled, result=None, spark=0))
        full = steps[-1]
        for spark in range(5):
            self.add(craft=dict(recipe=recipe, mat=mat, filled=full, result=result, spark=spark))


def pick_cell(counts, n_weeks, target_w, rows):
    """A commit square near column target_w in one of the wanted rows."""
    target_w = max(0, min(n_weeks - 1, target_w))
    cands = [(abs(w - target_w), w, d) for (w, d), c in counts.items() if c > 0 and d in rows]
    if cands:
        _, w, d = min(cands)
        return w, d
    return target_w, rows[0]


def build_script(counts, n_weeks):
    d = Director()
    tree_w, table_w = 3, max(6, round(n_weeks * 0.15))
    tree_x, table_x = col_x(tree_w), col_x(table_w)
    iron = pick_cell(counts, n_weeks, round(n_weeks * 0.34), [1, 2, 3])
    diamond = pick_cell(counts, n_weeks, round(n_weeks * 0.70), [4, 5, 6])

    # 1. chop the tree, bottom log first; the rest falls down a block each time
    d.walk_to(tree_x - 30)
    for log in range(3):
        for swing in (0, 1, 0, 1):
            d.add(swing=swing, aim="side", crack=(1 + swing, tree_x - B // 2, SKY - B))
        d.logs -= 1
        d.inv = ["log"]
        d.add(burst=(tree_x, SKY - B // 2, tone(LOG, False)))
    d.leaves = False
    d.add(burst=(tree_x, SKY - B, LEAVES))

    # 2. craft the stone pickaxe
    d.walk_to(table_x - 30)
    d.craft("pickaxe", COBBLE, "stone_pick", [1, 2, 3, 4, 5])
    d.tool, d.inv = "stone", ["stone_pick"]
    d.add()

    def mine(cell, kind, item):
        w, target_d = cell
        d.walk_to(col_x(w))
        cx, cy = col_x(w), top_y(target_d) + B // 2
        for phase in range(5):  # the commit turns into ore
            if phase == 2:
                d.ores[cell] = kind
            d.add(sparkle=(cx, cy, phase))
        for dd in range(target_d + 1):
            d.feet = float(top_y(dd))
            for swing in (0, 1, 0, 1):
                d.add(swing=swing, aim="down", crack=(1 + swing * 2, col_x(w) - B // 2, top_y(dd)))
            d.destroyed.add((w, dd))
            d.ores.pop((w, dd), None)
            d.add(burst=(col_x(w), top_y(dd) + B // 2, (w, dd)))
        d.feet = float(top_y(target_d + 1))
        d.inv = d.inv + [item]
        d.add()
        d.add(sparkle=(col_x(w), top_y(target_d + 1) - 10, 1))
        d.climb_out()

    # 3. commit -> iron ore, mine it, craft the iron pickaxe
    mine(iron, "iron", "iron")
    d.walk_to(table_x + 30)
    d.craft("pickaxe", IRON, "iron_pick", [1, 2, 3, 4, 5])
    d.tool, d.inv = "iron", ["iron_pick"]
    d.add()

    # 4. commit -> diamond ore, mine it, craft the armor
    mine(diamond, "diamond", "diamond")
    d.walk_to(table_x + 30)
    d.craft("chest", DIAMOND, "armor", [2, 4, 6, 8])
    d.armor, d.inv = True, ["iron_pick", "armor"]
    d.add()

    # 5. celebrate
    for _ in range(2):
        for j in (0, 8, 14, 18, 14, 8, 0):
            d.add(jump=j, sparkle=(d.x, d.feet - 30 - j, len(d.frames)))
    d.add()
    return d.frames


# --- Rendering ------------------------------------------------------------
class World:
    """Static layer and tile cache for one theme."""

    def __init__(self, counts, n_weeks, theme):
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
            return tone(DIRT, self.dark)
        return MOSS[self.theme][level - 1]

    def _build_base(self):
        img = Image.new("RGB", (self.width, self.height), SKY_COLOR[self.theme])
        draw = ImageDraw.Draw(img)
        rng = random.Random(7)
        if self.dark:
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


def render(frames, counts, n_weeks, theme, out_path):
    world = World(counts, n_weeks, theme)
    dark = world.dark
    rng = random.Random(3)
    tree_x, table_x = col_x(3), col_x(max(6, round(n_weeks * 0.15)))
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

        draw_steve(draw, f["x"], f["feet"] - f["jump"], f["walk"], f["swing"], f["aim"], f["tool"], f["armor"])

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

    frames = build_script(counts, n_weeks)
    render(frames, counts, n_weeks, "light", os.path.join(out_dir, "minecraft.gif"))
    render(frames, counts, n_weeks, "dark", os.path.join(out_dir, "minecraft-dark.gif"))


if __name__ == "__main__":
    main()
