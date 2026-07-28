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
