import os
import tempfile
from astropy.io import fits
import numpy as np

class FitsReader:
    """Wrapper class for handling FITS file reading and header management."""
    def __init__(self, filepath=None):
        self.filepath = filepath
        self.hdul = None
        self.data = None
        self.header = None
        self.image_extensions = []
        self._file_signature = None

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

    def load(self, filepath, ext=None, force=False):
        """Loads a FITS file and its primary data/header.

        The HDUList is reopened whenever the file on disk differs from the one we hold
        open — *including* when the path is unchanged. An instrument or DRP that rewrites
        a path in place was previously served the cached copy forever (B5). Reuse is kept
        for the byte-identical case so that switching extensions stays cheap and does not
        throw away header edits that have not been saved yet. `force=True` reopens
        regardless, for an explicit "reload from disk".
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"FITS file not found: {filepath}")

        signature = self._stat_signature(filepath)
        stale = (self.hdul is None
                 or self.filepath != filepath
                 or signature is None
                 or signature != self._file_signature)
        if force or stale:
            self.close()
            # memmap=False: reading through a mapping of a file the DRP is rewriting under
            # us yields undefined data (and can SIGBUS if it shrinks), and writeto() back
            # to the same path can fail while the mapping is open.
            self.hdul = fits.open(filepath, memmap=False)
            self._file_signature = signature

        self.filepath = filepath
        self.image_extensions = self.get_image_extensions()

        self.data = None
        self.header = None
        self.current_ext = 0
        
        if ext is not None:
            self.data = self.hdul[ext].data
            self.header = self.hdul[ext].header
            self.current_ext = ext
        else:
            for i, name in self.image_extensions:
                self.data = self.hdul[i].data
                self.header = self.hdul[i].header
                self.current_ext = i
                break
                
        # Fallback if no valid image data found
        if self.data is None:
            self.data = np.zeros((10, 10))
            self.header = fits.Header()
            
    def load_from_memory(self, data, header):
        if self.hdul:
            self.hdul.close()

        from astropy.io import fits
        hdu = fits.PrimaryHDU(data=data, header=header)
        self.hdul = fits.HDUList([hdu])
        self.filepath = None
        self._file_signature = None
        self.current_ext = 0
        self.data = data
        self.header = header
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

        Header-only on purpose: touching `hdu.data` here would pull every extension of a
        multi-extension file into memory now that `memmap` is off, and `data is not None`
        is also the wrong question — a `BINTABLE` answers yes (B6).
        """
        if not getattr(hdu, 'is_image', False):
            return False
        try:
            return int(hdu.header.get('NAXIS', 0)) > 0
        except (TypeError, ValueError):
            return False

    def get_image_extensions(self):
        """The extensions the viewer can display.

        The single definition of "displayable", shared with `load()` — the two used to
        disagree, so the Extension combo offered table HDUs that silently left the previous
        image on screen while `data`/`header` switched to the table underneath (B6).
        """
        extensions = []
        if self.hdul:
            for idx, hdu in enumerate(self.hdul):
                if not self._is_displayable(hdu):
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
        
    def update_header_card(self, keyword, value, comment=None, ext=None):
        """Updates or adds a header card."""
        hdr = self.get_header(ext=ext)
        if hdr is not None:
            if comment is not None:
                hdr[keyword] = (value, comment)
            else:
                hdr[keyword] = value

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

        try:
            hdul.close()
        except Exception:
            pass

        directory = os.path.dirname(os.path.abspath(save_path)) or '.'
        fd, tmp_path = tempfile.mkstemp(prefix='.pyql3_save_', suffix='.fits', dir=directory)
        os.close(fd)
        try:
            hdul.writeto(tmp_path, overwrite=True)
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
