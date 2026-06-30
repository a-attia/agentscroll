"""Tests for the bundled-launcher installer.

These assert that the launcher templates are packaged (readable via
importlib.resources) and that `install()` writes the expected files into a
temp destination -- without touching the real Desktop/Applications.
"""

import sys
from importlib import resources

import pytest

from agentscroll import launcher_install


def test_launcher_templates_are_packaged():
    # All four templates must be importable as package data (i.e. shipped).
    for name in (
        "agentscroll.command",
        "agentscroll.bat",
        "agentscroll.sh",
        "agentscroll.desktop",
    ):
        text = resources.files("agentscroll.launchers").joinpath(name).read_text()
        assert "agentscroll" in text


def test_install_writes_into_dest(tmp_path):
    created = launcher_install.install(tmp_path)
    assert created, "install should create at least one file"
    for p in created:
        assert p.exists()
    # The platform-appropriate primary launcher should be present.
    names = {p.name for p in created}
    if sys.platform == "darwin":
        assert "agentscroll.command" in names
    elif sys.platform == "win32":
        assert "agentscroll.bat" in names
    else:
        assert "agentscroll.desktop" in names


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS .app bundle only")
def test_macos_app_bundle(tmp_path):
    created = launcher_install.install(tmp_path, app_bundle=True)
    app = next((p for p in created if p.name == "agentscroll.app"), None)
    assert app is not None
    assert (app / "Contents" / "Info.plist").is_file()
    runner = app / "Contents" / "MacOS" / "agentscroll"
    assert runner.is_file()
    # Runner must be executable.
    import os
    import stat

    assert os.stat(runner).st_mode & stat.S_IXUSR
