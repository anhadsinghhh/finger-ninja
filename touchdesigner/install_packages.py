"""Install the extra Python packages TouchDesigner needs into td_packages/.

TouchDesigner has its own Python (3.11) with numpy and OpenCV already
included, but not MediaPipe. This script downloads MediaPipe (and pygame,
for sound) built for TouchDesigner's Python into touchdesigner/td_packages,
which td_engine.py adds to TouchDesigner's module search path.

Run it with any Python that has pip, from the repo folder:

    venv\\Scripts\\python touchdesigner\\install_packages.py

numpy and OpenCV are deliberately NOT installed here: TouchDesigner has
already loaded its own copies and mixing versions breaks things.
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

TD_PYTHON_VERSION = "3.11"  # the Python version TouchDesigner 2023/2025 uses
TARGET = Path(__file__).resolve().parent / "td_packages"

# MediaPipe 1.x is "py3-none": it talks to its C library with ctypes, so it
# doesn't depend on an exact Python/numpy build. It only needs certifi for
# the Hand Landmarker; the rest of its listed requirements are for features
# we don't use (drawing helpers, audio, model metadata tools).
PACKAGES_NO_DEPS = ["mediapipe==1.0.1"]
PACKAGES = ["certifi", "pygame>=2.5"]


def platform_tag():
    if sys.platform == "win32":
        return "win_amd64"
    if sys.platform == "darwin":
        return "macosx_11_0_arm64" if platform.machine() == "arm64" else "macosx_10_15_x86_64"
    raise SystemExit("TouchDesigner only runs on Windows and macOS.")


def pip_install(packages, no_deps):
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(TARGET),
        "--python-version", TD_PYTHON_VERSION,
        "--platform", platform_tag(),
        "--only-binary=:all:",  # needed when installing for another Python version
        "--upgrade",
    ]
    if no_deps:
        cmd.append("--no-deps")
    print(" ".join(cmd + packages))
    subprocess.check_call(cmd + packages)


def main():
    if TARGET.exists():
        print(f"Removing old {TARGET.name}/")
        shutil.rmtree(TARGET)
    pip_install(PACKAGES_NO_DEPS, no_deps=True)
    pip_install(PACKAGES, no_deps=True)
    print(f"\nDone. Packages installed in {TARGET}")


if __name__ == "__main__":
    main()
