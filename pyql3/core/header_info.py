"""Header facts that more than one part of the application needs, read in one place.

No Qt import, so these are testable without a display.
"""
import math
import re
import warnings
from dataclasses import dataclass

import astropy.units as u
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def _positive_finite(value):
    """Return `value` as a float when it is a finite number above zero, else None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number > 0:
        return number
    return None


def integration_seconds(header):
    """Integration time of one coadd, in seconds, or None when the header states none.

    `TRUITIME` is in seconds. `ITIME` is in milliseconds in OSIRIS cubes written since the
    detector upgrade (`ITIME = 900000` beside `TRUITIME = 899.92`) but in seconds in older
    ones, so `ITIME` is used only when there is no usable `TRUITIME`.
    """
    for key in ('TRUITIME', 'ITIME'):
        seconds = _positive_finite(header.get(key))
        if seconds is not None:
            return seconds
    return None


def total_dn_multiplier(header):
    """Factor that converts DN/s to Total DN, or None when the header states no time.

    Integration seconds (`integration_seconds`) times `COADDS`; a missing, non-finite or
    non-positive `COADDS` counts as 1. Without `TRUITIME` or `ITIME`, `EXPTIME`, which NIFS
    writes as the time summed over all coadds, so it is not multiplied by `COADDS`.
    """
    seconds = integration_seconds(header)
    if seconds is None:
        return _positive_finite(header.get('EXPTIME'))
    coadds = _positive_finite(header.get('COADDS'))
    return seconds * (coadds if coadds is not None else 1.0)


@dataclass(frozen=True)
class DataUnit:
    """The unit the data values are in, as the file states it (`data_unit`).

    `label` is what every unit readout shows. `is_rate` is True for a count per unit time,
    which is what Total DN converts, False for any other unit, and None when the unit is
    not known. `reason` is why Total DN is not available, `""` when it is.
    """
    label: str
    is_rate: bool | None
    reason: str


#: Units that count events, so that one of them per second is a count rate
COUNT_UNITS = (u.DN, u.adu, u.ct, u.electron, u.photon)

#: OSIRIS writes `BUNIT = 'ADU per second per coadd'`, which no unit parser reads. Only text
#: that names the counts ADU or DN is read this way, so "erg per second" is not a count rate.
_COUNTS_PER_SECOND = re.compile(r'^(ADU|DN)\b.*\bper\s+second\b', re.IGNORECASE)

UNKNOWN_UNIT = "Unknown"


def _is_count_rate(unit):
    """True when `unit` is one of `COUNT_UNITS` per second (`DN/s`, `electron/s`).

    Per second exactly, because `total_dn_multiplier` is in seconds: a count per millisecond
    or per minute times it is not Total DN. A flux such as `erg/s/cm2/Angstrom` is per second
    too, but multiplying it by the exposure time does not give a count.
    """
    counts = unit * u.s
    return any(counts.is_equivalent(count) and math.isclose(counts.to(count), 1.0)
               for count in COUNT_UNITS)


def data_unit(header):
    """The unit `header`'s `BUNIT` states, and whether Total DN can be shown (R83).

    OSIRIS's `ADU per second ...` text is DN/s. Any other `BUNIT` is labelled as the file wrote
    it; one astropy cannot read (`ELECTRONS/S`, `counts/s`) is not taken to be a rate. A
    missing `BUNIT` is Unknown: a unit the file does not state is never inferred, even when the
    values look like counts (NIFS). Total DN needs a count rate and an integration time
    (`total_dn_multiplier`); `reason` says which one is missing.
    """
    text = header.get('BUNIT') if header is not None else None
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return DataUnit(UNKNOWN_UNIT, None, "no unit is stated for these data")
    if _COUNTS_PER_SECOND.search(text):
        label, is_rate = "DN/s", True
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                unit = u.Unit(text)
        except ValueError:
            unit = None
        if unit is None or unit == u.dimensionless_unscaled:
            return DataUnit(text, None, f"the file's unit '{text}' is not recognised")
        label, is_rate = text, _is_count_rate(unit)
    if not is_rate:
        return DataUnit(label, False, f"data are in {label}")
    if total_dn_multiplier(header) is None:
        return DataUnit(label, True, "no integration time in the header")
    return DataUnit(label, True, "")


#: Keywords that state an integration time. `EXPTIME` is where NIFS keeps it.
INTEGRATION_KEYWORDS = ('TRUITIME', 'ITIME', 'EXPTIME')


def states_integration_time(header):
    """True when `header` has a usable (finite, positive) `TRUITIME`, `ITIME` or `EXPTIME`."""
    if header is None:
        return False
    return any(_positive_finite(header.get(key)) is not None for key in INTEGRATION_KEYWORDS)


def is_primary_header(header):
    """True for the header of a file's primary HDU, which begins with `SIMPLE`; an
    extension's begins with `XTENSION` instead (FITS standard, section 4.4.1)."""
    return header is not None and 'SIMPLE' in header and 'XTENSION' not in header


def _is_integer(dtype):
    """True for integer and boolean data, the types a file stores flags in."""
    if dtype is None:
        return False
    dtype = np.dtype(dtype)
    return np.issubdtype(dtype, np.integer) or np.issubdtype(dtype, np.bool_)


def data_keywords(header, reference=None, dtype=None):
    """A new header holding the keywords that describe the data values, for an extension
    whose own header may not state them (R25).

    The integration-time keywords and `COADDS` are copied together from `header` when it
    states an integration time, else from `reference` when that does, so one header's time
    is never multiplied by another's `COADDS`. `BUNIT` is copied from `header`, else from
    `reference`. Either argument may be None.

    `reference` lends nothing unless it is the primary header (`is_primary_header`) and
    `dtype`, the data's type, is not an integer type. OSIRIS writes the unit and the time of
    its noise extension in the primary, but a sibling extension describes only its own values:
    JWST's SCI is in MJy/sr, while the DQ, WMAP and VAR_* extensions beside it are flags,
    weights and squared units. Integer data are flags (the OSIRIS quality extension, JWST DQ),
    and a flag value has no unit. What is not lent reads as Unknown (`data_unit`).
    """
    if not is_primary_header(reference) or _is_integer(dtype):
        reference = None
    keywords = fits.Header()
    exposure = header
    if not states_integration_time(header) and states_integration_time(reference):
        exposure = reference
    for key in INTEGRATION_KEYWORDS + ('COADDS',):
        if exposure is not None and key in exposure:
            keywords[key] = (exposure[key], exposure.comments[key])
    for source in (header, reference):
        if source is not None and 'BUNIT' in source:
            keywords['BUNIT'] = (source['BUNIT'], source.comments['BUNIT'])
            break
    return keywords


def _with_micron_as_um(header):
    """Copy of `header` whose `CUNITn` values spelled micron or microns are `um`.

    wcslib parses `um` but not `micron`, though astropy's own FITS unit table does, so a
    cube that says `CUNIT1 = 'micron'` is otherwise refused whole.
    """
    fixed = header.copy()
    for key in fixed:
        if key.startswith('CUNIT') and key[5:].isdigit():
            value = fixed[key]
            if isinstance(value, str) and value.strip().lower() in ('micron', 'microns'):
                fixed[key] = 'um'
    return fixed


def _read_wcs(header):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        return WCS(header)


def build_wcs(header):
    """Return `(wcs, problem)` for a FITS header. Never raises.

    `wcs` is None when there is no header or astropy cannot read it, and `problem` is the
    exception message in the second case, `""` otherwise. A `CUNITn` of `micron` or
    `microns` is read as `um` on a second attempt; the caller's header is not changed.
    """
    if not header:
        return None, ""
    try:
        return _read_wcs(header), ""
    except Exception as first:
        problem = str(first) or type(first).__name__
    try:
        return _read_wcs(_with_micron_as_um(header)), ""
    except Exception:
        return None, problem


def _wat_card(header, axis):
    """The `WAT<n>_001` string IRAF writes for 0-based `axis`, or ""."""
    if header is None:
        return ""
    value = header.get(f'WAT{axis + 1}_001', "")
    return value if isinstance(value, str) else ""


def _wat_value(header, axis, key):
    """The value of `key=` in the `WAT<n>_001` card of 0-based `axis`, or None."""
    match = re.search(rf'(?:^|\s){key}=(\S+)', _wat_card(header, axis))
    return match.group(1) if match else None


def _celestial_axes(wcs):
    if wcs is None:
        return ()
    return tuple(axis for axis in (wcs.wcs.lng, wcs.wcs.lat) if axis >= 0)


def spectral_axis(wcs, header=None):
    """The 0-based FITS axis that is spectral, or None.

    astropy's `wcs.wcs.spec` covers every standard spectral type (`WAVE`, `AWAV`, `FREQ`,
    ...). IRAF-style files (Gemini NIFS) write `CTYPEn = 'LINEAR'`, which astropy does not
    recognise; they name the axis in `WAT<n>_001` (`axtype=wave`) or in `DISPAXIS`, 1-based.
    A `DISPAXIS` that names a celestial axis is not believed.
    """
    if wcs is not None and wcs.wcs.spec >= 0:
        return int(wcs.wcs.spec)
    naxis = wcs.naxis if wcs is not None else 0
    for axis in range(naxis):
        if (_wat_value(header, axis, 'axtype') or "").lower() == 'wave':
            return axis
    try:
        dispaxis = int(header.get('DISPAXIS')) - 1 if header is not None else None
    except (TypeError, ValueError):
        dispaxis = None
    if dispaxis is not None and 0 <= dispaxis < naxis and dispaxis not in _celestial_axes(wcs):
        return dispaxis
    return None


def has_world_axes(wcs, header=None):
    """True when `wcs` has a celestial axis or a spectral axis (`spectral_axis`, which also
    reads IRAF cards from `header`). A header with no WCS cards still gives astropy a WCS,
    with no axis types; that has none."""
    return bool(_celestial_axes(wcs)) or spectral_axis(wcs, header) is not None


def world_axis_label(wcs, header, axis):
    """`(name, unit)` for the readout of 0-based world `axis`; `unit` is `""` when unstated.

    The name is astropy's physical type (`pos.eq.ra`, `em.wl`, ...). An IRAF `LINEAR` axis has
    none, so the name is the `axtype=` of its `WAT<n>_001` card (`xi`, `eta` in a NIFS cube),
    else its `CTYPE<n>`, else `AXIS <n>`. The unit is the one the WCS holds for the axis, else
    the `units=` of the WAT card, else `""`. A spectral axis takes its unit from
    `spectral_unit`, so the FITS default wcslib fills in for a missing `CUNIT<n>` is not shown.
    """
    name = wcs.world_axis_physical_types[axis]
    if not name:
        name = (_wat_value(header, axis, 'axtype')
                or str(wcs.wcs.ctype[axis]).strip()
                or f"AXIS {axis + 1}")
    if axis == spectral_axis(wcs, header):
        unit = spectral_unit(wcs, header, axis)
        return name, (unit.to_string() if unit is not None else "")
    unit = str(wcs.world_axis_units[axis] or "").strip()
    if not unit:
        unit = _wat_value(header, axis, 'units') or ""
    return name, unit


def _parse_unit(text):
    """An astropy unit for a unit name as a FITS or IRAF header writes it, or None.

    IRAF writes plurals (`angstroms`, `microns`), which astropy does not parse.
    """
    text = (text or "").strip()
    if not text:
        return None
    for candidate in (text, text[:-1] if text.lower().endswith('s') else None):
        if not candidate:
            continue
        try:
            unit = u.Unit(candidate)
        except ValueError:
            continue
        if unit != u.dimensionless_unscaled:
            return unit
    return None


def spectral_unit(wcs, header, axis):
    """The unit of 0-based `axis` when the header states one, else None.

    The header states a unit in its own `CUNIT<n>` card or, IRAF-style, in `units=` in
    `WAT<n>_001`. Nothing else counts: wcslib gives a standard spectral axis with no `CUNIT<n>`
    the FITS default (`m` for `WAVE`, `Hz` for `FREQ`), but the file did not say so, and a unit
    the file does not state is never inferred — callers label it Unknown.

    When `CUNIT<n>` is stated, the unit returned is the one the WCS holds for the axis, because
    wcslib rescales a spectral axis to SI (`nm` and `um` both become `m`) and the WCS's values
    are in that unit.
    """
    if axis is None:
        return None
    stated = _parse_unit(str(header.get(f'CUNIT{axis + 1}', ""))) if header is not None else None
    if stated is not None:
        if wcs is not None and 0 <= axis < wcs.naxis:
            return _parse_unit(str(wcs.wcs.cunit[axis])) or stated
        return stated
    return _parse_unit(_wat_value(header, axis, 'units'))


def in_microns(value, unit):
    """`value`, a spectral coordinate in `unit`, as a wavelength in µm.

    None when the unit is unknown (None) or is not a spectral unit, such as a velocity.
    Accepts a number or an array.
    """
    if unit is None:
        return None
    try:
        return (value * unit).to_value(u.um, equivalencies=u.spectral())
    except u.UnitConversionError:
        return None


def _is_osiris(header):
    if header is None:
        return False
    return any(str(header.get(key, "")).strip().upper() == 'OSIRIS'
               for key in ('INSTRUME', 'CURRINST'))


def default_axes(wcs, header):
    """The 0-based FITS axes to show as (X, Y, Z) when a cube is opened (R81).

    1. Celestial axes: X is longitude, Y latitude, Z the remaining axis. This gives OSIRIS
       (wavelength, Dec, RA) X = AXIS 3, and JWST (RA, Dec, wavelength) X = AXIS 1.
    2. A spectral axis (`spectral_axis`): Z is it, X and Y the other two in FITS order.
    3. An OSIRIS file: OSIRIS's layout, (2, 1, 0).
    4. Otherwise FITS axes 1, 2, 3 are x, y and wavelength, as most IFUs write them.
    """
    lng = wcs.wcs.lng if wcs is not None else -1
    lat = wcs.wcs.lat if wcs is not None else -1
    if lng in (0, 1, 2) and lat in (0, 1, 2) and lng != lat:
        return (lng, lat, 3 - lng - lat)
    spectral = spectral_axis(wcs, header)
    if spectral in (0, 1, 2):
        x, y = (axis for axis in (0, 1, 2) if axis != spectral)
        return (x, y, spectral)
    if _is_osiris(header):
        return (2, 1, 0)
    return (0, 1, 2)
