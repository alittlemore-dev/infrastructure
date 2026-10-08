# Personal Workspace Telegram bot setup

`personal-workspace` owns the bot token, webhook, invitations, chat IDs, and connections.
`auth-api` owns the account's per-bot enable switch in `UserModel.settings`. The web settings
page calls both services; Personal Workspace reads the switch through a protected internal API.

1. Create the bot with BotFather. Set `TELEGRAM_BOT_USERNAME` and `TELEGRAM_AVAILABLE=true` in
   `config/personal-workspace/production.env`; set `TELEGRAM_AVAILABLE=true` in
   `config/auth-api/production.env`.
2. Keep `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` in the existing owner-only Personal
   Workspace bootstrap dotenv file. Encrypt them in
   `secrets/personal-workspace/telegram.sops.yaml`.
3. Generate one independent random `TELEGRAM_SERVICE_SECRET` and put the **same value** in the
   existing owner-only Personal Workspace and Auth API bootstrap dotenv files. Encrypt it into
   `secrets/personal-workspace/telegram.sops.yaml` and
   `secrets/auth-api/production.sops.yaml`. The two services receive it as a Docker secret.
4. Run `make secrets-verify SOPS_AGE_KEY_FILE=/absolute/path/to/age-key`, `make validate`, and
   the normal release checks before deployment. `TELEGRAM_DELIVERY_MODE=polling` is configured
   for production; startup removes the registered webhook without dropping queued updates
   and receives commands through the outbound proxy pool. The API starts independently of
   Telegram, and the bot becomes `ready` after its first successful update request.
5. In web settings, enable the bot, create a single-use invitation, and approve the pending
   connection after the participant opens the link in a private chat.

The internal settings endpoint is blocked at the public edge and authenticates every call
with the service secret. Rotating the bot token or webhook secret affects only
`personal-workspace`. Rotate the shared service secret in both encrypted documents together.

Both Personal Workspace API and TaskIQ worker slots join `personal-auth-verification-network`
to read account settings from Auth API. Notification workers need this internal connection
for the account timezone and bot preferences. Without it, date reminders cannot be planned
and finance deliveries exhaust their retries even while the bot and queue are healthy.

Auth API checks the active bot backend before writing changed Telegram preferences, including
removals. Compose supplies the required `TELEGRAM_PERSONAL_WORKSPACE_STATUS_URL` from
`PERSONAL_WORKSPACE_ACTIVE_BACKEND`, targeting `/api/internal/telegram/status` directly on
port 8080. This endpoint requires `X-Telegram-Service-Secret` and returns only the runtime
status. Only `ready` allows the settings write; other states and transport failures return
`503` without changing preferences. Other preferences still save when the submitted bot
preferences are unchanged. The public `/api/personal-workspace/internal/` subtree returns
`404`; local and deployment smoke checks verify this boundary.

Local development keeps `TELEGRAM_AVAILABLE=false`, generates one shared service credential,
and creates empty bot token and webhook secret files plus a `[]` proxy list. A real local webhook needs a reachable
HTTPS endpoint; Telegram cannot deliver webhooks to `*.localhost`.

## Update delivery mode

Set the required `TELEGRAM_DELIVERY_MODE` in `config/personal-workspace/production.env`:

- `polling`: receive messages with `getUpdates` through the existing proxy pool. No incoming
  Telegram connection to the server is needed. The registered webhook is removed with
  `drop_pending_updates=false`; the endpoint and its secret remain available for switching back.
- `webhook`: register `https://<APP_DOMAIN>/api/personal-workspace/telegram/webhook` in the
  background and receive HTTPS POST requests. The public endpoint must be reachable from Telegram.

The modes use the same dispatcher, invitations, connections, preferences, and outgoing clients.
Switch modes by changing this non-secret value and releasing/deploying normally; no SOPS update
or new proxy is required. Telegram permits only one delivery mode at a time.

One expiring, bot-wide Valkey lease controls polling and webhook registration across both
deployment slots and API processes. A standby does not alter Telegram delivery configuration.
The old slot retains ownership while draining; the new slot starts delivery after the old
owner stops or its lease expires. Standby shutdown cannot withdraw the owner's readiness.
Loss of ownership or Valkey connectivity cancels polling. Telegram settings and outgoing jobs
remain blocked until the selected slot has a fresh readiness lease.

Polling processes updates sequentially and advances `offset` after a handler attempt settles.
Handler failures are logged and acknowledged to avoid replaying committed work or uncertain
outbound replies; the user can retry a failed action explicitly. An outbound transport failure
pauses the remaining, unhandled batch until route recovery. Unconfirmed updates may be delivered
again after a crash; domain idempotency rules still apply.
The receiver retries network errors through the proxy pool and preserves its offset across
route changes. Rate limits wait for Telegram's `retry_after`; ordinary sends are never replayed
by the transport. [Telegram update delivery](https://core.telegram.org/bots/api#getupdates).

Logs are emitted by the active `personal_workspace_backend_blue` or
`personal_workspace_backend_green` container. Use `docker logs --since 30m -f <container>`;
nginx logs incoming Telegram requests only in webhook mode.

## External Telegram proxy list

The proxy list routes outbound Bot API requests. It does not forward incoming webhooks.
Purchase external SOCKS5 or HTTP CONNECT services that can reach `api.telegram.org:443`;
no local proxy container or tunnel is required.

After purchase, edit the existing owner-only Personal Workspace bootstrap dotenv file locally.
Store the ordered JSON string array inside dotenv single quotes, for example:

```dotenv
TELEGRAM_PROXY_URLS='["socks5://user:password@proxy-a.example.test:1080","http://user:password@proxy-b.example.test:8080"]'
```

Percent-encode each username and password before assembling its URL: `@` becomes `%40`,
`:` becomes `%3A`, `/` becomes `%2F`, and `%` becomes `%25`. Then serialize the URLs as a
JSON array, preserving JSON quoting. Keep the value in that private file, not a command
argument, public environment file, log, or Git diff. Re-run the existing
[SOPS bootstrap procedure](production-deploy.md#one-time-local-secret-bootstrap) with the
same source files and age recipients, then run
`make secrets-verify SOPS_AGE_KEY_FILE=/absolute/path/to/age-key` and `make validate`
before the normal authorized release/deployment.

The bootstrap encrypts this key in `secrets/personal-workspace/telegram.sops.yaml`. Compose
mounts the same secret for both backend slots and TaskIQ processes and sets
`TELEGRAM_PROXY_URLS_FILE=/run/secrets/telegram_proxy_urls`. An older encrypted document
may omit only this optional key; materialization then creates an empty file for direct
access. New bootstrap output includes an explicit `[]` when the key is omitted. Development
also uses `[]`. Other missing secrets still fail validation.

The service stays on the working proxy and retains only the selected pool index in Valkey;
proxy URLs and credentials remain in the secret. Network failures can switch to another
configured proxy. Exhausting a non-empty list makes the bot unavailable and triggers periodic
retries; it never falls back to direct access. Bot/API authentication errors and rate limits
such as `429` do not switch proxies. A failed message request is not automatically replayed,
because Telegram may have accepted it before the connection failed.

After activation, verify outbound connectivity from the configured service using
[`getMe`](https://core.telegram.org/bots/api#getme), then send a test message to a chat controlled
by the operator through the service's normal bot flow. In webhook mode, check
[`getWebhookInfo`](https://core.telegram.org/bots/api#getwebhookinfo) separately: confirm the
public webhook URL, send an incoming bot command, and verify delivery rather than only successful
registration. Inspect pending updates and recent delivery errors without publishing tokens,
proxy URLs, or private chat details. A working outbound proxy does not prove the inbound
HTTPS route works.
In polling mode, `getWebhookInfo.url` must be empty. Send `/start` via an invitation and
confirm the pending connection in the web settings; no incoming nginx request is expected.

To return to direct access, set `TELEGRAM_PROXY_URLS='[]'` in the private bootstrap source,
re-encrypt with the same inputs, and perform the normal authorized release. An empty string
is also accepted for compatibility. This preserves the bot token, webhook secret, shared
service credential, and user links.
