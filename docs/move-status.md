# Перенос модулей: где остановились

Читать первым при возобновлении работы. Целевая структура — `docs/architecture.md` §3.
Правила коммита переноса — там же. Найденное по дороге — `docs/found-during-move.md`.

## Перенесено

| Было | Стало | Контракт линтера |
|---|---|---|
| `bot/merge.py` | `core/domain/order.py` | `domain-is-pure`, `domain-does-not-know-its-callers` |
| `bot/quiet.py` | `core/domain/quiet.py` | те же |
| `bot/texts.py` | `core/texts.py` | `i18n-is-a-leaf` |
| `bot/i18n.py` | `core/i18n.py` | тот же |
| `bot/config.py` | `core/config.py` | `config-is-a-leaf` |
| `bot/services/keycrm.py` | `core/adapters/keycrm/{client,parse}.py` | `parsers-are-pure`, `adapters-do-not-know-their-callers`, `api-clients-know-no-telegram` |
| `bot/services/shopify.py` | `core/adapters/shopify/{client,parse}.py` + `shopify_external_id` в `core/domain/order.py` | те же три |
| `bot/services/novaposhta.py` | `core/adapters/novaposhta/{client,parse}.py` | те же три |
| `bot/db.py`: соединение и схема | `core/repos/base.py`, `core/repos/schema.py` | `repos-do-not-know-their-callers`, `core-siblings-are-independent` |
| `bot/db.py`: остальное, по агрегатам | `core/repos/{users,orders,broadcast,events,stock,support,fsm}.py` | `only-repos-touch-db` |
| `_do_refresh_orders` из `bot/handlers/orders.py` | `core/usecases/sync_orders.py` | `usecases-do-not-know-their-callers` |
| `_register_user`/`_sync_orders` из `bot/handlers/onboarding.py` | `core/usecases/register.py` | тот же |
| `KeyCRMOrder` + `ShopifyOrder` + оба `*_order_to_dict` | `Order` и `order_row` в `core/domain/order.py`, `core/ports/crm.py` | `usecases-see-ports-not-implementations`, `ports-are-only-signatures` |
| `normalize_phone` + правило владения из `own_contact_phone` | `core/domain/phone.py`, теперь типом `VerifiedPhone` (§11 v4) | `domain-is-pure` |
| Админские отчёты из `bot/handlers/broadcast.py` | `core/usecases/analytics.py` | `usecases-do-not-know-their-callers` |

Одиннадцать контрактов в `.importlinter`, 138 тестов, всё в продакшене. Ни
`bot/services/`, ни `bot/db.py` больше не существует: в `bot/` осталась одна
телеграм-обвязка. Обе границы ЭТАП 0 стали целевыми — httpx заперт в
`core.adapters`, SQL в `core.repos`, — а третья умерла вместе со своим
каталогом.

Что дало разделение `parse` и `client`: KeyCRM 47% → 65%, Shopify 60% → 71%,
Nova Poshta 83% → 85%; по трём клиентам вместе 61% → 73%. Проценты честные —
все три `parse.py` покрыты целиком, непокрыт ровно транспорт, и это единственное,
что теперь требует сети. Тесты Nova Poshta перестали поднимать мок транспорта:
они читают ту же фикстуру напрямую.

## Осталось, в этом порядке

1. **Порт репозитория, он же UnitOfWork.** Вторая половина долга: сценарии
   больше не видят адаптеров, но `upsert_orders` и `save_user` импортируют
   напрямую, поэтому `core.usecases` всё ещё вне
   `core-siblings-are-independent`. Порт репозитория — это транзакция на
   несколько репозиториев и `SET LOCAL app.user_id`, то есть вопрос
   постгресовый; отвечать на него на SQLite значит спроектировать вслепую.
   **Делать вместе с этапом 3, а не до него.**
2. **Остальные сценарии из хендлеров.** Карта — `components.md`, строки
   1048–1051. Но половина написанного там ждёт не переноса, а этапов 6–7:
   рассылка уезжает в outbox, доставка начинает читать `shipments` из базы,
   `_refresh_semaphore` удаляется вместе с уходом синка в воркер. **Всё, что
   было переносимо сегодня, перенесено:** синк заказов, регистрация, телефон и
   админские отчёты. Дальше — только то, что ждёт своих этапов.

   *Уточнение после этапа 4.* Синк в воркер не уехал: цикл крутится в процессе
   бота (`bot/sync.py`), потому что отсрочка этапа 3 опирается на «процесс
   один» — `docs/incremental-sync.md`. Значит `_refresh_semaphore` остаётся:
   фоновое обновление экрана никуда не делось и по-прежнему может уйти пачкой
   после рассылки. Удаляется вместе с появлением контейнера `worker`, а не
   раньше.

**Читать `DB_PATH` только как `base.DB_PATH`.** `configure()` переприсваивает имя
в своём модуле, поэтому `from core.repos.base import DB_PATH` замораживает
значение по умолчанию, и модуль тихо уходит работать не с той базой. `connect()`
читает его в момент вызова — эту функцию импортировать по имени безопасно.

## Что уже наступало на грабли — не наступать снова

**`Dockerfile` копирует пакеты поимённо.** Первый же перенос уронил продакшен:
`core/` не был в `COPY`, образ собрался, контейнер умер на импорте. Локально не
воспроизводится — там корень репозитория в `sys.path`. Закрыто шагом CI, который
запускает **собранный образ** и импортирует в нём точку входа. При появлении
нового пакета верхнего уровня проверить `Dockerfile`.

**Тесты исходного дерева не видят ошибок упаковки.** См. выше. Зелёный pytest не
означает работающий образ.

**Контракт линтера включается в том же коммите, что и модуль.** Правило без кода
под ним ничего не защищает, код без правила разъезжается.

**`forbidden` в import-linter по умолчанию ловит и косвенные импорты.** Первый
же перенесённый клиент сломал контракт «httpx только в адаптерах» цепочкой
`bot.stock → core.adapters.keycrm.client → httpx` — то есть ровно тем, ради чего
перенос и делался. Лечится строкой `allow_indirect_imports = True`: правило
должно запрещать прямой импорт, а дотягиваться до сети через адаптер — это и
есть архитектура. **То же самое ждёт `only-repos-touch-db`** на переносе
`db.py`: как только SQL уедет в `core.repos`, любой хендлер будет доставать
`aiosqlite` по цепочке.

**Перенос не меняет поведение.** Ни исправлений, ни переименований, ни
объединений файлов. Найденное — в `found-during-move.md`, чинится отдельным
коммитом после.

**Откат работает и проверен:** `IMAGE_TAG=sha-xxxx docker compose up -d` на
сервере, восемь секунд. Предыдущие теги лежат локально, реестр для отката не
нужен. Подробности — `deploy/README.md`.

## Известные отступления от `docs/architecture.md` §3

- **v4 §3 отдаёт джобы каталогу `worker/`; их два, и оба живут в `bot/`**
  (`bot/stock.py`, `bot/sync.py`). Сценарии при этом в `core/usecases`, в
  `bot/` только цикл и отправка. Причина не в лени: цена отсрочки этапа 3
  (`postgres-migration.md`) записана через «процесс один», и второй контейнер,
  пишущий в тот же файл SQLite, отменяет это допущение. Переезд — новый
  `__main__`, сервис в compose и строка в `Dockerfile`.

- v4 требует один `core/i18n.py`; лежат два (`core/i18n.py`, `core/texts.py`).
  Слияние 525 строк — изменение, а не перенос.
- v4 кладёт `CampaignKey` в `core/domain/campaign.py`, тихие часы в
  `core/domain/quiet.py`; `quiet.py` уже там, `campaign.py` ещё не существует.
- v4 кладёт демо в `bot/demo.py`; в коде `bot/handlers/demo.py`.
- v4 §3 рисует адаптеры плоскими файлами (`core/adapters/keycrm.py`); в коде
  пакет с `client.py` и `parse.py`, как в `components.md` §6. Плоский файл не
  даёт выразить контракт «парсер не знает про httpx» — он про модуль, а не про
  функцию.
- ~~`keycrm_order_to_dict` и `shopify_order_to_dict` остались в адаптерах~~ —
  закрыто: появился `core.domain.Order`, оба конвертера схлопнулись в один
  `order_row`, адаптеры парсят прямо в доменный тип. Проверено побайтовым
  сравнением всех восьми строк, которые уходят в базу, до и после.
- **v4 §3 просит `OrderItem`; позиции остались списком словарей.** Причина на
  диске: у позиций KeyCRM есть `sku`, у позиций Shopify его нет, а
  `products_json` — хранимое поле. Единый тип позиции переписал бы JSON каждого
  закэшированного заказа Shopify. Это миграция, и делать её надо с миграцией.
- v4 §3 отдаёт миграции каталогу `migrations/` верхнего уровня; шесть питоновских
  миграций лежат в `core/repos/schema.py` рядом с DDL. Отдельный каталог — это
  Alembic и эпоха Postgres; выносить его на SQLite значило бы завести пустую
  папку под инструмент, которого нет.
- `components.md` не называет репозитория для заявок на скидку; они лежат в
  `core/repos/support.py`. Уходят в тот же чат и отвечает на них человек, а
  модуль на две функции сказал бы о них меньше, чем строчка в докстринге.
- **`core.usecases` уже не видит адаптеров, но всё ещё видит репозитории.**
  Половина долга закрыта контрактом `usecases-see-ports-not-implementations`;
  вторая половина — порт репозитория — ждёт Postgres, потому что это UnitOfWork
  и `SET LOCAL app.user_id`. Поэтому `core.usecases` в
  `core-siblings-are-independent` пока не входит.
- `core/usecases/register.py` держит свой `_sync_orders` — тот же сценарий минус
  обновление профиля покупателя. Не слит с `core/usecases/sync_orders.py`
  намеренно: регистрация забирает профиль отдельно и строкой выше, и слияние
  дало бы ей вторую запись, которой сейчас нет. Теперь оба лежат рядом, так что
  слияние — это один заход, когда кто-то решит, что вторая запись безвредна.

## Не проверено живьём

Ни один из сценариев поддержки не прогонялся в реальном Telegram: альбом, ответ
менеджера фото, и главное — деплой посреди написания сообщения в поддержку.
Тестами и на копии боевой базы сходится. Пять минут ручной проверки перед бетой.
