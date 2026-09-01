# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram bot (aiogram 3) that sells VPN subscriptions on one or more [3x-ui](https://github.com/MHSanaei/3x-ui) panels, taking payment via YooMoney. User-facing text is Russian.

## Commands

```sh
.venv/bin/python app.py          # run (aiohttp server; needs a valid .env)
.venv/bin/pip install -r requirements.txt
.venv/bin/mypy .                 # mypy is installed in .venv; no config file
docker build -t 3xsub . && docker run --env-file .env -p 443:443 3xsub
```

There is no test suite, linter config, or `pyproject.toml`. Configuration comes only from `.env` (see `.env.example`); `config.py` fails fast at import time if a required variable is missing.

## Architecture

Single aiohttp application (`app.py:build_app`) serving two POST endpoints on one port:

- `TELEGRAM_WEBHOOK_PATH` — aiogram `SimpleRequestHandler`; the bot sets its own webhook to `WEBHOOK_URL + path` on startup.
- `YOOMONEY_WEBHOOK_PATH` — `webhook.handle_youmoney_callback`, the payment→provision path.

Startup also opens the SQLite connection, registers `ConnectionMiddleware` (injects `connection` into every handler's kwargs), and spawns `scheduler.run_scheduler` as a background task.

### Layering

`handlers.py` (bot UI) → `db.py` (local SQLite state) + `xui.py` (remote panel state). Layers below the handlers never import each other's callers; `models.py` holds all pydantic types shared between them.

### The key architectural point: where subscription state lives

SQLite stores only hosts, plans, transactions, and support threads. **It does not store subscriptions or users.** The 3x-ui panel is the source of truth for who has an active key and until when. Consequences:

- A user's identity on a panel is the synthesized email `f"{tg_id}@{host_name}"` — this convention is what links a Telegram user to a panel client, and it is duplicated in `handlers.py`, `webhook.py`, and implied by `xui.provision_key`. Change it in all places or existing keys become unreachable.
- Listing a user's keys means probing every host via `xui.get_client` and swallowing 404s (`cb_my_keys`).
- Broadcast recipients are derived from `xui.get_all_clients` across hosts, not from a users table.
- `db` and `xui` functions raise on "not found"/empty rather than returning `None`; handlers wrap nearly every call in `try/except` and answer with a Russian error message. Follow that pattern rather than introducing optional returns.

### Payment flow

1. `cb_buy_plan` / `cb_extend_plan` insert a `pending` transaction (uuid id) and show a YooMoney quickpay button built in `keyboards.payment_menu`. `create_transaction` cancels the user's other pending rows first, so at most one is open.
2. The quickpay link carries `label=<tg_id>` — that label is the only identifier YooMoney returns, so the callback matches on tg_id + amount.
3. `handle_youmoney_callback` validates the form with `YooMoneyRequest`, verifies the HMAC-SHA256 `sign` over the URL-encoded sorted params, then `find_pending_transaction` (amount must be within 10%), provisions, and marks the transaction completed.
4. Status codes are deliberate and used as debugging signal: 422 parse/lookup failure, 403 bad signature, 402 no pending transaction, 500 bookkeeping failure, 202 success. A provisioning failure returns 200 so YooMoney does not retry.

Buying and extending are the same code path — `xui.provision_key` adds the client if absent, and if a client exists with a future expiry it *extends from the existing expiry* rather than from now.

### Connection strings

If `host.public_url` is set, the user gets `{public_url}/{sub_id}` (a subscription URL). Otherwise `provision_key` falls back to the panel's `/panel/api/clients/links/{email}` and hands out the first raw link. `xui` disables TLS verification (`TCPConnector(ssl=False)`) because panels typically run self-signed certs.

### Support

`/support` creates a forum topic in `SUPPORT_GROUP_ID` and stores the tg_id↔topic_id mapping in `support_threads`. Afterwards any non-command private message is forwarded into that topic, and any reply inside the topic is copied back to the user. The catch-all forwarder is filtered by `StateFilter(None)` so it does not eat FSM input — new FSM flows work only because of that.

## Conventions

- All handler/db/xui functions are `async`; `connection: Connection` is passed explicitly (last parameter in `db`, injected by middleware in handlers).
- Callback data is colon-delimited and parsed with positional `split(":")` — keep prefixes free of extra colons or use an explicit `maxsplit` as `admin:delhost:` does.
- Expiry times are epoch **milliseconds** everywhere (panel convention), converted only for display.
- `# TODO:` comments in `handlers.py` record known pyright complaints about `call.message` / `call.data` being optional; they are unfixed, not stale.
