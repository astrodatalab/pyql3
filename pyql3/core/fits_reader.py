import os
from astropy.io import fits
import numpy as np

class FitsReader:
    """Wrapper class for handling FITS file reading and header management."""
    def __init__(self, filepath=None):
        self.filepath = filepath
        self.hdul = None
        self.data = None
        self.header = None
        
        if filepath:
            self.load(filepath)

    def load(self, filepath, ext=None):
        """Loads a FITS file and its primary data/header."""
        # If loading a different file, close the old one
        if self.filepath != filepath and self.hdul is not None:
            self.close()
            
        self.filepath = filepath
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"FITS file not found: {filepath}")
        
        if self.hdul is None:
            self.hdul = fits.open(filepath)
        
        # Look for the first extension with image data
        self.image_extensions = []
        for i, hdu in enumerate(self.hdul):
            if hdu.is_image and hdu.data is not None:
                name = hdu.name if hdu.name else f"EXT {i}"
                if name == "PRIMARY" and i != 0:
                    name = f"EXT {i}"
                self.image_extensions.append((i, name))
                
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
        self.current_ext = 0
        self.data = data
        self.header = header
        
    def get_image_extensions(self):
        extensions = []
        if self.hdul:
            for idx, hdu in enumerate(self.hdul):
                if hdu.data is not None:
                    name = hdu.name if hdu.name else f"EXT {idx}"
                    extensions.append((idx, name))
        return extensions
    def get_data(self):
        return self.data
        
    def get_header(self):
        return self.header
        
    def update_header_card(self, keyword, value, comment=None):
        """Updates or adds a header card."""
        if self.header is not None:
            if comment is not None:
                self.header[keyword] = (value, comment)
            else:
                self.header[keyword] = value

    def save(self, output_filepath=None):
        """Saves the modified FITS file."""
        if self.hdul is None:
            raise ValueError("No FITS file loaded.")
        
        save_path = output_filepath or self.filepath
        self.hdul.writeto(save_path, overwrite=True)
        
    def close(self):
        """Closes the FITS file handle."""
        if self.hdul is not None:
            self.hdul.close()
            self.hdul = None
            self.data = None
            self.header = None
