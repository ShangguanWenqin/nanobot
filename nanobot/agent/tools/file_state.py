"""Track file-read state for read-before-edit warnings and read deduplication.
FileStates 是 Agent 文件操作的“记忆账本”，追踪文件的操作状态
"""

from __future__ import annotations

import hashlib
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ReadState:
    mtime: float # 修改时间
    offset: int # 文件读取的起始位置
    limit: int | None # 文件读取的行数
    content_hash: str | None # 整个文件内容的 SHA-256 Hash
    can_dedup: bool # 当前这条读取记录是否允许作为“重复读取”的依据。


# 计算文件hash值
def _hash_file(p: str) -> str | None:
    try:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


# 文件状态字典，一个session一个
class FileStates:
    """Per-session read/write tracker.

    Owns its own state dict so read-dedup ("File unchanged since last read")
    and read-before-edit warnings stay scoped to one agent session and do
    not leak across sessions sharing this process.
    """

    __slots__ = ("_state",)

    def __init__(self) -> None:
        self._state: dict[str, ReadState] = {}

    # Agent 成功读取一个文件之后，记录这次读取
    def record_read(self, path: str | Path, offset: int = 1, limit: int | None = None) -> None:
        """Record that a file was read (called after successful read)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=offset,
            limit=limit,
            content_hash=_hash_file(p),
            can_dedup=True,
        )

    # Agent 修改一个文件之后，记录这次更新
    def record_write(self, path: str | Path) -> None:
        """Record that a file was written (updates mtime in state)."""
        p = str(Path(path).resolve())
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            self._state.pop(p, None)
            return
        self._state[p] = ReadState(
            mtime=mtime,
            offset=1,
            limit=None,
            content_hash=_hash_file(p),
            can_dedup=False, # 修改后读取缓存应该要更新
        )

    # 验证一个文件是否读过，且是最新状态（未修改）
    def check_read(self, path: str | Path) -> str | None:
        """Check if a file has been read and is fresh.

        Returns None if OK, or a warning string.
        When mtime changed but file content is identical (e.g. touch, editor save),
        the check passes to avoid false-positive staleness warnings.
        """
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return "Warning: file has not been read yet. Read it first to verify content before editing."
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return None
        # 修改时间不一致，但文件hash一致，更新文件状态中的修改时间
        if current_mtime != entry.mtime:
            if entry.content_hash and _hash_file(p) == entry.content_hash:
                entry.mtime = current_mtime
                return None
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        # mtime unchanged - still check content hash to detect quick modifications
        # hash值变了，需要重新读取
        if entry.content_hash and _hash_file(p) != entry.content_hash:
            return "Warning: file has been modified since last read. Re-read to verify content before editing."
        return None

    # 判断文件是否按之前的参数读取（offset和limit），且内容没变
    def is_unchanged(self, path: str | Path, offset: int = 1, limit: int | None = None) -> bool:
        """Return True if file was previously read with same params and content is unchanged."""
        p = str(Path(path).resolve())
        entry = self._state.get(p)
        if entry is None:
            return False
        if not entry.can_dedup:
            return False
        if entry.offset != offset or entry.limit != limit:
            return False
        try:
            current_mtime = os.path.getmtime(p)
        except OSError:
            return False
        if current_mtime != entry.mtime:
            # mtime changed - check if content also changed
            current_hash = _hash_file(p)
            if current_hash != entry.content_hash:
                # Content actually changed - don't dedup
                entry.can_dedup = False
                return False
            # Content identical despite mtime change (e.g. touch) - mark as not dedupable to force full read next time
            # 尽管hash没变，但mtime变了，下次就不允许can_dedup了，需要重新读取
            entry.can_dedup = False
            return True
        # mtime unchanged - content must be identical
        return True

    def get(self, path: str | Path) -> ReadState | None:
        """Return the raw ReadState entry for a path, or None."""
        return self._state.get(str(Path(path).resolve()))

    def raw_state(self) -> dict[str, ReadState]:
        """Return the mutable backing map for legacy compatibility."""
        return self._state

    def clear(self) -> None:
        """Clear all tracked state (useful for testing)."""
        self._state.clear()


# 多session德filestate
class FileStateStore:
    """Lookup table for per-session file read/write state."""

    __slots__ = ("_states_by_key",)

    def __init__(self) -> None:
        self._states_by_key: dict[str, FileStates] = {}

    def for_session(self, session_key: str | None) -> FileStates:
        key = session_key or "__default__"
        states = self._states_by_key.get(key)
        if states is None:
            states = FileStates()
            self._states_by_key[key] = states
        return states

    def clear(self) -> None:
        self._states_by_key.clear()


# 通过ContextVar实现 FileStates 隐式传递
_current_file_states: ContextVar[FileStates | None] = ContextVar(
    "nanobot_file_states",
    default=None,
)


def current_file_states(default: FileStates) -> FileStates:
    """Return the FileStates bound to the current agent task, or a fallback."""
    return _current_file_states.get() or default


def bind_file_states(file_states: FileStates) -> Token[FileStates | None]:
    """Bind file read/write state for the current async task."""
    return _current_file_states.set(file_states)


def reset_file_states(token: Token[FileStates | None]) -> None:
    _current_file_states.reset(token)


# Module-level default instance, retained for backward compatibility with
# tests and callers that reach in directly. Per-session callers should hold
# their own FileStates instance instead of touching this one.
# 默认的文件状态
_default = FileStates()


def record_read(path: str | Path, offset: int = 1, limit: int | None = None) -> None:
    _default.record_read(path, offset=offset, limit=limit)


def record_write(path: str | Path) -> None:
    _default.record_write(path)


def check_read(path: str | Path) -> str | None:
    return _default.check_read(path)


def is_unchanged(path: str | Path, offset: int = 1, limit: int | None = None) -> bool:
    return _default.is_unchanged(path, offset=offset, limit=limit)


def clear() -> None:
    _default.clear()


# Legacy attribute for callers that reached into the module-level dict
# directly (filesystem.py used to do this). Kept as a property-like accessor
# so existing imports keep working.
# 兼容旧版本的文件状态获取
def __getattr__(name: str):
    if name == "_state":
        return _default.raw_state()
    raise AttributeError(name)


"""
                    FileStateStore
                          │
                    session_key
                          │
              ┌───────────┴───────────┐
              │                       │
         Session A               Session B
              │                       │
              ▼                       ▼
         FileStates               FileStates
              │                       │
              ▼                       ▼
          _state dict              _state dict
              │
              ▼
          "/foo.py"
              │
              ▼
          ReadState
          ├── mtime
          ├── offset
          ├── limit
          ├── content_hash
          └── can_dedup


                    Agent Task
                        │
                        ▼
                bind_file_states()
                        │
                        ▼
                   ContextVar
                        │
                        ▼
              current_file_states()
                        │
                        ▼
                Filesystem Tool
                  /          \
                 /            \
                ▼              ▼
          check_read()   is_unchanged()
                │              │
                ▼              ▼
        Read-before-edit   Read dedup
"""
