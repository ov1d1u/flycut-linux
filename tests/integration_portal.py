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
            remote.closed.append(str(self._object_path))

        @dbus.service.signal("org.freedesktop.portal.Session", signature="a{sv}")
        def Closed(self, details):
            pass

    remote_iface = "org.freedesktop.portal.RemoteDesktop"

    class Remote(dbus.service.Object):
        """Fake RemoteDesktop portal; records key events for the tests."""

        def __init__(self, *args):
            super().__init__(*args)
            self.count = 0
            self.sessions = {}
            self.events = []
            self.selects = []
            self.closed = []
            self.deny = False

        @dbus.service.method(remote_iface, in_signature="a{sv}", out_signature="o", sender_keyword="sender")
        def CreateSession(self, options, sender):
            self.count += 1
            path = dbus.ObjectPath(f"/org/freedesktop/portal/desktop/session/test/remote{self.count}")
            self.sessions[path] = Session(bus, path)
            return portal.respond(sender, options, {"session_handle": dbus.String(path)})

        @dbus.service.method(remote_iface, in_signature="oa{sv}", out_signature="o", sender_keyword="sender", message_keyword="message")
        def SelectDevices(self, session, options, sender, message):
            assert str(message.get_signature()) == "oa{sv}"
            assert session in self.sessions
            assert int(options["types"]) == 1 and int(options["persist_mode"]) == 2
            self.selects.append(str(options.get("restore_token", "")))
            return portal.respond(sender, options, {})

        @dbus.service.method(remote_iface, in_signature="osa{sv}", out_signature="o", sender_keyword="sender")
        def Start(self, session, parent, options, sender):
            assert session in self.sessions
            if self.deny:
                return portal.respond(sender, options, {}, code=1)
            return portal.respond(sender, options, {"devices": dbus.UInt32(1), "restore_token": f"token-{self.count}"})

        @dbus.service.method(remote_iface, in_signature="oa{sv}iu", out_signature="", message_keyword="message")
        def NotifyKeyboardKeycode(self, session, options, keycode, state, message):
            assert str(message.get_signature()) == "oa{sv}iu"
            assert session in self.sessions
            self.events.append(["code", int(keycode), bool(state)])

        @dbus.service.method(remote_iface, in_signature="oa{sv}iu", out_signature="", message_keyword="message")
        def NotifyKeyboardKeysym(self, session, options, keysym, state, message):
            assert str(message.get_signature()) == "oa{sv}iu"
            assert session in self.sessions
            self.events.append(["sym", int(keysym), bool(state)])

        # Test-only controls.
        @dbus.service.method("org.flycut.Test", in_signature="", out_signature="s")
        def Take(self):
            import json
            result = json.dumps(dict(events=self.events, selects=self.selects, closed=self.closed))
            self.events, self.selects, self.closed = [], [], []
            return result

        @dbus.service.method("org.flycut.Test", in_signature="b", out_signature="")
        def Deny(self, deny):
            self.deny = deny

        @dbus.service.method("org.flycut.Test", in_signature="", out_signature="")
        def EndSession(self):
            path = max(self.sessions)
            self.sessions.pop(path).Closed({})

    class Portal(dbus.service.Object):
        @dbus.service.method("org.freedesktop.host.portal.Registry", in_signature="sa{sv}", out_signature="", sender_keyword="sender")
        def Register(self, app_id, options, sender):
            assert app_id == "org.flycut.FlycutLinux"
            registry.add(sender)

        def respond(self, sender, options, results, code=0):
            assert sender in registry
            path = "/org/freedesktop/portal/desktop/request/" + sender[1:].replace(".", "_") + "/" + options["handle_token"]
            request = Request(bus, path)
            requests.append(request)
            GLib.timeout_add(20, lambda: (request.Response(code, results), False)[1])
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

    class Desktop(Portal, Remote):
        pass

    portal = remote = Desktop(bus, "/org/freedesktop/portal/desktop")
    print("ready", flush=True)
    GLib.MainLoop().run()


def wait(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    return predicate()


def fake_state():
    import dbus
    proxy = dbus.SessionBus().get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")
    return dbus.Interface(proxy, "org.flycut.Test")


def take():
    import json
    return json.loads(fake_state().Take())


def keyboard_checks(app):
    from flycut_linux.portal import PortalThread, RemoteKeyboard
    thread = PortalThread()
    keyboard = RemoteKeyboard("saved-token", portal=thread)
    sent, failed, statuses, tokens = [], [], [], []
    keyboard.sent.connect(sent.append)
    keyboard.failed.connect(lambda op, message: failed.append((op, message)))
    keyboard.status.connect(statuses.append)
    keyboard.token.connect(tokens.append)
    thread.start()
    assert wait(app, lambda: thread.loop is not None and thread.loop.is_running())
    events = [("code", 42, True), ("code", 110, True), ("code", 110, False), ("code", 42, False)]
    many = [("sym", 0x61 + i % 26, pressed) for i in range(150) for pressed in (True, False)]
    keyboard.send(1, events)
    assert wait(app, lambda: sent or failed), "no keyboard result"
    assert sent == [1] and not failed, (sent, failed)
    assert keyboard.ready and tokens == ["token-1"], tokens
    keyboard.send(2, many)
    assert wait(app, lambda: len(sent) == 2), sent
    state = take()
    assert state["selects"] == ["saved-token"], state
    assert state["events"] == [list(e) for e in events + many], "events lost or reordered"
    # KDE ending the session: the next send starts a new one with the new token.
    fake_state().EndSession()
    assert wait(app, lambda: not keyboard.ready)
    keyboard.send(3, events)
    assert wait(app, lambda: len(sent) == 3), sent
    assert take()["selects"] == ["token-1"]
    # Resetting forgets the token; a denied request reports failure and sends nothing.
    keyboard.reset_permission()
    assert wait(app, lambda: statuses[-1] == "not requested yet")
    fake_state().Deny(True)
    keyboard.send(4, events)
    assert wait(app, lambda: failed), "denial not reported"
    assert failed[0][0] == 4 and "permission" in failed[0][1], failed
    assert statuses[-1] == "denied", statuses
    state = take()
    assert state["selects"] == [""] and state["events"] == [], state
    fake_state().Deny(False)
    thread.stop()
    assert not thread.thread.is_alive()


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
        keyboard_checks(app)
        print("PASS: portal registration, request framing, binding, token, release, rebinding, shutdown, "
              "remote keyboard ordering, restore tokens, session loss, reset and denial")
    finally:
        process.terminate()
        process.wait(timeout=3)


if __name__ == "__main__":
    server() if "--server" in sys.argv else client()
