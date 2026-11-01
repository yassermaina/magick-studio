"""Windows CI checks for the packaged Magick Studio application.

Usage (on Windows, from the repository root):
  python windows/ci/winci.py samples DIR
  python windows/ci/winci.py bundle-check APP_DIR
  python windows/ci/winci.py smoke APP_EXE SAMPLES_DIR WORK_DIR
  python windows/ci/winci.py gui-check APP_EXE SAMPLES_DIR REPORT.json
  python windows/ci/winci.py console-check APP_EXE SAMPLES_DIR WORK_DIR
  python windows/ci/winci.py screenshot APP_EXE SAMPLES_DIR OUT.png [--dark]
  python windows/ci/winci.py install-test SETUP_EXE WORK_DIR

Every command prints what it verified and exits non-zero on failure.
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import image_processor as ip  # noqa: E402

APP_NAME = "Magick Studio"
APP_ID = "{6F3C2A8E-4B1D-4E7A-9C55-2D8F1B7E9A31}_is1"
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def fail(msg: str):
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str):
    print(f"ok: {msg}")


def clean_env() -> dict:
    """Environment without Python, ImageMagick or developer tools on PATH."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"PATH", "PYTHONPATH", "PYTHONHOME", "MAGICK_STUDIO_MAGICK",
                                "MAGICK_HOME", "MAGICK_CONFIGURE_PATH"}}
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = f"{windir}\\System32;{windir}"
    return env


# ------------------------------------------------------------------ windows

def visible_windows() -> list[tuple[int, int, str, str]]:
    """(hwnd, pid, class, title) for every visible top-level window."""
    result = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, 256)
            result.append((hwnd, pid.value, cls.value, title.value))
        return True

    user32.EnumWindows(callback, 0)
    return result


def console_windows() -> set[int]:
    return {w[0] for w in visible_windows() if w[2] in CONSOLE_CLASSES}


def wait_for_window(pid: int, timeout: float = 60) -> int:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for hwnd, wpid, _cls, title in visible_windows():
            if wpid == pid and title == APP_NAME:
                return hwnd
        time.sleep(0.25)
    fail(f"no visible '{APP_NAME}' window for pid {pid}")


# ----------------------------------------------------------------- commands

def cmd_samples(out_dir: str):
    """Create test images with tricky names using the bundled ImageMagick."""
    m = ip.Magick.detect()
    out = Path(out_dir)
    (out / "Unicode é 写真").mkdir(parents=True, exist_ok=True)
    jobs = {
        "beach photo.jpg": ["-size", "2400x1600", "plasma:fractal"],
        "logo & icon (1).png": ["-size", "300x300", "xc:none", "-fill", "#e33",
                                "-draw", "circle 150,150 150,30"],
        "Unicode é 写真/café ünï.webp": ["-size", "1200x800", "gradient:navy-orange"],
    }
    for name, spec in jobs.items():
        subprocess.run(m.convert_cmd + spec + [str(out / name)], check=True, **ip.POPEN_FLAGS)
    ok(f"created {len(jobs)} sample images in {out}")


def cmd_bundle_check(app_dir: str):
    app = Path(app_dir)
    internal = app / "_internal"
    required = {
        "application": app / "MagickStudio.exe",
        "ImageMagick": internal / "imagemagick" / "magick.exe",
        "ImageMagick license": internal / "imagemagick" / "LICENSE.txt",
        "ImageMagick notice": internal / "imagemagick" / "NOTICE.txt",
        "ImageMagick policy": internal / "imagemagick" / "policy.xml",
        "LGPL text": internal / "licenses" / "LGPL-3.0.txt",
        "Python license": internal / "licenses" / "Python-LICENSE.txt",
        "app license": app / "LICENSE.txt",
        "third-party notices": app / "THIRD-PARTY-NOTICES.txt",
        "icon": internal / "assets" / "icon.ico",
    }
    for label, path in required.items():
        if not path.is_file():
            fail(f"missing {label}: {path}")
    patterns = {
        "Python DLL": "python3*.dll",
        "Qt Core": "Qt6Core.dll",
        "Qt Gui": "Qt6Gui.dll",
        "Qt Widgets": "Qt6Widgets.dll",
        "Qt Windows platform plugin": "qwindows.dll",
        "Qt JPEG plugin": "qjpeg.dll",
        "Qt WebP plugin": "qwebp.dll",
        "Qt ICO plugin": "qico.dll",
        "MSVC runtime": "vcruntime140.dll",
        "MSVC runtime (x64)": "vcruntime140_1.dll",
        "MSVC C++ runtime": "msvcp140.dll",
    }
    for label, pattern in patterns.items():
        hits = glob.glob(str(internal / "**" / pattern), recursive=True)
        if not hits:
            fail(f"missing {label} ({pattern}) in bundle")
        ok(f"{label}: {Path(hits[0]).relative_to(app)}")
    size = sum(f.stat().st_size for f in app.rglob("*") if f.is_file())
    ok(f"all required files present; bundle size {size / 1048576:.1f} MiB")

    magick = str(internal / "imagemagick" / "magick.exe")
    formats = ip.parse_writable_formats(subprocess.run(
        [magick, "-list", "format"], capture_output=True, encoding="utf-8",
        errors="replace", env=clean_env(), **ip.POPEN_FLAGS).stdout)
    for f in ("JPEG", "PNG", "WEBP"):
        if f not in formats:
            fail(f"bundled ImageMagick cannot write {f}")
    ok(f"bundled ImageMagick writes JPEG/PNG/WEBP; AVIF={'AVIF' in formats}")


def run_smoke(app_exe: str, samples: str, work: Path, out: Path | None = None) -> dict:
    out = out or work / "smoke output"
    report = work / "smoke-report.json"
    shutil.rmtree(out, ignore_errors=True)
    report.unlink(missing_ok=True)
    proc = subprocess.run([app_exe, "--smoke-test", samples, str(out), str(report)],
                          env=clean_env(), timeout=600)
    if not report.exists():
        fail(f"smoke test wrote no report (exit code {proc.returncode})")
    data = json.loads(report.read_text(encoding="utf-8"))
    data["exit_code"] = proc.returncode
    return data


def cmd_smoke(app_exe: str, samples: str, work_dir: str):
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    before = {p: p.stat().st_mtime_ns for p in Path(samples).rglob("*") if p.is_file()}
    data = run_smoke(app_exe, samples, work)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    if data["exit_code"] != 0 or not data["ok"]:
        fail("packaged smoke test reported failures")
    app_dir = Path(app_exe).resolve().parent
    if not Path(data["magick"]).resolve().is_relative_to(app_dir):
        fail(f"app used a non-bundled ImageMagick: {data['magick']}")
    ok(f"used bundled ImageMagick {data['magick']}")
    for r in data["results"]:
        if r["format"] != "WEBP" or max(r["width"], r["height"]) > 1600:
            fail(f"unexpected output {r}")
    ok(f"{len(data['results'])} images converted to WebP within 1600 px")
    after = {p: p.stat().st_mtime_ns for p in Path(samples).rglob("*") if p.is_file()}
    if before != after:
        fail("source images were modified")
    leftovers = [p for p in (work / "smoke output").rglob(ip.TEMP_PREFIX + "*")]
    if leftovers:
        fail(f"temporary files left behind: {leftovers}")
    ok("originals untouched, no temporary files left")


def run_gui_check(app_exe: str, samples: str, report: Path, expected: int):
    """Start the windowed app with images, let it write a startup report and quit."""
    report.unlink(missing_ok=True)
    proc = subprocess.run([app_exe, "--gui-check", str(report), samples],
                          env=clean_env(), timeout=180)
    if not report.exists():
        fail(f"GUI check wrote no report (exit code {proc.returncode})")
    gui = json.loads(report.read_text(encoding="utf-8"))
    print(json.dumps(gui, indent=2, ensure_ascii=False))
    if (proc.returncode != 0 or not gui["visible"] or gui["platform"] != "windows"
            or gui["valid"] != expected or gui["thumbnails"] != expected):
        fail("GUI startup check failed")
    if not Path(gui["magick"]).resolve().is_relative_to(Path(app_exe).resolve().parent):
        fail(f"GUI used a non-bundled ImageMagick: {gui['magick']}")
    ok(f"window opened and loaded {expected} images: {gui['status']} "
       f"(platform {gui['platform']}, style {gui['style']})")


def cmd_gui_check(app_exe: str, samples: str, report: str):
    count = sum(1 for p in Path(samples).rglob("*") if p.suffix.lower() in ip.SUPPORTED_EXTENSIONS)
    run_gui_check(app_exe, samples, Path(report), count)


def cmd_console_check(app_exe: str, samples: str, work_dir: str):
    """Detect console windows while the windowed app runs ImageMagick."""
    work = Path(work_dir)
    heavy = work / "console-samples"
    heavy.mkdir(parents=True, exist_ok=True)
    m = ip.Magick.detect()
    for i in range(4):
        subprocess.run(m.convert_cmd + ["-size", "2500x2500", "xc:gray", "+noise", "Random",
                                        str(heavy / f"noise {i}.png")], check=True,
                       **ip.POPEN_FLAGS)

    def watch(cmd, env=None) -> tuple[set[int], float]:
        baseline = console_windows()
        seen: set[int] = set()
        start = time.monotonic()
        proc = subprocess.Popen(cmd, env=env)
        while proc.poll() is None:
            seen |= console_windows() - baseline
            time.sleep(0.02)
        seen |= console_windows() - baseline
        return seen, time.monotonic() - start

    # Negative control: a GUI-subsystem Python starting a console program
    # WITHOUT CREATE_NO_WINDOW must produce a console window we can detect.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    control, _ = watch([str(pythonw), "-c",
                        "import subprocess; subprocess.run(['cmd.exe', '/c', "
                        "'ping -n 4 127.0.0.1 >nul'])"])
    detector_works = bool(control)
    print(f"negative control: {len(control)} console window(s) detected")

    seen, elapsed = watch([app_exe, "--smoke-test", str(heavy), str(work / "console out"),
                           str(work / "console-report.json")], env=clean_env())
    print(f"app processed {len(list(heavy.iterdir()))} images in {elapsed:.1f}s; "
          f"console windows seen: {len(seen)}")
    if seen:
        fail("console windows appeared while the packaged app was processing")
    if not detector_works:
        print("INCONCLUSIVE: the negative control produced no visible console window on "
              "this runner, so the check cannot prove absence. Verify manually.")
        sys.exit(2)
    ok("no console windows while processing (detector verified by negative control)")


def cmd_screenshot(app_exe: str, samples: str, out_png: str, dark: bool = False):
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
    winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 0 if dark else 1)
    proc = subprocess.Popen([app_exe, samples], env=clean_env())
    try:
        hwnd = wait_for_window(proc.pid)
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        w, h = min(1100, sw), min(720, sh - 48)
        user32.ShowWindow(hwnd, 9)                       # SW_RESTORE
        user32.SetWindowPos(hwnd, 0, (sw - w) // 2, max(0, (sh - 48 - h) // 2), w, h, 0x0040)
        user32.SetForegroundWindow(hwnd)
        time.sleep(8)                                    # thumbnails and preview load
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance() or QGuiApplication([])
        shot = app.primaryScreen().grabWindow(0, rect.left, rect.top,
                                              rect.right - rect.left, rect.bottom - rect.top)
        if shot.isNull() or not shot.save(out_png):
            fail("could not capture the window")
        ok(f"saved {out_png} ({shot.width()}x{shot.height()}, screen {sw}x{sh}, "
           f"{'dark' if dark else 'light'})")
        user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE
        proc.wait(timeout=30)
        ok(f"app closed cleanly (exit code {proc.returncode})")
    finally:
        if proc.poll() is None:
            proc.kill()
        winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1)


def cmd_install_test(setup_exe: str, work_dir: str):
    import winreg
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    local = Path(os.environ["LOCALAPPDATA"])
    install_dir = local / "Programs" / APP_NAME
    exe = install_dir / "MagickStudio.exe"
    start_menu = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs"
    shortcut = start_menu / f"{APP_NAME}.lnk"
    uninstall_key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"

    def install(log_name: str):
        r = subprocess.run([setup_exe, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                            f"/LOG={work / log_name}"], timeout=600)
        if r.returncode != 0:
            fail(f"installer exit code {r.returncode}")

    def reg_value(hive, name):
        try:
            with winreg.OpenKey(hive, uninstall_key) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None

    # User images that must survive install, upgrade and uninstall.
    user_dir = Path(os.environ["USERPROFILE"]) / "Pictures" / "Magick Studio CI"
    shutil.rmtree(user_dir, ignore_errors=True)
    shutil.rmtree(user_dir.with_name("Magick Studio CI output"), ignore_errors=True)
    shutil.copytree(work / "samples", user_dir)
    user_files = {p: p.read_bytes() for p in user_dir.rglob("*") if p.is_file()}

    install("install.log")
    if not exe.is_file():
        fail(f"{exe} not installed")
    ok(f"installed per-user into {install_dir}")
    if not shortcut.is_file():
        fail(f"Start Menu shortcut missing: {shortcut}")
    target = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut}').TargetPath"],
        capture_output=True, text=True).stdout.strip()
    if Path(target) != exe:
        fail(f"shortcut points to {target!r}, expected {exe}")
    ok(f"Start Menu shortcut -> {target}")
    version = reg_value(winreg.HKEY_CURRENT_USER, "DisplayVersion")
    if version != ip.APP_VERSION:
        fail(f"uninstall entry version {version!r}")
    if reg_value(winreg.HKEY_LOCAL_MACHINE, "DisplayVersion") is not None:
        fail("installer wrote a machine-wide (HKLM) uninstall entry")
    ok(f"per-user uninstall entry (HKCU) with version {version}; nothing in HKLM")
    if (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / APP_NAME).exists():
        fail("files were installed into Program Files")

    user_out = user_dir.with_name("Magick Studio CI output")
    data = run_smoke(str(exe), str(user_dir), work, user_out)
    if data["exit_code"] != 0 or not data["ok"]:
        fail(f"installed app smoke test failed: {data}")
    if not Path(data["magick"]).resolve().is_relative_to(install_dir):
        fail(f"installed app used a non-bundled ImageMagick: {data['magick']}")
    ok(f"installed app converted {len(data['results'])} images with its bundled ImageMagick")

    run_gui_check(str(exe), str(user_dir), work / "installed-gui.json", len(user_files))

    # Upgrade / repair over an existing installation removes stale files.
    stale = install_dir / "_internal" / "stale-from-old-version.txt"
    stale.write_text("old")
    install("upgrade.log")
    if stale.exists():
        fail("stale file from previous version survived the upgrade")
    if not exe.is_file():
        fail("application missing after upgrade")
    ok("re-install over existing version succeeded and removed stale files")

    uninstaller = install_dir / "unins000.exe"
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                   timeout=300)
    end = time.monotonic() + 120             # the uninstaller relaunches itself from %TEMP%
    while (install_dir.exists() or shortcut.exists()) and time.monotonic() < end:
        time.sleep(1)
    if install_dir.exists():
        fail(f"install folder remains after uninstall: {list(install_dir.rglob('*'))[:10]}")
    if shortcut.exists():
        fail("Start Menu shortcut remains after uninstall")
    if reg_value(winreg.HKEY_CURRENT_USER, "DisplayVersion") is not None:
        fail("uninstall registry entry remains")
    ok("uninstall removed the app folder, shortcut and registry entry")
    for path, content in user_files.items():
        if not path.is_file() or path.read_bytes() != content:
            fail(f"user image changed or removed: {path}")
    if not any(user_out.iterdir()):
        fail("processed output folder was removed")
    ok(f"{len(user_files)} user images and the output folder are intact")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "samples":
        cmd_samples(*args)
    elif cmd == "bundle-check":
        cmd_bundle_check(*args)
    elif cmd == "smoke":
        cmd_smoke(*args)
    elif cmd == "gui-check":
        cmd_gui_check(*args)
    elif cmd == "console-check":
        cmd_console_check(*args)
    elif cmd == "screenshot":
        cmd_screenshot(args[0], args[1], args[2], dark="--dark" in args)
    elif cmd == "install-test":
        cmd_install_test(*args)
    else:
        fail(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
