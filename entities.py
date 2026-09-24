"""Everything that flies around the screen: fruits, bombs, fruit halves, particles.

Physics (used by all moving objects)
------------------------------------
Each object has a position (x, y) and a velocity (vx, vy) in pixels/second.
Image coordinates have y pointing DOWN, so gravity is a positive number that
increases vy every frame. Each frame, with dt = seconds since the last frame:

    vy += gravity * dt      # gravity changes the velocity
    x  += vx * dt           # velocity changes the position
    y  += vy * dt

Using dt makes the motion run at the same speed whatever the frame rate is.
Throwing an object upwards (negative vy) makes it slow down, stop at the top of
its arc, then fall back down: a parabola, like a real throw.
"""
import math
import random

import cv2

# name: (body colour, flesh colour, juice colour, base radius)   colours are BGR
FRUIT_TYPES = {
    "apple": ((40, 40, 215), (180, 230, 250), (150, 200, 240), 38),
    "orange": ((0, 140, 255), (80, 190, 255), (40, 160, 255), 40),
    "lime": ((50, 200, 60), (150, 255, 190), (120, 240, 150), 34),
    "plum": ((130, 40, 120), (120, 200, 240), (160, 60, 170), 34),
    "lemon": ((0, 225, 255), (170, 250, 255), (80, 240, 255), 36),
    "watermelon": ((40, 140, 30), (70, 60, 235), (70, 60, 235), 50),
}


def darker(color, k=0.6):
    return tuple(int(c * k) for c in color)


def lighter(color, k=0.5):
    return tuple(int(c + (255 - c) * k) for c in color)


class PhysicsObject:
    """Something with a position, a velocity and gravity."""

    def __init__(self, x, y, vx, vy, radius, gravity):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.radius = radius
        self.gravity = gravity
        self.angle = random.uniform(0, 360)  # rotation, in degrees
        self.spin = random.uniform(-180, 180)  # degrees per second

    def update(self, dt):
        # Semi-implicit Euler integration (see the module docstring)
        self.vy += self.gravity * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.angle += self.spin * dt

    def fell_off(self, height):
        """True once the object is falling and completely below the screen."""
        return self.vy > 0 and self.y - self.radius > height

    @property
    def center(self):
        return (int(self.x), int(self.y))


class Fruit(PhysicsObject):
    def __init__(self, x, y, vx, vy, gravity, scale=1.0, kind=None):
        self.kind = kind or random.choice(list(FRUIT_TYPES))
        self.body, self.flesh, self.juice, base_r = FRUIT_TYPES[self.kind]
        super().__init__(x, y, vx, vy, base_r * scale, gravity)

    def draw(self, frame):
        c, r = self.center, int(self.radius)
        cv2.circle(frame, c, r, self.body, -1, cv2.LINE_AA)
        cv2.circle(frame, c, r, darker(self.body), 2, cv2.LINE_AA)

        if self.kind == "watermelon":
            # dark stripes that rotate with the fruit
            for k in (0.35, 0.7):
                cv2.ellipse(frame, c, (int(r * k), r), self.angle, 0, 360, darker(self.body, 0.5), 2, cv2.LINE_AA)
        else:
            # a little stem that rotates with the fruit, so you can see it spin
            a = math.radians(self.angle)
            sx, sy = self.x + math.sin(a) * r * 0.9, self.y - math.cos(a) * r * 0.9
            ex, ey = self.x + math.sin(a) * r * 1.25, self.y - math.cos(a) * r * 1.25
            cv2.line(frame, (int(sx), int(sy)), (int(ex), int(ey)), (30, 60, 100), max(2, r // 10), cv2.LINE_AA)

        # highlight: the light always comes from the top-left
        hx, hy = int(self.x - r * 0.35), int(self.y - r * 0.35)
        cv2.circle(frame, (hx, hy), max(2, int(r * 0.25)), lighter(self.body), -1, cv2.LINE_AA)
        cv2.circle(frame, (hx - r // 12, hy - r // 12), max(1, int(r * 0.08)), (255, 255, 255), -1, cv2.LINE_AA)


class Bomb(PhysicsObject):
    def __init__(self, x, y, vx, vy, gravity, scale=1.0):
        super().__init__(x, y, vx, vy, 40 * scale, gravity)

    def draw(self, frame):
        c, r = self.center, int(self.radius)
        cv2.circle(frame, c, r, (25, 25, 25), -1, cv2.LINE_AA)
        cv2.circle(frame, c, r, (90, 90, 90), 2, cv2.LINE_AA)
        cv2.circle(frame, (int(self.x - r * 0.35), int(self.y - r * 0.35)), max(2, int(r * 0.2)),
                   (110, 110, 110), -1, cv2.LINE_AA)

        # fuse sticking out of the top (rotates with the bomb)
        a = math.radians(self.angle)
        fx0, fy0 = self.x + math.sin(a) * r * 0.9, self.y - math.cos(a) * r * 0.9
        fx1, fy1 = self.x + math.sin(a) * r * 1.35, self.y - math.cos(a) * r * 1.35
        cv2.line(frame, (int(fx0), int(fy0)), (int(fx1), int(fy1)), (60, 110, 150), max(2, r // 8), cv2.LINE_AA)

        # flickering spark: random short rays, different every frame
        tip = (int(fx1), int(fy1))
        for _ in range(7):
            ang = random.uniform(0, 2 * math.pi)
            length = random.uniform(0.15, 0.45) * r
            end = (int(fx1 + math.cos(ang) * length), int(fy1 + math.sin(ang) * length))
            color = random.choice([(0, 220, 255), (0, 140, 255), (255, 255, 255)])
            cv2.line(frame, tip, end, color, 2, cv2.LINE_AA)
        cv2.circle(frame, tip, max(2, r // 8), (200, 255, 255), -1, cv2.LINE_AA)


class FruitHalf(PhysicsObject):
    """One half of a sliced fruit.

    `facing` is the direction (in degrees) the round side of this half points.
    Drawing an ellipse arc from -90 to +90 degrees, rotated by `facing`,
    gives a half-disc whose flat side lies along the cut.
    """

    def __init__(self, fruit, vx, vy, facing):
        super().__init__(fruit.x, fruit.y, vx, vy, fruit.radius, fruit.gravity)
        self.body, self.flesh = fruit.body, fruit.flesh
        self.angle = facing
        self.spin = random.choice([-1, 1]) * random.uniform(120, 300)

    def draw(self, frame):
        c, r = self.center, int(self.radius)
        cv2.ellipse(frame, c, (r, r), self.angle, -90, 90, self.body, -1, cv2.LINE_AA)
        inner = int(r * 0.8)
        cv2.ellipse(frame, c, (inner, inner), self.angle, -90, 90, self.flesh, -1, cv2.LINE_AA)


class Particle(PhysicsObject):
    """A drop of juice / spark that shrinks and disappears after `life` seconds."""

    def __init__(self, x, y, vx, vy, gravity, color, radius, life):
        super().__init__(x, y, vx, vy, radius, gravity)
        self.color = color
        self.life = self.max_life = life

    def update(self, dt):
        super().update(dt)
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0

    def draw(self, frame):
        r = int(self.radius * self.life / self.max_life)
        if r > 0:
            cv2.circle(frame, self.center, r, self.color, -1, cv2.LINE_AA)


class FloatingText:
    """Text that drifts upwards and disappears, e.g. "COMBO x3!" or "MISS"."""

    def __init__(self, text, x, y, color, scale=1.0, life=1.0, rise=60):
        self.text, self.x, self.y = text, x, y
        self.color, self.scale = color, scale
        self.life = self.max_life = life
        self.rise = rise  # pixels per second

    def update(self, dt):
        self.y -= self.rise * dt
        self.life -= dt

    @property
    def alive(self):
        return self.life > 0
