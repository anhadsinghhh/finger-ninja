"""The blade: fingertip trail, speed measurement and slice detection."""
import math
from collections import deque

import cv2
import numpy as np


def segment_hits_circle(a, b, center, radius):
    """Does the line segment a->b pass through the circle?

    The blade moves from point A (last frame) to point B (this frame). Checking
    only B would miss fruits the finger jumped over between frames, so we test
    the whole segment:

    1. Project the circle centre C onto the line through A and B:
           t = ((C - A) . (B - A)) / |B - A|^2
       t = 0 means the closest point is A, t = 1 means it is B.
    2. Clamp t to [0, 1] so the closest point stays on the segment.
    3. Closest point P = A + t * (B - A).
    4. It's a hit if the distance from P to C is at most the radius.
    """
    ax, ay = a
    bx, by = b
    cx, cy = center
    abx, aby = bx - ax, by - ay
    length_sq = abx * abx + aby * aby
    if length_sq == 0:  # A and B are the same point
        t = 0.0
    else:
        t = ((cx - ax) * abx + (cy - ay) * aby) / length_sq
        t = max(0.0, min(1.0, t))
    px, py = ax + t * abx, ay + t * aby
    return (px - cx) ** 2 + (py - cy) ** 2 <= radius * radius


def segment_hits_moving_circle(a, b, center_before, center_now, radius):
    """Like segment_hits_circle, but the fruit moved during the frame too.

    Between two frames the blade goes A -> B while the fruit goes from
    C_before to C_now. At 20-30 FPS a falling fruit can move 40+ pixels per
    frame, so testing only where it is now can miss cuts that looked right.

    Trick: look at the blade *from the fruit's point of view*. Relative to
    the fruit, the blade starts at A - C_before and ends at B - C_now, while
    the fruit sits still at (0, 0). Then it's the same segment-vs-circle test.
    """
    rel_a = (a[0] - center_before[0], a[1] - center_before[1])
    rel_b = (b[0] - center_now[0], b[1] - center_now[1])
    return segment_hits_circle(rel_a, rel_b, (0.0, 0.0), radius)


class BladeTrail:
    """Remembers the last few fingertip positions, measures speed, draws the trail."""

    def __init__(self, length=10, color=(255, 230, 160), max_thickness=16, max_gap=0.3):
        self.points = deque(maxlen=length)  # (x, y, time) tuples, oldest first
        self.color = color  # BGR, a light blue glow
        self.max_thickness = max_thickness
        self.speed = 0.0  # pixels per second
        # During a fast swipe the webcam image blurs and MediaPipe can lose the
        # hand for a few frames. Gaps shorter than this (seconds) are bridged.
        self.max_gap = max_gap
        self.moved = False  # did the blade get a new point this frame?

    def add_point(self, point, now):
        """Record the fingertip for this frame (None if no hand was found)."""
        if point is None:
            # Hand lost this frame. Keep the trail for a short while: if the
            # hand comes back quickly, the segment from the last known point
            # to the new one covers the blurry frames in between. Only after a
            # longer gap do we forget it, so the blade can't "teleport" across
            # the screen and slice everything in between.
            self.moved = False
            self.speed = 0.0
            if self.points and now - self.points[-1][2] > self.max_gap:
                self.points.clear()
            return
        self.moved = True
        self.points.append((point[0], point[1], now))
        if len(self.points) >= 2:
            # Speed = distance moved / time taken, between the last two frames
            x0, y0, t0 = self.points[-2]
            x1, y1, t1 = self.points[-1]
            self.speed = math.hypot(x1 - x0, y1 - y0) / max(t1 - t0, 1e-3)

    @property
    def tip(self):
        return self.points[-1][:2] if self.points else None

    def last_segment(self):
        """The path the fingertip took since the previous detection, or None."""
        if not self.moved or len(self.points) < 2:
            return None
        return self.points[-2][:2], self.points[-1][:2]

    def direction(self):
        """Unit vector of the latest movement (used to split fruit along the cut)."""
        seg = self.last_segment()
        if seg is None:
            return (1.0, 0.0)
        (x0, y0), (x1, y1) = seg
        d = math.hypot(x1 - x0, y1 - y0)
        return ((x1 - x0) / d, (y1 - y0) / d) if d > 0 else (1.0, 0.0)

    def draw(self, frame):
        """Draw a trail that gets thinner and more transparent towards the tail.

        We draw every segment into a greyscale "alpha mask" (255 = fully
        opaque, 0 = invisible), with older segments darker and thinner, then
        blend the blade colour into the frame using that mask. Only the
        rectangle around the trail is blended, to keep it fast.
        """
        n = len(self.points)
        if n < 2:
            return
        h, w = frame.shape[:2]
        pts = [(int(x), int(y)) for x, y, _ in self.points]
        pad = self.max_thickness
        x0 = max(min(p[0] for p in pts) - pad, 0)
        y0 = max(min(p[1] for p in pts) - pad, 0)
        x1 = min(max(p[0] for p in pts) + pad, w)
        y1 = min(max(p[1] for p in pts) + pad, h)
        if x1 <= x0 or y1 <= y0:
            return

        mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        for i in range(1, n):
            f = i / (n - 1)  # 0 for the oldest segment, 1 for the newest
            p0 = (pts[i - 1][0] - x0, pts[i - 1][1] - y0)
            p1 = (pts[i][0] - x0, pts[i][1] - y0)
            thickness = max(1, int(self.max_thickness * f))
            cv2.line(mask, p0, p1, int(255 * f), thickness, cv2.LINE_AA)

        roi = frame[y0:y1, x0:x1]
        alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
        color = np.array(self.color, dtype=np.float32)
        roi[:] = (roi * (1 - alpha) + color * alpha).astype(np.uint8)

        # bright dot on the fingertip itself
        cv2.circle(frame, pts[-1], max(3, self.max_thickness // 3), (255, 255, 255), -1, cv2.LINE_AA)
