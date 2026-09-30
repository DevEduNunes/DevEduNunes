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


def neighbors(cell, n_weeks):
    w, d = cell
    for nw, nd in ((w + 1, d), (w - 1, d), (w, d + 1), (w, d - 1)):
        if 0 <= nw < n_weeks and 0 <= nd < 7:
            yield (nw, nd)


def bfs_tree(snake, n_weeks, food=frozenset()):
    """BFS from the snake's head that never crosses the snake's own body. A
    body cell only counts as blocked until the tail has moved past it, so the
    snake may follow its own tail. Returns (parent, order): parent links for
    path reconstruction and every reached cell in increasing distance. Food
    cells can be reached but never crossed, so a path grows the snake at most
    once, on its last step."""
    length = len(snake)
    body_index = {cell: i for i, cell in enumerate(snake)}
    head = snake[0]
    parent = {head: None}
    order = []
    queue = deque([(head, 0)])
    while queue:
        cur, moves = queue.popleft()
        if moves > 0:
            order.append(cur)
            if cur in food:
                continue
        for nb in neighbors(cur, n_weeks):
            if nb in parent:
                continue
            idx = body_index.get(nb)
            # after moves + 1 steps, body cell idx is still occupied if
            # moves + 1 <= length - 1 - idx
            if idx is not None and moves + 1 < length - idx:
                continue
            parent[nb] = cur
            queue.append((nb, moves + 1))
    return parent, order


def path_from_tree(parent, target):
    path = [target]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]])
    path.pop()  # drop the head
    path.reverse()
    return path


def find_path(snake, targets, n_weeks, food=frozenset()):
    """Shortest body-avoiding path (head excluded) to the nearest target, or
    None if no target is reachable."""
    parent, order = bfs_tree(snake, n_weeks, food)
    for cell in order:
        if cell in targets:
            return path_from_tree(parent, cell)
    return None


def moved(snake, path, food):
    """The snake after walking path (growing on food cells)."""
    body = deque(snake)
    for cell in path:
        body.appendleft(cell)
        if cell not in food:
            body.pop()
    return body


def is_safe(snake, n_weeks, food):
    """A snake is safe when its head can still reach its own tail, so it
    can never get walled in by its body."""
    return find_path(snake, {snake[-1]}, n_weeks, food) is not None


def safe_food_path(snake, food, n_weeks):
    """Path to the nearest food cell that leaves the snake safe, or None."""
    parent, order = bfs_tree(snake, n_weeks, food)
    for cell in order:
        if cell in food:
            path = path_from_tree(parent, cell)
            if is_safe(moved(snake, path, food), n_weeks, food - {cell}):
                return path
    return None


def reachable_area(snake, cell, n_weeks):
    """Number of free cells reachable from cell, treating the body as walls
    (the tail is about to move, so it counts as free)."""
    blocked = set(list(snake)[:-1])
    seen = {cell}
    stack = [cell]
    while stack:
        cur = stack.pop()
        for nb in neighbors(cur, n_weeks):
            if nb not in seen and nb not in blocked:
                seen.add(nb)
                stack.append(nb)
    return len(seen)


def escape_step(snake, n_weeks):
    """One safe step used when no food is reachable: move to the free
    neighbour that keeps the most room open. Returns None if boxed in."""
    blocked = set(list(snake)[:-1])
    best, best_area = None, -1
    for nb in neighbors(snake[0], n_weeks):
        if nb in blocked:
            continue
        area = reachable_area(snake, nb, n_weeks)
        if area > best_area:
            best, best_area = nb, area
    return best


MAX_ESCAPE_STEPS = 300


def simulate(counts, n_weeks, start=(0, 0)):
    """Moves the snake toward the nearest remaining food cell at each step,
    growing whenever it lands on one. The body is solid: the snake never runs
    into itself. Once all food is eaten, it heads back to the starting cell
    if a free route exists. Returns list of (segments, eaten)."""
    food = {cell for cell, c in counts.items() if c > 0}
    eaten = set()

    # start with a short head-to-tail body instead of a single dot
    snake = deque((start[0], min(start[1] + i, 6)) for i in range(INITIAL_LENGTH))
    for cell in snake:
        if cell in food:
            food.discard(cell)
            eaten.add(cell)

    frames = [(list(snake), set(eaten))]

    def advance(cell):
        snake.appendleft(cell)
        if cell in food:
            food.discard(cell)
            eaten.add(cell)
        else:
            snake.pop()
        frames.append((list(snake), set(eaten)))

    stuck_steps = 0
    while food:
        path = safe_food_path(snake, food, n_weeks)
        if path is None:
            # no food can be eaten safely right now: keep moving (chasing the
            # tail) until the body opens up
            step = escape_step(snake, n_weeks)
            stuck_steps += 1
            if step is None or stuck_steps > MAX_ESCAPE_STEPS:
                break
            advance(step)
            continue
        stuck_steps = 0
        for cell in path:
            advance(cell)

    # head back to the starting point when a free route exists
    return_path = find_path(snake, {start}, n_weeks, food) if snake[0] != start else None
    if return_path:
        for cell in return_path:
            advance(cell)

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
