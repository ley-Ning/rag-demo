"""分层记忆：短期（会话历史，见 rag_service）之外的两层持久记忆。

- 全局记忆（scope=global）：全系统共享的规则/知识/口径，手动维护，所有对话注入。
- 用户长期记忆（scope=user, scope_key=用户标识）：对话后自动蒸馏出的持久事实/偏好，
  跨会话生效，也可手动增删。

注入格式（拼在 system prompt 尾部）：
    [全局记忆]
    - xxx
    [用户长期记忆]
    - yyy

记忆变更会 bump redis 的 memory:version，答案缓存键包含该版本，避免旧缓存与新记忆冲突。
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.config import get_settings
from app.core.redis_client import get_redis_client

logger = logging.getLogger(__name__)

MEMORY_VERSION_KEY = "memory:version"
MEMORY_VERSION_DEFAULT = 1

VALID_SCOPES = ("global", "user")
VALID_SOURCES = ("manual", "distilled")

MEMORY_DISTILL_SYSTEM_PROMPT = """你是长期记忆提取助手。请从下面这轮对话中提取"值得跨会话长期记住的用户事实或偏好"。

规则：
1. 只提取稳定、可复用的事实（如用户身份、偏好、约束、常用背景），不要提取一次性问题内容
2. 每条一句话，中文，不超过 60 字
3. 没有值得提取的内容时返回空数组
4. 只输出 JSON 数组本身，不要任何解释，例如：["用户偏好简洁的中文回答", "用户所在团队是数据平台组"]
"""


@dataclass
class MemoryEntry:
    id: int
    scope: str
    scope_key: str
    content: str
    source: str
    importance: int
    enabled: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "scopeKey": self.scope_key,
            "content": self.content,
            "source": self.source,
            "importance": self.importance,
            "enabled": self.enabled,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


def _row_to_entry(row: asyncpg.Record) -> MemoryEntry:
    return MemoryEntry(
        id=int(row["id"]),
        scope=row["scope"],
        scope_key=row["scope_key"],
        content=row["content"],
        source=row["source"],
        importance=int(row["importance"]),
        enabled=bool(row["enabled"]),
        created_at=row["created_at"].isoformat() if row["created_at"] else "",
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
    )


class MemoryService:
    def __init__(self) -> None:
        self._settings = get_settings()

    # ---------- 版本（缓存联动） ----------

    async def get_version(self) -> int:
        try:
            value = await get_redis_client().get_int(
                f"{self._settings.redis_key_prefix}:{MEMORY_VERSION_KEY}"
            )
            return value or MEMORY_VERSION_DEFAULT
        except Exception:
            return MEMORY_VERSION_DEFAULT

    async def bump_version(self) -> None:
        try:
            await get_redis_client().incr_int(
                f"{self._settings.redis_key_prefix}:{MEMORY_VERSION_KEY}"
            )
        except Exception:
            logger.warning("Failed to bump memory version, answer cache keeps old key")

    # ---------- 查询 ----------

    async def load_context_entries(
        self,
        conn: asyncpg.Connection,
        user_key: str = "default",
    ) -> dict[str, list[str]]:
        """加载注入上下文的记忆：全局 + 该用户的启用条目，按重要度/时间排序截断"""
        if not self._settings.layered_memory_enabled:
            return {"global": [], "user": []}
        cap = self._settings.memory_max_context_entries
        rows = await conn.fetch(
            """
            SELECT * FROM memory_entries
            WHERE enabled = TRUE
              AND ((scope = 'global') OR (scope = 'user' AND scope_key = $1))
            ORDER BY importance DESC, updated_at DESC
            LIMIT $2
            """,
            user_key or "default",
            cap * 2,
        )
        entries = [_row_to_entry(row) for row in rows]
        return {
            "global": [e.content for e in entries if e.scope == "global"][:cap],
            "user": [e.content for e in entries if e.scope == "user"][:cap],
        }

    def build_context_block(self, entries: dict[str, list[str]]) -> str:
        """生成拼接到 system prompt 的记忆块"""
        blocks: list[str] = []
        if entries.get("global"):
            lines = "\n".join(f"- {item}" for item in entries["global"])
            blocks.append(f"[全局记忆]\n{lines}")
        if entries.get("user"):
            lines = "\n".join(f"- {item}" for item in entries["user"])
            blocks.append(f"[用户长期记忆]\n{lines}")
        if not blocks:
            return ""
        return "\n\n".join(blocks)

    async def list_entries(
        self,
        conn: asyncpg.Connection,
        *,
        scope: str | None = None,
        include_disabled: bool = True,
    ) -> list[MemoryEntry]:
        conditions = ["TRUE"]
        args: list[Any] = []
        if scope and scope in VALID_SCOPES:
            args.append(scope)
            conditions.append(f"scope = ${len(args)}")
        if not include_disabled:
            conditions.append("enabled = TRUE")
        rows = await conn.fetch(
            f"""
            SELECT * FROM memory_entries
            WHERE {' AND '.join(conditions)}
            ORDER BY scope ASC, importance DESC, updated_at DESC
            LIMIT 500
            """,
            *args,
        )
        return [_row_to_entry(row) for row in rows]

    # ---------- 增删改 ----------

    async def add_entry(
        self,
        conn: asyncpg.Connection,
        *,
        scope: str,
        content: str,
        scope_key: str = "default",
        importance: int = 3,
        source: str = "manual",
    ) -> MemoryEntry:
        if scope not in VALID_SCOPES:
            raise ValueError(f"scope 仅支持 {'/'.join(VALID_SCOPES)}")
        if source not in VALID_SOURCES:
            raise ValueError(f"source 仅支持 {'/'.join(VALID_SOURCES)}")
        content = content.strip()
        if not content or len(content) > 200:
            raise ValueError("content 需在 1-200 字符之间")
        importance = max(1, min(int(importance), 5))

        # 去重：同 scope 下已有完全相同内容则直接返回旧条目
        existing = await conn.fetchrow(
            """
            SELECT * FROM memory_entries
            WHERE scope = $1 AND scope_key = $2 AND content = $3
            LIMIT 1
            """,
            scope,
            scope_key if scope == "user" else "default",
            content,
        )
        if existing is not None:
            return _row_to_entry(existing)

        row = await conn.fetchrow(
            """
            INSERT INTO memory_entries (scope, scope_key, content, source, importance)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            scope,
            scope_key if scope == "user" else "default",
            content,
            source,
            importance,
        )
        await self._prune_scope(conn, scope, scope_key if scope == "user" else "default")
        await self.bump_version()
        return _row_to_entry(row)

    async def update_entry(
        self,
        conn: asyncpg.Connection,
        entry_id: int,
        *,
        content: str | None = None,
        importance: int | None = None,
        enabled: bool | None = None,
    ) -> MemoryEntry:
        updates: list[str] = []
        args: list[Any] = []
        if content is not None:
            content = content.strip()
            if not content or len(content) > 200:
                raise ValueError("content 需在 1-200 字符之间")
            args.append(content)
            updates.append(f"content = ${len(args)}")
        if importance is not None:
            args.append(max(1, min(int(importance), 5)))
            updates.append(f"importance = ${len(args)}")
        if enabled is not None:
            args.append(bool(enabled))
            updates.append(f"enabled = ${len(args)}")
        if not updates:
            raise ValueError("未提供可更新字段")

        args.append(entry_id)
        row = await conn.fetchrow(
            f"""
            UPDATE memory_entries
            SET {', '.join(updates)}, updated_at = NOW()
            WHERE id = ${len(args)}
            RETURNING *
            """,
            *args,
        )
        if row is None:
            raise KeyError("记忆条目不存在")
        await self.bump_version()
        return _row_to_entry(row)

    async def delete_entry(self, conn: asyncpg.Connection, entry_id: int) -> None:
        result = await conn.execute("DELETE FROM memory_entries WHERE id = $1", entry_id)
        if result.endswith(" 0"):
            raise KeyError("记忆条目不存在")
        await self.bump_version()

    async def _prune_scope(
        self, conn: asyncpg.Connection, scope: str, scope_key: str
    ) -> None:
        """单 scope 超量时淘汰最旧且重要度最低的条目"""
        max_entries = self._settings.memory_max_entries_per_scope
        try:
            await conn.execute(
                """
                DELETE FROM memory_entries
                WHERE id IN (
                    SELECT id FROM memory_entries
                    WHERE scope = $1 AND scope_key = $2
                    ORDER BY importance DESC, updated_at DESC
                    OFFSET $3
                )
                """,
                scope,
                scope_key,
                max_entries,
            )
        except Exception:
            logger.warning("Memory prune skipped")

    # ---------- 蒸馏（对话 -> 长期记忆） ----------

    async def distill_from_exchange(
        self,
        question: str,
        answer: str,
        *,
        user_key: str = "default",
        model_id: str,
        registry: Any,
    ) -> list[MemoryEntry]:
        """用对话模型从一问一答中提取用户长期记忆（best-effort，失败返回空）"""
        if not self._settings.memory_distill_enabled:
            return []
        if len(answer.strip()) < self._settings.memory_distill_min_answer_chars:
            return []

        try:
            facts = await self._extract_facts(question, answer, model_id, registry)
        except Exception as exc:
            logger.warning("Memory distillation extraction failed: %s", exc)
            return []
        if not facts:
            return []

        saved: list[MemoryEntry] = []
        try:
            from app.core.database import db_conn_context

            async with db_conn_context() as conn:
                for fact in facts[:5]:
                    entry = await self.add_entry(
                        conn,
                        scope="user",
                        content=fact,
                        scope_key=user_key or "default",
                        importance=2,
                        source="distilled",
                    )
                    saved.append(entry)
        except Exception:
            logger.exception("Memory distillation persist failed")
            return []
        logger.info("Distilled %s memory entries for user=%s", len(saved), user_key)
        return saved

    async def _extract_facts(
        self,
        question: str,
        answer: str,
        model_id: str,
        registry: Any,
    ) -> list[str]:
        from app.domain.rag_service import get_rag_service

        rag_service = get_rag_service()
        raw = await rag_service.generate_text(
            system_prompt=MEMORY_DISTILL_SYSTEM_PROMPT,
            user_content=f"【用户提问】\n{question[:1000]}\n\n【助手回答】\n{answer[:2000]}",
            model_id=model_id,
            registry=registry,
            temperature=0.0,
            max_tokens=300,
        )
        text = (raw or "").strip()
        # 容错：剥掉可能的 markdown 代码围栏
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item).strip() for item in parsed if str(item).strip()][:5]


_memory_service = MemoryService()


def get_memory_service() -> MemoryService:
    return _memory_service


def now_ms() -> int:
    return int(time.monotonic() * 1000)
