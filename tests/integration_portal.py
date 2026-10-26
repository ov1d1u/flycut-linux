"""Run explicitly: dbus-run-session -- python tests/integration_portal.py.

Uses a fake portal on a private bus, never the user's desktop portal.
"""
import os
import subprocess
import sys
import time


def server():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    name = dbus.service.BusName("org.freedesktop.portal.Desktop", bus)
    registry = set()
    requests = []
    interface = "org.freedesktop.portal.GlobalShortcuts"

    class Request(dbus.service.Object):
        @dbus.service.signal("org.freedesktop.portal.Request", signature="ua{sv}")
        def Response(self, code, results):
            pass

    class Session(dbus.service.Object):
        @dbus.service.method("org.freedesktop.portal.Session", in_signature="", out_signature="")
        def Close(self):
            pass

    class Portal(dbus.service.Object):
        @dbus.service.method("org.freedesktop.host.portal.Registry", in_signature="sa{sv}", out_signature="", sender_keyword="sender")
        def Register(self, app_id, options, sender):
            assert app_id == "org.flycut.FlycutLinux"
            registry.add(sender)

        def respond(self, sender, options, results):
            assert sender in registry
            path = "/org/freedesktop/portal/desktop/request/" + sender[1:].replace(".", "_") + "/" + options["handle_token"]
            request = Request(bus, path)
            requests.append(request)
            GLib.timeout_add(20, lambda: (request.Response(0, results), False)[1])
            return dbus.ObjectPath(path)

        @dbus.service.method(interface, in_signature="a{sv}", out_signature="o", sender_keyword="sender")
        def CreateSession(self, options, sender):
            self.session_path = dbus.ObjectPath("/org/freedesktop/portal/desktop/session/test/flycut")
            requests.append(Session(bus, self.session_path))
            # Portals return the handle inside a{sv}, commonly as a string.
            # Reusing that received value preserves its variant wrapper.
            return self.respond(sender, options, {"session_handle": dbus.String(self.session_path)})

        @dbus.service.method(interface, in_signature="oa(sa{sv})sa{sv}", out_signature="o", sender_keyword="sender", message_keyword="message")
        def BindShortcuts(self, session, shortcuts, parent, options, sender, message):
            # dbus-python services otherwise accept a mismatched wire signature;
            # KDE's native service rejects it before dispatching the method.
            assert str(message.get_signature()) == "oa(sa{sv})sa{sv}"
            assert session == self.session_path
            assert shortcuts[0][0] == "history"
            assert shortcuts[0][1]["preferred_trigger"] == "CTRL+ALT+v"
            path = self.respond(sender, options, {"shortcuts": self.shortcuts("Ctrl+Alt+V")})
            GLib.timeout_add(70, lambda: (self.Activated(session, "history", 123, {"activation_token": "test-token"}), False)[1])
            GLib.timeout_add(100, lambda: (self.Deactivated(session, "history", 124, {}), False)[1])
            return path

        def shortcuts(self, label):
            return dbus.Array([dbus.Struct(("history", dbus.Dictionary({"trigger_description": label}, signature="sv")), signature="sa{sv}")], signature="(sa{sv})")

        @dbus.service.method(interface, in_signature="osa{sv}", out_signature="", message_keyword="message")
        def ConfigureShortcuts(self, session, parent, options, message):
            assert str(message.get_signature()) == "osa{sv}"
            self.ShortcutsChanged(session, self.shortcuts("Meta+V"))

        @dbus.service.signal(interface, signature="osta{sv}")
        def Activated(self, session, identifier, timestamp, options):
            pass

        @dbus.service.signal(interface, signature="osta{sv}")
        def Deactivated(self, session, identifier, timestamp, options):
            pass

        @dbus.service.signal(interface, signature="oa(sa{sv})")
        def ShortcutsChanged(self, session, shortcuts):
            pass

    portal = Portal(bus, "/org/freedesktop/portal/desktop")
    print("ready", flush=True)
    GLib.MainLoop().run()


def client():
    assert os.environ.get("FLYCUT_PRIVATE_TEST_BUS") == "1", "Use dbus-run-session with FLYCUT_PRIVATE_TEST_BUS=1"
    from PySide6.QtCore import QCoreApplication
    from flycut_linux.portal import Shortcuts
    process = subprocess.Popen([sys.executable, __file__, "--server"], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        app = QCoreApplication([])
        portal = Shortcuts()
        bindings, activations, releases, errors = [], [], [], []
        portal.binding.connect(bindings.append)
        portal.activated.connect(activations.append)
        portal.released.connect(lambda: releases.append(True))
        portal.error.connect(errors.append)
        portal.start()
        deadline = time.monotonic() + 5
        while not releases and not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert errors == [], errors
        assert bindings == ["Ctrl+Alt+V"], bindings
        assert activations == ["test-token"], activations
        assert releases == [True], releases
        portal.configure()
        deadline = time.monotonic() + 2
        while len(bindings) < 2 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert bindings == ["Ctrl+Alt+V", "Meta+V"], bindings
        portal.stop()
        assert not portal.thread.is_alive()
        print("PASS: portal registration, request framing, binding, token, release, rebinding and shutdown")
    finally:
        process.terminate()
        process.wait(timeout=3)


if __name__ == "__main__":
    server() if "--server" in sys.argv else client()
