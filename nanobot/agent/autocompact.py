"""Auto compact: proactive compression of idle sessions to reduce token cost and latency.
决定什么时候应该去压缩一个 Session,真正的压缩在memory 里的 consolidator"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine, cast

from loguru import logger

from nanobot.session.manager import MIN_COMPACTED_REPLAY_MESSAGES, Session, SessionManager

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator
    from nanobot.utils.llm_runtime import LLMRuntime


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = MIN_COMPACTED_REPLAY_MESSAGES
    _INTERNAL_SESSION_PREFIXES = ("dream:",)

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes
        self._archiving: set[str] = set() # 待压缩key集合
        self._summaries: dict[str, tuple[str, datetime]] = {} # 压缩结果，保存总结

    # 判断是否超时
    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        try:
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            current = now or datetime.now()
            if getattr(ts, "tzinfo", None) is not None or current.tzinfo is not None:
                idle_seconds = current.timestamp() - ts.timestamp()
            else:
                idle_seconds = (current - ts).total_seconds()
        except (OSError, OverflowError, TypeError, ValueError):
            # list_sessions() forwards raw persisted metadata; an unusable value
            # must not escape the idle scan and stop the agent loop.
            return False
        return idle_seconds >= self._ttl * 60

    # 判断是否有可压缩的项，从retain_recent_legal_suffix函数得出，判断待删除项目里是否还有未压缩的消息
    def _has_unarchived_messages(self, key: str) -> bool:
        session = self.sessions.get_or_create(key)
        return session.last_consolidated < len(session.messages)

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        return f"Previous conversation summary (last active {last_active.isoformat()}):\n{text}"

    # 判断是否为dream session，dream的session不参与 autocompact
    @classmethod
    def _is_internal_session(cls, key: str) -> bool:
        return key.startswith(cls._INTERNAL_SESSION_PREFIXES)

    # 周期性扫描所有 Session，把满足条件的 Session 交给 Consolidator 去后台压缩。
    def check_expired(
        self,
        schedule_background: Callable[[Coroutine[Any, Any, None]], None],
        resolve_runtime: Callable[[Session], LLMRuntime],
        active_session_keys: Collection[str] = (),
    ) -> None:
        """Schedule archival for idle sessions, skipping those with in-flight agent tasks."""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            # 两层过滤，过滤dream session，正在压缩的session，正在聊天的session
            if not key or self._is_internal_session(key) or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            updated_at = info.get("updated_at")
            if self._is_expired(updated_at, now) and self._has_unarchived_messages(key):
                session = self.sessions.get_or_create(key)
                try:
                    runtime = resolve_runtime(session)
                except (KeyError, ValueError):
                    # Invalid session selections remain recoverable through /model.
                    continue
                self._archiving.add(key)
                schedule_background(self._archive(key, runtime=runtime))

    # 压缩session，压缩结束后将其从archiving 集合删除
    async def _archive(self, key: str, *, runtime: LLMRuntime) -> None:
        # 剔除dream session
        if self._is_internal_session(key):
            self._archiving.discard(key)
            return
        try:
            # 压缩真正执行
            summary = await self.consolidator.compact_idle_session(
                key,
                runtime=runtime,
                max_suffix=self._RECENT_SUFFIX_MESSAGES,
            )
            if summary and summary != "(nothing)":
                session = self.sessions.get_or_create(key)
                meta = session.metadata.get("_last_summary")
                if isinstance(meta, dict):
                    self._summaries[key] = (
                        cast(str, meta["text"]),
                        datetime.fromisoformat(cast(str, meta["last_active"])),
                    )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    # 在开始新一轮对话之前，确保拿到的是最新 Session，并把后台生成的 Summary 安全交给本轮 Prompt。
    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        if self._is_internal_session(key):
            self._archiving.discard(key)
            self._summaries.pop(key, None)
            return session, None
        # 读取最新的session
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        # Hot path: summary from in-memory dict (process hasn't restarted).
        entry = self._summaries.pop(key, None)
        if entry:
            return session, self._format_summary(entry[0], entry[1])
        # Cold path: summary persisted in session metadata (process restarted).
        # Persisted metadata may outlive schema changes; a malformed summary must
        # not abort turn preparation.
        meta = session.metadata.get("_last_summary")
        if isinstance(meta, dict):
            summary_meta = cast(dict[str, object], meta)
            text = summary_meta.get("text")
            if isinstance(text, str) and text:
                raw_last_active = summary_meta.get("last_active")
                try:
                    last_active = (
                        datetime.fromisoformat(raw_last_active)
                        if isinstance(raw_last_active, str)
                        else session.updated_at
                    )
                except ValueError:
                    last_active = session.updated_at
                return session, self._format_summary(text, last_active)
        return session, None
