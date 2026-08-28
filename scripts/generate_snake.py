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
INITIAL_LENGTH = 3

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
        "snake_head": (156, 39, 176),
        "snake_body": (106, 27, 154),
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
        "snake_head": (225, 160, 255),
        "snake_body": (171, 71, 188),
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


def shortest_path_to_nearest(start, targets, n_weeks):
    """BFS from start to the nearest cell in targets. Returns the path
    (including start), or None if targets is empty."""
    if not targets:
        return None
    visited = {start}
    parent = {}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        if cur in targets:
            path = [cur]
            while path[-1] != start:
                path.append(parent[path[-1]])
            path.reverse()
            return path
        w, d = cur
        for nb in ((w + 1, d), (w - 1, d), (w, d + 1), (w, d - 1)):
            nw, nd = nb
            if 0 <= nw < n_weeks and 0 <= nd < 7 and nb not in visited:
                visited.add(nb)
                parent[nb] = cur
                queue.append(nb)
    return None


def simulate(counts, n_weeks, start=(0, 0)):
    """Moves the snake toward the nearest remaining food cell at each step,
    growing whenever it lands on one. Once all food is eaten, it travels
    straight back to the starting cell. Returns list of (segments, eaten)."""
    food = {cell for cell, c in counts.items() if c > 0}
    eaten = set()

    # start with a short head-to-tail body instead of a single dot
    snake = deque((start[0], min(start[1] + i, 6)) for i in range(INITIAL_LENGTH))
    for cell in snake:
        if cell in food:
            food.discard(cell)
            eaten.add(cell)

    frames = [(list(snake), set(eaten))]

    while food:
        path = shortest_path_to_nearest(snake[0], food, n_weeks)
        for cell in path[1:]:
            snake.appendleft(cell)
            if cell in food:
                food.discard(cell)
                eaten.add(cell)
            else:
                snake.pop()
            frames.append((list(snake), set(eaten)))

    # head straight back to the starting point
    return_path = shortest_path_to_nearest(snake[0], {start}, n_weeks)
    if return_path:
        for cell in return_path[1:]:
            snake.appendleft(cell)
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

        tail_len = len(snake) - 1
        for i, (w, d) in enumerate(snake):
            dist_from_tail = tail_len - i
            if dist_from_tail == 0:
                size = max(6, round(CELL * 0.5))
            elif dist_from_tail == 1:
                size = max(8, round(CELL * 0.75))
            else:
                size = CELL
            offset = (CELL - size) / 2
            x = MARGIN + w * (CELL + GAP) + offset
            y = MARGIN + d * (CELL + GAP) + offset
            color = palette["snake_head"] if i == 0 else palette["snake_body"]
            draw.rounded_rectangle(
                [x, y, x + size, y + size], radius=max(2, size // 4), fill=color
            )

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
    frames = simulate(counts, n_weeks)

    render_gif(frames, counts, n_weeks, "light", os.path.join(out_dir, "snake-grow.gif"))
    render_gif(frames, counts, n_weeks, "dark", os.path.join(out_dir, "snake-grow-dark.gif"))


if __name__ == "__main__":
    main()
