"""Header facts: integration time, the WCS, and which axes are spectral and shown (no Qt needed)."""
import astropy.units as u
import numpy as np
import pytest
from astropy.io import fits

from pyql3.core.header_info import (DataUnit, build_wcs, data_keywords, data_unit, default_axes,
                                    has_world_axes, header_value_scale, in_microns,
                                    integration_seconds, spectral_axis, spectral_unit,
                                    stated_unit, states_integration_time, total_dn_multiplier,
                                    unit_name, world_axis_label)


def test_truitime_wins_over_millisecond_itime():
    header = fits.Header({'ITIME': 900000, 'TRUITIME': 899.92})
    assert integration_seconds(header) == pytest.approx(899.92)


def test_itime_alone_is_seconds():
    assert integration_seconds(fits.Header({'ITIME': 10})) == 10.0


def test_zero_itime_falls_back_to_truitime():
    # A zero ITIME is present but unusable; with no TRUITIME either, there is no time.
    assert integration_seconds(fits.Header({'ITIME': 0, 'TRUITIME': 5.5})) == 5.5
    assert integration_seconds(fits.Header({'ITIME': 0})) is None


def test_no_exposure_keywords_means_no_multiplier():
    # A multiplier of 1.0 here made Total DN relabel unconverted values (R83)
    assert integration_seconds(fits.Header()) is None
    assert total_dn_multiplier(fits.Header()) is None


def test_exptime_is_the_last_fallback():
    """NIFS states only `EXPTIME`, the time summed over all coadds, so `COADDS` does not
    multiply it again. `TRUITIME` and `ITIME` still come first."""
    assert total_dn_multiplier(fits.Header({'EXPTIME': 600.0, 'COADDS': 3})) == 600.0
    assert total_dn_multiplier(fits.Header({'EXPTIME': 600.0, 'ITIME': 10.0,
                                            'COADDS': 3})) == 30.0
    assert total_dn_multiplier(fits.Header({'EXPTIME': 600.0, 'TRUITIME': 20.0})) == 20.0
    assert total_dn_multiplier(fits.Header({'EXPTIME': 0, 'COADDS': 3})) is None


def test_coadds_multiply():
    header = fits.Header({'TRUITIME': 20.0, 'COADDS': 3})
    assert total_dn_multiplier(header) == pytest.approx(60.0)
    # A missing, zero or non-finite COADDS counts as one.
    assert total_dn_multiplier(fits.Header({'TRUITIME': 20.0})) == 20.0
    assert total_dn_multiplier(fits.Header({'TRUITIME': 20.0, 'COADDS': 0})) == 20.0
    # A FITS header cannot hold NaN, but the function is handed any mapping with `get`.
    assert total_dn_multiplier({'TRUITIME': 20.0, 'COADDS': float('nan')}) == 20.0


@pytest.mark.parametrize("bunit,exposure,expected", [
    # OSIRIS writes the unit in words, which astropy does not parse
    ('ADU per second per coadd', {'TRUITIME': 899.92}, DataUnit('DN/s', True, "")),
    ('MJy/sr', {'EXPTIME': 100.0}, DataUnit('MJy/sr', False, "data are in MJy/sr")),
    ('DN/s', {'ITIME': 10.0}, DataUnit('DN/s', True, "")),
    ('electron/s', {'EXPTIME': 100.0}, DataUnit('electron/s', True, "")),
    # A flux per second is not a count rate: multiplying it by the time is not Total DN
    ('erg/s/cm2/Angstrom', {'EXPTIME': 100.0},
     DataUnit('erg/s/cm2/Angstrom', False, "data are in erg/s/cm2/Angstrom")),
    ('DN', {'EXPTIME': 100.0}, DataUnit('DN', False, "data are in DN")),
    # The multiplier is in seconds; a count per millisecond or per minute times it is not Total DN
    ('DN/ms', {'ITIME': 10.0}, DataUnit('DN/ms', False, "data are in DN/ms")),
    ('DN/min', {'ITIME': 10.0}, DataUnit('DN/min', False, "data are in DN/min")),
    ('DN s-1', {'ITIME': 10.0}, DataUnit('DN s-1', True, "")),
    # Free text is DN/s only when it is OSIRIS's: counts named ADU or DN, per second
    ('DN per second', {'ITIME': 10.0}, DataUnit('DN/s', True, "")),
    (None, {'EXPTIME': 600.0}, DataUnit('Unknown', None, "no unit is stated for these data")),
    ('', {'EXPTIME': 600.0}, DataUnit('Unknown', None, "no unit is stated for these data")),
    # A unit the file states but astropy cannot read is shown in the file's own words
    ('electrons per second', {'EXPTIME': 600.0},
     DataUnit('electrons per second', None,
              "the file's unit 'electrons per second' is not recognised")),
    ('erg per second per cm2', {'EXPTIME': 600.0},
     DataUnit('erg per second per cm2', None,
              "the file's unit 'erg per second per cm2' is not recognised")),
    ('ELECTRONS/S', {'EXPTIME': 600.0},
     DataUnit('ELECTRONS/S', None, "the file's unit 'ELECTRONS/S' is not recognised")),
    (' counts/s ', {'EXPTIME': 600.0},
     DataUnit('counts/s', None, "the file's unit 'counts/s' is not recognised")),
    ('e-/s', {'EXPTIME': 600.0}, DataUnit('e-/s', None, "the file's unit 'e-/s' is not recognised")),
    ('DN/sec', {'EXPTIME': 600.0},
     DataUnit('DN/sec', None, "the file's unit 'DN/sec' is not recognised")),
    ('DN/s', {}, DataUnit('DN/s', True, "no integration time in the header")),
])
def test_data_unit(bunit, exposure, expected):
    """The label every unit readout shows, whether Total DN applies, and why not (R83). A
    unit the file does not state is Unknown, never inferred from the values; one it states in
    words astropy does not read is shown as written, and is not a rate."""
    header = fits.Header(exposure)
    if bunit is not None:
        header['BUNIT'] = bunit
    assert data_unit(header) == expected


def test_data_unit_without_a_header():
    assert data_unit(None) == DataUnit('Unknown', None, "no unit is stated for these data")


# --- build_wcs (R13) ---------------------------------------------------------------

def _wave_header(unit, extra_units=('deg', 'deg')):
    header = fits.Header()
    header['NAXIS'] = 3
    for axis, (ctype, cunit, crval, cdelt, crpix) in enumerate([
            ('WAVE', unit, 2.0, 0.001, 1),
            ('DEC--TAN', extra_units[0], -29.0, 1e-5, 5),
            ('RA---TAN', extra_units[1], 266.4, -1e-5, 5)], start=1):
        header[f'CTYPE{axis}'] = ctype
        header[f'CUNIT{axis}'] = cunit
        header[f'CRVAL{axis}'] = crval
        header[f'CDELT{axis}'] = cdelt
        header[f'CRPIX{axis}'] = crpix
    return header


@pytest.mark.parametrize("unit", ['um', 'micron', 'microns', 'Micron', 'MICRONS'])
def test_build_wcs_accepts_micron(unit):
    header = _wave_header(unit)

    wcs, problem = build_wcs(header)

    assert problem == ""
    assert wcs is not None and wcs.naxis == 3
    assert wcs.wcs.ctype[0] == 'WAVE'
    # The caller's header is left as the file wrote it.
    assert header['CUNIT1'] == unit


def test_build_wcs_reports_an_unreadable_unit():
    wcs, problem = build_wcs(_wave_header('parsec'))

    assert wcs is None
    assert isinstance(problem, str) and problem


def test_build_wcs_without_a_header_is_not_an_error():
    assert build_wcs(None) == (None, "")
    assert build_wcs(fits.Header()) == (None, "")


# --- spectral_axis, spectral_unit, default_axes (R24, R81, R82) ---------------------

def _axes_header(*ctypes, **cards):
    """A header with a linear WCS on each named axis, plus any extra `cards`."""
    header = fits.Header()
    header['NAXIS'] = len(ctypes)
    units = {'RA': 'deg', 'DE': 'deg', 'WA': 'um', 'AW': 'um', 'FR': 'Hz'}
    values = {'RA': 266.4, 'DE': -29.0, 'WA': 2.0, 'AW': 2.0, 'FR': 1.4e11}
    for axis, ctype in enumerate(ctypes, start=1):
        header[f'CTYPE{axis}'] = ctype
        header[f'CRPIX{axis}'] = 1.0
        header[f'CDELT{axis}'] = 1e-5
        if ctype[:2] in units:
            header[f'CUNIT{axis}'] = units[ctype[:2]]
            header[f'CRVAL{axis}'] = values[ctype[:2]]
    for key, value in cards.items():
        header[key] = value
    return header


def _wcs_of(header):
    wcs, problem = build_wcs(header)
    assert wcs is not None, problem
    return wcs


@pytest.mark.parametrize("ctypes, expected", [
    (('WAVE', 'DEC--TAN', 'RA---TAN'), 0),
    (('AWAV', 'DEC--TAN', 'RA---TAN'), 0),       # R24: 'WAVE' is not a substring of 'AWAV'
    (('RA---TAN', 'DEC--TAN', 'FREQ'), 2),
    (('RA---TAN', 'DEC--TAN', 'LINEAR'), None),
], ids=['WAVE', 'AWAV', 'FREQ', 'none'])
def test_spectral_axis(ctypes, expected):
    header = _axes_header(*ctypes)
    assert spectral_axis(_wcs_of(header), header) == expected


def test_spectral_axis_from_iraf_cards(nifs_like_header):
    """R82: a `LINEAR` axis is spectral when IRAF's cards say so."""
    wat = nifs_like_header
    assert _wcs_of(wat).wcs.spec == -1, "astropy finds no spectral axis on its own"
    del wat['DISPAXIS']
    assert spectral_axis(_wcs_of(wat), wat) == 2

    dispaxis_alone = fits.Header({'NAXIS': 3, 'CTYPE1': 'LINEAR', 'CTYPE2': 'LINEAR',
                                  'CTYPE3': 'LINEAR', 'CRVAL3': 19968.66, 'CD3_3': 2.13,
                                  'DISPAXIS': 3})
    assert spectral_axis(_wcs_of(dispaxis_alone), dispaxis_alone) == 2

    neither = dispaxis_alone.copy()
    del neither['DISPAXIS']
    assert spectral_axis(_wcs_of(neither), neither) is None

    # DISPAXIS naming a celestial axis is not believed
    celestial = _axes_header('RA---TAN', 'DEC--TAN', 'LINEAR', DISPAXIS=1)
    assert spectral_axis(_wcs_of(celestial), celestial) is None


def test_a_missing_spectral_unit_is_unknown(nifs_like_header):
    """R82: no unit is ever inferred. The NIFS numbers are Ångström by Gemini's convention,
    but nothing in the header says so."""
    header = nifs_like_header
    assert spectral_unit(_wcs_of(header), header, 2) is None
    assert in_microns(19968.66, None) is None

    header['WAT3_001'] = 'wtype=linear axtype=wave units=angstroms'
    unit = spectral_unit(_wcs_of(header), header, 2)
    assert unit == u.AA
    assert in_microns(19968.66, unit) == pytest.approx(1.996866)

    header['WAT3_001'] = 'wtype=linear axtype=wave'
    header['CUNIT3'] = 'nm'
    assert spectral_unit(_wcs_of(header), header, 2) == u.nm

    # A WAVE axis is in the unit the WCS evaluates it in: wcslib turns um into m
    wave = _axes_header('WAVE', 'DEC--TAN', 'RA---TAN')
    wcs = _wcs_of(wave)
    unit = spectral_unit(wcs, wave, 0)
    assert in_microns(wcs.wcs_pix2world([[0, 0, 0]], 0)[0][0], unit) == pytest.approx(2.0)

    # A frequency is a wavelength through the spectral equivalency
    assert in_microns(299792458.0 / 2e-6, u.Hz) == pytest.approx(2.0)


@pytest.mark.parametrize("case, expected", [
    ('osiris', (2, 1, 0)),
    ('jwst', (0, 1, 2)),
    ('nifs', (0, 1, 2)),
    ('bare osiris', (2, 1, 0)),
    ('bare currinst', (2, 1, 0)),
    ('bare', (0, 1, 2)),
])
def test_default_axes(case, expected, nifs_like_header):
    """R81: X, Y, Z as 0-based FITS axes. Celestial axes decide first, then a spectral axis,
    then the instrument; a cube with nothing to go on is taken to be `[x, y, wavelength]`."""
    header = {
        'osiris': _axes_header('WAVE', 'DEC--TAN', 'RA---TAN'),
        'jwst': _axes_header('RA---TAN', 'DEC--TAN', 'WAVE'),
        'nifs': nifs_like_header,
        'bare osiris': fits.Header({'NAXIS': 3, 'INSTRUME': 'OSIRIS'}),
        'bare currinst': fits.Header({'NAXIS': 3, 'CURRINST': 'OSIRIS'}),
        'bare': fits.Header({'NAXIS': 3}),
    }[case]
    wcs, _ = build_wcs(header)
    assert default_axes(wcs, header) == expected


def test_a_unit_wcslib_fills_in_is_not_a_stated_unit():
    """wcslib gives a standard spectral axis without `CUNIT<n>` the FITS default (`m` for
    `WAVE`). The header does not state it, so it is Unknown, like any other unstated unit."""
    header = _axes_header('RA---TAN', 'DEC--TAN', 'WAVE')
    del header['CUNIT3']
    wcs = _wcs_of(header)
    assert str(wcs.wcs.cunit[2]) == 'm', "wcslib's default, which the header does not state"
    assert spectral_unit(wcs, header, 2) is None

    # Stated, it is used, and the WCS's values (rescaled by wcslib to m) convert correctly
    header['CUNIT3'] = 'nm'
    header['CRVAL3'] = 2000.0
    header['CDELT3'] = 0.25
    wcs = _wcs_of(header)
    unit = spectral_unit(wcs, header, 2)
    assert in_microns(wcs.wcs_pix2world([[0, 0, 0], [0, 0, 4]], 0)[:, 2], unit) == \
        pytest.approx([2.0, 2.001])


def test_the_header_numbers_behind_a_rescaled_spectral_axis(nifs_like_header):
    """A wavelength unit the user chooses describes the numbers the header writes. wcslib holds
    `CUNIT3 = 'nm'` in metres, so those numbers are the WCS's values times 1e9."""
    header = _axes_header('RA---TAN', 'DEC--TAN', 'WAVE')
    header['CUNIT3'] = 'nm'
    wcs = _wcs_of(header)
    assert stated_unit(header, 2) == u.nm
    assert spectral_unit(wcs, header, 2) == u.m
    assert header_value_scale(wcs, header, 2) == pytest.approx(1e9)

    # IRAF writes the unit in WAT, and the WCS holds the numbers as written
    nifs_like_header['WAT3_001'] = 'wtype=linear axtype=wave units=angstroms'
    wcs = _wcs_of(nifs_like_header)
    assert stated_unit(nifs_like_header, 2) == u.AA
    assert header_value_scale(wcs, nifs_like_header, 2) == 1.0

    # Nothing stated: nothing to scale
    del header['CUNIT3']
    assert stated_unit(header, 2) is None
    assert header_value_scale(_wcs_of(header), header, 2) == 1.0
    assert [unit_name(unit) for unit in (u.AA, u.nm, u.um)] == ["Å", "nm", "µm"]


# --- keywords borrowed from another header (R25) --------------------------------------

@pytest.mark.parametrize("case, expected", [
    ('celestial', True),
    ('spectral', True),
    ('iraf', True),
    ('linear only', False),
    ('none', False),
])
def test_has_world_axes(case, expected, nifs_like_header):
    header = {
        'celestial': _axes_header('RA---TAN', 'DEC--TAN'),
        'spectral': _axes_header('LINEAR', 'LINEAR', 'AWAV'),
        'iraf': nifs_like_header,
        'linear only': _axes_header('LINEAR', 'LINEAR', 'LINEAR'),
        'none': fits.Header({'NAXIS': 3}),
    }[case]
    wcs, _ = build_wcs(header)
    assert has_world_axes(wcs, header) is expected


def test_states_integration_time():
    assert states_integration_time(fits.Header({'ITIME': 900000}))
    assert states_integration_time(fits.Header({'TRUITIME': 899.92}))
    assert states_integration_time(fits.Header({'EXPTIME': 600.0}))
    assert not states_integration_time(fits.Header({'COADDS': 4}))
    assert not states_integration_time(fits.Header({'ITIME': 0}))
    assert not states_integration_time(None)


def _primary(cards):
    """A primary HDU's header holding `cards`: it begins with `SIMPLE`, as one read from a file
    does, which is how `data_keywords` tells it from an extension's."""
    return fits.PrimaryHDU(header=fits.Header(cards)).header


def _extension(cards):
    """An image extension's header holding `cards`: it begins with `XTENSION`."""
    return fits.ImageHDU(header=fits.Header(cards)).header


def test_data_keywords():
    """The integration time and `COADDS` come together from the header that states an
    integration time, the extension's own first; `BUNIT` from the first header that has one."""
    primary = _primary({'TRUITIME': 899.92, 'ITIME': 900000, 'COADDS': 2,
                        'BUNIT': 'ADU per second per coadd'})
    assert total_dn_multiplier(data_keywords(fits.Header(), primary)) == pytest.approx(1799.84)
    assert data_keywords(fits.Header(), primary)['BUNIT'] == 'ADU per second per coadd'

    # The extension's own time wins, and its group is not mixed with the reference's COADDS
    own = data_keywords(fits.Header({'ITIME': 10.0, 'BUNIT': 'DN/s'}), primary)
    assert total_dn_multiplier(own) == 10.0
    assert own['BUNIT'] == 'DN/s'

    # NIFS: EXPTIME and COADDS in the primary only
    nifs = data_keywords(fits.Header(), _primary({'EXPTIME': 600.0, 'COADDS': 1}))
    assert (nifs['EXPTIME'], nifs['COADDS']) == (600.0, 1)
    assert 'BUNIT' not in nifs

    assert len(data_keywords(None, None)) == 0
    assert data_keywords(fits.Header({'EXPTIME': 5.0}))['EXPTIME'] == 5.0


def test_data_keywords_are_borrowed_only_from_the_primary():
    """A sibling extension describes its own values: JWST's SCI is in MJy/sr, while the DQ,
    WMAP and VAR_* extensions beside it are flags, weights and squared units. Only the primary
    header, where OSIRIS writes the unit and time of every extension, lends them."""
    science = _extension({'BUNIT': 'MJy/sr', 'XPOSURE': 100.0, 'EXPTIME': 100.0})

    borrowed = data_keywords(_extension({}), science, np.dtype('float32'))

    assert 'BUNIT' not in borrowed and 'EXPTIME' not in borrowed
    assert data_unit(borrowed).label == "Unknown"
    # An extension that states its own unit (JWST ERR) keeps it
    assert data_keywords(_extension({'BUNIT': 'MJy/sr'}), science)['BUNIT'] == 'MJy/sr'


@pytest.mark.parametrize("dtype", ['uint8', 'int16', 'int32', 'bool'])
def test_integer_data_borrow_no_unit(dtype):
    """Integer data are flags (the OSIRIS quality extension is uint8, JWST DQ int32): a flag
    value has no unit, so the primary's `BUNIT` and integration time are not lent to it, and
    Total DN is not offered for it."""
    primary = _primary({'TRUITIME': 899.92, 'COADDS': 1, 'BUNIT': 'ADU per second per coadd'})

    keywords = data_keywords(_extension({}), primary, np.dtype(dtype))

    assert len(keywords) == 0
    assert data_unit(keywords).label == "Unknown"
    assert total_dn_multiplier(keywords) is None
    # The same primary lends both to float data (the OSIRIS noise extension)
    floats = data_keywords(_extension({}), primary, np.dtype('float32'))
    assert data_unit(floats).label == "DN/s"
    assert total_dn_multiplier(floats) == pytest.approx(899.92)
    # Integer data that state their own unit keep it
    own = data_keywords(_extension({'BUNIT': 'DN'}), primary, np.dtype(dtype))
    assert own['BUNIT'] == 'DN'


def test_world_axis_label(nifs_like_header):
    """The readout's name and unit for each world axis: astropy's physical type where it has
    one, else the IRAF `axtype`, else the CTYPE; an unstated unit is `""`, never a guess."""
    wcs, _ = build_wcs(nifs_like_header)
    assert world_axis_label(wcs, nifs_like_header, 0) == ('xi', "")
    assert world_axis_label(wcs, nifs_like_header, 1) == ('eta', "")

    nifs_like_header['WAT1_001'] = 'wtype=linear axtype=xi units=arcsec'
    del nifs_like_header['WAT2_001']
    wcs, _ = build_wcs(nifs_like_header)
    assert world_axis_label(wcs, nifs_like_header, 0) == ('xi', 'arcsec')
    assert world_axis_label(wcs, nifs_like_header, 1) == ('LINEAR', "")

    header = _axes_header('RA---TAN', 'DEC--TAN', 'WAVE')
    wcs, _ = build_wcs(header)
    assert world_axis_label(wcs, header, 2) == ('em.wl', 'm')

    # wcslib gives a WAVE axis with no CUNIT3 the FITS default `m`; the file did not say so.
    del header['CUNIT3']
    wcs, _ = build_wcs(header)
    assert world_axis_label(wcs, header, 2) == ('em.wl', "")
