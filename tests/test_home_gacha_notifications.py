from __future__ import annotations

import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from home_notifications import detect_home_reward_notification, find_red_exclamation_badges


def draw_exclamation_badge(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    *,
    color: tuple[int, int, int] = (220, 25, 45),
) -> None:
    center_x, center_y = center
    radius = 7
    draw.polygon(
        (
            (center_x, center_y - radius),
            (center_x + radius, center_y),
            (center_x, center_y + radius),
            (center_x - radius, center_y),
        ),
        fill=color,
    )
    draw.rectangle((center_x, center_y - 3, center_x, center_y), fill="white")
    draw.point((center_x, center_y + 3), fill="white")


class HomeGachaNotificationTests(unittest.TestCase):
    def test_gacha_yellow_exclamation_is_not_a_reward_badge(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        draw_exclamation_badge(draw, (97, 533), color=(224, 158, 58))

        found, _details = detect_home_reward_notification(image, "gacha")

        self.assertFalse(found)

    def test_gacha_red_badge_remains_actionable(self) -> None:
        image = Image.new("RGB", (1000, 600))
        draw = ImageDraw.Draw(image)
        draw_exclamation_badge(draw, (97, 533))

        found, _details = detect_home_reward_notification(image, "gacha")

        self.assertTrue(found)

    def test_all_marked_home_badges_are_detected_from_badge_pixels_only(self) -> None:
        image = Image.new("RGB", (1000, 600), color=(18, 20, 24))
        draw = ImageDraw.Draw(image)
        marked_badges = {
            "mail": (848, 19),
            "pass": (877, 130),
            "promotion": (927, 411),
            "gacha": (97, 533),
            "companion": (151, 533),
            "tasks": (368, 533),
            "achievements": (422, 533),
            "events": (476, 533),
        }
        for center in marked_badges.values():
            draw_exclamation_badge(draw, center)

        for target in marked_badges:
            found, _details = detect_home_reward_notification(image, target)
            self.assertTrue(found, target)

    def test_full_frame_scan_rejects_red_artwork_boxes_and_yellow_badges(self) -> None:
        image = Image.new("RGB", (1000, 600), color=(230, 230, 230))
        draw = ImageDraw.Draw(image)
        expected_centers = [
            (848, 19),
            (877, 130),
            (927, 411),
            (97, 533),
            (151, 533),
            (368, 533),
            (422, 533),
            (476, 533),
        ]
        for center in expected_centers:
            draw_exclamation_badge(draw, center)

        draw_exclamation_badge(draw, (205, 533), color=(224, 158, 58))
        draw.rectangle((40, 40, 260, 180), outline=(255, 70, 70), width=5)
        draw.ellipse((540, 450, 585, 495), fill=(210, 20, 35))
        draw.polygon(((650, 460), (663, 447), (676, 460), (663, 473)), fill=(220, 25, 45))

        badges = find_red_exclamation_badges(image, (0.0, 0.0, 1.0, 1.0))

        actual_centers = {
            (round(badge["center"][0] * image.width), round(badge["center"][1] * image.height))
            for badge in badges
        }
        self.assertEqual(actual_centers, set(expected_centers))


if __name__ == "__main__":
    unittest.main()
