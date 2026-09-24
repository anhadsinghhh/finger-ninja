"""Finger Ninja: Fruit Ninja played with your index finger in front of a webcam.

Main loop, once per camera frame:
    webcam frame -> mirror -> MediaPipe finds the index fingertip
    -> game.update() (blade speed, slicing, physics) -> game.draw() -> show
"""
import sys
import time
from pathlib import Path

import cv2

from game import Game
from hand_tracker import HandTracker
from sounds import SoundManager

WINDOW = "Finger Ninja"
HIGH_SCORE_FILE = Path(__file__).resolve().parent / "highscore.json"


def open_camera(index=0, width=1280, height=720):
    # DirectShow opens much faster than the default backend on Windows
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def main():
    cap = open_camera()
    ok, frame = cap.read()
    if not ok:
        print("Could not read from the webcam. Is it connected and not used by another app?")
        return 1
    h, w = frame.shape[:2]
    print(f"Camera running at {w}x{h}")

    tracker = HandTracker()
    game = Game(w, h, SoundManager(), HIGH_SCORE_FILE)
    cv2.namedWindow(WINDOW)

    prev = time.perf_counter()
    fps = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Lost the webcam feed.")
                break
            frame = cv2.flip(frame, 1)  # mirror, so moving right moves right on screen

            now = time.perf_counter()
            dt, prev = now - prev, now
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1 / dt)  # smoothed so the number is readable

            fingertip = tracker.find_index_tip(frame)
            game.update(dt, fingertip, now)
            game.draw(frame, fps, fingertip is not None)
            cv2.imshow(WINDOW, frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):  # q or Esc
                break
            if key == ord("p"):
                game.toggle_pause()
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:  # window closed
                break
    finally:
        cap.release()
        tracker.close()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
