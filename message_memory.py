#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Память диалогов: контекст чатов + опциональная персистентность в JSON.
persist_path задан — история переживает перезапуск бота; не задан —
файл ведёт себя как раньше (только в памяти).
"""

import json
import logging
import os
import threading
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessageMemory:
    def __init__(
        self,
        max_messages_per_chat: int = 200,
        persist_path: Optional[str] = None,
    ):
        self.max_messages_per_chat = max_messages_per_chat
        self.persist_path = persist_path
        self._chat_memories: Dict[int, deque] = {}
        self._lock = threading.Lock()

        if persist_path:
            self._load()

        logger.info(
            f"MessageMemory: до {max_messages_per_chat} сообщений на чат"
            + (f", файл: {persist_path}" if persist_path else ", без персистентности")
        )

    # ---------- Персистентность ----------

    def _save(self) -> None:
        """Атомарная запись (tmp + rename). Вызывается под lock."""
        if not self.persist_path:
            return
        try:
            data = {
                str(chat_id): list(msgs)
                for chat_id, msgs in self._chat_memories.items()
            }
            tmp_path = self.persist_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self.persist_path)
        except OSError as e:
            logger.error(f"Не удалось сохранить память в {self.persist_path}: {e}")

    def _load(self) -> None:
        try:
            if not os.path.exists(self.persist_path):
                return
            with open(self.persist_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.error(f"Неверный формат файла {self.persist_path}")
                return
            for chat_id_str, msgs in data.items():
                try:
                    chat_id = int(chat_id_str)
                except (TypeError, ValueError):
                    continue
                self._chat_memories[chat_id] = deque(
                    msgs[-self.max_messages_per_chat:],
                    maxlen=self.max_messages_per_chat,
                )
            total = sum(len(m) for m in self._chat_memories.values())
            logger.info(
                f"История загружена: {len(self._chat_memories)} чатов, {total} сообщений"
            )
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Не удалось загрузить память из {self.persist_path}: {e}")

    # ---------- Публичный API ----------

    def add_message(self, chat_id: int, message_data: Dict[str, Any]) -> None:
        with self._lock:
            chat = self._chat_memories.get(chat_id)
            if chat is None:
                chat = deque(maxlen=self.max_messages_per_chat)
                self._chat_memories[chat_id] = chat
            chat.append(message_data)
            self._save()

    def get_chat_messages(self, chat_id: int) -> List[Dict[str, Any]]:
        # .get() вместо []: чтение не создаёт фантомный пустой чат
        with self._lock:
            return list(self._chat_memories.get(chat_id, ()))

    def get_recent_messages(self, chat_id: int, count: int = 10) -> List[Dict[str, Any]]:
        return self.get_chat_messages(chat_id)[-count:]

    def clear_chat_memory(self, chat_id: int) -> None:
        with self._lock:
            if self._chat_memories.pop(chat_id, None) is not None:
                self._save()

    def get_memory_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'total_chats': len(self._chat_memories),
                'total_messages': sum(len(m) for m in self._chat_memories.values()),
                'chat_details': {
                    chat_id: len(msgs)
                    for chat_id, msgs in self._chat_memories.items()
                },
            }

    def cleanup_old_chats(self, keep_recent_chats: int = 100) -> int:
        """Удаляет пустые чаты и наименее активные сверх лимима.
        Возвращает число удалённых чатов."""
        with self._lock:
            empty = [cid for cid, msgs in self._chat_memories.items() if not msgs]
            for cid in empty:
                del self._chat_memories[cid]
            removed = len(empty)

            if len(self._chat_memories) > keep_recent_chats:
                by_activity = sorted(
                    self._chat_memories.items(),
                    key=lambda item: len(item[1]),
                    reverse=True,
                )
                for cid, _ in by_activity[keep_recent_chats:]:
                    del self._chat_memories[cid]
                    removed += 1

            if removed:
                self._save()
            return removed
