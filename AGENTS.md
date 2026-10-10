# Repository guidance

## What this is

A Telegram bot (aiogram 3) selling VPN subscriptions on a single
[3x-ui](https://github.com/MHSanaei/3x-ui) panel. There is one plan and one admin.
Payments are bank transfers confirmed manually by the admin. User-facing text is
Russian. The user receives a subscription URL, then extends it or contacts support;
there is no navigation menu, back button, or payment-provider endpoint.

## Commands and dependencies

```sh
uv sync --locked
uv run --locked app.py
uv add <dependency>
docker build -t 3xsub .
docker run --env-file .env -p 443:443 3xsub
```

Python 3.12+ is required: generic models and helpers use PEP 695 syntax.
`pyproject.toml` declares dependencies and `uv.lock` pins them. Update both when
adding dependencies. Docker also installs from the lockfile; there is no
`requirements.txt`.

There is no checked-in test suite or linter configuration. The practical static
gate imports all modules and constructs router filters:

```sh
uv run --locked python -c "import app, handlers, keyboards, scheduler, states; print('ok')"
git diff --check
```

Module imports do not require configuration. Run focused async checks for changes
to payment state, panel payloads, filters, scheduler cancellation, or database
migrations. Mock Telegram and panel calls rather than contacting live services.

## Configuration and dependency injection

`config.py` defines the Pydantic `Settings` class; it does not instantiate a global
settings object. `app.py` creates `Settings()` once in its entry point and passes
it to `build_app(settings)`. Settings read environment variables and `.env`; see
`.env.example`. Validation happens before the application starts.

- `TELEGRAM_ADMIN_ID` is one integer, not a list.
- `TELEGRAM_WEBHOOK_URL` is the complete webhook URL. It must contain a path and
  use a Telegram-supported port: 443, 80, 88, or 8443.
- `XUI_URL` is validated as `HttpUrl`, then converted to `yarl.URL` using
  `ValidateAs(RootModel[HttpUrl], ...)`. Keep panel paths intact when joining URLs.
- `Dispatcher(..., settings=settings)` injects settings into handlers and filters
  by parameter name. `IsAdmin` and `IsSupportGroup` use this dependency; do not
  capture configured IDs in module-level router expressions.
- `ConnectionMiddleware` injects `connection`; `SessionMiddleware` injects
  `session`. The scheduler receives settings and the session explicitly.
- Panel functions take an explicit `session` and required keyword-only
  `panel_url: yarl.URL`, rather than importing application settings.

## Application lifecycle

`app.py:build_app` creates one aiohttp application. `SimpleRequestHandler` serves
the POST path from `TELEGRAM_WEBHOOK_URL`. Startup registers the webhook, public
`/start` and `/support` commands, and `/broadcast` in the admin's private-chat
command scope.

Startup opens SQLite and creates a pooled panel `ClientSession` with a 15-second
timeout, `Authorization: Bearer <XUI_TOKEN>`, and `TCPConnector(ssl=False)` for
self-signed panel certificates. Reuse this session for all panel calls. Cleanup
cancels and awaits the scheduler before closing the panel session and database.

## Panel state and Result effects

`handlers.py` calls `db.py` for transactions/support mappings and `xui.py` for
clients/subscriptions. The panel is the subscription source of truth. SQLite has
no users, subscriptions, hosts, or plans tables in the current schema.

Panel client email is the bare `str(tg_id)`. Preserve that identity convention.
All panel envelopes are validated through `PanelResponse[T]`.

Panel operations use `expression.Result` and `@effect.async_result`:

```python
maybe_client = await get_client(email, session=session, panel_url=panel_url)
client = await maybe_client  # unwrap inside an effect; Error short-circuits
yield client
```

The first await runs the operation; the second unwraps its Result inside the
effect. Expected failures are returned as `Error`, not optional values. Helpers
`either_from_awaitable` and `either_from_throwable` convert specified exceptions
into Results. Unexpected exceptions still propagate, and cancellation must not
be swallowed. Await Results before iterating payloads or treating them as clients.

### Trial creation

`/start` calls `get_or_create_subscription`, which:

1. Always attempts `create_client` with `TRIAL_DAYS`.
2. Continues on creation success or `ClientAlreadyExists`, then fetches the client.
3. Fetches the subscription base URL and appends the client's `sub_id`.

The panel rejects duplicate creation without modifying the existing client.
Only the specific `email already in use: <email>` response maps to
`ClientAlreadyExists`; other errors must stop the workflow. Existing expired
clients must not receive another trial. Creation returns no client payload, so
the subsequent fetch is necessary.

`get_client` maps `record not found` / `(record not found)` messages and a success
response with no object to `ClientNotFound`.

`create_client` reads `/panel/api/inbounds/options`, uses every inbound, and sets
`xtls-rprx-vision` flow when any inbound is flow-capable.
`get_panel_settings` uses POST `/panel/api/setting/all` (GET returns 404).
`get_subscription_base_url` requires subscriptions enabled, a certificate, and a
path; it uses `subURI` when set, otherwise builds an HTTPS URL from the panel's
subscription domain, port, and path. It returns an error for invalid settings;
there is no raw-link fallback.

### Paid extension

`extend_subscription(client, days, session, panel_url=...)` updates an existing
client only. It never creates a client or changes its subscription ID. Expiry is
`max(client.expiry_ms, now_ms) + days * 86400 * 1000`; the client is enabled.
`Client` allows extra fields so credentials, flow, quotas, and other panel fields
survive a model round trip. Serialize with aliases and cast `id` to string for
POST `/panel/api/clients/update/{email}`. Return the copied, extended client only
after the panel reports success; leave the input model unchanged.

## Payment flow

1. `cb_extend` inserts a pending `Transaction` with ID, user ID, months, amount,
   status, and creation time, then edits the tapped message into payment details.
   `create_transaction` cancels that user's older pending transactions first.
2. `cb_paid` validates that the transaction is pending and belongs to the caller.
   Only pressing “Я оплатил” notifies the admin. `_prompt_confirm` raises if sending
   the admin card fails; the user must see that failure. The user's message becomes
   the payment-review screen with no buttons.
3. Admin-only `cb_confirm` loads the pending transaction, fetches the existing
   client, and calls `extend_subscription` for `tx.months * 30` days.
4. After a successful panel update, it completes the transaction, removes the
   admin confirmation button, and answers with the number of credited days.

`TransactionCallback` subclasses aiogram's Pydantic-based `CallbackData`. It holds
an action (`paid` or `confirm`) and UUID transaction ID, packs through the keyboard
builder, and is validated/injected by typed callback filters. The full transaction
stays in SQLite; Telegram callback data is limited to 64 bytes. Do not reintroduce
manual string splitting or compatibility handlers for old payment callbacks.

`get_pending_transaction` filters `status = 'pending'` in SQL. A completed or
cancelled payment cannot be confirmed again. If database completion fails after
extension, log the transaction for manual reconciliation and remove the button
anyway; retrying would credit more days. Do not retry successful panel mutations
because a later step failed. Transactions do not store message IDs, and extension
does not resend or pin a replacement key.

## Support and broadcast

`/support` finds or creates a forum topic in `SUPPORT_GROUP_ID` and stores its
user-to-topic mapping. Only `SupportThreadNotFound` permits creating a topic;
database failures must not be treated as an absent mapping. Missing transactions
raise `PendingTransactionNotFound`.

Non-command private messages are forwarded into the mapped topic. Support replies
are copied back so the support group's forwarding attribution is hidden. Keep
`StateFilter(None)` on the private-message catch-all: it must not consume FSM input.

`/broadcast` is admin-only, visible in the admin's private command scope, and sets
`AdminBroadcast.message`. The next message is copied to unique nonzero `tg_id`
values from the panel. Hand-created panel clients with `tgId = 0` are excluded.
Sending is paced and honors `TelegramRetryAfter.retry_after`. Clear the FSM state
when consuming the broadcast and report sent/failed counts. There is no admin menu.

The scheduler checks clients with a nonzero Telegram ID expiring within
`EXPIRY_NOTIFY_H`, sends an
expiry reminder with “Продлить”, then sleeps for `SCHEDULER_TIME_H`. Panel Result
errors propagate out of the check and are logged by the loop. Individual send
failures are logged without stopping other notifications. Unexpected raised
exceptions propagate; cancellation remains effective.

## Time and coding conventions

- Use `datetime.now(UTC)` and `datetime.fromtimestamp(..., tz=UTC)`. Displayed
  expiry times include a UTC label. Panel expiry values remain epoch milliseconds.
- Store transaction creation times as ISO strings with `+00:00`. Database startup
  normalizes older timestamps; reads use `datetime.fromisoformat` directly.
- All handler/database functions are async. Pass the database connection explicitly
  as the last database-function argument.
- Guard missing senders, absent/inaccessible callback messages, and missing topic
  IDs before accessing them. Answer callbacks on handled errors.
- Do not let a failed `try` fall through into use of names it was meant to bind;
  return on failure or use `else`.
- Keep `.env`, SQLite databases, credentials, and generated environment files out
  of commits and Docker build context.
