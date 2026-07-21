import os
import sys

try:
    from ._version import version as __version__
except ImportError:
    __version__ = "unknown"

def get_resource_path(relative_path=""):
    """Get absolute path to resource, working for dev and PyInstaller frozen bundles."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)
