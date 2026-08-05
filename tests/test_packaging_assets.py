import pathlib
import pytest
import pyql3


def test_linelist_data_files_exist():
    """Verify that all required linelist text files exist in pyql3/data directory."""
    pkg_dir = pathlib.Path(pyql3.__file__).parent
    data_dir = pkg_dir / "data"

    assert data_dir.exists() and data_dir.is_dir(), f"pyql3/data directory missing at {data_dir}"

    expected_files = [
        "nir_stellar_lines.txt",
        "arcturus_atomic_lines.txt",
        "arcturus_molecular_lines.txt"
    ]

    for fname in expected_files:
        fpath = data_dir / fname
        assert fpath.exists(), f"Required packaging file {fname} missing from {data_dir}"
        assert fpath.stat().st_size > 0, f"Packaging file {fname} is empty"


def test_cmcrameri_colormaps_available():
    """Verify that cmcrameri scientific colormaps module is available and registered."""
    try:
        import cmcrameri.cm
        import matplotlib.cm as cm
        assert "cmc.oslo" in cm._colormaps or "oslo" in cm._colormaps, "cmc.oslo colormap not registered in matplotlib"
    except ImportError:
        pytest.fail("cmcrameri package missing from environment")


def test_pyinstaller_spec_includes_all_assets():
    """Verify that QuickLook3.spec explicitly configures pyql3/data and cmcrameri collection."""
    spec_path = pathlib.Path("QuickLook3.spec")
    assert spec_path.exists(), "QuickLook3.spec missing from root directory"

    content = spec_path.read_text()
    assert "pyql3/data" in content, "QuickLook3.spec missing pyql3/data entry"
    assert "cmcrameri" in content, "QuickLook3.spec missing cmcrameri collection"


def test_macos_bundle_declares_an_identifier_and_fits_documents():
    """The .app has to be identifiable and has to claim FITS files.

    Without `CFBundleDocumentTypes` QuickLook3 never appears in Finder's "Open With" menu,
    and with PyInstaller's default `bundle_identifier=None` the identifier is the bare
    string `QuickLook3`, which makes `open -b` and LaunchServices registration unreliable.
    """
    content = pathlib.Path("QuickLook3.spec").read_text()

    assert "bundle_identifier=None" not in content, "the .app has no real bundle identifier"
    assert "bundle_identifier='edu.ucla.astro.pyql3'" in content

    assert "CFBundleDocumentTypes" in content, "the .app does not claim FITS documents"
    for extension in ("'fits'", "'fit'", "'fts'"):
        assert extension in content, f"FITS extension {extension} not claimed by the bundle"

    # Finder's open-document event is handled inside Qt (see pyql3/gui/file_open.py), so
    # PyInstaller's argv rewriting must stay off; the two mechanisms conflict.
    assert "argv_emulation=False" in content
