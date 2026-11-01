"""Tests for the experimental PySide6 frontend (skipped when PySide6 is missing).

Run:  QT_QPA_PLATFORM=offscreen python -m pytest experimental/windows
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qt_main  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ip = qt_main.ip
APP = QApplication.instance() or QApplication([])
MAGICK = ip.Magick.detect()


def pump_until(cond, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def make(path: Path, *spec):
    subprocess.run(MAGICK.convert_cmd + list(spec) + [str(path)], check=True)


@pytest.fixture
def images(tmp_path):
    src = tmp_path / "my photos"
    (src / "sub dir").mkdir(parents=True)
    make(src / "beach photo.jpg", "-size", "2400x1600", "plasma:fractal")
    make(src / "sub dir" / "logo ünï.png", "-size", "300x300", "xc:none",
         "-fill", "red", "-draw", "circle 150,150 150,30")
    (src / "broken.jpg").write_text("not an image")
    return src


def test_startup_import_resize_webp(images, tmp_path):
    win = qt_main.MainWindow(MAGICK)
    win.show()
    win.add_paths([str(images)])
    assert win.list.count() == 3
    assert win.status.text() == "2 images ready, 1 unsupported"
    assert not win.preview.pixmap().isNull()          # preview of the first image

    win.apply_web_preset()
    out = tmp_path / "out"
    win.output_dir = str(out)
    s = win.collect_settings()
    assert (s.output_format, s.width, s.height, s.quality, s.resize) == ("webp", 1600, 1600, 82, True)

    win.start()
    assert pump_until(lambda: win.cancel_event is None)
    assert win.status.text() == "2 succeeded, 0 failed"
    beach = MAGICK.identify(out / "beach photo.webp")
    assert (beach.width, beach.height, beach.format) == (1600, 1067, "WEBP")
    assert (out / "logo ünï.webp").exists()
    assert not [f for f in os.listdir(out) if f.startswith(ip.TEMP_PREFIX)]
    assert (images / "beach photo.jpg").exists()      # original untouched

    shot = os.environ.get("POC_SCREENSHOT")
    if shot:
        win.grab().save(shot)
    win.close()


def test_cancel(images, tmp_path):
    win = qt_main.MainWindow(MAGICK)
    win.add_paths([str(images)])
    win.output_dir = str(tmp_path / "out")
    win.format_box.setCurrentIndex(win.formats.index("png"))
    win.start()
    win.cancel()
    assert pump_until(lambda: win.cancel_event is None)
    assert win.start_btn.isVisibleTo(win)
    out = tmp_path / "out"
    assert not out.exists() or not [f for f in os.listdir(out) if f.startswith(ip.TEMP_PREFIX)]
    win.close()


def test_missing_imagemagick():
    win = qt_main.MainWindow(None, "ImageMagick is not installed.")
    assert not win.add_files_btn.isEnabled()
    assert "ImageMagick not found" in win.status.text()
    win.close()
