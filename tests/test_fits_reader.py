import io
import os
import stat
import warnings
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


def _find_axis(ctypes, *tokens):
    """FITS axis index (0-based) whose CTYPE starts with any of ``tokens``, else None."""
    for idx, ctype in enumerate(ctypes):
        if any(ctype.startswith(tok) for tok in tokens):
            return idx
    return None


def test_fits_reader_osiris_axis_mapping(real_osiris_fits):
    """Guard the FITS-vs-numpy axis reversal against real OSIRIS data.

    The synthetic fixtures are built to the very convention the code assumes, so
    they cannot catch an axis regression — they would move in lockstep with it.
    This test is the only place the reversal is checked against a file produced by
    the real instrument, which is why it is worth keeping despite needing external
    data (set PYQL3_TEST_CUBE).

    Nothing here is hardcoded to one cube: the expected numpy shape is derived from
    the file's own NAXISn cards, so any OSIRIS-ordered cube exercises it.
    """
    if real_osiris_fits is None:
        pytest.skip("Set PYQL3_TEST_CUBE to a real OSIRIS cube to run this test")

    reader = FitsReader(real_osiris_fits)
    try:
        header = reader.header
        ctypes = [str(c).upper() for c in WCS(header).wcs.ctype]

        # Axes are located by CTYPE, never by a hardcoded index (AGENTS.md).
        wave_ax = _find_axis(ctypes, 'WAVE', 'AWAV')
        dec_ax = _find_axis(ctypes, 'DEC')
        ra_ax = _find_axis(ctypes, 'RA')
        assert None not in (wave_ax, dec_ax, ra_ax), f"Could not identify all axes in {ctypes}"

        # OSIRIS convention: wavelength on FITS axis 1, Dec on 2, RA on 3.
        assert (wave_ax, dec_ax, ra_ax) == (0, 1, 2), (
            f"Expected OSIRIS order (WAVE, DEC, RA) on FITS axes 1/2/3, got {ctypes}"
        )

        # astropy reverses FITS axis order into C-contiguous numpy order, so
        # numpy_index = NAXIS - 1 - fits_index and data.shape is (RA, DEC, WAVE).
        naxis = header['NAXIS']
        assert naxis == 3, f"Expected a 3D cube, got NAXIS={naxis}"
        assert reader.data.ndim == 3

        expected_shape = tuple(header[f'NAXIS{i}'] for i in range(naxis, 0, -1))
        assert reader.data.shape == expected_shape, (
            f"Expected reversed shape {expected_shape} from NAXIS cards, "
            f"got {reader.data.shape}"
        )

        # The load-bearing consequence: wavelength is FITS axis 1 but the LAST
        # numpy axis. Getting this backwards silently transposes every cube.
        wave_np_ax = naxis - 1 - wave_ax
        assert wave_np_ax == 2
        assert reader.data.shape[wave_np_ax] == header['NAXIS1'], (
            "Wavelength channel count is not on the last numpy axis"
        )
        assert reader.data.shape[naxis - 1 - ra_ax] == header['NAXIS3']
        assert reader.data.shape[naxis - 1 - dec_ax] == header['NAXIS2']
    finally:
        reader.close()


@pytest.fixture
def rewritable_fits(tmp_path, rewrite_fits_in_place):
    """A single-HDU file whose contents can be rewritten in place, as a DRP would."""
    path = tmp_path / "live.fits"

    def write(value, extra_card=None, shape=(8, 8)):
        hdu = fits.PrimaryHDU(np.full(shape, value, dtype=np.float32))
        if extra_card:
            hdu.header[extra_card[0]] = extra_card[1]
        if path.exists():
            return rewrite_fits_in_place(path, hdu)
        hdu.writeto(path, overwrite=True)
        return str(path)

    write(10.0)
    return str(path), write


# --- B5: stale cache on same-path reload ------------------------------------------

def test_reload_same_path_picks_up_rewritten_data(rewritable_fits):
    """B5: load() skipped close() when the path was unchanged, so an instrument or DRP
    that rewrites a path in place was served the cached HDUList forever."""
    path, write = rewritable_fits
    reader = FitsReader(path)
    assert reader.get_data().mean() == 10.0

    write(1010.0)
    reader.load(path)

    assert reader.get_data().mean() == 1010.0, "reload served the stale cached HDUList"
    reader.close()


def test_reload_detects_a_rewrite_that_changes_size(tmp_path, rewrite_fits_in_place):
    """The staleness check must not rely on mtime alone."""
    path = str(tmp_path / "resize.fits")
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32)).writeto(path, overwrite=True)
    reader = FitsReader(path)
    assert reader.get_data().shape == (8, 8)

    rewrite_fits_in_place(path, fits.PrimaryHDU(np.ones((32, 32), dtype=np.float32)))
    reader.load(path)

    assert reader.get_data().shape == (32, 32)
    reader.close()


def test_unchanged_file_reuses_the_open_handle(rewritable_fits):
    """Reuse for the byte-identical case is deliberate: it keeps switching extensions
    cheap and preserves header edits that have not been saved yet."""
    path, _ = rewritable_fits
    reader = FitsReader(path)
    handle = reader.hdul

    reader.load(path)

    assert reader.hdul is handle, "an unchanged file should not be reopened"
    reader.close()


def test_force_reopens_even_when_the_file_looks_unchanged(rewritable_fits):
    """'Display -> Redisplay image' passes force=True so it is always a real re-read."""
    path, _ = rewritable_fits
    reader = FitsReader(path)
    handle = reader.hdul

    reader.load(path, force=True)

    assert reader.hdul is not handle
    assert reader.get_data().mean() == 10.0
    reader.close()


def test_header_edits_survive_an_extension_switch(tmp_path):
    """Reopening unconditionally would silently discard pending Header Editor edits."""
    path = str(tmp_path / "multi.fits")
    fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        fits.ImageHDU(np.ones((4, 4), dtype=np.float32), name="SCI"),
    ]).writeto(path, overwrite=True)

    reader = FitsReader(path)
    reader.update_header_card("OBSERVER", "Tuan", ext=0)

    reader.load(path, ext=1)
    reader.load(path, ext=0)

    assert reader.get_header(ext=0).get("OBSERVER") == "Tuan"
    reader.close()


def test_rewritten_file_wins_over_pending_header_edits(rewritable_fits):
    """If the bytes on disk changed, the in-memory edits describe stale data and go."""
    path, write = rewritable_fits
    reader = FitsReader(path)
    reader.update_header_card("OBSERVER", "Tuan")

    write(99.0)
    reader.load(path)

    assert reader.get_data().mean() == 99.0
    assert reader.get_header().get("OBSERVER") is None
    reader.close()


def test_save_to_the_same_path_succeeds_while_open(rewritable_fits):
    """The Header Editor's "save directly to file" writes over the path we hold open.

    Windows refuses to unlink a file with an open handle and astropy implements
    `overwrite=True` as `os.remove()` + create, so this raised
    `PermissionError: [WinError 32]` on Windows only (caught by the Windows CI job).
    `save()` now releases the handle and swaps a temp file in with `os.replace()`.
    """
    path, _ = rewritable_fits
    reader = FitsReader(path)
    data_before = reader.get_data().copy()
    reader.update_header_card("OBSERVER", "Tuan")

    reader.save()                      # same path, file still open

    # save() reopens, so the reader is already consistent with disk
    assert reader.get_header().get("OBSERVER") == "Tuan"
    assert np.array_equal(reader.get_data(), data_before), "save() must not alter the pixels"

    fresh = FitsReader(path)
    assert fresh.get_header().get("OBSERVER") == "Tuan"
    assert np.array_equal(fresh.get_data(), data_before)
    fresh.close()
    reader.close()


def test_save_leaves_no_temp_files_behind(rewritable_fits):
    """The temp file is a sibling of the target, so a leak would litter the data directory."""
    path, _ = rewritable_fits
    directory = os.path.dirname(path)
    before = set(os.listdir(directory))

    reader = FitsReader(path)
    reader.update_header_card("OBSERVER", "Tuan")
    reader.save()
    reader.close()

    assert set(os.listdir(directory)) == before


def test_save_as_a_new_path_keeps_the_original_open(rewritable_fits, tmp_path):
    """Save-As must not silently switch which file the reader is pointing at."""
    path, _ = rewritable_fits
    other = str(tmp_path / "copy.fits")

    reader = FitsReader(path)
    reader.update_header_card("OBSERVER", "Tuan")
    reader.save(other)

    assert reader.filepath == path, "reader should still be on the original file"
    assert os.path.exists(other)

    written = FitsReader(other)
    assert written.get_header().get("OBSERVER") == "Tuan"
    written.close()
    reader.close()


def test_save_preserves_every_extension(tmp_path):
    """Materialising HDUs before releasing the handle must not drop or empty any of them."""
    path = str(tmp_path / "multi.fits")
    fits.HDUList([
        fits.PrimaryHDU(np.full((4, 4), 1.0, dtype=np.float32)),
        fits.ImageHDU(np.full((4, 4), 2.0, dtype=np.float32), name="SCI"),
        fits.ImageHDU(np.full((3, 4, 4), 3.0, dtype=np.float32), name="CUBE"),
    ]).writeto(path, overwrite=True)

    reader = FitsReader(path)
    reader.update_header_card("OBSERVER", "Tuan", ext=1)
    reader.save()
    reader.close()

    with fits.open(path) as hdul:
        assert len(hdul) == 3
        assert hdul[0].data.mean() == 1.0
        assert hdul[1].data.mean() == 2.0
        assert hdul[2].data.shape == (3, 4, 4)
        assert hdul[2].data.mean() == 3.0
        assert hdul[1].header.get("OBSERVER") == "Tuan"


# --- B6: non-image extensions in the dropdown -------------------------------------

@pytest.fixture
def table_ext_fits(tmp_path):
    """Image + table + 3-D image, i.e. a realistic multi-extension product."""
    path = tmp_path / "with_table.fits"
    cols = [fits.Column(name="WAVELENGTH", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        fits.ImageHDU(np.ones((4, 4), dtype=np.float32), name="SCI"),
        fits.BinTableHDU.from_columns(cols, name="WAVETAB"),
        fits.ImageHDU(np.ones((3, 4, 4), dtype=np.float32), name="DQ"),
    ]).writeto(path, overwrite=True)
    return str(path)


def test_get_image_extensions_excludes_tables(table_ext_fits):
    """B6: get_image_extensions() checked only `hdu.data is not None`, so a BINTABLE was
    offered in the Extension combo; selecting it left the previous image on screen while
    data/header switched to the table underneath."""
    reader = FitsReader(table_ext_fits)
    names = [name for _, name in reader.get_image_extensions()]

    assert "WAVETAB" not in names
    assert names == ["PRIMARY", "SCI", "DQ"]
    reader.close()


def test_image_extensions_and_getter_agree(table_ext_fits):
    """The two used to be independent definitions of 'displayable'."""
    reader = FitsReader(table_ext_fits)
    assert reader.image_extensions == reader.get_image_extensions()
    reader.close()


def test_all_extensions_still_lists_the_table(table_ext_fits):
    """get_all_extensions() is a different question and must keep listing everything."""
    reader = FitsReader(table_ext_fits)
    names = [name for _, name in reader.get_all_extensions()]
    assert names == ["PRIMARY", "SCI", "WAVETAB", "DQ"]
    reader.close()


def test_header_only_primary_is_not_offered(tmp_path):
    """A NAXIS=0 primary has no image to show."""
    path = str(tmp_path / "hdr_only.fits")
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.ImageHDU(np.ones((4, 4), dtype=np.float32), name="SCI"),
    ]).writeto(path, overwrite=True)

    reader = FitsReader(path)
    assert [name for _, name in reader.get_image_extensions()] == ["SCI"]
    reader.close()


def test_compressed_image_extension_is_still_offered(tmp_path):
    """The displayability test is header-only, so it must not reject fpacked images."""
    path = str(tmp_path / "comp.fits")
    try:
        fits.HDUList([
            fits.PrimaryHDU(),
            fits.CompImageHDU(np.arange(16, dtype=np.int32).reshape(4, 4), name="COMPSCI"),
        ]).writeto(path, overwrite=True)
    except Exception as exc:                      # pragma: no cover - build without CFITSIO
        pytest.skip(f"compression unavailable: {exc}")

    reader = FitsReader(path)
    assert [name for _, name in reader.get_image_extensions()] == ["COMPSCI"]
    assert reader.data is not None and reader.data.shape == (4, 4)
    reader.close()


def test_extension_discovery_does_not_read_pixel_data(table_ext_fits):
    """With memmap off, probing `hdu.data` during discovery would pull every extension of
    a multi-extension file into memory."""
    reader = FitsReader(table_ext_fits)
    loaded = [i for i, hdu in enumerate(reader.hdul) if hdu._data_loaded]

    assert loaded == [reader.current_ext], \
        f"discovery materialised data for extensions {loaded}, expected only {reader.current_ext}"
    reader.close()


def test_file_without_image_data_reports_no_data_rather_than_inventing_it(tmp_path):
    """A FITS with no displayable image HDU must yield data=None.

    load() used to substitute np.zeros((10, 10)) with an empty Header. Because that
    made `data` never None, it silently disabled every `if data is None` guard in the
    application, and such a file was displayed as a black 10x10 square as if it were a
    real observation of an empty field.
    """
    path = str(tmp_path / "header_only.fits")
    header = fits.Header()
    header['OBJECT'] = 'GC'
    header['ITIME'] = 10.0
    fits.PrimaryHDU(header=header).writeto(path)

    reader = FitsReader(path)
    try:
        assert reader.get_image_extensions() == []
        assert reader.get_data() is None, "must not fabricate an array"
        # The header is still published so the file can be inspected, just not shown.
        assert reader.get_header() is not None
        assert reader.get_header()['OBJECT'] == 'GC'
    finally:
        reader.close()


def test_table_only_file_reports_no_image_data(tmp_path):
    """A BINTABLE has `data is not None`, so it must be excluded by _is_displayable."""
    path = str(tmp_path / "table_only.fits")
    cols = [fits.Column(name="WAVE", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.BinTableHDU.from_columns(cols, name="WAVETAB"),
    ]).writeto(path)

    reader = FitsReader(path)
    try:
        assert reader.get_data() is None
    finally:
        reader.close()


# --- R12: a failed load changes nothing --------------------------------------------

def _two_extension_cube(path):
    fits.HDUList([
        fits.PrimaryHDU(np.full((6, 5, 5), 3.0, dtype=np.float32)),
        fits.ImageHDU(np.full((6, 5, 5), 4.0, dtype=np.float32), name="SCI"),
    ]).writeto(path)


def _write_unreadable(path, kind):
    """A file whose load fails: in the data read ("truncated", a frame still being
    copied) or in `fits.open` itself ("corrupt", a header cut short)."""
    buf = io.BytesIO()
    fits.PrimaryHDU(np.ones((6, 5, 5), dtype=np.float32)).writeto(buf)
    size = {"truncated": 2944, "corrupt": 1000}[kind]
    with open(path, "wb") as f:
        f.write(buf.getvalue()[:size])


@pytest.mark.parametrize("kind", ["truncated", "corrupt"])
def test_a_failed_load_leaves_the_reader_unchanged(tmp_path, kind):
    """load() closed the current HDUList and assigned `filepath` before the data read,
    which is where a partial file fails, so the reader ended up on a file it could not
    read while the window still showed the old one (R12)."""
    cube = str(tmp_path / "good_cube.fits")
    _two_extension_cube(cube)
    bad = str(tmp_path / "arriving.fits")
    _write_unreadable(bad, kind)

    reader = FitsReader(cube)
    reader.load(cube, ext=1)
    hdul = reader.hdul
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # astropy warns about the truncation
            with pytest.raises(Exception):
                reader.load(bad)

        assert reader.filepath == cube
        assert reader.current_ext == 1
        assert reader.get_data() is not None and float(reader.get_data().mean()) == 4.0
        assert reader.hdul is hdul
        # Still open: extension 0 has not been read yet, and reading it needs the handle.
        assert float(reader.hdul[0].data.mean()) == 3.0
    finally:
        reader.close()


def test_require_data_refuses_a_table_only_file(tmp_path):
    """With require_data=True a file with no displayable image raises NoImageData
    before anything is replaced, so the window keeps the file it displays."""
    from pyql3.core.fits_reader import NoImageData

    cube = str(tmp_path / "good_cube.fits")
    _two_extension_cube(cube)
    table_only = str(tmp_path / "table_only.fits")
    cols = [fits.Column(name="WAVE", format="E", array=np.arange(4, dtype=np.float32))]
    fits.HDUList([fits.PrimaryHDU(),
                  fits.BinTableHDU.from_columns(cols, name="WAVETAB")]).writeto(table_only)

    reader = FitsReader(cube)
    hdul = reader.hdul
    try:
        with pytest.raises(NoImageData):
            reader.load(table_only, require_data=True)
        assert issubclass(NoImageData, ValueError)

        assert reader.filepath == cube
        assert reader.hdul is hdul
        assert float(reader.get_data().mean()) == 3.0
        assert reader.get_image_extensions() == [(0, "PRIMARY"), (1, "SCI")]
    finally:
        reader.close()


@pytest.mark.parametrize("primary,science", [((1, 6, 5, 5), (6, 5, 5)), ((6,), (5, 5))],
                         ids=["4-D primary, 3-D SCI", "1-D primary, 2-D SCI"])
def test_a_primary_that_is_not_2d_or_3d_opens_the_science_extension(tmp_path, primary,
                                                                     science):
    """A primary the viewer cannot show is not displayable (B6), so the default pick passes
    over it to the SCI extension. Phase 5 refused such a file whole, though SCI opened before."""
    from pyql3.core.fits_reader import NoImageData

    path = str(tmp_path / "odd_primary.fits")
    fits.HDUList([fits.PrimaryHDU(np.zeros(primary, dtype=np.float32)),
                  fits.ImageHDU(np.full(science, 4.0, dtype=np.float32), name="SCI")]
                 ).writeto(path)

    reader = FitsReader()
    try:
        reader.load(path, require_data=True)
        assert reader.current_ext == 1
        assert reader.get_data().shape == science
        assert reader.get_image_extensions() == [(1, "SCI")]
        with pytest.raises(NoImageData):
            reader.load(path, ext=0, require_data=True)     # asked for by number: refused
        assert reader.current_ext == 1
    finally:
        reader.close()


@pytest.mark.parametrize("shape", [(1, 6, 5, 5), (6,)], ids=["4-D", "1-D"])
def test_require_data_refuses_an_image_that_is_not_2d_or_3d(tmp_path, shape):
    """The viewer shows only 2-D and 3-D arrays. A file whose only image is 4-D or 1-D has
    nothing to show, and must be refused before the reader commits, or it would fail in the
    viewer and split the two again (R12)."""
    from pyql3.core.fits_reader import NoImageData

    cube = str(tmp_path / "good_cube.fits")
    _two_extension_cube(cube)
    other = str(tmp_path / "other_ndim.fits")
    fits.PrimaryHDU(np.ones(shape, dtype=np.float32)).writeto(other)

    reader = FitsReader(cube)
    hdul = reader.hdul
    try:
        with pytest.raises(NoImageData):
            reader.load(other, require_data=True)

        assert reader.filepath == cube
        assert reader.hdul is hdul
        assert float(reader.get_data().mean()) == 3.0
    finally:
        reader.close()


# --------------------------------------------------------- save: permissions


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o664, 0o644, 0o600, 0o666])
def test_save_preserves_the_target_file_permissions(tmp_path, mode):
    """mkstemp creates 0600 and os.replace carries that onto the destination, so
    saving a group-shared file used to silently lock collaborators out."""
    path = str(tmp_path / "shared.fits")
    fits.PrimaryHDU(np.zeros((8, 8), dtype=np.float32)).writeto(path)
    os.chmod(path, mode)

    reader = FitsReader(path)
    try:
        reader.update_header_card("OBJECT", "GC")
        reader.save()
    finally:
        reader.close()

    assert stat.S_IMODE(os.stat(path).st_mode) == mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_save_as_to_a_new_path_uses_the_umask_default(tmp_path):
    """A new file should look like any other file the user creates, not mkstemp's 0600."""
    src = str(tmp_path / "src.fits")
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(src)

    reader = FitsReader(src)
    target = str(tmp_path / "new.fits")
    try:
        reader.save(target)
    finally:
        reader.close()

    current_umask = os.umask(0)
    os.umask(current_umask)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o666 & ~current_umask


def test_holds_current_copy_after_its_own_save_only(tmp_path, rewrite_fits_in_place):
    """R70: the window uses this to skip the poller's announcement of its own save."""
    path = str(tmp_path / "frame.fits")
    other = str(tmp_path / "other.fits")
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(path)
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(other)

    reader = FitsReader(path)
    try:
        assert reader.holds_current_copy(path)
        assert not reader.holds_current_copy(other)

        reader.update_header_card("OBJECT", "GC")
        reader.save()                               # replaced through os.replace
        assert reader.holds_current_copy(path)

        # Written into the open file, not replaced: Windows refuses to replace a file the
        # reader holds open (docs/developer.md, "Windows: an open file cannot be unlinked").
        rewrite_fits_in_place(path, fits.PrimaryHDU(np.ones((4, 4), dtype=np.float32)))
        assert not reader.holds_current_copy(path), "a file rewritten by someone else"
    finally:
        reader.close()


def test_holds_current_copy_under_another_spelling_of_the_path(tmp_path, monkeypatch):
    """The poller names a file by its watch directory, which need not be spelled the way
    the file was opened: a relative path, or a symlinked directory."""
    real = tmp_path / "real"
    real.mkdir()
    path = str(real / "frame.fits")
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(path)

    reader = FitsReader(path)
    try:
        monkeypatch.chdir(tmp_path)
        assert reader.holds_current_copy(os.path.join("real", "frame.fits"))
        if os.name != "nt":                         # symlinks need privileges on Windows
            link = tmp_path / "link"
            link.symlink_to(real, target_is_directory=True)
            assert reader.holds_current_copy(str(link / "frame.fits"))
    finally:
        reader.close()


# ------------------------------------------------ structural keyword guarding


@pytest.mark.parametrize("keyword", [
    "SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS3", "EXTEND", "END", "naxis2",
])
def test_structural_keywords_are_refused(tmp_path, keyword):
    """SIMPLE=F in particular made the primary HDU stop being recognised as an image,
    and save() overwrites in place -- one edit could render a science file unreadable."""
    path = str(tmp_path / "f.fits")
    fits.PrimaryHDU(np.arange(64, dtype=np.float32).reshape(8, 8)).writeto(path)

    reader = FitsReader(path)
    try:
        assert reader.update_header_card(keyword, 7) is False
        reader.save()
    finally:
        reader.close()

    check = FitsReader(path)
    try:
        assert check.get_data() is not None, f"editing {keyword} corrupted the file"
        assert check.get_data().shape == (8, 8)
    finally:
        check.close()


def test_ordinary_keywords_are_still_editable(tmp_path):
    path = str(tmp_path / "f.fits")
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(path)
    reader = FitsReader(path)
    try:
        assert reader.update_header_card("OBJECT", "GC", comment="target") is True
        reader.save()
    finally:
        reader.close()

    check = FitsReader(path)
    try:
        assert check.get_header()["OBJECT"] == "GC"
    finally:
        check.close()


def test_header_editor_only_reports_structural_keywords_the_user_changed(qapp, tmp_path):
    """The editor's table lists every card, including SIMPLE/BITPIX/NAXISn, so an
    untouched save must stay silent rather than nagging about cards nobody edited."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    path = str(tmp_path / "f.fits")
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(path)

    reader = FitsReader(path)
    try:
        dialog = HeaderEditorDialog(reader)

        dialog.apply_table_edits(ext=0)
        assert dialog._skipped_keywords == [], "an untouched header must report nothing"

        for row in range(dialog.table.rowCount()):
            cell = dialog.table.item(row, 0)
            if cell and cell.text() == "SIMPLE":
                dialog.table.item(row, 1).setText("False")
        dialog.apply_table_edits(ext=0)
        assert dialog._skipped_keywords == ["SIMPLE"]
    finally:
        reader.close()


def test_load_from_memory_keeps_one_header(qapp, tmp_path):
    """BUGS.md R23: `load_from_memory` kept the caller's header as `self.header` while the
    HDU held a normalised copy, so the reader held two headers that disagreed — the caller's
    still said BITPIX 16 and BZERO 32768 over float data."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    header = fits.Header([('BITPIX', 16), ('NAXIS', 2), ('NAXIS1', 5), ('NAXIS2', 4),
                          ('BZERO', 32768), ('BSCALE', 1), ('BLANK', -32768),
                          ('OBJECT', 'before')])
    reader = FitsReader()
    try:
        reader.load_from_memory(np.full((4, 5), 20000.0), header)
        assert reader.get_header() is reader.hdul[0].header
        assert reader.get_header()['BITPIX'] == -64 and 'BZERO' not in reader.get_header()
        # BLANK is defined only for integer data (Phase 7 review m5).
        assert 'BLANK' not in reader.get_header()

        dialog = HeaderEditorDialog(reader)
        for row in range(dialog.table.rowCount()):
            if dialog.table.item(row, 0).text() == 'OBJECT':
                dialog.table.item(row, 1).setText('after')
        assert dialog.apply_table_edits(ext=dialog.current_ext)
        assert reader.get_header()['OBJECT'] == 'after'

        path = str(tmp_path / "result.fits")
        reader.save(path)
        assert fits.getheader(path)['OBJECT'] == 'after'
    finally:
        reader.close()


def _header_editor_file(tmp_path):
    """A primary header with duplicate keywords, an undefined value, HISTORY, COMMENT and a
    blank card (R42), and an image extension."""
    header = fits.Header([('OBJECT', 'GC'), ('FILTER', 'Kn5', 'first card'),
                          ('FILTER', 'Hbb', 'second card'), ('UNDEFKEY', None, 'no value'),
                          ('OBSID', '123', 'a string that reads as a number')])
    header.add_history('reduced with DRP v4')
    header.add_comment('a comment')
    header.append(('', 'blank text'), bottom=True)
    path = str(tmp_path / "h.fits")
    fits.HDUList([fits.PrimaryHDU(np.zeros((3, 3), np.float32), header=header),
                  fits.ImageHDU(np.zeros((2, 2)), name='SCI')]).writeto(path)
    return path


def _editor_rows(dialog, keyword):
    return [row for row in range(dialog.table.rowCount())
            if dialog.table.item(row, 0).text() == keyword]


def test_header_editor_round_trip_is_identity(qapp, tmp_path):
    """BUGS.md R42: rows were written back by keyword, so applying an untouched table set both
    FILTER cards to the second one's value and UNDEFKEY to 'None', and an edited HISTORY row
    was dropped."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    reader = FitsReader(_header_editor_file(tmp_path))
    try:
        before = reader.header.tostring()
        dialog = HeaderEditorDialog(reader)
        assert dialog.apply_table_edits(ext=0)
        assert reader.header.tostring() == before
        assert dialog._skipped_keywords == []

        # An edit reaches the card it was made on, and only that card.
        second_filter = _editor_rows(dialog, 'FILTER')[1]
        dialog.table.item(second_filter, 1).setText('Jbb')
        dialog.table.item(_editor_rows(dialog, 'HISTORY')[0], 1).setText('EDITED')
        assert dialog.apply_table_edits(ext=0)
        cards = reader.header.cards
        assert [c.value for c in cards if c.keyword == 'FILTER'] == ['Kn5', 'Jbb']
        assert [c.comment for c in cards if c.keyword == 'FILTER'] == ['first card',
                                                                        'second card']
        assert list(reader.header['HISTORY']) == ['EDITED']
        assert list(reader.header['COMMENT']) == ['a comment']
        assert reader.header.cards['UNDEFKEY'].value is fits.card.UNDEFINED
        assert len(reader.header) == len(fits.Header.fromstring(before))

        # A comment edited alone leaves the value as it was, its type included.
        dialog.table.item(_editor_rows(dialog, 'UNDEFKEY')[0], 2).setText('still none')
        dialog.table.item(_editor_rows(dialog, 'OBSID')[0], 2).setText('observation id')
        assert dialog.apply_table_edits(ext=0)
        assert reader.header.cards['UNDEFKEY'].value is fits.card.UNDEFINED
        assert reader.header.comments['UNDEFKEY'] == 'still none'
        assert reader.header['OBSID'] == '123'
        assert reader.header.comments['OBSID'] == 'observation id'
    finally:
        reader.close()


def test_a_refused_edit_writes_none_of_the_edits(qapp, tmp_path):
    """Phase 7 review m2: an edit astropy refuses (a non-ASCII value) raised after the rows
    before it had been written, so Cancel no longer discarded everything."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    reader = FitsReader(_header_editor_file(tmp_path))
    try:
        before = reader.header.tostring()
        dialog = HeaderEditorDialog(reader)
        dialog.table.item(_editor_rows(dialog, 'OBJECT')[0], 1).setText('GC_field_A')
        dialog.table.item(_editor_rows(dialog, 'FILTER')[1], 1).setText('\u0394')
        with pytest.raises(ValueError, match='FILTER'):
            dialog.apply_table_edits()
        assert reader.header.tostring() == before
    finally:
        reader.close()


def test_cancel_after_an_extension_switch_keeps_the_header(qapp, tmp_path):
    """BUGS.md R42: switching the editor's extension wrote the table into the reader, so
    Cancel kept the edit and the next save of any kind wrote it. Edits are held in the
    dialog, per extension, until Save."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog

    reader = FitsReader(_header_editor_file(tmp_path))
    try:
        primary, sci = reader.hdul[0].header.tostring(), reader.hdul[1].header.tostring()
        dialog = HeaderEditorDialog(reader)
        dialog.table.item(_editor_rows(dialog, 'OBJECT')[0], 1).setText('typo')
        dialog.combo_ext.setCurrentIndex(dialog.combo_ext.findData(1))
        assert reader.hdul[0].header.tostring() == primary
        dialog.reject()
        assert reader.hdul[0].header.tostring() == primary
        assert reader.hdul[1].header.tostring() == sci

        # Held, not lost: switching back shows the edit, and Save applies both extensions'.
        dialog = HeaderEditorDialog(reader)
        dialog.table.item(_editor_rows(dialog, 'OBJECT')[0], 1).setText('GC_field_A')
        dialog.combo_ext.setCurrentIndex(dialog.combo_ext.findData(1))
        dialog.table.item(_editor_rows(dialog, 'EXTNAME')[0], 1).setText('SCI2')
        dialog.combo_ext.setCurrentIndex(dialog.combo_ext.findData(0))
        assert dialog.table.item(_editor_rows(dialog, 'OBJECT')[0], 1).text() == 'GC_field_A'
        assert reader.hdul[0].header['OBJECT'] == 'GC'
        assert dialog.apply_table_edits()
        assert reader.hdul[0].header['OBJECT'] == 'GC_field_A'
        assert reader.hdul[1].header['EXTNAME'] == 'SCI2'
    finally:
        reader.close()


SCALED_STORED = np.array([[0, 1, 2], [-32768, 100, 32767]], dtype=np.int16)


def _write_scaled_int16(path):
    """BITPIX 16 with BSCALE 0.5, BZERO 100 and BLANK, each with a comment, followed by other
    cards, so the scaling cards have a position to keep. Holds 100, 100.5, 101, NaN, 150 and
    16483.5."""
    # Set on the header after the data, so astropy writes the stored integers with these
    # cards as they are rather than rescaling.
    hdu = fits.PrimaryHDU(SCALED_STORED)
    hdu.header['BSCALE'] = (0.5, 'value = 0.5 * stored + 100')
    hdu.header['BZERO'] = (100.0, 'offset')
    hdu.header['BLANK'] = (-32768, 'undefined pixel')
    hdu.header['OBJECT'] = 'GC'
    hdu.header.add_history('written by the test')
    hdu.writeto(path)
    assert fits.getheader(path)['BSCALE'] == 0.5
    return fits.getdata(path)


def _write_unsigned(path):
    """A float32 primary (the image displayed) with a uint32 DQ and a uint16 extension, as a
    JWST cal or s3d file has: BZERO 2**31 and 2**15 make them unsigned."""
    fits.HDUList([
        fits.PrimaryHDU(np.ones((3, 4), np.float32)),
        fits.ImageHDU(np.array([[3_000_000_000, 2151153665, 513, 0]] * 3, np.uint32), name='DQ'),
        fits.ImageHDU(np.full((3, 4), 40000, np.uint16), name='COUNTS'),
    ]).writeto(path)
    with fits.open(path) as hdul:
        return [hdul[1].data.copy(), hdul[2].data.copy()]


def test_header_only_save_keeps_the_scaled_integer_encoding(tmp_path):
    """BUGS.md R71: a header-only save rewrote BITPIX 16 data with BSCALE 0.5, BZERO 100 and
    BLANK as BITPIX -32 and dropped the three keywords. They are written back in their own
    places, with their comments, and the array the reader handed out is not changed."""
    path = str(tmp_path / "scaled.fits")
    values = _write_scaled_int16(path)
    assert np.isnan(values[1, 0]) and values[1, 1] == 150.0
    original = fits.getheader(path)

    reader = FitsReader(path)
    try:
        shown = reader.get_data()
        shown_before = shown.copy()
        reader.update_header_card('OBJECT', 'GC_field_A')
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            reader.save()
        assert np.array_equal(shown, shown_before, equal_nan=True)
    finally:
        reader.close()

    saved = fits.getheader(path)
    assert saved['BITPIX'] == 16
    assert (saved['BSCALE'], saved['BZERO'], saved['BLANK']) == (0.5, 100.0, -32768)
    assert saved['OBJECT'] == 'GC_field_A'
    assert ([c.image for c in saved.cards if c.keyword != 'OBJECT']
            == [c.image for c in original.cards if c.keyword != 'OBJECT'])
    assert list(saved.keys()) == list(original.keys())
    assert np.array_equal(fits.getdata(path, do_not_scale_image_data=True), SCALED_STORED)
    assert np.array_equal(fits.getdata(path), values, equal_nan=True)


@pytest.mark.parametrize("save_as", [False, True], ids=["save in place", "save as"])
def test_a_save_keeps_unsigned_integer_extensions(tmp_path, save_as):
    """Phase 7 review C1: opened with `scale_back=True`, a save wrote uint16 and uint32 HDUs
    without their BZERO, so 40000 read back as 7232 and a JWST DQ flag 2151153665 as 3670017
    -- in an extension that was never displayed."""
    path = str(tmp_path / "cal.fits")
    expected = _write_unsigned(path)
    target = str(tmp_path / "copy.fits") if save_as else path

    reader = FitsReader(path)
    try:
        reader.update_header_card('OBJECT', 'GC')
        reader.save(target)
    finally:
        reader.close()

    with fits.open(target) as hdul:
        for ext, values in zip((1, 2), expected):
            assert hdul[ext].header['BZERO'] == {1: 2**31, 2: 2**15}[ext]
            assert hdul[ext].data.dtype == values.dtype
            assert np.array_equal(hdul[ext].data, values)


def test_a_save_keeps_identity_scaling_cards(tmp_path):
    """Phase 7 review I1: OSIRIS cubes carry `BSCALE = 1` and `BZERO = 0` on float data, and
    a save with nothing edited removed both."""
    path = str(tmp_path / "osiris_like.fits")
    hdu = fits.PrimaryHDU(np.arange(12, dtype=np.float32).reshape(3, 4))
    hdu.header['BSCALE'] = (1, 'scale factor')
    hdu.header['BZERO'] = (0, 'zero point')
    hdu.header['OBJECT'] = 'GC'
    hdu.writeto(path)
    original = fits.getheader(path).tostring()

    reader = FitsReader(path)
    try:
        reader.save()
    finally:
        reader.close()
    assert fits.getheader(path).tostring() == original


@pytest.mark.parametrize("kind", ["scaled int16", "unsigned", "identity scaling"])
def test_an_untouched_save_is_byte_identical(tmp_path, kind):
    path = str(tmp_path / "f.fits")
    if kind == "scaled int16":
        _write_scaled_int16(path)
    elif kind == "unsigned":
        _write_unsigned(path)
    else:
        hdu = fits.PrimaryHDU(np.arange(12, dtype=np.float32).reshape(3, 4))
        hdu.header['BSCALE'], hdu.header['BZERO'] = (1, 'scale factor'), (0, 'zero point')
        hdu.writeto(path)
    with open(path, 'rb') as f:
        before = f.read()

    reader = FitsReader(path)
    try:
        reader.save()
    finally:
        reader.close()
    with open(path, 'rb') as f:
        assert f.read() == before


def _write_integer_hdu(path, hdu, displayed):
    """`hdu`'s integers and cards as the primary (the image a reader displays), or as an
    extension behind a float primary, which is displayed instead."""
    target = fits.PrimaryHDU(hdu.data) if displayed else fits.ImageHDU(hdu.data, name='QUAL')
    # Set after construction: a header given to the constructor loses BSCALE and BZERO.
    for keyword in ('BSCALE', 'BZERO', 'BLANK'):
        if keyword in hdu.header:
            target.header[keyword] = (hdu.header[keyword], hdu.header.comments[keyword])
    hdus = [target] if displayed else [fits.PrimaryHDU(np.ones((3, 4), np.float32)), target]
    fits.HDUList(hdus).writeto(path)
    with fits.open(path) as hdul:
        assert all(k in hdul[0 if displayed else 1].header for k in hdu.header
                   if k in ('BSCALE', 'BZERO', 'BLANK'))


def _blank_only_hdu(dtype):
    """Integers with BLANK and no BSCALE/BZERO; three of the twelve pixels are BLANK."""
    blank = {np.uint8: 255, np.int16: -32768, np.int32: -2147483648}[dtype]
    stored = np.arange(12, dtype=dtype).reshape(3, 4)
    stored[0, 1] = stored[2, 2] = stored[1, 3] = blank
    hdu = fits.ImageHDU(stored)
    hdu.header['BLANK'] = (blank, 'undefined pixel')
    return hdu


def _fine_scale_hdu(kind):
    """Scaled integers whose physical values float32 cannot hold exactly (BUGS.md R71, Phase 7
    re-review N2)."""
    if kind == "int64":
        stored = np.array([[2**53 + 1, 2**62 + 12345], [2**63 - 1, -2**63]], dtype=np.int64)
        bscale, bzero = 0.5, 1.0
    elif kind == "int16 BLANK":
        # In range: rebuilt from float32, about one in six of these moves one step.
        stored = np.random.default_rng(4).integers(-30000, 30000, (50, 50)).astype(np.int16)
        stored[1, 1] = stored[7, 3] = -32768
        hdu = fits.ImageHDU(stored)
        hdu.header['BSCALE'], hdu.header['BZERO'] = (1e-4, 'scale'), (2000.0, 'zero')
        hdu.header['BLANK'] = (-32768, 'undefined pixel')
        return hdu
    else:
        stored = np.random.default_rng(3).integers(-32768, 32768, (50, 50)).astype(np.int16)
        stored[0, 0], stored[0, 1] = -32768, 32767   # round outside int16 from float32
        bscale, bzero = 1e-4, 2000.0
    hdu = fits.ImageHDU(stored)
    hdu.header['BSCALE'], hdu.header['BZERO'] = (bscale, 'scale'), (bzero, 'zero')
    return hdu


def _stored(path, ext):
    with fits.open(path, do_not_scale_image_data=True) as hdul:
        return hdul[ext].data.copy(), hdul[ext].header.tostring()


@pytest.mark.parametrize("target", ["in place", "save as", "save as gz"])
@pytest.mark.parametrize("displayed", [True, False], ids=["displayed", "extension"])
@pytest.mark.parametrize("hdu_for", [
    pytest.param(lambda: _blank_only_hdu(np.uint8), id="uint8 BLANK"),
    pytest.param(lambda: _blank_only_hdu(np.int16), id="int16 BLANK"),
    pytest.param(lambda: _blank_only_hdu(np.int32), id="int32 BLANK"),
    pytest.param(lambda: _fine_scale_hdu("int16"), id="int16 BSCALE 1e-4"),
    pytest.param(lambda: _fine_scale_hdu("int64"), id="scaled int64"),
])
def test_a_save_writes_the_stored_integers_back(tmp_path, hdu_for, displayed, target):
    """Phase 7 re-review N1: astropy reads integers with BLANK and no scaling as float32 and
    wrote the float32 bytes under the integer BITPIX, so every value was lost. N2: integers
    rebuilt from astropy's float32 moved one step when BSCALE was finer than float32 holds,
    fell back to BITPIX -32 outside the int16 range, and lost bits in int64."""
    import gzip
    path = str(tmp_path / "frame.fits")
    _write_integer_hdu(path, hdu_for(), displayed)
    ext = 0 if displayed else 1
    stored, header = _stored(path, ext)
    with open(path, 'rb') as f:
        before = f.read()
    out = {"in place": path, "save as": str(tmp_path / "copy.fits"),
           "save as gz": str(tmp_path / "copy.fits.gz")}[target]

    reader = FitsReader(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            reader.save(out)
    finally:
        reader.close()

    opener = gzip.open if out.endswith('.gz') else open
    with opener(out, 'rb') as f:
        assert f.read() == before
    after, after_header = _stored(out, ext)
    assert after.dtype == stored.dtype and np.array_equal(after, stored)
    assert after_header == header


def test_an_edit_to_one_hdu_keeps_another_hdus_stored_integers(tmp_path):
    """Save Header after an edit: the BLANK-only extension, never displayed, keeps its
    integers (Phase 7 re-review N1)."""
    path = str(tmp_path / "frame.fits")
    _write_integer_hdu(path, _blank_only_hdu(np.int16), displayed=False)
    stored, header = _stored(path, 1)
    reader = FitsReader(path)
    try:
        reader.update_header_card('OBJECT', 'GC')
        reader.save()
    finally:
        reader.close()
    after, after_header = _stored(path, 1)
    assert np.array_equal(after, stored) and after_header == header
    assert fits.getheader(path)['OBJECT'] == 'GC'


def _touch(path):
    """Change the file's modification time: to the reader, a file changed on disk."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))


@pytest.mark.parametrize("kind", ["int16 BLANK", "int16"], ids=["in range", "outside int16"])
@pytest.mark.parametrize("in_place", [True, False], ids=["save", "save as"])
def test_a_save_after_the_file_changed_on_disk_is_refused(tmp_path, kind, in_place):
    """When the file on disk is no longer the one read, its stored integers cannot be read
    back, and rebuilding them from astropy's float32 either moved values one step in silence
    (Phase 7 re-review 2, m-2) or, outside int16, fell back to BITPIX -32 (N2). The save is
    refused with the cause and the remedy (m-3), and nothing is written."""
    path = str(tmp_path / "frame.fits")
    _write_integer_hdu(path, _fine_scale_hdu(kind), displayed=True)
    reader = FitsReader(path)
    try:
        shown = reader.get_data()
        _touch(path)
        with open(path, 'rb') as f:
            before = f.read()
        out = path if in_place else str(tmp_path / "copy.fits")
        with pytest.raises(ValueError) as refused:
            reader.save(out)
        message = str(refused.value)
        assert "frame.fits changed on disk since it was opened" in message
        assert "Nothing was saved" in message and "Reopen" in message
        assert in_place or not os.path.exists(out)
        with open(path, 'rb') as f:
            assert f.read() == before
        assert reader.get_data() is shown and reader.hdul is not None
    finally:
        reader.close()


@pytest.mark.parametrize("compression", [("GZIP_2", (16, 16)), ("RICE_1", None)],
                         ids=["GZIP_2 16x16", "RICE_1"])
@pytest.mark.parametrize("save_as", [False, True], ids=["save header", "save as"])
def test_a_compressed_hdu_keeps_its_integers_and_compression(tmp_path, compression, save_as):
    """Phase 7 re-review 2, I-1: a tile-compressed HDU's integers were rebuilt from float32, so
    with BSCALE 1e-4 about one in five moved one step, and stored -32768/32767 made every save
    fail. m-1: it was also rewritten as RICE_1 with one-row tiles."""
    compression_type, tiles = compression
    stored = np.random.default_rng(5).integers(-32768, 32768, (64, 80)).astype(np.int16)
    stored[0, 0], stored[0, 1] = -32768, 32767
    hdu = fits.CompImageHDU(stored, compression_type=compression_type, tile_shape=tiles)
    hdu.header['BSCALE'], hdu.header['BZERO'] = (1e-4, 'scale'), (2000.0, 'zero')
    path = str(tmp_path / "frame.fits")
    fits.HDUList([fits.PrimaryHDU(np.ones((3, 4), np.float32)), hdu]).writeto(path)
    tile_cards = ('ZCMPTYPE', 'ZTILE1', 'ZTILE2', 'ZQUANTIZ', 'ZDITHER0')

    def compression_cards(p):
        with fits.open(p, disable_image_compression=True) as hdul:
            return {k: hdul[1].header.get(k) for k in tile_cards}

    before = compression_cards(path)
    assert before['ZCMPTYPE'] == compression_type
    out = str(tmp_path / "copy.fits") if save_as else path
    reader = FitsReader(path)
    try:
        reader.update_header_card('OBJECT', 'GC')
        reader.save(out)
    finally:
        reader.close()

    with fits.open(out, do_not_scale_image_data=True) as hdul:
        assert isinstance(hdul[1], fits.CompImageHDU)
        assert np.array_equal(hdul[1].data, stored)
        assert (hdul[1].header['BSCALE'], hdul[1].header['BZERO']) == (1e-4, 2000.0)
    assert compression_cards(out) == before


def test_a_refused_save_header_leaves_the_header_and_keeps_the_edits(qapp, tmp_path,
                                                                     monkeypatch):
    """Phase 7 re-review 2, m-4: a refused Save Header left the edits applied to the reader's
    header, so Cancel no longer discarded them and a later save would write them. The header is
    restored and the edits stay held, unsaved, in the editor."""
    from PySide6.QtWidgets import QMessageBox
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
    errors = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: errors.append(a[2])))
    for name in ("information", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(
            lambda *a, **k: pytest.fail(f"message box: {a[2]}")))

    path = str(tmp_path / "frame.fits")
    _write_integer_hdu(path, _fine_scale_hdu("int16 BLANK"), displayed=True)
    fits.setval(path, 'OBJECT', value='GC', comment='target')
    reader = FitsReader(path)
    try:
        header_before = reader.header.tostring()
        dialog = HeaderEditorDialog(reader)
        _touch(path)
        row = _editor_rows(dialog, 'OBJECT')[0]
        dialog.table.item(row, 1).setText('GC_field_A')
        dialog.save_header()
        assert len(errors) == 1 and "changed on disk" in errors[0]
        assert reader.header.tostring() == header_before
        assert dialog.table.item(row, 1).text() == 'GC_field_A'
        assert dialog._held_edits == {0: {row: ('GC_field_A', 'target')}}
        dialog.reject()
        assert reader.header.tostring() == header_before
    finally:
        reader.close()


def test_editing_the_scaling_of_a_scaled_extension_is_refused(qapp, tmp_path):
    """Phase 7 re-review m-N3: the editor shows BSCALE, BZERO and BLANK of a scaled extension
    that is not displayed, and an edit to them was dropped without a message at save. Such an
    edit is refused and reported, as a structural keyword is."""
    from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
    path = str(tmp_path / "frame.fits")
    _write_integer_hdu(path, _fine_scale_hdu("int16"), displayed=False)
    reader = FitsReader(path)
    try:
        assert reader.update_header_card('BZERO', 5.0, ext=1) is False
        dialog = HeaderEditorDialog(reader)
        dialog.combo_ext.setCurrentIndex(dialog.combo_ext.findData(1))
        dialog.table.item(_editor_rows(dialog, 'BZERO')[0], 2).setText('edited comment')
        dialog.table.item(_editor_rows(dialog, 'BSCALE')[0], 1).setText('2.0')
        assert dialog.apply_table_edits()
        assert sorted(dialog._skipped_keywords) == ['BSCALE', 'BZERO']
        assert reader.hdul[1].header['BSCALE'] == 1e-4
        assert reader.hdul[1].header.comments['BZERO'] == 'zero'
    finally:
        reader.close()


def test_reference_header_for_an_error_extension(s3d_like_fits, osiris_like_fits_with_noise,
                                                 tmp_path):
    """R25: an extension without a WCS of its own is given the header of the extension of the
    same shape that has one; an extension that lacks only an integration time is given the
    primary header; one that has both is given none."""
    reader = FitsReader(s3d_like_fits)
    try:
        reader.load(s3d_like_fits, ext=2)                      # ERR: no WCS cards
        assert reader.reference_header()['EXTNAME'] == 'SCI'
        reader.load(s3d_like_fits, ext=3)                      # DQ: no WCS cards
        assert reader.reference_header()['EXTNAME'] == 'SCI'
        reader.load(s3d_like_fits, ext=1)                      # SCI: a WCS but no ITIME
        assert reader.reference_header() is reader.hdul[0].header
    finally:
        reader.close()

    reader = FitsReader(osiris_like_fits_with_noise)
    try:
        assert reader.current_ext == 0
        assert reader.reference_header() is None, "the primary has a WCS and an ITIME"
        reader.load(osiris_like_fits_with_noise, ext=1)        # noise: an empty header
        assert reader.reference_header() is reader.hdul[0].header
    finally:
        reader.close()

    # An extension of another shape is not a reference, however good its WCS
    path = str(tmp_path / "other_shape.fits")
    with fits.open(s3d_like_fits) as hdul:
        data, header = hdul[1].data.copy(), hdul[1].header.copy()
    fits.HDUList([fits.PrimaryHDU(),
                  fits.ImageHDU(data[:, :, :5], header, name='SCI'),
                  fits.ImageHDU(data, name='ERR')]).writeto(path)
    reader = FitsReader(path)
    try:
        reader.load(path, ext=2)
        assert reader.reference_header() is reader.hdul[0].header
    finally:
        reader.close()
