"""Watch-folder mode: monitor a directory and auto-submit upscale jobs.

Runs as a background thread, controlled by config flags.
"""
import logging
import os
import time
from typing import Callable, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("upscaling.watcher")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}


class _UpscaleHandler(FileSystemEventHandler):
    """Submit upscale jobs for new video files."""

    def __init__(self, submit_fn: Callable[[str], None]):
        self.submit_fn = submit_fn

    def on_created(self, event):
        if event.is_directory:
            return
        ext = os.path.splitext(event.src_path)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            return

        path = event.src_path
        logger.info(f"New file detected: {path}")

        # Wait for file to finish writing (size stability check)
        prev_size = -1
        for _ in range(10):  # up to 20 seconds
            time.sleep(2)
            try:
                curr_size = os.path.getsize(path)
            except OSError:
                return  # file disappeared
            if curr_size == prev_size and curr_size > 0:
                break
            prev_size = curr_size
        else:
            logger.warning(f"File {path} still changing after 20s, submitting anyway")

        self.submit_fn(path)


class FolderWatcher:
    """Watches an input directory and calls submit_fn for each new video file."""

    def __init__(self, input_dir: str, submit_fn: Callable[[str], None]):
        self.input_dir = input_dir
        self.submit_fn = submit_fn
        self._observer: Optional[Observer] = None

    def start(self):
        if not os.path.isdir(self.input_dir):
            os.makedirs(self.input_dir, exist_ok=True)
        handler = _UpscaleHandler(self.submit_fn)
        self._observer = Observer()
        self._observer.schedule(handler, self.input_dir, recursive=False)
        self._observer.daemon = True
        self._observer.start()
        logger.info(f"Watching folder: {self.input_dir}")

    def stop(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Folder watcher stopped")
