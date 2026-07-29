import pytest
import pathlib
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
from astropy.wcs import WCS
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.viewers.image_viewer import ImageViewer


@pytest.fixture(scope="session")
def qapp():
    """Ensure a single QApplication instance exists for GUI tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


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
    """Locate the real OSIRIS reference FITS file if present on disk."""
    p = pathlib.Path("~/drp_me/ql2/s150531_a025002_Kn5_035.fits")
    if p.exists():
        return str(p)
    return None


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
