"""Locate activity controls from current OCR boxes, independent of screen coordinates."""
from __future__ import annotations

from collections import deque
import re
import numpy as np
from game_text_recognition import _ocr_engine


def compact(text):
    return re.sub(r"\s+", "", str(text))


def read_controls(image, groups):
    result = _ocr_engine()(np.asarray(image.convert("RGB")))
    boxes, texts, scores = result.boxes, result.txts, result.scores
    if boxes is None or texts is None or scores is None or not len(boxes) == len(texts) == len(scores):
        raise ValueError("missing OCR boxes/text/confidence")
    observations = []
    for box, text, score in zip(boxes, texts, scores):
        points = np.asarray(box, dtype=float)
        if not float(score) >= .90 or points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        if (points < 0).any() or (points[:, 0] > image.width).any() or (points[:, 1] > image.height).any():
            continue
        if (np.ptp(points, axis=0) <= 0).any():
            continue
        observations.append({"text": compact(text), "score": float(score), "box": points.tolist()})
    anchors = {name: [entry for entry in observations if entry["text"] in config["labels"]
                         or (config.get("pattern") and re.fullmatch(config["pattern"], entry["text"]))]
               for name, config in groups.items()}
    words = " ".join(entry["text"] for entry in observations)
    # Shared action wording needs page semantics, not a positional distinction.
    if "拼图" not in words:
        anchors["puzzle_unlock"] = []
    if not any(word in words for word in ("宾果", "兑换所", "BINGO", "Bingo")):
        anchors["bingo_unlock"] = []
    return anchors, observations


def center(entry, image):
    points = np.asarray(entry["box"])
    return float(points[:, 0].mean()/image.width), float(points[:, 1].mean()/image.height)


def unique_target(anchors, group, image, *, first=False):
    candidates = anchors.get(group, [])
    if first and candidates:
        return min(candidates, key=lambda e: (center(e, image)[1], center(e, image)[0]))
    if len(candidates) != 1:
        raise ValueError(f"{group}: expected one OCR target, found {len(candidates)}")
    return candidates[0]


def adjacent_toggle(image, entry):
    """Find an actual pill below its OCR label and inspect the knob position."""
    box = np.asarray(entry["box"])
    left, top = box.min(axis=0)
    right, bottom = box.max(axis=0)
    height, width = bottom-top, right-left
    x0, y0 = max(0, int(left-height*2)), max(0, int(bottom))
    x1, y1 = min(image.width, int(right+height*2)), min(image.height, int(bottom+height*4))
    rgb = np.asarray(image.convert("RGB"))[y0:y1, x0:x1].astype(float)
    gray = rgb.mean(axis=2)
    mask = gray < 185
    seen = np.zeros(mask.shape, dtype=bool)
    candidates = []
    for y, x in zip(*np.where(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        queue, points = deque([(y, x)]), []
        while queue:
            yy, xx = queue.popleft()
            points.append((yy, xx))
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                ny, nx = yy+dy, xx+dx
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        ys, xs = zip(*points)
        l, t, r, b = map(int, (min(xs), min(ys), max(xs)+1, max(ys)+1))
        w, h = r-l, b-t
        if 1.6 <= w/h <= 3.5 and height*1.1 <= h <= height*3.5 and w <= width*2.6 and len(points)/(w*h) >= .45:
            cx, cy = (l+r)/2, (t+b)/2
            if left <= x0+cx <= right and y0+t-bottom <= height*2.5:
                # Compare knob interiors; avoid the rounded outer corners.
                a = gray[int(t+h*.3):int(t+h*.7), int(l+w*.15):int(l+w*.35)].mean()
                z = gray[int(t+h*.3):int(t+h*.7), int(l+w*.65):int(l+w*.85)].mean()
                state = "off" if a-z > 18 else "on" if z-a > 18 else "unknown"
                candidates.append({"point": ((x0+cx)/image.width, (y0+cy)/image.height),
                                   "state": state, "box": (x0+l, y0+t, x0+r, y0+b)})
    if len(candidates) != 1 or candidates[0]["state"] == "unknown":
        raise ValueError("automatic-play label has no unique verified adjacent toggle")
    return candidates[0]
