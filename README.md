# Flycut Linux

A small clipboard manager for **KDE Plasma Wayland**, with a translucent,
single-clipping history preview. Text and image history stays on your machine.
No network services are used.

Flycut Linux is inspired by [Flycut](https://github.com/TermiT/Flycut), the
macOS clipboard manager (itself based on
[Jumpcut](https://github.com/snark/jumpcut)). It is a separate Python/Qt
application written for Wayland and shares no code with the macOS app.

## Project layout

| Path | Contents |
|------|----------|
| `flycut_linux/app.py` | Entry point, tray, single-instance socket and focus handling |
| `flycut_linux/ui.py` | History preview and settings windows |
| `flycut_linux/capture.py` | `wl-paste` child that frames each copied entry |
| `flycut_linux/storage.py` | Private SQLite history, JSON settings and login autostart |
| `flycut_linux/portal.py` | GlobalShortcuts (history hotkey) and RemoteDesktop (paste keys) portal clients |
| `flycut_linux/processes.py` | Clipboard watcher and paste sequencing (`wl-copy`) |
| `flycut_linux/blur.py` | Optional KWin background blur through KWindowSystem |
| `flycut_linux/flycut-linux.svg` | Application icon |
| `packaging/` | Desktop launcher (`org.flycut.FlycutLinux.desktop`) |
| `tests/` | Unit tests and private-bus integration scripts |
| `PKGBUILD`, `pyproject.toml` | Arch package recipe and Python package metadata |

## Arch Linux / CachyOS installation

From the repository checkout:

```sh
sudo pacman -S --needed base-devel python-build python-installer python-setuptools python-wheel
makepkg -si
flycut-linux --settings
```

The PKGBUILD builds the local checkout, including local changes; it does not
fetch a release from the network. Run `makepkg -Csf` to rebuild after editing.
Install the resulting package on another machine with `sudo pacman -U
flycut-linux-0.1.0-8-any.pkg.tar.zst`. This is a checkout-local PKGBUILD, not an
AUR source package.

Runtime dependencies: Python 3.11+, PySide6, Qt's Wayland plugin, dbus-python,
PyGObject, **wl-clipboard 2.3+**, xdg-desktop-portal and its KDE backend.
Optional: KWindowSystem (KF6) for background blur, already present on Plasma.
The application forces Qt's native Wayland backend; it does not use X11 or
XWayland. KDE must expose the data-control protocol, GlobalShortcuts portal
version 2 and the RemoteDesktop portal for capture, the shortcut editor and
automatic paste respectively. These are available
on the Plasma 6.8 desktop used during development.

For source development with distro-installed dependencies:

```sh
python -m flycut_linux.app --settings
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
# Optional private-bus portal integration (never uses your desktop portal):
PYTHONPATH=. FLYCUT_PRIVATE_TEST_BUS=1 dbus-run-session -- python tests/integration_portal.py
PYTHONPATH=. FLYCUT_PRIVATE_TEST_BUS=1 dbus-run-session -- python tests/integration_app.py
```

Install the package once before testing global shortcuts from source: the
desktop portal needs the installed `org.flycut.FlycutLinux.desktop` launcher
to identify the application.

## Paste permission

Automatic paste sends keys through KDE's RemoteDesktop portal, which needs no
daemon or `/dev/uinput` access. The first automatic paste asks KDE for
keyboard control and KDE shows a permission prompt once. Flycut asks for
the keyboard only (no pointer or screen). After you allow it, KDE returns a
restore token that Flycut keeps in its private config directory, so later
sessions and restarts don't ask again. If you deny the prompt, the entry stays
copied and the next selection asks again.

**Settings → Reset paste permission** forgets the saved token so KDE asks
again. Settings → Status shows the current permission state. If you used
ydotool with an earlier version, you can disable its service
(`systemctl --user disable --now ydotool.service`); Flycut no longer uses it.

## Use

- Start **Flycut Linux** from the application launcher, or run `flycut-linux` to
  start quietly in the tray. Subsequent invocations open the existing instance.
- The first run asks KDE to assign the history hotkey, requesting **Ctrl+Alt+V**.
  KDE owns the final binding. Settings shows the assigned shortcut; **Change
  hotkey** opens KDE's portal editor. If Klipper or another app owns a shortcut,
  choose a free one in that editor.
- Open history, browse with arrows or J/K, Home/End or the scroll wheel. Enter
  or double-click selects an entry; Delete removes it; Escape cancels.
  Switching to another window dismisses history and cancels pending insertion.
- Selection copies the exact text, waits for selection keys/buttons to be
  released, closes the preview, and sends Shift+Insert through the RemoteDesktop
  portal after 150 ms. The delay is configurable. Enable **Type clipboard content**
  to type the text instead of pressing Shift+Insert, or disable automatic insertion
  to copy only. Keys are sent without intentional delays; the delay after closing
  the preview still allows focus to return safely.
  The selected text stays on the clipboard.
- Copied images appear in history as a scaled preview with their format,
  dimensions and size. Selecting one puts the original image back on the
  clipboard in its original format and pastes it with Shift+Insert. Images are never
  typed, even with **Type clipboard content** enabled. Turn off **Save copied
  images** in Settings to keep history text-only.
- The tray offers settings, pause capture, clear history and quit. Closing a
  window keeps the background process running. Start-at-login is opt-in.
- `flycut-linux --show` and `flycut-linux --settings` contact the existing
  instance through a private local socket.

The preview is translucent and, where the compositor supports it, blurs what
is behind it. On KDE this uses KWin's Blur effect through
KWindowSystem, so the effect must be enabled in System Settings → Desktop
Effects. Blur is skipped automatically on other compositors, when KWindowSystem
is missing, or when PySide6 bundles its own Qt (pip wheels); the preview then
stays plainly translucent. Turn it off with **Blur what is behind the preview**
in Settings, or set `FLYCUT_NO_BLUR=1`. Settings → Status shows whether blur is
available. Wayland
lets the compositor choose the final placement; the app requests a centered
500×320 window. Automatic pasting relies on KDE restoring the previously focused
app after the preview closes. In paste mode, targets must support Shift+Insert. Typing mode sends each character as a keysym, which KDE maps through your
active keyboard layout; characters that layout cannot produce may be skipped.
Use paste mode for arbitrary Unicode text. There is no per-application mode detection. If focus remains inside Flycut,
input is held back and eventually cancelled. Do not switch target windows while
a paste is pending.

Regular text and images are collected. Text entries over 1 MiB, empty
selections, invalid UTF-8 and NUL-containing text are skipped. Images must be
PNG, JPEG, GIF, BMP or WebP and at most 32 MiB each; other formats are skipped.
When a copy offers both plain text and an image (for example spreadsheet cells
with a rendered picture), only the text is kept. Whitespace and trailing
newlines are preserved. Entries marked sensitive by wl-clipboard are ignored;
applications that do not mark secrets can still place them in saved history.
Primary/middle-click selection is not tracked.

## Local data and removal

- History: `${XDG_DATA_HOME:-~/.local/share}/flycut-linux/history.sqlite3`
- Settings: `${XDG_CONFIG_HOME:-~/.config}/flycut-linux/settings.json`
- Paste permission token: `${XDG_CONFIG_HOME:-~/.config}/flycut-linux/remote-desktop-token`
- Login entry: `${XDG_CONFIG_HOME:-~/.config}/autostart/org.flycut.FlycutLinux.desktop`

Directories and saved history/settings are private to your user. History is
unencrypted, bounded to 100 entries by default (configurable). Saved images
also share a 256 MiB total; the oldest images are dropped beyond that.
Histories from earlier versions are upgraded automatically on first start. Clear History
removes Flycut's saved entries and leaves the system clipboard alone. Klipper
has its own separate history and settings.

To uninstall, disable Flycut's start-at-login option, quit it from the tray, and
run `sudo pacman -R flycut-linux`. The package manager preserves your personal
history and settings. Remove the directories above yourself if you want to
permanently discard them.

## Verification

The automated suite covers exact capture framing, sensitive/binary/oversized
input, image capture, preview and paste, history upgrades, private persistent storage, deduplication and history limits, settings
and autostart, preview navigation, and paste sequencing with a mocked keyboard.
The optional integration scripts use a private bus and fake GlobalShortcuts and
RemoteDesktop portals; the full app script also substitutes clipboard executables
and uses temporary history and settings. They never read the desktop clipboard or send real keys.

For desktop acceptance, verify hotkey approval/rebinding, translucency with and
without blur, capture from native Wayland applications, focus restoration and paste
into an editor and terminal, image copy from a screenshot tool or browser and paste into
an image-aware app, tray controls, restart persistence and login
autostart. Use disposable text and a test editor for paste checks.

Integration references: [GlobalShortcuts portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html),
[RemoteDesktop portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html),
[wl-clipboard](https://github.com/bugaevc/wl-clipboard).
