#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot «Проводник Души» (DeepSeek API).

Личные чаты — отвечает на всё; группы — на слово-триггер или ответ.

ПАМЯТЬ: история (50 сообщений) — chat_memory.json; профиль, книга уроков,
фокус недели — user_data.json. Оба переживают рестарт и обновление кода.
Сюцай, личный год, фаза возраста считаются заново из ДР при каждом запросе.

ДУГА СЕССИИ: контакт → диагностика → нарастание → кульминация →
проживание → закрепление. Модель помечает фазу [PHASE: ...], код считает
зависания и подталкивает к кульминации. /session — полная сессия по запросу.

Ленивая персонализация: дата рождения перехватывается из разговора,
остальное модель сохраняет тихими маркерами [SAVE: ...].
КПТ: детектор словесных ловушек в коде; /rebuild N — пересборка убеждения.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from bot_config import (
    BotConfig,
    MORNING_PROMPT,
    EVENING_PROMPT,
    SUNDAY_EVENING_ADD,
    REBUILD_PROMPT,
    SESSION_PROMPT,
)
from calculations import calc_summary, parse_birth_date, build_calc_lines
from openrouter_client import OpenRouterClient
from message_memory import MessageMemory
from keep_alive import (
    keep_alive_thread,
    start_keep_alive_server,
    update_status,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_MSG_LIMIT = 4096
GROUP_KEYWORDS = ("проводник", "наставник", "архитектор")
RU_WEEKDAYS = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')

# Автоперехват даты рождения: ДД.ММ.ГГГГ (разделители . - /)
DATE_RE = re.compile(r'\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b')
# Тихие маркеры модели: [SAVE: к=в] [LESSON: т] [FOCUS: т] [PHASE: ф]
MARKER_RE = re.compile(r'\[\s*(SAVE|LESSON|FOCUS|PHASE)\s*:\s*([^\]]+)\]')

SESSION_PHASES = ('контакт', 'диагностика', 'нарастание',
                  'кульминация', 'проживание', 'закрепление')

PROFILE_FIELDS = {
    'birth': 'birth_date', 'др': 'birth_date', 'дата': 'birth_date',
    'пол': 'gender', 'gender': 'gender',
    'psychotype': 'psychotype', 'психотип': 'psychotype',
    'hd': 'hd', 'дизайн': 'hd',
    'хронотип': 'chronotype', 'chronotype': 'chronotype',
    'соляр': 'solar', 'solar': 'solar',
    'кнопка': 'red_button', 'red_button': 'red_button',
    'запрос': 'main_request', 'main_request': 'main_request',
}

PROFILE_LABELS = {
    'name': 'Имя',
    'birth_date': 'Дата рождения',
    'gender': 'Пол',
    'psychotype': 'Психотип',
    'hd': 'Дизайн Человека',
    'chronotype': 'Хронотип',
    'solar': 'Соляр (факт)',
    'red_button': '«Красная кнопка»',
    'main_request': 'Главный запрос',
}

ASKABLE = ('psychotype', 'hd', 'gender', 'chronotype',
           'solar', 'red_button', 'main_request')

# --- Детектор словесных ловушек: КПТ-линзы в коде, ноль токенов в промте ---
COGNITIVE_PATTERNS = [
    (re.compile(r'\b(всегда|никогда|постоянно|вечно)\b', re.IGNORECASE),
     'обобщение «всегда/никогда» — спроси про исключение'),
    (re.compile(r'\b(должен|должна|обязан|обязана|надо же)\b', re.IGNORECASE),
     'долженствование — спроси, кто и когда это назначил'),
    (re.compile(r'\b(все люди|никто|все вокруг|всем|ничего не выйдет|'
                r'всё бесполезно|конец)\b', re.IGNORECASE),
     'чёрно-белое/катастрофизация — предложи шкалу 0–10'),
    (re.compile(r'\b(наверное|а вдруг|а если|вдруг)\b', re.IGNORECASE),
     'катастрофизация «а вдруг» — спроси, какова реальная вероятность'),
    (re.compile(r'\b(обычно|типичный|привычка|как всегда|по привычке)\b',
                re.IGNORECASE),
     'автопилот — спроси, что он выбирает сам, а что «так принято»'),
    (re.compile(r'\b(я такой|я не тот|у меня характер|такой как я)\b',
                re.IGNORECASE),
     'ярлык на себя — спроси, когда он не был «таким»'),
]


def detect_patterns(text: str, limit: int = 2) -> List[str]:
    """Максимум limit подсказок, чтобы не заваливать модель."""
    found = []
    for rx, hint in COGNITIVE_PATTERNS:
        if rx.search(text):
            found.append(hint)
            if len(found) >= limit:
                break
    return found


class SoulGuideBot:
    def __init__(self):
        self.config = BotConfig()

        self.ai_client = OpenRouterClient(
            api_key=self.config.openrouter_api_key,
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            timeout=self.config.request_timeout,
        )

        self.memory = MessageMemory(
            max_messages_per_chat=self.config.memory_messages,
            persist_path=self.config.memory_file,
        )
        self.bot_username = None
        self.user_data = self._load_user_data()
        # Фаза сессии (анти-зацикливание): {'phase': ..., 'same': N}.
        # Живёт в памяти процесса: после рестарта — новая дуга. Намеренно.
        self.session_state: Dict[int, Dict] = {}

    # ---------------- Команды ----------------

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🕯 Привет. Я — Проводник Души.\n\n"
            "Веду через уроки жизни: подсвечиваю ложные убеждения, показываю "
            "зеркала, помогаю малыми шагами выйти из застоя. Готовых решений "
            "не даю — ответы находишь ты, я задаю точные вопросы.\n\n"
            "Просто расскажи, что сейчас происходит — с этого и начнём.\n\n"
            "По желанию: /session тема — целая сессия · /morning — карта дня · "
            "/help — всё о моей работе"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "🕯 <b>Проводник Души</b> — наставник, зеркало, ежедневный навигатор.\n\n"
            "<b>Сессия:</b>\n"
            "/session тема — полная сессия по запросу: от пары точных вопросов "
            "до момента истины\n\n"
            "<b>Ритм:</b>\n"
            "/morning — карта дня: тема, зеркало, одно действие\n"
            "/evening — разбор дня: три вопроса (в воскресенье — и фокус недели)\n\n"
            "<b>Книга уроков:</b>\n"
            "/lessons — найденные убеждения и стратегии\n"
            "/rebuild N — пошаговая пересборка убеждения (урока N)\n"
            "/done N — пометить урок проработанным\n"
            "/focus N или /focus тема — фокус недели\n"
            "/lesson текст — добавить урок вручную\n\n"
            "<b>Данные:</b>\n"
            "/set ключ значение — birth, пол, psychotype, hd, соляр, хронотип, "
            "кнопка, запрос\n"
            "/profile — всё, что я о тебе знаю\n"
            "Дату рождения можно просто написать в разговоре — подхвачу сам.\n\n"
            "<b>Память:</b>\n"
            f"/clear_memory — с чистого листа (вижу {self.config.max_history_messages} "
            "последних сообщений; история, профиль и уроки переживают перезапуск)\n\n"
            "<b>В группах</b> зови меня словом «проводник».\n\n"
            "Я не даю советов и не ставлю диагнозов — только вопросы, "
            "честные наблюдения и маленькие действия."
        )
        await update.message.reply_text(help_text, parse_mode="HTML")

    async def set_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/set ключ значение — ручное заполнение (ускорение ленивого сбора)"""
        chat_id = update.effective_chat.id
        args = context.args

        if not args or len(args) < 2:
            await update.message.reply_text(
                "📝 Заполнение вручную: /set ключ значение\n\n"
                "/set birth 14.03.1990 — дата рождения\n"
                "/set пол м\n"
                "/set psychotype Аналитик-интроверт\n"
                "/set hd Проектор 2/4, эмоциональный авторитет\n"
                "/set соляр 8-й год, тема расширения\n"
                "/set хронотип сова\n"
                "/set кнопка деньги — панический стыд\n"
                "/set запрос выйти из застоя\n\n"
                "Многое из этого я соберу сам в разговоре. Просмотр: /profile"
            )
            return

        key = args[0].lower()
        value = " ".join(args[1:]).strip()
        field = PROFILE_FIELDS.get(key)
        if not field:
            await update.message.reply_text(
                "⚠️ Не знаю такого поля. Доступно: birth, пол, psychotype, hd, "
                "соляр, хронотип, кнопка, запрос. Пример: /set birth 14.03.1990"
            )
            return

        if field == 'birth_date' and not parse_birth_date(value):
            await update.message.reply_text(
                "⚠️ Не понял дату. Формат: ДД.ММ.ГГГГ, например 14.03.1990"
            )
            return

        self.user_data.setdefault(chat_id, {})[field] = value
        self._save_user_data()

        extra = ""
        if field == 'birth_date':
            s = calc_summary(value)
            if s:
                extra = (f"\n🔢 Посчитал: возраст {s['age']}, "
                         f"личный год {s['personal_year']}, "
                         f"число пути {s['path']}.")
        await update.message.reply_text(
            f"✅ Записал: {PROFILE_LABELS[field]} — «{value}».{extra}"
        )

    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        info = self.user_data.get(chat_id) or {}

        if not info:
            await update.message.reply_text(
                "📋 Пока я о тебе почти ничего не знаю — и это нормально: "
                "данные всплывают в разговоре. Ускорить: /set birth 14.03.1990"
            )
            return

        lines = ["📋 Всё, что я о тебе знаю:"]
        for field in ('name', 'birth_date', 'gender', 'psychotype', 'hd',
                      'chronotype', 'solar', 'red_button', 'main_request'):
            if info.get(field):
                lines.append(f"• {PROFILE_LABELS[field]}: {info[field]}")

        if info.get('birth_date'):
            lines.append("\n🔢 Расчёт (точный):")
            for line in build_calc_lines(info['birth_date']):
                lines.append(f"• {line}")

        lessons = info.get('lessons') or []
        if lessons:
            lines.append(f"\n📖 Уроков в книге: {len(lessons)} (список: /lessons)")
        focus = info.get('focus_week') or {}
        if focus.get('theme'):
            lines.append(f"🎯 Фокус недели: «{focus['theme']}»")

        lines.append("\n(Сюцай и нумерология — рамки для размышления, не факты.)")
        await update.message.reply_text("\n".join(lines))

    async def lessons_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        info = self.user_data.get(chat_id) or {}
        lessons = info.get('lessons') or []
        focus = info.get('focus_week') or {}

        if not lessons:
            await update.message.reply_text(
                "📖 Книга уроков пуста.\n\n"
                "Уроки появляются, когда в разговоре вскрывается ложное "
                "убеждение или неработающая стратегия и ты подтверждаешь: "
                "«да, это про меня». Начни: /session или просто расскажи, "
                "что сейчас происходит."
            )
            return

        lines = ["📖 Книга уроков:"]
        for l in lessons:
            mark = '✓' if l.get('status') == 'проработан' else '•'
            lines.append(f"{mark} {l['id']}. «{l['title']}» — {l['status']}"
                         f" (с {l.get('created', '?')})")
        if focus.get('theme'):
            lines.append(f"\n🎯 Фокус недели: «{focus['theme']}»")
        lines.append("\n/rebuild N — пересборка · /done N — проработан · "
                     "/focus N — фокус недели")
        await update.message.reply_text("\n".join(lines))

    async def lesson_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/lesson текст — вручную добавить урок (страховка от маркеров)"""
        chat_id = update.effective_chat.id
        title = " ".join(context.args).strip() if context.args else ""
        if not title:
            await update.message.reply_text(
                "Формат: /lesson короткое название убеждения.\n"
                "Например: /lesson Просить о помощи = быть слабым"
            )
            return
        self._add_lesson(chat_id, title)
        await update.message.reply_text(f"📖 В книгу уроков: «{title}»")

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        lessons = (self.user_data.get(chat_id) or {}).get('lessons') or []
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Формат: /done N — пометить урок N проработанным. Список: /lessons"
            )
            return
        n = int(context.args[0])
        for l in lessons:
            if l['id'] == n:
                l['status'] = 'проработан'
                l['done_at'] = datetime.now().strftime('%d.%m')
                self._save_user_data()
                await update.message.reply_text(
                    f"✅ Урок «{l['title']}» — проработан. Снимаю шляпу."
                )
                return
        await update.message.reply_text(f"Урока с номером {n} нет. Список: /lessons")

    async def focus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args

        if not args:
            active = [l for l in (self.user_data.get(chat_id) or {}).get('lessons', [])
                      if l.get('status') == 'в работе']
            if active:
                listing = "\n".join(f"{l['id']}. «{l['title']}»" for l in active)
                await update.message.reply_text(
                    f"🎯 Уроки в работе:\n{listing}\n\n"
                    "/focus N — сделать фокусом недели\n"
                    "/focus своя тема — задать тему словами"
                )
            else:
                await update.message.reply_text(
                    "🎯 Формат: /focus N (номер урока из /lessons) "
                    "или /focus своя тема недели."
                )
            return

        if args[0].isdigit():
            n = int(args[0])
            for l in (self.user_data.get(chat_id) or {}).get('lessons') or []:
                if l['id'] == n:
                    self._set_focus(chat_id, l['title'])
                    await update.message.reply_text(
                        f"🎯 Фокус недели: «{l['title']}»"
                    )
                    return
            await update.message.reply_text(f"Урока {n} нет. Список: /lessons")
            return

        theme = " ".join(args)
        self._set_focus(chat_id, theme)
        await update.message.reply_text(f"🎯 Фокус недели: «{theme}»")

    async def rebuild_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rebuild N — пошаговая пересборка убеждения (урока N) по КПТ-цепочке"""
        chat_id = update.effective_chat.id
        lessons = (self.user_data.get(chat_id) or {}).get('lessons') or []
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Формат: /rebuild N — пересборка урока N. Список: /lessons"
            )
            return
        n = int(context.args[0])
        lesson = next((l for l in lessons if l['id'] == n), None)
        if not lesson:
            await update.message.reply_text(f"Урока {n} нет. Список: /lessons")
            return

        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': f'🔧 Пересборка урока {n}: «{lesson["title"]}»',
            'timestamp': update.message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self.generate_response(
            chat_id, update.message.chat.type, mode='rebuild'
        )
        await self._process_and_reply(update.message, chat_id, response)

    async def clear_memory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.memory.clear_chat_memory(chat_id)
        self.session_state.pop(chat_id, None)
        await update.message.reply_text(
            "🧹 Память разговора очищена. Книга уроков и профиль остались. "
            "Начнём с чистого листа."
        )

    # ---------------- Ритуалы и сессия ----------------

    async def morning_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")

        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': '☀️ Утренний ритуал',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self.generate_response(chat_id, message.chat.type,
                                                mode='morning')
        await self._process_and_reply(message, chat_id, response)

    async def evening_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")

        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': '🌙 Вечерний ритual',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self.generate_response(chat_id, message.chat.type,
                                                mode='evening')
        await self._process_and_reply(message, chat_id, response)

    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🎧 /session [тема] — полная сессия по запросу"""
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        topic = " ".join(context.args).strip() if context.args else ""

        self.session_state.pop(chat_id, None)  # свежая дуга

        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': f'🎧 Сессия: {topic}' if topic else '🎧 Сессия',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self.generate_response(chat_id, message.chat.type,
                                                mode='session')
        await self._process_and_reply(message, chat_id, response)

    # ---------------- Сообщения ----------------

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            message = update.message
            if not message or not message.text:
                return

            user = update.effective_user
            if user is None or user.is_bot:
                return

            update_status(last_update=time.time())

            chat_id = update.effective_chat.id
            chat_type = message.chat.type
            username = user.username or user.first_name or "Искатель"
            raw_text = message.text

            # 0. Имя — тихо из Telegram (только личка)
            if chat_type == 'private':
                profile = self.user_data.setdefault(chat_id, {})
                if user.first_name and profile.get('name') != user.first_name:
                    profile['name'] = user.first_name
                    self._save_user_data()

            # 1. Отвечать ли (по исходному тексту, до чистки)
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

            # 2. Вырезаем @username бота — чистый промт
            text = raw_text
            if self.bot_username:
                text = re.sub(
                    rf'@{re.escape(self.bot_username)}\b',
                    '', text, flags=re.IGNORECASE
                ).strip()
                text = re.sub(r' {2,}', ' ', text)

            if not text:
                return

            # 3. Автоперехват даты рождения (личка, ещё не известна)
            self._try_capture_birth(chat_id, text, chat_type)

            # 4. Память: чистый текст (подсказка паттерна туда не попадает)
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

            # 5. Детектор ловушек: подсказка модели в последний user-текст
            pattern_hint = detect_patterns(text)
            if pattern_hint:
                text_for_model = f"{text}\n[Паттерн: {'; '.join(pattern_hint)}]"
            else:
                text_for_model = text

            # 6. Генерация + разбор маркеров + ответ
            response = await self.generate_response(
                chat_id, chat_type, user_text=text_for_model
            )
            await self._process_and_reply(message, chat_id, response)

        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    "⚠️ Непредвиденная ошибка. Давай попробуем позже."
                )
            except Exception:
                pass

    # ---------------- Ленивый сбор данных ----------------

    def _try_capture_birth(self, chat_id: int, text: str, chat_type: str) -> bool:
        """Тихо ловит ДД.ММ.ГГГГ в личке, если ДР ещё не известен.
        Защита: только личка, только впервые, валидная дата,
        год в прошлом, возраст >= 5."""
        if chat_type != 'private':
            return False
        info = self.user_data.get(chat_id)
        if info and info.get('birth_date'):
            return False

        m = DATE_RE.search(text)
        if not m:
            return False

        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            birth = datetime(y, mo, d).date()
        except ValueError:
            return False

        today = datetime.now().date()
        if not (1920 <= y <= today.year):
            return False
        age = today.year - birth.year - (
            (today.month, today.day) < (birth.month, birth.day))
        if age < 5:
            return False

        self.user_data.setdefault(chat_id, {})['birth_date'] = \
            birth.strftime('%d.%m.%Y')
        self._save_user_data()
        logger.info(f"Chat {chat_id}: дата рождения перехвачена ({birth})")
        return True

    def _extract_markers(self, text: str, chat_id: int) -> Tuple[str, List[str]]:
        """Вырезает [SAVE/LESSON/FOCUS/PHASE]-маркеры, сохраняет данные.
        Возвращает чистый текст и строки подтверждения пользователю."""
        confirms: List[str] = []

        for m in MARKER_RE.finditer(text):
            kind, body = m.group(1), m.group(2).strip()

            if kind == 'SAVE':
                if '=' not in body:
                    continue
                key, value = body.split('=', 1)
                field = PROFILE_FIELDS.get(key.strip().lower())
                value = value.strip()
                if not field or not value:
                    continue
                self.user_data.setdefault(chat_id, {})[field] = value
                self._save_user_data()
                confirms.append(
                    f"📝 Записал: {PROFILE_LABELS.get(field, field)} — «{value}»")

            elif kind == 'LESSON':
                title = body.strip(' «»"\'')
                if title:
                    self._add_lesson(chat_id, title)
                    confirms.append(f"📖 В книгу уроков: «{title}»")

            elif kind == 'FOCUS':
                theme = body.strip(' «»"\'')
                if theme:
                    self._set_focus(chat_id, theme)
                    confirms.append(f"🎯 Фокус недели: «{theme}»")

            elif kind == 'PHASE':
                # Служебный маркер: пользователю не показываем
                phase = body.strip().lower()
                if phase in SESSION_PHASES:
                    self._update_phase(chat_id, phase)

        clean = MARKER_RE.sub('', text)
        clean = re.sub(r'\n{3,}', '\n\n', clean).rstrip()
        return clean, confirms

    def _add_lesson(self, chat_id: int, title: str) -> None:
        lessons = self.user_data.setdefault(chat_id, {}).setdefault('lessons', [])
        if any(l['title'].lower() == title.lower() for l in lessons):
            return
        lessons.append({
            'id': (lessons[-1]['id'] + 1) if lessons else 1,
            'title': title,
            'status': 'в работе',
            'created': datetime.now().strftime('%d.%m'),
        })
        self._save_user_data()

    def _update_phase(self, chat_id: int, phase: str) -> None:
        """Считаем зависания: сколько реплик подряд в одной фазе."""
        st = self.session_state.get(chat_id)
        if st and st['phase'] == phase:
            st['same'] += 1
        else:
            st = {'phase': phase, 'same': 1}
        self.session_state[chat_id] = st

    def _set_focus(self, chat_id: int, theme: str) -> None:
        self.user_data.setdefault(chat_id, {})['focus_week'] = {
            'theme': theme,
            'set_at': datetime.now().strftime('%d.%m'),
        }
        self._save_user_data()

    # ---------------- Генерация ----------------

    async def generate_response(self, chat_id: int, chat_type: str,
                                mode: Optional[str] = None,
                                user_text: Optional[str] = None) -> Optional[str]:
        try:
            history = self.memory.get_chat_messages(chat_id)
            context_history = history[-self.config.max_history_messages:]

            # Последнее user-сообщение — с подсказкой о паттернах (если есть);
            # остальная история — как есть
            if user_text and context_history and not context_history[-1]['is_bot']:
                context_history = context_history[:-1] + [
                    {**context_history[-1], 'text': user_text}
                ]

            messages = [{
                "role": "system",
                "content": self._build_system_prompt(chat_id, mode),
            }]

            for msg in context_history:
                role = "assistant" if msg['is_bot'] else "user"
                if msg['is_bot']:
                    content = msg['text']
                elif chat_type in ('group', 'supergroup'):
                    content = f"{msg['username']}: {msg['text']}"
                else:
                    content = msg['text']
                messages.append({"role": role, "content": content})

            return await self.ai_client.generate_response(messages)

        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}", exc_info=True)
            return None

    def _build_system_prompt(self, chat_id: int, mode: Optional[str] = None) -> str:
        info = self.user_data.get(chat_id) or {}
        blocks = [self.config.system_prompt]

        data = ["", "=== ДАННЫЕ О ПОЛЬЗОВАТЕЛЕ (факты, не выдумывай) ==="]
        for field in ('name', 'birth_date', 'gender', 'psychotype', 'hd',
                      'chronotype', 'solar', 'red_button', 'main_request'):
            if info.get(field):
                data.append(f"— {PROFILE_LABELS[field]}: {info[field]}")

        if info.get('birth_date'):
            data.append("— РАСЧЁТ (посчитан точно, тебе считать не нужно):")
            for line in build_calc_lines(info['birth_date']):
                data.append(f"  {line}")

        lessons = info.get('lessons') or []
        active = [l for l in lessons if l.get('status') == 'в работе'][:8]
        finished = [l for l in lessons if l.get('status') == 'проработан'][-3:]
        if active or finished:
            data.append("— КНИГА УРОКОВ:")
            for l in active:
                data.append(
                    f"  {l['id']}. [в работе] «{l['title']}» "
                    f"(с {l.get('created', '?')})")
            for l in finished:
                data.append(f"  {l['id']}. [проработан] «{l['title']}»")

        focus = info.get('focus_week') or {}
        if focus.get('theme'):
            data.append(
                f"— Фокус недели (задан {focus.get('set_at', '?')}): "
                f"«{focus['theme']}»")
        else:
            data.append("— Фокус недели: не задан")

        # Фаза сессии + детектор зависания (анти-зацикливание в коде)
        st = self.session_state.get(chat_id)
        if st:
            phase_line = f"— Фаза сессии: {st['phase']}"
            if st['same'] >= 2 and st['phase'] == 'контакт':
                phase_line += " (зависание: переходи к диагностике)"
            elif st['same'] >= 3 and st['phase'] in ('диагностика', 'нарастание'):
                phase_line += (" (зависание: материала достаточно — "
                               "к кульминации, без новых вопросов)")
            elif st['same'] >= 2 and st['phase'] == 'проживание':
                phase_line += " (пора к закреплению)"
            data.append(phase_line)
        else:
            data.append("— Фаза сессии: не отслежена (начни с контакта)")

        missing = [PROFILE_LABELS[f] for f in ASKABLE if not info.get(f)]
        if missing:
            data.append(f"— Ещё не знаю: {', '.join(missing)} "
                        "(спрашивай по одному и только когда нужно)")

        now = datetime.now()
        data.append(f"— Сегодня: {now.strftime('%d.%m.%Y')} "
                    f"({RU_WEEKDAYS[now.weekday()]})")
        blocks.append("\n".join(data))

        if mode == 'morning':
            blocks.append(MORNING_PROMPT)
        elif mode == 'evening':
            blocks.append(EVENING_PROMPT)
            if now.weekday() == 6:  # воскресенье
                blocks.append(SUNDAY_EVENING_ADD)
        elif mode == 'rebuild':
            blocks.append(REBUILD_PROMPT)
        elif mode == 'session':
            blocks.append(SESSION_PROMPT)

        return "\n".join(blocks)

    async def _process_and_reply(self, message, chat_id: int,
                                 response: Optional[str]) -> None:
        """Маркеры → сохранение; чистый текст → пользователю → в память."""
        if not response:
            await message.reply_text(
                "⚠️ Не получилось сформировать ответ. Попробуй ещё раз."
            )
            return

        clean, confirms = self._extract_markers(response, chat_id)
        if not clean:
            if confirms:
                clean = "Записал."
            else:
                await message.reply_text("⚠️ Пустой ответ. Попробуй ещё раз.")
                return

        out = clean if not confirms else clean + "\n\n" + "\n".join(confirms)

        sent = None
        for i in range(0, len(out), TELEGRAM_MSG_LIMIT):
            sent = await message.reply_text(out[i:i + TELEGRAM_MSG_LIMIT])

        # В память — только чистый текст (маркеры не возвращаются в историю)
        self.memory.add_message(chat_id, {
            'user_id': 0,
            'username': self.bot_username or 'Проводник',
            'text': clean,
            'timestamp': (sent.date if sent else message.date).isoformat(),
            'is_bot': True,
        })

    # ---------------- Персистентность ----------------

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
                    {str(chat_id): info
                     for chat_id, info in self.user_data.items()},
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
        start_keep_alive_server()

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
        application.add_handler(CommandHandler("session", self.session_command))
        application.add_handler(CommandHandler("morning", self.morning_command))
        application.add_handler(CommandHandler("evening", self.evening_command))
        application.add_handler(CommandHandler("set", self.set_command))
        application.add_handler(CommandHandler("profile", self.profile_command))
        application.add_handler(CommandHandler("lessons", self.lessons_command))
        application.add_handler(CommandHandler("lesson", self.lesson_command))
        application.add_handler(CommandHandler("done", self.done_command))
        application.add_handler(CommandHandler("focus", self.focus_command))
        application.add_handler(CommandHandler("rebuild", self.rebuild_command))
        application.add_handler(CommandHandler("clear_memory",
                                               self.clear_memory_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        application.add_error_handler(self.error_handler)

        logger.info("Бот запускается...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    SoulGuideBot().run()
