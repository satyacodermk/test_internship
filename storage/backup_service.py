"""Timestamped backups of the JSON data files, with validation and restore."""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

import config
from config import DATA_FILES, MAX_BACKUPS, ValidationError
from storage.json_storage import JsonStorage

_FILE_TO_NAME = {filename: name for name, filename in DATA_FILES.items()}


class BackupService:
    def __init__(self, data_dir=None, backup_dir=None, validator=None, activity=None,
                 now=datetime.now, max_backups=MAX_BACKUPS):
        self.data_dir = Path(data_dir) if data_dir else config.DATA_DIR
        self.backup_dir = Path(backup_dir) if backup_dir else config.BACKUP_DIR
        self.validator = validator or JsonStorage.validate
        self.activity = activity
        self.now = now
        self.max_backups = max_backups

    def _folders(self):
        if not self.backup_dir.exists():
            return []
        return sorted((p for p in self.backup_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)

    def _folder_valid(self, folder):
        files = [f for f in folder.glob("*.json") if f.name in _FILE_TO_NAME]
        if not files:
            return False
        try:
            return all(self.validator(_FILE_TO_NAME[f.name], JsonStorage.load_file(f)) for f in files)
        except Exception:
            return False

    def _log(self, action, detail):
        if self.activity:
            self.activity.log("system", "-", action, "OK", detail)

    def create_backup(self, reason="manual"):
        """Copy every data file into a new timestamped folder and return its name."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.now().strftime("%Y-%m-%d_%H-%M-%S")
        name, counter = stamp, 2
        while (self.backup_dir / name).exists():
            name, counter = f"{stamp}_{counter}", counter + 1
        target = self.backup_dir / name
        target.mkdir()
        for filename in DATA_FILES.values():
            source = self.data_dir / filename
            if source.exists():
                shutil.copy2(source, target / filename)
        for old in self._folders()[self.max_backups:]:
            shutil.rmtree(old, ignore_errors=True)
        self._log("BACKUP", f"{name} ({reason})")
        return name

    def list_backups(self):
        result = []
        for folder in self._folders():
            size = sum(f.stat().st_size for f in folder.iterdir() if f.is_file())
            result.append({"name": folder.name, "size": size, "valid": self._folder_valid(folder)})
        return result

    def latest_valid(self):
        return next((b["name"] for b in self.list_backups() if b["valid"]), None)

    def _copy_atomic(self, source, destination):
        tmp = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}.tmp")
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)

    def restore_file(self, filename):
        """Restore one data file from the newest backup where it is valid. Returns the backup name or None."""
        logical = _FILE_TO_NAME[filename]
        for folder in self._folders():
            candidate = folder / filename
            if not candidate.exists():
                continue
            try:
                if self.validator(logical, JsonStorage.load_file(candidate)):
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    self._copy_atomic(candidate, self.data_dir / filename)
                    self._log("RESTORE_FILE", f"{filename} from {folder.name}")
                    return folder.name
            except Exception:
                continue
        return None

    def restore_backup(self, name):
        """Restore every file from a backup (the current data is backed up first)."""
        folder = self.backup_dir / name
        if not folder.is_dir():
            raise ValidationError(f"Backup '{name}' does not exist.")
        if not self._folder_valid(folder):
            raise ValidationError(f"Backup '{name}' is damaged and cannot be restored.")
        self.create_backup("pre-restore")
        for file in folder.glob("*.json"):
            if file.name in _FILE_TO_NAME:
                self._copy_atomic(file, self.data_dir / file.name)
        self._log("RESTORE", name)
        return name
