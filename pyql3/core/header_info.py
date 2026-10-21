"""Header facts that more than one part of the application needs, read in one place.

No Qt import, so these are testable without a display.
"""
import math


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
    """Integration time of one coadd, in seconds.

    `TRUITIME` is in seconds. `ITIME` is in milliseconds in OSIRIS cubes written since the
    detector upgrade (`ITIME = 900000` beside `TRUITIME = 899.92`) but in seconds in older
    ones, so `ITIME` is used only when there is no usable `TRUITIME`. With neither, 1.0.
    """
    for key in ('TRUITIME', 'ITIME'):
        seconds = _positive_finite(header.get(key))
        if seconds is not None:
            return seconds
    return 1.0


def total_dn_multiplier(header):
    """Factor that converts DN/s to Total DN: integration seconds times `COADDS`.

    A missing, non-finite or non-positive `COADDS` counts as 1.
    """
    coadds = _positive_finite(header.get('COADDS'))
    return integration_seconds(header) * (coadds if coadds is not None else 1.0)
