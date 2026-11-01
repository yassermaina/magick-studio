# Contributing to Magick Studio

Thanks for your interest in improving Magick Studio! The project is deliberately
small: two Python modules, two shell scripts and a test suite. Please keep it that way.

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
| `image_processor.py` | All ImageMagick logic. No GTK imports, so it is easy to test. |
| `main.py` | GTK 4 / Libadwaita user interface. |
| `install.sh`, `uninstall.sh` | Per-user installation into `~/.local`. |
| `tests/` | pytest suite (`test_gui.py` needs a display). |

## Guidelines

- Call external programs with argument lists. Never use `shell=True`.
- Never modify or overwrite user files. New output must go through `process_image()`,
  which writes atomically and picks collision-free names.
- Keep long-running work off the GTK main thread. Update widgets with `GLib.idle_add`.
- Support both ImageMagick 6 and 7, and GTK 4.14 / Libadwaita 1.5 (Ubuntu 24.04).
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

## Pull requests

- Keep changes focused and describe how you tested them (distribution + versions).
- Make sure `python3 -m pytest tests` and `bash -n install.sh uninstall.sh` pass.
