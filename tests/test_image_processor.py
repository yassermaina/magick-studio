import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import image_processor as ip  # noqa: E402

MAGICK = ip.Magick.detect()


def make_image(path: Path, size="400x200", spec="gradient:red-blue", extra=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = path.suffix.lstrip(".").upper().replace("JPG", "JPEG")
    subprocess.run(MAGICK.convert_cmd + ["-size", size, spec, *extra, f"{fmt}:{path}"],
                   check=True)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dims(path: Path):
    info = MAGICK.identify(path)
    return info.width, info.height, info.format


def settings(tmp_path, **kw):
    kw.setdefault("output_dir", str(tmp_path / "out"))
    return ip.Settings(**kw)


def test_detect_finds_imagemagick():
    assert MAGICK.version in (6, 7)
    assert {"jpeg", "png", "webp"} <= set(MAGICK.available_output_formats())


def test_detect_missing_imagemagick(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ip.MagickError, match="not installed"):
        ip.Magick.detect()


def test_output_dir_is_a_file(tmp_path):
    img = make_image(tmp_path / "a.png")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    r = ip.process_image(MAGICK, img, ip.Settings(output_dir=str(blocker)))
    assert not r.ok and "output folder" in r.error.lower()
    assert blocker.read_text() == "x"


def test_identify(tmp_path):
    img = make_image(tmp_path / "a.png")
    info = MAGICK.identify(img)
    assert (info.width, info.height, info.format) == (400, 200, "PNG")
    assert info.size == img.stat().st_size


def test_resize_keeps_aspect_ratio(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(tmp_path, resize=True, width=100))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (100, 50)


def test_resize_height_only(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(tmp_path, resize=True, height=50))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (100, 50)


def test_resize_exact_without_aspect(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(
        tmp_path, resize=True, width=120, height=120, keep_aspect=False))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (120, 120)


def test_no_upscale_by_default(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(tmp_path, resize=True, width=1600, height=1600))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (400, 200)


def test_upscale_when_allowed(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(
        tmp_path, resize=True, width=800, allow_upscale=True))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (800, 400)


def test_keep_original_dimensions(tmp_path):
    img = make_image(tmp_path / "a.png", "400x200")
    r = ip.process_image(MAGICK, img, settings(tmp_path, resize=False, width=10))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (400, 200)


@pytest.mark.parametrize("fmt,expected,ext", [
    ("jpeg", "JPEG", ".jpg"), ("png", "PNG", ".png"), ("webp", "WEBP", ".webp"),
])
def test_format_conversion(tmp_path, fmt, expected, ext):
    img = make_image(tmp_path / "a.png")
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format=fmt))
    assert r.ok, r.error
    assert r.output.suffix == ext
    assert dims(r.output)[2] == expected


def test_keep_original_format(tmp_path):
    img = make_image(tmp_path / "a.jpg")
    r = ip.process_image(MAGICK, img, settings(tmp_path))
    assert r.ok, r.error
    assert r.output.name == "a.jpg"
    assert dims(r.output)[2] == "JPEG"


def test_webp_compression_quality(tmp_path):
    img = make_image(tmp_path / "photo.png", "800x600", "plasma:fractal")
    hi = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp", quality=95))
    lo = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp", quality=20))
    assert hi.ok and lo.ok
    assert lo.output_size < hi.output_size < img.stat().st_size
    assert lo.saved_percent > 0


def test_web_preset(tmp_path):
    img = make_image(tmp_path / "big.jpg", "3000x2000", "plasma:fractal",
                     extra=["-set", "comment", "secret"])
    s = ip.web_preset()
    s.output_dir = str(tmp_path / "out")
    r = ip.process_image(MAGICK, img, s)
    assert r.ok, r.error
    assert dims(r.output) == (1600, 1067, "WEBP")
    assert r.output_size < r.original_size
    comment = subprocess.run(MAGICK.identify_cmd + ["-format", "%c", str(r.output)],
                             capture_output=True, text=True).stdout
    assert "secret" not in comment


def test_jpeg_transparency_becomes_white(tmp_path):
    img = make_image(tmp_path / "t.png", "50x50", "xc:none")
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="jpeg"))
    assert r.ok, r.error
    px = subprocess.run(MAGICK.convert_cmd + [str(r.output), "-format",
                        "%[pixel:p{0,0}]", "info:"], capture_output=True, text=True).stdout
    assert px in ("srgb(255,255,255)", "gray(255)", "white")


def test_webp_preserves_transparency(tmp_path):
    img = make_image(tmp_path / "t.png", "50x50", "xc:none")
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp"))
    assert r.ok, r.error
    opaque = subprocess.run(MAGICK.convert_cmd + [str(r.output), "-format", "%[opaque]",
                            "info:"], capture_output=True, text=True).stdout
    assert opaque.strip().lower() == "false"


def add_exif_orientation(jpeg: Path, value: int):
    """Insert a minimal EXIF segment with the given Orientation tag."""
    tiff = (b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x01"
            + b"\x01\x12\x00\x03\x00\x00\x00\x01" + value.to_bytes(2, "big") + b"\x00\x00"
            + b"\x00\x00\x00\x00")
    payload = b"Exif\x00\x00" + tiff
    data = jpeg.read_bytes()
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    jpeg.write_bytes(data[:2] + segment + data[2:])


def test_auto_orient_before_strip(tmp_path):
    img = make_image(tmp_path / "rot.jpg", "400x200")
    add_exif_orientation(img, 6)  # rotate 90° clockwise
    orient = subprocess.run(MAGICK.identify_cmd + ["-format", "%[orientation]", str(img)],
                            capture_output=True, text=True).stdout
    assert orient == "RightTop"
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="jpeg"))
    assert r.ok, r.error
    assert dims(r.output)[:2] == (200, 400)


def test_filename_with_spaces(tmp_path):
    img = make_image(tmp_path / "my holiday photo.png")
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp"))
    assert r.ok, r.error
    assert r.output.name == "my holiday photo.webp"


@pytest.mark.parametrize("name", ["café ünïcødé 日本語.png", "-dash.png", "colon:name.png",
                                  "brackets[0].png"])
def test_unusual_filenames(tmp_path, name):
    img = make_image(tmp_path / "src" / "x.png")
    target = img.with_name(name)
    img.rename(target)
    r = ip.process_image(MAGICK, target, settings(tmp_path, output_format="webp"))
    assert r.ok, r.error
    assert r.output.name == Path(name).stem + ".webp"
    assert dims(r.output)[2] == "WEBP"


def test_output_collisions_never_overwrite(tmp_path):
    img = make_image(tmp_path / "a.png")
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "a.webp"
    existing.write_bytes(b"do not touch")
    s = settings(tmp_path, output_format="webp")
    r1 = ip.process_image(MAGICK, img, s)
    r2 = ip.process_image(MAGICK, img, s)
    assert r1.ok and r2.ok
    assert existing.read_bytes() == b"do not touch"
    assert r1.output.name == "a (1).webp"
    assert r2.output.name == "a (2).webp"


def test_output_has_normal_permissions(tmp_path):
    img = make_image(tmp_path / "a.png")
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp"))
    assert r.ok, r.error
    mask = os.umask(0)
    os.umask(mask)
    assert r.output.stat().st_mode & 0o777 == 0o666 & ~mask


def test_reserve_unique_path(tmp_path):
    a = ip.reserve_unique_path(tmp_path, "x.jpg")
    b = ip.reserve_unique_path(tmp_path, "x.jpg")
    assert (a.name, b.name) == ("x.jpg", "x (1).jpg")


def test_invalid_image(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_text("this is not an image")
    with pytest.raises(ip.MagickError):
        MAGICK.identify(bad)
    r = ip.process_image(MAGICK, bad, settings(tmp_path, output_format="webp"))
    assert not r.ok and r.error
    out = tmp_path / "out"
    assert not out.exists() or list(out.iterdir()) == []


def test_missing_file(tmp_path):
    r = ip.process_image(MAGICK, tmp_path / "nope.png", settings(tmp_path))
    assert not r.ok and "not found" in r.error.lower()


def test_unwritable_output_dir(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores permissions")
    img = make_image(tmp_path / "a.png")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        r = ip.process_image(MAGICK, img, ip.Settings(output_dir=str(locked / "sub")))
        assert not r.ok and r.error
    finally:
        locked.chmod(0o700)


def test_original_preserved(tmp_path):
    img = make_image(tmp_path / "keep.jpg", "600x400", "plasma:fractal")
    before, mtime = digest(img), img.stat().st_mtime_ns
    for fmt in ("original", "jpeg", "png", "webp"):
        r = ip.process_image(MAGICK, img, settings(
            tmp_path, output_format=fmt, resize=True, width=100))
        assert r.ok, r.error
    assert digest(img) == before and img.stat().st_mtime_ns == mtime


def test_default_output_folder_next_to_image(tmp_path):
    img = make_image(tmp_path / "pics" / "a.png")
    r = ip.process_image(MAGICK, img, ip.Settings(output_format="webp"))
    assert r.ok, r.error
    assert r.output == tmp_path / "pics" / "output" / "a.webp"


def test_same_folder_output_does_not_overwrite_original(tmp_path):
    img = make_image(tmp_path / "a.png")
    before = digest(img)
    r = ip.process_image(MAGICK, img, ip.Settings(output_dir=str(tmp_path)))
    assert r.ok, r.error
    assert r.output.name == "a (1).png"
    assert digest(img) == before


def test_cancel_before_start(tmp_path):
    img = make_image(tmp_path / "a.png")
    ev = threading.Event()
    ev.set()
    r = ip.process_image(MAGICK, img, settings(tmp_path, output_format="webp"), ev)
    assert not r.ok and r.error == "Cancelled"
    out = tmp_path / "out"
    assert not out.exists() or list(out.iterdir()) == []


def test_cancel_while_running_leaves_no_partial_files(tmp_path):
    img = make_image(tmp_path / "huge.png", "3000x3000", "xc:gray", extra=["+noise", "Random"])
    ev = threading.Event()
    threading.Timer(0.3, ev.set).start()
    start = time.monotonic()
    r = ip.process_image(MAGICK, img, settings(
        tmp_path, output_format="webp", resize=True, width=4000, allow_upscale=True), ev)
    assert not r.ok and r.error == "Cancelled"
    assert time.monotonic() - start < 3
    assert list((tmp_path / "out").iterdir()) == []


def test_scan_folder_skips_output_and_hidden(tmp_path):
    make_image(tmp_path / "a.jpg")
    make_image(tmp_path / "sub dir" / "b.png")
    make_image(tmp_path / "output" / "a.jpg")
    make_image(tmp_path / ".hidden" / "c.png")
    make_image(tmp_path / "custom" / "d.png")
    (tmp_path / "notes.txt").write_text("x")
    found = ip.scan_folder(tmp_path, exclude=[tmp_path / "custom"])
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["a.jpg", "sub dir/b.png"]


def test_scan_empty_folder(tmp_path):
    assert ip.scan_folder(tmp_path) == []


def test_settings_validation():
    with pytest.raises(ValueError):
        ip.Settings(quality=0).validate()
    with pytest.raises(ValueError):
        ip.Settings(output_format="bmp").validate()
    with pytest.raises(ValueError):
        ip.Settings(resize=True).validate()
    ip.web_preset().validate()


def test_resize_geometry():
    assert ip.resize_geometry(ip.Settings()) is None
    assert ip.resize_geometry(ip.Settings(resize=True, width=1600, height=1600)) == "1600x1600>"
    assert ip.resize_geometry(ip.Settings(resize=True, width=10, height=20,
                                          keep_aspect=False, allow_upscale=True)) == "10x20!"


def test_command_never_uses_shell(tmp_path):
    cmd = ip.build_command(MAGICK, Path("/x/a b.png"), "/o/t.tmp", ip.web_preset())
    assert isinstance(cmd, list)
    assert "/x/a b.png" in cmd and cmd[-1] == "WEBP:/o/t.tmp"


def test_parse_writable_formats():
    listing = """
   Format  Module    Mode  Description
-------------------------------------------------------------------------------
     AVIF            rw+   AV1 Image File Format (1.17.6)
     JPEG* JPEG      rw-   Joint Photographic Experts Group JFIF format
     WEBP* WEBP      rw+   WebP Image Format (libwebp 1.3.2 [020F])
      PDF* PDF       ---   Portable Document Format
      SVG  SVG       r--   Scalable Vector Graphics
"""
    assert ip.parse_writable_formats(listing) == {"AVIF", "JPEG", "WEBP"}


@pytest.fixture
def cwebp_magick():
    """A Magick instance forced to encode WebP through cwebp."""
    if not MAGICK.cwebp:
        pytest.skip("cwebp not installed")
    m = ip.Magick(MAGICK.convert_cmd, MAGICK.identify_cmd, MAGICK.version, MAGICK.cwebp)
    m._webp_quality_ok = False
    assert m.use_cwebp()
    return m


def test_cwebp_fallback_quality_and_resize(tmp_path, cwebp_magick):
    img = make_image(tmp_path / "photo one.png", "800x600", "plasma:fractal",
                     extra=["-set", "comment", "secret"])
    hi = ip.process_image(cwebp_magick, img, settings(
        tmp_path, output_format="webp", quality=95, resize=True, width=400))
    lo = ip.process_image(cwebp_magick, img, settings(
        tmp_path, output_format="webp", quality=20, resize=True, width=400))
    assert hi.ok and lo.ok, (hi.error, lo.error)
    assert lo.output_size < hi.output_size
    assert dims(hi.output) == (400, 300, "WEBP")
    assert [f.name for f in (tmp_path / "out").iterdir() if f.name.startswith(".")] == []


def test_cwebp_fallback_keeps_transparency(tmp_path, cwebp_magick):
    img = make_image(tmp_path / "t.png", "50x50", "xc:none")
    r = ip.process_image(cwebp_magick, img, settings(tmp_path, output_format="webp"))
    assert r.ok, r.error
    opaque = subprocess.run(MAGICK.convert_cmd + [str(r.output), "-format", "%[opaque]",
                            "info:"], capture_output=True, text=True).stdout
    assert opaque.strip().lower() == "false"


def test_cwebp_fallback_invalid_image(tmp_path, cwebp_magick):
    bad = tmp_path / "bad.png"
    bad.write_text("nope")
    r = ip.process_image(cwebp_magick, bad, settings(tmp_path, output_format="webp"))
    assert not r.ok
    assert list((tmp_path / "out").iterdir()) == []


def test_human_size():
    assert ip.human_size(512) == "512 B"
    assert ip.human_size(2048) == "2.0 KB"
