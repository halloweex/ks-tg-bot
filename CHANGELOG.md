# Changelog

## 2026-08-25 — Инлайн-списки, голос бренда и починенное реле

Большой день: 37 коммитов. Ниже по темам, а не по порядку.

### Два инлайн-списка вместо одного экрана

Улюблені и замовлення теперь открываются панелью над клавиатурой — той самой,
что рисует Telegram по `switch_inline_query_current_chat`. У каждой строки фото,
поиск работает по мере ввода, и это то, чего экраны не умеют в принципе: пять
кнопок нельзя отфильтровать, а три заказа на страницу нельзя обыскать.

Один инлайн-вход на двоих, поэтому список выбирается словом после юзернейма:
есть `замовлення` — заказы, нет — товары. Слово локализованное и принимается на
обоих языках: кнопка, отправленная месяц назад, переживает смену языка.

В карточке товара — купити, повідомити про наявність и хочу знижку (последняя
несёт sku, так что менеджера спрашивают про конкретный товар, а не про топ-5).
В карточке заказа — «🛒 Замовити ще раз»: cart-permalink со всей той корзиной,
собранный из того, что магазин ещё продаёт, с честной подписью «2 з 3», если
осталось меньше.

Отдельный обработчик подписки для карточек: у callback из инлайн-сообщения нет
`message`, только `inline_message_id`, и экранный обработчик на нём падал бы.

### Меню теперь в двух видах, а навигация — один экран

Клавиатура снизу осталась (только она рисует ☰ в строке ввода), и рядом
появилось то же меню кнопками в сообщении — единственный вид, который умеет
отдать поле ввода инлайн-списку. Нажатие в меню **правит то же сообщение**, а не
плодит новые: «Меню → Довідка → Оплата» — это один пузырь, который меняется
трижды. Из каждого раздела ведёт «📋 Меню».

Клавиша «🚚 Відслідкувати замовлення» из меню убрана: это была вторая клавиша
для того, что клиент считает одним вопросом. Живой ответ Нової Пошти переехал на
карточку заказа кнопкой «Де посилка?» — один запрос на одну посылку и только по
нажатию, поэтому экран открывается мгновенно.

### Экран замовлень — дайджест

Реализован макет, утверждённый 19 августа: верхний заказ карточкой, остальные —
строкой `✅ 15.06.2026 · 1 465 грн · 3 товари`, отменённые под кнопкой
`❌ Скасовані (N)`. Пять заказов — 13 строк вместо 42.

Нерешённый тогда вопрос про кнопки закрыт: они подписаны тем же значком и датой,
что и строка, поэтому нумерация и легенда к ней не нужны вовсе.

### Реле поддержки: чинили не конфиг, а тишину

Ничего не доходило до менеджера, и узнали об этом от клиента. Telegram отвечал
`Forbidden: bot can't initiate conversation with a user` — боту нельзя написать
первым тому, кто не открыл его сам.

Сам факт чинится нажатием «Почати» на аккаунте менеджера. Починили другое: три
способа, которыми это оставалось невидимым. Пересылка в поддержку падала в лог,
клиент не получал ни подтверждения, ни ошибки. Запрос на скидку глотал отказ,
благодарил за запрос, которого никто не получил, и успевал записать его в базу —
после чего недельный лимит отказывал в повторе. И ничего не проверялось при
старте: `getChat` на такой чат отвечает успешно, поэтому проверка теперь через
`sendChatAction` — она не создаёт сообщения и падает так же, как настоящая
отправка.

Заодно: менеджер видит, **кто** пишет (имя ссылкой на профиль, @username,
телефон), ответ клиенту приходит без подписи «Відповідь від менеджера:» и с
паузой «печатает…», а на сообщение клиента бот ставит 👀, как только оно дошло.

### Голос бренда

Из стратегии 2026 (346 слайдов) взято то, что применимо к боту, — и это не цвета,
а голос: бренд обращается на «ти». Все клиентские строки переписаны. Страницы
«Про нас», «Оплата», «Доставка», «Контакти» — их собственными словами.

Цвета и шрифты в чате задать нельзя вовсе (Telegram рисует текст темой клиента),
поэтому фирменное появилось там, где оно возможно:

* **кастомные эмодзи** — настоящие знаки Нової Пошти, Instagram, Visa,
  Mastercard, monobank прямо в тексте и на кнопках;
* **поздравительная карточка** ко дню рождения — бордовое поле, знак, логотип,
  всё снято с макетов айдентики;
* **цвет кнопок** — три семантических стиля Telegram (зелёный = тратит деньги,
  синий = вход в список, красный = отменяет то, что клиент завёл).

Кастомные эмодзи — привилегия, которая держится на Premium владельца бота,
поэтому исходящая middleware снимает их с текста и с клавиатуры, если Telegram
откажет: сообщение уходит простым, а не не уходит вовсе.

### День рождения

`getChat` отдаёт дату рождения, если человек её указал и не скрыл. Раз в час бот
спрашивает про полсотни клиентов, ответ (в том числе «даты нет») сохраняет в
колонку и поздравляет тех, у кого сегодня. Поздравление ничего не обещает —
скидочной политики нет, а бот, выдумавший её, обязывает бизнес.

### Реферальная программа и скидки

«🎁 Запроси подругу» — седьмой пункт меню. Клиентка отправляет бота подруге
(inline-режим, `switch_inline_query_chosen_chat`), подруга приходит по ссылке
`?start=ref_<id>`, и это пишется в `users.source` — **один раз**, первым
касанием. Когда у подруги появляется первый неотменённый заказ, обходчик (раз в
15 минут) платит за реферал: строка в таблице `referrals` — защита от двойной
оплаты, затем сообщение пригласившей.

Экран показывает две цифры, а не одну: «Запрошено: 3 · вже замовили: 1» —
иначе тот, у кого подруга ещё не купила, читает ноль как «не работает».

Скидки — две, обе через ссылку `/discount/КОД?redirect=/`, которую Shopify
применяет сам (**Admin-токен для этого не нужен**, проверено на живом магазине):

* новому клиенту — 10% на первый заказ; показывается только тем, у кого нет ни
  одного заказа: после регистрации и на пустых экранах замовлень и улюблених;
* пригласившей — 10% на следующий заказ, приходит вместе с поздравлением.

Коды бот не создаёт: их заводят в админке магазина, а `first_order_code` и
`referral_code` в `config.yaml` — это выключатели. Пока кода нет, обещание
остаётся в силе, но выполняет его человек: клиенту предлагается написать
менеджеру. Состояния «пообещали и никто не узнал» нет ни в одной ветке.

### Открытки

Две карточки в фирменных цветах: бордовая ко дню рождения и розовая для
приглашения (на ней нарисовано предложение). Знак и логотип вырезаны из макетов
айдентики в презентации и перекрашены — это ваши знаки, а не перерисовка.
Собираются скриптом `webapp/build_cards.py`, публикуются на GitHub Pages вместе
со страницей Mini App.

Два урока, оплаченных по дороге: Telegram кеширует превью по URL — поэтому
адреса карточек несут `?v=` из `assets_version`; и превью, построенное по
ссылке, может не построиться вовсе — поэтому приглашение уходит настоящим фото.

### Мелочи, которые заметны

Реакция 👀 на сообщение в поддержку, конфетти при подтверждении номера,
исчезающие через 45 секунд служебные подтверждения, «Красуне, обери дію» в
строке ввода.

### Петля на номере, который не читается

Клиент делится контактом, номер в нём не разбирается — и бот отвечал «введи у
міжнародному форматі», хотя следующий его же обработчик отклоняет всё набранное
руками: доказать владение можно только кнопкой. Два сообщения, противоречащих
друг другу, и никакого третьего выхода: ключи меню намеренно отключены на время
шаринга телефона, поэтому «💬 Менеджер» на клавиатуре провалился бы в тот же
отказ.

Теперь после неразобранного номера клавиатура несёт второй ключ, а он обработан
там, где живёт состояние, — выше catch-all, в обоих потоках (онбординг и смена
номера в настройках). Обычная клавиатура шаринга не изменилась: выход появляется
только после осечки. Текст больше не просит того, что бот не примет.

Заодно: удалён `MSG_LANGUAGE_CURRENT` — не показывался никогда и в украинской
таблице зашивал «Українська» в строку, то есть при английском назвал бы не тот
язык. И `MSG_NEW_PHONE_PROMPT` перешёл на «ти» — последняя «ви»-строка из тех,
что видит клиент.

## 2026-07-31 — One screen instead of a scroll

The rest of the UX plan from the previous session, items 1–4. Nothing here
changes what the bot knows; it changes how much of it a person has to read.

### The menu is a grid, and the labels are short

Seven buttons, one per row, filled a phone screen and gave equal weight to
"📦 Мої замовлення" and "⚙️ Налаштування". Now 2+2+3: the two questions people
arrive with on top (orders, delivery), what they might do next below
(favourites, manager), and the rest on the last row. Labels lost the words that
were doing nothing — «📦 Мої замовлення» → «📦 Замовлення».

Inline, that grid had to become 2+2+2+1: an inline keyboard is only as wide as
the message bubble it hangs under, the menu's text is three words, and at three
columns the labels were cut off in production. The constraint disappeared when
the menu became a reply keyboard (below), which spans the screen — the layout
is 2+2+3 again.

### Orders and favourites are numbered, and the buttons say the number

`🔎 Товари: 📸 Instagram, 15.06.2026` was one button per row and still ambiguous:
most orders come from Instagram with no order number, so two of them in the
same week read identically. The list is numbered across pages now, the heading
of each order is bold, the newest carries ⭐ — and the button is `🔎 3`, five of
them to a row, with one line above the list saying what the number means. The
same for the back-in-stock buttons on favourites.

### One live message

Navigation edits the screen instead of sending a new message. Menu → orders →
expand → back → favourites used to leave five messages in the chat; it is now
one message that changes. `bot/screen.py` holds `render()`, which edits and
falls back to sending when Telegram will not let it edit (message too old, not
a text message, already identical). Across the customer-facing handlers the
call sites went from 43 sends / 10 edits to 23 sends / 21 edits — what is left
sending is what genuinely must: replies to a message the customer just typed,
and the share-phone prompts.

Also folded in: the discount and subscribe confirmations are pop-ups rather
than messages; toggling a back-in-stock subscription redraws the screen, so the
button reflects what it just did; "Номер прийнято!" and "Завантажую…" are gone,
replaced by "typing…"; /start greets and shows the menu in one message.

Opening a *section* is now a new message rather than an edit — the menu key is
a message of the customer's own, so there is nothing above it to edit into.
Within a section, editing in place is unchanged.

### The menu *is* the keyboard under the input field

Settled after three wrong turns in one evening, and worth writing down because
the reasoning is not obvious from the API docs:

* The square toggle in the input row that people reach for is drawn by the
  client **only while a reply keyboard exists**. No API creates it —
  `setChatMenuButton` is a different button, and `set_my_commands` is a
  different list. An inline menu leaves that corner of the screen empty.
* So the main menu is a `ReplyKeyboardMarkup` (`main_menu_kb`), 2+2+3, with an
  `input_field_placeholder`. Three to a row is fine here: a reply keyboard
  spans the screen, not the message bubble.
* `is_persistent` is left **off**, which is the opposite of what its name
  suggests: the icon exists to hide and reopen the keyboard, so a keyboard that
  can never be hidden gets no icon. Setting it to True made the menu appear and
  the button not.
* Its keys arrive as ordinary messages, matched on text in every language the
  label can be rendered in (`variants()`), so a keyboard that predates a
  language change still works. The labels come from the same constants the
  keyboard is built from, which is what makes the emoji-variation-selector trap
  impossible here.
* «🌐 Сайт» is a key like any other and answers with a message carrying the
  link, because a reply button cannot hold a URL.
* Each key opens a section as a new message; the section's own inline buttons
  then edit that message in place. No inline main menu, and no Back buttons
  anywhere — the menu never leaves the screen.

Pressing a menu key clears any FSM state (an abandoned "write to support" no
longer swallows the next message), except while a phone number is being shared,
where the keys are ignored so the flow cannot be half-abandoned.

### What the previous attempts got wrong

1. **Removed the reply keyboard entirely**, on the theory that it duplicated
   Telegram's own button. It does sit in the same slot — but removing it left
   nothing there at all, because the slot is *made* by the keyboard.
2. **Chased `setChatMenuButton`.** `profile.ensure_menu_button()` survives from
   that attempt and still runs on /start: it sets `MenuButtonCommands` for the
   chat, which is worth having (a per-chat setting overrides the global default
   and outlives whatever set it) but was never the button being asked about.
3. **Brought back a single «📋 Меню» key** instead of the menu itself — one tap
   more than necessary for every action.

One implementation bug found along the way, and it is a good trap to know:
a `ReplyKeyboardRemove` sent on a message that is then **deleted does not
stick**. The client ties the keyboard's state to the message that changed it, so
deleting the message restores the keyboard. Removal has to ride on a message
that stays — or, as now, simply be replaced by sending another keyboard.

### Quiet at night, confetti when it is good news

`bot/quiet.py`: 22:00–09:00 Kyiv, `disable_notification=True` for anything the
bot sends on its own initiative — restock notifications and broadcasts, decided
per recipient at the moment of sending, so a long job does not wake people at
one in the morning. The restock message carries Telegram's 🎉 effect, sent
best-effort: if the effect id is ever rejected the message goes again without
it. `tzdata` added to requirements so the timezone resolves inside the slim
image.

### Verified live

The menu keyboard and the toggle icon in the input row, on production, after
the `is_persistent` fix.

### Not verified live

The message effect on restock messages (the id is only guarded by a retry
without it), and the section screens — orders, delivery, favourites, the
language switch — which were checked offline against fakes.

## 2026-07-29 … 07-31 — Customer-facing features, and a lot of measuring

### Shipped to production

- **Cross-system order dedup.** KeyCRM and Shopify both hold website orders;
  `orders.external_id` (Shopify's numeric id, mirrored by KeyCRM as
  `global_source_uuid`) merges them, KeyCRM wins. Latent until SHOPIFY_API_TOKEN
  is set, which it still is not.
- **Backups that leave the machine.** Integrity-checked snapshot, copy out of the
  volume, rsync to a Hetzner Storage Box, prune all three. Fails loudly to
  Telegram — the box has no MTA, so cron mail went nowhere. **Off-site is still
  not configured**; the nightly alert is the reminder.
- **Delivery**: TTN as a link to novaposhta.ua; six Nova Poshta keys configured;
  live status, branch and actual delivery date.
- **i18n**: Ukrainian/English per the user's Telegram language, explicit choice
  in settings, resolved per *recipient* (see memory: language-per-audience).
- **Orders screen**: paging (5/page), collapse/expand for long item lists,
  shortened product names, translated statuses, whole-hryvnia totals, cancelled
  orders excluded from Delivery, a way out of the "no orders" dead end.
- **Favourites** (top 5 by orders containing the product) with **back-in-stock
  subscriptions** polling KeyCRM `offers/stocks` every 15 min, and a
  **"I'd like a discount"** button that files a request to a manager.
- **Analytics**: `events` table with chat_id, `track()`, `/stats` for admins,
  UTM on the website link.
- **Bot front door**: localised commands with admin scoping, menu button,
  profile description, link previews off, typing indicator, warmer copy.
- `/demo` seeds fixtures into the admin's own cache; `/chatid` reports a chat id.

### Verified end to end

Back-in-stock fired on production for real: a subscription, a detected
transition, a delivered Telegram message, the subscription cleared and the
snapshot corrected by the same sweep.

### Corrections worth remembering

Three confident statements turned out wrong, each after measuring:

1. **"The CRM has no delivery city."** It does. The code read `delivery_city` and
   `receive_point`, fields the API does not have; the real names are
   `shipping_address_city` (93.4% filled) and `shipping_receive_point` (97.2%).
2. **"The stock export has stalled."** It had not. Those parquet files are a
   by-product of a weekly DuckDB compaction, deleted and rebuilt each Sunday —
   which is also why they are the wrong source for restock detection.
3. **"Six legal entities mean six keys are needed."** Any one key tracks any
   parcel when the recipient phone is supplied; measured across 2024-2026.

### Known follow-ups

- `orders` is keyed `UNIQUE(source, source_order_id)` without chat_id — two
  Telegram accounts sharing a phone would move rows between each other.
- `events` has no retention policy.
- The restore drill validates an archive, not a full bring-up.

## 2026-07-29 — Cross-system order dedup + off-site backups

### Duplicate orders (KeyCRM ↔ Shopify)

KeyCRM and Shopify were fetched in parallel and both written to `orders`, keyed
`UNIQUE(source, source_order_id)`. An order placed on the website exists in
**both** systems, so the customer would see it twice — once with the KeyCRM
status, once with the Shopify one.

**Confirmed on live data**, not assumed: over the 250 most recent KeyCRM orders,
**118 (47%) came from the Shopify integration** — i.e. nearly half of all orders
would double up. The overlap is currently latent only because
`SHOPIFY_API_TOKEN` is unset (the bot runs KeyCRM-only and `shopify` is `None`);
it appears the moment Shopify credentials are configured.

The dedup key, verified against the API:

| KeyCRM field | Shopify field | Example |
|---|---|---|
| `global_source_uuid` | numeric tail of the GraphQL `id` | `13025577828684` |
| `source_uuid` | `name` (order number) | `19966` → `#19966` |

Both are `null` for manually created orders (Instagram / Telegram / expo:
`source_id` 1, 2, 5) and set only for `source_id: 4` = *Shopify Integration*,
driver `shopify`, shop `qy2jmd-ui.myshopify.com`. `global_source_uuid` was
unique across the whole sample.

- `orders.external_id` added (+ `ix_orders_external`), populated on both sides;
  `shopify_external_id()` parses the gid defensively.
- **KeyCRM wins the merge** — it is the operational system of record
  (fulfilment status, tracking code, delivery point), and it mirrors the store
  order number, so nothing is lost. The Shopify copy is dropped in-memory in
  `_do_refresh_orders()` before it is ever written.
- `upsert_orders()` also sweeps shadowed rows in SQL on every refresh, and a
  one-time backfill recovers `external_id` for Shopify rows cached before the
  column existed — so duplicates written by earlier versions heal themselves on
  the next refresh instead of lingering forever.
- Orders KeyCRM pulled from Shopify now render as `🌐 Сайт #19966` instead of
  falling back to the Instagram label.

Verified end-to-end against real KeyCRM payloads: a cache seeded the old way
(25 orders + 10 duplicates) collapses to 25 rows after one refresh, KeyCRM
statuses survive, and a second refresh is a no-op.

### Backups are now actually backups

14 archives inside the `botdata` volume die with the volume — one
`docker volume rm`, one lost disk, and the database and every copy of it go at
the same moment.

- `deploy/backup.sh` rewritten: snapshot → **integrity check** (a corrupt or
  suspiciously empty snapshot is deleted, not kept) → copy out of the volume →
  **rsync to a Hetzner Storage Box** over SSH (port 23) → prune all three
  locations to 14. It **exits non-zero until the off-site target is
  configured**, so a half-finished setup surfaces in cron mail.
- Remote pruning goes through `sftp`, deliberately **not** `rsync --delete` —
  an emptied local directory must never be able to erase the off-site history.
- `deploy/restore-test.sh` (new) — the honest drill: pulls the newest archive
  **from the Storage Box**, restores it to a throwaway file, and checks
  integrity, table presence and that the data is populated. Warns if the newest
  archive is over 48h old. Scheduled monthly alongside the daily backup.
- `deploy/backup.env.example` (new) — Storage Box sub-account config.
- Two restore bugs fixed in the runbook: `docker compose cp` writes as **root**,
  which would leave `ksbot` unable to open its own database, and the stale
  `-wal`/`-shm` sidecars must be removed or SQLite replays them over the
  restored file. Restore now pipes through `docker compose run` as the container
  user. Added a from-scratch server rebuild procedure.

### Known follow-ups

- `orders` is still keyed `UNIQUE(source, source_order_id)` without `chat_id`.
  Two Telegram accounts sharing one phone (a family) would move rows between
  each other rather than duplicate them. Needs a table rebuild; not urgent at
  current scale.
- The restore drill validates the archive, not a full bring-up. Once a year,
  restore onto a scratch VPS and watch the bot actually start.

## 2026-07-28 — Delivery, deployment, and production hardening

This session shipped the delivery feature, put the bot on a server with
CI/CD, and closed a set of correctness/security/reliability gaps found while
reasoning about real scale (10k+ users, promo-push bursts).

### Features

- **Nova Poshta delivery tracking** (`feat(05)`) — new `NovaPoshtaClient` +
  `delivery` handler showing per-order TTN status, with a CRM-data fallback
  when the NP key is absent. Conditionally injected; KeyCRM-only mode preserved.
- **Broadcast confirmation buttons** — inline ✅ Так / ❌ Ні under the confirm
  prompt (typed так/yes/да kept as a fallback).
- **International phone entry** — a shared `normalize_phone()` accepts numbers
  from any country, with or without a leading `+` (Ukrainian local formats
  still map to +380).

### Reliability / scale

- **SQLite WAL + busy_timeout** via a shared `_connect()` helper, plus
  `ix_orders_chat_id` — concurrent order-refresh writers no longer hit
  "database is locked" during activity bursts.
- **Bounded background refresh** — a 5-min freshness TTL (`get_last_sync_time`)
  and a `Semaphore(10)` stop a post-broadcast burst from hammering
  KeyCRM/Shopify.
- **Durable, resumable broadcasts** — `broadcast_jobs` + `broadcast_targets`
  tables track per-recipient status; `resume_broadcasts()` finishes any job
  interrupted by a restart/redeploy, sending only still-pending recipients.
  `403 Forbidden` → mark blocked + `opt_out` (prunes dead chat_ids);
  `429` → honour `retry_after`.
- **Background-task tracking** (`bot/tasks.py`) — `spawn()` keeps a strong
  reference and logs exceptions (raw `asyncio.create_task` could be GC'd
  mid-request and swallowed errors); `drain()` runs on shutdown.

### Security

- **Phone spoofing / IDOR fixed** — a user could bind *any* phone (typed, or a
  forwarded contact card) to their chat and read another person's orders +
  delivery address. Now the phone is set only from the user's **own** contact,
  shared via `request_contact` and verified with
  `contact.user_id == from_user.id`. Manual entry is refused in onboarding
  **and** settings. See `own_contact_phone()` / `share_phone_kb()`.

### Deployment & CI/CD

- **Docker** — `Dockerfile` (python:3.12-slim, non-root, sqlite3 for backups),
  `docker-compose.yml` (`restart: always`, `env_file`, named volume `botdata`
  for the SQLite DB), `BOT_DB_PATH` so the DB lives on the volume.
- **Hetzner VPS** (`89.167.20.30`, Ubuntu 24.04) — long-polling, no inbound
  ports needed. Runbook in `deploy/DEPLOY.md`; WAL-safe daily backups via
  `deploy/backup.sh` (cron).
- **GitHub Actions auto-deploy** (`.github/workflows/deploy.yml`) — every push
  to `master` rsyncs code to the server (excluding `.env`/DB/volume) and runs
  `docker compose up -d --build` + a health check. Secrets: `DEPLOY_SSH_KEY`,
  `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KNOWN_HOSTS`.

### Verified but unchanged

- **Support relay** stores its reply-target (`chat_id`) in the forwarded
  message text in the support chat, not in process memory — it already
  survives restarts.

### Known follow-ups (not blocking)

- FSM state is in-memory (`MemoryStorage`): an *unconfirmed* broadcast draft is
  lost on redeploy (the running job is durable). Move FSM to Redis if needed.
- Per-service httpx clients are created per call; `NovaPoshtaClient.track_many`
  is sequential — reuse a pooled client and `gather` when traffic grows.
- Broadcast still sends in-process (~20 msg/sec, single coroutine). Fine now;
  consider a dedicated worker past ~50k recipients.
- Support-reply UX: managers should reply to the metadata note (has `chat_id`);
  replying to the forwarded message relies on `forward_from`, which is `None`
  under Telegram forward-privacy.
