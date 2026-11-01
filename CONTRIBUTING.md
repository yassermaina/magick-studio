# Contributing to Magick Studio

Thanks for your interest in improving Magick Studio! The project is deliberately
small: a shared backend, one interface per platform, and a test suite. Please keep it that way.

## Getting started

Install the runtime dependencies (see the README), plus the test tools:

```bash
# Fedora
sudo dnf install python3-pytest libwebp-tools
# Debian / Ubuntu
sudo apt install python3-pytest webp xvfb
```

Run the app straight from the source tree:

```bash
python3 main.py
```

## Code layout

| File | Purpose |
| --- | --- |
| `image_processor.py` | All ImageMagick logic, shared by both platforms. No GUI imports, so it is easy to test. |
| `main.py` | Linux interface (GTK 4 / Libadwaita). |
| `install.sh`, `uninstall.sh` | Linux per-user installation into `~/.local`. |
| `windows/magick_studio_win.py` | Windows interface (PySide6). |
| `windows/build.ps1`, `windows/installer.iss` | Windows build and installer. See [`windows/README.md`](windows/README.md). |
| `tests/` | Backend tests and the GTK test (`test_gui.py` needs a display). |
| `windows/tests/` | Qt interface tests. |

## Guidelines

- Call external programs with argument lists. Never use `shell=True`.
- Never modify or overwrite user files. New output must go through `process_image()`,
  which writes atomically and picks collision-free names.
- Keep long-running work off the GUI thread. In GTK, update widgets with `GLib.idle_add`.
  In Qt, use signals.
- Put image logic in `image_processor.py`, not in an interface, so both platforms get it.
- Pass `**POPEN_FLAGS` to every `subprocess` call, so no console windows appear on Windows.
- Support ImageMagick 6 and 7, and GTK 4.14 / Libadwaita 1.5 (Ubuntu 24.04).
- Don't make Linux depend on PySide6, and don't change one platform's interface for the other.
- Add or update tests for every behaviour change.

## Running the tests

```bash
python3 -m pytest tests                    # on a desktop session
xvfb-run -a python3 -m pytest tests        # headless (GUI test runs on Xvfb)
```

GUI tests are skipped automatically when no display is available. Tests that need
`cwebp` are skipped when it is not installed.

## Testing other distributions

With Podman or Docker you can run the suite in a clean container, for example:

```bash
podman run --rm -it -v "$PWD":/src:ro ubuntu:24.04 bash
```

Inside it, create a normal user with sudo, copy `/src`, and run `./install.sh --yes`.

## Windows

See [`windows/README.md`](windows/README.md) for building, running and testing on Windows.
If you don't have Windows, the Qt tests also run on Linux in a virtual environment:

```bash
python3 -m venv .venv && .venv/bin/pip install PySide6-Essentials pytest
.venv/bin/python -m pytest windows/tests        # runs offscreen
```

The Windows workflow runs on every pull request and is the real check.

## Pull requests

- Keep changes focused and describe how you tested them (OS or distribution, and versions).
- Make sure `python3 -m pytest tests` and `bash -n install.sh uninstall.sh` pass.
