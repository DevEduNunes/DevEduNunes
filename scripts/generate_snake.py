"""Generates a growing-snake GIF from a GitHub user's contribution graph.

Unlike Platane/snk (fixed-length snake), this snake grows by one segment
each time it eats a contribution square.
"""

import os
import sys
from collections import deque

import requests
from PIL import Image, ImageDraw

CELL = 16
GAP = 3
MARGIN = 20
FRAME_DURATION_MS = 90
LOOP_PAUSE_FRAMES = 12

PALETTES = {
    "light": {
        "bg": (255, 255, 255),
        "levels": [
            (235, 237, 240),
            (155, 233, 168),
            (64, 196, 99),
            (48, 161, 78),
            (33, 110, 57),
        ],
        "snake_head": (255, 87, 34),
        "snake_body": (33, 33, 33),
    },
    "dark": {
        "bg": (13, 17, 23),
        "levels": [
            (22, 27, 34),
            (14, 68, 41),
            (0, 109, 50),
            (38, 166, 65),
            (57, 211, 83),
        ],
        "snake_head": (255, 138, 101),
        "snake_body": (230, 230, 230),
    },
}


def fetch_contribution_calendar(login: str, token: str):
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                weekday
                contributionCount
              }
            }
          }
        }
      }
    }
    """
    resp = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": {"login": login}},
        headers={"Authorization": f"bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]


def level_for_count(count: int) -> int:
    if count == 0:
        return 0
    if count < 3:
        return 1
    if count < 6:
        return 2
    if count < 10:
        return 3
    return 4


def build_grid(weeks):
    counts = {}
    n_weeks = len(weeks)
    for w, week in enumerate(weeks):
        for day in week["contributionDays"]:
            counts[(w, day["weekday"])] = day["contributionCount"]
    return counts, n_weeks


def build_path(n_weeks: int):
    """Boustrophedon path: down column 0, up column 1, down column 2, ..."""
    path = []
    for w in range(n_weeks):
        rows = range(7) if w % 2 == 0 else range(6, -1, -1)
        for d in rows:
            path.append((w, d))
    return path


def simulate(path, counts):
    """Returns list of frames; each frame is (snake_segments, eaten_set)."""
    frames = []
    eaten = set()
    snake = deque([path[0]])
    frames.append((list(snake), set(eaten)))

    for cell in path[1:]:
        snake.appendleft(cell)
        grew = counts.get(cell, 0) > 0 and cell not in eaten
        if grew:
            eaten.add(cell)
        else:
            snake.pop()
        frames.append((list(snake), set(eaten)))

    return frames


def render_gif(frames, counts, n_weeks, palette_name: str, out_path: str):
    palette = PALETTES[palette_name]
    width = MARGIN * 2 + n_weeks * (CELL + GAP)
    height = MARGIN * 2 + 7 * (CELL + GAP)

    images = []
    snake_set_prev = None
    for snake, eaten in frames:
        img = Image.new("RGB", (width, height), palette["bg"])
        draw = ImageDraw.Draw(img)

        for (w, d), count in counts.items():
            x = MARGIN + w * (CELL + GAP)
            y = MARGIN + d * (CELL + GAP)
            level = 0 if (w, d) in eaten else level_for_count(count)
            draw.rounded_rectangle(
                [x, y, x + CELL, y + CELL], radius=3, fill=palette["levels"][level]
            )

        for i, (w, d) in enumerate(snake):
            x = MARGIN + w * (CELL + GAP)
            y = MARGIN + d * (CELL + GAP)
            color = palette["snake_head"] if i == 0 else palette["snake_body"]
            draw.rounded_rectangle([x, y, x + CELL, y + CELL], radius=4, fill=color)

        images.append(img)

    # pause on the final, fully-grown frame before looping
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
    login = os.environ["GITHUB_REPOSITORY_OWNER"]
    token = os.environ["GH_TOKEN"]
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "dist"
    os.makedirs(out_dir, exist_ok=True)

    weeks = fetch_contribution_calendar(login, token)
    counts, n_weeks = build_grid(weeks)
    path = build_path(n_weeks)
    frames = simulate(path, counts)

    render_gif(frames, counts, n_weeks, "light", os.path.join(out_dir, "snake-grow.gif"))
    render_gif(frames, counts, n_weeks, "dark", os.path.join(out_dir, "snake-grow-dark.gif"))


if __name__ == "__main__":
    main()
