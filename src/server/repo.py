import fcntl
import hashlib
import os
import subprocess
from collections.abc import Iterator
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

import attr

VCARD_SUFFIX = ".vcf"
_LOCK_NAME = "write.lock"
_GIT_TIMEOUT = 120

# Keep the container's (and a developer's) ambient git config out of the picture entirely: identity and settings
# are passed per invocation so a commit here never depends on what is configured elsewhere.
_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}

AUTHOR_NAME = "contacts"
AUTHOR_EMAIL = "contacts@localhost"


class GitError(RuntimeError):
    pass


@attr.s(auto_attribs=True, frozen=True)
class Revision:
    commit: str
    timestamp: str
    summary: str


@attr.s(auto_attribs=True, frozen=True)
class TreeChange:
    """One path's fate between two commits."""

    path: str
    is_deleted: bool


def blob_sha(content: bytes) -> str:
    """The object name git would give this content, computed without shelling out."""
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


class Repository:
    """A git working tree of ``.vcf`` files, which is the address book.

    Every write goes through :meth:`commit`, under a file lock, so concurrent CardDAV and web writes serialise
    rather than racing on git's index.  Reads go straight to the working tree and do not take the lock.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path / ".git" / _LOCK_NAME
        self._ensure_initialised()

    @property
    def path(self) -> Path:
        return self._path

    def _run(self, *arguments: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self._path,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, **_GIT_ENV},
        )
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(arguments)} failed ({result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()}"
            )
        return result.stdout.decode("utf-8", "replace")

    def _ensure_initialised(self) -> None:
        self._path.mkdir(parents=True, exist_ok=True)
        if (self._path / ".git").is_dir():
            return
        self._run("init", "-b", "main", "-q")
        # An empty root commit means HEAD always resolves, so nothing downstream has to special-case a repository
        # that has never been written to.
        self._commit_now("initialise address book")

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialise writers.

        ``flock`` is held per open file description, so this serialises threads within this process and other
        processes alike — which matters if hypercorn is ever run with more than one worker.
        """
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _commit_now(self, message: str) -> str:
        self._run(
            "-c",
            f"user.name={AUTHOR_NAME}",
            "-c",
            f"user.email={AUTHOR_EMAIL}",
            "commit",
            "--allow-empty",
            "--quiet",
            "-m",
            message,
        )
        return self.head()

    def commit(self, message: str) -> str:
        """Stage the whole working tree and commit it, if anything actually changed.

        Writing a card that is byte-for-byte what is already there must not move HEAD: HEAD is the sync token, so
        an empty commit would tell every client to resynchronise over nothing.
        """
        self._run("add", "--all", ".")
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=self._path,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, **_GIT_ENV},
        )
        if staged.returncode == 0:
            return self.head()
        return self._commit_now(message)

    def head(self) -> str:
        return self._run("rev-parse", "HEAD").strip()

    def is_known_commit(self, commit: str) -> bool:
        if not commit or any(character not in "0123456789abcdef" for character in commit.lower()):
            return False
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=self._path,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, **_GIT_ENV},
        )
        return result.returncode == 0

    def changes_since(self, commit: str) -> tuple[TreeChange, ...]:
        """What happened to each path between ``commit`` and HEAD.

        ``--no-renames`` keeps the output to one path per record, so a contact that moved reads as a deletion
        plus an addition — which is exactly what a CardDAV client needs to hear.
        """
        raw = self._run("diff", "--name-status", "--no-renames", "-z", commit, "HEAD")
        fields = [field for field in raw.split("\0") if field]
        changes: list[TreeChange] = []
        for status, path in zip(fields[::2], fields[1::2], strict=False):
            changes.append(TreeChange(path=path, is_deleted=status.startswith("D")))
        return tuple(changes)

    def history(self, relative_path: str, limit: int = 50) -> tuple[Revision, ...]:
        raw = self._run("log", f"--max-count={limit}", "--format=%H%x1f%cI%x1f%s", "--follow", "--", relative_path)
        revisions: list[Revision] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            commit, _, rest = line.partition("\x1f")
            timestamp, _, summary = rest.partition("\x1f")
            revisions.append(Revision(commit=commit, timestamp=timestamp, summary=summary))
        return tuple(revisions)

    def show(self, commit: str, relative_path: str) -> str | None:
        """The contents of a path at a past commit, or None if it did not exist there."""
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative_path}"],
            cwd=self._path,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
            env={**os.environ, **_GIT_ENV},
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", "replace")

    def write_files(self, files: Sequence[tuple[str, str]]) -> None:
        # newline="" throughout: a vCard's CRLFs are part of its bytes, and translating them would change the
        # blob name — which is the ETag — and break byte-for-byte round tripping.
        for relative_path, text in files:
            with open(self._path / relative_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)

    def remove_file(self, relative_path: str) -> bool:
        target = self._path / relative_path
        if not target.is_file():
            return False
        target.unlink()
        return True

    def list_files(self) -> tuple[tuple[str, os.stat_result], ...]:
        """Every vCard in the working tree, with its stat, skipping ``.git`` and anything else."""
        entries: list[tuple[str, os.stat_result]] = []
        with os.scandir(self._path) as scan:
            for entry in scan:
                if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                    continue
                if not entry.name.endswith(VCARD_SUFFIX):
                    continue
                entries.append((entry.name, entry.stat()))
        return tuple(entries)

    def read_file(self, relative_path: str) -> str | None:
        try:
            with open(self._path / relative_path, encoding="utf-8", newline="") as handle:
                return handle.read()
        except (FileNotFoundError, IsADirectoryError):
            return None
