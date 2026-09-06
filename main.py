#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot "Архитектор Судьбы" (DeepSeek API).
Личные чаты — отвечает на всё; группы — только на упоминание.
История диалогов и профили пользователей переживают перезапуск (JSON-файлы).
"""

import json
import logging
import re
import threading
from typing import Dict, Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from bot_config import BotConfig
from openrouter_client import OpenRouterClient
from message_memory import MessageMemory
from keep_alive import keep_alive_thread

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_MSG_LIMIT = 4096  # лимит Telegram на одно сообщение
GROUP_KEYWORDS = ("наставник", "архитектор")  # триггеры в группах


class ArchitectBot:
    def __init__(self):
        self.config = BotConfig()

        # AI-клиент: все параметры берём из конфига (env)
        self.ai_client = OpenRouterClient(
            api_key=self.config.openrouter_api_key,
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            timeout=self.config.request_timeout,
        )

        # Память: храним memory_messages (50), в LLM уходит max_history_messages (12).
        # Хранить больше, чем отправлять, бесплатно — запас нужен для дайджестов
        self.memory = MessageMemory(
            max_messages_per_chat=self.config.memory_messages,
            persist_path=self.config.memory_file,
        )
        self.bot_username = None
        # Персональные данные: chat_id -> {birth_date, psychotype, session_digest}
        self.user_data = self._load_user_data()

    # ---------------- Команды ----------------

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🧘 Приветствую, искатель!\n\n"
            "Я — твой Архитектор Судьбы. Я не даю готовых ответов — "
            "я задаю вопросы, которые ведут к твоим собственным инсайтам.\n\n"
            "Пиши мне в личку — отвечу на всё. В группах обращайся ко мне: «наставник».\n\n"
            "Готов начать путь? Опиши ситуацию подробно и что сейчас ощущаешь."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "🧭 <b>Архитектор Судьбы</b> — твой наставник и зеркало.\n\n"
            "<b>Как использовать:</b>\n"
            "• В личных сообщениях я отвечаю на всё, что ты напишешь.\n"
            f"• Я помню последние {self.config.max_history_messages} сообщений "
            "и сохраняю историю между сессиями.\n\n"
            "<b>Команды:</b>\n"
            "/start — начать диалог\n"
            "/help — эта справка\n"
            "/profile — указать дату рождения и психотип\n"
            "/clear_memory — очистить память о разговоре\n\n"
            "<b>Важно:</b> я не даю советов. Я задаю вопросы, которые помогут тебе "
            "самому прийти к ответам. Будь готов к честности."
        )
        await update.message.reply_text(help_text, parse_mode="HTML")

    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Формат: /profile ДД.ММ.ГГГГ психотип"""
        chat_id = update.effective_chat.id
        args = context.args

        if not args or len(args) < 2:
            current = self.user_data.get(chat_id, {})
            await update.message.reply_text(
                "👤 Формат: /profile ДД.ММ.ГГГГ психотип\n"
                "Например: /profile 14.03.1990 интроверт\n\n"
                f"Текущие данные: дата рождения — {current.get('birth_date', 'не указана')}, "
                f"психотип — {current.get('psychotype', 'не указан')}."
            )
            return

        birth_date, psychotype = args[0], " ".join(args[1:])
        self.user_data.setdefault(chat_id, {})
        self.user_data[chat_id]["birth_date"] = birth_date
        self.user_data[chat_id]["psychotype"] = psychotype
        self._save_user_data()

        await update.message.reply_text(
            f"✅ Записал: дата рождения {birth_date}, психотип «{psychotype}».\n"
            "Теперь я буду учитывать это в наших разговорах."
        )

    async def clear_memory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.memory.clear_chat_memory(chat_id)
        await update.message.reply_text(
            "🧹 Память этого чата очищена. Начнём с чистого листа."
        )

    # ---------------- Сообщения ----------------

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главный обработчик текстовых сообщений"""
        try:
            message = update.message
            if not message or not message.text:
                return

            # Канальные посты и другие боты — игнор (защита от бот-циклов)
            user = update.effective_user
            if user is None or user.is_bot:
                return

            chat_id = update.effective_chat.id
            chat_type = message.chat.type
            username = user.username or user.first_name or "Искатель"
            raw_text = message.text

            # 1. Решаем, отвечать ли (по исходному тексту, до чистки)
            should_respond = False
            if chat_type == 'private':
                should_respond = True
                logger.info(f"Private chat {chat_id} from {username}")
            elif chat_type in ('group', 'supergroup'):
                raw_lower = raw_text.lower()
                if self.bot_username and self.bot_username.lower() in raw_lower:
                    should_respond = True
                elif any(kw in raw_lower for kw in GROUP_KEYWORDS):
                    should_respond = True
                elif (message.reply_to_message
                      and message.reply_to_message.from_user
                      and message.reply_to_message.from_user.id == context.bot.id):
                    should_respond = True
                if should_respond:
                    logger.info(f"Group mention in chat {chat_id}")

            # 2. Вырезаем @username бота — чистый промт, экономия токенов
            text = raw_text
            if self.bot_username:
                text = re.sub(
                    rf'@{re.escape(self.bot_username)}\b',
                    '', text, flags=re.IGNORECASE
                ).strip()
                text = re.sub(r' {2,}', ' ', text)

            # Сообщение из одного только @упоминания содержания не несёт
            if not text:
                return

            # 3. Память: текущее сообщение станет последним в истории
            self.memory.add_message(chat_id, {
                'user_id': user.id,
                'username': username,
                'text': text,
                'timestamp': message.date.isoformat(),
                'is_bot': False,
            })

            if not should_respond:
                return

            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # 4. Генерация: история уже содержит текущее сообщение — не дублируем
            response = await self.generate_response(chat_id, chat_type)

            if response:
                sent = None
                for i in range(0, len(response), TELEGRAM_MSG_LIMIT):
                    sent = await message.reply_text(
                        response[i:i + TELEGRAM_MSG_LIMIT]
                    )
                self.memory.add_message(chat_id, {
                    'user_id': context.bot.id,
                    'username': self.bot_username or 'Архитектор',
                    'text': response,
                    'timestamp': (sent.date if sent else message.date).isoformat(),
                    'is_bot': True,
                })
            else:
                await message.reply_text(
                    "⚠️ Не получилось сформировать ответ. Попробуй ещё раз."
                )

        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    "⚠️ Непредвиденная ошибка. Давай попробуем позже."
                )
            except Exception:
                pass

    # ---------------- Генерация ответа ----------------

    async def generate_response(self, chat_id: int, chat_type: str) -> Optional[str]:
        """Сборка сообщений для LLM и вызов клиента"""
        try:
            history = self.memory.get_chat_messages(chat_id)
            # В LLM уходит только окно контекста, а не вся память
            context_history = history[-self.config.max_history_messages:]

            messages = [{
                "role": "system",
                "content": self._build_system_prompt(chat_id),
            }]

            for msg in context_history:
                role = "assistant" if msg['is_bot'] else "user"
                if msg['is_bot']:
                    content = msg['text']  # без префикса — экономия токенов
                elif chat_type in ('group', 'supergroup'):
                    content = f"{msg['username']}: {msg['text']}"  # различаем участников
                else:
                    content = msg['text']  # в личке ролей достаточно
                messages.append({"role": role, "content": content})

            return await self.ai_client.generate_response(messages)

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}", exc_info=True)
            return None

    def _build_system_prompt(self, chat_id: int) -> str:
        """Системный промпт + блок персональных данных, если они есть"""
        prompt = self.config.system_prompt
        user_info = self.user_data.get(chat_id)
        if user_info:
            prompt += (
                "\n\nДанные пользователя:\n"
                f"— Дата рождения: {user_info.get('birth_date', 'неизвестна')}\n"
                f"— Психотип: {user_info.get('psychotype', 'неизвестен')}\n"
                f"— Дайджест прошлых сессий: {user_info.get('session_digest', '—')}"
            )
        return prompt

    # ---------------- Персистентность профилей ----------------

    def _load_user_data(self) -> Dict[int, Dict]:
        path = self.config.user_data_file
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return {int(chat_id): info for chat_id, info in data.items()}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error(f"Не удалось загрузить {path}: {e}")
            return {}

    def _save_user_data(self) -> None:
        path = self.config.user_data_file
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(
                    {str(chat_id): info for chat_id, info in self.user_data.items()},
                    f, ensure_ascii=False,
                )
        except OSError as e:
            logger.error(f"Не удалось сохранить {path}: {e}")

    # ---------------- Инфраструктура ----------------

    async def error_handler(self, update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Исключение: {context.error}")

    async def post_init(self, application: Application):
        bot_info = await application.bot.get_me()
        self.bot_username = bot_info.username
        logger.info(f"Бот запущен: @{self.bot_username}")

    async def post_shutdown(self, application: Application):
        await self.ai_client.close()
        logger.info("AI-клиент закрыт")

    def run(self):
        keep_alive = threading.Thread(target=keep_alive_thread, daemon=True)
        keep_alive.start()
        logger.info("Keep-alive поток запущен")

        application = Application.builder() \
            .token(self.config.telegram_bot_token) \
            .post_init(self.post_init) \
            .post_shutdown(self.post_shutdown) \
            .build()

        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("profile", self.profile_command))
        application.add_handler(CommandHandler("clear_memory", self.clear_memory_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        application.add_error_handler(self.error_handler)

        logger.info("Бот запускается...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    ArchitectBot().run()
