"""Hand tracking with MediaPipe's Hand Landmarker (Tasks API).

MediaPipe finds 21 landmarks on the hand. Landmark 8 is the tip of the index
finger, which we use as the "blade". Coordinates come back normalised (0..1),
so we multiply by the frame size to get pixels.
"""
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
MODEL_PATH = Path(__file__).resolve().parent / "hand_landmarker.task"
INDEX_FINGER_TIP = 8


def ensure_model(path=MODEL_PATH, url=MODEL_URL):
    """Download the hand landmarker model the first time the game runs."""
    if path.exists() and path.stat().st_size > 0:
        return path
    print(f"Downloading hand landmark model to {path.name} ...")
    tmp = path.with_suffix(".download")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)  # rename only once the download is complete
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Could not download the model from {url}.\n"
            "Check your internet connection, or download it manually and put "
            f"it next to the game as {path.name}."
        ) from exc
    print("Model downloaded.")
    return path


class HandTracker:
    """Finds the index fingertip in each frame and smooths its position."""

    def __init__(self, smoothing=0.5):
        options = vision.HandLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=str(ensure_model())),
            # VIDEO mode uses the previous frame to track the hand, which is
            # faster and steadier than detecting from scratch every frame.
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.landmarker = vision.HandLandmarker.create_from_options(options)
        # 0 = no smoothing, closer to 1 = smoother but laggier
        self.smoothing = smoothing
        self.smoothed = None
        self.last_timestamp_ms = -1

    def find_index_tip(self, frame_bgr):
        """Return the smoothed (x, y) pixel position of the index fingertip, or None."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)  # MediaPipe expects RGB
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # VIDEO mode needs strictly increasing timestamps in milliseconds
        timestamp_ms = max(int(time.monotonic() * 1000), self.last_timestamp_ms + 1)
        self.last_timestamp_ms = timestamp_ms
        result = self.landmarker.detect_for_video(image, timestamp_ms)

        if not result.hand_landmarks:
            self.smoothed = None
            return None

        tip = result.hand_landmarks[0][INDEX_FINGER_TIP]
        raw = (tip.x * w, tip.y * h)

        # Exponential moving average: each new position only pulls the smoothed
        # position part of the way, which filters out small jitter.
        #   smoothed = a * smoothed + (1 - a) * raw
        if self.smoothed is None:
            self.smoothed = raw
        else:
            a = self.smoothing
            self.smoothed = (
                a * self.smoothed[0] + (1 - a) * raw[0],
                a * self.smoothed[1] + (1 - a) * raw[1],
            )
        return self.smoothed

    def close(self):
        self.landmarker.close()
