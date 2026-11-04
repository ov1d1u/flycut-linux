"""Asynchronous desktop portal integration on a dedicated GLib/D-Bus thread."""
import threading
import uuid
from PySide6.QtCore import QObject, Signal
from dbus.mainloop.glib import threads_init

# Initialize before the application's portal thread (and Qt event loop) starts.
threads_init()

SERVICE = "org.freedesktop.portal.Desktop"
PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.GlobalShortcuts"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
REQUEST = "org.freedesktop.portal.Request"
SESSION = "org.freedesktop.portal.Session"
APP_ID = "org.flycut.FlycutLinux"

KEYBOARD = 1
PERSIST_UNTIL_REVOKED = 2
# Events sent before waiting for a reply. D-Bus keeps them in order.
BATCH = 64


class PortalThread:
    """One private portal connection and GLib loop shared by all portal clients.

    dbus-python's GLib integration only drives GLib's default main context,
    and only one thread can run that context, so clients share this thread.
    """

    def __init__(self):
        self.clients = []
        self.loop = None
        self.bus = None
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def add(self, client):
        self.clients.append(client)

    def start(self):
        if not self.thread.is_alive() and not self.stopping:
            try:
                self.thread.start()
            except RuntimeError:
                pass  # Already started and finished.

    def run(self):
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
            self.dbus = dbus
            self.GLib = GLib
            # Only this thread uses D-Bus. Qt receives queued signals.
            self.loop = GLib.MainLoop()
            self.bus = dbus.SessionBus(private=True, mainloop=DBusGMainLoop())
            # libdbus otherwise calls exit(1) when this connection's Disconnected
            # message is dispatched, including after our own close().
            self.bus.set_exit_on_disconnect(False)
            self.proxy = self.bus.get_object(SERVICE, PATH, introspect=False)
            # CLI launches also need a stable portal identity, independent of
            # whether Plasma started them in an application systemd scope.
            dbus.Interface(self.proxy, "org.freedesktop.host.portal.Registry").Register(
                APP_ID, dbus.Dictionary({}, signature="sv"), timeout=5)
        except Exception as exc:
            for client in self.clients:
                client.unavailable(exc)
            return
        for client in self.clients:
            try:
                client.attach(self)
            except Exception as exc:
                client.unavailable(exc)
        if not self.stopping:
            self.loop.run()
        for client in self.clients:
            try:
                client.detach()
            except Exception:
                pass
        self.bus.close()

    def interface(self, name):
        return self.dbus.Interface(self.proxy, name)

    def idle(self, function):
        """Run function on the portal thread; dropped if the portal is not running."""
        if self.loop and not self.stopping:
            self.GLib.idle_add(lambda: (function(), False)[1])
            return True
        return False

    def request(self, interface, method, callback, failed, options, *args):
        """Call a portal method that answers through a Request object's Response signal."""
        token = "flycut_" + uuid.uuid4().hex
        options = dict(options, handle_token=token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        holder = {}

        def response(code, results):
            holder["match"].remove()
            if code:
                failed(int(code), None)
            else:
                callback(results)

        def error(exc):
            holder["match"].remove()
            failed(None, exc)

        holder["match"] = self.bus.add_signal_receiver(response, signal_name="Response", dbus_interface=REQUEST, path=path)
        getattr(self.interface(interface), method)(*args, self.dbus.Dictionary(options, signature="sv"),
                                                   reply_handler=lambda _path: None, error_handler=error)

    def close_session(self, session):
        proxy = self.bus.get_object(SERVICE, session, introspect=False)
        self.dbus.Interface(proxy, SESSION).Close(timeout=2)

    def stop(self):
        self.stopping = True
        if self.loop:
            self.loop.quit()
        if self.thread.is_alive():
            self.thread.join(timeout=3)


class PortalClient(QObject):
    """Base for objects that live on a PortalThread. Creates one if none is given."""

    def __init__(self, portal=None, parent=None):
        super().__init__(parent)
        self.portal = portal or PortalThread()
        self.portal.add(self)

    @property
    def thread(self):
        return self.portal.thread

    def start(self):
        self.portal.start()

    def stop(self):
        self.portal.stop()

    def attach(self, portal):
        pass

    def detach(self):
        pass

    def unavailable(self, exc):
        pass


class Shortcuts(PortalClient):
    activated = Signal(str)
    released = Signal()
    binding = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None, portal=None):
        super().__init__(portal, parent)
        self.session = None

    def attach(self, portal):
        self.dbus = portal.dbus
        self.interface = portal.interface(IFACE)
        portal.bus.add_signal_receiver(self.on_activated, signal_name="Activated", dbus_interface=IFACE, path=PATH)
        portal.bus.add_signal_receiver(self.on_released, signal_name="Deactivated", dbus_interface=IFACE, path=PATH)
        portal.bus.add_signal_receiver(self.on_changed, signal_name="ShortcutsChanged", dbus_interface=IFACE, path=PATH)
        self.request("CreateSession", self.created, {"session_handle_token": "flycut_" + uuid.uuid4().hex})

    def detach(self):
        if self.session:
            self.portal.close_session(self.session)

    def unavailable(self, exc):
        self.error.emit(f"Global shortcut unavailable: {exc}. Use the tray or flycut-linux --show.")

    def request(self, method, callback, options, *args):
        def failed(code, exc):
            if exc is not None:
                self.error.emit(f"Shortcut portal failed: {exc}")
            else:
                self.error.emit("Global shortcut request was cancelled or denied; use Settings to retry.")
        self.portal.request(IFACE, method, callback, failed, options, *args)

    def created(self, results):
        # a{sv} response values retain variant_level=1 in dbus-python.
        # Outgoing methods require a bare object path, not that variant (or
        # the string some backends use to represent the returned path).
        self.session = self.dbus.ObjectPath(str(results["session_handle"]), variant_level=0)
        shortcuts = self.dbus.Array([
            self.dbus.Struct(("history", self.dbus.Dictionary({
                "description": "Show clipboard history", "preferred_trigger": "CTRL+ALT+v"
            }, signature="sv")), signature="sa{sv}")
        ], signature="(sa{sv})")
        self.request("BindShortcuts", self.bound, {}, self.session, shortcuts, "")

    def bound(self, results):
        self.on_changed(self.session, results.get("shortcuts", []))

    def on_changed(self, session, shortcuts):
        if session == self.session:
            for identifier, properties in shortcuts:
                if identifier == "history":
                    self.binding.emit(str(properties.get("trigger_description", "Assigned by KDE")))
                    return
            self.binding.emit("Unassigned — configure shortcut")

    def on_activated(self, session, identifier, _timestamp, options):
        if session == self.session and identifier == "history":
            self.activated.emit(str(options.get("activation_token", "")))

    def on_released(self, session, identifier, *_args):
        if session == self.session and identifier == "history":
            self.released.emit()

    def configure(self):
        if not self.portal.loop or not self.session:
            self.error.emit("Shortcut portal is not ready. Check xdg-desktop-portal-kde, then restart Flycut.")
            return

        def invoke():
            self.interface.ConfigureShortcuts(self.session, "", self.dbus.Dictionary({}, signature="sv"),
                                               reply_handler=lambda: None,
                                               error_handler=lambda exc: self.error.emit(f"Cannot open shortcut editor: {exc}"))
        self.portal.idle(invoke)


def keysym(character):
    """X keysym for a character, as understood by the compositor's keymap lookup."""
    special = {"\n": 0xff0d, "\r": 0xff0d, "\t": 0xff09, "\b": 0xff08, "\x1b": 0xff1b}
    if character in special:
        return special[character]
    code = ord(character)
    if 0x20 <= code <= 0x7e or 0xa0 <= code <= 0xff:
        return code
    return 0x01000000 | code


class RemoteKeyboard(PortalClient):
    """Sends key events through the RemoteDesktop portal.

    The session is created on the first send. KDE asks the user once; the
    restore token returned afterwards lets later sessions start without asking.
    Events are ("code", evdev_keycode, pressed) or ("sym", keysym, pressed).
    """
    sent = Signal(int)
    failed = Signal(int, str)
    status = Signal(str)
    token = Signal(str)

    def __init__(self, restore_token="", parent=None, portal=None):
        super().__init__(portal, parent)
        self.restore_token = restore_token
        self.session = None
        self.starting = False
        # Read from the Qt thread; written only on the portal thread.
        self.ready = False
        self.pending = None
        self.current = None
        self.pressed = set()
        self.closed_match = None

    def attach(self, portal):
        self.dbus = portal.dbus
        self.interface = portal.interface(REMOTE)

    def detach(self):
        self.release_all()
        if self.session:
            self.portal.close_session(self.session)

    def unavailable(self, exc):
        self.status.emit(f"portal unavailable ({exc})")

    # Qt-thread API

    def send(self, operation, events):
        if not self.portal.idle(lambda: self.queue(operation, list(events))):
            self.failed.emit(operation, "The desktop portal is not running; automatic paste is unavailable.")

    def cancel(self, operation):
        self.portal.idle(lambda: self.drop(operation))

    def reset_permission(self):
        self.restore_token = ""
        self.portal.idle(self.forget)

    # Portal-thread implementation

    def queue(self, operation, events):
        self.drop(self.current)
        self.pending = (operation, events)
        if self.ready:
            self.flush()
        elif not self.starting:
            self.create()

    def drop(self, operation):
        if self.pending and self.pending[0] == operation:
            self.pending = None
        if operation is not None and self.current == operation:
            self.current = None
            self.release_all()

    def forget(self):
        self.release_all()
        self.current = None
        self.end_session()
        self.status.emit("not requested yet")

    def end_session(self):
        if self.closed_match:
            self.closed_match.remove()
            self.closed_match = None
        if self.session:
            try:
                self.portal.close_session(self.session)
            except Exception:
                pass
        self.session = None
        self.starting = False
        self.ready = False

    def create(self):
        self.starting = True
        self.status.emit("waiting for KDE permission")
        self.portal.request(REMOTE, "CreateSession", self.created, self.denied,
                            {"session_handle_token": "flycut_" + uuid.uuid4().hex})

    def created(self, results):
        self.session = self.dbus.ObjectPath(str(results["session_handle"]), variant_level=0)
        self.closed_match = self.portal.bus.add_signal_receiver(
            self.closed, signal_name="Closed", dbus_interface=SESSION, path=self.session)
        options = {"types": self.dbus.UInt32(KEYBOARD), "persist_mode": self.dbus.UInt32(PERSIST_UNTIL_REVOKED)}
        if self.restore_token:
            options["restore_token"] = self.restore_token
        self.portal.request(REMOTE, "SelectDevices", self.selected, self.denied, options, self.session)

    def selected(self, _results):
        self.portal.request(REMOTE, "Start", self.started, self.denied, {}, self.session, "")

    def started(self, results):
        if not int(results.get("devices", 0)) & KEYBOARD:
            self.denied(2, None)
            return
        token = str(results.get("restore_token", ""))
        if token:
            self.restore_token = token
            self.token.emit(token)
        self.starting = False
        self.ready = True
        self.status.emit("granted")
        self.flush()

    def denied(self, code, exc):
        self.end_session()
        if exc is not None:
            message = f"Automatic paste is unavailable: the RemoteDesktop portal failed ({exc})."
            self.status.emit("portal error")
        else:
            message = ("Automatic paste needs keyboard permission. KDE's request was cancelled or denied; "
                       "select an entry again to retry. The entry remains copied.")
            self.status.emit("denied" if code == 1 else "not granted")
        if self.pending:
            operation, _events = self.pending
            self.pending = None
            self.failed.emit(operation, message)

    def closed(self, *_args):
        # KDE ended the session, for example after the user revoked access.
        self.pressed.clear()
        operation = self.current
        self.current = None
        self.end_session()
        self.status.emit("session closed; will ask again on the next paste")
        if operation is not None:
            self.failed.emit(operation, "KDE ended the remote keyboard session; automatic paste was interrupted.")

    def flush(self):
        if not self.pending:
            return
        operation, events = self.pending
        self.pending = None
        self.current = operation
        self.step(operation, events, 0)

    def step(self, operation, events, index):
        if self.current != operation:
            return
        if index >= len(events):
            self.current = None
            self.sent.emit(operation)
            return
        batch = events[index:index + BATCH]
        for position, event in enumerate(batch):
            last = position == len(batch) - 1
            self.notify(event,
                        (lambda: self.step(operation, events, index + len(batch))) if last else (lambda: None),
                        lambda exc: self.notify_failed(operation, exc))

    def notify(self, event, reply, error):
        kind, code, pressed = event
        method = self.interface.NotifyKeyboardKeycode if kind == "code" else self.interface.NotifyKeyboardKeysym
        if pressed:
            self.pressed.add((kind, code))
        else:
            self.pressed.discard((kind, code))
        method(self.session, self.dbus.Dictionary({}, signature="sv"), self.dbus.Int32(code),
               self.dbus.UInt32(1 if pressed else 0), reply_handler=reply, error_handler=error)

    def notify_failed(self, operation, exc):
        if self.current != operation:
            return
        self.current = None
        self.release_all()
        self.failed.emit(operation, f"Could not send keys through the RemoteDesktop portal ({exc}); the entry remains copied.")

    def release_all(self):
        """Never leave a key (such as Shift) held after a cancel or error."""
        if self.session and self.ready:
            for kind, code in list(self.pressed):
                self.notify((kind, code, False), lambda: None, lambda _exc: None)
        self.pressed.clear()
