# Инструкции по развертыванию Telegram бота «Архитектор Судьбы»

Бот использует DeepSeek API напрямую (через совместимый клиент). Все переменные окружения описаны ниже.

## Варианты хостинга для постоянной работы

### 1. Railway.app (Рекомендуется — бесплатно)

1. Зарегистрируйтесь на https://railway.app (может потребоваться VPN).
2. Подключите GitHub аккаунт.
3. Создайте репозиторий с кодом бота на GitHub (см. инструкцию по переносу).
4. В Railway создайте новый проект из GitHub.
5. В разделе **Environment Variables** добавьте:
   - `TELEGRAM_BOT_TOKEN` = ваш токен от @BotFather
   - `OPENROUTER_API_KEY` = ваш ключ DeepSeek (полученный на platform.deepseek.com)
6. Railway автоматически развернет бота.

### 2. Render.com (Бесплатно с ограничениями)

1. Зарегистрируйтесь на https://render.com
2. Создайте Web Service из GitHub репозитория.
3. Настройки:
   - **Build Command**: `pip install python-telegram-bot==22.3 aiohttp==3.12.15`
   - **Start Command**: `python main.py`
4. Добавьте переменные окружения (те же, что выше).

### 3. Heroku (Бесплатно до 550 часов/месяц)

1. Установите Heroku CLI.
2. Выполните команды:
```bash
heroku create your-bot-name
heroku config:set TELEGRAM_BOT_TOKEN=your_token
heroku config:set OPENROUTER_API_KEY=your_key
git push heroku main
4. VPS/Сервер

    Установите Python 3.11+.

    Загрузите код бота.

    Установите зависимости: pip install python-telegram-bot aiohttp requests.

    Настройте переменные окружения (см. выше).

    Запустите: python main.py.

    Используйте systemd или screen для фоновой работы.

Переменные окружения

Обязательно установите на хостинге:
Переменная	Описание
TELEGRAM_BOT_TOKEN	Токен вашего Telegram-бота (от @BotFather)
OPENROUTER_API_KEY	Ключ DeepSeek (получить на platform.deepseek.com) — название оставлено для совместимости с кодом
Файлы для развертывания

    main.py — основной файл бота

    bot_config.py — конфигурация и системный промпт

    openrouter_client.py — клиент для DeepSeek API

    message_memory.py — память сообщений

    keep_alive.py — keep-alive система

    Procfile — для Heroku

    Dockerfile — для Docker

    runtime.txt — версия Python

После развертывания

Бот будет работать постоянно 24/7 и отвечать:

    В личных сообщениях — на любое сообщение.

    В группах — только на упоминание бота (по имени или слову «Архитектор»).

    Контекст — помнит последние 20 сообщений в каждом чате.

    Ответы — генерируются DeepSeek по системному промпту «Архитектора Судьбы».

---

## 📄 Файл 2: `инструкция_по_переносу_в_github.md` (или как он у вас называется)

Если у вас файл называется `инструкция_по_переносу_проекта_в_github.md`, замените его содержимое на это:

```markdown
# Инструкция по переносу проекта в GitHub

## Шаг 1: Подготовка файлов

Ваш проект уже готов для GitHub со всеми необходимыми файлами:

### Основные файлы бота:
- `main.py` — основной файл бота (обработка сообщений, память)
- `bot_config.py` — конфигурация и системный промпт Архитектора
- `openrouter_client.py` — клиент для DeepSeek API
- `message_memory.py` — управление историей чатов
- `keep_alive.py` — keep-alive механизм

### Файлы для развертывания:
- `Procfile` — для Heroku
- `Dockerfile` — для Docker/Railway
- `runtime.txt` — версия Python
- `.gitignore` — исключения для Git

### Документация:
- `README.md` — описание проекта
- `deploy_instructions.md` — инструкции по развертыванию

## Шаг 2: Создание репозитория на GitHub

1. Зайдите на https://github.com
2. Нажмите **New repository**.
3. Название: `architect-bot` (или любое другое, например `nastavnik-bot`).
4. Описание: `Telegram бот-наставник «Архитектор Судьбы» на базе DeepSeek`.
5. Выберите Public или Private.
6. **НЕ** добавляйте README, .gitignore (у нас уже есть).
7. Нажмите **Create repository**.

## Шаг 3: Загрузка кода

### Вариант A: Через веб-интерфейс GitHub
1. Скачайте все файлы проекта из текущего источника (например, из Replit или локальной папки).
2. На странице нового репозитория нажмите **uploading an existing file**.
3. Перетащите все файлы проекта (перечисленные выше).
4. Добавьте commit message: `Initial commit: Architect Bot with DeepSeek`.
5. Нажмите **Commit new files**.

### Вариант B: Через Git командную строку
```bash
git clone https://github.com/ваш-username/architect-bot.git
cd architect-bot
# Скопируйте все файлы проекта в эту папку
git add .
git commit -m "Initial commit: Architect Bot with DeepSeek"
git push origin main

Шаг 4: Проверка загрузки

Убедитесь, что все файлы загружены:

    ✅ main.py

    ✅ bot_config.py

    ✅ openrouter_client.py

    ✅ message_memory.py

    ✅ keep_alive.py

    ✅ README.md

    ✅ Procfile

    ✅ Dockerfile

    ✅ .gitignore

    ✅ deploy_instructions.md

Шаг 5: Развертывание на Railway.app

    Зайдите на https://railway.app

    Нажмите Start a New Project.

    Выберите Deploy from GitHub repo.

    Подключите GitHub аккаунт.

    Выберите репозиторий architect-bot (или ваше название).

    Railway автоматически обнаружит Python проект.

    Добавьте переменные окружения (см. раздел ниже).

    Нажмите Deploy.

Переменные окружения для Railway:

    TELEGRAM_BOT_TOKEN = ваш токен от @BotFather

    OPENROUTER_API_KEY = ваш ключ DeepSeek (с platform.deepseek.com)

Шаг 6: Проверка работы

После успешного развертывания:

    Бот будет работать 24/7.

    В личных сообщениях отвечает на всё.

    В группах — только на упоминание.

    Использует системный промпт «Архитектора Судьбы».

Готово!

Ваш бот теперь:
✅ Размещен на GitHub
✅ Работает постоянно на Railway
✅ Отвечает как наставник с DeepSeek
✅ Помнит контекст разговора
✅ Полностью автономен
