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
