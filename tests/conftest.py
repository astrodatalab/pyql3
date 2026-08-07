import os
import pytest
import pathlib
import warnings
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
from astropy.wcs import WCS
from pyql3.core.fits_reader import FitsReader
from pyql3.services.config import ConfigManager
from pyql3.gui.viewers.image_viewer import ImageViewer


@pytest.fixture(scope="session")
def qapp():
    """Ensure a single QApplication instance exists for GUI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture(autouse=True, scope="session")
def isolated_settings(tmp_path_factory):
    """Keep the suite out of the developer's own `~/.pyql3/config.json`.

    `MainWindow` takes the process-wide `ConfigManager`, so every test that builds a window was
    writing to the real settings file: it overwrote the Recent Files list with pytest temp paths,
    and left behind whatever a test toggled — a window that then opened with a region toolbar
    nobody asked for. It also made the suite depend on the developer's saved polling interval.

    Autouse and session-scoped so no test can opt out by forgetting.
    """
    import pyql3.services.config as config_module

    settings = tmp_path_factory.mktemp("settings") / "config.json"
    saved = config_module._shared_config
    config_module._shared_config = ConfigManager(settings)
    try:
        yield config_module._shared_config
    finally:
        config_module._shared_config = saved


@pytest.fixture
def sample_3d_fits(tmp_path):
    """Generate a synthetic 3D spectral FITS data cube for testing."""
    shape = (50, 40, 40) # (WAVE, DEC, RA) in FITS order
    data = np.random.normal(100.0, 10.0, shape).astype(np.float32)
    
    # Add a synthetic emission line peak at channel 25
    data[25, 15:25, 15:25] += 500.0
    
    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, -118.0]
    w.wcs.cdelt = [0.0005, 0.0001, 0.0001]
    w.wcs.crpix = [1, 20, 20]
    w.wcs.cunit = ['um', 'deg', 'deg']

    header = w.to_header()
    header['EXPTIME'] = 10.0
    header['ITIME'] = 10.0
    header['BUNIT'] = 'DN/s'

    hdu = fits.PrimaryHDU(data=data, header=header)
    filepath = tmp_path / "test_cube_3d.fits"
    hdu.writeto(filepath, overwrite=True)
    return str(filepath)


@pytest.fixture
def sample_2d_fits(tmp_path):
    """Generate a synthetic 2D FITS image for testing."""
    data = np.random.normal(50.0, 5.0, (100, 100)).astype(np.float32)
    hdu = fits.PrimaryHDU(data=data)
    hdu.header['EXPTIME'] = 5.0
    filepath = tmp_path / "test_image_2d.fits"
    hdu.writeto(filepath, overwrite=True)
    return str(filepath)


@pytest.fixture
def loaded_viewer(qapp, sample_3d_fits):
    """Fixture returning an ImageViewer pre-loaded with synthetic 3D data."""
    reader = FitsReader(sample_3d_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    return viewer


@pytest.fixture
def real_osiris_fits():
    """Path to a real OSIRIS cube, or None if one is not configured.

    Set ``PYQL3_TEST_CUBE`` to exercise the real-data tests; they skip otherwise.
    The path is deliberately not hardcoded — it differs per machine, and this
    repository is public (see AGENTS.md, "Reference data").

    A *misconfigured* variable warns rather than skipping silently. Returning None
    for both "not configured" and "configured wrongly" is how the previous version
    of this fixture hid a bug for months: it used ``pathlib.Path("~/...")``, which
    does not expand ``~``, so it always returned None and every dependent test
    skipped while looking perfectly healthy.
    """
    raw = os.environ.get("PYQL3_TEST_CUBE")
    if not raw:
        return None

    path = pathlib.Path(raw).expanduser()
    if not path.is_file():
        warnings.warn(
            f"PYQL3_TEST_CUBE is set to {path!s}, which is not a readable file; "
            "real-data tests will skip.",
            stacklevel=2,
        )
        return None
    return str(path)


def _rewrite_fits_in_place(path, hdu):
    """Overwrite a FITS file's bytes without unlinking it, then guarantee a new mtime.

    Two portability constraints, both learned from a Windows CI failure:

    * `hdu.writeto(path, overwrite=True)` cannot be used while something still holds the
      file open. Windows refuses to delete or replace such a file, and astropy implements
      overwrite as `os.remove()` + create, so it raises `PermissionError: [WinError 32]`.
      Writing *into* the existing file (`r+b`) is permitted on every platform.
    * File timestamp granularity is coarser than a test: the Windows clock ticks roughly
      every 15 ms, so a rewrite microseconds after the original can land on an identical
      mtime. Bumping the mtime explicitly makes staleness detection deterministic instead
      of dependent on how fast the test ran.
    """
    import io
    import os

    buf = io.BytesIO()
    hdu.writeto(buf)
    payload = buf.getvalue()

    with open(path, 'r+b') as fh:
        fh.write(payload)
        fh.truncate(len(payload))

    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    return str(path)


@pytest.fixture
def rewrite_fits_in_place():
    """Rewrite a FITS file in place, as an instrument or DRP overwriting a frame would.

    Use this instead of `writeto(..., overwrite=True)` in any test where something still
    has the file open -- see `_rewrite_fits_in_place` for why that fails on Windows.
    """
    return _rewrite_fits_in_place


@pytest.fixture
def click_event():
    """Factory for a pyqtgraph mouse-click event, as the scene delivers one to an item.

    Region items handle right-click and double-click themselves (see `RegionItemInteraction`), so
    several test files need to synthesise these.
    """
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    from pyqtgraph.GraphicsScene.mouseEvents import MouseClickEvent

    def make(button=Qt.MouseButton.RightButton, double=False, scene_pos=(5.0, 5.0),
             screen_pos=(50, 50)):
        qt_event = QGraphicsSceneMouseEvent()
        qt_event.setButton(button)
        qt_event.setScenePos(QPointF(*scene_pos))
        qt_event.setScreenPos(QPoint(*screen_pos))
        return MouseClickEvent(qt_event, double=double)

    return make
