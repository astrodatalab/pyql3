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


@pytest.fixture(autouse=True)
def destroy_leftover_windows(qapp):
    """Destroy the windows a test leaves behind, so the next test does not pay for them.

    `widget.close()` hides a window; it does not destroy it. The C++ object survives, and so does
    everything it owns — including the cube in `ImageViewer.raw_data`. Dropping the last Python
    reference does not help either: every `lambda: self.something()` connected to a QAction is held
    by that action, which is held by a menu, which is held by the widget, so the cycle runs through
    C++ where Python's collector cannot follow it. Measured: **415 widgets survive every
    `MainWindow`, permanently**, and `gc.collect()` reclaims none of them.

    That is not merely untidy. `ViewBox.__init__` calls `ViewBox.updateAllViewLists()`, which walks
    *every* live ViewBox in the process, so building one costs O(live views) — and the suite builds
    hundreds. Constructing a window slows down measurably as the run goes on: 0.037 s at ten
    windows, 0.090 s at a hundred, 0.129 s at 150. That is where the suite's 0.24 s -> 0.67 s
    fixture creep comes from.

    `shiboken6.delete` destroys the C++ object outright, which drops the connections and with them
    the cycle. Autouse fixtures are finalized last, so this runs *after* a test's own `win.close()`.

    **Only whole windows are destroyed.** pyqtgraph leaves parentless helper widgets — context
    menus, a colour dialog, the frames behind them — belonging to a scene that is still alive;
    deleting one of those and then its window is a segfault, and deleting its menus makes pyqtgraph
    trip over its own combo box the next time a view list updates. Their cost is small and they are
    left alone.
    """
    import shiboken6
    from PySide6.QtWidgets import QMainWindow

    def windows():
        return {w for w in QApplication.topLevelWidgets()
                if isinstance(w, (QMainWindow, ImageViewer)) and shiboken6.isValid(w)}

    before = windows()
    yield

    leftovers = [w for w in windows() - before]
    for widget in leftovers:
        if shiboken6.isValid(widget):
            widget.close()
    qapp.processEvents()

    # Re-checked one at a time: closing a window destroys anything parented to it, so a wrapper
    # collected a moment ago may already be dangling, and deleting one of those is a segfault
    # rather than an exception.
    for widget in leftovers:
        if shiboken6.isValid(widget):
            shiboken6.delete(widget)
    qapp.processEvents()


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
def osiris_like_fits(tmp_path):
    """Generate a cube with the shape, WCS and header cards of a real OSIRIS frame.

    `sample_3d_fits` is square, unrotated, ICRS-free and has no TRUITIME, so it cannot catch
    anything that depends on those. This one is not square (NAXIS 40 x 51 x 66), has the
    sky axes rotated 35 degrees through a PC matrix, is FK5, and carries ITIME in
    milliseconds beside TRUITIME in seconds, as OSIRIS cubes written since the 2016 detector
    upgrade do.

    The value at numpy index ``[i, j, k]`` is ``10000*i + 100*j + k``, so every voxel
    identifies itself and a swapped or shifted index shows up in the number.
    """
    shape = (66, 51, 40)  # (RA, DEC, WAVE) in numpy order
    i, j, k = np.indices(shape)
    data = (10000 * i + 100 * j + k).astype(np.float32)

    pixel_scale = 0.035 / 3600.0  # degrees
    theta = np.deg2rad(35.0)

    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.0e-6, 34.0, 266.4]
    w.wcs.crpix = [1, 26, 33]
    w.wcs.cdelt = [2.5e-10, pixel_scale, pixel_scale]
    w.wcs.cunit = ['m', 'deg', 'deg']
    w.wcs.pc = [[1, 0, 0],
                [0, np.cos(theta), -np.sin(theta)],
                [0, np.sin(theta), np.cos(theta)]]
    w.wcs.radesys = 'FK5'
    w.wcs.equinox = 2000.0

    header = w.to_header()
    # to_header() converts the spectral axis to metres; OSIRIS writes nanometres.
    header['CUNIT1'] = 'nm'
    header['CRVAL1'] = (2000.0, '[nm] Coordinate value at reference point')
    header['CDELT1'] = (0.25, '[nm] Coordinate increment at reference point')
    header['CURRINST'] = 'OSIRIS'
    header['ITIME'] = 900000
    header['TRUITIME'] = 899.92
    header['COADDS'] = 1
    header['BUNIT'] = 'ADU per second per coadd'

    filepath = tmp_path / "osiris_like_cube.fits"
    fits.PrimaryHDU(data=data, header=header).writeto(filepath, overwrite=True)
    return str(filepath)


@pytest.fixture
def nifs_like_header():
    """The SCI header of a Gemini NIFS cube, cut down to its axis cards (`BUGS.md` R81, R82).

    NIFS writes its axes the IRAF way: every `CTYPEn` is `LINEAR`, so astropy finds neither a
    celestial nor a spectral axis, and the wavelength axis (FITS axis 3) is named only by
    `WAT3_001 = '... axtype=wave'` and `DISPAXIS = 3`. No unit is stated anywhere; the numbers
    are Ångström by Gemini's convention. A data array for it is numpy `(wave, y, x)`.
    """
    header = fits.Header()
    for key, value in [('DISPAXIS', 3), ('CTYPE1', 'LINEAR'), ('CTYPE2', 'LINEAR'),
                       ('CTYPE3', 'LINEAR'), ('CD1_1', 0.05), ('CD2_2', 0.05),
                       ('CD3_3', 2.1325798), ('CRVAL3', 19968.66), ('CRPIX3', 1.0),
                       ('WAT1_001', 'wtype=linear axtype=xi'),
                       ('WAT2_001', 'wtype=linear axtype=eta'),
                       ('WAT3_001', 'wtype=linear axtype=wave')]:
        header[key] = value
    return header


def _wcs_cards(*ctypes):
    """The header cards of a 3-axis WCS with the given `CTYPEn`."""
    wcs = WCS(naxis=3)
    wcs.wcs.ctype = list(ctypes)
    wcs.wcs.cdelt = [1e-9 if ctype.startswith('WAVE') else 1e-5 for ctype in ctypes]
    return wcs.to_header()


@pytest.fixture
def s3d_like_fits(tmp_path):
    """A file laid out as a JWST NIRSpec `*_s3d.fits` (`BUGS.md` R25): an empty primary, then
    SCI, ERR and DQ cubes of one shape. Only SCI carries WCS cards and `BUNIT`. Numpy shape
    (wave, dec, ra) = (60, 9, 7)."""
    cube = np.arange(60 * 9 * 7, dtype=np.float32).reshape(60, 9, 7)
    science = _wcs_cards('RA---TAN', 'DEC--TAN', 'WAVE')
    science['BUNIT'] = 'MJy/sr'
    path = tmp_path / "s3d_like.fits"
    fits.HDUList([fits.PrimaryHDU(),
                  fits.ImageHDU(cube, science, name='SCI'),
                  fits.ImageHDU(cube, name='ERR'),
                  fits.ImageHDU(np.zeros(cube.shape, dtype=np.uint8), name='DQ')]).writeto(path)
    return str(path)


@pytest.fixture
def osiris_like_fits_with_noise(tmp_path):
    """A file laid out as an OSIRIS cube (`BUGS.md` R25): the primary holds the science data, its
    WCS (wavelength, Dec, RA), `TRUITIME`/`ITIME`/`COADDS` and `BUNIT`; extensions 1 and 2 (noise
    and quality) have the primary's shape and headers with nothing in them. Numpy shape
    (ra, dec, wave) = (7, 9, 60)."""
    cube = np.ones((7, 9, 60), dtype=np.float32)
    header = _wcs_cards('WAVE', 'DEC--TAN', 'RA---TAN')
    header['ITIME'] = 900000
    header['TRUITIME'] = 899.92
    header['COADDS'] = 2
    header['BUNIT'] = 'ADU per second per coadd'
    path = tmp_path / "osiris_like.fits"
    fits.HDUList([fits.PrimaryHDU(cube, header),
                  fits.ImageHDU(cube * 0.1),
                  fits.ImageHDU(np.zeros(cube.shape, dtype=np.uint8))]).writeto(path)
    return str(path)


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
