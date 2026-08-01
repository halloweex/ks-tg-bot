# Компонентный контракт

Целевое состояние. Решения `docs/architecture.md` (Postgres, KeyCRM как
единственный источник заказов, outbox, SSR на HTMX, отсутствие Redis, брокера и
вебхука Shopify) здесь не пересматриваются — они переводятся в границы модулей.

Текущее состояние, относительно которого написан этот документ, — в
`docs/current-state.md`. Правила зависимостей из раздела «Запрещено импортировать»
машинно проверяются конфигом `.importlinter` (раздел 13); всё, что там проверить
нельзя, вынесено в раздел 12 отдельным списком, а не спрятано.

---

## 1. Три модуля сверх списка задачи

В задаче перечислено семь модулей: `core.domain`, `core.ports`, `core.adapters`,
`core.repos`, `bot`, `web`, `worker`. Ниже описано десять: добавлены
`core.usecases`, `core.config` и `core.i18n`. Все три приняты (блок 1);
основания оставлены здесь, потому что без них правила зависимостей читаются
как произвол.

**`core.usecases`.** В списке нет модуля, где может жить оркестрация,
общая для нескольких точек входа. «Собрать список заказов пользователя» нужен и
боту, и кабинету; «синхронизировать заказы» нужен воркеру, но его правила слияния
— доменные. Если такого модуля нет, оркестрация дублируется в `bot` и `web`, что
прямо противоречит §2 документа («три образа гарантированно приведут к двум разным
парсерам ответа KeyCRM»). `core.domain` его заменить не может: оркестрация делает
ввод-вывод через порты, а домен обязан оставаться чистым.

Имя `core.usecases`, а не `core.services`: `bot/services/` уже занято под
адаптеры внешних API, и на время переезда два разных «services» в одном дереве
путали бы — особенно в сообщениях линтера, где видно только имя модуля.

**`core.config` и `core.i18n`** — листовые модули рядом с доменом. Конфиг читают
все три точки входа; тексты, по §7 документа, должны быть сведены в один модуль
для бота и веба. Класть их в `core.domain` нельзя: конфиг читает файлы и
окружение, то есть домен перестанет быть чистым.

---

## 2. Стек слоёв

Стрелка — «может импортировать». Обратное направление запрещено и проверяется.

```
        bot              web             worker          ← точки входа, независимы
         │                │                │
         └────────────────┼────────────────┘
                          ▼
                   core.usecases                          ← сценарии, только через порты
                          │
   core.adapters   core.repos                             ← реализации портов, сиблинги
         │              │      │
         └──────┬───────┘      │
                ▼              ▼
            core.ports  ──────►                           ← Protocol + словарь ошибок
                │
                ▼
   core.domain   core.config   core.i18n                  ← листья, ничего не импортируют из core
```

`core.adapters`, `core.repos` и `core.usecases` — один уровень: они не видят друг
друга. Точки входа видят всех, потому что каждая из них содержит собственный
композиционный корень, который собирает адаптеры и репозитории и передаёт их в
сценарии.

---

## 3. `core.domain`

**Зона ответственности.** Типы предметной области и чистые функции над ними.
Ноль ввода-вывода, ноль зависимостей от чего-либо в `core`.

**Публичный интерфейс.**

```python
# core/domain/phone.py
Phone = NewType("Phone", str)                # нормализован до 380XXXXXXXXX

_OWNERSHIP = object()                        # приватный ключ конструктора

@dataclass(frozen=True, slots=True)
class VerifiedPhone:
    """Номер, владение которым подтвердил Telegram.

    Не NewType: NewType существует только для проверки типов, в рантайме
    VerifiedPhone(x) это x, и голая строка проходит везде, где ждут этот тип.
    Отдельный класс с обязательным полем-ключом делает так, что случайная
    конструкция невозможна, а намеренная требует значения, которого нет ни в
    одном другом модуле.
    """
    value: Phone
    _ownership: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._ownership is not _OWNERSHIP:
            raise TypeError("VerifiedPhone строится только verified_from_contact()")

    def __repr__(self) -> str:               # чтобы номер не утёк через лог
        return f"VerifiedPhone(<phone:{_digest(self.value)}>)"

def normalize(raw: str | None) -> Phone | None: ...

def verified_from_contact(
    *, contact_user_id: int | None, sender_id: int, raw: str | None
) -> VerifiedPhone | None: ...
    # ЕДИНСТВЕННЫЙ конструктор VerifiedPhone во всём дереве.
    # Возвращает не-None только при contact_user_id == sender_id.

# core/domain/order.py
Source   = Literal["keycrm", "shopify"]
MergeKey = NewType("MergeKey", str)
SOURCE_RANK: Final[Mapping[Source, int]] = {"keycrm": 2, "shopify": 1}

def merge_key(source: Source, source_order_id: str, external_id: str | None) -> MergeKey: ...
def merge(existing: Order, incoming: Order) -> Order: ...   # правило приоритета по source_rank

@dataclass(frozen=True, slots=True)
class Order:
    merge_key: MergeKey
    source: Source
    source_order_id: str
    external_id: str | None
    external_number: str | None
    status: str
    status_group: int
    total: Decimal
    currency: str
    ordered_at: datetime            # timezone-aware, UTC
    items: tuple[OrderItem, ...]
    buyer: Buyer
    shipping: Shipping | None
    source_rank: int

# core/domain/outbox.py
DedupKey = NewType("DedupKey", str)

class NotificationKind(StrEnum): ...
class AmbiguityPolicy(StrEnum):     # §5.2 документа
    RESEND     = "resend"           # статусы доставки: пропуск хуже дубля
    QUARANTINE = "quarantine"       # рассылка: дубль хуже пропуска

def dedup_key(kind: NotificationKind, user_id: UserId, subject: str) -> DedupKey: ...
def next_send_time(kind: NotificationKind, now: datetime, tz: ZoneInfo) -> datetime: ...  # тихие часы

# core/domain/stock.py
def restocked(previous: Mapping[Sku, int], current: Mapping[Sku, int]) -> frozenset[Sku]: ...

# core/domain/replenishment.py
def prior_duration_days(step: CareStep, volume_ml: int | None) -> int: ...
def expected_runout(last_purchase: date, prior_days: int, personal_gaps: Sequence[int]) -> date: ...
    # смешивание с весом n / (n + 2), §6.2 документа
```

**Запрещено импортировать.**

| Что | Почему |
|---|---|
| `core.ports`, `core.adapters`, `core.repos`, `core.usecases` | домен — низ стека; импорт вверх делает цикл и тянет ввод-вывод в чистые функции |
| `bot`, `web`, `worker` | то же, плюс домен должен собираться без aiogram и FastAPI |
| `httpx`, `aiogram`, `sqlalchemy`, `asyncpg`, `fastapi`, `jinja2` | функции этого модуля обязаны тестироваться без сети, БД и моков — единственный слой, где это гарантируется |

**Инварианты.**

1. `verified_from_contact` — единственный способ получить `VerifiedPhone`, и это
   держится в рантайме, а не только проверкой типов. Проверено на python 3.14.2:
   `VerifiedPhone("380…")` падает с «missing 1 required positional argument»,
   `VerifiedPhone("380…", object())` — с «строится только
   verified_from_contact()», значение после создания не меняется, равенство
   идёт по номеру. Остаётся один обход — `object.__setattr__` на готовом
   объекте; это уже не ошибка, а намеренное действие.
2. `merge_key` — единственное место, где живёт правило идентичности заказа.
3. `merge` — единственное место, где живёт правило приоритета источников.
4. Все `datetime` в сигнатурах — timezone-aware. Naive-время в домен не входит.

---

## 4. `core.config` и `core.i18n`

**Зона ответственности.** `core.config` — чтение настроек из окружения и файла,
один типизированный объект на процесс. `core.i18n` — все пользовательские тексты
и выбор локали, общие для бота и кабинета.

**Публичный интерфейс.**

```python
# core/config.py
@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    bot_token: SecretStr
    keycrm_api_key: SecretStr
    novaposhta_keys: tuple[SecretStr, ...]
    shopify: ShopifyCreds | None
    admin_ids: frozenset[int]
    shop_tz: ZoneInfo
    brand: BrandSettings

def load() -> Settings: ...    # единственная точка чтения окружения во всём дереве

# core/i18n.py
Lang = Literal["uk", "ru"]

def t(key: str, lang: Lang, /, **kwargs: object) -> str: ...
```

**Запрещено импортировать.** Всё остальное из `core`, плюс `bot`, `web`, `worker`
и любой транспорт. Причина: это листья, их импортирует всё дерево, и любая их
зависимость становится зависимостью всего дерева.

**Инвариант.** `os.environ` и `os.getenv` читаются только внутри `core.config`.

**Известное нарушение, а не исключение из правила:** `bot/db.py:14` читает
`BOT_DB_PATH` через `os.getenv` на уровне модуля. Следствие проверяемое:
`python-dotenv` в `bot/` не импортируется нигде, поэтому локально значение из
`.env` не подхватывается, а в Docker подхватывается — через `env_file`
(`docker-compose.yml:9`), который инжектит переменные по-настоящему. Устраняется
при переезде `bot/config.py` → `core/config.py` (§14, карта переезда): путь к
базе становится полем `Settings`, а `core.repos` получает его аргументом.
До тех пор греп-проверка из §13 находит ровно эту строку и ничего больше.

---

## 5. `core.ports`

**Зона ответственности.** Описание того, что нужно сценариям от внешнего мира:
Protocol-интерфейсы и словарь ошибок. Реализаций здесь нет.

**Публичный интерфейс.**

```python
# core/ports/errors.py — словарь ошибок, общий для всех адаптеров
class ExternalError(Exception): ...
class Unauthorized(ExternalError): ...              # 401/403 от внешнего API
class RateLimited(ExternalError):  retry_after: float
class Unavailable(ExternalError): ...               # 5xx, таймаут, обрыв
class MalformedResponse(ExternalError): ...         # 200, но разобрать нельзя
class RecipientBlocked(ExternalError): ...          # Telegram 403

# core/ports/sources.py
class OrderSource(Protocol):
    async def fetch_updated_since(
        self, since: datetime, *, page: int, page_size: int
    ) -> Page[RawOrder]: ...

class CatalogSource(Protocol):
    async def fetch_products(self, *, cursor: str | None) -> Page[RawProduct]: ...

class DeliveryTracker(Protocol):
    async def track(
        self, ttns: Sequence[Ttn], phone: Phone
    ) -> Mapping[Ttn, TrackingStatus | Unavailable]: ...

class Notifier(Protocol):
    async def send(self, chat_id: ChatId, text: str, *, silent: bool) -> None: ...

class Clock(Protocol):
    def now(self) -> datetime: ...

# core/ports/repos.py
class UnitOfWork(Protocol):
    orders:     OrderRepo
    users:      UserRepo
    outbox:     OutboxRepo
    shipments:  ShipmentRepo
    sync_state: SyncStateRepo
    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, *exc: object) -> None: ...

class OrderRepo(Protocol):
    async def upsert(self, orders: Sequence[Order]) -> None: ...
    async def for_user(self, user_id: UserId) -> Sequence[Order]: ...
    async def link_orphans(
        self, phone: Phone, user_id: UserId, *, not_older_than: date
    ) -> int: ...

class OutboxRepo(Protocol):
    async def enqueue(self, msg: NewOutboxMessage) -> bool: ...      # False = dedup_key занят
    async def claim(self, *, limit: int, lease: timedelta) -> Sequence[OutboxMessage]: ...
    async def mark_sent(self, message_id: int) -> None: ...
    async def defer(self, message_id: int, not_before: datetime, error: str | None) -> None: ...
    async def quarantine(self, message_id: int, error: str) -> None: ...

class SyncStateRepo(Protocol):
    async def cursor(self, source: Source) -> datetime | None: ...
    async def advance(self, source: Source, to: datetime) -> None: ...
    async def last_success(self, source: Source) -> datetime | None: ...
```

**Запрещено импортировать.**

| Что | Почему |
|---|---|
| `core.adapters`, `core.repos`, `core.usecases` | порт, знающий свою реализацию, перестаёт быть точкой подмены; тесты сценариев потянут за собой httpx |
| `httpx`, `aiogram`, `sqlalchemy`, `asyncpg` | если тип из библиотеки протёк в сигнатуру порта, подменить реализацию в тесте нельзя без этой библиотеки |
| `bot`, `web`, `worker` | порты ниже точек входа |

Разрешено: `core.domain` — сигнатуры оперируют доменными типами.

**Инварианты.**

1. **Словарь ошибок закрыт.** Адаптер обязан перевести ошибку своей библиотеки в
   один из типов `core.ports.errors`. Сценарий не видит `httpx.HTTPError`.
2. **Ошибка не превращается в пустой результат.** Порт либо возвращает данные,
   либо бросает. Сегодня нарушено во всех трёх клиентах: `[]` при 401/429/5xx
   (`bot/services/keycrm.py:175`, `bot/services/shopify.py:165`), `{}`
   (`keycrm.py:212`), `None` (`novaposhta.py:110`) — и вызывающий не может
   отличить сбой от «данных нет» (`bot/handlers/orders.py:449-458`).
3. **Пагинация — часть контракта.** `Page` несёт признак «есть ещё», поэтому
   молчаливая потеря заказов за пределами первых 50
   (`bot/services/keycrm.py:153-157`, `bot/services/shopify.py:17`) перестаёт
   быть выразимой.

---

## 6. `core.adapters`

**Зона ответственности.** Реализации портов внешних API. Единственное место в
дереве, где живут `httpx` и клиент Telegram.

**Структура и публичный интерфейс.**

```
core/adapters/
  keycrm/     client.py   parse.py
  shopify/    client.py   parse.py
  novaposhta/ client.py   parse.py
  telegram/   notifier.py
```

```python
# core/adapters/keycrm/client.py
class KeyCRMClient:                      # реализует core.ports.OrderSource
    def __init__(self, http: httpx.AsyncClient, api_key: SecretStr) -> None: ...
    async def fetch_updated_since(self, since, *, page, page_size) -> Page[RawOrder]: ...

_: OrderSource = cast(KeyCRMClient, ...)   # проверка соответствия порту, см. раздел 12.4

# core/adapters/keycrm/parse.py — чистый модуль, без httpx
def parse_order(raw: Mapping[str, object]) -> Order: ...          # бросает MalformedResponse
def parse_page(body: Mapping[str, object]) -> Page[RawOrder]: ...  # разбор конверта, включая last_page
```

**Запрещено импортировать.**

| Что | Почему |
|---|---|
| `core.repos` | адаптер, который сам пишет в базу, делает запись невидимой для транзакции сценария |
| `core.usecases` | сиблинги; иначе получится цикл «сценарий → адаптер → сценарий» |
| `bot`, `web`, `worker` | адаптер общий для трёх точек входа, зависимость от одной из них его к ней приколачивает |
| `sqlalchemy`, `asyncpg` | БД — не зона адаптеров внешних API |
| **`core.adapters.*.parse` дополнительно: `httpx`** | парсер, тянущий транспорт, требует мока сети; вся ценность отделения `parse` от `client` в том, что его тесты идут на сохранённых фикстурах |

**Инварианты.**

1. HTTP-клиент передаётся снаружи и переиспользуется. Сегодня новый
   `httpx.AsyncClient` создаётся на каждый вызов в шести местах
   (`keycrm.py:160,190,227`, `shopify.py:141`, `novaposhta.py:86`).
2. `parse.py` не импортирует `httpx` и покрыт тестами на фикстурах. Сегодня
   разбор конверта сидит внутри `async with httpx.AsyncClient`
   (`keycrm.py:170-171,199-208`, `shopify.py:151-161`), а у Nova Poshta чистого
   парсера нет вовсе (`novaposhta.py:97-106`).
3. Ретраи и чтение `Retry-After` — здесь и только здесь. Сегодня `Retry-After`
   не читается нигде в репозитории, а ретраев с backoff нет ни в одном из трёх
   клиентов.
4. Телефоны и ТТН не попадают в сообщения исключений и в логи адаптера. Сегодня
   нарушено в семи местах для телефона (в том числе
   `bot/services/keycrm.py:174`, где номер уходит и явным аргументом, и внутри
   `HTTPStatusError` с URL, содержащим `filter[buyer_phone]`) и в трёх для ТТН.

---

## 7. `core.repos`

**Зона ответственности.** Реализации репозиториев и `UnitOfWork` поверх Postgres.
Единственное место, где живут SQL и драйвер БД.

**Публичный интерфейс.**

```python
# core/repos/uow.py
class SqlUnitOfWork:                     # реализует core.ports.UnitOfWork
    def __init__(self, pool: AsyncEngine, *, user_id: UserId | None = None) -> None: ...
    # user_id задан → в начале транзакции выполняется SET LOCAL app.user_id,
    # и RLS-политики фильтруют строки. None → служебный контекст воркера.

# core/repos/orders.py, users.py, outbox.py, shipments.py, sync_state.py
class SqlOutboxRepo:
    async def claim(self, *, limit: int, lease: timedelta) -> Sequence[OutboxMessage]: ...
    # UPDATE ... SET locked_until = now() + lease, attempts = attempts + 1
    # WHERE id IN (SELECT id ... FOR UPDATE SKIP LOCKED LIMIT :limit) RETURNING *
    # Транзакция закрывается ДО отправки, §5.3 документа.
```

**Запрещено импортировать.**

| Что | Почему |
|---|---|
| `core.adapters` | репозиторий, ходящий во внешний API, делает время транзакции равным времени сетевого вызова |
| `core.usecases` | сиблинги |
| `bot`, `web`, `worker` | репозитории общие для трёх точек входа |
| `httpx`, `aiogram` | здесь нет сетевых вызовов, кроме соединения с БД |

**Инварианты.**

1. **SQL существует только здесь.** Сегодня это единственная граница, которая
   соблюдается полностью: весь SQL — в `bot/db.py`, вне его ни одного вхождения.
   При переезде она должна сохраниться, а не потеряться.
2. **Транзакция не переживает сетевой вызов.** `claim` коммитится до отправки.
3. **`SET LOCAL app.user_id` ставится в начале транзакции** для любого контекста,
   пришедшего из веба.
4. **Пул один на процесс.** Сегодня пула нет вообще: `_connect()`
   (`bot/db.py:20-48`) открывает и закрывает соединение на каждый вызов, 30 раз
   по файлу.
5. **Идемпотентность записи выражена в SQL, а не в вызывающем.** `upsert` — по
   `merge_key`, `enqueue` — по уникальному `dedup_key`.

---

## 8. `core.usecases`

**Зона ответственности.** Сценарии: последовательность шагов через порты, без
знания о том, кто их реализует и кто их вызывает.

**Публичный интерфейс.**

```python
class SyncOrders:
    def __init__(self, source: OrderSource, uow: UnitOfWork, clock: Clock) -> None: ...
    async def run(self, *, overlap: timedelta) -> SyncReport: ...

class BackfillOrders:
    async def run(self, *, since: date, resume_from: str | None) -> BackfillReport: ...

class RegisterUser:
    async def run(self, chat_id: ChatId, phone: VerifiedPhone) -> User: ...
    # link_orphans вызывается с ограничением по давности, §4.4 документа

class TrackShipments:
    async def run(self, *, batch: int) -> TrackReport: ...   # переходы → outbox.enqueue

class DispatchOutbox:
    def __init__(self, notifier: Notifier, uow: UnitOfWork, clock: Clock) -> None: ...
    async def run(self, *, batch: int, lease: timedelta) -> DispatchReport: ...

class ListOrders:
    async def run(self, user_id: UserId) -> Sequence[Order]: ...   # общий для bot и web
```

**Запрещено импортировать.**

| Что | Почему |
|---|---|
| `core.adapters`, `core.repos` | сценарий, знающий конкретную реализацию, нельзя протестировать без сети и БД; это и есть смысл существования `core.ports` |
| `bot`, `web`, `worker` | сценарий не знает, кто его вызвал |
| `httpx`, `aiogram`, `sqlalchemy`, `asyncpg`, `fastapi` | то же самое, выраженное через библиотеки |

**Инварианты.**

1. **Единственный способ отправить сообщение клиенту — `outbox.enqueue`.**
   `Notifier` внедряется ровно в один сценарий, `DispatchOutbox`.
2. **Запись в БД и постановка уведомления — одна транзакция.** Это причина, по
   которой `UnitOfWork` — порт, а не набор независимых репозиториев. Сегодня
   такой связки нет: `mark_target` и `opt_out_user` идут разными транзакциями
   (`bot/handlers/broadcast.py:78-79`), `save_stock_levels` коммитится до
   рассылки (`bot/stock.py:102` против `:120-131`).
3. **Курсор синка двигается только после успешной записи всей страницы**, окно
   берётся с перехлёстом (§4.2 документа).
4. Сценарий не ловит `Exception` широко: он различает типы из
   `core.ports.errors` и решает, что делать с каждым.

---

## 9. Точки входа: `bot`, `web`, `worker`

Общее для трёх: каждая содержит композиционный корень (`bot/main.py`,
`web/app.py`, `worker/main.py`) — единственное место в своём пакете, где
разрешено импортировать `core.adapters` и `core.repos`, собирать их и передавать
в сценарии.

### 9.1. `bot`

**Зона ответственности.** Telegram как интерфейс: роутеры, клавиатуры, FSM,
рендеринг экранов. Ни бизнес-правил, ни SQL, ни HTTP.

```python
# bot/main.py — композиционный корень
def build_dispatcher(settings: Settings) -> Dispatcher: ...

# bot/handlers/orders.py
@router.callback_query(MenuAction.filter(F.action == "orders"))
async def show_orders(callback: CallbackQuery, list_orders: ListOrders) -> None: ...
    # сценарий внедряется через dp[...], тип — из core.usecases
```

**Запрещено импортировать:** `httpx`, `sqlalchemy`, `asyncpg` (нигде в пакете);
`core.adapters`, `core.repos` (везде, кроме `bot.main`); `web`, `worker`.

Причина для `bot.handlers`: сегодня хендлеры вызывают сетевые клиенты напрямую
(`bot/handlers/orders.py:369,371`, `onboarding.py:74,76,111`, `delivery.py:106`) и
импортируют `bot.db` в 8 из 10 файлов. Пока хендлер видит конкретный клиент, его
нельзя протестировать без сети, и утверждение §1 документа «бот в сеть за данными
не ходит» остаётся комментарием.

**Инварианты.**

1. `bot` запускается **в одном экземпляре**. FSM — `MemoryStorage`
   (`bot/__main__.py:55`), long-polling по одному токену отдаёт апдейт одному
   потребителю. Документ требует три процесса и отказывается от Redis (§10), но
   это ограничение в нём не записано; здесь оно записано. Из него следуют два
   пункта ниже — они не «договорённость», а часть контракта.
2. **Диспетчер и поллинг существуют только в `bot`.** `worker` не создаёт
   `Dispatcher` и не вызывает `start_polling`. Сегодня это нарушено не в
   воркере (его нет), а в самом боте: `stock_watcher` спавнится из
   `@dp.startup()` (`bot/__main__.py:95`), то есть привязан к диспетчеру и
   поднимется в каждом процессе, который этот диспетчер создаст. При переезде
   свип уходит в `worker/jobs/stock.py`, и правило начинает выполняться по
   построению, а не по внимательности.
3. **Деплой — последовательный рестарт, а не параллельный.** Два контейнера
   `bot`, пересекшиеся хотя бы на секунду, дают второму поллеру 409 Conflict от
   Telegram, а обработчика 409 в коде нет. Сегодня `docker compose up -d`
   (`.github/workflows/deploy.yml:44`) останавливает старый контейнер и
   поднимает новый по очереди, и `stop_grace_period: 20s`
   (`docker-compose.yml:16`) даёт старому доиграть; менять это на схему с
   перекрытием (`--no-recreate`, две реплики, blue-green) нельзя, пока FSM
   лежит в памяти процесса. Ограничение относится только к `bot`: `web` и
   `worker` состояния диалогов не держат.
4. Телефон принимается только из `verified_from_contact`. Хендлер не
   конструирует `VerifiedPhone`, он получает `VerifiedPhone | None`.
5. **Ответ — можно, пуш — нельзя.** Реакция на текущий апдейт
   (`message.answer`, `callback.message.edit_text`) остаётся в хендлере: это и
   есть работа интерфейса. Сообщение с явно указанным получателем
   (`bot.send_message(chat_id, …)`) в `bot` запрещено и живёт только в
   `worker`. Формулировка и проверка — §12.2.

### 9.2. `web`

**Зона ответственности.** Кабинет: маршруты FastAPI, шаблоны Jinja, HTMX-фрагменты,
сессии и вход по одноразовой ссылке.

```python
# web/app.py
def create_app(settings: Settings) -> FastAPI: ...

# web/deps.py
async def current_user(request: Request) -> User: ...
async def uow_for(user: User = Depends(current_user)) -> UnitOfWork: ...
    # SqlUnitOfWork(user_id=user.id) → SET LOCAL app.user_id → RLS
```

**Запрещено импортировать:** `httpx`, `aiogram`; `core.adapters`, `core.repos`
(везде, кроме `web.app`); `bot`, `worker`.

**Инварианты.**

1. Любой запрос к данным клиента идёт через `UnitOfWork`, созданный с `user_id`.
   Маршрут не получает `UnitOfWork` без пользователя.
2. В URL нет внутренних числовых идентификаторов — номер заказа, который клиент
   знает (§3.4 документа).
3. Пул соединений — свой на процесс, pgbouncer не ставится (§10 документа).

### 9.3. `worker`

**Зона ответственности.** Всё, что происходит без пользователя: синхронизация,
опрос доставки, отправка из outbox, ночной пересчёт, планировщик.

```python
# worker/main.py
async def main() -> None: ...            # цикл планировщика

# worker/jobs/*.py
async def sync_orders(deps: Deps) -> None: ...
async def track_shipments(deps: Deps) -> None: ...
async def dispatch_outbox(deps: Deps) -> None: ...
async def recompute_personalization(deps: Deps) -> None: ...
```

**Запрещено импортировать:** `httpx`, `aiogram` (кроме `worker.main`, если
`Notifier` собирается там), `sqlalchemy`, `asyncpg`; `core.adapters`, `core.repos`
(везде, кроме `worker.main`); `bot`, `web`.

**Инварианты.**

1. Портов не слушает.
2. **Не создаёт `Dispatcher` и не поллит.** Воркеру нужен только экземпляр
   `Bot` с тем же токеном, чтобы отправлять; поллинг — работа `bot`, и второй
   поллер по одному токену получит от Telegram 409. Это явное правило, а не
   само собой разумеющееся: сегодня фоновая работа (`stock_watcher`) как раз и
   висит на диспетчере (`bot/__main__.py:95`).
3. Единственный отправитель в Telegram — джоба `dispatch_outbox`.
4. Взаимные исключения — через `pg_advisory_lock`, не через `asyncio.Lock`.
   Сегодня `_send_lock` (`bot/handlers/broadcast.py:41`) и
   `_refresh_semaphore` (`bot/handlers/orders.py:37`) видимы только внутри
   процесса, и при трёх процессах заявленные лимиты умножаются на три.
5. Каждая джоба пишет `last_success_at`; алерт строится на отсутствии успеха, а
   не на наличии ошибки (§4.5 документа).

---

## 10. Подсистемы, которых нет в архитектурном документе

Пять подсистем работают в коде и не упомянуты в `docs/architecture.md` ни разу
(`docs/current-state.md`, §7.3). Раньше они были только в карте переезда — то
есть имели адрес, но не имели контракта, и следующий разбор нашёл бы то же
расхождение. Здесь у каждой есть зона ответственности, интерфейс и инварианты.

### 10.1. Аналитика → `core.usecases.analytics` + `core.repos.events`

**Зона ответственности.** Продуктовые события и агрегаты над ними: воронка,
доля неудачных поисков, возвращаемость.

```python
# core/usecases/analytics.py
class Track:
    async def __call__(
        self, user_id: UserId | None, event: str, **meta: object
    ) -> None: ...

# core/ports/repos.py
class EventRepo(Protocol):
    async def log(self, user_id: UserId | None, event: str, meta: Mapping[str, object]) -> None: ...
    async def funnel(self, steps: Sequence[str], *, days: int) -> Mapping[str, int]: ...
    async def event_counts(self, *, days: int) -> Sequence[EventCount]: ...
    async def returning_users(self, *, days: int) -> tuple[int, int]: ...
```

**Инварианты.**

1. **Каждое проактивное сообщение несёт ключ кампании в `callback_data`, и это
   тот же ключ, что в `dedup_key`.** Без него `events` даёт только «отправлено»
   и «куплено», а середина воронки — нажал ли человек на то, что ему прислали —
   не восстанавливается ничем. Задним числом к уже отправленным сообщениям это
   не приделать, поэтому требование фиксируется до первой отправки через
   outbox, а не после. Формально:

   ```python
   # core/domain/outbox.py
   CampaignKey = NewType("CampaignKey", str)     # напр. "replenish:2026-08:sku-1234"

   def dedup_key(kind: NotificationKind, user_id: UserId, campaign: CampaignKey) -> DedupKey: ...
   ```
   и та же `campaign` кладётся в `callback_data` каждой кнопки сообщения.
   `OutboxRepo.enqueue` принимает `campaign` обязательным полем, а не
   опциональным, — тогда отправитель без ключа не собирается.
2. Запись события никогда не роняет и не задерживает сценарий, который её
   породил. Сегодня это так (`bot/analytics.py:37` спавнит запись,
   ошибка глотается на `:27-28`), и это поведение сохраняется.
3. Агрегаты живут в репозитории, а не в хендлере. Сегодня их четыре
   (`bot/db.py:701-768`) и вызываются они из админского хендлера
   (`bot/handlers/broadcast.py:277-280`).

**Оговорка.** Прунинг `outbox` (§8 документа, «после отправки чистится
агрессивно, через неделю») уничтожает шапку отправки раньше, чем закрывается
окно повторной покупки. Поэтому шапка (`user_id`, `kind`, `campaign`,
`sent_at`) переживает прунинг тела; иначе воронка, ради которой введён
`CampaignKey`, не считается.

### 10.2. Остатки и подписка «сообщить о поступлении» → `worker.jobs.stock` + `core.domain.stock`

**Зона ответственности.** Периодический снимок остатков, обнаружение перехода
«не было → появилось», уведомление подписавшихся.

```python
# core/domain/stock.py  (чистое)
def restocked(previous: Mapping[Sku, int], current: Mapping[Sku, int]) -> frozenset[Sku]: ...

# core/ports/repos.py
class StockRepo(Protocol):
    async def levels(self) -> Mapping[Sku, int]: ...
    async def save_levels(self, levels: Mapping[Sku, int]) -> None: ...
    async def subscribers(self, skus: Collection[Sku]) -> Sequence[StockSubscription]: ...
    async def clear(self, pairs: Collection[tuple[UserId, Sku]]) -> None: ...

# worker/jobs/stock.py
async def sweep(deps: Deps) -> StockReport: ...
```

**Инварианты.**

1. **Это первый отправитель, переезжающий на outbox — раньше рассылки и раньше
   статусов доставки.** Причина: подписка «сообщить о поступлении» уже
   работающий проактивный канал с таблицей подписчиков
   (`stock_subscriptions`, `bot/db.py:177`), то есть готовая модель
   replenishment в миниатюре — тот же вопрос «кому, про какой SKU, один раз».
   Объём при этом маленький, и цена ошибки ниже, чем у рассылки. В карте
   переезда (§14) он помечен соответственно.
2. Снимок остатков сохраняется **в той же транзакции**, что и постановка
   уведомлений в outbox, и снятие подписок. Сегодня разорвано:
   `save_stock_levels` коммитится на `bot/stock.py:102`, рассылка идёт на
   `:120-131`, `clear_subscriptions` — на `:133`; падение между первым и
   третьим означает, что переход «0 → есть» уже записан как база и об этом
   поступлении не узнает никто.
3. Пустой ответ склада не считается «всё кончилось». Сегодня это единственное
   место, где пустой результат внешнего API обработан явно
   (`bot/stock.py:94-98`), и это поведение сохраняется — в целевом контракте
   оно выражается тем, что порт бросает, а не возвращает `{}`.

### 10.3. Запрос скидки → `core.usecases.discount`

**Зона ответственности.** Клиент просит скидку, менеджер получает запрос,
повторные просьбы ограничены окном.

```python
# core/usecases/discount.py
class RequestDiscount:
    async def __call__(self, user_id: UserId, orders: Sequence[Order]) -> DiscountOutcome: ...
    # OK | ALREADY_REQUESTED(until=...)
```

**Инварианты.**

1. Ограничение окна — уникальный индекс, а не проверка перед вставкой. Сегодня
   это check-then-act через два соединения (`bot/handlers/orders.py:589` и
   `:591`) при полном отсутствии ограничений на таблице
   (`bot/db.py:191-198`): два быстрых нажатия проходят оба.
2. Сообщение менеджеру ставится в outbox в той же транзакции, что и запись
   запроса. Сегодня запись коммитится до отправки, и если отправка не
   удалась — а её исключение проглатывается (`bot/handlers/orders.py:617`) —
   клиент видит «отправлено», менеджер не видит ничего, и повторить клиент не
   может: окно уже занято.

### 10.4. Локализация → `core.i18n`

Описана в §4. Сюда относится только то, что она уже существует
(`bot/i18n.py`, 308 строк, 13 импортёров, колонка `users.language`,
`bot/db.py:67`) и переезжает целиком, вместе с `bot/texts.py`.

**Инвариант.** Язык выбирается один раз на входящее событие и передаётся вниз
явным аргументом. Сегодня это делает middleware (`bot/middlewares.py`), в вебе
источником будет сессия; общим остаётся то, что ни один модуль ниже точки входа
не решает, на каком языке говорить, — он получает `Texts`.

### 10.5. Демо-режим → `bot.handlers.demo`, с отдельным правилом изоляции

**Зона ответственности.** Показать админу, как выглядят экраны, без живого
клиента.

**Инварианты.** Это не «по аналогии с остальными», а отдельное правило, и вот
почему: после бэкфилла в базе будет полная история 19 456 клиентов
(`keycrm-business-baseline`, измерено 2026-07-29), и любой экран, который умеет
показать не своё, становится каналом утечки — RLS его не остановит, потому что
запрос придёт от легитимного пользователя-админа.

1. Демо читает **только** фиксированного синтетического пользователя и
   **никогда** не подмешивает реальные строки к его выдаче. Не «фильтрует
   лишнее», а не запрашивает ничего, кроме демо-владельца.
2. Синтетические данные помечены на уровне строки и не попадают ни в один
   агрегат: ни в аналитику, ни в персонализацию, ни в рассылку. Сегодня метка
   есть — отдельный `source` (`bot/handlers/demo.py:52`, `DEMO_SOURCE`).
3. Обязательный тест: сессия админа с включённым демо не видит ни одной строки
   реального клиента, и наоборот — демо-строки не появляются ни в одном
   агрегате. Тест идёт в один список с матрицей авторизации (§13), а не в
   отдельный, чтобы новый демо-экран покрывался автоматически.

---

## 11. Инварианты и чем каждый проверяется

| Инвариант | Где живёт | Чем проверяется |
|---|---|---|
| `VerifiedPhone` только из `verified_from_contact` | `core.domain.phone` | **не выражается в `.importlinter`**, см. 12.1 |
| `merge_key` — единственное правило идентичности | `core.domain.order` | юнит-тест на обе ветки + `UNIQUE(merge_key)` в миграции |
| Приоритет источников | `core.domain.order.merge` | юнит-тест: применение в любом порядке даёт один результат |
| Ошибка внешнего API не становится пустым результатом | `core.ports.errors` + адаптеры | тесты адаптеров на фикстурах 401/429/5xx |
| Парсеры чисты | `core.adapters.*.parse` | контракт `parsers-are-pure` в `.importlinter` |
| SQL только в `core.repos` | `core.repos` | контракт `only-repos-touch-db` |
| Сценарии не знают реализаций | `core.usecases` | контракт `core-layers` |
| Точки входа не говорят по HTTP | `bot`/`web`/`worker` | контракт `no-http-in-entrypoints` |
| Захват outbox с арендой, отправка вне транзакции | `core.repos.outbox` | интеграционный тест на Postgres, см. 12.3 |
| `SET LOCAL app.user_id` и RLS | `core.repos.uow` + миграция | тест под ролью приложения, см. 12.5 |
| Пуш с явным получателем только в `worker` | `bot`/`web` | AST-проверка в CI, §13; см. 12.2 |
| `bot` — один экземпляр | развёртывание | **ничем в коде**, см. 12.6 |
| Проактивное сообщение несёт `CampaignKey` | `core.domain.outbox` | обязательное поле `enqueue`, см. 10.1 |
| Демо не видит реальных строк | `bot.handlers.demo` | тест в матрице авторизации, см. 10.5 |
| Окружение читается только в `core.config` | `core.config` | греп-шаг в CI, см. 12.7 |

---

## 12. Контракты, которые не выражаются в коде без костыля

Здесь то, что документ требует, а язык и линтер не дают. Обходной путь не
придумывается — фиксируется факт и называется цена.

**12.1. `VerifiedPhone` — вычеркнуто, инвариант выражается.**
Здесь стояло, что правило «только один модуль конструирует этот тип» не
закрепляется ничем, кроме mypy. Это было верно для `NewType` и неверно вообще.
Frozen-dataclass с обязательным полем-ключом (§3) держит инвариант в рантайме:
случайный `VerifiedPhone("380…")` не собирается вовсе, намеренный требует
объекта-ключа, которого нет ни в одном другом модуле, значение после создания
не меняется. Проверено на python 3.14.2.

Остаётся, и это цена, а не оговорка:

- `object.__setattr__(vp, "value", …)` обходит `frozen`. Обходит его и у любого
  другого frozen-объекта в стандартной библиотеке; это намеренное действие, а
  не ошибка невнимательности, и защищаться от него нечем.
- Линтер по-прежнему ничего об этом не знает: правило держит сам тип, а не
  конфиг. Если кто-то заведёт второй конструктор рядом, в том же модуле,
  инвариант тихо исчезнет. Это ловится ревью и тестом «нет второго значения
  `_OWNERSHIP`», а не статически.
- mypy остаётся полезен (он ловит `str` там, где ждут `VerifiedPhone`, до
  запуска), но больше не является единственной опорой.

**12.2. Ответ и пуш — разные вещи, и различает их получатель.**
Прежняя формулировка «всё исходящее через outbox» была неверна: для `bot`
она невыполнима, потому что aiogram отдаёт хендлеру объект `Message`, у
которого `answer` уже есть и импорта не требует. Правило переформулировано так,
чтобы его можно было проверить:

- **Разрешено:** реакция на текущий апдейт — `message.answer`,
  `callback.message.edit_text`, `callback.answer`. Получатель здесь не
  выбирается, он и есть тот, кто прислал апдейт.
- **Запрещено вне `worker`:** отправка с явно указанным получателем —
  `bot.send_message(chat_id, …)`, `bot.forward_message(chat_id, …)`. Явный
  получатель — это и есть определение пуша, и именно он требует outbox:
  дедупликации, тихих часов, повторов, `notify_from`.

Различие ловится по форме вызова, поэтому проверка возможна — AST-шаг в CI
(§13), а не import-linter: `send_message` не требует импорта, он приезжает
атрибутом объекта.

Дополнительная мера, снимающая половину случаев: не инжектить голый `Bot` в
хендлеры. Аргумент `bot: Bot` в сигнатуре — это то, что aiogram подставляет по
имени; если его не объявлять, объекта в хендлере просто нет. Полностью это
проблему не решает (`message.bot` доступен всегда — так и сделано в
`bot/handlers/support.py:37`), но убирает самый удобный путь.

**Что нарушено сегодня — десять вызовов, они же список работ по переезду:**

| Место | Что это | Куда переезжает |
|---|---|---|
| `bot/stock.py:57,64` | уведомление «снова в наличии» | outbox, первым (§10.2) |
| `bot/handlers/broadcast.py:75,83` | рассылка | outbox |
| `bot/handlers/broadcast.py:120` | отчёт админу о завершении | outbox |
| `bot/handlers/support.py:37,43,50` | пересылка обращения менеджеру | outbox |
| `bot/handlers/support.py:105` | ответ менеджера клиенту | outbox |
| `bot/handlers/orders.py:613` | запрос скидки менеджеру | outbox (§10.3) |

Проверка становится зелёной по мере переезда; пока она красная, её вывод — это
и есть оставшийся объём.

**12.2. «Все исходящие сообщения идут через outbox».**
Для `worker` и `web` выражается: им запрещено импортировать `aiogram`. Для `bot`
не выражается никак — aiogram отдаёт хендлеру объект `Message`, у которого
`answer`/`edit_text` уже есть, импорта не требуют и отличить их от уведомления
статически невозможно. Значит правило распадается надвое: «прямой ответ на
апдейт — можно» проверить нельзя и не нужно; «инициативное сообщение — только
через outbox» держится только код-ревью. Сегодня инициативные отправки живут в
`bot/handlers/broadcast.py:75,83` и `bot/stock.py:60,64`.

**12.3. Семантика захвата outbox.**
`.importlinter` проверяет, кто кого импортирует, и ничего не знает про то, что
транзакция закрыта до сетевого вызова. Правило «`claim` коммитится раньше, чем
`Notifier.send`» проверяется только интеграционным тестом на живом Postgres:
два конкурентных `claim` не пересекаются, строка с истёкшим `locked_until`
возвращается снова, `attempts` растёт при падении процесса. Без такого теста
контракт §5.3 документа существует только в комментарии к SQL.

**12.4. Соответствие адаптера порту.**
`Protocol` не проверяется в рантайме: `runtime_checkable` сверяет наличие
атрибутов, но не сигнатуры. Поэтому адаптер, у которого разъехался порядок
аргументов, соберётся и упадёт в проде. Единственный способ поймать это без
mypy — строка вида `_: OrderSource = KeyCRMClient(...)` в конце модуля адаптера,
которая проверяется mypy, но в рантайме требует сконструировать объект. Костыль
осознанный; альтернатива — mypy в CI как обязательный шаг.

**12.5. RLS.**
Не выражается ни в линтере, ни в типах. Проверяется только тестом, который
подключается **под ролью приложения** (не под владельцем таблиц) и убеждается,
что чужие строки не видны, а строки с `user_id IS NULL` не видны никому.
Отдельно: сам факт, что роль приложения не владеет таблицами, проверяется не
кодом, а миграцией и тестом на `pg_roles`.

**12.6. «`bot` — один экземпляр».**
Это свойство развёртывания, а не кода. В коде оно проявляется только как
`MemoryStorage` по умолчанию (`bot/__main__.py:55`) и как 409 от Telegram при
втором поллере. Ни линтер, ни тест этого не поймают.

Три следствия, которые всё-таки можно записать и проверить глазами при ревью
конфигурации, а не кода:

1. Не давать `bot` запускать джобы воркера. Сегодня нарушено: `stock_watcher`
   спавнится из `@dp.startup()` (`bot/__main__.py:95`), то есть поднимется в
   каждом процессе, который создаст этот `Dispatcher`.
2. `worker` не создаёт `Dispatcher` и не поллит (§9.3, инвариант 2). Ему нужен
   только `Bot` с тем же токеном — отправка второго поллера не требует.
3. Рестарт при деплое — последовательный. Перекрытие старого и нового
   контейнера даёт 409, обработчика которого в коде нет. Сегодня схема
   правильная — `docker compose up -d` (`.github/workflows/deploy.yml:44`)
   пересоздаёт контейнер по очереди, `stop_grace_period: 20s`
   (`docker-compose.yml:16`) даёт старому доиграть, — но это следствие
   умолчаний compose, а не записанного требования. Любой переход к схеме с
   перекрытием (две реплики, blue-green, `--no-recreate` с ручным стартом)
   ломает бота молча: 409 в логах есть, а сообщения клиентам просто перестают
   доходить в один из двух процессов.

**12.7. «Окружение читается только в `core.config`».**
Проверено на import-linter 2.13: `os.environ` в `forbidden_modules` отвергается
дословным сообщением «Invalid forbidden module os.environ: subpackages of
external packages are not valid». Запретить `os` целиком нельзя — его импортируют
ради `os.path` и прочего, а `import os` с последующим `os.getenv(...)` это
обращение к атрибуту, которого граф импортов не видит по устройству. Линтер
закрывает только импорт `dotenv`; вторая половина правила проверяется отдельным
шагом CI:

```bash
! grep -rn "os\.getenv\|os\.environ" --include='*.py' core bot web worker \
    | grep -v "^core/config.py:"
```

Сегодня это правило нарушено ровно один раз — `bot/db.py:14`.

**12.8. Пять подсистем — закрыто, но остаётся расхождение с документом.**
`analytics`, `stock`, `discount_requests`, `i18n`, `demo` получили зону
ответственности, интерфейс и инварианты в разделе 10, а не только адрес в карте
переезда. Что этим **не** решено: в `docs/architecture.md` их по-прежнему нет ни
строкой, поэтому §12 того документа (план работ) не содержит для них ни одного
этапа и ни одной оценки. Три из пяти при этом стоят на критическом пути:
`stock` — первый отправитель через outbox (§10.2), `analytics` — источник
критерия готовности «видна конверсия» для двух этапов сразу, `i18n` — общий
модуль текстов, без которого кабинет и бот разъедутся (§7 документа). Правки в
`docs/architecture.md` этот документ не делает — это его расхождение, не наше.

---

## 13. Правила зависимостей в CI

Конфиг — `.importlinter` в корне репозитория. Запуск:

```bash
pip install import-linter          # добавить в requirements-dev.txt
lint-imports --config .importlinter
```

Два правила линтером не выражаются и проверяются отдельными шагами.

**Пуш с явным получателем (§12.2).** Достаточно грепа, AST здесь не нужен:
`send_message` и `forward_message` не существуют в форме без получателя, а
`message.answer` — другое имя, так что различить их можно по имени вызова, не
разбирая аргументы. AST понадобился бы только чтобы разрешать вызовы по
контексту (например, внутри определённого декоратора), а правило у нас
по каталогу.

```bash
! grep -rn "\.send_message(\|\.forward_message(" --include='*.py' core bot web
```

Сегодня даёт десять строк — список в §12.2. Шаг включается в CI в тот момент,
когда последняя из них переедет, а до тех пор запускается вручную как счётчик
оставшегося.

**Окружение (§12.7).**

```bash
! grep -rn "os\.getenv\|os\.environ" --include='*.py' core bot web worker \
    | grep -v "^core/config.py:"
```

Шаг GitHub Actions (до деплоя, отдельной джобой):

```yaml
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: lint-imports --config .importlinter
      - name: окружение читается только в core.config
        run: |
          ! grep -rn "os\.getenv\|os\.environ" --include='*.py' core bot web worker \
              | grep -v "^core/config.py:"
```

Версия python — 3.14: на ней собран `.venv` проекта (проверено:
`python 3.14.2, sqlite 3.51.2, aiosqlite 0.22.1`), и на ней же держится
транзакционность миграций из `bot/db.py`.

Два замечания по работе линтера, которые сэкономят время:

- Контракты типа `forbidden` со сторонними пакетами требуют
  `include_external_packages = True` и видят только **прямой** импорт. Импорт
  через строку в DI-контейнере не отследится.
- Импорты под `if TYPE_CHECKING:` линтер видит и считает нарушением. Для
  контракта «хендлер видит только порты» это нужное поведение: аннотация должна
  быть Protocol-ом из `core.ports`, а не конкретным классом. Сегодня
  `bot/handlers/orders.py:25,26` импортирует именно конкретные клиенты.

---

## 14. Карта переезда

Поведение на этом шаге не меняется. «Как есть» — файл переносится и правится
только импортами. «Распадается» — содержимое расходится по нескольким модулям.

| Текущий файл | Целевой модуль | Действие |
|---|---|---|
| `bot/__main__.py` | `bot/main.py` | как есть; DI переключается с конкретных клиентов на сценарии |
| `bot/config.py` | `core/config.py` | распадается: чтение `.env`/yaml как есть; `BOT_DB_PATH` из `bot/db.py:14` переезжает сюда |
| `bot/db.py` (855 строк) | `core/repos/*`, `core/domain/*`, `migrations/` | распадается: 40 запросов → репозитории по агрегатам; `_ORDER_COLUMNS` (`:460`), `CANCELLED_STATUS_GROUP` (`:471`) → домен; `_MIGRATIONS`/`_migrate` (`:249-269`) → Alembic; агрегаты аналитики (`:664-731`) → `core/repos/events.py` |
| `bot/services/keycrm.py` | `core/adapters/keycrm/{client,parse}.py` + `core/domain/order.py` | распадается: `_parse_order` (`:58`), `normalize_phone_for_keycrm` (`:47`) → `parse.py` как есть; сетевые методы → `client.py`; `keycrm_order_to_dict` (`:105`) → домен |
| `bot/services/shopify.py` | `core/adapters/shopify/{client,parse}.py` | распадается так же; `shopify_external_id` (`:80`) → `core/domain/order.py` |
| `bot/services/novaposhta.py` | `core/adapters/novaposhta/{client,parse}.py` | распадается; **парсер придётся выделить** — сегодня его нет (`:97-106` внутри `async with`) |
| `bot/quiet.py` | `core/domain/outbox.py` | как есть; `is_quiet_now()` (`:38`) становится `next_send_time()`, `SHOP_TZ` (`:35`) приходит параметром вместо модульной переменной |
| `bot/texts.py`, `bot/i18n.py` | `core/i18n.py` | как есть, объединяются |
| `bot/tasks.py` | `bot/tasks.py` | как есть; перестаёт быть носителем доставки уведомлений — их забирает outbox |
| `bot/logs.py` | `core/logs.py` | как есть; маскирование номеров нужно всем трём точкам входа, а не одному боту |
| `bot/analytics.py` | `core/usecases/analytics.py` + `bot/` | распадается: `track()` → сценарий с `EventRepo` (§10.1); `spawn` остаётся инфраструктурой точки входа. **Обязательное новое поле:** `CampaignKey` в `enqueue` и в `callback_data` — вводится до первой отправки через outbox, задним числом не приделывается |
| `bot/stock.py` (151 строка) | `worker/jobs/stock.py` + `core/domain/stock.py` | распадается: `restocked()` (`:34-43`) → домен как есть; свип (`:91-138`) → джоба; вечный цикл (`:141-151`) → планировщик воркера. **Отправка (`:57,64`) переезжает в outbox ПЕРВОЙ — раньше рассылки и раньше статусов доставки** (§10.2): это уже работающий проактивный канал с таблицей подписчиков, то есть replenishment в миниатюре, при малом объёме и низкой цене ошибки |
| `bot/handlers/orders.py` (691 строка) | `bot/handlers/orders.py` + `core/services/orders.py` | распадается: `_do_refresh_orders` (`:352-409`) → `SyncOrders`; форматирование и клавиатуры остаются; `_refresh_semaphore` (`:37`) **удаляется** — синк уходит в воркер |
| `bot/handlers/onboarding.py` | `bot/handlers/onboarding.py` + `core/domain/phone.py` + `core/services/register.py` | распадается: `own_contact_phone` (`:50`) и `normalize_phone` (`:28`) → домен, с изменением типа на `VerifiedPhone`; `_sync_orders`/`_register_user` → сценарий |
| `bot/handlers/broadcast.py` (318 строк) | `bot/handlers/broadcast.py` + `core/services/broadcast.py` | распадается: FSM и клавиатуры остаются; `_send_one` (`:55-91`), `run_broadcast_job` (`:107`), `resume_broadcasts` (`:128`) → постановка в outbox; `_send_lock` (`:41`) **удаляется**; админские отчёты (`:277-280`) → `core/services/analytics.py` |
| `bot/handlers/delivery.py` | `bot/handlers/delivery.py` + `core/services/delivery.py` | распадается: `_format_*` (`:26-73`) остаются; `novaposhta.track_many` (`:106`) уходит в `worker/jobs/track.py`, экран начинает читать `shipments` из базы |
| `bot/handlers/support.py` | `bot/handlers/support.py` + `core/repos/support.py` | распадается: восстановление адресата regex'ом по тексту (`:80-88`) заменяется таблицей; ответ менеджера → outbox |
| `bot/handlers/{common,info,menu,settings}.py` | `bot/handlers/` | как есть; вызовы `bot.db` заменяются на порты |
| `bot/handlers/demo.py` | `bot/handlers/demo.py` | как есть по коду, **плюс правило изоляции и тест** (§10.5): читает только фиксированного синтетического пользователя, никогда не подмешивает реальные строки, не попадает ни в один агрегат. После бэкфилла 19 456 клиентов это путь утечки мимо RLS — запрос приходит от легитимного админа |
| `bot/{keyboards,callbacks,states,screen,profile,middlewares}.py` | `bot/` | как есть, это транспорт Telegram |
| `bot/callbacks.py:57` `DeliveryAction` | — | **удаляется**: не используется нигде, единственное вхождение — само определение |
| `bot/config.py:15` `bot_username` | — | **удаляется**: не читается нигде |
| `bot_data.db`, `_DELETE_SHADOWED` (`bot/db.py:518`), бэкфилл `external_id` (`:218`) | — | **удаляются** вместе с переездом на Postgres: дедуп заменяется `UNIQUE(merge_key)`, бэкфилл выполняется один раз скриптом переноса |
| — | `tests/` | **создаётся**: сегодня тестов ноль, а половина инвариантов раздела 11 проверяется только тестами. Первыми — три проверки из блока 0, которые сейчас живут в скретчпаде: атомарность миграции, отсутствие номеров в логах, failover ключей Nova Poshta |
