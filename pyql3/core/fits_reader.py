import os
import stat
import tempfile
from collections import namedtuple

import numpy as np
from astropy.io import fits

from pyql3.core.header_info import build_wcs, has_world_axes, states_integration_time


def _default_file_mode():
    """The mode a normally-created file would get, i.e. 0666 masked by the umask.

    Used when saving to a path that does not exist yet. Without it a new file would
    inherit mkstemp's 0600 and be unreadable to the group, which is surprising for a
    "Save As" into a shared reduction directory.

    Reading the umask requires setting it, so this is momentarily racy. That is
    acceptable here: saves are user-initiated from the GUI thread, and the only other
    thread in the application (the directory poller) reads but never creates files.
    """
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


class NoImageData(ValueError):
    """The chosen extension holds no 2-D or 3-D image, the only kinds the viewer shows.
    Raised by `FitsReader.load(require_data=True)`."""


def _header_shape(header):
    """The numpy shape of an HDU's data, read from its `NAXISn` cards without reading the data."""
    try:
        naxis = int(header.get('NAXIS', 0))
        return tuple(int(header.get(f'NAXIS{n}', 0)) for n in range(naxis, 0, -1))
    except (TypeError, ValueError):
        return None


def _has_world_axes(header):
    wcs, _ = build_wcs(header)
    return has_world_axes(wcs, header)


#: The integer type each integer BITPIX is stored as.
_STORED_DTYPES = {8: np.uint8, 16: np.int16, 32: np.int32, 64: np.int64}

#: How an HDU's integers were scaled in the file: the values of BSCALE, BZERO and BLANK
#: (None when absent), the three cards as `(position, Card)` pairs, and how many blank cards
#: ended the header. Recorded at open, before astropy removes the cards on reading the data.
_Scaling = namedtuple('_Scaling', 'bitpix bscale bzero blank cards trailing_blanks')


def _trailing_blank_cards(header):
    count = 0
    for card in reversed(header.cards):
        if card.keyword or card.value or card.comment:
            break
        count += 1
    return count


def _integer_scaling_of(hdul):
    """`{HDU index: _Scaling}` for every integer image HDU that astropy reads as floats.

    That is one whose integers the file scales (BSCALE/BZERO other than 1/0), or that has a
    BLANK. Reads headers only. Must run before any HDU's `.data` is read: astropy then converts
    the integers to floats. For a scaled HDU it deletes BSCALE, BZERO and BLANK from
    `hdu.header`, and a save would write the floats as BITPIX -32 without them (R71). For an
    HDU with BLANK and identity scaling it keeps the integer BITPIX, and a save would write the
    float32 bytes under it, losing every value. Identity scaling without BLANK is left out:
    astropy keeps those integers and cards as they are.
    """
    scaling = {}
    for idx, hdu in enumerate(hdul):
        if not isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU)):
            continue
        header = hdu.header
        try:
            bitpix = int(header.get('BITPIX', 0))
            bscale = float(header.get('BSCALE', 1))
            bzero = float(header.get('BZERO', 0))
            blank = int(header['BLANK']) if 'BLANK' in header else None
        except (TypeError, ValueError):
            continue
        if bitpix not in _STORED_DTYPES or ((bscale, bzero) == (1.0, 0.0) and blank is None):
            continue
        cards = [(header.index(keyword), fits.Card.fromstring(header.cards[keyword].image))
                 for keyword in ('BSCALE', 'BZERO', 'BLANK') if keyword in header]
        scaling[idx] = _Scaling(bitpix, bscale, bzero, blank, sorted(cards, key=lambda c: c[0]),
                                _trailing_blank_cards(header))
    return scaling


def _reads_as_floats(hdu, scaling):
    """True if astropy read this recorded integer HDU as floats, so `save()` has to write its
    stored integers back itself."""
    return (scaling is not None and hdu.data is not None and hdu.data.dtype.kind == 'f')


#: The parameters a tile-compressed HDU is rebuilt with, so a save keeps the file's
#: compression type, tiling, quantisation and dithering (ZCMPTYPE, ZTILEn, ZQUANTIZ, ...).
_COMPRESSION_SETTINGS = ('compression_type', 'tile_shape', 'quantize_level', 'quantize_method',
                         'dither_seed', 'hcomp_scale', 'hcomp_smooth')


def _encoded_for_writing(hdu, scaling, stored):
    """The HDU to write in place of `hdu`: `hdu` itself, or, when astropy read the integers as
    floats (`_reads_as_floats`), a new HDU holding `stored`, the integers read from the file,
    with the file's own scaling cards and, for a tile-compressed HDU, its compression settings.

    The array the viewer shows as `raw_data` is never changed. Unsigned integers (BZERO 2**15,
    2**31, 2**63) are read by astropy as `uint` arrays and written back correctly, so they come
    back unchanged here.
    """
    if not _reads_as_floats(hdu, scaling):
        return hdu

    header = hdu.header.copy()
    for keyword in ('BSCALE', 'BZERO', 'BLANK'):
        header.remove(keyword, ignore_missing=True, remove_all=True)
    # astropy put a blank card at the end for each of BSCALE and BZERO it deleted.
    added = sum(1 for _, card in scaling.cards if card.keyword in ('BSCALE', 'BZERO'))
    while added and _trailing_blank_cards(header) > scaling.trailing_blanks:
        del header[-1]
        added -= 1
    header['BITPIX'] = scaling.bitpix
    for position, card in scaling.cards:
        header.insert(min(position, len(header)), fits.Card.fromstring(card.image),
                      useblanks=False)

    if isinstance(hdu, fits.CompImageHDU):
        settings = {name: getattr(hdu, name) for name in _COMPRESSION_SETTINGS}
        if settings['tile_shape'] is not None:
            settings['tile_shape'] = tuple(int(n) for n in settings['tile_shape'])
        encoded = fits.CompImageHDU(data=stored, **settings)
    else:
        encoded = type(hdu)(data=stored)
    # The header is given its cards after construction: passed to the constructor, BSCALE
    # and BZERO would be dropped, and a primary header would lose EXTEND. Cards in a header
    # do not rescale the integers on write.
    encoded.header.clear()
    encoded.header.extend(header, strip=False, useblanks=False)
    return encoded


def _canonical(path):
    """One spelling per file: absolute, symlinks resolved, case folded where the OS does."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


class FitsReader:
    """Wrapper class for handling FITS file reading and header management."""
    def __init__(self, filepath=None):
        self.filepath = filepath
        self.hdul = None
        self.data = None
        self.header = None
        self.image_extensions = []
        self._file_signature = None
        self._scaling = {}

        if filepath:
            self.load(filepath)

    @staticmethod
    def _stat_signature(filepath):
        """Identity of the bytes on disk. Any change here means we must reopen."""
        try:
            st = os.stat(filepath)
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def load(self, filepath, ext=None, force=False, require_data=False):
        """Loads a FITS file and its primary data/header.

        The HDUList is reopened whenever the file on disk differs from the one we hold
        open — *including* when the path is unchanged. An instrument or DRP that rewrites
        a path in place was previously served the cached copy forever (B5). Reuse is kept
        for the byte-identical case so that switching extensions stays cheap and does not
        throw away header edits that have not been saved yet. `force=True` reopens
        regardless, for an explicit "reload from disk".

        All or nothing: the new file is opened and the chosen extension's data read before
        anything here is replaced, so if any of that raises, the reader still holds the
        file it held before, open (R12). A frame still being copied fails in the data
        read, and the window that displays the old file goes on acting on it.

        `require_data=True` raises `NoImageData`, also before anything is replaced, when
        the chosen extension holds no 2-D or 3-D image.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"FITS file not found: {filepath}")

        signature = self._stat_signature(filepath)
        stale = (self.hdul is None
                 or self.filepath != filepath
                 or signature is None
                 or signature != self._file_signature)
        if force or stale:
            # memmap=False: reading through a mapping of a file the DRP is rewriting under
            # us yields undefined data (and can SIGBUS if it shrinks), and writeto() back
            # to the same path can fail while the mapping is open.
            hdul = fits.open(filepath, memmap=False)
        else:
            hdul = self.hdul

        try:
            # Before any data is read; see `_integer_scaling_of`.
            scaling = self._scaling if hdul is self.hdul else _integer_scaling_of(hdul)
            image_extensions = self._image_extensions_of(hdul)
            if ext is not None:
                data = hdul[ext].data
            elif image_extensions:
                ext = image_extensions[0][0]
                data = hdul[ext].data
            else:
                ext, data = 0, None
            header = hdul[ext].header if data is not None else hdul[0].header
            # The viewer shows only 2-D and 3-D arrays. An extension asked for by number
            # need not be displayable: a 1-D or 4-D image, or a table, whose FITS_rec is 1-D.
            if require_data and (data is None or data.ndim not in (2, 3)):
                raise NoImageData(f"No displayable image in {os.path.basename(filepath)}")
        except BaseException:
            if hdul is not self.hdul:
                hdul.close()
            raise

        if hdul is not self.hdul:
            self.close()
            self.hdul = hdul
            self._file_signature = signature
            self._scaling = scaling
        self.filepath = filepath
        self.image_extensions = image_extensions
        self.current_ext = ext
        self.data = data
        # With no displayable image, `data` deliberately stays None so callers can tell
        # "nothing to show" from "here is your image", and the primary header is published
        # so the file can be inspected.
        #
        # This used to substitute np.zeros((10, 10)) with an empty Header. That made
        # `data` never None, which silently disabled every `if data is None` guard in the
        # application: a FITS carrying no image HDU was displayed as a black 10x10
        # square, titled with the filename and added to Recent Files, indistinguishable
        # from a real observation of an empty field.
        self.header = header

    def reference_header(self):
        """The header to take the WCS and the integration time from when the current
        extension's own header lacks them (R25), or None when it has both.

        A JWST ERR or DQ extension has no WCS cards, and an OSIRIS noise or quality extension
        has an empty header. When the WCS is missing, the reference is the first other image
        extension with the same data shape whose header has a celestial or spectral axis (SCI
        in a JWST file, the primary in an OSIRIS file), else the primary header. When only the
        integration time is missing, it is the primary header, where OSIRIS and NIFS keep it.
        None when the reference would be the current extension itself.

        Only headers are read, so no other extension's data is pulled into memory. The viewer
        decides what it uses from the reference: it does not apply a WCS with fewer axes than
        the data (a NIFS primary's WCS is the 2-D detector frame's), and takes `BUNIT` and the
        integration time from it only when it is the primary header and the data are not
        integers (`header_info.data_keywords`). So an extension lacking both, whose reference
        is the sibling with the WCS (JWST DQ beside SCI), borrows the WCS and no unit.
        """
        header = self.header
        if self.hdul is None or header is None or self.data is None:
            return None
        has_wcs = _has_world_axes(header)
        if has_wcs and states_integration_time(header):
            return None
        if not has_wcs:
            shape = _header_shape(header)
            for idx, _name in self.image_extensions:
                candidate = self.hdul[idx].header
                if (idx != self.current_ext and _header_shape(candidate) == shape
                        and _has_world_axes(candidate)):
                    return candidate
        if self.current_ext == 0:
            return None
        return self.hdul[0].header

    def holds_current_copy(self, path):
        """True if `path` is the file this reader holds and it is unchanged on disk.

        After `save()` the reader has reopened the file it wrote, so this is true of the
        reader's own save until something else changes the file. A watched directory
        announces that save as a changed file (R70), naming it through the watch
        directory, which need not be spelled the way the file was opened -- hence the
        canonical comparison, as the poller's `_watch_key` makes.
        """
        if self.filepath is None or self._file_signature is None:
            return False
        if _canonical(path) != _canonical(self.filepath):
            return False
        return self._stat_signature(path) == self._file_signature

    def load_from_memory(self, data, header):
        """Hold `data` as a one-HDU file with no path, such as an Arithmetic result.

        The header kept is the HDU's own, not the caller's: `PrimaryHDU` normalises a copy
        (BITPIX from the data's type, BZERO and BSCALE dropped for float data), and keeping
        the caller's object as well left two headers that disagreed, of which the Header
        Editor edited one and Save wrote the other (R23).
        """
        if self.hdul:
            self.hdul.close()

        if header is not None and data is not None and np.asarray(data).dtype.kind == 'f':
            # FITS defines BLANK for integer data only, and an integer operand's header has
            # one. Removed from a copy before the HDU is built, which otherwise warns.
            header = header.copy()
            header.remove('BLANK', ignore_missing=True, remove_all=True)
        hdu = fits.PrimaryHDU(data=data, header=header)
        self.hdul = fits.HDUList([hdu])
        self.filepath = None
        self._file_signature = None
        self._scaling = {}
        self.current_ext = 0
        self.data = data
        self.header = hdu.header
        self.image_extensions = self.get_image_extensions()


    def get_all_extensions(self):
        extensions = []
        if self.hdul:
            for idx, hdu in enumerate(self.hdul):
                name = hdu.name if hdu.name else f"EXT {idx}"
                extensions.append((idx, name))
        elif self.header is not None:
            extensions.append((0, "PRIMARY"))
        return extensions

    @staticmethod
    def _is_displayable(hdu):
        """Whether an HDU holds an image the viewer can actually show.

        An image HDU with two or three axes: the viewer shows nothing else, so a 1-D or 4-D
        image is neither offered in the Extension combo nor picked by default, and a file whose
        primary is 4-D opens its 3-D SCI extension.

        Header-only on purpose: touching `hdu.data` here would pull every extension of a
        multi-extension file into memory now that `memmap` is off, and `data is not None`
        is also the wrong question — a `BINTABLE` answers yes (B6).
        """
        if not getattr(hdu, 'is_image', False):
            return False
        try:
            return int(hdu.header.get('NAXIS', 0)) in (2, 3)
        except (TypeError, ValueError):
            return False

    def get_image_extensions(self):
        """The extensions the viewer can display.

        The single definition of "displayable", shared with `load()` — the two used to
        disagree, so the Extension combo offered table HDUs that silently left the previous
        image on screen while `data`/`header` switched to the table underneath (B6).
        """
        return self._image_extensions_of(self.hdul)

    @classmethod
    def _image_extensions_of(cls, hdul):
        """`get_image_extensions()` for a given HDUList, which `load()` needs before it
        has replaced `self.hdul`."""
        extensions = []
        if hdul:
            for idx, hdu in enumerate(hdul):
                if not cls._is_displayable(hdu):
                    continue
                name = hdu.name if hdu.name else f"EXT {idx}"
                if name == "PRIMARY" and idx != 0:
                    name = f"EXT {idx}"
                extensions.append((idx, name))
        return extensions

    def get_data(self):
        return self.data
        
    def get_header(self, ext=None):
        if ext is not None and self.hdul and 0 <= ext < len(self.hdul):
            return self.hdul[ext].header
        return self.header
        
    #: Keywords that describe the physical layout of the file rather than the
    #: observation. Editing them is either destructive or futile:
    #:
    #:   SIMPLE = F marks the file as non-conforming, and the primary HDU then stops
    #:   being recognised as an image — save() overwrites in place, so one keystroke
    #:   in the Header Editor could make a science file unreadable.
    #:
    #:   NAXIS/NAXISn/BITPIX and friends are regenerated by astropy from the data
    #:   array on write, so an edit silently reverts, which merely confuses.
    #:
    #: Either way the viewer should not be the tool that does it.
    PROTECTED_KEYWORDS = frozenset({
        'SIMPLE', 'XTENSION', 'BITPIX', 'NAXIS', 'END', 'EXTEND', 'PCOUNT', 'GCOUNT',
        'TFIELDS',
    })

    #: Cards that hold free text and no comment field.
    COMMENTARY_KEYWORDS = frozenset({'', 'COMMENT', 'HISTORY'})

    @classmethod
    def is_protected_keyword(cls, keyword):
        """True for structural keywords, including the NAXISn / TFORMn families."""
        name = str(keyword).strip().upper()
        if name in cls.PROTECTED_KEYWORDS:
            return True
        for prefix in ('NAXIS', 'TFORM', 'TBCOL'):
            if name.startswith(prefix) and name[len(prefix):].isdigit():
                return True
        return False

    def _keeps_keyword(self, keyword, ext):
        """True if `keyword` of extension `ext` (None: the current one) may not be edited:
        a structural keyword, or BSCALE, BZERO or BLANK of an HDU whose integers `save()`
        writes back with the file's own scaling cards (`_integer_scaling_of`). An edit to those
        would be replaced by the recorded cards, so it is refused rather than lost."""
        if self.is_protected_keyword(keyword):
            return True
        index = self.current_ext if ext is None else ext
        return (str(keyword).strip().upper() in ('BSCALE', 'BZERO', 'BLANK')
                and index in self._scaling)

    def update_header_card(self, keyword, value, comment=None, ext=None):
        """Update or add a header card. Returns False if the keyword may not be edited
        (`_keeps_keyword`)."""
        if self._keeps_keyword(keyword, ext):
            return False

        hdr = self.get_header(ext=ext)
        if hdr is None:
            return False
        if comment is not None:
            hdr[keyword] = (value, comment)
        else:
            hdr[keyword] = value
        return True

    def update_header_card_at(self, index, value, comment=None, ext=None):
        """Set the value, and the comment if given, of the card at position `index`.

        By position, because a keyword names only its first card: writing a second FILTER
        card by name overwrote the first (R42). A COMMENT, HISTORY or blank card has no
        comment field, so only its text is set. Returns False, writing nothing, for a
        keyword that may not be edited (`_keeps_keyword`).
        """
        hdr = self.get_header(ext=ext)
        if hdr is None:
            return False
        card = hdr.cards[index]
        if self._keeps_keyword(card.keyword, ext):
            return False
        if card.keyword in self.COMMENTARY_KEYWORDS:
            hdr[index] = value
        elif comment is not None:
            hdr[index] = (value, comment)
        else:
            hdr[index] = value
        return True

    def _stored_integers(self):
        """`{HDU index: integers}` for the HDUs in `_scaling`, read from the file as stored.

        Exactly what the file holds. Rebuilding the integers from astropy's floats is not
        exact: for BITPIX 8 and 16 those are float32, which cannot hold every
        `stored * BSCALE + BZERO` (with BSCALE 1e-4 and BZERO 2000, one pixel in six moved one
        step), and scaled BITPIX 64 loses bits even in float64. Valid because the viewer
        never changes pixel data, only headers. Tile-compressed HDUs are read exactly too.
        Empty when the file on disk is no longer the one that was read.
        """
        if (not self._scaling or self.filepath is None
                or self._stat_signature(self.filepath) != self._file_signature):
            return {}
        stored = {}
        with fits.open(self.filepath, memmap=False, do_not_scale_image_data=True) as raw:
            for idx in self._scaling:
                if raw[idx].data is not None:
                    stored[idx] = np.array(raw[idx].data)
        return stored

    def save(self, output_filepath=None):
        """Write the in-memory HDUList out, overwriting the target if it exists.

        Saving over the file we currently have open needs care on Windows: a file with an
        open handle cannot be deleted or replaced, and astropy implements `overwrite=True`
        as `os.remove()` followed by a fresh create. That made the Header Editor's
        "save directly to file" fail with `PermissionError: [WinError 32]`.

        So: pull every HDU into memory, release the OS handle, write to a sibling temp file
        and swap it in with `os.replace()` (atomic on both platforms, and it never leaves a
        half-written file where the original was). Then reopen so our state matches disk.
        """
        if self.hdul is None:
            raise ValueError("No FITS file loaded.")

        save_path = output_filepath or self.filepath
        if not save_path:
            raise ValueError("No output path given and no current file path is known.")

        hdul = self.hdul

        # Materialise before dropping the handle: with memmap off these become real arrays
        # that stay valid after close(), which lazily-loaded HDUs would not.
        for hdu in hdul:
            _ = hdu.data

        # Integers that astropy read as floats go back as the file stores them (R71). Checked
        # and built before anything is closed or written, so a refusal leaves the reader and
        # the file as they were.
        stored = self._stored_integers()
        for idx, hdu in enumerate(hdul):
            if (_reads_as_floats(hdu, self._scaling.get(idx))
                    and (idx not in stored or stored[idx].shape != hdu.data.shape)):
                # The integers can only be read from the file that was opened. Rebuilding
                # them from astropy's floats moves values (see `_stored_integers`), and an
                # in-place save would also overwrite the newer file with this older content.
                raise ValueError(
                    f"{os.path.basename(self.filepath or '')} changed on disk since it was "
                    "opened, so its integer data cannot be written back exactly as the file "
                    "stores them. Nothing was saved. Reopen the file and make the change "
                    "again.")
        to_write = fits.HDUList([
            _encoded_for_writing(hdu, self._scaling.get(idx), stored.get(idx))
            for idx, hdu in enumerate(hdul)])

        try:
            hdul.close()
        except Exception:
            pass

        # Capture the target's permissions before it is replaced. mkstemp creates 0600
        # by design, and os.replace carries the temp file's mode onto the destination,
        # so saving a group-shared file used to silently drop it to owner-only and lock
        # collaborators out of a reduction directory. Nothing warned; they simply got
        # permission denied later.
        original_mode = None
        try:
            original_mode = stat.S_IMODE(os.stat(save_path).st_mode)
        except OSError:
            pass  # new file (Save As); fall back to the umask default below

        # Same directory as the target, so os.replace can never be cross-filesystem.
        directory = os.path.dirname(os.path.abspath(save_path)) or '.'
        # astropy picks gzip from the file name, so a `.gz` target needs a `.gz` temp name or
        # the swapped-in file is plain FITS under a compressed name.
        tmp_suffix = '.fits.gz' if save_path.lower().endswith('.gz') else '.fits'
        fd, tmp_path = tempfile.mkstemp(prefix='.pyql3_save_', suffix=tmp_suffix, dir=directory)
        os.close(fd)
        try:
            to_write.writeto(tmp_path, overwrite=True)
            os.chmod(tmp_path, original_mode if original_mode is not None
                     else _default_file_mode())
            os.replace(tmp_path, save_path)
        except BaseException:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

        # Our handle is gone and the bytes on disk have changed, so reopen from scratch.
        reopen_path = save_path if self.filepath in (None, save_path) else self.filepath
        ext = self.current_ext
        self.hdul = None
        self._file_signature = None
        if os.path.exists(reopen_path):
            self.load(reopen_path, ext=ext, force=True)


    def close(self):
        """Closes the FITS file handle."""
        if self.hdul is not None:
            self.hdul.close()
            self.hdul = None
            self.data = None
            self.header = None
            self._scaling = {}
