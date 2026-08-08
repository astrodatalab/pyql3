"""Per-channel aperture photometry (`pyql3/core/spectral_photometry.py`).

No Qt here: this is the arithmetic behind the Depth Plot's circular extraction, and the
point of keeping it Qt-free is that it can be checked against a hand-computed answer and
against the Photometry tool's independent implementation.
"""

import numpy as np
import pytest

from pyql3.core.spectral_photometry import (
    ApertureError,
    annulus_background,
    annulus_spectrum,
    aperture_spectrum,
)


def _cube(nz=6, nx=41, ny=41, background=3.0):
    """A flat cube in (z, x, y) display order."""
    return np.full((nz, nx, ny), float(background))


def _add_source(cube, cx, cy, flux_per_channel, radius=2.0):
    """Put `flux_per_channel` of signal inside `radius` of (cx, cy), spread evenly."""
    nx, ny = cube.shape[1], cube.shape[2]
    xx, yy = np.mgrid[:nx, :ny]
    inside = ((xx - cx) ** 2 + (yy - cy) ** 2) <= radius ** 2
    per_pixel = np.asarray(flux_per_channel, dtype=float) / inside.sum()
    cube[:, inside] += per_pixel[:, None]
    return inside.sum()


# ------------------------------------------------------------------ the flat case


def test_a_flat_cube_yields_the_background_times_the_area():
    cube = _cube(background=3.0)
    result = annulus_spectrum(cube, (20.0, 20.0), r_ap=5.0, r_in=8.0, r_out=12.0)

    assert result.source == pytest.approx(3.0 * np.pi * 25.0, rel=1e-3)
    assert result.background == pytest.approx(np.full(6, 3.0))
    # Nothing but background, so the subtracted spectrum is zero to floating-point noise.
    assert result.subtracted == pytest.approx(np.zeros(6), abs=1e-9)


def test_the_source_flux_is_recovered_exactly_over_a_flat_background():
    cube = _cube(background=7.5)
    flux = np.array([100.0, 200.0, 50.0, 0.0, -25.0, 10.0])
    _add_source(cube, 20, 20, flux, radius=2.0)

    result = annulus_spectrum(cube, (20.0, 20.0), r_ap=6.0, r_in=10.0, r_out=15.0)

    assert result.subtracted == pytest.approx(flux, rel=1e-6, abs=1e-6)
    assert result.background == pytest.approx(np.full(6, 7.5))


def test_a_sloping_background_is_not_claimed_to_be_exact():
    """A median annulus removes the level, not a gradient -- documented, not fixed."""
    cube = _cube(background=0.0)
    nx = cube.shape[1]
    cube += np.arange(nx, dtype=float)[None, :, None]      # ramp along x
    result = annulus_spectrum(cube, (20.0, 20.0), r_ap=5.0, r_in=8.0, r_out=12.0)

    # The annulus is symmetric about the aperture, so a linear ramp still cancels.
    assert result.subtracted == pytest.approx(np.zeros(6), abs=1e-6)


# ------------------------------------------------------- axis order, the real trap


def test_the_aperture_lands_on_x_y_not_y_x():
    """A non-square cube with an off-centre asymmetric source: (x,y) swaps show up here.

    On a square field with a centred source, a transposed aperture gives the same answer,
    which is exactly how this class of bug survives a weak test.
    """
    cube = _cube(nz=3, nx=61, ny=31, background=0.0)
    flux = np.array([90.0, 60.0, 30.0])
    _add_source(cube, 45, 10, flux, radius=1.5)

    on_source = annulus_spectrum(cube, (45.0, 10.0), r_ap=4.0, r_in=7.0, r_out=11.0)
    assert on_source.subtracted == pytest.approx(flux, rel=1e-6)

    # The transposed position must find nothing -- and is a valid position in this cube,
    # so an out-of-bounds error cannot be what makes this pass.
    swapped = annulus_spectrum(cube, (10.0, 45.0 % 31), r_ap=4.0, r_in=7.0, r_out=11.0)
    assert swapped.subtracted == pytest.approx(np.zeros(3), abs=1e-6)


def test_the_cross_check_against_the_photometry_tool():
    """One plane must agree with `photometry.py`'s independent photutils call.

    The two tools measure the same thing and are written differently; if they ever disagree
    the axis convention has drifted in one of them.
    """
    from photutils.aperture import CircularAnnulus, CircularAperture, aperture_photometry

    cube = _cube(nz=4, nx=51, ny=45, background=2.25)
    _add_source(cube, 30, 18, np.array([500.0, 400.0, 300.0, 200.0]), radius=3.0)
    cx, cy, r_ap, r_in, r_out = 30.0, 18.0, 6.0, 9.0, 14.0

    result = annulus_spectrum(cube, (cx, cy), r_ap, r_in, r_out, combine="Total")

    for k in range(cube.shape[0]):
        # Exactly what PhotometryDialog.update_photometry does, transpose included.
        plane = cube[k].T
        aperture = CircularAperture([(cx, cy)], r=r_ap)
        annulus = CircularAnnulus([(cx, cy)], r_in=r_in, r_out=r_out)
        raw = aperture_photometry(plane, aperture)["aperture_sum"][0]
        mask = annulus.to_mask(method="center")[0]
        values = mask.multiply(plane)[mask.data > 0]
        expected = raw - np.nanmedian(values) * aperture.area

        assert result.subtracted[k] == pytest.approx(expected, rel=1e-9), f"channel {k}"


# ------------------------------------------------------------- combine methods


def test_average_is_the_total_divided_by_the_area():
    cube = _cube(background=1.0)
    _add_source(cube, 20, 20, np.array([80.0] * 6), radius=2.0)

    total = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0, combine="Total")
    average = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0, combine="Average")

    assert average.subtracted == pytest.approx(total.subtracted / total.aperture_area,
                                               rel=1e-6)


def test_median_uses_whole_pixels_and_ignores_a_faint_wing():
    cube = _cube(background=4.0)
    cube[:, 20, 20] += 1000.0                      # one hot pixel
    result = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0, combine="Median")
    # A median over the aperture is unmoved by a single bright pixel.
    assert result.subtracted == pytest.approx(np.zeros(6), abs=1e-9)


def _contaminated_cube(hot_value):
    """Flat at 5.0, with one quadrant raised -- under half the annulus, so the median holds.

    The quadrant is chosen rather than a pixel count because which pixels photutils calls
    "in the annulus" is its boundary rule to decide, not this test's; asserting a count here
    would be testing the reimplementation of a ring, which is not the behaviour under test.
    """
    cube = _cube(nz=1, background=5.0)
    xx, yy = np.mgrid[:41, :41]
    cube[:, (xx < 20) & (yy < 20)] = hot_value
    return cube


def test_the_median_estimator_is_unmoved_by_a_contaminated_quadrant():
    for hot in (50.0, 500.0, 5000.0):
        level, count = annulus_background(_contaminated_cube(hot), (20.0, 20.0), 9.0, 13.0,
                                          estimator="Median")
        assert level[0] == pytest.approx(5.0), f"the median followed a {hot} contaminant"
        assert count > 0


def test_the_average_estimator_does_follow_it():
    """Which is the reason to offer both, and the reason Median is the default."""
    median, _ = annulus_background(_contaminated_cube(500.0), (20.0, 20.0), 9.0, 13.0,
                                   estimator="Median")
    average, _ = annulus_background(_contaminated_cube(500.0), (20.0, 20.0), 9.0, 13.0,
                                    estimator="Average")
    assert average[0] > median[0] + 50.0


def test_the_annulus_pixel_count_is_reported():
    """Not its exact value -- only that it is a sane count the caller can warn on."""
    _, count = annulus_background(_cube(), (20.0, 20.0), 9.0, 13.0)
    ideal = np.pi * (13.0 ** 2 - 9.0 ** 2)
    assert count == pytest.approx(ideal, rel=0.1), f"{count} pixels vs ~{ideal:.0f} expected"


# --------------------------------------------------------------- NaN and edges


def test_a_nan_pixel_in_the_aperture_neither_adds_flux_nor_area():
    cube = _cube(background=2.0)
    clean = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0)
    cube[:, 20, 21] = np.nan
    holed = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0)

    assert np.isfinite(holed.source).all(), "a single NaN wiped out the spectrum"
    # Flat field: the subtracted flux is still zero, and the area shrank by ~one pixel.
    assert holed.subtracted == pytest.approx(np.zeros(6), abs=1e-9)
    assert holed.aperture_area == pytest.approx(clean.aperture_area - 1.0, abs=1e-6)


def test_a_channel_that_is_all_nan_in_the_annulus_yields_nan_not_an_exception():
    cube = _cube(nz=3, background=1.0)
    cube[1] = np.nan
    result = annulus_spectrum(cube, (20.0, 20.0), 5.0, 9.0, 13.0)

    assert np.isnan(result.background[1])
    assert np.isfinite(result.background[[0, 2]]).all(), "one dead plane lost the others"


def test_an_aperture_at_the_edge_measures_the_part_that_overlaps():
    cube = _cube(background=2.0)
    result = annulus_spectrum(cube, (1.0, 1.0), r_ap=4.0, r_in=6.0, r_out=9.0)
    assert np.isfinite(result.source).all()
    assert result.aperture_area < np.pi * 16.0, "clipping did not reduce the area"


def test_geometry_that_cannot_be_measured_is_refused_rather_than_guessed():
    cube = _cube()
    with pytest.raises(ApertureError, match="does not overlap"):
        annulus_spectrum(cube, (500.0, 500.0), 5.0, 9.0, 13.0)
    with pytest.raises(ApertureError, match="inner < outer"):
        annulus_spectrum(cube, (20.0, 20.0), 5.0, r_in=13.0, r_out=9.0)
    with pytest.raises(ApertureError, match="positive"):
        annulus_spectrum(cube, (20.0, 20.0), 0.0, 9.0, 13.0)
    with pytest.raises(ApertureError, match="3-D cube"):
        annulus_spectrum(np.zeros((4, 4)), (2.0, 2.0), 1.0, 2.0, 3.0)


def test_a_background_total_is_not_offered():
    """Summing the annulus and subtracting it per pixel has no physical meaning."""
    with pytest.raises(ApertureError, match="estimator must be"):
        annulus_spectrum(_cube(), (20.0, 20.0), 5.0, 9.0, 13.0, estimator="Total")


def test_without_an_annulus_only_the_aperture_is_measured():
    result = annulus_spectrum(_cube(background=3.0), (20.0, 20.0), r_ap=5.0)
    assert result.background is None and result.subtracted is None
    assert result.source == pytest.approx(3.0 * np.pi * 25.0, rel=1e-3)


def test_the_aperture_alone_agrees_with_the_composed_call():
    cube = _cube(background=3.0)
    _add_source(cube, 20, 20, np.array([10.0] * 6), radius=2.0)
    direct, _ = aperture_spectrum(cube, (20.0, 20.0), 5.0, combine="Total")
    assert direct == pytest.approx(annulus_spectrum(cube, (20.0, 20.0), 5.0).source)
