from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import activity_targets as targets
import activity_rewards as activity

FIXTURE = Path(__file__).parent / "fixtures/recognition/activity-dice-controls-displaced.png"


def result(entries):
    return SimpleNamespace(boxes=[np.array([[x,y],[x+100,y],[x+100,y+30],[x,y+30]]) for x,y,t,s in entries],
                           txts=[t for x,y,t,s in entries], scores=[s for x,y,t,s in entries])


class ActivityTargetTests(unittest.TestCase):
    def test_same_button_moves_without_changing_recognition_or_planned_coordinates(self):
        image = Image.new("RGB", (1280,720), "white")
        for x,y in [(80,120),(1040,620),(500,360)]:
            with patch.object(targets,"_ocr_engine") as engine, patch.object(activity,"_click_ratio_logged") as click:
                engine.return_value.return_value = result([(x,y,"全部领取",.99)])
                activity.click_ratio_logged(123,image,(.01,.01),key="activity_regular_claim_all",logger=MagicMock())
            self.assertEqual(click.call_args.args[2],((x+50)/1280,(y+15)/720))

    def test_missing_low_confidence_and_duplicate_targets_never_fall_back(self):
        image = Image.new("RGB",(1280,720),"white")
        for entries in [[],[(50,50,"全部领取",.89)],[(50,50,"全部领取",.99),(800,500,"全部领取",.99)],
                        [(50,50,"请点击全部领取按钮",.99)],[(1300,900,"全部领取",.99)]]:
            with patch.object(targets,"_ocr_engine") as engine, patch.object(activity,"_click_ratio_logged") as click:
                engine.return_value.return_value=result(entries)
                with self.assertRaises(ValueError):
                    activity.click_ratio_logged(123,image,(.7,.7),key="activity_regular_claim_all",logger=MagicMock())
                click.assert_not_called()

    def test_real_dice_page_is_recognized_and_toggle_click_uses_detected_geometry(self):
        with Image.open(FIXTURE) as image:
            matches, details = activity.recognize_activity_controls(image)
            self.assertEqual(activity.classify_activity_from_matches(matches),"dice_auto")
            toggle = targets.adjacent_toggle(image,details["anchors"]["dice_auto"][0])
            self.assertEqual(toggle["state"],"off")
            with patch.object(activity,"_click_ratio_logged") as click:
                activity.click_ratio_logged(123,image,(0,0),key="activity_dice_auto",logger=MagicMock())
            self.assertEqual(click.call_args.args[2],toggle["point"])
            self.assertNotEqual(click.call_args.args[2],activity.DICE_AUTO_POINT)

    def test_shifted_toggle_and_on_state_do_not_need_fixed_coordinates(self):
        for dx,dy in [(0,0),(250,140)]:
            for state in ("off","on"):
                image=Image.new("RGB",(1000,650),"white")
                x,y=150+dx,100+dy
                entry={"text":"自动进行","box":[[x,y],[x+110,y],[x+110,y+30],[x,y+30]]}
                draw=ImageDraw.Draw(image)
                draw.rounded_rectangle((x-10,y+40,x+120,y+95),radius=27,fill=(85,130,155))
                knob=x+17 if state=="off" else x+93
                draw.ellipse((knob-20,y+47,knob+20,y+87),fill=(240,240,240))
                found=targets.adjacent_toggle(image,entry)
                self.assertEqual(found["state"],state)
                self.assertAlmostEqual(found["point"][0],(x+55)/1000,delta=.003)
        with self.assertRaises(ValueError):
            targets.adjacent_toggle(Image.new("RGB",(1000,650),"white"),entry)

    def test_shared_action_words_need_page_semantics_and_purchase_needs_free_context(self):
        image=Image.new("RGB",(1280,720),"white")
        with patch.object(targets,"_ocr_engine") as engine:
            engine.return_value.return_value=result([(400,200,"全部解锁",.99),(200,80,"拼图活动",.99)])
            matches,details=activity.recognize_activity_controls(image)
        self.assertEqual(activity.classify_activity_from_matches(matches),"puzzle")
        self.assertFalse(matches["bingo_unlock"])
        for entries in [[(600,300,"购买",.99)],[(50,50,"免费1次",.99),(900,500,"购买",.99)],
                        [(600,300,"购买",.99),(600,200,"免费1次",.99),(600,250,"价格200钻石",.99)]]:
            with patch.object(targets,"_ocr_engine") as engine, patch.object(activity,"_click_ratio_logged") as click:
                engine.return_value.return_value=result(entries)
                with self.assertRaises(ValueError):
                    activity.click_ratio_logged(123,image,(.5,.5),key="activity_paid_free_confirm",logger=MagicMock())
                click.assert_not_called()
        with patch.object(targets,"_ocr_engine") as engine, patch.object(activity,"_click_ratio_logged") as click:
            engine.return_value.return_value=result([(600,300,"购买",.99),(600,200,"免费1次",.99)])
            activity.click_ratio_logged(123,image,(.01,.01),key="activity_paid_free_confirm",logger=MagicMock())
            self.assertEqual(click.call_args.args[2],(650/1280,315/720))


if __name__ == "__main__":
    unittest.main()
