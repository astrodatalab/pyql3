import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PySide6.QtCore import QObject, Signal

class FITSFileHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        
    def on_created(self, event):
        if not event.is_directory:
            ext = os.path.splitext(event.src_path)[1].lower()
            if ext in ['.fits', '.fit']:
                # The file might not be fully written yet when on_created fires.
                # In a robust system, we might wait a bit or use on_modified or try to open it.
                # For QL, just emitting is often enough.
                self.callback(event.src_path)
                
    def on_moved(self, event):
        if not event.is_directory:
            ext = os.path.splitext(event.dest_path)[1].lower()
            if ext in ['.fits', '.fit']:
                self.callback(event.dest_path)

class DirectoryPoller(QObject):
    file_detected = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.observer = None
        self.watch_path = None
        
    def start_polling(self, path):
        self.stop_polling()
        self.watch_path = path
        
        if not os.path.exists(path):
            return False
            
        event_handler = FITSFileHandler(self._on_file)
        self.observer = Observer()
        self.observer.schedule(event_handler, path, recursive=False)
        self.observer.start()
        return True
        
    def stop_polling(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            
    def _on_file(self, path):
        self.file_detected.emit(path)
