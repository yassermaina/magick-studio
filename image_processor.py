"""ImageMagick backend for Magick Studio.

This module has no GTK dependency so it can be tested and reused on its own.
All ImageMagick calls use argument lists (never a shell), absolute paths and
an explicit output format prefix, so spaces, Unicode, leading dashes and
colons in filenames are handled safely.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".png", ".webp", ".avif", ".gif", ".bmp",
    ".tif", ".tiff", ".heic", ".heif", ".jxl",
}

# Output format id -> (ImageMagick coder, file extension, uses quality setting)
OUTPUT_FORMATS = {
    "jpeg": ("JPEG", ".jpg", True),
    "png": ("PNG", ".png", False),
    "webp": ("WEBP", ".webp", True),
    "avif": ("AVIF", ".avif", True),
}

# Extension -> output format id, used when keeping the original format.
_EXT_TO_FORMAT = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".jpe": "jpeg",
    ".png": "png", ".webp": "webp", ".avif": "avif",
}

APP_VERSION = "1.1.0"

# Images larger than this are refused before decoding. ImageMagick keeps
# pixels as floats, so a small crafted file (a "decompression bomb") could
# otherwise take many gigabytes of memory and disk per image.
MAX_PIXELS = 200_000_000

DEFAULT_OUTPUT_DIRNAME = "output"
DEFAULT_QUALITY = 82
TEMP_PREFIX = ".magick-studio-"

# On Windows, magick.exe is a console program: without this flag every call
# from the windowed app would flash a console window.
POPEN_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


# Permissions for processed images, e.g. 0644 with the usual umask of 022.
OUTPUT_FILE_MODE = 0o666 & ~_umask()

QUALITY_PRESETS = {
    "high": 92,
    "balanced": DEFAULT_QUALITY,
    "small": 65,
}


class MagickError(Exception):
    """Raised when ImageMagick is missing or a command fails."""


class Cancelled(Exception):
    """Raised when processing is cancelled by the user."""


@dataclass
class Settings:
    output_format: str = "original"  # "original" or a key of OUTPUT_FORMATS
    width: int = 0                   # 0 = automatic
    height: int = 0                  # 0 = automatic
    resize: bool = False             # False = keep original dimensions
    keep_aspect: bool = True
    allow_upscale: bool = False
    quality: int = DEFAULT_QUALITY
    strip_metadata: bool = True
    high_effort: bool = True         # slower, better compression (WebP/AVIF/PNG)
    output_dir: str | None = None    # None = "output" folder next to each image

    def validate(self) -> None:
        if self.output_format != "original" and self.output_format not in OUTPUT_FORMATS:
            raise ValueError(f"Unsupported output format: {self.output_format}")
        if not 1 <= int(self.quality) <= 100:
            raise ValueError("Quality must be between 1 and 100")
        for name, value in (("Width", self.width), ("Height", self.height)):
            if not 0 <= int(value) <= 65535:
                raise ValueError(f"{name} must be between 0 and 65535")
        if self.resize and not self.width and not self.height:
            raise ValueError("Enter a width and/or height to resize")


def web_preset() -> Settings:
    """The one-click "Optimize for Web" preset."""
    return Settings(
        output_format="webp",
        width=1600,
        height=1600,
        resize=True,
        keep_aspect=True,
        allow_upscale=False,
        quality=DEFAULT_QUALITY,
        strip_metadata=True,
        high_effort=True,
    )


@dataclass
class ImageInfo:
    path: Path
    width: int
    height: int
    format: str
    size: int


@dataclass
class ProcessResult:
    source: Path
    output: Path | None = None
    ok: bool = False
    error: str = ""
    original_size: int = 0
    output_size: int = 0

    @property
    def saved_percent(self) -> float:
        if not self.ok or self.original_size <= 0:
            return 0.0
        return (1 - self.output_size / self.original_size) * 100


@dataclass
class Magick:
    """Locates ImageMagick 7 (`magick`) or ImageMagick 6 (`convert`/`identify`)."""

    convert_cmd: list[str]
    identify_cmd: list[str]
    version: int
    cwebp: str | None = None
    _formats: set[str] | None = field(default=None, repr=False)
    _webp_quality_ok: bool | None = field(default=None, repr=False)

    @classmethod
    def detect(cls) -> "Magick":
        cwebp = shutil.which("cwebp")
        override = os.environ.get("MAGICK_STUDIO_MAGICK")
        if override:
            resolved = shutil.which(override)   # a full path, or a name on PATH
            if not resolved:
                raise MagickError("MAGICK_STUDIO_MAGICK is not an executable program: "
                                  f"{override}")
            override = resolved
        magick = override or _bundled_magick() or shutil.which("magick")
        if magick:
            return cls([magick], [magick, "identify"], 7, cwebp)
        # Windows has an unrelated C:\Windows\System32\convert.exe (disk tool),
        # and ImageMagick 6 is not supported there.
        if sys.platform == "win32":
            raise MagickError("ImageMagick 7 (magick.exe) was not found. "
                              "Reinstall Magick Studio, or install ImageMagick 7 "
                              "and add it to PATH.")
        convert, identify = shutil.which("convert"), shutil.which("identify")
        if convert and identify:
            return cls([convert], [identify], 6, cwebp)
        raise MagickError(
            "ImageMagick is not installed. Install it with "
            "'sudo dnf install ImageMagick' (Fedora) or "
            "'sudo apt install imagemagick' (Debian/Ubuntu)."
        )

    def writable_formats(self) -> set[str]:
        """Upper-case coder names that this ImageMagick build can write."""
        if self._formats is None:
            try:
                out = subprocess.run(
                    self.convert_cmd + ["-list", "format"],
                    capture_output=True, encoding="utf-8", errors="replace",
                    timeout=30, **POPEN_FLAGS,
                ).stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
            self._formats = parse_writable_formats(out)
        return self._formats

    def webp_quality_works(self) -> bool:
        """Whether -quality affects WebP output.

        ImageMagick 6.9.12 as shipped by Ubuntu 24.04 silently ignores it.
        """
        if self._webp_quality_ok is None:
            sizes = []
            for quality in ("10", "90"):
                try:
                    out = subprocess.run(
                        self.convert_cmd + ["rose:", "-quality", quality, "WEBP:-"],
                        capture_output=True, timeout=30, **POPEN_FLAGS,
                    ).stdout
                except (OSError, subprocess.SubprocessError):
                    out = b""
                sizes.append(len(out))
            self._webp_quality_ok = 0 < sizes[0] < sizes[1]
        return self._webp_quality_ok

    def use_cwebp(self) -> bool:
        """Encode WebP with cwebp when ImageMagick cannot apply the quality."""
        return self.cwebp is not None and not self.webp_quality_works()

    def available_output_formats(self) -> list[str]:
        writable = self.writable_formats()
        return [key for key, (coder, _, _) in OUTPUT_FORMATS.items()
                if coder in writable]

    def identify(self, path: str | os.PathLike) -> ImageInfo:
        path = Path(path).absolute()
        if not path.is_file():
            raise MagickError("File not found")
        source = _input(path)
        try:
            proc = subprocess.run(
                self.identify_cmd + ["-ping", "-quiet", "-format", "%w %h %m\n", source],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=60, **POPEN_FLAGS,
            )
        except subprocess.TimeoutExpired:
            raise MagickError("Timed out reading image")
        except OSError as exc:
            raise MagickError(str(exc))
        lines = proc.stdout.strip().splitlines()
        if proc.returncode != 0 or not lines:
            raise MagickError(_clean_error(proc.stderr) or "Not a valid image")
        try:
            w, h, fmt = lines[0].split(maxsplit=2)
            return ImageInfo(path, int(w), int(h), fmt, path.stat().st_size)
        except (ValueError, OSError) as exc:
            raise MagickError(f"Could not read image information: {exc}")


def parse_writable_formats(listing: str) -> set[str]:
    """Parse `-list format` output into the set of writable coder names."""
    formats: set[str] = set()
    for line in listing.splitlines():
        # e.g. "WEBP* WEBP  rw+  WebP Image Format" — the module column
        # may be empty: "AVIF  rw+  AV1 Image File Format"
        parts = line.split()
        mode = next((p for p in parts[1:3] if re.fullmatch(r"[r-][w-][+-]", p)), "")
        if mode[1:2] == "w":
            formats.add(parts[0].rstrip("*").upper())
    return formats


def sniff_coder(path: str | os.PathLike) -> str | None:
    """ImageMagick decoder for a file, from its signature; None if not a supported image.

    ImageMagick picks the decoder from the file content, not the name. Without
    this, a "photo.jpg" that is really PostScript, PDF, SVG or MVG would be
    handed to Ghostscript or a vector renderer, which can read other files.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        return "TIFF"
    if head[:2] == b"BM" and len(head) >= 26:
        return "BMP"
    if head[:2] == b"\xff\x0a" or head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return "JXL"
    if head[4:8] == b"ftyp":   # ISO media: major brand plus compatible brands
        size = int.from_bytes(head[:4], "big")
        brands = {head[i:i + 4] for i in range(8, min(size, len(head)) - 3, 4)}
        if brands & {b"avif", b"avis"}:
            return "AVIF"
        if brands & {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}:
            return "HEIC"
    return None


def _input(path: Path) -> str:
    """Absolute input path with an explicit decoder, e.g. "PNG:/photos/a.png"."""
    coder = sniff_coder(path)
    if coder is None:
        raise MagickError("Not a supported image (JPEG, PNG, WebP, AVIF, GIF, BMP, "
                          "TIFF, HEIC or JPEG XL)")
    return f"{coder}:{path.absolute()}"


def check_size(info: ImageInfo) -> None:
    if info.width * info.height > MAX_PIXELS:
        raise MagickError(f"Image is too large ({info.width} × {info.height}; "
                          f"the limit is {MAX_PIXELS // 1_000_000} megapixels)")


def _bundled_magick() -> str | None:
    """ImageMagick shipped inside a PyInstaller build (Windows installer)."""
    if not getattr(sys, "frozen", False):
        return None
    base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    exe = base / "imagemagick" / ("magick.exe" if sys.platform == "win32" else "magick")
    return str(exe) if exe.is_file() else None


def is_supported(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def scan_folder(folder: str | os.PathLike, exclude: list[str | os.PathLike] = ()) -> list[Path]:
    """Recursively find supported images in a folder.

    Hidden directories, folders named "output" (the default destination) and
    any explicitly excluded directory are skipped so processed results are
    never re-imported.
    """
    folder = Path(folder).absolute()
    excluded = {os.path.realpath(p) for p in exclude}
    found: list[Path] = []
    for root, dirs, files in os.walk(folder, onerror=lambda e: None):
        dirs[:] = sorted(
            d for d in dirs
            if not _is_hidden(root, d)
            and d != DEFAULT_OUTPUT_DIRNAME
            and os.path.realpath(os.path.join(root, d)) not in excluded
        )
        for name in sorted(files):
            if not _is_hidden(root, name) and is_supported(name):
                found.append(Path(root) / name)
    return found


def _is_hidden(root: str, name: str) -> bool:
    """Dot-files everywhere, plus files marked hidden on Windows."""
    if name.startswith("."):
        return True
    if sys.platform == "win32":
        try:
            attrs = os.stat(os.path.join(root, name)).st_file_attributes
        except OSError:
            return False
        return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    return False


def resolve_output_format(source: Path, settings: Settings) -> str:
    """Return the output format id used for a source file."""
    if settings.output_format != "original":
        return settings.output_format
    return _EXT_TO_FORMAT.get(source.suffix.lower(), "")


def output_dir_for(source: Path, settings: Settings) -> Path:
    if settings.output_dir:
        return Path(settings.output_dir).absolute()
    return source.absolute().parent / DEFAULT_OUTPUT_DIRNAME


def resize_geometry(settings: Settings) -> str | None:
    if not settings.resize or (not settings.width and not settings.height):
        return None
    w = str(settings.width) if settings.width else ""
    h = str(settings.height) if settings.height else ""
    geometry = f"{w}x{h}"
    if not settings.keep_aspect and settings.width and settings.height:
        geometry += "!"
    if not settings.allow_upscale:
        geometry += ">"
    return geometry


def build_command(magick: Magick, source: Path, dest: str, settings: Settings,
                  intermediate: bool = False) -> list[str]:
    """Build the ImageMagick argument list for converting one image.

    `dest` is the final on-disk path; the coder is given explicitly so the
    destination extension (e.g. a temporary name) does not matter.
    With `intermediate`, writes a quick 8-bit PNG for cwebp to encode.
    """
    fmt = "png" if intermediate else resolve_output_format(source, settings)
    args = list(magick.convert_cmd)
    args += [_input(source)]
    args += ["-delete", "1--1"]   # use first frame of animated/multi-page files
    args += ["-auto-orient"]      # fix orientation before metadata is removed
    geometry = resize_geometry(settings)
    if geometry:
        args += ["-resize", geometry]
    if settings.strip_metadata:
        # Remove EXIF/XMP/IPTC/comments but keep the colour profile.
        args += ["+profile", "!icc,*", "+set", "comment"]

    if fmt == "jpeg":
        args += ["-background", "white", "-alpha", "remove", "-alpha", "off",
                 "-quality", str(settings.quality), "-interlace", "Plane"]
        if settings.quality < 90:
            args += ["-sampling-factor", "4:2:0"]
    elif intermediate:
        args += ["-depth", "8", "-define", "png:compression-level=1"]
    elif fmt == "png":
        args += ["-define", "png:compression-level=9"]
        if settings.strip_metadata:
            args += ["-define", "png:exclude-chunks=date,time"]
    elif fmt == "webp":
        args += ["-quality", str(settings.quality),
                 "-define", f"webp:method={6 if settings.high_effort else 4}",
                 "-define", "webp:alpha-quality=100"]
    elif fmt == "avif":
        args += ["-quality", str(settings.quality),
                 "-define", f"heic:speed={4 if settings.high_effort else 7}"]
    elif settings.output_format == "original":
        # Other formats (GIF, TIFF, ...) kept as-is; quality where meaningful.
        args += ["-quality", str(settings.quality)]

    coder = OUTPUT_FORMATS[fmt][0] if fmt else source.suffix.lstrip(".").upper()
    args += [f"{coder}:{dest}"]
    return args


def cwebp_command(magick: Magick, png: str, dest: str, settings: Settings) -> list[str]:
    return [
        magick.cwebp, "-quiet",
        "-q", str(settings.quality),
        "-m", "6" if settings.high_effort else "4",
        "-alpha_q", "100",
        "-metadata", "icc" if settings.strip_metadata else "all",
        png, "-o", dest,
    ]


def output_name(source: Path, settings: Settings) -> str:
    fmt = resolve_output_format(source, settings)
    ext = OUTPUT_FORMATS[fmt][1] if fmt else source.suffix.lower()
    return source.stem + ext


def reserve_unique_path(directory: Path, filename: str) -> Path:
    """Atomically create an empty placeholder with a unique name.

    Returns e.g. "photo.webp", then "photo (1).webp", "photo (2).webp", ...
    Existing files are never touched.
    """
    stem, ext = os.path.splitext(filename)
    counter = 0
    while True:
        name = filename if counter == 0 else f"{stem} ({counter}){ext}"
        candidate = directory / name
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            counter += 1
            if counter > 10000:
                raise MagickError("Could not find a free output filename")
            continue
        os.close(fd)
        return candidate


def process_image(
    magick: Magick,
    source: str | os.PathLike,
    settings: Settings,
    cancel_event: threading.Event | None = None,
) -> ProcessResult:
    """Process one image. Never modifies the source; never overwrites files.

    The image is written to a hidden temporary file in the destination folder
    and only renamed into place after ImageMagick succeeds, so a failure or
    cancellation never leaves a partial output file behind.
    """
    source = Path(source).absolute()
    result = ProcessResult(source=source)
    tmp_path: str | None = None
    try:
        settings.validate()
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        if not source.is_file():
            raise MagickError("File not found")
        result.original_size = source.stat().st_size
        if not os.access(source, os.R_OK):
            raise MagickError("Permission denied reading file")
        check_size(magick.identify(source))
        if settings.output_format != "original" and \
                OUTPUT_FORMATS[settings.output_format][0] not in magick.writable_formats():
            raise MagickError(
                f"This ImageMagick build cannot write {settings.output_format.upper()}")

        out_dir = output_dir_for(source, settings)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MagickError(f"Cannot create output folder: {exc.strerror or exc}")
        if not os.access(out_dir, os.W_OK):
            raise MagickError(f"Output folder is not writable: {out_dir}")

        fd, tmp_path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".tmp", dir=out_dir)
        os.close(fd)

        if resolve_output_format(source, settings) == "webp" and magick.use_cwebp():
            fd, png_path = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".png", dir=out_dir)
            os.close(fd)
            try:
                _run(build_command(magick, source, png_path, settings, intermediate=True),
                     cancel_event)
                _run(cwebp_command(magick, png_path, tmp_path, settings), cancel_event)
            finally:
                os.unlink(png_path)
        else:
            _run(build_command(magick, source, tmp_path, settings), cancel_event)

        if os.path.getsize(tmp_path) == 0:
            raise MagickError("ImageMagick produced an empty file")

        # mkstemp creates files as 0600; give results normal permissions.
        os.chmod(tmp_path, OUTPUT_FILE_MODE)
        final = reserve_unique_path(out_dir, output_name(source, settings))
        try:
            os.replace(tmp_path, final)  # replaces only our own placeholder
        except OSError:
            final.unlink(missing_ok=True)
            raise
        tmp_path = None
        result.output = final
        result.output_size = final.stat().st_size
        result.ok = True
    except Cancelled:
        result.error = "Cancelled"
    except (MagickError, ValueError) as exc:
        result.error = str(exc)
    except OSError as exc:
        result.error = exc.strerror or str(exc)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return result


def _run(cmd: list[str], cancel_event: threading.Event | None) -> None:
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, **POPEN_FLAGS)
    except OSError as exc:
        raise MagickError(f"Could not run ImageMagick: {exc}")
    # Read stderr in a thread so a chatty process can never block on a full pipe.
    stderr_chunks: list[bytes] = []
    reader = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()),
                              daemon=True)
    reader.start()
    try:
        while True:
            try:
                proc.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    proc.wait()
                    raise Cancelled()
    finally:
        reader.join(timeout=5)
    if proc.returncode != 0:
        err = b"".join(stderr_chunks).decode("utf-8", "replace")
        raise MagickError(_clean_error(err) or f"ImageMagick failed (exit code {proc.returncode})")


def _clean_error(stderr: str) -> str:
    """Return the first meaningful ImageMagick error line, without noise."""
    for line in stderr.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # "magick: no decode delegate for this image format `JPG' @ error/..."
        if line.startswith(("magick:", "magick.exe:", "convert:", "identify:")):
            line = line.split(":", 1)[1].strip()
        line = line.split(" @ ")[0]
        # Drop the quoted file path ImageMagick appends; the UI already shows it.
        # IM6 quotes it `like this', IM7 'like this'; paths may contain quotes.
        line = re.sub(r"\s*[`'](?:[A-Z]+:)?(?:/|[A-Za-z]:\\|\\\\).*?'(?=[:\s]|$)", "", line)
        return line[:300]
    return ""


def thumbnail(magick: Magick, source: str | os.PathLike, size: int, dest: str) -> None:
    """Render a PNG preview (used for formats GdkPixbuf cannot load)."""
    source = Path(source).absolute()
    check_size(magick.identify(source))
    cmd = magick.convert_cmd + [
        _input(source), "-delete", "1--1", "-auto-orient",
        "-thumbnail", f"{size}x{size}>", f"PNG:{dest}",
    ]
    _run(cmd, None)


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


__all__ = [
    "Magick", "MagickError", "Settings", "ImageInfo", "ProcessResult",
    "OUTPUT_FORMATS", "SUPPORTED_EXTENSIONS", "QUALITY_PRESETS", "DEFAULT_QUALITY",
    "MAX_PIXELS", "sniff_coder", "check_size",
    "web_preset", "scan_folder", "is_supported", "process_image", "build_command",
    "resize_geometry", "reserve_unique_path", "thumbnail", "human_size",
]
