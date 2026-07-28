import pytest
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from pyql3.core.fits_reader import FitsReader


def test_fits_reader_load_3d_cube(sample_3d_fits):
    """Verify loading 3D spectral datacube into FitsReader."""
    reader = FitsReader(sample_3d_fits)
    assert reader.data is not None, "FitsReader failed to load 3D data cube"
    assert reader.data.ndim == 3, f"Expected 3D data array, got ndim={reader.data.ndim}"
    assert reader.header is not None, "FitsReader missing FITS header"
    assert 'CTYPE1' in reader.header, "FitsReader header missing WCS keywords"
    assert len(reader.get_image_extensions()) > 0, "No image extensions discovered in 3D FITS"


def test_fits_reader_load_2d_image(sample_2d_fits):
    """Verify loading 2D image into FitsReader."""
    reader = FitsReader(sample_2d_fits)
    assert reader.data is not None, "FitsReader failed to load 2D FITS image"
    assert reader.data.ndim == 2, f"Expected 2D data array, got ndim={reader.data.ndim}"
    assert reader.header['EXPTIME'] == 5.0


def test_fits_reader_multi_extension(tmp_path):
    """Verify extension enumeration and header modification across multi-HDU files."""
    primary_hdu = fits.PrimaryHDU()
    sci_data = np.zeros((20, 20), dtype=np.float32)
    sci_hdu = fits.ImageHDU(data=sci_data, name="SCI")
    var_hdu = fits.ImageHDU(data=sci_data * 0.1, name="VAR")

    hdul = fits.HDUList([primary_hdu, sci_hdu, var_hdu])
    filepath = tmp_path / "multi_ext.fits"
    hdul.writeto(filepath, overwrite=True)

    reader = FitsReader(str(filepath))
    all_exts = reader.get_all_extensions()
    img_exts = reader.get_image_extensions()

    assert len(all_exts) == 3, f"Expected 3 extensions, got {len(all_exts)}"
    assert len(img_exts) == 2, f"Expected 2 image data extensions, got {len(img_exts)}"

    # Test updating header card on specific extension
    reader.update_header_card("TESTKEY", "TESTVAL", comment="Test comment", ext=1)
    sci_hdr = reader.get_header(ext=1)
    assert sci_hdr.get("TESTKEY") == "TESTVAL", "Failed to update header card on extension 1"
    reader.close()


def test_fits_reader_osiris_axis_mapping(real_osiris_fits):
    """Verify OSIRIS cube axis ordering rules (Axis 1=WAVE, Axis 2=DEC, Axis 3=RA)."""
    if real_osiris_fits is None:
        pytest.skip("Real OSIRIS FITS file not found on disk")

    reader = FitsReader(real_osiris_fits)
    wcs = WCS(reader.header)
    
    # OSIRIS spectral cubes map Axis 1 to Wavelength (CTYPE1='WAVE')
    ctype1 = str(wcs.wcs.ctype[0]).upper()
    assert 'WAVE' in ctype1 or 'AWAV' in ctype1, f"Expected OSIRIS Axis 1 CTYPE WAVE, got {ctype1}"
    reader.close()
