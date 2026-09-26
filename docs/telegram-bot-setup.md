# Personal Workspace Telegram bot setup

The Personal Workspace service uses one shared Telegram bot. The bot is disabled until its
deployment credentials are configured. Web users then enable it for their own Workspace in
Settings → Telegram.

1. Create a bot with BotFather. Record its username without `@` and its API token. Generate an
   independent random webhook secret using an approved secret generator. Add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` to the owner-only Personal Workspace
   bootstrap dotenv file outside the repository. Do not place either secret in a tracked `.env` file.
2. Set `TELEGRAM_BOT_USERNAME` and `TELEGRAM_AVAILABLE=true` in
   `config/personal-workspace/production.env`.
3. With an authorized age identity, encrypt the two Telegram values into
   `secrets/personal-workspace/telegram.sops.yaml`. The bootstrap script reads the same owner-only
   Personal Workspace dotenv source for this document and for `production.sops.yaml`. For later
   rotations, edit only the Telegram SOPS document as described in `docs/production-deploy.md`.
4. Run `make secrets-verify SOPS_AGE_KEY_FILE=/absolute/path/to/age-key` and `make validate`,
   then deploy through the normal release process. The service registers
   `https://<APP_DOMAIN>/api/personal-workspace/telegram/webhook` with Telegram at startup.
5. In Personal Workspace, enable Telegram under account settings. Create a single-use invitation
   for each participant. A participant opens it in a private chat; the owner approves the pending
   request in the same settings page.

The bot token is shared infrastructure configuration. A web invitation is a separate, short-lived
token shown once to the owner. Rotating a web invitation cancels its predecessor; rotating the bot
token requires changing the encrypted deployment secret and restarting the service.

The local development stack keeps `TELEGRAM_AVAILABLE=false` and creates empty files for bot
credentials. To test a real webhook locally, configure a publicly reachable HTTPS endpoint and
matching bot credentials; Telegram cannot deliver webhooks to `*.localhost`.
