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
   (Change the path if the repo is somewhere else.) The script creates `/project1/finger_ninja` and opens the game window.
4. **Save** with Ctrl+S as `touchdesigner/finger_ninja.toe`. From then on, just open that file.

## Troubleshooting

- **The Textport prints "Some parameters could not be set"**: a node parameter has a different name in your TouchDesigner version. The rest of the network is built. Look up the right name in that node's parameter dialog.
- **Red error text on the game image**: the Python error is shown on screen and printed in the Textport. After fixing it, run this in the Textport:
  `import td_engine, importlib; importlib.reload(td_engine)`
- **Black camera image**: another app (or the Python version of the game) is using the webcam. Close it, or choose the right camera in the `camera` node's parameters.
- **Wrong camera**: select a different device in the `camera` (Video Device In TOP) parameters.
