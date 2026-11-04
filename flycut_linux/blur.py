"""Ask the compositor to blur what is behind a window, when it can.

KDE's KWindowSystem already speaks KWin's blur protocol on Wayland (and the
X11 hint) and re-applies it when the window's surface is recreated. It has no
Python bindings, so its two exported functions are called through ctypes.
Anything missing or mismatched simply leaves the window unblurred.
"""
import ctypes
import os
from pathlib import Path

import PySide6
from PySide6.QtGui import QRegion

_ENABLE = "_ZN14KWindowEffects16enableBlurBehindEP7QWindowbRK7QRegion"
_AVAILABLE = "_ZN14KWindowEffects17isEffectAvailableENS_6EffectE"
_BLUR_BEHIND = 7  # KWindowEffects::BlurBehind
_library = None
_status = None


def _load():
    global _library, _status
    if _status is not None:
        return _library
    _status = "unavailable: KWindowSystem (libKF6WindowSystem) not found"
    if os.environ.get("FLYCUT_NO_BLUR"):
        _status = "off (FLYCUT_NO_BLUR is set)"
        return None
    # A pip wheel of PySide6 ships its own Qt. Loading KDE's library would pull
    # in the system Qt as a second copy, so only use it with the distro Qt.
    if (Path(PySide6.__file__).parent / "Qt" / "lib").is_dir():
        _status = "unavailable: PySide6 uses a bundled Qt"
        return None
    try:
        library = ctypes.CDLL("libKF6WindowSystem.so.6")
        enable = getattr(library, _ENABLE)
        available = getattr(library, _AVAILABLE)
    except (OSError, AttributeError):
        return None
    enable.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p]
    enable.restype = None
    available.argtypes = [ctypes.c_int]
    available.restype = ctypes.c_bool
    _library = library
    _status = None
    return library


def _pointer(qt_object):
    import shiboken6
    return shiboken6.getCppPointer(qt_object)[0]


def available():
    """True when the running compositor offers blur behind windows."""
    global _status
    library = _load()
    if library is None:
        return False
    try:
        result = bool(getattr(library, _AVAILABLE)(_BLUR_BEHIND))
    except Exception:  # noqa: BLE001 - never let a visual effect break the app
        result = False
    if not result:
        _status = "unavailable: the compositor does not offer blur"
    return result


def status():
    """A short human-readable state for the settings window."""
    if available():
        return "available"
    return _status or "unavailable"


def set_blur(window, enabled, region=None):
    """Blur behind a top-level QWindow, limited to region when given.

    Returns True when the request was sent to the compositor.
    """
    if window is None or not available():
        return False
    region = region if region is not None else QRegion()
    try:
        getattr(_library, _ENABLE)(_pointer(window), bool(enabled), _pointer(region))
    except Exception:  # noqa: BLE001
        return False
    return True
