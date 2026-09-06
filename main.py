#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot "Архитектор Судьбы" with DeepSeek API
Отвечает на все сообщения в личных чатах, в группах — только на упоминание.
"""

import logging
import asyncio
import threading
from datetime import datetime
from typing import Dict, List

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from bot_config import BotConfig
from openrouter_client import OpenRouterClient
from message_memory import MessageMemory
from keep_alive import keep_alive_thread

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class ArchitectBot:
    def __init__(self):
        self.config = BotConfig()
        self.ai_client = OpenRouterClient(self.config.openrouter_api_key)
        self.memory = MessageMemory(max_messages=self.config.max_history_messages)
        self.bot_username = None
        # В будущем здесь можно хранить персональные данные пользователей
        self.user_data: Dict[int, Dict] = {}  # chat_id -> {birth_date, psychotype, ...}

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик /start"""
        await update.message.reply_text(
            "🧘 Приветствую, искатель!\n\n"
            "Я — твой Архитектор Судьбы. Я не даю готовых ответов, я задаю вопросы, которые ведут к твоим собственным инсайтам.\n\n"
            "Просто пиши мне в личку — я отвечу на всё. В группах обращайся ко мне по имени (упомяни бота).\n\n"
            "Готов начать путь? Отправь мне своё первое сообщение."
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик /help"""
        help_text = """
🧭 **Архитектор Судьбы** — твой наставник и зеркало.

**Как использовать:**
• В **личных сообщениях** я отвечаю на всё, что ты напишешь.
• В **групповых чатах** — только если ты упомянешь меня (или напишешь "Саныч").
• Я помню последние 20 сообщений в каждом чате, чтобы видеть контекст.

**Команды:**
/start — начать диалог
/help — эта справка
/clear_memory — очистить мою память о нашем разговоре

**Важно:** Я не даю советов. Я задаю вопросы, которые помогут тебе самому прийти к ответам. Будь готов к честности.
"""
        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def clear_memory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Очистка памяти чата"""
        chat_id = update.effective_chat.id
        self.memory.clear_chat_memory(chat_id)
        await update.message.reply_text("🧹 Память этого чата очищена. Начнём с чистого листа.")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Главный обработчик сообщений"""
        try:
            message = update.message
            if not message or not message.text:
                return

            chat_id = update.effective_chat.id
            user_id = update.effective_user.id
            username = update.effective_user.username or update.effective_user.first_name
            text = message.text

            # Сохраняем сообщение пользователя в память
            self.memory.add_message(chat_id, {
                'user_id': user_id,
                'username': username,
                'text': text,
                'timestamp': message.date.isoformat(),
                'is_bot': False
            })

            # Определяем, нужно ли отвечать
            should_respond = False

            # Если чат личный — отвечаем всегда
            if message.chat.type == 'private':
                should_respond = True
                logger.info(f"Private chat {chat_id} from {username}")

            # Если группа — только на упоминание или ответ на сообщение бота
            elif message.chat.type in ['group', 'supergroup']:
                # Проверка упоминания (имя бота или "Саныч")
                if self.bot_username and (self.bot_username in text or 'саныч' in text.lower()):
                    should_respond = True
                    logger.info(f"Group mention in chat {chat_id}")
                # Проверка ответа на сообщение бота
                elif message.reply_to_message and message.reply_to_message.from_user.id == context.bot.id:
                    should_respond = True
                    logger.info(f"Reply to bot in group {chat_id}")

            if not should_respond:
                return

            # Показываем индикатор набора текста
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            # Генерируем ответ
            response = await self.generate_response(chat_id, text, username)

            if response:
                sent = await message.reply_text(response)
                # Сохраняем ответ бота в память
                self.memory.add_message(chat_id, {
                    'user_id': context.bot.id,
                    'username': self.bot_username or 'Архитектор',
                    'text': response,
                    'timestamp': sent.date.isoformat(),
                    'is_bot': True
                })
            else:
                await message.reply_text("⚠️ Произошла ошибка при генерации ответа. Попробуй ещё раз.")

        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}")
            try:
                await update.message.reply_text("⚠️ Непредвиденная ошибка. Давай попробуем позже.")
            except:
                pass

    async def generate_response(self, chat_id: int, user_message: str, username: str) -> str:
        """Генерация ответа через DeepSeek с системным промптом и контекстом"""
        try:
            # Собираем историю чата (последние N сообщений)
            history = self.memory.get_chat_messages(chat_id)
            # Берём последние self.config.max_history_messages для контекста
            context_history = history[-self.config.max_history_messages:]

            # Формируем список сообщений для API
            messages = []

            # Системный промпт (основная инструкция)
            system_prompt = self.config.system_prompt
            # Можно добавить информацию о пользователе, если она есть
            user_info = self.user_data.get(chat_id)
            if user_info:
                system_prompt += f"\n\nИнформация о пользователе: дата рождения {user_info.get('birth_date', 'неизвестна')}, психотип {user_info.get('psychotype', 'неизвестен')}."
            messages.append({"role": "system", "content": system_prompt})

            # Добавляем историю
            for msg in context_history:
                role = "assistant" if msg['is_bot'] else "user"
                # Для сообщений от бота используем имя "Архитектор"
                name = "Архитектор" if msg['is_bot'] else msg['username']
                messages.append({
                    "role": role,
                    "content": f"{name}: {msg['text']}"
                })

            # Текущее сообщение пользователя
            messages.append({"role": "user", "content": f"{username}: {user_message}"})

            # Отправляем запрос к DeepSeek
            response = await self.ai_client.generate_response(messages)
            return response

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            return None

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Глобальный обработчик ошибок"""
        logger.error(f"Исключение: {context.error}")

    async def post_init(self, application: Application):
        """Выполняется после инициализации бота"""
        bot_info = await application.bot.get_me()
        self.bot_username = bot_info.username
        logger.info(f"Бот запущен: @{self.bot_username}")

    def run(self):
        """Запуск бота"""
        # Запускаем поток для поддержания активности (если нужно)
        keep_alive = threading.Thread(target=keep_alive_thread, daemon=True)
        keep_alive.start()
        logger.info("Keep-alive поток запущен")

        # Создаём приложение
        application = Application.builder() \
            .token(self.config.telegram_bot_token) \
            .post_init(self.post_init) \
            .build()

        # Регистрируем команды
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("clear_memory", self.clear_memory_command))

        # Обработчик текстовых сообщений (не команд)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        # Обработчик ошибок
        application.add_error_handler(self.error_handler)

        # Запускаем поллинг
        logger.info("Бот запускается...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    bot = ArchitectBot()
    bot.run()
