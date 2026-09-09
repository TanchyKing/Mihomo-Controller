from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class ControllerError(Exception):
    """A safe, user-facing controller failure."""


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, data) -> None:
    atomic_write(path, (json.dumps(data, indent=2) + '\n').encode())


class Paths:
    def __init__(self, data: Path | None = None, config: Path | None = None):
        self.data = data or Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'minimal-mihomo'
        self.config = config or Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'minimal-mihomo'
        self.runtime = self.data / 'runtime/runtime.yaml'
        self.good = self.data / 'runtime/runtime.last-good.yaml'
        self.candidate = self.data / 'runtime/candidate.yaml'
        self.transaction = self.data / 'runtime/transaction.json'
        self.state = self.data / 'runtime/state.json'
        self.profiles = self.data / 'profiles.json'
        self.settings = self.config / 'settings.json'
        self.core = self.data / 'core'
        for directory in (self.data, self.config, self.runtime.parent, self.core):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def lock(self):
        with (self.data / 'controller.lock').open('a') as stream:
            os.chmod(stream.name, 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ControllerError('Another controller operation is in progress.') from None
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
