#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент DeepSeek API (прямое подключение, не OpenRouter).
Имя класса OpenRouterClient сохранено для совместимости с main.py.
"""

import asyncio
import logging
from typing import List, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """
    Асинхронный клиент DeepSeek с таймаутами и ретраями.

    model='deepseek-chat'     — V3: быстрая, дешёвая, держит системный промпт (рекомендую).
    model='deepseek-reasoner' — R1: рассуждения платные и входят в max_tokens,
                                temperature не поддерживает, системный промпт держит слабо.
    """

    RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        max_tokens: int = 600,
        temperature: float = 0.8,
        timeout: float = 60.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com"
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.session: Optional[aiohttp.ClientSession] = None

    # ---------- Внутреннее ----------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self.session

    @staticmethod
    def _extract_content(data: dict) -> Optional[str]:
        choices = data.get("choices") or []
        if not choices:
            logger.error(f"Нет choices в ответе: {str(data)[:300]}")
            return None

        message = choices[0].get("message") or {}
        content = (message.get("content") or "").strip()

        if not content:
            finish_reason = choices[0].get("finish_reason")
            logger.error(
                f"Пустой content (finish_reason={finish_reason}). Частая причина: "
                "max_tokens меньше, чем модель потратила (у deepseek-reasoner "
                "рассуждения входят в лимит)."
            )
            return None

        usage = data.get("usage", {})
        logger.info(
            f"Ответ получен: {len(content)} символов; токены: "
            f"{usage.get('prompt_tokens')}+{usage.get('completion_tokens')}"
        )
        return content

    # ---------- Публичное ----------

    async def generate_response(self, messages: List[Dict]) -> Optional[str]:
        """Генерация ответа. None — только если все попытки провалились."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        # deepseek-reasoner не поддерживает temperature — не отправляем
        if "reasoner" not in self.model:
            payload["temperature"] = self.temperature

        session = await self._get_session()
        logger.info(f"Запрос к DeepSeek ({self.model}), сообщений: {len(messages)}")

        last_error = "нет данных"
        total_attempts = self.max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                async with session.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status == 200:
                        return self._extract_content(await response.json())

                    error_text = (await response.text())[:500]

                    if response.status not in self.RETRYABLE_STATUS:
                        # 400/401/402/403 — повторять бессмысленно
                        logger.error(f"DeepSeek API error {response.status}: {error_text}")
                        return None

                    last_error = f"HTTP {response.status}: {error_text}"
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after)
                    except (TypeError, ValueError):
                        delay = 2.0 ** attempt

            except asyncio.TimeoutError:
                last_error = f"таймаут {self.timeout}с"
                delay = 2.0 ** attempt
            except aiohttp.ClientError as e:
                last_error = f"сетевая ошибка: {e}"
                delay = 2.0 ** attempt
            except Exception as e:
                logger.error(f"Неожиданная ошибка DeepSeek: {e}", exc_info=True)
                return None

            if attempt < total_attempts:
                logger.warning(
                    f"DeepSeek: {last_error}. "
                    f"Повтор {attempt + 1}/{total_attempts} через {delay:.0f}с"
                )
                await asyncio.sleep(delay)

        logger.error(f"DeepSeek: все попытки исчерпаны ({last_error})")
        return None

    async def close(self) -> None:
        """Аккуратно закрывает сессию. Вызывать из post_shutdown бота."""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("DeepSeek-клиент: сессия закрыта")
