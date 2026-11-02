"""GUI smoke tests. Skipped automatically when no display is available."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    if os.environ.get("MAGICK_STUDIO_REQUIRE_ALL"):   # CI: a broken Xvfb must not pass
        pytest.fail("no graphical display available", pytrace=False)
    pytest.skip("no graphical display available", allow_module_level=True)

gi = pytest.importorskip("gi")
import main  # noqa: E402
from main import Adw, GLib, ip  # noqa: E402

Adw.init()


def pump(predicate, timeout=20.0):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window():
    app = main.MagickStudio()
    win = main.MainWindow(app, ip.Magick.detect(), "")
    yield win
    win.cancel_event = None
    win.loader.shutdown(wait=True, cancel_futures=True)
    win.destroy()


def test_import_preset_and_process(window, tmp_path):
    magick = window.magick
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(magick.convert_cmd + ["-size", "2000x1000", "plasma:", str(src / "big photo.jpg")],
                   check=True)
    (src / "bad.png").write_text("nope")

    window.add_paths([str(src)])
    assert pump(lambda: len(window.items) == 2
                and all(it.info or it.error for it in window.items))
    assert window.status_label.get_label() == "1 image ready, 1 unsupported"
    assert window.start_button.get_sensitive()

    window.apply_web_preset()
    s = window.collect_settings()
    expected = ip.web_preset()
    assert (s.output_format, s.width, s.height, s.quality, s.resize, s.allow_upscale) == \
        (expected.output_format, expected.width, expected.height, expected.quality, True, False)

    window.set_output_dir(str(tmp_path / "out"))
    window.start_processing()
    assert window.busy and window.progress.get_visible()
    assert pump(lambda: not window.busy, timeout=60)
    dialog = window.get_visible_dialog()
    if dialog:
        dialog.force_close()

    good = next(it for it in window.items if it.info)
    assert good.result.ok
    assert good.result.output == tmp_path / "out" / "big photo.webp"
    assert magick.identify(good.result.output).width == 1600
    assert window.status_label.get_label() == "1 succeeded, 0 failed"


def test_missing_imagemagick_shows_message():
    app = main.MagickStudio()
    win = main.MainWindow(app, None, "ImageMagick is not installed.")
    try:
        assert win.empty_page.get_title() == "ImageMagick Not Found"
        assert not win.add_images_button.get_sensitive()
        assert not win.start_button.get_sensitive()
        assert win.formats == ["original"]
        assert win.empty_page.get_child() is None
    finally:
        win.loader.shutdown(wait=True)
        win.destroy()


def test_non_utf8_filename_and_disguised_file(window, tmp_path):
    """A Latin-1 file name must not stop the import (it used to hang at "Scanning…")."""
    src = tmp_path / "src"
    src.mkdir()
    latin1 = os.fsdecode(b"caf\xe9.jpg")
    subprocess.run(window.magick.convert_cmd + ["-size", "200x100", "xc:red",
                                                f"JPEG:{src / latin1}"], check=True)
    (src / "scan.jpg").write_bytes(b"%!PS\nshowpage\n")   # PostScript disguised as JPEG
    window.add_paths([str(src)])
    assert pump(lambda: len(window.items) == 2
                and all(it.info or it.error for it in window.items))
    assert window.status_label.get_label() == "1 image ready, 1 unsupported"
    scan = next(it for it in window.items if it.path.name == "scan.jpg")
    assert "Not a supported image" in scan.error


def test_quit_cancels_running_batch(tmp_path):
    """Ctrl+Q used to hide the window while the batch kept writing files."""
    app = main.MagickStudio()
    win = main.MainWindow(app, ip.Magick.detect(), "")
    app.window = win
    try:
        for i in range(6):
            subprocess.run(win.magick.convert_cmd + ["-size", "2500x2500", "xc:gray",
                                                     "+noise", "Random",
                                                     str(tmp_path / f"n{i}.png")], check=True)
        win.add_paths([str(tmp_path)])
        assert pump(lambda: len(win.items) == 6 and all(it.info for it in win.items))
        win.set_output_dir(str(tmp_path / "out"))
        win.format_row.set_selected(win.formats.index("webp"))
        win.start_processing()
        event = win.cancel_event
        assert event is not None
        app.lookup_action("quit").activate(None)   # the Ctrl+Q action
        assert event.is_set()
        assert pump(lambda: not win.busy, timeout=60)
        produced = [it for it in win.items if it.result and it.result.ok]
        assert len(produced) < 6
    finally:
        win.loader.shutdown(wait=True, cancel_futures=True)
        win.destroy()
