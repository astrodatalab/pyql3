import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from pyql3.services import cli_install
from pyql3.services.cli_install import (
    MARKER,
    CliInstallError,
    choose_install_dir,
    install,
    launch_command,
    looks_on_path,
    macos_app_bundle,
    path_hint,
    shim_text,
)

pytestmark = pytest.mark.skipif(
    sys.platform.startswith('win'), reason="the shell launcher is macOS/Linux only")


def test_launch_command_from_a_source_checkout_points_at_main_py():
    command = launch_command()
    assert command[0] == os.path.abspath(sys.executable)
    assert Path(command[-1]).name == 'main.py'
    assert Path(command[-1]).is_file(), "the generated launcher would reference nothing"


def test_launch_command_keeps_the_virtual_environment_interpreter():
    """Resolving `sys.executable` would break the launcher.

    A venv's `bin/python` is a symlink to the base interpreter, which has none of this
    project's dependencies installed; a launcher pointing at the resolved path dies with
    `ModuleNotFoundError: No module named 'PySide6'`.
    """
    interpreter = Path(launch_command()[0])
    assert interpreter == Path(sys.executable), "the launcher left the active environment"
    if interpreter.is_symlink():
        assert interpreter.resolve() != interpreter, "test premise: venv python is a symlink"


def test_launch_command_for_a_frozen_build_is_the_executable_itself(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/Applications/QuickLook3.app/Contents/MacOS/QuickLook3')
    assert launch_command() == ['/Applications/QuickLook3.app/Contents/MacOS/QuickLook3']


def test_macos_app_bundle_finds_the_enclosing_bundle():
    inner = '/Applications/QuickLook3.app/Contents/MacOS/QuickLook3'
    assert macos_app_bundle(inner) == Path('/Applications/QuickLook3.app')
    assert macos_app_bundle('/opt/QuickLook3/QuickLook3') is None


def test_plan_touches_nothing(tmp_path, monkeypatch):
    """The confirmation dialog is built from a plan, so planning must have no side effects."""
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    target_dir = tmp_path / 'bin'

    proposed = cli_install.plan(dest_dir=target_dir)

    assert proposed.path == target_dir / 'quicklook3'
    assert proposed.command == ['/bin/echo', 'TARGET']
    assert proposed.existing is None
    assert not target_dir.exists(), "planning created the install directory"
    assert not proposed.path.exists(), "planning wrote the launcher"


def test_plan_reports_what_an_install_would_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])

    assert cli_install.plan(dest_dir=tmp_path).existing is None

    (tmp_path / 'quicklook3').write_text("#!/bin/sh\necho someone else\n")
    assert cli_install.plan(dest_dir=tmp_path).existing == 'foreign'

    install(dest_dir=tmp_path, force=True)
    assert cli_install.plan(dest_dir=tmp_path).existing == 'ours'


def test_describe_plan_states_the_path_the_target_and_the_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_install, 'launch_command',
                        lambda: ['/Applications/QuickLook3.app/Contents/MacOS/QuickLook3'])
    monkeypatch.setattr(cli_install, 'looks_on_path', lambda directory: True)

    text = "\n".join(cli_install.describe_plan(cli_install.plan(dest_dir=tmp_path)))

    assert str(tmp_path / 'quicklook3') in text
    assert '/Applications/QuickLook3.app/Contents/MacOS/QuickLook3' in text
    assert 'quicklook3 yourfile.fits' in text
    assert f"rm {tmp_path / 'quicklook3'}" in text


def test_describe_plan_leads_with_the_path_setup_when_one_is_needed(tmp_path, monkeypatch):
    """The PATH line has to come before the run instruction, since it is a prerequisite."""
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    monkeypatch.setattr(cli_install, 'looks_on_path', lambda directory: False)

    text = "\n".join(cli_install.describe_plan(cli_install.plan(dest_dir=tmp_path)))

    assert 'export PATH=' in text and str(tmp_path) in text
    assert text.index('export PATH=') < text.index('yourfile.fits')
    assert f"rm {tmp_path / 'quicklook3'}" in text, "undo instructions are not optional"


def test_install_writes_the_plan_it_was_given(tmp_path, monkeypatch):
    """What the user confirmed is what lands on disk, even if the default would differ."""
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'TARGET'])
    proposed = cli_install.plan(dest_dir=tmp_path / 'confirmed')

    monkeypatch.setattr(cli_install, 'choose_install_dir',
                        lambda: pytest.fail("install re-planned instead of using the plan"))
    path = install(plan_=proposed)

    assert path == tmp_path / 'confirmed' / 'quicklook3'
    assert path.is_file()


def test_the_generated_launcher_forwards_arguments(tmp_path, monkeypatch):
    """The whole point of the shim: `quicklook3 a.fits --flag` has to reach the application."""
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'QL3'])
    path = install(dest_dir=tmp_path)

    assert path == tmp_path / 'quicklook3'
    assert path.stat().st_mode & stat.S_IXUSR, "launcher is not executable"

    out = subprocess.run([str(path), 'cube.fits', '--collapsed'],
                         capture_output=True, text=True, check=True)
    assert out.stdout.split() == ['QL3', 'cube.fits', '--collapsed']


def test_paths_with_spaces_survive_quoting(tmp_path, monkeypatch):
    """A bundle in `~/Applications/My Tools/` must not split into two arguments."""
    spaced = tmp_path / "dir with spaces"
    spaced.mkdir()
    stub = spaced / "QuickLook3"
    stub.write_text('#!/bin/sh\necho "target:$#"\n')
    stub.chmod(0o755)

    monkeypatch.setattr(cli_install, 'launch_command', lambda: [str(stub)])
    path = install(dest_dir=tmp_path / 'bin')
    out = subprocess.run([str(path), 'a.fits'], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == 'target:1'


def test_reinstalling_over_our_own_launcher_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'first'])
    first = install(dest_dir=tmp_path)
    assert MARKER in first.read_text()

    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'second'])
    second = install(dest_dir=tmp_path)
    assert second == first and 'second' in second.read_text()


def test_a_foreign_file_at_the_target_is_left_alone(tmp_path, monkeypatch):
    """Someone may already have a `quicklook3` on PATH; overwriting it silently is not ours to do."""
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'QL3'])
    existing = tmp_path / 'quicklook3'
    existing.write_text("#!/bin/sh\necho not ours\n")

    with pytest.raises(CliInstallError, match="not created by QuickLook 3"):
        install(dest_dir=tmp_path)
    assert existing.read_text() == "#!/bin/sh\necho not ours\n"

    install(dest_dir=tmp_path, force=True)
    assert MARKER in existing.read_text()


def test_installing_from_a_mounted_disk_image_is_refused(tmp_path, monkeypatch):
    """Running from the mounted .dmg is the mistake this catches: the path disappears."""
    monkeypatch.setattr(cli_install, 'launch_command',
                        lambda: ['/Volumes/QuickLook3/QuickLook3.app/Contents/MacOS/QuickLook3'])
    monkeypatch.setattr(cli_install, 'on_read_only_filesystem', lambda path: True)

    with pytest.raises(CliInstallError, match="read-only volume"):
        install(dest_dir=tmp_path)


def test_a_sealed_system_path_is_not_mistaken_for_a_disk_image():
    """Since Big Sur the macOS system volume is read-only, so read-only alone means nothing.

    `/bin/echo` is genuinely on a read-only filesystem on macOS. Refusing to install
    because of that would have been a false positive; only a read-only volume *under a
    mount root* is a disk image.
    """
    assert cli_install.on_temporary_volume('/bin/echo') is False
    assert cli_install.under_removable_mount('/Volumes/QuickLook3/QuickLook3.app') is True
    assert cli_install.under_removable_mount('/Applications/QuickLook3.app') is False


def test_on_read_only_filesystem_says_no_for_an_ordinary_directory(tmp_path):
    assert cli_install.on_read_only_filesystem(tmp_path) is False
    assert cli_install.on_read_only_filesystem('/nonexistent-path-xyz') is False


def test_quarantine_guard_is_only_written_for_a_macos_bundle():
    bundled = shim_text(['/Applications/QuickLook3.app/Contents/MacOS/QuickLook3'],
                        app_bundle=Path('/Applications/QuickLook3.app'))
    assert 'com.apple.quarantine' in bundled
    assert 'xattr -cr' in bundled, "the guard must state the fix, not just refuse"

    plain = shim_text(['/opt/QuickLook3/QuickLook3'])
    assert 'com.apple.quarantine' not in plain
    assert plain.rstrip().endswith('"$@"')


def test_install_writes_the_quarantine_guard_for_a_frozen_bundle(tmp_path, monkeypatch):
    """A .dmg install is the case that needs the guard, so check it end to end."""
    bundle = tmp_path / 'QuickLook3.app'
    inner = bundle / 'Contents' / 'MacOS'
    inner.mkdir(parents=True)
    exe = inner / 'QuickLook3'
    exe.write_text('#!/bin/sh\nexit 0\n')
    exe.chmod(0o755)

    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(cli_install, 'launch_command', lambda: [str(exe)])
    path = install(dest_dir=tmp_path / 'bin')

    text = path.read_text()
    assert str(bundle) in text and 'com.apple.quarantine' in text


def test_path_hint_is_advice_only_when_the_directory_is_not_on_path(monkeypatch):
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    assert path_hint('/usr/local/bin') is None, "on the default login PATH"
    hint = path_hint(Path.home() / '.local' / 'bin')
    assert hint is not None and 'export PATH=' in hint


def test_looks_on_path_ignores_the_processes_own_environment_for_system_dirs(monkeypatch):
    """An app launched from Finder inherits a minimal PATH; that is not evidence."""
    monkeypatch.setenv('PATH', '')
    assert looks_on_path('/usr/local/bin') is True
    assert looks_on_path('/some/place/else') is False


def test_choose_install_dir_prefers_a_writable_usr_local_bin(monkeypatch, tmp_path):
    preferred = tmp_path / 'usr_local_bin'
    preferred.mkdir()
    fallback = tmp_path / 'home_local_bin'
    monkeypatch.setattr(cli_install, 'CANDIDATE_DIRS', (str(preferred), str(fallback)))
    assert choose_install_dir() == preferred

    # Unwritable first choice falls through to the second
    preferred.chmod(0o500)
    try:
        if os.access(preferred, os.W_OK):  # running as root: the check cannot be exercised
            pytest.skip("cannot make a directory unwritable as this user")
        assert choose_install_dir() == fallback
    finally:
        preferred.chmod(0o700)


def test_a_home_without_dot_local_can_still_be_installed_into(tmp_path, monkeypatch):
    """A fresh macOS account has no ~/.local at all, let alone ~/.local/bin.

    Judging creatability from the immediate parent only used to fail here, so the installer
    reported "No writable directory to install into" on exactly the machines that a
    downloaded .dmg lands on.
    """
    fresh_home = tmp_path / 'fresh_home'
    fresh_home.mkdir()
    assert not (fresh_home / '.local').exists(), "test premise"

    monkeypatch.setattr(cli_install, 'CANDIDATE_DIRS',
                        ('/definitely/not/writable', str(fresh_home / '.local' / 'bin')))
    monkeypatch.setattr(cli_install, 'launch_command', lambda: ['/bin/echo', 'QL3'])

    path = install()

    assert path == fresh_home / '.local' / 'bin' / 'quicklook3'
    assert path.is_file() and path.stat().st_mode & stat.S_IXUSR


def test_can_create_rejects_a_path_under_an_unwritable_root():
    assert cli_install.can_create('/definitely/not/writable/bin') is False
    assert cli_install.can_create(Path.home() / 'some' / 'new' / 'dir') is True


def test_install_is_refused_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    with pytest.raises(CliInstallError, match="macOS and Linux"):
        install(dest_dir=tmp_path)
