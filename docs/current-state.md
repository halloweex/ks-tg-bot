# Текущее состояние кода

Снимок собран на коммите `27cd73f`. **Четыре находки уже исправлены** —
коммиты `424f837`, `2a61ece`, `7b545a8`, `4a9b4f7` на ветке
`fix/block-0-hardening`; в тексте они помечены как «исправлено» прямо на месте,
а не вычеркнуты, потому что описание того, что было сломано, — это и есть
обоснование правила в `docs/components.md`.

Собрано пятью читающими агентами по непересекающимся зонам: граф зависимостей,
контракты внешних API, слой данных, состояние и жизненный цикл, конфиг/деплой/
наблюдаемость. Каждое утверждение ниже имеет ссылку `файл:строка`. Где проверить
не удалось — написано «не проверено».

Ничего не чинится и не предлагается. Это база, относительно которой пишется
`docs/components.md`.

---

## 1. Размер и состав

`bot/` — 31 файл, 5128 строк (`wc -l bot/**/*.py`), из них `bot/db.py` — 855,
`bot/handlers/orders.py` — 691, `bot/handlers/broadcast.py` — 318, `bot/i18n.py` — 308.

Автотестов нет: каталога `tests/`, файлов `test_*.py` и pytest в
`requirements.txt` не существует. `requirements.txt` — 7 зависимостей:
aiogram, aiosqlite, python-dotenv, pyyaml, httpx, loguru, pydantic-settings, tzdata.

---

## 2. Граф зависимостей

**Циклов нет.** Проверено двумя способами: AST-обход с DFS по 31 модулю (вывод
`MODULES 31 / === CYCLES === / NONE`) и фактический импорт всех 29 импортируемых
модулей интерпретатором `.venv` — ни одного `ImportError`.

**Границы, которые сегодня держатся:**

- `httpx` импортируется ровно в трёх файлах: `bot/services/keycrm.py:8`,
  `bot/services/novaposhta.py:6`, `bot/services/shopify.py:7`. Ни один хендлер
  httpx не импортирует (`grep -rn httpx bot/handlers/` — пусто).
- `aiosqlite` импортируется ровно в одном файле: `bot/db.py:9`.
- `aiogram` не импортируется ни в одном файле `bot/services/`
  (`grep -rn aiogram bot/services/` — пусто).
- Ни один модуль не импортирует одновременно `aiogram` и `httpx` (проверено
  пересечением множеств при AST-обходе).

**Границы, которых нет:**

- Хендлеры вызывают сетевые клиенты напрямую через инъекцию:
  `bot/handlers/orders.py:369,371` (`keycrm.get_orders_by_phone`,
  `shopify.get_orders_by_phone`), `bot/handlers/onboarding.py:74,76,111`,
  `bot/handlers/delivery.py:106` (`novaposhta.track_many`). Импорт клиента как
  типа: `bot/handlers/orders.py:25,26`, `bot/handlers/onboarding.py:18,19`,
  `bot/handlers/delivery.py:15`.
- 8 из 10 хендлеров импортируют `bot.db` напрямую: `broadcast.py:17` (12 функций),
  `common.py:12`, `delivery.py:13`, `demo.py:24`, `onboarding.py:15`,
  `orders.py:19` (12 имён), `settings.py:12`, `support.py:15`. Без импорта БД
  только `info.py` и `menu.py`.
- `bot/stock.py` — модуль воркерной формы внутри пакета бота: импортирует
  `aiogram` (`bot/stock.py:16,17`), вызывает KeyCRM (`bot/stock.py:93`), пишет в
  БД (`bot/stock.py:22`), рассылает сообщения (`bot/stock.py:60,64`).
- Межхендлерные рёбра: `bot/handlers/menu.py:23,24` → delivery/orders,
  `bot/handlers/settings.py:13` → onboarding.

**Самые импортируемые модули:** `bot/i18n.py` — 13 импортёров, `bot/db.py` — 12,
`bot/config.py` — 10, `bot/analytics.py` — 10.

**Мёртвое:** `DeliveryAction` (`bot/callbacks.py:57`) не используется нигде —
единственное вхождение в репозитории это само определение класса.
`bot_username` (`bot/config.py:15`) не читается нигде, хотя задан в `.env`.

**Особый случай:** `bot/handlers/delivery.py` не содержит `Router` и ни одного
декоратора — это модуль-экран, его `delivery_screen` (`bot/handlers/delivery.py:77`)
вызывается из `bot/handlers/menu.py:72`. Остальные 9 файлов `bot/handlers/` —
роутеры, все 9 зарегистрированы (`bot/__main__.py:113-121`).

---

## 3. Внешние API

### 3.1. Общее для трёх клиентов

- **Ретраев с backoff нет ни одного.** Единственный `asyncio.sleep` во всех трёх
  файлах — `bot/services/keycrm.py:209`, и это троттлинг между страницами, а не
  backoff. Retry-with-backoff в проекте существует только для Telegram:
  `bot/handlers/broadcast.py:81`, `bot/stock.py:79`.
- **Заголовок `Retry-After` не читается нигде** в репозитории.
- **HTTP-статус проверяется явно ровно один раз:** `bot/services/keycrm.py:167`
  (`== 429`), и то без действия, кроме записи в лог.
- **Клиент создаётся на каждый вызов:** `bot/services/keycrm.py:160,190,227`,
  `bot/services/shopify.py:141`, `bot/services/novaposhta.py:86`. Ни `httpx.Limits`,
  ни `AsyncHTTPTransport(retries=...)`, ни `httpx.Timeout(connect=…, read=…)` не
  используются нигде.
- **Ошибка внешнего API возвращается как пустой результат**, неотличимый от «данных
  нет»: `[]` (`keycrm.py:175`, `shopify.py:153,157,165`), `{}` (`keycrm.py:212`),
  `None` (`keycrm.py:247`, `novaposhta.py:95,110,113`).
- **Исключение из парсера уходит мимо `except`.** `except httpx.HTTPError` не ловит
  `KeyError`/`ValueError`/`AttributeError` из list-comprehension: `keycrm.py:171`,
  `shopify.py:161`. Вызывающий получает их через
  `asyncio.gather(return_exceptions=True)` (`bot/handlers/orders.py:375`,
  `bot/handlers/onboarding.py:80`) и отбрасывает без лога — ветки `else` у
  `if not isinstance(result, Exception)` нет (`orders.py:378,394`).

### 3.2. KeyCRM — `bot/services/keycrm.py`

- `GET /order` (`:161`) с `include=buyer,products,status,shipping`,
  `filter[buyer_phone]`, `limit=50` (`:153-157`), таймаут 10 с (`:165`).
  **Пагинации нет**, параметр `page` не отправляется, `last_page` не читается —
  заказы за пределами первых 50 теряются молча.
- `GET /offers/stocks` (`:193`) с `limit=50, page` — **пагинация есть**
  (`:191-208`), пауза 0.55 с между страницами (`:14,:209`), выход по `last_page`
  (`:206`). Отсутствие `last_page` в ответе неотличимо от «каталог влез на одну
  страницу» (`or 1` на `:206`).
- `GET /order` с `limit=1` для профиля покупателя (`:228`).
- Поля без fallback, дающие `KeyError`/`ValueError` наружу: `p["name"]`,
  `p["quantity"]` (`:63`), `raw["id"]` (`:86`), `float(raw.get("grand_total", 0))`
  (`:89`). `raw.get("status", {}).get("name", …)` (`:66`) падает `AttributeError`,
  если `status` присутствует и равен `null` — в отличие от `buyer`/`shipping`,
  где стоит `or {}` (`:68,:72`).
- Чистые функции, тестируемые без сети: `normalize_phone_for_keycrm` (`:47`),
  `_parse_order` (`:58`), `keycrm_order_to_dict` (`:105`). Разбор конверта
  (`data["data"]`) и весь разбор стоков сидят инлайн в сетевых методах
  (`:170-171`, `:199-208`) — без мока httpx не тестируются.
- `response.json()` не покрыт `except` в `:170` и `:235` (`ValueError` уходит наружу);
  покрыт в `:198` (через `:210`).
- `bot/services/keycrm.py:211` логирует `page`; при исключении до `page = 1`
  (`:191`) даст `UnboundLocalError` поверх исходной ошибки.

### 3.3. Shopify — `bot/services/shopify.py`

- Один `POST` на `/admin/api/2025-01/graphql.json` (`:124`, `:142`), версия API
  захардкожена. Таймаут 10 с (`:146`).
- **Пагинации нет ни на одном уровне**: `customers(first: 1)` (`:12`),
  `orders(first: 50)` (`:17`), `lineItems(first: 10)` (`:28`). `pageInfo`/
  `hasNextPage` не запрашиваются нигде.
- Запрошенные, но не читаемые поля: `customers.node.id` (`:15`), `displayName` (`:16`).
- Поля доставки (`shippingAddress`, `fulfillments`, `trackingInfo`) не
  запрашиваются вообще, поэтому `shopify_order_to_dict` проставляет
  `tracking_code`/`delivery_city`/`receive_point`/`recipient_name` пустыми
  константами (`:108-112`).
- GraphQL-ошибка (HTTP 200 + `errors`) логируется и даёт `[]` (`:151-153`); код
  `extensions.code` (например `THROTTLED`) не читается, ретрая нет.
- Три разные ситуации сводятся к одному `[]`: GraphQL errors (`:153`), покупатель
  не найден (`:157`), HTTP-ошибка (`:165`).
- Чистые функции: `_parse_shopify_order` (`:57`), `shopify_external_id` (`:80`),
  `shopify_order_to_dict` (`:92`). Распаковка GraphQL-конверта (`:151-161`) —
  инлайн внутри `async with httpx.AsyncClient`.

### 3.4. Nova Poshta — `bot/services/novaposhta.py`

- Один `POST` на `https://api.novaposhta.ua/v2.0/json/` (`:9`, `:68`), метод
  задаётся полями тела (`:58-67`). Заголовки не задаются вообще; аутентификация —
  поле `apiKey` **в теле** (`:59`).
- Массив `Documents` всегда содержит ровно один элемент (`:62-66`) — батчинг API
  не используется. `track_many` (`:115-124`) итерирует ТТН **последовательно**,
  без `gather`, создавая новый httpx-клиент на каждую посылку (`:86`).
- Из телефона удаляется только `+` (`:64`); пробелы, дефисы и скобки — нет, в
  отличие от `normalize_phone_for_keycrm` (`bot/services/keycrm.py:47-55`).
- Мультиключевой failover (`:88-92`) с кэшем «какой ключ сработал для ТТН»
  (`:45`, запись `:91`, чтение `:49`). Кэш не ограничен по размеру.
- **Failover не работал на ошибках ответа — исправлено в `4a9b4f7`.** Было:
  `raise_for_status()` (`:69`) выбрасывал исключение из `_track_with` наружу
  цикла по ключам, поэтому при 429/5xx на первом ключе остальные не пробовались
  и `except` давал `None`, хотя докстринг (`:37-40`) обещает failover именно
  для rate-limited ключа. Стало: `httpx.HTTPStatusError` перехватывается внутри
  цикла. Транспортные ошибки намеренно оставлены снаружи — все ключи бьют в
  один и тот же `API_URL`, и перебор умножал бы 10-секундный таймаут на число
  ключей внутри Telegram-хендлера.
- Поля ответа `errors`/`warnings`/`messageCodes` не читаются — причина отказа от
  Nova Poshta не попадает в лог никогда (`:71`, `:94`).
- **Чистой функции парсинга нет вообще** — единственный из трёх клиентов без
  отделимого парсера. Конструирование `TrackingStatus` (`:97-106`) сидит внутри
  `async with httpx.AsyncClient` в `track()`.
- Отсутствие ответа неотличимо от отсутствия ключей: `bot/handlers/delivery.py:114`
  → `_format_delivery_block(row, None, t)` → ветка `else` (`:66-73`) молча
  показывает данные из CRM-кэша в той же вёрстке, что живой трекинг.

---

## 4. Слой данных

**Весь SQL — в `bot/db.py`.** Grep по `execute|SELECT|INSERT|UPDATE|DELETE|
aiosqlite|commit` по всему `bot/` даёт вне `bot/db.py` только текстовые совпадения
в комментариях (`bot/handlers/orders.py:326,580`, `bot/__main__.py:83`). Импортов
`aiosqlite` вне `bot/db.py:9` нет. Это единственная граница слоя, которая сегодня
соблюдается полностью.

**Таблиц девять** (`bot/db.py:325-333`): `users` (`:35`), `opt_out` (`:61`),
`orders` (`:77`), `broadcast_jobs` (`:112`), `broadcast_targets` (`:123`),
`events` (`:139`), `stock_levels` (`:152`), `stock_subscriptions` (`:162`),
`discount_requests` (`:176`).

**Ключ заказа:** `UNIQUE(chat_id, source, source_order_id)` — `bot/db.py:114`.
`source` входит в ключ, ключ уникален внутри чата, а не глобально.
`chat_id INTEGER NOT NULL REFERENCES users(chat_id)` — `bot/db.py:95`.

**Миграции версионированы через `PRAGMA user_version`** (чтение `:257`, запись
`:267`, `SCHEMA_VERSION = 2` на `:201`). Список — `:249-252`, драйвер — `:255-269`.
ALTER'ы защищены не `try/except`, а проверкой наличия колонки через
`PRAGMA table_info` (`:205`, `:212`). Вокруг миграций нет ни одного `try/except` —
исключение всплывает из `init_db()` намеренно (комментарий `:186-199`).

**Транзакционность миграций была слабее, чем заявлено в докстринге —
исправлено в `424f837`.** Было: докстринг `bot/db.py` утверждал, что миграция
идёт внутри одной транзакции и провал оставляет базу нетронутой, тогда как
sqlite3 в legacy-режиме открывает неявный BEGIN только перед DML, но не перед
DDL и не перед PRAGMA (проверено на `:memory:`: после `CREATE TABLE`
`in_transaction=False`, после `INSERT` — `True`). Падение на `INSERT ... SELECT`
оставляло пустую `orders_migrated`, повторный запуск падал на «table already
exists», и `restart: always` превращал это в крэш-луп без возможности отката —
образ собирается на боевой машине.

Стало: `_connect(transactional=True)` открывает соединение с
`autocommit=False`, и весь блок схемы в `init_db()` коммитится целиком или
никак; WAL вынесен на отдельное соединение, потому что SQLite отказывается
входить в WAL из транзакции («cannot change into wal mode from within a
transaction» первым же оператором, и молча возвращает `delete` после любого
другого); миграция 2 сносит `orders_migrated` перед созданием — `IF NOT EXISTS`
сам по себе оставил бы частично скопированную таблицу и удвоил бы строки.
Проверено убийством процесса на середине копирования: `user_version` остаётся 1,
все 25 строк и их `id` целы, следующий старт мигрирует сам.

**Внешние ключи объявлены (`:80`, `:125`), но не проверяются:** `PRAGMA foreign_keys`
не встречается в `bot/` нигде, в SQLite он выключен по умолчанию и задаётся
на соединение.

**Соединения:** пула нет. `_connect()` (`:20-33`) открывает и закрывает новое
`aiosqlite`-соединение на каждый вызов — 30 отдельных `async with _connect()` в
файле. `PRAGMA busy_timeout = 5000` (`:17`, `:30`) на каждом соединении,
`journal_mode = WAL` один раз в `init_db` (`:283`). `row_factory` выставляется
per-call в четырёх местах (`:408`, `:522`, `:538`, `:615`).

**Конкатенация в SQL:** есть в девяти местах (`:30`, `:205`, `:213`, `:242`,
`:267`, `:299`, `:508-509`, `:816-817`, плюс `.format` на `:107` и `:236`). Во всех
подставляются только имена таблиц/колонок из констант, счётчики плейсхолдеров и
int-константы. **Пользовательские данные в текст запроса не попадают нигде** —
все идут через `?`. Склеек через `+` нет.

**Запросы без подходящего индекса** (индексы — `:306,310,316,319,323,327`):
`ORDER BY ordered_at DESC` при `WHERE chat_id=?` (`:523`, `:539`);
`MAX(synced_at) WHERE chat_id=?` (`:552`); `NOT IN (SELECT chat_id FROM opt_out)`
— полный проход по `users` (`:453`, `:578`); `broadcast_jobs WHERE status='running'`
(`:616`); `events WHERE created_at >=` без `event` (`:667`); предикаты
`json_extract(...)` (`:704`); `discount_requests WHERE chat_id=? AND created_at>=`
— у таблицы нет ни одного индекса кроме rowid-PK (`:841`).

**Транзакции.** В одной транзакции: `init_db` (все CREATE + миграции + индексы,
commit `:331`), `upsert_orders` (N вставок + `_DELETE_SHADOWED`, commit `:516`),
`create_broadcast_job` (job + targets, commit `:584`), `save_stock_levels` (`:777`),
`clear_subscriptions` (`:831`).

Логически связанные записи **без** общей транзакции:

- `mark_target(..., "blocked")` и `opt_out_user(chat_id)` —
  `bot/handlers/broadcast.py:78-79` и `:86-87`, разные соединения. Падение между:
  получатель помечен `blocked` в этой рассылке, но не в `opt_out`, и следующая
  рассылка возьмёт его снова (`bot/db.py:615`).
- `save_user` → повторный `save_user` с профилем → `upsert_orders` —
  `bot/handlers/onboarding.py:106,113,93`, три транзакции. Падение между `:106` и
  `:113` оставляет `full_name`/`email` пустыми навсегда (до следующего обогащения),
  потому что `INSERT OR REPLACE` (`bot/db.py:391`) пишет `NULL` в отсутствующие поля.
- `save_stock_levels` (`bot/stock.py:102`) коммитится **до** рассылки уведомлений
  (`:120-131`) и до `clear_subscriptions` (`:133`). Падение между: новый уровень
  уже записан как база, поэтому переход «0 → есть» на следующем проходе не
  увидится, и о конкретно этом возврате товара не узнает никто.
- `recent_discount_request` (`bot/handlers/orders.py:589`) и `add_discount_request`
  (`:591`) — check-then-act через два соединения, уникального ограничения на
  `discount_requests` нет (`bot/db.py:191-198`).
- `bot/analytics.py:37` — `track()` не ждёт записи (`spawn(_write(...))`),
  ошибка глотается на `:27-28`. Аналитическое событие никак не связано
  транзакцией с бизнес-записью, которую описывает.

**Идентификатор заказа в callback-данных нестабилен.** `orders.id` — AUTOINCREMENT
(`bot/db.py:94`), в `_ORDER_COLUMNS` (`:460-466`) его нет, запись идёт
`INSERT OR REPLACE` (`:508`), который при конфликте удаляет строку и вставляет
новую с новым `id`. Этот же `id` кладётся в callback-данные (`bot/callbacks.py:34,40`)
и сравнивается с `row.get("id")` (`bot/handlers/orders.py:188,225`). Миграция 2
специально сохраняет `id` при перестроении таблицы (`bot/db.py:255-258`); обычный
рефреш — нет.

---

## 5. Состояние в памяти и жизненный цикл

**Инвентарь того, что не переживает рестарт и не видно другому процессу:**

| Что | Где | Что ломается при трёх процессах |
|---|---|---|
| `_background_tasks: set[Task]` | `bot/tasks.py:16` | `drain()` (`:37`) ждёт только свои задачи |
| `_send_lock = asyncio.Lock()` | `bot/handlers/broadcast.py:41` | три процесса войдут в `run_broadcast_job` (`:109`) одновременно; заявленный лимит ~20 msg/s (`:39-40`) станет ~60 |
| `_refresh_semaphore = Semaphore(10)` | `bot/handlers/orders.py:37` | потолок обращений к KeyCRM/Shopify станет 30, а не 10 |
| FSM `MemoryStorage` | неявно, `bot/__main__.py:55` | у каждого процесса свой словарь состояний |
| `stock_watcher` | `bot/__main__.py:84,95` | `@dp.startup()` выполнится в каждом процессе → три независимых свипа KeyCRM каждые 15 минут и тройные уведомления |
| `NovaPoshtaClient._key_for_ttn` | `bot/services/novaposhta.py:45` | три независимых кэша, каждый перебирает ключи заново |
| `SHOP_TZ` | `bot/quiet.py:35` | при разной tzdata фолбэк `:32` сработает не везде, и одна рассылка уйдёт частью со звуком, частью без |
| клиенты в `dp[...]` | `bot/__main__.py:58-78` | `load_config()` читает `config.yaml` на старте, правка без рестарта всех процессов даст разные значения |

**FSM: `storage` при создании `Dispatcher` не передаётся** — `bot/__main__.py:55`,
`dp = Dispatcher()`. Во всём репозитории нет ни одного вхождения `MemoryStorage`,
`RedisStorage`, `storage=`. Значит работает дефолт aiogram 3.25 —
`storage or MemoryStorage()`, словарь в RAM; `events_isolation` тоже дефолтный
(`DisabledEventIsolation`).

Конкретные последствия рестарта, по коду:

- Админ, стоявший на `BroadcastStates.waiting_confirm` (`bot/handlers/broadcast.py:175`),
  жмёт «✅ Так» — фильтр `:182` не совпадает, `callback.answer()` не вызывается,
  у админа висит спиннер.
- Клиент в `OnboardingStates.waiting_phone` (`bot/handlers/common.py:82`) жмёт
  «поделиться номером» — контакт не попадёт ни в `process_contact`
  (`bot/handlers/onboarding.py:135`), ни в `reject_typed_phone` (`:166`), оба
  привязаны к состоянию. Регистрации не происходит.
- `broadcast_text` читается двумя способами: `.get` на `bot/handlers/broadcast.py:204`
  и прямым `data["broadcast_text"]` на `:230` — второй даст `KeyError`.

**Фоновые задачи — все через `bot.tasks.spawn`**, прямых `asyncio.create_task` в
проекте нет ни одного (единственное вхождение — `bot/tasks.py:22`). Порождения:
`bot/__main__.py:95` (stock watcher), `bot/handlers/broadcast.py:132,142`,
`bot/handlers/orders.py:446`, `bot/analytics.py:37`.

**Что доигрывается после рестарта:** только рассылки (`bot/__main__.py:91` →
`bot/handlers/broadcast.py:128-135`, фильтр `status='pending'` на уровне SQL,
`bot/db.py:625-633`). **Что теряется:** FSM целиком, фоновое обновление заказов
(`bot/handlers/orders.py:446` — нигде не помечено), аналитические события в полёте
(`bot/analytics.py:37`), кэш ключей НП, позиция в свипе остатков.

**Возобновление рассылки не имеет захвата.** `get_unfinished_broadcasts()`
(`bot/db.py:649-657`) отдаёт все `status='running'` без claim, `get_pending_targets`
(`:588`) — read-only. Два процесса, стартовавшие с одной БД, прочитают один и тот
же список и отправят всё дважды.

**Отправка и отметка — разные транзакции.** `bot.send_message`
(`bot/handlers/broadcast.py:75`) и `mark_target` (`:76`). Обрыв между ними
оставляет получателя `pending`, `resume_broadcasts` отдаст его снова, человек
получит текст дважды. То же для ретрая после 429 (`:83`).

**Graceful shutdown:** `drain(timeout=8.0)` (`bot/tasks.py:37`, вызов
`bot/__main__.py:105`) использует `asyncio.wait`, который **не отменяет** задачи по
таймауту — они остаются запущенными, а aiogram сразу закрывает сессию бота, и их
`send_message` падает. `stock_watcher.cancel()` (`bot/__main__.py:104`) идёт до
`drain()`. `stop_grace_period: 20s` (`docker-compose.yml:16`), `CMD` в exec-форме
(`Dockerfile:28`), SIGTERM доходит до Python напрямую. `await asyncio.sleep(e.retry_after)`
(`bot/handlers/broadcast.py:81`, `bot/stock.py:79`) ничем не ограничен и при
большом `retry_after` переживёт и `drain`, и весь grace period.

---

## 6. Конфиг, деплой, наблюдаемость

**Конфиг** — два источника: `.env` через pydantic-settings (`bot/config.py:14-28`,
`env_file` на `:57-60`) и `config.yaml` (`bot/config.py:80`, путь относительно CWD).
Обязательные: `BOT_TOKEN` (`:14`), `KEYCRM_API_KEY` (`:16`), плюс три ключа yaml,
читаемые через `yaml_data[...]` без дефолта (`:91-93`). Один параметр читается
мимо конфига — `BOT_DB_PATH` через `os.getenv` на уровне модуля (`bot/db.py:14`);
`python-dotenv` в `bot/` не импортируется нигде, поэтому локально этот параметр из
`.env` не подхватится, а в Docker работает через `env_file` (`docker-compose.yml:9`).

**Деградация при отсутствующем ключе:** `BOT_TOKEN`/`KEYCRM_API_KEY` — процесс не
стартует (`ValidationError` из `bot/config.py:86`, вызов не обёрнут —
`bot/__main__.py:39`), с `restart: always` (`docker-compose.yml:6`) это бесконечный
цикл рестартов. `NOVAPOSHTA_*` и `SHOPIFY_*` — тихая деградация:
`dp["novaposhta"] = None` (`bot/__main__.py:67`), `dp["shopify"] = None` (`:72`),
пользователь не видит ни ошибки, ни пометки. `ADMIN_USER_IDS` с нечисловым
значением — `ValueError` из ленивого property (`bot/config.py:58`) в startup-хуке
(`bot/__main__.py:93`), поллинг не запустится.

**Логгер не конфигурировался нигде — исправлено в `2a61ece`.** Было: 0
совпадений по `logger.add` / `logger.remove` / `logger.configure` / `LOGURU_*`,
то есть дефолты loguru 0.7.3 — уровень DEBUG, цветной формат с ANSI внутри
json-file-лога Docker, `backtrace=True` и **`diagnose=True`**, печатающий
значения локальных переменных в каждом трейсбеке.

Стало: `bot/logs.py` ставит один sink на stderr, уровень из `LOG_LEVEL`
(по умолчанию INFO), `backtrace=False`, `diagnose=False` и маскирование
украинских номеров в **готовой строке**, а не в `record["message"]` —
маскировать надо и текст исключения, куда httpx кладёт URL с
`filter[buyer_phone]`. `setup_logging()` вызывается до `load_config()`, чтобы
падение при чтении секретов не печатало их. `_refresh_orders` теперь принимает
`chat_id` и достаёт телефон внутри, а не получает его аргументом задачи.
Проверено: прогон с явным логом номера, номером внутри исключения, упавшей
фоновой задачей и тремя написаниями одного номера не оставляет ни одной цифры —
семь вхождений схлопываются в одну стабильную маску, `chat_id` и id заказа
Shopify целы.

Логи stdlib `logging` (aiogram, httpx) по-прежнему никем не перехватываются —
`logging.` в `bot/` 0 совпадений, `InterceptHandler` отсутствует.

**Персональные данные в логах.** Телефон в открытом виде — 7 мест:
`bot/handlers/onboarding.py:118,124` (уровень DEBUG, который в проде включён),
`bot/services/keycrm.py:168,174,246`, `bot/services/shopify.py:152,164`. В
`keycrm.py:174` номер попадает дважды — явным аргументом и внутри текста
`HTTPStatusError`, который включает URL с `filter[buyer_phone]`. ТТН — 3 места
(`bot/services/novaposhta.py:118,133,136`). `chat_id` — 11 мест. Тексты сообщений
не логируются нигде. Косвенный канал: `diagnose=True` печатает локальные
переменные, а задача `refresh_orders` спавнится с аргументами `(chat_id, phone)`
(`bot/handlers/orders.py:446`) и логируется при падении через
`logger.opt(exception=exc)` (`bot/tasks.py:34`); задача `broadcast_job_*`
(`bot/handlers/broadcast.py:132,142`) несёт текст рассылки. Маскирования нет
нигде. Всё уходит в json-file, до 50 МБ на диске (`docker-compose.yml:17-21`).

**Деплой** (`.github/workflows/deploy.yml`): push в `master` (`:3-5`) с
`paths-ignore` на `**.md` и `.planning/**` (`:7-9`), `concurrency` без отмены
(`:13-15`), `rsync -az --delete` всего чекаута на сервер (`:32-40`) с исключением
`.env`, `data`, `backups`, `deploy/backup.env`, `*.db*` (`:36-38`), затем
**сборка образа на боевой машине**: `docker compose up -d --build` (`:44`).
Проверка результата есть — `docker inspect -f "{{.State.Status}}"` через 5 секунд
(`:52-56`); строка «Bot started successfully» (`bot/__main__.py:96`) печатается в
логи (`:55`), но не проверяется. **Отката нет:** шагов с `if: failure()` или
пересборкой предыдущего SHA в файле нет, образ всегда один тег `ks-tg-bot`
(`docker-compose.yml:4`), предыдущий после успешной сборки становится dangling.

**При неудачной сборке** контейнер остаётся жив на старом образе (кода в
контейнере нет по bind-mount, он вшит в образ — `Dockerfile:15-16`), но на диске
сервера уже лежит новый код: рабочее дерево и работающий контейнер расходятся,
пометки об этом нет.

**Healthcheck отсутствует** и в `docker-compose.yml` (24 строки), и в `Dockerfile`
(28 строк). Единственная проверка живости — разовая, в CI. Зависший процесс
`restart: always` не детектирует.

**Секреты в образ не попадают** — `Dockerfile` копирует только `requirements.txt`
(`:11`), `bot/` (`:15`) и `config.yaml` (`:16`), плюс `.dockerignore:10` исключает
`.env`. История git чиста: `.env` фигурирует в двух коммитах, но блоб нулевого
размера в обоих. Процесс в контейнере идёт от непривилегированного `ksbot`
(`Dockerfile:23-26`).

**В отслеживаемых git файлах в открытом виде:** `config.yaml:3` —
`support_chat_id: 129462784`; `.env.example:10` — два реальных Telegram user id;
`CHANGELOG.md:300` — IP продакшена. Отдельно, **вне git** (не в чекауте CI):
`.claude/settings.local.json:55,59,60` содержит реальный телефон
`+380660146763` в аргументах отладочных запусков, `:31` — ручную rsync-команду
без исключения `.env`.

**Бэкапы есть и трёхуровневые** (`deploy/backup.sh`): снапшот `sqlite3 .backup`
внутри контейнера с `integrity_check` (`:129-142`) и проверкой непустоты `users`
(`:146-153`), копия на хост (`:159-163`), off-site rsync на Storage Box (`:174-176`),
ретеншн 14 (`:29`), Telegram-алерт при провале (`:112-122`), учебное восстановление
`deploy/restore-test.sh`. Расписание задаётся только текстом в `deploy/DEPLOY.md:117-122`,
ни compose, ни workflow его не ставят. `CHANGELOG.md:129-132` фиксирует, что
off-site до сих пор не настроен.

**`deploy/DEPLOY.md` расходится с workflow в четырёх местах:** описывает доставку
кода через `git clone`/`git pull --ff-only` (`:44-48`, `:156-158`) вместо rsync
(`.github/workflows/deploy.yml:32-40`) — при этом `.git` на сервере сохраняется
(`--exclude '.git'`) и молча устаревает; требует Deploy key (`:34-42`), который
workflow не использует; не упоминает автодеплой вообще; требует проверять строку
в логах (`:67-68`), тогда как workflow проверяет только `State.Status`.

---

## 7. Расхождения между `docs/architecture.md` и кодом

Не чинится. Выписано, чтобы `docs/components.md` знал, что переезд, а что
переименование.

### 7.1. Контракты документа, которых в коде нет

| Документ | Код |
|---|---|
| §1, §2: «бот в сеть за данными не ходит», читает только свою базу | Хендлеры вызывают KeyCRM/Shopify/НП напрямую: `bot/handlers/orders.py:369,371`, `onboarding.py:74,76,111`, `delivery.py:106` |
| §3.1: `merge_key`, `source` — атрибут, `UNIQUE(merge_key)` глобально | `UNIQUE(chat_id, source, source_order_id)` — `bot/db.py:114`; `source` в ключе, ключ per-chat |
| §3.1: `source_rank` в строке | Колонки нет — `bot/db.py:92-116` |
| §3.2: сырой ответ API в JSONB отдельно от разобранного | Колонки `raw` нет ни в одной таблице; в `orders` только разобранные поля (`bot/db.py:92-116`) |
| §3.3: суррогатный `users.id`, все ссылки на него | `chat_id INTEGER PRIMARY KEY` (`bot/db.py:52`); все таблицы ссылаются на `chat_id` |
| §3.3: `phone_normalized` уникален | `phone TEXT NOT NULL` без UNIQUE (`bot/db.py:53`); нормализованной колонки нет |
| §3.3: все метки `timestamptz`, внутри UTC | Все метки `TEXT` через `datetime('now')` (`bot/db.py:54,113,…`); сравнение с `datetime.utcnow()` (`bot/handlers/orders.py:335`) |
| §3.4: RLS | Неприменимо к SQLite; разделение по `chat_id` в каждом запросе вручную |
| §4.2: `sync_state`, курсор с перехлёстом | Таблицы нет; синхронизация — по запросу пользователя, по телефону (`bot/handlers/orders.py:362-375`) |
| §4.4: `orders.user_id` nullable, привязка по телефону | `chat_id INTEGER NOT NULL REFERENCES users(chat_id)` (`bot/db.py:95`) — заказ не может существовать без чата |
| §4.5: алерт на отсутствие успеха синка | Ни `sync_state`, ни алертов; единственный алертинг в проекте — Telegram-уведомления бэкапа (`deploy/backup.sh:112-122`) |
| §5: outbox с арендой, `dedup_key`, `not_before` | Таблицы нет. Рассылка шлёт напрямую (`bot/handlers/broadcast.py:75`), уведомления о стоке — тоже (`bot/stock.py:60,64`) |
| §5.5: `users.notify_from` | Колонки нет (`bot/db.py:61-74`) |
| §8: Alembic | Собственный драйвер на `PRAGMA user_version` (`bot/db.py:226,280-300`) |
| §8: сборка в GHCR, на сервере только `pull` | Сборка на боевой машине (`.github/workflows/deploy.yml:44`) |
| §8: `/healthz` и `/readyz` в healthcheck компоуза | Healthcheck отсутствует в обоих файлах |
| §8: пять пунктов тестов | Тестов ноль |
| §9: `VerifiedPhone` как тип | `own_contact_phone(message) -> str | None` (`bot/handlers/onboarding.py:50`), `normalize_phone(raw) -> str | None` (`:28`) — обычный `str`, инвариант держится расположением кода |

### 7.2. Где код опережает документ

- §8 требует убрать «паттерн `ALTER` в `try/except`». **Уже убрано:** миграции
  версионированы (`bot/db.py:226,286-300`), ALTER'ы идут по проверке
  `PRAGMA table_info` (`:205,212`), `try/except` вокруг миграций нет вовсе
  (комментарий `:186-199` фиксирует, что так было раньше).
- §5.1 перечисляет «тихие часы через `not_before`» как то, что даст будущий
  outbox. **Тихие часы уже работают** без outbox: `bot/quiet.py:38`,
  используется в `bot/handlers/broadcast.py:73` и `bot/stock.py:55`, через
  `disable_notification` (`broadcast.py:75,83`, `stock.py:60,64`).

### 7.3. Чего в документе нет вообще, а в коде есть

Пять подсистем, не упомянутых ни в одном разделе `docs/architecture.md`, и для
них в целевом контракте нет модуля:

| Подсистема | Код | Таблицы |
|---|---|---|
| Продуктовая аналитика | `bot/analytics.py`, 10 импортёров; агрегаты `bot/db.py:701-768` | `events` (`bot/db.py:154`) |
| Мониторинг остатков и подписка «сообщить о поступлении» | `bot/stock.py` (151 строка), вечный цикл `:141-151` | `stock_levels` (`:152`), `stock_subscriptions` (`:162`) |
| Запрос скидки | `bot/handlers/orders.py:589-619` | `discount_requests` (`:176`) |
| Локализация ru/uk | `bot/i18n.py` (308 строк), `bot/middlewares.py`, 13 импортёров | `users.language` (`bot/db.py:67`) |
| Демо-режим для админа | `bot/handlers/demo.py` (141 строка) | использует `orders` с отдельным `source` |

Плюс инфраструктурные модули без места в целевом списке: `bot/screen.py`
(редактирование сообщений), `bot/profile.py` (публикация команд бота),
`bot/quiet.py` (тихие часы), `bot/keyboards.py`, `bot/callbacks.py`, `bot/states.py`.

### 7.4. Ограничение, которое документ не формулирует

Документ §2 требует три процесса и §10 явно отказывается от Redis. При этом FSM
живёт в `MemoryStorage` (`bot/__main__.py:55`), а long-polling по одному токену
(`bot/__main__.py:125`) отдаёт апдейт ровно одному потребителю. Значит **процесс
`bot` обязан оставаться в одном экземпляре**; на `web` и `worker` это ограничение
не распространяется, потому что FSM у них нет. В документе это условие не
записано ни в §2, ни в §10.

---

## 8. Не проверено

- Реальные форматы ответов KeyCRM, Shopify Admin GraphQL и Nova Poshta. Фикстур
  в репозитории нет, сети у агентов не было. Имена полей (`global_source_uuid`,
  `source_uuid`, `last_page`, `StatusCode`, `shipping_address_city`)
  подтверждаются только комментариями в коде (`bot/services/keycrm.py:29-33,75-78`).
- Поддерживает ли KeyCRM фильтр по `updated_at` — в коде используется только
  `filter[buyer_phone]` (`bot/services/keycrm.py:155`).
- Возвращает ли KeyCRM ошибки со статусом 200 (это определяет, маскирует ли
  `data.get("data", [])` на `:171` тело ошибки).
- Может ли KeyCRM отдать один sku в нескольких оферах (`:205` присваивает, а не
  суммирует).
- Фактическое распределение апдейтов между несколькими процессами на одном токене
  — требует запуска.
- Реальный путь volume `botdata` на диске сервера — нет доступа к серверу.
