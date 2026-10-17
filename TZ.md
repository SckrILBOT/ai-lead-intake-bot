# ТЗ: AI Lead Intake & Triage Bot

Production-ready Telegram-бот на Python для портфолио AI-разработчика.

## 1. Легенда

AI-агент приёма и квалификации лидов для студии разработки и AI-автоматизации. Вместо кнопочной анкеты бот ведёт естественный диалог (консультирует по услугам и мягко собирает данные), понимает текст и голосовые, оценивает «теплоту» лида (Lead Scoring), сохраняет структурированную заявку в Google Sheets и шлёт HTML-уведомление админу на почту.

## 2. Стек (строго)

| Область | Технология |
|---|---|
| Язык | Python 3.11+ (на машине разработчика 3.14.7) |
| Telegram | aiogram 3.x (async) |
| LLM | `google-genai` (`from google import genai`), НЕ `google-generativeai` |
| Модель | настраиваемая через `GEMINI_MODEL`; по умолчанию `gemini-3.5-flash` (2.5-flash закрыт для новых пользователей; см. п. 12) |
| Валидация | pydantic v2 |
| История | aiosqlite |
| Sheets | gspread + google-auth, вызовы через `asyncio.to_thread` |
| Email | aiosmtplib + `email.message.EmailMessage` |
| Конфиг/деплой | pydantic-settings (.env), Docker, docker-compose |

## 3. Структура проекта

```
TG_bot/
  bot/
    handlers/ (start.py, chat.py)
    keyboards.py
    middlewares/ (throttling.py)
  services/ (llm_service.py, sheets_service.py, email_service.py, lead_dispatcher.py)
  database/ (db.py)
  schemas/ (lead.py)
  tests/
  config.py
  main.py
  requirements.txt  .env.example  .gitignore  .dockerignore
  Dockerfile  docker-compose.yml  README.md  TZ.md  START.md
```

## 4. Диалог и Gemini (`services/llm_service.py`)

- История в SQLite, в контекст идут последние 15 сообщений. История начинается с `user`-роли, роли чередуются (иначе Gemini вернёт ошибку).
- Системный промпт: дружелюбный профессиональный AI-консультант студии. Услуги: разработка ботов, внедрение AI-агентов, интеграции CRM. Ненавязчиво выясняет 4 поля:
  1. `client_name`  2. `contact_info` (телефон/email/подтверждение связи в Telegram)  3. `project_description`  4. `budget_or_timeline`.
- Отвечать на языке клиента. Без markdown (ответы шлются без parse_mode). Короткие реплики, один вопрос за раз.
- В промпт на каждый запрос подставляются данные Telegram (first_name, username), чтобы «контакт — этот Telegram» работал всегда.
- Рубрика скоринга в промпте: **High** — конкретная задача + названный бюджет + срок до ~1 мес; **Medium** — задача понятна, но бюджет или срок размыты; **Low** — расплывчато, без бюджета и сроков.
- Tool Calling: инструмент `submit_lead`, схема из Pydantic (`parameters_json_schema`), automatic function calling ОТКЛЮЧЁН. Поля: `client_name`, `contact_info`, `project_description`, `budget_or_timeline`, `ai_summary` (1–2 предложения для менеджера), `lead_priority` (Literal High/Medium/Low), `category` (например Telegram Bot / AI Agent / CRM Integration / Other).
- Невалидные аргументы (пустые поля и т.п.) → в модель возвращается `function_response` с ошибкой, диалог продолжается.
- После успешной заявки второй LLM-вызов НЕ делается: шаблонное подтверждение клиенту.
- Ретраи с backoff на 429/503 (вручную, без tenacity). Пустой ответ/safety-блок → вежливый fallback-текст.

## 5. Голосовые

- `F.voice`: скачать в `io.BytesIO`, лимит длительности/размера.
- **Два шага**: (1) Gemini транскрибирует аудио (`audio/ogg`, `types.Part.from_bytes`); (2) транскрипт сохраняется в историю как текст и идёт обычным диалоговым вызовом.
- Другие типы контента (фото, стикеры) → вежливое сообщение «понимаю текст и голос».

## 6. Google Sheets (`services/sheets_service.py`)

Строка: `[Дата/Время, @username, Telegram ID, Имя, Контакты, Категория, Саммари (AI), Бюджет/Сроки, Приоритет, Статус="New"]`.
- Нет заголовков → создаются автоматически.
- Запись с `value_input_option="RAW"` (защита от формул-инъекций).
- `GOOGLE_SERVICE_ACCOUNT_JSON` — путь к файлу ИЛИ сам JSON (определять по `{`). Таблицу нужно расшарить на email сервис-аккаунта.
- Часовой пояс — `TIMEZONE` (по умолчанию UTC).

## 7. Email (`services/email_service.py`)

- Тема: `🔥 [High Priority] Новая заявка от {client_name} ({category})` (🔥 High, ⚡ Medium, 💤 Low).
- HTML-карточка со всеми полями + AI-саммари + plain-text альтернатива. Все пользовательские данные через `html.escape`.
- Переменные: `SMTP_HOST`, `SMTP_PORT`(587), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`(=USER), `ADMIN_EMAIL`. 465 → implicit TLS, иначе STARTTLS.

## 8. Mock / Fallback Mode (ключевое)

Запуск возможен только с `TELEGRAM_BOT_TOKEN` и `GEMINI_API_KEY`.
- Каналы Sheets и Email включаются независимо; пустые значения в `.env` (`""`) = не задано (валидатор «пусто → None»).
- Не настроенный ИЛИ упавший на runtime канал → красивый лог лида в консоль + запись в `leads_backup.json` (JSON-массив, с asyncio.Lock и атомарной записью).
- Каналы отправляются через `asyncio.gather(return_exceptions=True)`: сбой одного не блокирует другой.
- Клиент получает успех, если лид сохранён хотя бы где-то (включая backup).

## 9. Telegram UX

- `/start` — приветствие (можно писать текстом или голосом) + инлайн-кнопка «Начать заново / Очистить контекст».
- `/reset` — очистка истории и статуса сессии.
- `ChatActionSender` (typing; record_voice для голосовых) — пока идёт генерация.
- Защита от дублей: таблица сессий со статусом `active/completed`; per-user `asyncio.Lock`; после заявки `submit_lead` больше не передаётся модели, в промпт добавляется «заявка уже оформлена». Бот остаётся консультантом, новую заявку создаёт только кнопка «Новая заявка».
- Throttling-middleware против спама (бережём квоту Gemini).
- Сообщения длиннее 4096 символов режутся.

## 10. БД (`database/db.py`)

SQLite, WAL. Таблицы: `messages(id, user_id, role, content, created_at)`, `sessions(user_id PK, status, updated_at)`. Хранится не более ~15 последних сообщений на пользователя.

## 11. Безопасность

`.gitignore`/`.dockerignore`: `.env`, ключи сервис-аккаунта, `leads_backup.json`, `data/`, `.venv`. Секреты не логировать. Экранирование HTML в письме, RAW в Sheets.

## 12. Выбор модели

Пользователь имеет подписку Google AI Pro и хочет использовать самую новую модель («gemini argon»). Условия:
- Подписка Google AI Pro (потребительская) НЕ даёт квоту API: для `google-genai` нужен ключ из Google AI Studio со своими лимитами/биллингом.
- Точный ID модели «argon» не подтверждён. Модель берётся из `GEMINI_MODEL`; ID нужно сверить через `client.models.list()` с ключом пользователя (в проекте будет утилита/команда для вывода списка моделей).
- Код не должен зависеть от конкретной модели; thinking-параметры опциональны.

## 13. Тесты (`tests/`)

Офлайн, с моком Gemini: полный поток диалога, вызов `submit_lead`, валидация ошибок, fallback в JSON, защита от дублей, сборка HTML-письма, обрезка истории.

## 14. Документация

`README.md` — только на русском, оформление для GitHub-портфолио: бизнес-ценность, архитектура (схема), стек, запуск, получение ключей (BotFather, AI Studio, Google Sheets service account, SMTP app password), Docker, fallback-режим.

## 15. Критерии готовности

1. Все файлы созданы по структуре; `py_compile` и импорты проходят.
2. Офлайн-тесты зелёные.
3. Бот стартует с одним `TELEGRAM_BOT_TOKEN` + `GEMINI_API_KEY`.
4. Инструкция первого запуска для Windows (PowerShell) и Docker.
