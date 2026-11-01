"""Tests for the PySide6 (Windows) interface.

Run on Windows:  python -m pytest windows/tests
Run on Linux:    QT_QPA_PLATFORM=offscreen python -m pytest windows/tests
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MAGICK_STUDIO_NO_DIALOGS"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import magick_studio_win as gui  # noqa: E402

ip = gui.ip
APP = gui.create_app([])
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
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(MAGICK.convert_cmd + list(spec) + [str(path)], check=True,
                   **ip.POPEN_FLAGS)


@pytest.fixture
def images(tmp_path):
    src = tmp_path / "My Photos é"
    make(src / "beach photo.jpg", "-size", "2400x1600", "plasma:fractal")
    make(src / "sub dir" / "logo ünï.png", "-size", "300x300", "xc:none",
         "-fill", "red", "-draw", "circle 150,150 150,30")
    (src / "broken.jpg").write_text("not an image")
    (src / "notes.txt").write_text("not an image either")
    return src


@pytest.fixture
def win():
    w = gui.MainWindow(MAGICK)
    w.show()
    yield w
    if w.cancel_event:
        w.cancel_event.set()
    pump_until(lambda: not w.busy, 30)
    w.close()


def load(win, paths):
    win.add_paths([str(p) for p in paths])
    assert pump_until(lambda: win.items and all(it.info or it.error for it in win.items))


def test_startup_and_empty_state(win):
    assert win.windowTitle() == "Magick Studio"
    assert win.images_stack.currentIndex() == 0          # empty page
    assert not win.start_button.isEnabled()
    assert win.status.text() == "No images"
    assert {"original", "jpeg", "png", "webp"} <= set(win.formats)
    assert not APP.windowIcon().isNull()


def test_import_thumbnails_preview(win, images):
    load(win, [images])
    assert len(win.items) == 3                            # notes.txt is ignored
    assert win.images_stack.currentIndex() == 1
    assert win.status.text() == "2 images ready, 1 unsupported"
    assert win.start_button.isEnabled()
    good = [it for it in win.items if it.info]
    assert all(not it.widget.icon().isNull() for it in good)
    beach = next(it for it in good if it.path.name == "beach photo.jpg")
    assert "2400 × 1600" in beach.widget.text() and "JPEG" in beach.widget.text()
    broken = next(it for it in win.items if it.error)
    assert "Unsupported or damaged image" in broken.widget.text()
    win.list.setCurrentItem(beach.widget)
    assert pump_until(lambda: not win.preview._image.isNull())
    assert "beach photo.jpg" in win.preview_info.text()

    load(win, [images])                                   # no duplicates
    assert len(win.items) == 3


def test_web_preset_processing_and_summary(win, images, tmp_path):
    load(win, [images])
    win.apply_web_preset()
    out = tmp_path / "out put"
    win.set_output_dir(str(out))
    s = win.collect_settings()
    assert (s.output_format, s.width, s.height, s.quality, s.resize, s.allow_upscale,
            s.strip_metadata) == ("webp", 1600, 1600, 82, True, False, True)

    win.start_processing()
    assert win.busy and win.cancel_button.isVisible() and win.progress.isVisible()
    assert not win.settings_panel.isEnabled() and not win.act_add_files.isEnabled()
    assert pump_until(lambda: not win.busy)
    assert win.status.text() == "2 succeeded, 0 failed"
    assert win.last_output_dirs == [out]
    beach = MAGICK.identify(out / "beach photo.webp")
    assert (beach.width, beach.height, beach.format) == (1600, 1067, "WEBP")
    assert (out / "logo ünï.webp").exists()
    assert not [f for f in os.listdir(out) if f.startswith(ip.TEMP_PREFIX)]
    assert (images / "beach photo.jpg").exists()
    assert win.settings_panel.isEnabled() and win.start_button.isVisible()
    done = next(it for it in win.items if it.path.name == "beach photo.jpg")
    assert "✓ beach photo.webp" in done.widget.text()

    shot = os.environ.get("GUI_SCREENSHOT")
    if shot:
        win.grab().save(shot)


def test_formats_quality_and_resize(win, images, tmp_path):
    load(win, [images / "beach photo.jpg"])
    win.format_box.setCurrentIndex(win.formats.index("png"))
    assert not win.quality.isEnabled()                   # PNG is lossless
    win.format_box.setCurrentIndex(win.formats.index("jpeg"))
    assert win.quality.isEnabled()
    win.resize_box.setChecked(True)
    win.width_box.setValue(0)
    win.height_box.setValue(400)
    win.quality.setValue(ip.QUALITY_PRESETS["small"])
    win.set_output_dir(str(tmp_path / "o"))
    win.start_processing()
    assert pump_until(lambda: not win.busy)
    info = MAGICK.identify(tmp_path / "o" / "beach photo.jpg")
    assert (info.width, info.height, info.format) == (600, 400, "JPEG")


def test_invalid_resize_settings_rejected(win, images, monkeypatch):
    load(win, [images / "beach photo.jpg"])
    warnings = []
    monkeypatch.setattr(gui.QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    win.resize_box.setChecked(True)
    win.width_box.setValue(0)
    win.height_box.setValue(0)
    win.start_processing()
    assert not win.busy and warnings and "width" in warnings[0].lower()


def test_cancel(win, tmp_path):
    for i in range(6):
        make(tmp_path / "big" / f"n{i}.png", "-size", "2500x2500", "xc:gray", "+noise", "Random")
    load(win, [tmp_path / "big"])
    win.set_output_dir(str(tmp_path / "out"))
    win.format_box.setCurrentIndex(win.formats.index("webp"))
    win.start_processing()
    pump_until(lambda: False, 0.4)
    win.cancel_processing()
    assert pump_until(lambda: not win.busy, 60)
    assert "cancelled" in win.last_summary
    out = tmp_path / "out"
    assert not out.exists() or not [f for f in os.listdir(out) if f.startswith(ip.TEMP_PREFIX)]


def test_remove_and_clear(win, images):
    load(win, [images])
    win.remove_failed()
    assert len(win.items) == 2
    win.list.setCurrentRow(0)
    win.list.item(0).setSelected(True)
    win.remove_selected()
    assert len(win.items) == 1
    win.clear_list()
    assert not win.items and win.images_stack.currentIndex() == 0


def test_shortcuts_registered(win):
    shortcuts = {a.shortcut().toString() for a in win.actions() if not a.shortcut().isEmpty()}
    assert {"Ctrl+O", "Ctrl+Shift+O", "Ctrl+Return", "Del", "Ctrl+Q", "F1"} <= shortcuts


def test_missing_imagemagick():
    w = gui.MainWindow(None, "ImageMagick 7 (magick.exe) was not found.")
    assert w.empty_title.text() == "ImageMagick not found"
    assert not w.act_add_files.isEnabled() and not w.empty_add_files.isEnabled()
    assert not w.start_button.isEnabled()
    assert w.formats == ["original"]
    w.close()


def test_smoke_test_mode(images, tmp_path):
    report = tmp_path / "report.json"
    code = gui.smoke_test(str(images), str(tmp_path / "smoke"), str(report))
    import json
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["magick"]
    assert len(data["results"]) == 3
    assert code == 1                                      # broken.jpg fails as expected
    assert sum(r["ok"] for r in data["results"]) == 2
