# Deploy to Google Cloud Run

This bot ships with a `Dockerfile`, so it deploys to Cloud Run with no extra
build configuration.

## 1. Set your project

```sh
gcloud config set project YOUR_PROJECT_ID
```

## 2. Build and deploy

```sh
gcloud run deploy telegram-caption-bot \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars RUN_MODE=webhook,WEBHOOK_PATH=/webhook
```

Cloud Run will print a service URL when this finishes (e.g.
`https://telegram-caption-bot-xxxxx.a.run.app`). Copy it — you need it next.

## 3. Set the remaining required environment variables

```sh
gcloud run services update telegram-caption-bot \
  --region us-central1 \
  --set-env-vars \
BOT_TOKEN=your_bot_token,\
OWNER_ID=your_telegram_user_id,\
WEBHOOK_URL=https://telegram-caption-bot-xxxxx.a.run.app,\
WEBHOOK_SECRET=some_long_random_string,\
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require,\
SCRATCH_CHAT_ID=-1001234567890
```

Replace `WEBHOOK_URL` with the actual URL Cloud Run gave you in step 2 — it
must match exactly (no trailing slash).

## 4. Verify

```sh
gcloud run services describe telegram-caption-bot --region us-central1
```

Visit the service URL's `/health` path — it should return `Bot is alive`.

See `.env.example` in the repo root for what each variable means and how to
obtain it (BotFather, userinfobot, Neon Postgres, etc).
