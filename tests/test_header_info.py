"""Integration-time rules for the Total DN multiplier (no Qt needed)."""
import pytest
from astropy.io import fits

from pyql3.core.header_info import integration_seconds, total_dn_multiplier


def test_truitime_wins_over_millisecond_itime():
    header = fits.Header({'ITIME': 900000, 'TRUITIME': 899.92})
    assert integration_seconds(header) == pytest.approx(899.92)


def test_itime_alone_is_seconds():
    assert integration_seconds(fits.Header({'ITIME': 10})) == 10.0


def test_zero_itime_falls_back_to_truitime():
    # A zero ITIME is present but unusable; with no TRUITIME either, the result is 1.
    assert integration_seconds(fits.Header({'ITIME': 0, 'TRUITIME': 5.5})) == 5.5
    assert integration_seconds(fits.Header({'ITIME': 0})) == 1.0


def test_no_exposure_keywords_means_one():
    assert integration_seconds(fits.Header()) == 1.0
    assert total_dn_multiplier(fits.Header()) == 1.0


def test_coadds_multiply():
    header = fits.Header({'TRUITIME': 20.0, 'COADDS': 3})
    assert total_dn_multiplier(header) == pytest.approx(60.0)
    # A missing, zero or non-finite COADDS counts as one.
    assert total_dn_multiplier(fits.Header({'TRUITIME': 20.0})) == 20.0
    assert total_dn_multiplier(fits.Header({'TRUITIME': 20.0, 'COADDS': 0})) == 20.0
    # A FITS header cannot hold NaN, but the function is handed any mapping with `get`.
    assert total_dn_multiplier({'TRUITIME': 20.0, 'COADDS': float('nan')}) == 20.0
