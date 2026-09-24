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
TRACK_WIDTH = 640  # MediaPipe gets a copy this wide (it shrinks images internally anyway)
BLACK_TIMEOUT = 2.0  # seconds of black camera image before we react


_state = {
    "game": None,
    "tracker": None,
    "error": None,
    "size": None,
    "prev_time": None,
    "fps": 0.0,
    "fingertip": None,
    "camera_checked": False,
    "camera": None,  # the Video Device In TOP
    "original_format": None,  # its signal format before we changed it
    "black_since": None,  # when the camera image went black
}


def _create(width, height):
    """Build the tracker and game the first time a frame arrives."""
    from game import Game
    from hand_tracker import HandTracker
    from sounds import SoundManager

    if _state["tracker"] is None:
        _state["tracker"] = HandTracker(max_width=TRACK_WIDTH)
    game = Game(width, height, SoundManager(), ROOT / "highscore.json")
    game.draw_blade = False  # the feedback loop in TouchDesigner draws the trail
    _state["game"] = game
    _state["size"] = (width, height)


MIN_HEIGHT = 720  # draw the game at least this tall so text and fruit stay sharp


def pick_camera_format(camera, target_width=1280, min_fps=25):
    """Choose the webcam's best signal format close to 1280x720.

    The Video Device In TOP lists the camera's modes in its 'signalformat'
    menu, e.g. "1280x720 30.000 fps MJPG". TouchDesigner's default is often a
    small one. We parse width and fps from each label and prefer: fast enough
    (>= min_fps), then width closest to target_width, then higher fps.
    """
    import re

    try:
        par = camera.par.signalformat
        options = list(zip(par.menuNames, par.menuLabels))
    except Exception as exc:
        print('Finger Ninja: camera has no signal format menu ({})'.format(exc))
        return
    best, best_score = None, None
    for name, label in options:
        size = re.search(r'(\d{3,4})\s*[xX]\s*(\d{3,4})', label)
        if not size:
            continue
        width = int(size.group(1))
        rate = (re.search(r'(\d+(?:\.\d+)?)\s*(?:fps|hz)', label, re.I)
                or re.search(r'[p@]\s*(\d+(?:\.\d+)?)', label[size.end():]))
        fps = float(rate.group(1)) if rate else 30.0
        score = (fps >= min_fps, -abs(width - target_width), fps)
        if best_score is None or score > best_score:
            best, best_score = (name, label), score
    if best is None:
        print('Finger Ninja: could not read camera formats:', [label for _, label in options])
        return
    if par.eval() != best[0]:
        _state["original_format"] = par.eval()
        par.val = best[0]
    print('Finger Ninja: camera format ->', best[1])


def _find_camera(script_op):
    """Walk up the inputs (game <- mirror <- camera) to the Video Device In TOP."""
    node = script_op
    for _ in range(5):
        inputs = getattr(node, "inputs", None)
        if not inputs:
            return None
        node = inputs[0]
        try:
            if node.par.signalformat is not None:
                return node
        except Exception:
            pass
    return None


def top_to_bgr(top):
    """TouchDesigner TOP -> OpenCV image.

    numpyArray() gives float RGBA values 0..1 with the BOTTOM row first,
    OpenCV wants uint8 BGR with the TOP row first. OpenCV's own functions do
    the conversion about 3x faster than numpy maths.
    """
    rgba = top.numpyArray()
    bgr = cv2.cvtColor(cv2.convertScaleAbs(rgba, alpha=255), cv2.COLOR_RGBA2BGR)
    return cv2.flip(bgr, 0)


def output_rgba(script_op, image, is_bgr=True):
    """OpenCV image -> Script TOP output (RGBA, bottom row first)."""
    code = cv2.COLOR_BGR2RGBA if is_bgr else cv2.COLOR_RGB2RGBA
    script_op.copyNumpyArray(cv2.flip(cv2.cvtColor(image, code), 0))


def _show_error(script_op, frame, message):
    """Draw a readable error on the camera image instead of failing silently."""
    y = 40
    for line in message.splitlines()[-12:]:
        cv2.putText(frame, line[:110], (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
        y += 28
    output_rgba(script_op, frame)


def _track(frame):
    """Find the fingertip (full-size pixel coordinates) or None."""
    h, w = frame.shape[:2]
    small_h = round(h * TRACK_WIDTH / w)
    small = cv2.resize(frame, (TRACK_WIDTH, small_h), interpolation=cv2.INTER_AREA)
    tip = _state["tracker"].find_index_tip(small)
    if tip is None:
        return None
    return (tip[0] * w / TRACK_WIDTH, tip[1] * h / small_h)


def _camera_is_black(frame, now):
    """Handle a camera that only delivers black images.

    Returns a message to show, or None if the image is fine. If we switched
    the camera's format and it has been black for BLACK_TIMEOUT seconds, the
    new format probably doesn't work with this webcam, so we switch back.
    """
    if frame[::16, ::16].max() > 8:  # anything not (almost) black
        _state["black_since"] = None
        return None
    if _state["black_since"] is None:
        _state["black_since"] = now
        return None
    if now - _state["black_since"] < BLACK_TIMEOUT:
        return None
    camera, original = _state["camera"], _state["original_format"]
    if camera is not None and original is not None:
        print('Finger Ninja: camera stayed black, switching back to its original format:', original)
        camera.par.signalformat.val = original
        _state["original_format"] = None
        _state["black_since"] = None
        return None
    return ("No camera image. Is another app (or the Python game) using the webcam?\n"
            "Otherwise pick a different Device or Signal Format in the 'camera' node.")


def cook_game(script_op):
    """Called by the 'game' Script TOP every frame."""
    if not script_op.inputs:
        return
    if not _state["camera_checked"]:
        _state["camera_checked"] = True
        _state["camera"] = _find_camera(script_op)
        if _state["camera"] is not None:
            pick_camera_format(_state["camera"])

    frame = top_to_bgr(script_op.inputs[0])
    h, w = frame.shape[:2]
    if h < MIN_HEIGHT:
        # small camera image: scale it up first, so everything we draw on top
        # (fruit, text, hearts) is drawn at full resolution and looks sharp
        scale = MIN_HEIGHT / h
        frame = cv2.resize(frame, (round(w * scale), MIN_HEIGHT), interpolation=cv2.INTER_LINEAR)
        h, w = frame.shape[:2]

    if _state["error"]:
        _show_error(script_op, frame, _state["error"])
        return
    black = _camera_is_black(frame, time.perf_counter())
    if black:
        _show_error(script_op, frame, black)
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

        fingertip = _track(frame)
        _state["fingertip"] = fingertip
        game = _state["game"]
        game.update(dt, fingertip, now)
        game.draw(frame, _state["fps"], fingertip is not None)
        output_rgba(script_op, frame)
    except Exception:
        _state["error"] = "Finger Ninja error:\n" + traceback.format_exc()
        print(_state["error"])
        _show_error(script_op, frame, _state["error"])


TRAIL_SCALE = 0.5  # the trail gets blurred into a glow anyway, so half resolution is plenty


def cook_trail(script_op):
    """Called by the 'trail_source' Script TOP every frame.

    Draws only the NEWEST piece of the blade (last position -> current
    position) on a black image. The feedback loop keeps older pieces
    around, dimming them a bit every frame, which makes the fading trail.
    It's drawn at half resolution (faster); the final composite scales it up.
    """
    size = _state["size"]
    if size is None and script_op.inputs:
        size = (script_op.inputs[0].width, script_op.inputs[0].height)
    w, h = size or (1280, 720)
    k = TRAIL_SCALE
    canvas = np.zeros((round(h * k), round(w * k), 3), dtype=np.uint8)

    game = _state["game"]
    if game is not None:
        segment = game.blade.last_segment()
        thickness = max(2, int(14 * k * h / 720))
        if segment is not None:
            (x0, y0), (x1, y1) = segment
            cv2.line(canvas, (int(x0 * k), int(y0 * k)), (int(x1 * k), int(y1 * k)),
                     BLADE_COLOR_RGB, thickness, cv2.LINE_AA)
        if game.blade.tip is not None and _state["fingertip"] is not None:
            tip = tuple(int(v * k) for v in game.blade.tip)
            cv2.circle(canvas, tip, thickness // 2 + 1, (255, 255, 255), -1, cv2.LINE_AA)
    output_rgba(script_op, canvas, is_bgr=False)


def toggle_pause():
    if _state["game"] is not None:
        _state["game"].toggle_pause()


def reset():
    """Forget the game (and any error) so it's rebuilt on the next frame."""
    _state["game"] = None
    _state["error"] = None
    _state["prev_time"] = None
    _state["camera_checked"] = False
    _state["black_since"] = None
