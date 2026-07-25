import os
import sys

def _determine_version():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_dir = os.path.join(base_dir, ".git")
    
    # 1. If in a git repository, query setuptools_scm or git describe
    if os.path.exists(git_dir):
        try:
            from setuptools_scm import get_version
            return get_version(root=base_dir, relative_to=__file__)
        except Exception:
            pass

        try:
            import subprocess
            res = subprocess.run(
                ['git', 'describe', '--tags', '--always', '--dirty'],
                cwd=base_dir,
                capture_output=True,
                text=True,
                timeout=2
            )
            if res.returncode == 0 and res.stdout.strip():
                ver = res.stdout.strip()
                return ver[1:] if ver.startswith('v') else ver
        except Exception:
            pass

    # 2. Try installed package metadata via importlib.metadata
    try:
        import importlib.metadata
        return importlib.metadata.version("pyql3")
    except Exception:
        pass

    # 3. Fallback to static _version.py if present
    try:
        from ._version import version
        return version
    except ImportError:
        return "unknown"

__version__ = _determine_version()

def get_resource_path(relative_path=""):
    """Get absolute path to resource, working for dev and PyInstaller frozen bundles."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)
