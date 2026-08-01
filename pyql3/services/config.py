import os
import json
import tempfile
from pathlib import Path


class ConfigManager:
    """User settings persisted to ``~/.pyql3/config.json``.

    A damaged config must never stop the application from starting. This is
    constructed from ``MainWindow.__init__``, so an exception here means the window
    never opens and the user has no way to discover why, let alone repair it — the
    only fix would be deleting the file from a terminal. Anything unreadable or
    unexpected is therefore moved aside and replaced with defaults.
    """

    def __init__(self, config_file="~/.pyql3/config.json"):
        self.config_file = Path(config_file).expanduser()
        self.config = {}
        self.load()

    def load(self):
        self.config = {}
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            # Previously only json.JSONDecodeError was caught, so non-UTF-8 bytes
            # (UnicodeDecodeError) or the path existing as a directory / being
            # unreadable (OSError) propagated out of __init__ and the app could not
            # start. ValueError also covers JSONDecodeError, which subclasses it.
            self._quarantine(f"{type(exc).__name__}: {exc}")
            return

        if not isinstance(loaded, dict):
            # Valid JSON of the wrong shape, e.g. "[1, 2]". This parsed cleanly and
            # then raised AttributeError on the first .get() call.
            self._quarantine(f"expected a JSON object, found {type(loaded).__name__}")
            return

        self.config = loaded

    def _quarantine(self, reason):
        """Move a damaged config aside, so defaults are used and the next save is clean."""
        spoiled = self.config_file.with_name(self.config_file.name + '.corrupt')
        try:
            os.replace(self.config_file, spoiled)
            outcome = f"moved to {spoiled}"
        except OSError:
            outcome = "could not be moved aside; it will be overwritten on the next save"
        print(f"[pyql3] Ignoring unreadable config {self.config_file} ({reason}); {outcome}")

    def save(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        # Write a sibling temp file and swap it in. A crash or power loss partway
        # through a direct overwrite would leave a truncated config, which the load
        # path above would then have to quarantine.
        fd, tmp_path = tempfile.mkstemp(
            prefix='.' + self.config_file.name + '.',
            suffix='.tmp',
            dir=str(self.config_file.parent),
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.config_file)
        except BaseException:
            # BaseException so an interrupt cannot strand the temp file either.
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
            
    def get(self, key, default=None):
        return self.config.get(key, default)
        
    def set(self, key, value):
        self.config[key] = value
        self.save()

    def get_recent_files(self):
        recent = self.get("recent_files", [])
        if not isinstance(recent, list):
            return []
        return [str(f) for f in recent if isinstance(f, str)]

    def add_recent_file(self, filepath, max_items=10):
        if not filepath:
            return
        filepath = os.path.realpath(os.path.abspath(os.path.expanduser(filepath)))
        recent = [os.path.realpath(os.path.abspath(os.path.expanduser(f))) for f in self.get_recent_files()]
        recent = [f for f in recent if f != filepath]
        recent.insert(0, filepath)
        recent = recent[:max_items]
        self.set("recent_files", recent)

    def remove_recent_file(self, filepath):
        if not filepath:
            return
        filepath = os.path.realpath(os.path.abspath(os.path.expanduser(filepath)))
        recent = [os.path.realpath(os.path.abspath(os.path.expanduser(f))) for f in self.get_recent_files()]
        recent = [f for f in recent if f != filepath]
        self.set("recent_files", recent)

    def clear_recent_files(self):
        self.set("recent_files", [])

