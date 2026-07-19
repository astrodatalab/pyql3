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
