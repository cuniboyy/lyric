"""Per-track sync offsets, persisted so a correction only has to be made once."""
import json
import os
from pathlib import Path
import tempfile


class SyncOffsets:
    def __init__(self, path):
        self.path = Path(path)
        try:
            self.values = json.loads(self.path.read_text())
            if not isinstance(self.values, dict):
                self.values = {}
        except (OSError, ValueError):
            self.values = {}

    @staticmethod
    def key(info):
        return json.dumps([info[0], info[1], round(info[2])], ensure_ascii=False)

    def get(self, info):
        try:
            return float(self.values.get(self.key(info), 0))
        except (ValueError, TypeError):
            return 0

    def save(self, info, value):
        """Persist atomically; a crash mid-write must not lose every offset."""
        self.values[self.key(info)] = round(value, 3)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(self.values, output, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
