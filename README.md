# Myatmyat

## GitHub + Manus workflow

This repository is the source of truth for the Telegram bot. Make code changes in GitHub, review the commit on `main`, and use Manus to inspect, test, and manage the project. Runtime secrets must stay outside GitHub in the environment configuration used by the machine or project that runs the bot.

### Required environment variables

```text
BOT_TOKEN=<Telegram bot token>
GITHUB_TOKEN=<GitHub token with the required repository access>
ADMIN_ID=<Telegram admin ID>
REPO_OWNER=monm76761-cloud
REPO_NAME=Myatmyat
```

For the optional dashboard metrics endpoint, configure the following values without committing their secrets:

```text
METRICS_TOKEN=<private token used by the dashboard client>
DASHBOARD_ORIGIN=https://stlinkdash-9eyizxud.manus.space
```

The bot binds its HTTP service to `0.0.0.0` and the `PORT` environment variable when a host provides one. The dashboard metrics route is `GET /api/metrics`; it requires `Authorization: Bearer <METRICS_TOKEN>` and only allows the configured dashboard origin. The Telegram bot, scan modes, admin/key system, session flow, and other runtime behavior remain unchanged.

### Local run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 bbboy.py
```

Do not commit `.env`, tokens, passwords, proxy lists, or generated runtime data. Use the project or host secret manager for those values.
