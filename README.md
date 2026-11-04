# Flycut Linux

A small clipboard manager for **KDE Plasma Wayland**, with Flycut's translucent,
single-clipping preview. The macOS application is independent and unchanged.
Text and image history stays on your machine. No network services are used.

## Arch Linux / CachyOS installation

From the repository checkout:

```sh
sudo pacman -S --needed base-devel python-build python-installer python-setuptools python-wheel
cd linux
makepkg -si
flycut-linux --settings
```

The PKGBUILD builds the local checkout, including local changes; it does not
fetch a release from the network. Run `makepkg -Csf` to rebuild after editing.
Install the resulting package on another machine with `sudo pacman -U
flycut-linux-0.1.0-8-any.pkg.tar.zst`. This is a checkout-local PKGBUILD, not an
AUR source package.

Runtime dependencies: Python 3.11+, PySide6, Qt's Wayland plugin, dbus-python,
PyGObject, **wl-clipboard 2.3+**, ydotool, xdg-desktop-portal and its KDE backend.
Optional: KWindowSystem (KF6) for background blur, already present on Plasma.
The application forces Qt's native Wayland backend; it does not use X11 or
XWayland. KDE must expose the data-control protocol and GlobalShortcuts portal
version 2 for capture and the shortcut editor respectively. These are available
on the Plasma 6.8 desktop used during development.

For source development with distro-installed dependencies:

```sh
cd linux
python -m flycut_linux.app --settings
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
# Optional private-bus portal integration (never uses your desktop portal):
PYTHONPATH=. FLYCUT_PRIVATE_TEST_BUS=1 dbus-run-session -- python tests/integration_portal.py
PYTHONPATH=. FLYCUT_PRIVATE_TEST_BUS=1 dbus-run-session -- python tests/integration_app.py
```

Install the package once before testing global shortcuts from source: the
desktop portal needs the installed `org.flycut.FlycutLinux.desktop` launcher
to identify the application.

## ydotool setup

`ydotoold` must already be running and allowed to access `/dev/uinput`. Flycut
runs as your regular user and never changes device permissions or starts a
privileged service. On Arch, inspect the packaged user service:

```sh
systemctl --user cat ydotool.service
systemctl --user enable --now ydotool.service
systemctl --user status ydotool.service
```

If it fails to open `/dev/uinput`, follow your distribution's device-access
policy for your user, then restart the service. Do not make the device or daemon
socket world-writable. Service changes are your explicit setup step, separate
from enabling Flycut's login autostart.

The default client socket is `$XDG_RUNTIME_DIR/.ydotool_socket`. For a daemon
using another path, export `YDOTOOL_SOCKET` into the graphical session before
starting Flycut. `ydotool debug` checks connectivity without sending keys.
Settings also reports daemon connectivity and required command availability.

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
  released, closes the preview, and sends Shift+Insert through ydotool after 150 ms.
  The delay is configurable. Enable **Type clipboard content** to use
  `ydotool type` instead of Shift+Insert, or disable automatic insertion to copy only.
  Typing uses zero key delay and zero hold time for maximum speed; the delay
  after closing the preview still allows focus to return safely.
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
is behind it (like the macOS bezel). On KDE this uses KWin's Blur effect through
KWindowSystem, so the effect must be enabled in System Settings → Desktop
Effects. Blur is skipped automatically on other compositors, when KWindowSystem
is missing, or when PySide6 bundles its own Qt (pip wheels); the preview then
stays plainly translucent. Turn it off with **Blur what is behind the preview**
in Settings, or set `FLYCUT_NO_BLUR=1`. Settings → Status shows whether blur is
available. Wayland
lets the compositor choose the final placement; the app requests a centered
500×320 window. Automatic pasting relies on KDE restoring the previously focused
app after the preview closes. In paste mode, targets must support Shift+Insert. Typing mode sends the text through
ydotool's keyboard simulation; character support and the resulting text depend
on ydotool and the active keyboard layout (ydotool 1.0.4 uses a US ASCII key map).
Use paste mode for Unicode text. There is no per-application mode detection. If focus remains inside Flycut,
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
and autostart, preview navigation, and paste sequencing with mocked processes.
The optional integration scripts use a private bus and fake portal; the full
app script also substitutes clipboard/input executables and uses temporary
history and settings. They never read the desktop clipboard or send real keys.

For desktop acceptance, verify hotkey approval/rebinding, translucency with and
without blur, capture from native Wayland applications, focus restoration and paste
into an editor and terminal, image copy from a screenshot tool or browser and paste into
an image-aware app, tray controls, restart persistence and login
autostart. Use disposable text and a test editor for paste checks.

Integration references: [GlobalShortcuts portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html),
[wl-clipboard](https://github.com/bugaevc/wl-clipboard),
[ydotool](https://github.com/ReimuNotMoe/ydotool).
