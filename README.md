# Finger Ninja 🍉🥷

A Fruit Ninja–style game you play with your **index finger** in front of your webcam.
Built with Python, [MediaPipe](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker) hand tracking and OpenCV.

## Features

- Real-time hand tracking with MediaPipe's **Hand Landmarker** (Tasks API). Your index fingertip is the blade.
- Smoothed fingertip position and a fading blade trail
- Fruits thrown in arcs with gravity. When you slice one it splits into two halves and sprays juice.
- Bombs with a flickering fuse. Hitting one costs a life and flashes the screen red.
- 3 lives, shown as hearts
- **Combos**: slice 3+ fruits in one quick swipe for bonus points
- Difficulty ramps up over time: more fruits, faster throws and more bombs
- Start screen and game-over screen. Slice the floating fruit to start or play again.
- High score saved to `highscore.json`
- Sound effects generated in code, so there are no audio files. If audio isn't available, the game runs silently.
- Score, high score and FPS on screen
- All graphics are drawn with OpenCV shapes, so there are no image files

## How to play

1. Stand or sit so that your hand is clearly visible to the webcam.
2. Point with your index finger. A glowing trail follows your fingertip.
3. **Swipe quickly** through fruits to slice them. Moving slowly or hovering doesn't cut anything.
4. Avoid the bombs, and don't let fruit fall off the bottom of the screen.
5. Slice 3 or more fruits in one swipe for a combo bonus.

| Key | Action |
|-----|--------|
| `P` | Pause / resume |
| `Q` or `Esc` | Quit |

## How to run

You need Python 3.9–3.12 and a webcam.

```bash
git clone https://github.com/anhadsinghhh/finger-ninja.git
cd finger-ninja
python -m venv venv
```

Activate the virtual environment:

```bash
# Windows (PowerShell)
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

Install and run:

```bash
pip install -r requirements.txt
python main.py
```

The first time you run it, the game downloads the hand model (`hand_landmarker.task`, about 8 MB).

On macOS, allow camera access for your terminal when you're asked.

## How it works

```
webcam frame ──► hand tracking ──► blade ──► collision detection ──► physics & drawing
```

### 1. Hand tracking (`hand_tracker.py`)
Each frame is mirrored, converted to RGB and passed to MediaPipe's Hand Landmarker in `VIDEO` mode. It returns 21 landmarks per hand. Landmark **8** is the index fingertip.
The coordinates come back normalised (0–1), so we multiply them by the frame size to get pixels.
Raw positions jitter a little, so we apply an **exponential moving average**:

```
smoothed = 0.5 * smoothed + 0.5 * new_position
```

### 2. The blade (`blade.py`)
`BladeTrail` keeps the last 10 fingertip positions with timestamps.
- **Speed** = distance between the last two positions ÷ time between them (pixels per second).
- The **trail** is drawn into an alpha mask. Newer segments are thicker and more opaque. The mask is then used to blend the blade colour into the frame.

### 3. Collision detection: line segment vs circle
Between two frames the fingertip moves from point **A** to point **B**. A fast swipe can jump right over a fruit, so checking only B would miss it. Instead, we test the whole segment **AB** against each fruit (a circle with centre **C** and radius **r**):

1. Project C onto the line: `t = ((C − A) · (B − A)) / |B − A|²`
2. Clamp `t` to `[0, 1]` so the point stays on the segment.
3. Closest point: `P = A + t·(B − A)`
4. **Hit** if `|C − P| ≤ r`

A slice only counts if the blade speed is at least **1 screen-width per second**, so hovering doesn't cut anything.

### 4. Physics (`entities.py`, `game.py`)
Every object has a position and a velocity. Each frame (`dt` = seconds since the last frame):

```
vy += gravity * dt
x  += vx * dt
y  += vy * dt
```

Fruits are thrown from below the screen. To reach a height `H`, an object needs an upward speed `v = √(2gH)`, and it reaches the top after `v / g` seconds. That tells us the sideways speed needed to reach a target x position at the top of the arc.
To make the game harder, velocity is multiplied by `k` and gravity by `k²`. The arc keeps the same shape but plays `k` times faster.
When a fruit is sliced, its two halves are pushed apart perpendicular to the swipe direction, and juice particles fly out in random directions.

### Code layout

| File | What's inside |
|------|---------------|
| `main.py` | Webcam loop, keyboard input |
| `hand_tracker.py` | `HandTracker`: model download, MediaPipe, smoothing |
| `blade.py` | `BladeTrail`, `segment_hits_circle()` |
| `entities.py` | `Fruit`, `Bomb`, `FruitHalf`, `Particle`, `FloatingText` |
| `game.py` | `Game`: states, spawning, difficulty, lives, combos, HUD |
| `sounds.py` | `SoundManager`: sound effects made with numpy, played by pygame |

## What I learned

<!-- Fill this in! -->
