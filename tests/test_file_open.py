"""Finder / desktop "open with" routing.

macOS does not put a double-clicked file in argv; it sends the running application an
open-document event. These tests use real `QFileOpenEvent`s delivered through the live
QApplication, which is the only thing that proves the filter is installed correctly.
"""

import pytest
from PySide6.QtCore import QEvent, QUrl
from PySide6.QtGui import QFileOpenEvent

from pyql3.gui.file_open import FileOpenHandler


def test_paths_arriving_before_the_window_exists_are_queued():
    """A cold launch delivers the event before MainWindow is built."""
    handler = FileOpenHandler()
    handler.open_path('/data/first.fits')
    handler.open_path('/data/second.fits')

    assert handler.pending == ['/data/first.fits', '/data/second.fits']

    opened = []
    handler.set_loader(opened.append)

    assert opened == ['/data/first.fits', '/data/second.fits'], "queue drained out of order"
    assert handler.pending == [], "queue not cleared, so a later open would replay it"


def test_paths_arriving_after_the_loader_go_straight_through():
    handler = FileOpenHandler()
    opened = []
    handler.set_loader(opened.append)

    handler.open_path('/data/later.fits')

    assert opened == ['/data/later.fits']
    assert handler.pending == []


def test_empty_requests_are_ignored():
    handler = FileOpenHandler()
    handler.open_path('')
    handler.open_path(None)
    assert handler.pending == []


def test_a_file_open_event_on_the_application_reaches_the_loader(qapp):
    """The wiring test: an event filter on the app has to see QEvent.FileOpen."""
    handler = FileOpenHandler()
    qapp.installEventFilter(handler)
    opened = []
    handler.set_loader(opened.append)

    try:
        accepted = qapp.sendEvent(qapp, QFileOpenEvent('/data/from_finder.fits'))
    finally:
        qapp.removeEventFilter(handler)

    assert accepted, "the filter did not consume the open-document event"
    assert opened == ['/data/from_finder.fits']


def test_a_url_only_request_still_yields_a_local_path(qapp):
    """QFileOpenEvent.file() is empty for a URL request; the path comes off the QUrl."""
    handler = FileOpenHandler()
    opened = []
    handler.set_loader(opened.append)
    qapp.installEventFilter(handler)

    try:
        qapp.sendEvent(qapp, QFileOpenEvent(QUrl.fromLocalFile('/data/via_url.fits')))
    finally:
        qapp.removeEventFilter(handler)

    assert opened == ['/data/via_url.fits']


def test_unrelated_events_are_not_swallowed(qapp):
    handler = FileOpenHandler()
    opened = []
    handler.set_loader(opened.append)
    qapp.installEventFilter(handler)

    try:
        qapp.sendEvent(qapp, QEvent(QEvent.Type.User))
    finally:
        qapp.removeEventFilter(handler)

    assert opened == []


def test_a_finder_request_loads_a_real_file_into_the_window(qapp, sample_2d_fits):
    """End to end with MainWindow.load_fits as the loader, as main.py wires it."""
    from pyql3.gui.main_window import MainWindow

    win = MainWindow()
    handler = FileOpenHandler()
    qapp.installEventFilter(handler)
    try:
        # queued first, exactly as a cold launch would
        handler.open_path(sample_2d_fits)
        handler.set_loader(win.load_fits)

        assert win.image_viewer.raw_data is not None
        assert "test_image_2d.fits" in win.windowTitle()
    finally:
        qapp.removeEventFilter(handler)
        win.close()


def _menu_action_labels(win):
    labels = []
    for top in win.menuBar().actions():
        menu = top.menu()
        if menu is not None:
            labels.extend(action.text() for action in menu.actions())
    return labels


@pytest.mark.parametrize("platform,expected", [("darwin", True), ("linux", True),
                                               ("win32", False)])
def test_the_install_cli_menu_action_is_present_except_on_windows(qapp, monkeypatch,
                                                                 platform, expected):
    import pyql3.gui.main_window as mw

    monkeypatch.setattr(mw.sys, 'platform', platform)
    win = mw.MainWindow()
    try:
        labels = _menu_action_labels(win)
        assert any('quicklook3' in label for label in labels) is expected, labels
        assert any('About' in label for label in labels), "Help menu lost its About item"
    finally:
        win.close()


def test_the_menu_action_installs_nothing_until_the_user_agrees(qapp, monkeypatch, tmp_path):
    """The dangerous version of this feature wrote an executable on the first click."""
    from pyql3.gui.main_window import MainWindow
    from pyql3.services import cli_install

    target = tmp_path / 'bin' / 'quicklook3'
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    monkeypatch.setattr(cli_install, 'choose_install_dir', lambda: tmp_path / 'bin')

    shown = []
    monkeypatch.setattr(MainWindow, 'confirm_cli_install',
                        lambda self, proposed: shown.append(proposed) or False)

    win = MainWindow()
    try:
        win.install_cli_tool()
    finally:
        win.close()

    assert len(shown) == 1, "the user was never asked"
    assert not target.exists(), "declining still installed the launcher"
    assert not target.parent.exists(), "declining still created the directory"


def test_the_menu_action_installs_exactly_what_was_confirmed(qapp, monkeypatch, tmp_path):
    from pyql3.gui.main_window import MainWindow
    from pyql3.services import cli_install

    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    monkeypatch.setattr(cli_install, 'choose_install_dir', lambda: tmp_path / 'bin')

    agreed_to = []
    monkeypatch.setattr(MainWindow, 'confirm_cli_install',
                        lambda self, proposed: agreed_to.append(proposed) or True)
    reported = []
    monkeypatch.setattr('pyql3.gui.main_window.QMessageBox.information',
                        lambda parent, title, text: reported.append(text))

    win = MainWindow()
    try:
        win.install_cli_tool()
    finally:
        win.close()

    installed = tmp_path / 'bin' / 'quicklook3'
    assert installed.is_file()
    assert agreed_to[0].path == installed, "installed somewhere other than what was shown"
    assert str(installed) in reported[0] and f"rm {installed}" in reported[0]


def test_the_confirmation_dialog_states_the_path_the_command_and_the_undo(qapp, tmp_path):
    """Everything the user needs to decide has to be in the dialog itself."""
    from pyql3.gui.main_window import MainWindow
    from pyql3.services.cli_install import InstallPlan

    proposed = InstallPlan(
        path=tmp_path / 'bin' / 'quicklook3',
        command=['/Applications/QuickLook3.app/Contents/MacOS/QuickLook3'],
        app_bundle=None, hint=None, existing=None, name='quicklook3')

    win = MainWindow()
    captured = {}
    try:
        # Drive the real QMessageBox: record what it displays, then click Install. The
        # click has to be *queued* -- clicking before exec() starts leaves the modal loop
        # with no events to process and it never returns.
        import PySide6.QtWidgets as qtw
        from PySide6.QtCore import QTimer

        original_exec = qtw.QMessageBox.exec

        def click_install(box):
            captured['text'] = box.text()
            captured['details'] = box.informativeText()
            captured['default'] = box.defaultButton().text()
            captured['accept_label'] = box.button(qtw.QMessageBox.StandardButton.Ok).text()
            QTimer.singleShot(0, box.button(qtw.QMessageBox.StandardButton.Ok).click)
            return original_exec(box)

        qtw.QMessageBox.exec = click_install
        try:
            accepted = win.confirm_cli_install(proposed)
        finally:
            qtw.QMessageBox.exec = original_exec
    finally:
        win.close()

    assert accepted is True
    assert 'quicklook3' in captured['text']
    assert captured['accept_label'] == 'Install'
    assert captured['default'] == 'Cancel', "the safe option must be the default"

    details = captured['details']
    assert str(proposed.path) in details, "the dialog never said where it would write"
    assert '/Applications/QuickLook3.app/Contents/MacOS/QuickLook3' in details
    assert 'quicklook3 yourfile.fits' in details, "no instructions for running it"
    assert f'rm {proposed.path}' in details, "no instructions for uninstalling it"


def test_cancelling_the_real_dialog_returns_false(qapp, tmp_path):
    from pyql3.gui.main_window import MainWindow
    from pyql3.services.cli_install import InstallPlan
    import PySide6.QtWidgets as qtw
    from PySide6.QtCore import QTimer

    proposed = InstallPlan(path=tmp_path / 'quicklook3', command=['/bin/echo'],
                           app_bundle=None, hint=None, existing=None, name='quicklook3')

    original_exec = qtw.QMessageBox.exec

    def click_cancel(box):
        QTimer.singleShot(0, box.button(qtw.QMessageBox.StandardButton.Cancel).click)
        return original_exec(box)

    win = MainWindow()
    qtw.QMessageBox.exec = click_cancel
    try:
        assert win.confirm_cli_install(proposed) is False
    finally:
        qtw.QMessageBox.exec = original_exec
        win.close()


def test_an_impossible_install_is_reported_before_any_dialog(qapp, monkeypatch):
    """A failed plan must show the actionable message, not a traceback and not a prompt.

    Anything that calls `install_cli_tool` has to control `confirm_cli_install`, or a real
    modal dialog opens and the test hangs forever.
    """
    from pyql3.gui.main_window import MainWindow
    from pyql3.services import cli_install

    def refuse(*args, **kwargs):
        raise cli_install.CliInstallError("read-only volume")

    asked = []
    monkeypatch.setattr(cli_install, 'plan', refuse)
    monkeypatch.setattr(MainWindow, 'confirm_cli_install',
                        lambda self, proposed: asked.append(proposed) or True)
    shown = []
    monkeypatch.setattr('pyql3.gui.main_window.QMessageBox.warning',
                        lambda parent, title, text: shown.append(text))

    win = MainWindow()
    try:
        win.install_cli_tool()
    finally:
        win.close()

    assert shown == ["read-only volume"]
    assert asked == [], "asked the user to confirm something that cannot be done"


def test_a_write_failure_after_confirmation_is_reported(qapp, monkeypatch, tmp_path):
    """The plan can succeed and the write still fail -- a race, or a permissions change."""
    from pyql3.gui.main_window import MainWindow
    from pyql3.services import cli_install

    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    monkeypatch.setattr(cli_install, 'choose_install_dir', lambda: tmp_path / 'bin')
    monkeypatch.setattr(MainWindow, 'confirm_cli_install', lambda self, proposed: True)

    def fail(*args, **kwargs):
        raise cli_install.CliInstallError("Cannot write /somewhere/quicklook3")

    monkeypatch.setattr(cli_install, 'install', fail)
    shown = []
    monkeypatch.setattr('pyql3.gui.main_window.QMessageBox.warning',
                        lambda parent, title, text: shown.append(text))

    win = MainWindow()
    try:
        win.install_cli_tool()
    finally:
        win.close()

    assert shown == ["Cannot write /somewhere/quicklook3"]


def test_the_success_message_repeats_the_path_setup_when_one_is_needed(qapp, monkeypatch,
                                                                      tmp_path):
    from pyql3.gui.main_window import MainWindow
    from pyql3.services import cli_install

    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    monkeypatch.setattr(cli_install, 'choose_install_dir', lambda: tmp_path / 'bin')
    monkeypatch.setattr(cli_install, 'looks_on_path', lambda directory: False)
    monkeypatch.setattr(MainWindow, 'confirm_cli_install', lambda self, proposed: True)
    shown = []
    monkeypatch.setattr('pyql3.gui.main_window.QMessageBox.information',
                        lambda parent, title, text: shown.append(text))

    win = MainWindow()
    try:
        win.install_cli_tool()
    finally:
        win.close()

    installed = tmp_path / 'bin' / 'quicklook3'
    assert len(shown) == 1
    assert str(installed) in shown[0]
    assert 'export PATH=' in shown[0], "a directory off PATH must come with the fix"
    assert f'rm {installed}' in shown[0], "the success message must say how to undo it"
