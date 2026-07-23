import os
import json
from pathlib import Path

class ConfigManager:
    def __init__(self, config_file="~/.pyql3/config.json"):
        self.config_file = Path(config_file).expanduser()
        self.config = {}
        self.load()
        
    def load(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    self.config = json.load(f)
            except json.JSONDecodeError:
                self.config = {}
        else:
            self.config = {}
            
    def save(self):
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=4)
            
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
        filepath = os.path.abspath(filepath)
        recent = [f for f in self.get_recent_files() if f != filepath]
        recent.insert(0, filepath)
        recent = recent[:max_items]
        self.set("recent_files", recent)

    def remove_recent_file(self, filepath):
        if not filepath:
            return
        filepath = os.path.abspath(filepath)
        recent = [f for f in self.get_recent_files() if f != filepath]
        self.set("recent_files", recent)

    def clear_recent_files(self):
        self.set("recent_files", [])

