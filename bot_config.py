#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration for the Architect Bot
"""

import os
import logging

logger = logging.getLogger(__name__)

# Системный промпт «Архитектора Судьбы»: одно правило — одна строка, минимум токенов
SYSTEM_PROMPT = """Ты — «Архитектор Судьбы»: ведёшь человека через жизненные уроки. Не утешай — выводи из иллюзий к инсайту.
Стиль: просто, логично, дерзко; без унижений — с уважением и заботой.
Метод:
— Советов не давай: только вопросы и честные наблюдения, ответ человек находит сам.
— Точно вскрывай противоречия в его словах и действиях.
— Зеркало: претензии к другим — отражение самого человека; подавай как гипотезу-вопрос.
— Опирайся на контекст беседы и блок «Данные пользователя», если он есть.
Формат: 2–5 предложений + открытый вопрос + одна строка практики (простое физическое действие; подавай как эксперимент, варьируй).
Запрет: диагнозы, мед./фин. советы, гарантии результата; при признаках острого кризиса (самоповреждение, насилие, отчаяние) мягко выйди из роли и порекомендуй живого специалиста.
Язык: русский."""


def _get_env_var(name: str, required: bool = True, default: str = None) -> str:
    """Обязательная или опциональная переменная окружения"""
    value = os.getenv(name)
    if not value:
        if required:
            raise ValueError(f"Environment variable {name} is required!")
        logger.info(f"{name} не задана, использую значение по умолчанию")
        return default
    logger.info(f"Loaded {name}")
    return value


class BotConfig:
    def __init__(self):
        # --- Обязательные ---
        self.telegram_bot_token = _get_env_var('TELEGRAM_BOT_TOKEN')
        self.openrouter_api_key = _get_env_var('OPENROUTER_API_KEY')

        # --- Параметры LLM: опционально, меняются через env без правки кода ---
        # Проверь точный слаг модели, который используешь на OpenRouter
        self.model = _get_env_var('DEEPSEEK_MODEL', required=False,
                                  default='deepseek/deepseek-chat')
        # Жёсткий потолок токенов на один ответ — прямой рычаг экономии
        self.max_tokens = int(_get_env_var('MAX_TOKENS', required=False, default='600'))
        self.temperature = float(_get_env_var('TEMPERATURE', required=False, default='0.8'))

        # --- Память контекста ---
        # 12 вместо 7: боту нужны более ранние слова пользователя, чтобы
        # вскрывать противоречия; компактный формат ответов компенсирует рост
        self.max_history_messages = int(_get_env_var('MAX_HISTORY_MESSAGES',
                                                     required=False, default='12'))

        # --- Промпт ---
        self.system_prompt = SYSTEM_PROMPT
