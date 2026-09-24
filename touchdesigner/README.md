# Finger Ninja: TouchDesigner version

The same game, running inside [TouchDesigner](https://derivative.ca/). The camera, mirroring, the glowing blade trail and the output window are TouchDesigner nodes. Hand tracking and the game rules run in Python inside two Script TOPs, using the same `game.py`, `entities.py`, `blade.py` and `hand_tracker.py` as the standalone version.

## Network

```
camera ─► mirror ─► game (Script TOP) ───────────────────────────► final ─► out ─► window
                      │   hand tracking + game + drawing            ▲
                      ▼                                             │
                 trail_source (Script TOP)                          │
                      │   newest blade segment only                 │
                      ▼                                             │
                 trail (Composite: add) ◄──── trail_fade (Level 72%) │
                      │        │                    ▲               │
                      │        └──► trail_feedback ─┘               │
                      └──► trail_glow (Blur) ───────────────────────┘

keys (Keyboard In CHOP) ─► key_actions (CHOP Execute DAT)   P = pause, Q = close window
```

**The blade trail is a feedback loop.** Every frame, `trail_source` draws only the newest piece of the blade (from the previous fingertip position to the current one). `trail_feedback` hands `trail` its own previous image, `trail_fade` dims it to 72%, and the new piece is added on top. Older pieces get dimmer every frame until they disappear. A blurred copy is added on top to make the glow.

## Setup (Windows)

1. **Install TouchDesigner** from [derivative.ca/download](https://derivative.ca/download). Open it once and activate the free Non-Commercial licence.
2. **Install MediaPipe for TouchDesigner's Python.** TouchDesigner has its own Python 3.11 with numpy and OpenCV, but no MediaPipe. From the repo folder, run:
   ```bash
   venv\Scripts\python touchdesigner\install_packages.py
   ```
   This puts MediaPipe 1.0.1 and pygame (for sound) into `touchdesigner/td_packages/`.
3. **Build the network.** In TouchDesigner, open a new project, then open **Dialogs → Textport and DATs** and run:
   ```python
   exec(open(r'C:/Users/ANHAD/finger-ninja/touchdesigner/build_network.py').read())
   ```
   (Change the path if the repo is somewhere else.) The script creates `/project1/finger_ninja` and shows the game on the network editor's background.
4. **Save** with Ctrl+S as `touchdesigner/finger_ninja.toe`. From then on, just open that file.

## Where the game is shown

- **As a node in `/project1`**: the build script adds `finger_ninja_view`, a Select TOP showing `finger_ninja/out`, with a live image in its tile. In the starter project it takes `moviefilein1`'s place. Whatever `moviefilein1` was connected to now gets the game image instead, and `moviefilein1` is moved aside, not deleted. Plug `finger_ninja_view` into any other TOP to use the game image in your own network.
- **Network background ("infinite canvas")**: `finger_ninja_view` also has its **Display flag** on, so TouchDesigner draws it behind the nodes. Any other TOPs in `/project1` that had the flag on are switched off, and the Textport lists them.
- **Separate window**: go into `finger_ninja`, select the `window` node and press **Open** in its parameters. Press Q to close it again.

## Resolution

On the first frame, `td_engine.pick_camera_format()` reads the webcam's formats from the camera node's **Signal Format** menu and picks the one closest to 1280×720 at 25 fps or more. The Textport shows the choice, e.g. `camera format -> 1280x720 30.000 fps MJPG`. If the camera only gives a smaller image, it's scaled up to 720 lines *before* the game is drawn, so the fruit and text stay sharp. You can also choose a format by hand in the `camera` node.

## Troubleshooting

- **The Textport prints "Some parameters could not be set"**: a node parameter has a different name in your TouchDesigner version. The rest of the network is built. Look up the right name in that node's parameter dialog.
- **Red error text on the game image**: the Python error is shown on screen and printed in the Textport. After fixing it, run this in the Textport:
  `import td_engine, importlib; importlib.reload(td_engine)`
- **Black camera image**: another app (or the Python version of the game) is using the webcam. Close it, or choose the right camera in the `camera` node's parameters.
- **Wrong camera**: select a different device in the `camera` (Video Device In TOP) parameters.
