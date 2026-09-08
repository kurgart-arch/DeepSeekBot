#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot «Проводник Души» (DeepSeek API). Версия 9.

ДУГА: контакт → диагностика → нарастание → кульминация → проживание →
закрепление. Анти-зацикливание: счётчик вопросов (императивы = 0.5),
предохранитель с 4-го обмена, вариация структуры (в промте).
ФИЗИОЛОГИЯ: время суток, энергия (/set энергия), цикл (opt-in),
темп года, хронотип — рамки в блоке данных.
ЦЕННОСТИ: [VALUE], /values, сверка шагов. ОПОРА: /foundation, [PILLAR].
РЕВИЗОР: /scan (scanning.py — каталоги 22+17). ПОБЕДЫ: [WIN] + качество.
"""

import asyncio
import json
import logging
import random
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
    FOUNDATION_PROMPT,
    REENTRY_PROMPT,
)
from calculations import calc_summary, parse_birth_date, build_calc_lines
from scanning import build_scanner_prompt, PILLARS
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

DATE_RE = re.compile(r'\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b')
MARKER_RE = re.compile(
    r'\[\s*(SAVE|LESSON|FOCUS|PHASE|ANCHOR|CARE|WIN|VALUE|BUG|PILLAR)'
    r'\s*:\s*([^\]]+)\]')

SESSION_PHASES = ('контакт', 'диагностика', 'нарастание',
                  'кульминация', 'проживание', 'закрепление')

PHASE_FIXES = {
    'диагностике': 'диагностика', 'диагностику': 'диагностика',
    'диагностека': 'диагностика', 'диагнотсика': 'диагностика',
    'кумуляция': 'кульминация', 'кульминаци': 'кульминация',
    'культминация': 'кульминация', 'кульминации': 'кульминация',
    'контакту': 'контакт', 'контакте': 'контакт',
    'нарастания': 'нарастание', 'проживания': 'проживание',
    'закрепления': 'закрепление', 'закрипление': 'закрепление',
}

PROFILE_FIELDS = {
    'birth': 'birth_date', 'др': 'birth_date', 'дата': 'birth_date',
    'пол': 'gender', 'gender': 'gender',
    'psychotype': 'psychotype', 'психотип': 'psychotype',
    'hd': 'hd', 'дизайн': 'hd',
    'хронотип': 'chronotype', 'chronotype': 'chronotype',
    'соляр': 'solar', 'solar': 'solar',
    'кнопка': 'red_button', 'red_button': 'red_button',
    'запрос': 'main_request', 'main_request': 'main_request',
    'ценность': 'values', 'энергия': 'energy',
    'цикл': 'cycle_start', 'цикл_длина': 'cycle_length',
}

PROFILE_LABELS = {
    'name': 'Имя', 'birth_date': 'Дата рождения', 'gender': 'Пол',
    'psychotype': 'Психотип', 'hd': 'Дизайн Человека',
    'chronotype': 'Хронотип', 'solar': 'Соляр (факт)',
    'red_button': '«Красная кнопка»', 'main_request': 'Главный запрос',
    'energy': 'Энергия', 'cycle_start': 'Цикл (первый день)',
    'cycle_length': 'Длина цикла',
}

ASKABLE = ('psychotype', 'hd', 'gender', 'chronotype',
           'solar', 'red_button', 'main_request')

# --- Детектор ловушек: (regex, подсказка, имя бага для счётчика) ---
COGNITIVE_PATTERNS = [
    (re.compile(r'\b(всегда|никогда|постоянно|вечно)\b', re.IGNORECASE),
     'обобщение — зеркало: «в мире 8 млрд, но НИКТО/ВСЕГДА?» попроси исключение',
     'сверхобобщение'),
    (re.compile(r'\b(должен|должна|обязан|обязана)\b', re.IGNORECASE),
     'долженствование — спроси, кто и когда это назначил',
     'долженствование'),
    (re.compile(r'\b(все люди|никто|всё бесполезно|ничего не выйдет|конец)\b',
                re.IGNORECASE),
     'чёрно-белое — предложи шкалу 0–10', 'чёрно-белое'),
    (re.compile(r'\b(а вдруг|наверное же|точно провал\w*)\b', re.IGNORECASE),
     'катастрофизация — какова реальная вероятность 0–100',
     'катастрофизация'),
    (re.compile(r'\b(он(а|и)? думает|они считают|все знают, что)\b',
                re.IGNORECASE),
     'чтение мыслей — спросил или додумал? где доказательство',
     'чтение мыслей'),
    (re.compile(r'\b(я такой|я не тот|такой как я|неудачник)\b',
                re.IGNORECASE),
     'ярлык — когда он не был «таким»', 'ярлык на себя'),
    (re.compile(r'\b(не дано|не способен|поздно менять|не тот человек)\b',
                re.IGNORECASE),
     'фиксированное мышление — «не выходит = пока не научился; чему?»',
     'фиксированное мышление'),
    (re.compile(r'\b(нечем похвастаться|ничего не добил\w*|пора бы уже)\b',
                re.IGNORECASE),
     'условная самоценность — отзеркалил бы другу за то же?',
     'условная самоценность'),
]

CARE_TRIGGERS = re.compile(
    r'\b(плачу|плакал|плакала|рыдаю|разревелся|разревелась|слёзы|слезы|'
    r'дрожь|трясёт|трясет|ком\s+в\s+горле|больно|невмоготу|накрывает|'
    r'трудно\s+дышать|не\s+могу\s+дышать|паник\w*)\b', re.IGNORECASE)

CRISIS_TRIGGERS = re.compile(
    r'\b(не\s+вижу\s+смысла\s+жить|не\s+хочу\s+жить|хочу\s+умереть|'
    r'покончить|самоубий\w*|убить\s+себя|себя\s+убить|'
    r'сделать\s+с\s+собой|резать\s+себя)\b', re.IGNORECASE)

TOPIC_SWITCH = re.compile(
    r'\b(друг\w*\s+тема|друг\w*\s+вопрос|нов\w*\s+тема|нов\w*\s+вопрос|'
    r'смен\w*\s+тему|ладно[,\s]+по\b|верн\w*\s+к\b|кстати)\b',
    re.IGNORECASE)

# Императивы = «полвопроса» для счётчика (анти-обход лимита)
IMPERATIVE_RE = re.compile(
    r'\b(попробуй|выполни|сделай сейчас|попрактикуй|выполняй)\b',
    re.IGNORECASE)

# --- Физиология: словари ---
YEAR_TEMPO = {
    1: 'старт — новое начинать можно', 2: 'терпение и связи — темп мягкий',
    3: 'рост и самовыражение', 4: 'корни и система — достигаторство гасить',
    5: 'перемены — рушить старое допустимо',
    6: 'отношения и забота',
    7: 'пауза и переосмысление — амбиции к сохранению ресурса',
    8: 'урожай и результат — рывок уместен',
    9: 'завершение — большое не начинать, отпустить',
}

HD_STRATEGY = [
    ('рефлектор', 'частый баг — растворяется в чужих состояниях; '
                  'стратегия — своё пространство и пауза перед решением'),
    ('проектор', 'частый баг — инициирует сам из вины и выгорает; '
                 'стратегия — ждать приглашения, не в лоб'),
    ('манифестирующ', 'частый баг — срывается из «надо», игнорируя отклик; '
                      'стратегия — сначала «хочу/не хочу», потом рывок'),
    ('генератор', 'частый баг — игнорирует своё «не хочу» из «надо»; '
                  'стратегия — отклик: сначала «хочу?», потом действие'),
    ('манифестор', 'частый баг — действует без информирования, собирает '
                   'сопротивление; стратегия — предупреждать о своих ходах'),
]


def detect_patterns(text: str, limit: int = 2) -> List[str]:
    found = []
    for rx, hint, _bug in COGNITIVE_PATTERNS:
        if rx.search(text):
            found.append(hint)
            if len(found) >= limit:
                break
    return found


def strip_markdown(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'\1', text)
    text = re.sub(r'`([^`\n]+?)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.M)
    text = re.sub(r'^\s*[-*_]{3,}\s*$', '', text, flags=re.M)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


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
        self.session_state: Dict[int, Dict] = {}
        self._app = None
        self._followup_task = None

    # ---------------- Команды ----------------

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🕯 Привет! Я — Проводник Души, и я рад, что ты здесь.\n\n"
            "🧭 <b>Кто я</b>\n"
            "Тёплый друг-наставник с опытом и интуицией. Честно сразу: "
            "я не терапевт и не врач. Вместо готовых советов — точные "
            "вопросы, зеркала, техники под состояние и малые шаги.\n\n"
            "🔮 <b>Что я умею</b>\n"
            "• Сессии — от пары точных вопросов до момента истины\n"
            "• Бережно вести через вскрытую боль — до якоря и шага\n"
            "• Книга уроков и копилка побед; ценности — твой компас\n"
            "• /foundation — курс внутренней опоры по столпам\n"
            "• /scan — ревизия: твои повторяющиеся ловушки, по фактам\n"
            "• Помню разговоры, учитываю время суток, энергию и темп года\n\n"
            "🤝 <b>Как работать</b>\n"
            "Просто напиши, что сейчас происходит. Здесь можно как есть.\n\n"
            "💬 Тяжело — «стоп»: остановимся, подышим.\n\n"
            "Что происходит прямо сейчас? 🌱",
            parse_mode="HTML"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = (
            "🕯 <b>Проводник Души</b> — наставник, зеркало, навигатор. "
            "Я не терапевт и не врач.\n\n"
            "<b>🎧 Сессия:</b> /session тема\n\n"
            "<b>🏛 Опора и ценности:</b>\n"
            "/foundation — курс внутренней опоры (8 столпов)\n"
            "/values — твои ценности · /set ценность свобода\n"
            "/scan — ревизия истории: ловушки и ценности\n\n"
            "<b>⚓ Якоря и победы:</b>\n"
            "/anchors · /anchor фраза · победы копятся сами (в /profile)\n\n"
            "<b>☀️🌙 Ритм:</b> /morning · /evening (в вс — фокус)\n\n"
            "<b>📖 Уроки:</b>\n"
            "/lessons · /rebuild N · /done N · /focus N или тема · "
            "/lesson текст\n\n"
            "<b>📋 Данные:</b>\n"
            "/set birth 14.03.1990 · /set пол м · /set hd Проектор 2/4\n"
            "/set энергия 7 · /set цикл 05.01.2025 (по желанию)\n"
            "/profile · /digest\n\n"
            "<b>🧹 /clear_memory</b> — с чистого листа.\n\n"
            "В группах — слово «проводник»."
        )
        await update.message.reply_text(help_text, parse_mode="HTML")

    async def set_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args

        if not args or len(args) < 2:
            await update.message.reply_text(
                "📝 /set ключ значение\n\n"
                "/set birth 14.03.1990 · /set пол м · "
                "/set psychotype Аналитик\n"
                "/set hd Проектор 2/4 · /set хронотип сова\n"
                "/set энергия 7 (0–10) · /set ценность свобода\n"
                "/set цикл 05.01.2025 (первый день, по желанию) · "
                "/set цикл_длина 30\n\n"
                "Многое соберу сам в разговоре. Просмотр: /profile"
            )
            return

        key = args[0].lower()
        value = " ".join(args[1:]).strip()
        field = PROFILE_FIELDS.get(key)
        if not field:
            await update.message.reply_text(
                "⚠️ Не знаю поля. Доступно: birth, пол, psychotype, hd, "
                "хронотип, соляр, кнопка, запрос, ценность, энергия, "
                "цикл, цикл_длина."
            )
            return

        info = self.user_data.setdefault(chat_id, {})

        if field == 'values':
            self._add_value(chat_id, value)
            await update.message.reply_text(f"💠 Ценность: «{value}»")
            return

        if field == 'energy':
            if not value.isdigit() or not (0 <= int(value) <= 10):
                await update.message.reply_text(
                    "⚠️ Энергия — число 0–10. Пример: /set энергия 7")
                return
            info['energy'] = value
            info['energy_at'] = datetime.now().strftime('%d.%m')
            self._save_user_data()
            await update.message.reply_text(f"⚡ Энергия: {value}/10 — учту.")
            return

        if field == 'cycle_start':
            if not parse_birth_date(value):
                await update.message.reply_text(
                    "⚠️ Формат даты: ДД.ММ.ГГГГ. Пример: /set цикл 05.01.2025")
                return
            info['cycle_start'] = value
            self._save_user_data()
            await update.message.reply_text(
                "🌙 Записал начало цикла. Фазу буду учитывать мягко — "
                "как рамку, не диагноз. Обновляй каждый месяц: "
                "/set цикл ДД.ММ.ГГГГ")
            return

        if field == 'cycle_length':
            if not value.isdigit() or not (21 <= int(value) <= 35):
                await update.message.reply_text(
                    "⚠️ Длина цикла — 21–35 дней. Пример: /set цикл_длина 30")
                return
            info['cycle_length'] = value
            self._save_user_data()
            await update.message.reply_text(f"🌙 Длина цикла: {value} дней.")
            return

        if field == 'birth_date' and not parse_birth_date(value):
            await update.message.reply_text(
                "⚠️ Формат: ДД.ММ.ГГГГ, например 14.03.1990")
            return

        info[field] = value
        self._save_user_data()
        extra = ""
        if field == 'birth_date':
            s = calc_summary(value)
            if s:
                extra = (f"\n🔢 Возраст {s['age']}, личный год "
                         f"{s['personal_year']}, путь {s['path']}.")
        await update.message.reply_text(
            f"✅ Записал: {PROFILE_LABELS.get(field, field)} — «{value}».{extra}"
        )

    async def profile_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        info = self.user_data.get(chat_id) or {}

        if not info:
            await update.message.reply_text(
                "📋 Пока почти ничего не знаю — и это нормально. "
                "Ускорить: /set birth 14.03.1990"
            )
            return

        lines = ["📋 Всё, что я о тебе знаю:"]
        for field in ('name', 'birth_date', 'gender', 'psychotype', 'hd',
                      'chronotype', 'solar', 'red_button', 'main_request'):
            if info.get(field):
                lines.append(f"• {PROFILE_LABELS[field]}: {info[field]}")
        if info.get('energy'):
            lines.append(f"• Энергия: {info['energy']}/10 "
                         f"(от {info.get('energy_at', '?')})")
        if info.get('cycle_start'):
            lines.append(f"• Цикл: с {info['cycle_start']} "
                         f"(длина {info.get('cycle_length', '28')})")

        if info.get('birth_date'):
            lines.append("\n🔢 Расчёт (точный):")
            for line in build_calc_lines(info['birth_date']):
                lines.append(f"• {line}")

        values = info.get('values') or []
        if values:
            lines.append("\n💠 Ценности: " +
                         ", ".join(f"«{v['text']}»" for v in values))
        if info.get('digest'):
            lines.append("🧠 Память сессий: собрана (/digest)")
        lessons = info.get('lessons') or []
        if lessons:
            lines.append(f"📖 Уроков: {len(lessons)} (/lessons)")
        anchors = info.get('anchors') or []
        if anchors:
            lines.append(f"⚓ Якорей: {len(anchors)} (/anchors)")
        wins = info.get('wins') or []
        if wins:
            lines.append(f"🏆 Побед: {len(wins)} (свежие — «" +
                         "», «".join(w['text'] for w in wins[-3:]) + "»)")
        bugs = info.get('bugs') or {}
        if bugs:
            top = sorted(bugs.items(), key=lambda x: -x[1])[:3]
            lines.append("🔎 Топ искажений: " +
                         ", ".join(f"{k} ×{v}" for k, v in top))
        pillars = info.get('pillars') or []
        if pillars:
            nxt = next((i for i in range(len(PILLARS)) if i not in pillars), None)
            lines.append(f"🏛 Опора: {len(pillars)}/{len(PILLARS)} столпов"
                         + (f", следующий — «{PILLARS[nxt]}» (/foundation)"
                            if nxt is not None else " — курс пройден!"))
        focus = info.get('focus_week') or {}
        if focus.get('theme'):
            lines.append(f"🎯 Фокус недели: «{focus['theme']}»")

        await update.message.reply_text("\n".join(lines))

    async def values_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        values = (self.user_data.get(chat_id) or {}).get('values') or []
        if not values:
            await update.message.reply_text(
                "💠 Ценностей пока нет.\n\n"
                "Ценности — твой компас: они всплывают в сессиях "
                "(что злит — там ценность; кому завидуешь — указатель; "
                "когда был живой — пик). Добавить руками: "
                "/set ценность свобода. Найти по фактам: /scan"
            )
            return
        lines = ["💠 Твои ценности:"]
        for v in values:
            lines.append(f"• «{v['text']}» (с {v.get('created', '?')})")
        lines.append("\nСверяй шаги: приближает к ценности — "
                     "или обслуживает страх?")
        await update.message.reply_text("\n".join(lines))

    async def scan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """🔍 /scan — ревизия истории: ловушки, сценарии, ценности"""
        chat_id = update.effective_chat.id
        info = self.user_data.setdefault(chat_id, {})
        history = self.memory.get_chat_messages(chat_id)
        if len(history) < 6:
            await update.message.reply_text(
                "🔍 Ревизии пока мало данных — поговорим подольше "
                "(6+ сообщений) и возвращайся."
            )
            return

        lessons = [l['title'] for l in (info.get('lessons') or [])]
        values = [v['text'] for v in (info.get('values') or [])]
        user_content = (self._history_to_text(chat_id, limit=50)
                        + "\n\nУРОКИ: " + ("; ".join(lessons) or "нет")
                        + "\nЦЕННОСТИ: " + ("; ".join(values) or "нет"))

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        try:
            messages = [
                {"role": "system", "content": build_scanner_prompt()},
                {"role": "user", "content": user_content},
            ]
            response = await self.ai_client.generate_response(messages)
        except Exception as e:
            logger.error(f"Chat {chat_id}: ошибка /scan: {e}")
            response = None
        await self._process_and_reply(update.message, chat_id, response)

    async def foundation_command(self, update: Update,
                                 context: ContextTypes.DEFAULT_TYPE):
        """🏛 /foundation — курс внутренней опоры"""
        chat_id = update.effective_chat.id
        info = self.user_data.setdefault(chat_id, {})
        pillars = info.get('pillars') or []
        nxt = next((i for i in range(len(PILLARS)) if i not in pillars), None)
        if nxt is None:
            await update.message.reply_text(
                "🏛 Курс опоры пройден целиком — снимаю шляпу. "
                "Опору теперь поддерживай: якоря, победы, ценности, "
                "утренние карты. Хочешь заново — скажи, сброшу столпы."
            )
            return

        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': f'🏛 Опора: столп {nxt} — {PILLARS[nxt]}',
            'timestamp': update.message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._generate_with_alive(
            update.message, chat_id, mode='foundation')
        await self._process_and_reply(update.message, chat_id, response)

    async def digest_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if len(self.memory.get_chat_messages(chat_id)) < 4:
            await update.message.reply_text(
                "🧠 История короткая — памяти собирать не из чего."
            )
            return
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        digest = await self._build_digest(chat_id)
        if digest:
            self.user_data.setdefault(chat_id, {})['digest'] = digest
            self._save_user_data()
            preview = digest[:800] + ("…" if len(digest) > 800 else "")
            await update.message.reply_text(
                "🧠 Память сессий обновлена:\n\n" + preview)
        else:
            await update.message.reply_text(
                "⚠️ Не удалось собрать. Попробуй позже: /digest")

    async def anchors_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        anchors = (self.user_data.get(chat_id) or {}).get('anchors') or []
        if not anchors:
            await update.message.reply_text(
                "⚓ Якорей пока нет. Якорь — короткое новое убеждение "
                "(3–7 слов), возвращает в силу. /anchor фраза"
            )
            return
        lines = ["⚓ Твои якоря:"]
        for a in anchors:
            lines.append(f"• «{a['text']}» (с {a.get('created', '?')})")
        lines.append("\nПроизнеси вслух откликающийся — рука на груди.")
        await update.message.reply_text("\n".join(lines))

    async def anchor_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        phrase = " ".join(context.args).strip() if context.args else ""
        if not phrase:
            await update.message.reply_text(
                "Формат: /anchor короткая фраза. "
                "Например: /anchor Я справляюсь с трудным")
            return
        self._add_anchor(chat_id, phrase)
        await update.message.reply_text(
            f"⚓ Якорь: «{phrase}». Произнеси 2–3 раза, рука на груди.")

    async def lessons_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        info = self.user_data.get(chat_id) or {}
        lessons = info.get('lessons') or []
        if not lessons:
            await update.message.reply_text(
                "📖 Книга уроков пуста. Начни: /session или /scan."
            )
            return
        lines = ["📖 Книга уроков:"]
        for l in lessons:
            mark = '✓' if l.get('status') == 'проработан' else '•'
            lines.append(f"{mark} {l['id']}. «{l['title']}» — {l['status']}"
                         f" (с {l.get('created', '?')})")
        focus = info.get('focus_week') or {}
        if focus.get('theme'):
            lines.append(f"\n🎯 Фокус: «{focus['theme']}»")
        lines.append("\n/rebuild N · /done N · /focus N")
        await update.message.reply_text("\n".join(lines))

    async def lesson_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        title = " ".join(context.args).strip() if context.args else ""
        if not title:
            await update.message.reply_text(
                "Формат: /lesson название. "
                "Например: /lesson Просить о помощи = слабость")
            return
        self._add_lesson(chat_id, title)
        await update.message.reply_text(f"📖 В книгу уроков: «{title}»")

    async def done_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        lessons = (self.user_data.get(chat_id) or {}).get('lessons') or []
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Формат: /done N. Список: /lessons")
            return
        n = int(context.args[0])
        for l in lessons:
            if l['id'] == n:
                l['status'] = 'проработан'
                l['done_at'] = datetime.now().strftime('%d.%m')
                self._save_user_data()
                st = self.session_state.get(chat_id)
                if st:
                    st.pop('rebuild', None)
                await update.message.reply_text(
                    f"✅ Урок «{l['title']}» — проработан. Снимаю шляпу.")
                return
        await update.message.reply_text(f"Урока {n} нет. Список: /lessons")

    async def focus_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args
        if not args:
            active = [l for l in (self.user_data.get(chat_id) or {})
                      .get('lessons', [])
                      if l.get('status') == 'в работе']
            if active:
                listing = "\n".join(f"{l['id']}. «{l['title']}»"
                                   for l in active)
                await update.message.reply_text(
                    f"🎯 Уроки в работе:\n{listing}\n\n"
                    "/focus N или /focus тема")
            else:
                await update.message.reply_text(
                    "🎯 /focus N или /focus своя тема недели.")
            return
        if args[0].isdigit():
            n = int(args[0])
            for l in (self.user_data.get(chat_id) or {}).get('lessons') or []:
                if l['id'] == n:
                    self._set_focus(chat_id, l['title'])
                    await update.message.reply_text(
                        f"🎯 Фокус недели: «{l['title']}»")
                    return
            await update.message.reply_text(f"Урока {n} нет. /lessons")
            return
        theme = " ".join(args)
        self._set_focus(chat_id, theme)
        await update.message.reply_text(f"🎯 Фокус недели: «{theme}»")

    async def rebuild_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        lessons = (self.user_data.get(chat_id) or {}).get('lessons') or []
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text(
                "Формат: /rebuild N. Список: /lessons")
            return
        n = int(context.args[0])
        lesson = next((l for l in lessons if l['id'] == n), None)
        if not lesson:
            await update.message.reply_text(f"Урока {n} нет. /lessons")
            return
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        st = self.session_state.setdefault(chat_id, {})
        st['rebuild'] = True
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': f'🔧 Пересборка урока {n}: «{lesson["title"]}»',
            'timestamp': update.message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._generate_with_alive(
            update.message, chat_id, mode='rebuild')
        await self._process_and_reply(update.message, chat_id, response)

    async def clear_memory_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.memory.clear_chat_memory(chat_id)
        self.session_state.pop(chat_id, None)
        info = self.user_data.get(chat_id) or {}
        if info.get('digest'):
            info.pop('digest', None)
            self._save_user_data()
        await update.message.reply_text(
            "🧹 Память разговора и дайджест очищены. Уроки, якоря, победы, "
            "ценности и профиль остались."
        )

    # ---------------- Ритуалы и сессия ----------------

    async def morning_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        self.session_state.pop(chat_id, None)
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': '☀️ Утренний ритуал',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._generate_with_alive(
            message, chat_id, mode='morning')
        await self._process_and_reply(message, chat_id, response)

    async def evening_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        self.session_state.pop(chat_id, None)
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': '🌙 Вечерний ритуал',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._generate_with_alive(
            message, chat_id, mode='evening')
        await self._process_and_reply(message, chat_id, response)

    async def session_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.message
        chat_id = update.effective_chat.id
        user = update.effective_user
        username = ((user.username or user.first_name or "Искатель")
                    if user else "Искатель")
        topic = " ".join(context.args).strip() if context.args else ""
        self.session_state.pop(chat_id, None)
        self.memory.add_message(chat_id, {
            'user_id': user.id if user else 0,
            'username': username,
            'text': f'🎧 Сессия: {topic}' if topic else '🎧 Сессия',
            'timestamp': message.date.isoformat(),
            'is_bot': False,
        })
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        response = await self._generate_with_alive(
            message, chat_id, mode='session')
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

            if chat_type == 'private':
                profile = self.user_data.setdefault(chat_id, {})
                if user.first_name and profile.get('name') != user.first_name:
                    profile['name'] = user.first_name
                    self._save_user_data()

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

            text = raw_text
            if self.bot_username:
                text = re.sub(
                    rf'@{re.escape(self.bot_username)}\b',
                    '', text, flags=re.IGNORECASE).strip()
                text = re.sub(r' {2,}', ' ', text)
            if not text:
                return

            self._try_capture_birth(chat_id, text, chat_type)

            self.memory.add_message(chat_id, {
                'user_id': user.id,
                'username': username,
                'text': text,
                'timestamp': message.date.isoformat(),
                'is_bot': False,
            })

            if not should_respond:
                return

            # Счётчик искажений (regex-часть профиля багов)
            info = self.user_data.setdefault(chat_id, {})
            bugs = info.setdefault('bugs', {})
            hit_any = False
            for rx, _hint, bug_name in COGNITIVE_PATTERNS:
                if rx.search(text):
                    bugs[bug_name] = bugs.get(bug_name, 0) + 1
                    hit_any = True
            if hit_any:
                self._save_user_data()

            await context.bot.send_chat_action(chat_id=chat_id, action="typing")

            hints = []
            pattern_hint = detect_patterns(text)
            if pattern_hint:
                hints.append(f"[Паттерн: {'; '.join(pattern_hint)}]")

            if CRISIS_TRIGGERS.search(text):
                hints.append("[КРИЗИС: отмени дугу, протокол кризиса]")
                st = self.session_state.setdefault(chat_id, {})
                st['crisis'] = True
                st.pop('rebuild', None)
            elif CARE_TRIGGERS.search(text):
                hints.append("[БЕРЕЖНЫЙ РЕЖИМ: триггер боли]")

            if TOPIC_SWITCH.search(text):
                hints.append("[НОВАЯ ТЕМА: счётчики сброшены — короткий "
                             "контракт, контакт]")
                st = self.session_state.get(chat_id)
                if st:
                    st['questions'] = 0
                    st['turns'] = 0
                    st.pop('rebuild', None)
                    st.pop('crisis', None)

            text_for_model = text if not hints else text + "\n" + "\n".join(hints)

            response = await self._generate_with_alive(
                message, chat_id, mode=None, user_text=text_for_model
            )
            await self._process_and_reply(message, chat_id, response)

        except Exception as e:
            logger.error(f"Ошибка в handle_message: {e}", exc_info=True)
            try:
                await update.message.reply_text(
                    "⚠️ Непредвиденная ошибка. Давай попробуем позже.")
            except Exception:
                pass

    # ---------------- Ленивый сбор ----------------

    def _try_capture_birth(self, chat_id: int, text: str, chat_type: str) -> bool:
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
        logger.info(f"Chat {chat_id}: ДР перехвачен ({birth})")
        return True

    def _extract_markers(self, text: str, chat_id: int) -> Tuple[str, List[str]]:
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
                if field == 'values':
                    self._add_value(chat_id, value)
                    confirms.append(f"💠 Ценность: «{value}»")
                    continue
                info = self.user_data.setdefault(chat_id, {})
                if field == 'energy':
                    info['energy_at'] = datetime.now().strftime('%d.%m')
                info[field] = value
                self._save_user_data()
                confirms.append(
                    f"📝 Записал: {PROFILE_LABELS.get(field, field)} — «{value}»")

            elif kind == 'LESSON':
                title = body.strip(' «»"\'')
                if title:
                    self._add_lesson(chat_id, title)
                    self._set_followup(chat_id, 'lesson', title)
                    confirms.append(f"📖 В книгу уроков: «{title}»")

            elif kind == 'FOCUS':
                theme = body.strip(' «»"\'')
                if theme:
                    self._set_focus(chat_id, theme)
                    confirms.append(f"🎯 Фокус недели: «{theme}»")

            elif kind == 'ANCHOR':
                phrase = body.strip(' «»"\'')
                if phrase:
                    self._add_anchor(chat_id, phrase)
                    confirms.append(f"⚓ Якорь сохранён: «{phrase}»")

            elif kind == 'WIN':
                phrase = body.strip(' «»"\'')
                if phrase:
                    self._add_win(chat_id, phrase)
                    confirms.append(f"🏆 В копилку побед: «{phrase}»")

            elif kind == 'VALUE':
                val = body.strip(' «»"\'')
                if val:
                    self._add_value(chat_id, val)
                    confirms.append(f"💠 Ценность: «{val}»")

            elif kind == 'BUG':
                # Счётчик семантических находок модели — тихо
                bug = body.strip(' «»"\'').lower()[:40]
                if bug:
                    info = self.user_data.setdefault(chat_id, {})
                    bugs = info.setdefault('bugs', {})
                    bugs[bug] = bugs.get(bug, 0) + 1
                    self._save_user_data()

            elif kind == 'PILLAR':
                num = body.strip().rstrip('.')
                if num.isdigit() and int(num) < len(PILLARS):
                    n = int(num)
                    info = self.user_data.setdefault(chat_id, {})
                    pillars = info.setdefault('pillars', [])
                    if n not in pillars:
                        pillars.append(n)
                        self._save_user_data()
                    confirms.append(
                        f"🏛 Столп «{PILLARS[n]}» поставлен "
                        f"({len(pillars)}/{len(PILLARS)}).")

            elif kind == 'CARE':
                topic = body.strip(' «»"\'')
                if topic:
                    self.user_data.setdefault(chat_id, {})['care'] = {
                        'topic': topic,
                        'set_at': datetime.now().strftime('%d.%m %H:%M'),
                    }
                    self._set_followup(chat_id, 'care', topic)
                    self._save_user_data()
                    confirms.append("🌱 Завтра спрошу, как ты. Обещаю.")

            elif kind == 'PHASE':
                phase = body.strip().lower().rstrip('.')
                phase = PHASE_FIXES.get(phase, phase)
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
            'title': title, 'status': 'в работе',
            'created': datetime.now().strftime('%d.%m'),
        })
        self._save_user_data()

    def _add_anchor(self, chat_id: int, phrase: str) -> None:
        anchors = self.user_data.setdefault(chat_id, {}).setdefault('anchors', [])
        if any(a['text'].lower() == phrase.lower() for a in anchors):
            return
        anchors.append({'text': phrase,
                        'created': datetime.now().strftime('%d.%m')})
        self._save_user_data()

    def _add_win(self, chat_id: int, phrase: str) -> None:
        wins = self.user_data.setdefault(chat_id, {}).setdefault('wins', [])
        if any(w['text'].lower() == phrase.lower() for w in wins):
            return
        wins.append({'text': phrase,
                     'created': datetime.now().strftime('%d.%m')})
        if len(wins) > 15:
            del wins[:len(wins) - 15]
        self._save_user_data()

    def _add_value(self, chat_id: int, val: str) -> None:
        values = self.user_data.setdefault(chat_id, {}).setdefault('values', [])
        if any(v['text'].lower() == val.lower() for v in values):
            return
        values.append({'text': val,
                       'created': datetime.now().strftime('%d.%m')})
        self._save_user_data()

    def _set_followup(self, chat_id: int, ftype: str, topic: str) -> None:
        self.user_data.setdefault(chat_id, {})['followup'] = {
            'type': ftype, 'topic': topic,
            'due_ts': time.time() + self.config.followup_hours * 3600,
        }
        self._save_user_data()

    def _update_phase(self, chat_id: int, phase: str) -> None:
        st = self.session_state.get(chat_id) or {}
        new_topic = (phase == 'контакт')
        turns = 1 if new_topic else st.get('turns', 0) + 1
        same = st.get('same', 0) + 1 if st.get('phase') == phase else 1
        force = (turns >= 4
                 and phase not in ('кульминация', 'проживание', 'закрепление'))
        crisis = bool(st.get('crisis')) and not new_topic
        rebuild = bool(st.get('rebuild')) and not new_topic
        questions = 0 if new_topic else st.get('questions', 0)
        self.session_state[chat_id] = {
            'phase': phase, 'same': same, 'turns': turns, 'force': force,
            'crisis': crisis, 'rebuild': rebuild, 'questions': questions,
        }

    def _set_focus(self, chat_id: int, theme: str) -> None:
        self.user_data.setdefault(chat_id, {})['focus_week'] = {
            'theme': theme,
            'set_at': datetime.now().strftime('%d.%m'),
        }
        self._save_user_data()

    # ---------------- Физиология ----------------

    def _cycle_line(self, info: Dict) -> Optional[str]:
        start = parse_birth_date(info.get('cycle_start') or '')
        if not start:
            return None
        try:
            length = int(info.get('cycle_length', 28) or 28)
        except (TypeError, ValueError):
            length = 28
        length = max(21, min(35, length))
        days = (datetime.now().date() - start).days
        if days < 0:
            return None
        if days > length + 10:
            return ("— ЦИКЛ: данные устарели — попроси обновить "
                    "(/set цикл ДД.ММ.ГГГГ)")
        day = days % length
        if day == 0:
            day = length
        if day <= 5:
            phase = "менструация (сил меньше — бережность к телу)"
        elif day <= 13:
            phase = "фолликулярная (энергия растёт)"
        elif day <= 16:
            phase = "овуляторное окно (пик энергии)"
        elif day <= length - 6:
            phase = "лютеальная (стабильная)"
        else:
            phase = ("поздняя лютеальная/PMS-окно: эмоции могут быть "
                     "обострены — это химия, не «она такая»")
        return f"— ЦИКЛ: день ~{day} из {length}, {phase}. Рамка, не диагноз."

    # ---------------- Генерация ----------------

    async def _generate_with_alive(self, message, chat_id: int,
                                   mode: Optional[str] = None,
                                   user_text: Optional[str] = None) -> Optional[str]:
        typing_task = asyncio.create_task(self._keep_typing(chat_id))
        try:
            response = await self.generate_response(
                chat_id, message.chat.type, mode=mode, user_text=user_text)
            if response:
                await asyncio.sleep(random.uniform(2.0, 3.0))
            return response
        finally:
            typing_task.cancel()

    async def _keep_typing(self, chat_id: int) -> None:
        if self._app is None:
            return
        try:
            while True:
                await self._app.bot.send_chat_action(
                    chat_id=chat_id, action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def generate_response(self, chat_id: int, chat_type: str,
                                mode: Optional[str] = None,
                                user_text: Optional[str] = None) -> Optional[str]:
        try:
            history = self.memory.get_chat_messages(chat_id)
            context_history = history[-self.config.max_history_messages:]
            if user_text and context_history and not context_history[-1]['is_bot']:
                context_history = context_history[:-1] + [
                    {**context_history[-1], 'text': user_text}]
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
            logger.error(f"Ошибка генерации: {e}", exc_info=True)
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
            data.append("— РАСЧЁТ (посчитан точно):")
            for line in build_calc_lines(info['birth_date']):
                data.append(f"  {line}")
            s = calc_summary(info['birth_date'])
            if s:
                tempo = YEAR_TEMPO.get(s['personal_year'], '')
                if tempo:
                    data.append(f"— ТЕМП ГОДА (рамка): {s['personal_year']} — "
                                f"{tempo}. Запросы «порвать всех» сверяй с "
                                "темпом; конфликт — назови вслух.")

        # Физиология
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 11:
            tod = "утро (кортизоловый подъём — энергия для сложного)"
        elif 11 <= hour < 17:
            tod = "день"
        elif 17 <= hour < 23:
            tod = "вечер"
        else:
            tod = ("глубокая ночь: утомление искажает мышление — "
                   "бережность выше, тяжёлые вскрытия отложить")
        ct = (info.get('chronotype') or '').lower()
        if 'сова' in ct and not (5 <= hour < 11):
            tod += "; сове сейчас легче, но сон важнее"
        elif 'жаворонок' in ct and (hour >= 22 or hour < 5):
            tod += "; жаворонку сейчас тяжело — темп мягче"
        data.append(f"— Время суток: {tod}")

        if info.get('energy'):
            data.append(f"— Энергия (самоотчёт, {info.get('energy_at','?')}): "
                        f"{info['energy']}/10. Запросы сверяй с ресурсом.")

        cycle = self._cycle_line(info)
        if cycle:
            data.append(cycle)

        # HD-стратегия
        hd = (info.get('hd') or '').lower()
        if hd:
            for key, strategy in HD_STRATEGY:
                if key in hd:
                    data.append(f"— HD-СТРАТЕГИЯ (гипотеза): {strategy}. "
                                "Применяй при темах выгорания/инициативы.")
                    break

        if info.get('digest'):
            data.append("— ПАМЯТЬ СЕССИЙ (суть):")
            for ln in info['digest'].splitlines():
                if ln.strip():
                    data.append(f"  {ln.strip()}")

        values = info.get('values') or []
        if values:
            data.append("— ЦЕННОСТИ (компас; сверяй шаги: приближает "
                        "или обслуживает страх):")
            for v in values[-5:]:
                data.append(f"  💠 «{v['text']}»")

        lessons = info.get('lessons') or []
        active = [l for l in lessons if l.get('status') == 'в работе'][:8]
        finished = [l for l in lessons if l.get('status') == 'проработан'][-3:]
        if active or finished:
            data.append("— КНИГА УРОКОВ:")
            for l in active:
                data.append(f"  {l['id']}. [в работе] «{l['title']}»")
            for l in finished:
                data.append(f"  {l['id']}. [проработан] «{l['title']}»")

        anchors = (info.get('anchors') or [])[-3:]
        if anchors:
            data.append("— ЯКОРЯ (по одному, в уместный момент):")
            for a in anchors:
                data.append(f"  ⚓ «{a['text']}»")

        wins = (info.get('wins') or [])[-3:]
        if wins:
            data.append("— ПОБЕДЫ (при залипании поднимай как "
                        "доказательства):")
            for w in wins:
                data.append(f"  🏆 «{w['text']}»")

        bugs = info.get('bugs') or {}
        if bugs:
            top = sorted(bugs.items(), key=lambda x: -x[1])[:2]
            data.append("— ЧАСТЫЕ ИСКАЖЕНИЯ (бей прицельно): " +
                        ", ".join(f"{k} ×{v}" for k, v in top))

        pillars = info.get('pillars') or []
        if pillars or mode == 'foundation':
            nxt = next((i for i in range(len(PILLARS))
                        if i not in pillars), None)
            if mode == 'foundation' and nxt is not None:
                data.append(f"— ОПОРА (курс активен): завершено "
                            f"{len(pillars)}/{len(PILLARS)}; текущий столп "
                            f"{nxt} «{PILLARS[nxt]}».")
            elif pillars:
                data.append(f"— ОПОРА: {len(pillars)}/{len(PILLARS)} столпов "
                            "(/foundation).")

        focus = info.get('focus_week') or {}
        if focus.get('theme'):
            data.append(f"— Фокус недели: «{focus['theme']}»")
        else:
            data.append("— Фокус недели: не задан")

        care = info.get('care')
        if care:
            data.append(
                f"— ⚠️ БЕРЕЖНЫЙ РЕЖИМ: недавно вскрыта тема "
                f"«{care.get('topic', '')}». Начни с короткой фразы заботы; "
                "напомни якорь, если уместно.")

        st = self.session_state.get(chat_id)
        if st:
            if st.get('crisis'):
                data.append(
                    "— КРИЗИСНЫЙ РЕЖИМ: дуга отменена. 1) «я с тобой», "
                    "дышим; 2) «ты в безопасности прямо сейчас?» "
                    "(опасность — 112/близкий); 3) живая поддержка.")
            else:
                q = st.get('questions', 0)
                if st.get('rebuild'):
                    data.append("— РЕЖИМ ПЕРЕСБОРКИ: лимит не действует; "
                                "один вопрос на реплику.")
                data.append(
                    f"— Фаза сессии: {st['phase']} (обменов: "
                    f"{st.get('turns', 1)}; вопросов задано: {q}/4)")
                if q >= 4 and st['phase'] != 'проживание':
                    data.append("— ЛИМИТ ВОПРОСОВ ИСЧЕРПАН (4/4): только "
                                "выборы без «?», кульминация или закрепление.")
                elif q >= 3 and st['phase'] != 'проживание':
                    data.append("— Остался один вопрос до лимита; после — "
                                "только выборы и утверждения.")
                if st.get('force'):
                    data.append("— ПРЕДОХРАНИТЕЛЬ: 4+ обменов — следующий "
                                "ответ кульминация: 1–3 коротких предложения, "
                                "БЕЗ вопросов. Затем проживание/закрепление.")
                elif st.get('turns', 0) >= 3:
                    data.append("— СЖАТИЕ: реплики короче, к сути.")
                elif st.get('same', 1) >= 2 and st['phase'] == 'контакт':
                    data.append("— ЗАВИСАНИЕ: контакт затянут — к диагностике.")
                elif st.get('same', 1) >= 2 and st['phase'] == 'диагностика':
                    data.append("— ЗАВИСАНИЕ: гипотезу проверяй кульминацией.")
                elif st.get('same', 1) >= 2 and st['phase'] == 'нарастание':
                    data.append("— ЗАВИСАНИЕ: материала достаточно — "
                                "кульминация, без вопросов.")
                elif st.get('same', 1) >= 3 and st['phase'] == 'проживание':
                    data.append("— Пора к закреплению.")
        else:
            data.append("— Фаза сессии: не отслежена (начни с контакта)")

        missing = [PROFILE_LABELS[f] for f in ASKABLE if not info.get(f)]
        if missing:
            data.append(f"— Ещё не знаю: {', '.join(missing)} "
                        "(по одному, только когда нужно)")
        data.append(f"— Сегодня: {now.strftime('%d.%m.%Y')} "
                    f"({RU_WEEKDAYS[now.weekday()]})")
        blocks.append("\n".join(data))

        if mode == 'morning':
            blocks.append(MORNING_PROMPT)
        elif mode == 'evening':
            blocks.append(EVENING_PROMPT)
            if now.weekday() == 6:
                blocks.append(SUNDAY_EVENING_ADD)
        elif mode == 'rebuild':
            blocks.append(REBUILD_PROMPT)
        elif mode == 'session':
            blocks.append(SESSION_PROMPT)
        elif mode == 'foundation':
            blocks.append(FOUNDATION_PROMPT)

        return "\n".join(blocks)

    async def _process_and_reply(self, message, chat_id: int,
                                 response: Optional[str]) -> None:
        if not response:
            await message.reply_text(
                "⚠️ Не получилось сформировать ответ. Попробуй ещё раз.")
            return
        old_care = (self.user_data.get(chat_id) or {}).get('care')
        clean, confirms = self._extract_markers(response, chat_id)
        clean = strip_markdown(clean)
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
        self.memory.add_message(chat_id, {
            'user_id': 0,
            'username': self.bot_username or 'Проводник',
            'text': clean,
            'timestamp': (sent.date if sent else message.date).isoformat(),
            'is_bot': True,
        })
        # Счётчик вопросов: «?» = 1, императив = 0.5 (анти-обход)
        st = self.session_state.get(chat_id)
        if st is not None and not st.get('rebuild'):
            q = clean.count('?')
            q += 0.5 * len(IMPERATIVE_RE.findall(clean))
            st['questions'] = st.get('questions', 0) + q
        info = self.user_data.get(chat_id) or {}
        if old_care and info.get('care') == old_care:
            info.pop('care', None)
            self._save_user_data()

    # ---------------- Follow-up ----------------

    async def _followup_loop(self) -> None:
        logger.info("Follow-up петля запущена")
        while True:
            try:
                await asyncio.sleep(600)
                now_ts = time.time()
                fired = False
                for chat_id, info in self.user_data.items():
                    fu = info.get('followup')
                    if not fu or fu.get('due_ts', 0) > now_ts:
                        continue
                    try:
                        if fu.get('type') == 'lesson':
                            text = (f"👋 Вчера договорились: эксперимент "
                                    f"«{fu.get('topic', '')}». Удалось? "
                                    "Как ощущения 0–10?")
                        else:
                            text = ("👋 Вчера был глубокий процесс. "
                                    "Как дышишь сегодня? Что с энергией?")
                            info['care'] = {
                                'topic': fu.get('topic', ''),
                                'set_at': datetime.now().strftime('%d.%M'),
                            }
                        await self._app.bot.send_message(
                            chat_id=chat_id, text=text)
                        self.memory.add_message(chat_id, {
                            'user_id': 0,
                            'username': self.bot_username or 'Проводник',
                            'text': text,
                            'timestamp': datetime.now().isoformat(),
                            'is_bot': True,
                        })
                        info.pop('followup', None)
                        fired = True
                        logger.info(f"Chat {chat_id}: follow-up отправлен")
                    except Exception as e:
                        logger.error(f"Chat {chat_id}: follow-up: {e}")
                        info.pop('followup', None)
                if fired:
                    self._save_user_data()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Follow-up петля: {e}")

    # ---------------- Дайджест ----------------

    async def _build_digest(self, chat_id: int) -> Optional[str]:
        if len(self.memory.get_chat_messages(chat_id)) < 4:
            return None
        try:
            messages = [
                {"role": "system", "content": REENTRY_PROMPT},
                {"role": "user",
                 "content": self._history_to_text(chat_id, limit=50)},
            ]
            digest = await self.ai_client.generate_response(messages)
            if not digest:
                return None
            digest = strip_markdown(digest).strip()
            digest = re.sub(r'\n{3,}', '\n\n', digest)
            return digest[:1500] or None
        except Exception as e:
            logger.error(f"Chat {chat_id}: дайджест: {e}")
            return None

    def _history_to_text(self, chat_id: int, limit: int = 50) -> str:
        history = self.memory.get_chat_messages(chat_id)[-limit:]
        return "\n".join(
            f"{'Проводник' if m['is_bot'] else m.get('username', 'Человек')}: "
            f"{m['text']}"
            for m in history)

    async def _reentry_after_restart(self) -> None:
        chats = [cid for cid in self.user_data
                 if len(self.memory.get_chat_messages(cid)) >= 4]
        if not chats:
            logger.info("Возвращение в контекст: историй нет")
            return
        logger.info(f"Возвращение в контекст: {len(chats)} чат(ов)")
        for chat_id in chats:
            try:
                digest = await self._build_digest(chat_id)
                if digest:
                    self.user_data.setdefault(chat_id, {})['digest'] = digest
                    logger.info(f"Chat {chat_id}: дайджест обновлён")
            except Exception as e:
                logger.error(f"Chat {chat_id}: дайджест при старте: {e}")
        self._save_user_data()

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
                    f, ensure_ascii=False)
        except OSError as e:
            logger.error(f"Не удалось сохранить {path}: {e}")

    # ---------------- Инфраструктура ----------------

    async def error_handler(self, update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Исключение: {context.error}")

    async def post_init(self, application: Application):
        bot_info = await application.bot.get_me()
        self.bot_username = bot_info.username
        self._app = application
        logger.info(f"Бот запущен: @{self.bot_username}")
        await self._reentry_after_restart()
        self._followup_task = asyncio.create_task(self._followup_loop())

    async def post_shutdown(self, application: Application):
        if self._followup_task:
            self._followup_task.cancel()
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
        application.add_handler(CommandHandler("digest", self.digest_command))
        application.add_handler(CommandHandler("values", self.values_command))
        application.add_handler(CommandHandler("scan", self.scan_command))
        application.add_handler(CommandHandler("foundation",
                                               self.foundation_command))
        application.add_handler(CommandHandler("anchors", self.anchors_command))
        application.add_handler(CommandHandler("anchor", self.anchor_command))
        application.add_handler(CommandHandler("lessons", self.lessons_command))
        application.add_handler(CommandHandler("lesson", self.lesson_command))
        application.add_handler(CommandHandler("done", self.done_command))
        application.add_handler(CommandHandler("focus", self.focus_command))
        application.add_handler(CommandHandler("rebuild", self.rebuild_command))
        application.add_handler(CommandHandler("clear_memory",
                                               self.clear_memory_command))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        application.add_error_handler(self.error_handler)

        logger.info("Бот запускается...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    SoulGuideBot().run()
