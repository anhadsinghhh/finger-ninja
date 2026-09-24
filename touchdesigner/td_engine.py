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

import threading  # noqa: E402

BLADE_COLOR_RGB = (160, 230, 255)  # light blue, same as the Python version
TRACK_WIDTH = 640  # MediaPipe gets a copy this wide (it shrinks images internally anyway)
# True: hand tracking runs on a background thread, so drawing never waits for
# MediaPipe. (Tests switch it off to get the same result every run.)
THREADED = True


class TrackerThread:
    """Runs MediaPipe hand tracking on a background thread.

    The game hands over the newest camera image with submit() and keeps
    drawing. The thread works on the newest image it has been given (older
    ones are simply replaced) and publishes the result. MediaPipe's C code
    lets Python run meanwhile, so tracking and drawing happen at the same time.
    """

    def __init__(self, tracker):
        self.tracker = tracker
        self.error = None
        self._cond = threading.Condition()
        self._job = None
        self._running = True
        # (fingertip or None, capture time, sample number); replaced as a whole
        self.result = (None, 0.0, 0)
        self._thread = threading.Thread(target=self._run, name="FingerNinjaTracker", daemon=True)
        self._thread.start()

    def submit(self, small_frame, scale, capture_time):
        with self._cond:
            self._job = (small_frame, scale, capture_time)  # only the newest image matters
            self._cond.notify()

    def _run(self):
        sample = 0
        while True:
            with self._cond:
                while self._job is None and self._running:
                    self._cond.wait()
                if not self._running:
                    return
                frame, (sx, sy), capture_time = self._job
                self._job = None
            try:
                tip = self.tracker.find_index_tip(frame)
            except Exception:
                self.error = traceback.format_exc()
                return
            if tip is not None:
                tip = (tip[0] * sx, tip[1] * sy)  # back to full-size pixels
            sample += 1
            self.result = (tip, capture_time, sample)

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify()


# When this module is reloaded (rebuilding the network), the old _state is
# still here for a moment: stop its tracker thread before replacing it.
_old_state = globals().get("_state")
if _old_state and _old_state.get("tracker") is not None and hasattr(_old_state["tracker"], "stop"):
    _old_state["tracker"].stop()

_state = {
    "game": None,
    "tracker": None,  # TrackerThread (or a plain HandTracker when THREADED is False)
    "error": None,
    "size": None,
    "prev_time": None,
    "fps": 0.0,
    "fingertip": None,
    "camera_checked": False,
    "last_sample": 0,
    "track_rate": 0.0,  # hand-tracking results per second
    "rate_start": None,
    "rate_samples": 0,
}


def _create(width, height):
    """Build the tracker and game the first time a frame arrives."""
    from game import Game
    from hand_tracker import HandTracker
    from sounds import SoundManager

    if _state["tracker"] is None:
        tracker = HandTracker(max_width=TRACK_WIDTH)
        _state["tracker"] = TrackerThread(tracker) if THREADED else tracker
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

    delayed=True returns the image downloaded from the graphics card on the
    previous call instead of making the GPU stop and wait for this frame's
    image, which is the slowest part of reading a TOP from Python.
    """
    rgba = None
    try:
        rgba = top.numpyArray(delayed=True)
    except TypeError:  # older TouchDesigner without the 'delayed' option
        pass
    if rgba is None:  # the first delayed call has nothing yet
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


def _track(frame, now):
    """Hand the frame to the hand tracker; return (fingertip, capture time, new?).

    With the background thread, the result usually belongs to an image from a
    moment ago, and on some frames there's no new result at all (the game
    draws faster than MediaPipe tracks). `new` tells the game which is which.
    """
    h, w = frame.shape[:2]
    small_h = round(h * TRACK_WIDTH / w)
    small = cv2.resize(frame, (TRACK_WIDTH, small_h), interpolation=cv2.INTER_AREA)
    scale = (w / TRACK_WIDTH, h / small_h)
    tracker = _state["tracker"]

    if not isinstance(tracker, TrackerThread):  # simple mode: wait for the result
        tip = tracker.find_index_tip(small)
        if tip is not None:
            tip = (tip[0] * scale[0], tip[1] * scale[1])
        return tip, now, True

    if tracker.error:
        raise RuntimeError("hand tracking thread failed:\n" + tracker.error)
    tracker.submit(small, scale, now)  # small is a new array, safe to hand over
    tip, capture_time, sample = tracker.result
    new_sample = sample != _state["last_sample"]
    _state["last_sample"] = sample
    return tip, capture_time, new_sample


def _draw_track_rate(frame, new_sample, now):
    """Show how many hand-tracking results arrive per second (next to FPS)."""
    from game import draw_text

    if _state["rate_start"] is None:
        _state["rate_start"] = now
    _state["rate_samples"] += int(new_sample)
    elapsed = now - _state["rate_start"]
    if elapsed >= 1.0:
        _state["track_rate"] = _state["rate_samples"] / elapsed
        _state["rate_start"], _state["rate_samples"] = now, 0
    s = frame.shape[0] / 720
    draw_text(frame, "Hand tracking: {:.0f}/s".format(_state["track_rate"]),
              (150 * s, frame.shape[0] - 20 * s), 0.6 * s, (255, 255, 255), 1)


def cook_game(script_op):
    """Called by the 'game' Script TOP every frame."""
    if not script_op.inputs:
        return
    if not _state["camera_checked"]:
        _state["camera_checked"] = True
        camera = _find_camera(script_op)
        if camera is not None:
            pick_camera_format(camera)

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
    try:
        if _state["game"] is None or _state["size"] != (w, h):
            _create(w, h)

        now = time.perf_counter()
        prev = _state["prev_time"]
        dt = now - prev if prev else 1 / 30
        _state["prev_time"] = now
        if dt > 0:
            _state["fps"] = 0.9 * _state["fps"] + 0.1 / dt

        fingertip, capture_time, new_sample = _track(frame, now)
        _state["fingertip"] = fingertip
        game = _state["game"]
        game.update(dt, fingertip, capture_time, new_sample)
        game.draw(frame, _state["fps"], fingertip is not None)
        _draw_track_rate(frame, new_sample, now)
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
