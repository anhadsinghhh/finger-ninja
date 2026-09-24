"""Game rules: states, spawning, slicing, lives, combos, difficulty and drawing."""
import json
import math
import random
from pathlib import Path

import cv2
import numpy as np

from blade import BladeTrail, segment_hits_circle
from entities import Bomb, FloatingText, Fruit, FruitHalf, Particle

MENU, PLAYING, PAUSED, GAME_OVER = "menu", "playing", "paused", "game_over"

FONT = cv2.FONT_HERSHEY_DUPLEX
WHITE, YELLOW, RED, GREY = (255, 255, 255), (0, 220, 255), (40, 40, 230), (90, 90, 90)

START_LIVES = 3
# The fingertip must move at least this many screen-widths per second to cut.
# Hovering or slow movement is well below this, a quick swipe is well above.
MIN_SLICE_SPEED = 1.0
# Slices less than this many seconds apart count as the same swipe (combo).
COMBO_WINDOW = 0.35
RETRY_DELAY = 1.0  # seconds before the "play again" fruit can be sliced


def load_high_score(path):
    try:
        return int(json.loads(Path(path).read_text()).get("high_score", 0))
    except Exception:  # missing or broken file
        return 0


def save_high_score(path, score):
    try:
        Path(path).write_text(json.dumps({"high_score": score}))
    except OSError as exc:
        print(f"Could not save high score: {exc}")


def draw_text(frame, text, pos, scale, color=WHITE, thickness=2, center=False):
    """Text with a black outline so it's readable on any background."""
    x, y = int(pos[0]), int(pos[1])
    if center:
        (tw, th), _ = cv2.getTextSize(text, FONT, scale, thickness)
        x, y = x - tw // 2, y + th // 2
    o = thickness + 1  # outline = black copies shifted around the real text
    for dx, dy in ((-o, 0), (o, 0), (0, -o), (0, o), (-o, -o), (o, o), (-o, o), (o, -o)):
        cv2.putText(frame, text, (x + dx, y + dy), FONT, scale, (0, 0, 0), thickness, cv2.LINE_AA)
    cv2.putText(frame, text, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


def draw_heart(frame, cx, cy, size, color):
    """A heart = two circles (the top bumps) + a triangle (the point)."""
    r = size / 2
    cv2.circle(frame, (int(cx - r), int(cy)), int(r), color, -1, cv2.LINE_AA)
    cv2.circle(frame, (int(cx + r), int(cy)), int(r), color, -1, cv2.LINE_AA)
    pts = np.array([(cx - size * 0.98, cy + r * 0.3), (cx + size * 0.98, cy + r * 0.3), (cx, cy + size * 1.25)],
                   dtype=np.int32)
    cv2.fillPoly(frame, [pts], color, cv2.LINE_AA)


class Game:
    def __init__(self, width, height, sounds, high_score_path):
        self.w, self.h = width, height
        self.s = height / 720  # scale factor so everything fits any camera resolution
        self.sounds = sounds
        self.high_score_path = high_score_path
        self.high_score = load_high_score(high_score_path)
        self.base_gravity = 1.3 * height  # pixels / second^2

        self.blade = BladeTrail(max_thickness=max(6, int(16 * self.s)))
        self.fruits, self.bombs = [], []
        self.halves, self.particles, self.texts = [], [], []
        self.state = MENU
        self.clock = 0.0  # game time in seconds (stops while paused)
        self.flash = 0.0  # red flash timer
        self.new_high = False
        self.reset_round()
        self.button_fruit = self.make_button_fruit()

    # ------------------------------------------------------------------ setup
    def reset_round(self):
        self.score = 0
        self.lives = START_LIVES
        self.play_time = 0.0
        self.spawn_timer = 1.0  # short breather before the first throw
        self.combo_count = 0
        self.last_slice_time = -10.0
        self.fruits.clear()
        self.bombs.clear()

    def make_button_fruit(self):
        """The big floating fruit you slice to start / restart."""
        y = self.h * (0.58 if self.state == MENU else 0.66)
        fruit = Fruit(self.w / 2, y, 0, 0, 0, scale=1.8 * self.s, kind="orange")
        fruit.home_y = y
        return fruit

    def start_game(self):
        self.reset_round()
        self.state = PLAYING
        self.new_high = False
        self.sounds.play("start")

    def game_over(self):
        self.finish_combo()
        self.state = GAME_OVER
        self.game_over_time = self.clock
        self.new_high = self.score > self.high_score
        if self.new_high:
            self.high_score = self.score
            save_high_score(self.high_score_path, self.high_score)
        self.fruits.clear()
        self.bombs.clear()
        self.button_fruit = self.make_button_fruit()
        self.sounds.play("game_over")

    def toggle_pause(self):
        if self.state == PLAYING:
            self.state = PAUSED
        elif self.state == PAUSED:
            self.state = PLAYING

    # ------------------------------------------------------------- difficulty
    def difficulty(self):
        """Everything gets harder as play_time goes up, then levels off."""
        t = self.play_time
        return {
            "spawn_interval": max(0.6, 1.8 - t * 0.02),  # seconds between throws
            "max_per_wave": min(5, 1 + int(t // 15)),  # objects per throw
            "speed": min(1.7, 1.0 + t / 100),  # how fast the arcs play out
            "bomb_chance": 0 if t < 5 else min(0.3, 0.06 + t / 250),
        }

    def throw(self, obj_class, speed, **kwargs):
        """Throw an object up from below the screen so its arc stays on screen.

        Physics: to rise a height H against gravity g, an object needs an
        upward speed of v = sqrt(2 * g * H) (from v^2 = 2gH). It reaches the
        top after t = v / g seconds, so to arrive over target_x at the top we
        set vx = (target_x - x) / t.

        Difficulty `speed` multiplies velocity by k and gravity by k^2: the
        arc keeps exactly the same shape but is played k times faster.
        """
        gravity = self.base_gravity * speed ** 2
        x = random.uniform(0.15, 0.85) * self.w
        radius_guess = 50 * self.s
        y = self.h + radius_guess
        peak_y = random.uniform(0.1, 0.45) * self.h
        vy = -math.sqrt(2 * gravity * (y - peak_y))
        time_to_peak = -vy / gravity
        target_x = random.uniform(0.25, 0.75) * self.w
        vx = (target_x - x) / time_to_peak
        return obj_class(x, y, vx, vy, gravity, scale=self.s, **kwargs)

    def spawn_wave(self):
        d = self.difficulty()
        for _ in range(random.randint(1, d["max_per_wave"])):
            if random.random() < d["bomb_chance"]:
                self.bombs.append(self.throw(Bomb, d["speed"]))
            else:
                self.fruits.append(self.throw(Fruit, d["speed"]))

    # ---------------------------------------------------------------- update
    def update(self, dt, fingertip, now):
        """Advance the game by dt seconds. `now` is the real time, for blade speed."""
        self.blade.add_point(fingertip, now)
        if self.state == PAUSED:
            return
        dt = min(dt, 0.05)  # avoid huge jumps if a frame was slow
        self.clock += dt
        self.flash = max(0.0, self.flash - dt)

        # Speed check: only a fast-moving fingertip counts as a cut.
        cut = None
        if self.blade.speed >= MIN_SLICE_SPEED * self.w:
            cut = self.blade.last_segment()

        if self.state in (MENU, GAME_OVER):
            self.update_button(cut)
        elif self.state == PLAYING:
            self.update_playing(dt, cut)

        # effects keep moving on every screen
        for obj in self.halves + self.particles + self.texts:
            obj.update(dt)
        self.halves = [hf for hf in self.halves if not hf.fell_off(self.h)]
        self.particles = [p for p in self.particles if p.alive]
        self.texts = [t for t in self.texts if t.alive]

    def update_button(self, cut):
        fruit = self.button_fruit
        fruit.y = fruit.home_y + math.sin(self.clock * 2.5) * 12 * self.s  # gentle bobbing
        if self.state == GAME_OVER and self.clock - self.game_over_time < RETRY_DELAY:
            return
        if cut and segment_hits_circle(*cut, (fruit.x, fruit.y), fruit.radius):
            fruit.gravity = self.base_gravity  # so its halves fall
            self.split_fruit(fruit)
            self.start_game()

    def update_playing(self, dt, cut):
        self.play_time += dt
        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self.spawn_wave()
            self.spawn_timer = self.difficulty()["spawn_interval"]

        for obj in self.fruits + self.bombs:
            obj.update(dt)

        # Slice detection: does this frame's blade segment cross any object?
        if cut:
            for fruit in list(self.fruits):
                if segment_hits_circle(*cut, (fruit.x, fruit.y), fruit.radius):
                    self.fruits.remove(fruit)
                    self.slice_fruit(fruit)
            for bomb in list(self.bombs):
                if segment_hits_circle(*cut, (bomb.x, bomb.y), bomb.radius):
                    self.bombs.remove(bomb)
                    self.hit_bomb(bomb)
                    if self.state != PLAYING:
                        return

        # Missed fruit: fell off the bottom without being sliced
        for fruit in list(self.fruits):
            if fruit.fell_off(self.h):
                self.fruits.remove(fruit)
                self.texts.append(FloatingText("MISS", fruit.x, self.h - 40 * self.s, RED, 0.9 * self.s, 0.8))
                self.lose_life()
                if self.state != PLAYING:
                    return
        self.bombs = [b for b in self.bombs if not b.fell_off(self.h)]

        # A combo ends once no fruit has been sliced for COMBO_WINDOW seconds
        if self.combo_count and self.clock - self.last_slice_time > COMBO_WINDOW:
            self.finish_combo()

    # ------------------------------------------------------------ game events
    def split_fruit(self, fruit):
        """Replace a fruit by two halves flying apart + a burst of juice."""
        dx, dy = self.blade.direction()
        nx, ny = -dy, dx  # perpendicular to the cut
        facing = math.degrees(math.atan2(ny, nx))
        push = 180 * self.s
        self.halves.append(FruitHalf(fruit, fruit.vx + nx * push, fruit.vy + ny * push, facing))
        self.halves.append(FruitHalf(fruit, fruit.vx - nx * push, fruit.vy - ny * push, facing + 180))
        self.burst(fruit.x, fruit.y, fruit.juice, count=16, speed=350)
        self.sounds.play("slice")

    def burst(self, x, y, colors, count, speed, size=6):
        colors = colors if isinstance(colors, list) else [colors]
        for _ in range(count):
            ang = random.uniform(0, 2 * math.pi)
            v = random.uniform(0.3, 1.0) * speed * self.s
            self.particles.append(Particle(
                x, y, math.cos(ang) * v, math.sin(ang) * v, self.base_gravity,
                random.choice(colors), random.uniform(0.5, 1.2) * size * self.s, random.uniform(0.4, 0.8)))

    def slice_fruit(self, fruit):
        self.split_fruit(fruit)
        self.score += 1
        # Combo: count fruits sliced in quick succession
        if self.clock - self.last_slice_time <= COMBO_WINDOW:
            self.combo_count += 1
        else:
            self.finish_combo()
            self.combo_count = 1
        self.last_slice_time = self.clock
        self.combo_pos = (fruit.x, fruit.y)

    def finish_combo(self):
        if self.combo_count >= 3:
            bonus = self.combo_count  # +1 extra point per fruit in the combo
            self.score += bonus
            x = min(max(self.combo_pos[0], 0.2 * self.w), 0.8 * self.w)
            y = min(max(self.combo_pos[1], 0.2 * self.h), 0.8 * self.h)
            self.texts.append(FloatingText(f"COMBO x{self.combo_count}! +{bonus}", x, y, YELLOW, 1.3 * self.s, 1.2))
            self.sounds.play("combo")
        self.combo_count = 0

    def hit_bomb(self, bomb):
        self.burst(bomb.x, bomb.y, [(0, 140, 255), (0, 220, 255), (60, 60, 60), (255, 255, 255)],
                   count=40, speed=600, size=8)
        self.flash = 0.4
        self.sounds.play("bomb")
        self.lose_life()

    def lose_life(self):
        self.lives -= 1
        if self.lives <= 0:
            self.game_over()

    # ------------------------------------------------------------------ draw
    def draw(self, frame, fps, hand_visible):
        s = self.s
        if self.state != PLAYING:
            frame[:] = cv2.convertScaleAbs(frame, alpha=0.55)  # dim the camera image

        for obj in self.fruits + self.bombs + self.halves + self.particles:
            obj.draw(frame)

        if self.state == MENU:
            draw_text(frame, "Finger Ninja", (self.w / 2, self.h * 0.18), 2.4 * s, YELLOW, max(2, int(4 * s)), True)
            draw_text(frame, "Slice the START fruit to begin", (self.w / 2, self.h * 0.3), 1.0 * s, WHITE, 2, True)
            self.draw_button(frame, "START")
        elif self.state == GAME_OVER:
            draw_text(frame, "GAME OVER", (self.w / 2, self.h * 0.15), 2.2 * s, RED, max(2, int(4 * s)), True)
            draw_text(frame, f"Score: {self.score}", (self.w / 2, self.h * 0.27), 1.3 * s, WHITE, 2, True)
            draw_text(frame, f"High score: {self.high_score}", (self.w / 2, self.h * 0.35), 1.0 * s, WHITE, 2, True)
            if self.new_high:
                draw_text(frame, "NEW HIGH SCORE!", (self.w / 2, self.h * 0.42), 1.0 * s, YELLOW, 2, True)
            if self.clock - self.game_over_time >= RETRY_DELAY:
                draw_text(frame, "Slice to play again", (self.w / 2, self.h * 0.5), 0.9 * s, WHITE, 2, True)
                self.draw_button(frame, "AGAIN")

        for t in self.texts:
            draw_text(frame, t.text, (t.x, t.y), t.scale, t.color, 2, True)

        self.blade.draw(frame)
        self.draw_hud(frame, fps, hand_visible)

        if self.flash > 0:  # red flash after hitting a bomb
            alpha = 0.55 * self.flash / 0.4
            red = np.full_like(frame, (0, 0, 255))
            cv2.addWeighted(red, alpha, frame, 1 - alpha, 0, dst=frame)

        if self.state == PAUSED:
            frame[:] = cv2.convertScaleAbs(frame, alpha=0.5)
            draw_text(frame, "PAUSED", (self.w / 2, self.h * 0.45), 2.0 * s, WHITE, 3, True)
            draw_text(frame, "press P to continue", (self.w / 2, self.h * 0.56), 0.9 * s, WHITE, 2, True)

    def draw_button(self, frame, label):
        fruit = self.button_fruit
        fruit.draw(frame)
        draw_text(frame, label, (fruit.x, fruit.y), 0.9 * self.s, WHITE, 2, True)

    def draw_hud(self, frame, fps, hand_visible):
        s = self.s
        if self.state in (PLAYING, PAUSED):
            draw_text(frame, f"Score: {self.score}", (20 * s, 50 * s), 1.2 * s, WHITE, 2)
            draw_text(frame, f"Best: {max(self.high_score, self.score)}", (20 * s, 90 * s), 0.8 * s, YELLOW, 2)
            for i in range(START_LIVES):
                color = RED if i < self.lives else GREY
                draw_heart(frame, self.w - (40 + 60 * i) * s, 35 * s, 20 * s, color)
        else:
            draw_text(frame, f"Best: {self.high_score}", (20 * s, 50 * s), 0.8 * s, YELLOW, 2)
        draw_text(frame, f"FPS: {fps:.0f}", (20 * s, self.h - 20 * s), 0.6 * s, WHITE, 1)
        draw_text(frame, "P: pause   Q: quit", (self.w - 230 * s, self.h - 20 * s), 0.6 * s, WHITE, 1)
        if not hand_visible:
            draw_text(frame, "Show your index finger to the camera", (self.w / 2, self.h - 60 * s),
                      0.8 * s, YELLOW, 2, True)
