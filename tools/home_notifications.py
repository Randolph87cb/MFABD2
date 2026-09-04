"""Detect reward-notification badges on fixed home-screen entry icons."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image


# Regions cover only the badge position of each home entry. Detection inside a
# region still uses only the red diamond and its white exclamation mark. No text
# or icon appearance participates in the decision.
HOME_NOTIFICATION_BADGE_REGIONS = {
    "mail": (0.836, 0.015, 0.025, 0.040),
    "gacha": (0.089, 0.873, 0.018, 0.036),
    "companion": (0.142, 0.873, 0.018, 0.036),
    "tasks": (0.359, 0.873, 0.018, 0.036),
    "achievements": (0.413, 0.873, 0.018, 0.036),
    "events": (0.467, 0.873, 0.018, 0.036),
    "pass": (0.868, 0.200, 0.020, 0.030),
    "quick_hunt": (0.914, 0.200, 0.020, 0.030),
    "promotion": (0.910, 0.640, 0.035, 0.090),
}


def _red_mask(frame: np.ndarray) -> np.ndarray:
    red = frame[:, :, 0].astype(np.int16)
    green = frame[:, :, 1].astype(np.int16)
    blue = frame[:, :, 2].astype(np.int16)
    return (
        (red > 150)
        # Gold/orange alert diamonds use a similar red channel but retain much
        # more green.  Daily reward badges are distinctly red.
        & (red > green * 1.90)
        & (red > blue * 1.20)
        & ((red - green) > 45)
    )


def _largest_component(mask: np.ndarray) -> tuple[int, tuple[int, int, int, int] | None]:
    """Return the size and bounds of the largest connected red component."""
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    largest_area = 0
    largest_bounds: tuple[int, int, int, int] | None = None

    for y, x in zip(*np.nonzero(mask)):
        if visited[y, x]:
            continue
        stack = [(int(x), int(y))]
        visited[y, x] = True
        area = 0
        x0 = x1 = int(x)
        y0 = y1 = int(y)
        while stack:
            current_x, current_y = stack.pop()
            area += 1
            x0 = min(x0, current_x)
            x1 = max(x1, current_x)
            y0 = min(y0, current_y)
            y1 = max(y1, current_y)
            for next_x, next_y in (
                (current_x - 1, current_y),
                (current_x + 1, current_y),
                (current_x, current_y - 1),
                (current_x, current_y + 1),
            ):
                if (
                    0 <= next_x < width
                    and 0 <= next_y < height
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    stack.append((next_x, next_y))
        if area > largest_area:
            largest_area = area
            largest_bounds = (x0, y0, x1, y1)
    return largest_area, largest_bounds


def _components(mask: np.ndarray) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Return every connected red component with its local bounds."""
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: list[tuple[int, tuple[int, int, int, int]]] = []
    for y, x in zip(*np.nonzero(mask)):
        if visited[y, x]:
            continue
        stack = [(int(x), int(y))]
        visited[y, x] = True
        area = 0
        x0 = x1 = int(x)
        y0 = y1 = int(y)
        while stack:
            current_x, current_y = stack.pop()
            area += 1
            x0 = min(x0, current_x)
            x1 = max(x1, current_x)
            y0 = min(y0, current_y)
            y1 = max(y1, current_y)
            for next_x, next_y in (
                (current_x - 1, current_y),
                (current_x + 1, current_y),
                (current_x, current_y - 1),
                (current_x, current_y + 1),
            ):
                if (
                    0 <= next_x < width
                    and 0 <= next_y < height
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    stack.append((next_x, next_y))
        components.append((area, (x0, y0, x1, y1)))
    return components


def find_notification_badges(
    image: Image.Image,
    region: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Find compact red notification badges in a normalized list region."""
    frame = np.asarray(image.convert("RGB"))
    height, width = frame.shape[:2]
    rx, ry, rw, rh = region
    x0, x1 = int(width * rx), int(width * (rx + rw))
    y0, y1 = int(height * ry), int(height * (ry + rh))
    mask = _red_mask(frame[y0:y1, x0:x1])
    crop_area = int(mask.size)
    minimum_area = max(8, round(crop_area * 0.002))
    maximum_area = round(crop_area * 0.08)
    badges = []
    for area, (local_x0, local_y0, local_x1, local_y1) in _components(mask):
        if not minimum_area <= area <= maximum_area:
            continue
        badges.append(
            {
                "pixels": area,
                "bounds": (x0 + local_x0, y0 + local_y0, x0 + local_x1, y0 + local_y1),
                "center": (
                    (x0 + local_x0 + x0 + local_x1) / 2 / width,
                    (y0 + local_y0 + y0 + local_y1) / 2 / height,
                ),
            }
        )
    return sorted(badges, key=lambda badge: badge["center"][1])


def find_red_exclamation_badges(
    image: Image.Image,
    region: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Find compact red diamonds containing a white vertical mark and dot."""
    frame = np.asarray(image.convert("RGB"))
    height, width = frame.shape[:2]
    rx, ry, rw, rh = region
    x0, x1 = int(width * rx), int(width * (rx + rw))
    y0, y1 = int(height * ry), int(height * (ry + rh))
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []

    red = _red_mask(crop)
    light = np.all(crop > 180, axis=2)
    shortest_side = min(width, height)
    minimum_side = max(5, round(shortest_side * 0.007))
    maximum_side = max(minimum_side + 2, round(shortest_side * 0.040))
    candidates: list[dict[str, Any]] = []

    for red_area, (red_x0, red_y0, red_x1, red_y1) in _components(red):
        red_width = red_x1 - red_x0 + 1
        red_height = red_y1 - red_y0 + 1
        shorter = min(red_width, red_height)
        longer = max(red_width, red_height)
        if not (
            minimum_side <= shorter
            and longer <= maximum_side
            and shorter / longer >= 0.72
        ):
            continue

        component = red[red_y0:red_y1 + 1, red_x0:red_x1 + 1]
        fill_ratio = red_area / (red_width * red_height)
        if not 0.32 <= fill_ratio <= 0.68:
            continue

        maximum_tip = max(4, round(shorter * 0.35))
        if (
            int(component[0].sum()) > maximum_tip
            or int(component[-1].sum()) > maximum_tip
            or int(component[:, 0].sum()) > maximum_tip
            or int(component[:, -1].sum()) > maximum_tip
        ):
            continue

        light_components = _components(
            light[red_y0:red_y1 + 1, red_x0:red_x1 + 1]
        )
        line_candidates = []
        dot_candidates = []
        for light_area, (light_x0, light_y0, light_x1, light_y1) in light_components:
            if (
                light_x0 == 0
                or light_y0 == 0
                or light_x1 == red_width - 1
                or light_y1 == red_height - 1
            ):
                continue
            light_width = light_x1 - light_x0 + 1
            light_height = light_y1 - light_y0 + 1
            center_x = (light_x0 + light_x1) / 2
            center_y = (light_y0 + light_y1) / 2
            center_x_ratio = center_x / max(1, red_width - 1)
            center_y_ratio = center_y / max(1, red_height - 1)
            if (
                light_area >= 3
                and light_width <= max(3, round(red_width * 0.20))
                and max(3, round(red_height * 0.16))
                <= light_height
                <= max(4, round(red_height * 0.45))
                and 0.35 <= center_x_ratio <= 0.65
                and 0.20 <= center_y_ratio <= 0.58
            ):
                line_candidates.append(
                    (light_area, (light_x0, light_y0, light_x1, light_y1), center_x)
                )
            if (
                1 <= light_area <= max(12, round(red_area * 0.08))
                and light_width <= max(4, round(red_width * 0.25))
                and light_height <= max(4, round(red_height * 0.25))
                and 0.35 <= center_x_ratio <= 0.65
                and 0.55 <= center_y_ratio <= 0.90
            ):
                dot_candidates.append(
                    (light_area, (light_x0, light_y0, light_x1, light_y1), center_x)
                )

        exclamation = None
        for line_area, line_bounds, line_center_x in line_candidates:
            for dot_area, dot_bounds, dot_center_x in dot_candidates:
                gap = dot_bounds[1] - line_bounds[3] - 1
                if (
                    0 <= gap <= max(5, round(red_height * 0.30))
                    and abs(line_center_x - dot_center_x)
                    <= max(2, round(red_width * 0.15))
                ):
                    exclamation = {
                        "line_pixels": line_area,
                        "dot_pixels": dot_area,
                        "line_bounds": line_bounds,
                        "dot_bounds": dot_bounds,
                    }
                    break
            if exclamation is not None:
                break
        if exclamation is None:
            continue

        candidates.append(
            {
                "pixels": red_area,
                "bounds": (x0 + red_x0, y0 + red_y0, x0 + red_x1, y0 + red_y1),
                "center": (
                    (x0 + red_x0 + x0 + red_x1) / 2 / width,
                    (y0 + red_y0 + y0 + red_y1) / 2 / height,
                ),
                "red_pixels": red_area,
                "red_fill_ratio": round(fill_ratio, 3),
                "exclamation_pixels": exclamation["line_pixels"] + exclamation["dot_pixels"],
                "exclamation": exclamation,
            }
        )

    return sorted(candidates, key=lambda item: (item["center"][1], item["center"][0]))


def detect_red_exclamation_badge(
    image: Image.Image,
    region: tuple[float, float, float, float],
) -> tuple[bool, dict[str, Any]]:
    """Confirm one red diamond with its white exclamation in a fixed small region.

    This deliberately requires the diamond-shaped red component and the light
    vertical exclamation within it.  It is intended for a known badge position,
    not for searching an arbitrary artwork-heavy part of the screen.
    """
    candidates = find_red_exclamation_badges(image, region)
    if not candidates:
        return False, {"region": region, "reason": "no_red_diamond_with_exclamation"}
    candidate = max(candidates, key=lambda item: item["red_pixels"])
    return True, {"region": region, **candidate}


def detect_notification_badge(
    image: Image.Image,
    region: tuple[float, float, float, float],
) -> tuple[bool, dict[str, Any]]:
    """Detect a compact red notification badge inside one normalized region."""
    frame = np.asarray(image.convert("RGB"))
    height, width = frame.shape[:2]
    rx, ry, rw, rh = region
    x0, x1 = int(width * rx), int(width * (rx + rw))
    y0, y1 = int(height * ry), int(height * (ry + rh))
    crop = frame[y0:y1, x0:x1]
    mask = _red_mask(crop)
    largest_area, bounds = _largest_component(mask)
    crop_area = int(mask.size)
    minimum_area = max(8, round(crop_area * 0.012))
    maximum_area = round(crop_area * 0.35)
    badge_found = minimum_area <= largest_area <= maximum_area
    return badge_found, {
        "region": region,
        "bounds": (x0, y0, x1, y1),
        "red_pixels": int(mask.sum()),
        "red_ratio": float(mask.mean()) if mask.size else 0.0,
        "largest_component_pixels": largest_area,
        "largest_component_bounds": bounds,
        "minimum_component_pixels": minimum_area,
        "maximum_component_pixels": maximum_area,
    }


def detect_home_reward_notification(
    image: Image.Image,
    target: str,
) -> tuple[bool, dict[str, Any]]:
    """Detect the red badge for a named home-screen reward entry."""
    try:
        region = HOME_NOTIFICATION_BADGE_REGIONS[target]
    except KeyError as exc:
        raise ValueError(f"unsupported home notification target: {target}") from exc
    found, details = detect_red_exclamation_badge(image, region)
    return found, {"target": target, **details}
