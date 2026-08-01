import os
import stat
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
