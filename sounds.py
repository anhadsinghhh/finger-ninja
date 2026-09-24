"""Sound effects with pygame.mixer.

The sounds are generated in code with numpy (no audio files needed). If pygame
or the audio device isn't available, the game carries on silently.
"""
import os

import numpy as np

SAMPLE_RATE = 44100


def _t(seconds):
    return np.linspace(0, seconds, int(SAMPLE_RATE * seconds), endpoint=False)


def _sweep(f_start, f_end, seconds):
    """Sine wave whose pitch glides from f_start to f_end."""
    freq = np.linspace(f_start, f_end, int(SAMPLE_RATE * seconds))
    return np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)


def _smooth(x, n):
    """Moving average = crude low-pass filter (removes the harsh high part of noise)."""
    return np.convolve(x, np.ones(n) / n, mode="same")


def make_slice():
    t = _t(0.18)
    env = np.minimum(t / 0.01, 1) * np.exp(-t * 18)
    noise = _smooth(np.random.uniform(-1, 1, t.size), 4)
    return (0.6 * noise + 0.4 * _sweep(2000, 500, 0.18)) * env


def make_bomb():
    t = _t(0.8)
    env = np.exp(-t * 5)
    noise = _smooth(np.random.uniform(-1, 1, t.size), 25)
    return (0.7 * noise * 3 + 0.5 * _sweep(120, 35, 0.8)) * env


def make_combo():
    notes = [660, 880, 1100]
    return np.concatenate([np.sin(2 * np.pi * f * _t(0.08)) * np.exp(-_t(0.08) * 20) for f in notes])


def make_start():
    notes = [523, 784]
    return np.concatenate([np.sin(2 * np.pi * f * _t(0.12)) * np.exp(-_t(0.12) * 12) for f in notes])


def make_game_over():
    parts = []
    for f in (440, 350, 260):
        t = _t(0.28)
        wave = np.sin(2 * np.pi * f * t) + 0.3 * np.sign(np.sin(2 * np.pi * f * t))
        parts.append(wave * np.exp(-t * 4))
    return np.concatenate(parts)


class SoundManager:
    def __init__(self, volume=0.5):
        self.enabled = False
        self.sounds = {}
        try:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame

            pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 512)
            pygame.mixer.init()
            pygame.mixer.set_num_channels(16)  # lets several slices overlap
            _, _, channels = pygame.mixer.get_init()
            makers = {
                "slice": make_slice,
                "bomb": make_bomb,
                "combo": make_combo,
                "start": make_start,
                "game_over": make_game_over,
            }
            for name, make in makers.items():
                wave = np.clip(make() * volume, -1, 1)
                samples = (wave * 32767).astype(np.int16)
                if channels == 2:
                    samples = np.column_stack([samples, samples])
                self.sounds[name] = pygame.mixer.Sound(buffer=np.ascontiguousarray(samples).tobytes())
            self.enabled = True
        except Exception as exc:  # no pygame, no audio device, ...
            print(f"[sound] Sound disabled: {exc}")

    def play(self, name):
        if self.enabled and name in self.sounds:
            try:
                self.sounds[name].play()
            except Exception:
                pass
