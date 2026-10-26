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
REQUEST = "org.freedesktop.portal.Request"


class Shortcuts(QObject):
    activated = Signal(str)
    released = Signal()
    binding = Signal(str)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = None
        self.session = None
        self.stopping = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        try:
            import dbus
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
            self.dbus = dbus
            self.GLib = GLib
            # Only this thread uses D-Bus. Qt receives queued signals.
            # dbus-python's GLib adapter and idle_add use the default context;
            # only this worker drives it. Qt runs its own event loop.
            self.loop = GLib.MainLoop()
            self.bus = dbus.SessionBus(private=True, mainloop=DBusGMainLoop())
            proxy = self.bus.get_object(SERVICE, PATH, introspect=False)
            # CLI launches also need a stable portal identity, independent of
            # whether Plasma started them in an application systemd scope.
            dbus.Interface(proxy, "org.freedesktop.host.portal.Registry").Register(
                "org.flycut.FlycutLinux", dbus.Dictionary({}, signature="sv"), timeout=5)
            self.interface = dbus.Interface(proxy, IFACE)
            self.bus.add_signal_receiver(self.on_activated, signal_name="Activated", dbus_interface=IFACE, path=PATH)
            self.bus.add_signal_receiver(self.on_released, signal_name="Deactivated", dbus_interface=IFACE, path=PATH)
            self.bus.add_signal_receiver(self.on_changed, signal_name="ShortcutsChanged", dbus_interface=IFACE, path=PATH)
            self.request("CreateSession", self.created,
                         {"session_handle_token": "flycut_" + uuid.uuid4().hex})
            if not self.stopping:
                self.loop.run()
            if self.session:
                session = self.bus.get_object(SERVICE, self.session, introspect=False)
                dbus.Interface(session, "org.freedesktop.portal.Session").Close(timeout=2)
            self.bus.close()
        except Exception as exc:
            self.error.emit(f"Global shortcut unavailable: {exc}. Use the tray or flycut-linux --show.")

    def request(self, method, callback, options, *args):
        token = "flycut_" + uuid.uuid4().hex
        options = dict(options, handle_token=token)
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        holder = {}

        def response(code, results):
            holder["match"].remove()
            if code:
                self.error.emit("Global shortcut request was cancelled or denied; use Settings to retry.")
            else:
                callback(results)

        holder["match"] = self.bus.add_signal_receiver(response, signal_name="Response", dbus_interface=REQUEST, path=path)
        getattr(self.interface, method)(*args, self.dbus.Dictionary(options, signature="sv"),
                                        reply_handler=lambda _path: None,
                                        error_handler=lambda exc: self.error.emit(f"Shortcut portal failed: {exc}"))

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
        if not self.loop or not self.session:
            self.error.emit("Shortcut portal is not ready. Check xdg-desktop-portal-kde, then restart Flycut.")
            return

        def invoke():
            self.interface.ConfigureShortcuts(self.session, "", self.dbus.Dictionary({}, signature="sv"),
                                               reply_handler=lambda: None,
                                               error_handler=lambda exc: self.error.emit(f"Cannot open shortcut editor: {exc}"))
            return False
        self.GLib.idle_add(invoke)

    def stop(self):
        self.stopping = True
        if self.loop:
            self.loop.quit()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
