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
   the normal release checks before deployment. At startup the bot registers
   `https://<APP_DOMAIN>/api/personal-workspace/telegram/webhook` with Telegram.
5. In web settings, enable the bot, create a single-use invitation, and approve the pending
   connection after the participant opens the link in a private chat.

The internal settings endpoint is blocked at the public edge and authenticates every call
with the service secret. Rotating the bot token or webhook secret affects only
`personal-workspace`. Rotate the shared service secret in both encrypted documents together.

Local development keeps `TELEGRAM_AVAILABLE=false`, generates one shared service credential,
and creates empty bot token and webhook secret files. A real local webhook needs a reachable
HTTPS endpoint; Telegram cannot deliver webhooks to `*.localhost`.
