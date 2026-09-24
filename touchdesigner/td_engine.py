"""Glue between the TouchDesigner network and the Finger Ninja Python code.

TouchDesigner calls into this module from two Script TOPs every frame:

    camera -> mirror -> [game Script TOP] ------------------------> final
                             |  cook_game(): hand tracking + game  ^
                             v                                     |
                        [trail Script TOP] -> feedback loop -> glow
                             cook_trail(): draws the newest blade segment

The game rules, physics and drawing are the same classes as the standalone
Python version (game.py, entities.py, blade.py, hand_tracker.py). Only the
blade trail is different: TouchDesigner draws it with a Feedback TOP loop,
where each frame the old trail image is dimmed and the new segment is added
on top, so older parts fade away by themselves.
"""
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent  # .../finger-ninja/touchdesigner
ROOT = HERE.parent  # .../finger-ninja (the Python game lives here)
PACKAGES = HERE / "td_packages"  # MediaPipe etc. installed for TouchDesigner's Python

# Our folders go first so their modules are found before anything else.
# TouchDesigner's own numpy and OpenCV are already loaded, so those stay in use.
for path in (str(ROOT), str(PACKAGES)):
    if path not in sys.path:
        sys.path.insert(0, path)

import cv2  # noqa: E402  (bundled with TouchDesigner)
import numpy as np  # noqa: E402


def _stub_matplotlib():
    """MediaPipe imports matplotlib.pyplot at load time, only for plotting
    helpers we never call. TouchDesigner doesn't ship matplotlib, so if it's
    missing we register an empty placeholder module instead of installing it."""
    try:
        import matplotlib.pyplot  # noqa: F401
    except ImportError:
        import types

        mpl = types.ModuleType("matplotlib")
        mpl.pyplot = types.ModuleType("matplotlib.pyplot")
        sys.modules["matplotlib"] = mpl
        sys.modules["matplotlib.pyplot"] = mpl.pyplot


_stub_matplotlib()

BLADE_COLOR_RGB = (160, 230, 255)  # light blue, same as the Python version

_state = {
    "game": None,
    "tracker": None,
    "error": None,
    "size": None,
    "prev_time": None,
    "fps": 0.0,
    "fingertip": None,
}


def _create(width, height):
    """Build the tracker and game the first time a frame arrives."""
    from game import Game
    from hand_tracker import HandTracker
    from sounds import SoundManager

    if _state["tracker"] is None:
        _state["tracker"] = HandTracker()
    game = Game(width, height, SoundManager(), ROOT / "highscore.json")
    game.draw_blade = False  # the feedback loop in TouchDesigner draws the trail
    _state["game"] = game
    _state["size"] = (width, height)


def top_to_bgr(top):
    """TouchDesigner TOP -> OpenCV image.

    numpyArray() gives float RGBA values 0..1 with the BOTTOM row first,
    OpenCV wants uint8 BGR with the TOP row first.
    """
    rgba = top.numpyArray()
    rgb = (rgba[::-1, :, :3] * 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def output_rgba(script_op, image, is_bgr=True):
    """OpenCV image -> Script TOP output (RGBA, bottom row first)."""
    code = cv2.COLOR_BGR2RGBA if is_bgr else cv2.COLOR_RGB2RGBA
    rgba = cv2.cvtColor(image, code) if image.shape[2] == 3 else image
    script_op.copyNumpyArray(np.ascontiguousarray(rgba[::-1]))


def _show_error(script_op, frame, message):
    """Draw a readable error on the camera image instead of failing silently."""
    y = 40
    for line in message.splitlines()[-12:]:
        cv2.putText(frame, line[:110], (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
        y += 28
    output_rgba(script_op, frame)


def cook_game(script_op):
    """Called by the 'game' Script TOP every frame."""
    if not script_op.inputs:
        return
    frame = top_to_bgr(script_op.inputs[0])
    h, w = frame.shape[:2]

    if _state["error"]:
        _show_error(script_op, frame, _state["error"])
        return
    try:
        if _state["game"] is None or _state["size"] != (w, h):
            _create(w, h)

        now = time.perf_counter()
        prev = _state["prev_time"]
        dt = now - prev if prev else 1 / 30
        _state["prev_time"] = now
        if dt > 0:
            _state["fps"] = 0.9 * _state["fps"] + 0.1 / dt

        fingertip = _state["tracker"].find_index_tip(frame)
        _state["fingertip"] = fingertip
        game = _state["game"]
        game.update(dt, fingertip, now)
        game.draw(frame, _state["fps"], fingertip is not None)
        output_rgba(script_op, frame)
    except Exception:
        _state["error"] = "Finger Ninja error:\n" + traceback.format_exc()
        print(_state["error"])
        _show_error(script_op, frame, _state["error"])


def cook_trail(script_op):
    """Called by the 'trail_source' Script TOP every frame.

    Draws only the NEWEST piece of the blade (last position -> current
    position) on a black image. The feedback loop keeps older pieces
    around, dimming them a bit every frame, which makes the fading trail.
    """
    size = _state["size"]
    if size is None and script_op.inputs:
        size = (script_op.inputs[0].width, script_op.inputs[0].height)
    w, h = size or (1280, 720)
    canvas = np.zeros((h, w, 3), dtype=np.uint8)

    game = _state["game"]
    if game is not None:
        segment = game.blade.last_segment()
        thickness = max(4, int(14 * h / 720))
        if segment is not None:
            (x0, y0), (x1, y1) = segment
            cv2.line(canvas, (int(x0), int(y0)), (int(x1), int(y1)), BLADE_COLOR_RGB, thickness, cv2.LINE_AA)
        if game.blade.tip is not None and _state["fingertip"] is not None:
            tip = tuple(int(v) for v in game.blade.tip)
            cv2.circle(canvas, tip, thickness // 2 + 2, (255, 255, 255), -1, cv2.LINE_AA)
    output_rgba(script_op, canvas, is_bgr=False)


def toggle_pause():
    if _state["game"] is not None:
        _state["game"].toggle_pause()


def reset():
    """Forget the game (and any error) so it's rebuilt on the next frame."""
    _state["game"] = None
    _state["error"] = None
    _state["prev_time"] = None
