# Implementation plan — align the Strehl tool with the KAI reference

Reference: [`kai/strehl.py`](https://github.com/Keck-DataReductionPipelines/KAI/blob/dev/kai/strehl.py)
(Keck AO Imaging pipeline, BSD 3-Clause, authors J.R. Lu, A. Gautam, T. Do).
Ours: `pyql3/analysis/strehl.py` + `pyql3/gui/tools/strehl.py`.

## Recommendation

**Adopt KAI's measurement and normalisation; keep our synthetic reference PSF; add an IFS
instrument-metadata layer.** Do not vendor KAI wholesale and do not depend on it.

The reason is that the two codebases disagree about what data they are for. KAI's Strehl path
is built for the **OSIRIS imager / NIRC2** at ~0.00995 "/pix with an empirical
diffraction-limited image per *imaging* filter. pyql3 shows the **OSIRIS spectrograph** —
0.020–0.100 "/pix, narrow-band IFS filters at arbitrary central wavelengths. KAI's
instrument layer reads keywords that are absent or meaningless in our headers, and it has no
reference image for our filters. What transfers cleanly is the *method*, which is the part
that matters and the part ours gets wrong.

---

## What the two implementations actually do

| | KAI (`kai/strehl.py`) | pyql3 (`analysis/strehl.py`) |
|---|---|---|
| Target data | OSIRIS imager / NIRC2 frames, ~0.00995 "/pix | OSIRIS IFS cube slices, 0.020–0.100 "/pix |
| Entry point | `calc_strehl(file_list, out_file, apersize=0.3, instrument)` — batch, reads `.fits` + `.coo`, writes a text table | `calculate_strehl(image, pos, skyval, photrad, camname, effwave)` — single position, in-memory array |
| DL reference | **Empirical** FITS per filter, `kai/data/diffrac_lim_img/Keck/<filter>.fits`, resampled with `scipy.ndimage.zoom(order=3)` | **Synthetic** — Keck pupil model (`generate_pupil_mask`, hex segments) → FFT → `generate_psf` |
| Strehl statistic | peak pixel / wide-aperture flux, normalised by the same on the DL image | (fitted peak / aperture flux) / same on the synthetic PSF |
| Peak measurement | max pixel in a cutout (`Cutout2D`, `mode='strict'`) | **amplitude of a 1-D Gaussian fitted to the radial profile** of the core |
| Sky | `CircularAnnulus(r+20, r+30)` subtracted from the aperture sum | scalar `skyval` (GUI passes the whole-image median) |
| FWHM | bounded 2-D `Gaussian2D + Const2D`, LevMar, box grown up to 30 iterations until FWHM is in range | from the same 1-D radial fit |
| Aperture | `radius = ceil(apersize/scale)`, min 3 px; default 0.3″ | `ap_radius = photrad/pscl`; default 0.5″ |
| Also reports | RMS WFE via Maréchal, MJD; sentinel `-1` on failure | star/PSF FWHM, radial profiles for plotting |
| Sub-pixel star position | not modelled | **modelled** — PSF generated at the measured sub-pixel offset |
| Failure handling | range-checks Strehl and FWHM, returns `-1.0` | returns `None` for edge cases; otherwise unchecked |

The single most important structural difference: **KAI never fits the diffraction core to get
the peak.** It takes the brightest pixel. That is what makes it work on undersampled data,
and it is precisely where ours breaks (below).

---

## Evidence gathered while comparing

All measured against the working tree and the real cube
`s150531_a025002_Kn5_035.fits` (Kn5, SSCALE 0.035, 465 channels; see `PYQL3_TEST_CUBE` in
`AGENTS.md`).

### 1. Our tool produces an impossible Strehl on real IFS data

With the values the GUI actually passes today (`camname='0.020'`, `effwave=2.1245`):

```
strehl = 2.0382     star_fwhm = 0.0000"     psf_fwhm = 0.0494"
```

A Strehl ratio above 1 is unphysical, and the star FWHM is zero. This is not a small
calibration error — the number is meaningless.

### 2. With the *correct* header values it crashes

`camname='0.035'`, `effwave=2.350`:

```
TypeError: Improper input: func input vector length N=2 must not exceed
           func output vector length M=1
  at fit_radial_profile -> astropy LevMarLSQFitter
```

`fit_radial_profile` masks to `r < 0.7 * fwhm0/pscl`. At 0.035 "/pix that threshold is
0.97 px, so **one** pixel survives and a 2-parameter Gaussian cannot be fitted. This is a
third defect, independent of B8.

### 3. The IFS is undersampled — the core cannot be fitted at all

Airy FWHM at 2.35 µm is 0.0498″, so:

| SSCALE | DL FWHM (″) | DL FWHM (px) | KAI aper r (px) | px inside our fit threshold |
|---|---|---|---|---|
| 0.020 | 0.0498 | 2.49 | 15 | 9 |
| 0.035 | 0.0498 | 1.42 | 9 | **1** — fit impossible |
| 0.050 | 0.0498 | 1.00 | 6 | **1** — fit impossible |
| 0.100 | 0.0498 | 0.50 | 3 | **1** — fit impossible |

Nyquist wants ~2 px per FWHM. Only the 0.020 scale critically samples the K-band core;
every other IFS scale undersamples it. **Any approach that fits the diffraction core is
unusable for three of the four OSIRIS IFS plate scales.** KAI's peak-pixel statistic needs
no resolved core, which is the decisive argument for adopting it.

### 4. Our headers do not contain the keywords B8 assumes

Real Kn5 cube:

```
CURRINST = 'OSIRIS'      INSTR    = 'spec'
SFILTER  = 'Kn5'         SSCALE   = '0.035'      (strings)
WAVECNTR = 2350.0        WAVEBLUE = 2292.0   WAVERED = 2408.0   TARGWAVE = '2.350'
CAMNAME  = <absent>      EFFWAVE  = <absent>
IFILTER  = 'Hn1 ? Kn1'                        <- meaningless for a spec exposure
```

`CAMNAME`/`EFFWAVE` are NIRC2/imager keywords. **The fix proposed in BUGS.md B8 — read
`CAMNAME` and `EFFWAVE` from the real header — would still not work on OSIRIS IFS data.**
The IFS equivalents are `SSCALE` and `WAVECNTR` (nm). Note both scale keywords are *strings*
and need parsing.

Note also that `IFILTER` is populated with junk (`'Hn1 ? Kn1'`), so KAI's
`OSIRIS.get_filter_name()` would return that string and look for `hn1 ? kn1.fits`.

### 5. Correction to B8's PSF-centre table

B8 tabulates the `generate_psf` output sizes. Measured:

| camname | actual shape | true peak | assumed `(128,128)` | B8 claims |
|---|---|---|---|---|
| 0.020 | 256×256 | (127,127) | off by 1 px | 256², ✓ |
| 0.035 | **256×256** | (127,127) | off by 1 px | ~~512², ✗~~ **wrong** |
| 0.050 | 512×512 | (255,255) | off by 127 px | not listed |
| 0.100 | 1024×1024 | (511,511) | off by 383 px | 1024², ✗ |

So the badly broken cameras are **0.050 and 0.100**, not 0.035 — and *every* camera is off by
one pixel, because the pupil is centred on `npix/2 - 0.5`. Our test cube is 0.035, so this
particular bug is not what produces the 2.0382 above.

There is a second, related defect in the same area that B8 only hints at: `ap_radius =
photrad / pscl` uses the **detector** plate scale but is applied to the **PSF** array, whose
own scale is `pscl * rdfac / rpfac`. For 0.050 and 0.100 those differ by a factor of 2 and 4,
so the reference aperture encloses the wrong solid angle.

### 6. KAI cannot be used as a dependency

- **Not on PyPI.** `pypi.org/project/kai` is an unrelated file-extraction utility by a
  different author. It would have to be a git dependency.
- **Pins conflict hard.** KAI `requirements.txt`: `numpy~=1.24.3`, `astropy~=5.3`,
  `photutils~=1.8.0`, `scipy~=1.10.1`. pyql3: `numpy>=2.5.1`, `astropy>=8.0.1`,
  `photutils>=3.0.0`, `scipy>=1.18.0`. `setup.cfg` still carries
  `#python_requires = 2.7 … But MUST be true`.
- **Import side effects.** `kai/strehl.py` does `import matplotlib.pyplot as plt` and
  `import pdb` at module scope, and `from kai import instruments` drags in the distortion/mask
  file tree. Importing pyplot inside a running Qt app risks grabbing a backend.
- **License is fine.** BSD 3-Clause, so *adapting* the algorithm with attribution is clean.
  That is the route this plan takes.

### 7. If we ever want KAI's empirical references

`kai/data/diffrac_lim_img/Keck/` holds 19 entries, 512×512 float32, ~1.05 MB each (~19 MB
total), sampled at 0.009952 "/pix:

```
brgamma co_2-0_bh feii h hbb hcont heib j jcont k kcont kn3 kp-lhex kp ks lp ms nb2.108 pabeta
```

Two are **git symlinks**, not files (the GitHub API reports them as `type=file` with the
target-path length as the size, which is why they look like 7- and 9-byte corrupt files):
`kn3.fits -> kp.fits` and `co_2-0_bh.fits -> ./kp.fits`. So even Keck approximates the
narrow-band Kn3 reference with broadband Kp.

**There is no `kn5.fits`** — nor any entry for most OSIRIS IFS filters. The nearest available
to Kn5 (2.292–2.408 µm) is `kcont` at 2.270 µm. This is the reason to keep a synthetic,
wavelength-parameterised reference rather than adopt KAI's image library.

---

## Plan

### Phase 0 — Instrument metadata layer (prerequisite; supersedes B8)

New `pyql3/analysis/instrument.py`, one function resolving a FITS header to the physical
parameters the Strehl maths needs, with the instrument mode detected rather than assumed:

```python
def resolve_instrument(header, wcs=None):
    """-> dict(plate_scale, wavelength_um, filter_name, telescope_diam, mode, source)"""
```

- **OSIRIS spectrograph** (`CURRINST=='OSIRIS'` and `INSTR=='spec'`): `plate_scale` from
  `SSCALE` (string → float), `wavelength_um` from `WAVECNTR`/1000, falling back to
  `TARGWAVE`, then `(WAVEBLUE+WAVERED)/2000`, `filter_name` from `SFILTER`.
- **OSIRIS imager** (`INSTR=='imag'`): `IFILTER`, and KAI's date-dependent scale
  (0.0099418 before 2020-11-16, 0.0099576 after).
- **NIRC2 / generic**: `CAMNAME`, `EFFWAVE` — i.e. B8's original assumption, kept for the
  instruments where those keywords really exist.
- **Cross-check** the plate scale against the WCS (`CDELT`/`CD` on the spatial axes), which
  our cubes always have, and record which source won in `source` so the GUI can display it.
- Report `wavelength_um` per *channel* when a cube slice is being measured, not just the
  filter centre — the WCS already gives this via `get_wavelength_for_slice`.

`gui/tools/strehl.py` then reads `self.image_viewer.header` (the real header, already stored
by `set_data`) instead of `self.image_viewer.wcs.to_header()`. **This closes B8** and fixes
the wrong-camera/wrong-wavelength defaults.

### Phase 1 — Port KAI's measurement core

Add to `pyql3/analysis/strehl.py`, adapted from KAI with attribution in the module docstring:

- `peak_flux_ratio(img, coords, radius, skysub=True, sky_pad=(20, 30))` — `Cutout2D` peak,
  `CircularAperture` sum, `CircularAnnulus` sky subtraction. Direct port of
  `calc_peak_flux_ratio`.
- `fit_gaussian2d(img, coords, boxsize, fwhm_min, fwhm_max, pos_delta_max)` — bounded
  `Gaussian2D + Const2D` with `LevMarLSQFitter`, mapping the fitted centre back to image
  coordinates via `Cutout2D.to_original_position`. Direct port.
- `fwhm_with_box_growth(img, coords, dl_res_in_pix)` — KAI's `while` loop that grows the box
  until the FWHM lands in range, capped at 30 iterations, updating `coords` when the fit stays
  within the box.
- `rms_wfe_from_strehl(strehl, wavelength_um)` — Maréchal,
  `sqrt(-ln(strehl)) * λ * 1e3 / 2π` nm.
- Range-check and fail gracefully as KAI does, but return `None` plus a *reason string*
  rather than `-1.0` sentinels, so the dialog can say why.

Then rewrite `calculate_strehl` to use peak/aperture ratios as the primary statistic. Keep
the radial profile **only** as plot data for the existing `star_plot`/`psf_plot` curves — it
stops being an input to the Strehl number.

### Phase 2 — Repair the synthetic reference PSF

Keep `generate_psf`; it is parameterised by wavelength and plate scale (which the IFS filter
set requires) and it models the star's sub-pixel offset. Fix its three defects:

1. Derive the centre from the array actually returned instead of hardcoding `(128, 128)` —
   `psf_center = (psf.shape[1] // 2, psf.shape[0] // 2)`, or better, locate the peak.
2. Return the PSF's own pixel scale alongside the array (`pscl * rdfac / rpfac`) and compute
   the reference aperture radius in *those* pixels.
3. Assert the returned scale is what the caller assumes, so a future factor change is loud.

Optionally add `load_empirical_reference(path, scale_dl=0.009952)` mirroring KAI's zoom-based
resampling, for the cross-validation in Phase 4 and for imaging filters where a Keck empirical
PSF exists. No vendored data required unless we choose it (see Decisions).

### Phase 3 — Handle undersampling honestly

- Compute `dl_res_in_pix` as KAI does and, when the core is undersampled (≲2 px per FWHM),
  mark the FWHM readout as unreliable rather than printing a confident number.
- Keep generating the reference at the measured sub-pixel offset. On undersampled data the
  peak-pixel statistic depends on where the star sits within a pixel, and this is the one
  place our implementation is **better** than KAI's — the reference and the science star are
  then phased alike, so the intra-pixel term largely cancels.
- Surface the aperture radius, filter, plate scale and wavelength actually used in the dialog.

### Phase 4 — Validation

1. **Cross-validate against KAI** on data where both are valid: an OSIRIS imager / NIRC2
   frame in a filter KAI ships (`kp`), at ~0.00995 "/pix. Run KAI in a throwaway venv with
   its own pins, compare Strehl and FWHM, and record the agreement in the test docstring.
   This is the only way to know our port is faithful.
2. **Synthetic recovery tests**: feed `calculate_strehl` a known input — the DL PSF itself
   (expect Strehl ≈ 1), and the DL PSF convolved with a Gaussian of known width (expect a
   predictable reduction). Assert `0 < strehl <= 1`.
3. **Real-cube regression**: assert the Kn5 cube yields a physical Strehl (currently 2.0382)
   and does not raise, at every one of the four `SSCALE` values (currently three crash).
4. **Header-resolution tests**: `resolve_instrument` on the real OSIRIS spec header, a
   synthetic imager header and a NIRC2-style header.
5. Keep the existing `tests/test_analysis_tools.py` Strehl coverage green.

### Phase 5 — Dialog wiring

Report Strehl, FWHM (mas), RMS WFE (nm), the aperture radius used, and the resolved
filter/scale/λ with their source. Add the undersampling flag from Phase 3. Route the plate
scale through `resolve_instrument` so the *photometric radius* spinbox is interpreted in
arcsec consistently.

---

## Decisions needed

1. **Reference PSF policy.** Synthetic only (recommended — covers arbitrary IFS
   wavelengths); or vendor KAI's 19 empirical FITS (~19 MB, needs `datas` entries in
   `QuickLook3.spec` and the `build_app.sh`/CI verification step extended, and still has no
   Kn5); or ship both with a selector in the dialog.
2. **Telescope diameter.** KAI uses **10.5 m**; our `fwhm0` uses **10.0 m**. This scales the
   theoretical FWHM and hence the reported numbers directly. 10.0 matches the ql2 heritage;
   10.5 matches Keck's published Strehl numbers. Which do you want to be comparable to?
3. **Aperture default.** KAI's `apersize` default is 0.3″; our `photrad` default is 0.5″.
   Align on 0.3″, or keep 0.5″ for ql2 continuity?
4. **Keep the legacy number?** Should the old radial-profile Strehl remain visible as a
   secondary readout for continuity with ql2, or be removed entirely?

## Risks

- **No ground truth for IFS Strehl.** Phase 4's cross-validation only covers the imaging
  regime. For 0.035–0.100 "/pix we can prove internal consistency and physical plausibility,
  not absolute accuracy. Worth stating in the docs rather than implying the numbers are
  calibrated.
- **Undersampled Strehl is intrinsically uncertain.** At 0.100 "/pix the core is half a pixel;
  a peak-pixel statistic there carries a large intra-pixel systematic even with the phased
  reference. Consider refusing to report Strehl below some sampling threshold instead of
  reporting a precise-looking wrong number.
- **Porting drift.** KAI's `dev` branch is a moving target. Pin the commit we ported from in
  the module docstring.

## Sequencing

Phase 0 is a prerequisite for everything and on its own converts a meaningless number into a
correctly-parameterised one, so it is worth landing first even if the rest waits. Phase 1 is
the substantive change. Phases 2–3 are small once 0 and 1 exist. Phase 4 is where the real
cost sits, because of the KAI cross-validation environment.

B8 should be closed by Phase 0 + Phase 2 rather than by the fix currently written in its
entry, which targets keywords our data does not have.
