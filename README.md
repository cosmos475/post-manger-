# 🤖 Ambivert

> Professional Telegram Caption & Post Management Bot

A button-driven Telegram bot for bulk-editing captions and bulk-deleting old posts across channels, groups, and forum topics — built on the **Telegram Bot API** (via [aiogram](https://docs.aiogram.dev/)), deployed as a **Render Web Service**, backed by **PostgreSQL**.

---

## 📖 Overview

Admins running a channel or group often need to fix, clean up, or remove a large batch of *already-posted* messages — without editing or deleting them one at a time by hand. Ambivert solves this with two independent tools:

- **✏️ Caption Manager** — configure a channel + a message range, then apply one or more caption transformations (find & replace, link/URL cleanup, quote removal, promotional-line removal, text injection, hyperlink wrapping) across that range in a single run.
- **🗑 Post Manager** — configure a target (channel, normal group, or forum topic) + a message range, then bulk-delete those messages sequentially.

Both tools share the same underlying job engine: only **one job runs at a time** (across both modules), progress is persisted to PostgreSQL, and a job automatically resumes from where it left off if the bot restarts mid-run.

Access is restricted to the configured owner plus anyone explicitly authorized via `/addauth` — every other user is ignored.

---

## ✨ Features

### ✏️ Caption Manager

Caption Manager is **configuration-only** on each feature screen — you enable/configure features independently, then run everything together from **▶️ Preview & Run**, the single execution point.

| Feature | What it does |
|---|---|
| 🔤 **Find & Replace** | Whole-word, case-insensitive find/replace. If the matched text is part of a Telegram hyperlink, the link is safely removed and replaced with plain text. |
| 🧹 **Caption Cleanup → Remove Direct URLs** | Strips plain-text URLs (`http://`, `https://`, `www.`, `t.me/`, `telegram.me/`). |
| 🧹 **Caption Cleanup → Remove Hyperlink Formatting** | Removes Telegram hyperlink formatting while keeping the visible text. |
| 🧹 **Caption Cleanup → Quote Removal** | Strips blockquote/expandable-blockquote *formatting* only — the underlying text is always preserved. |
| 🚫 **Promotional Line Remover** | Removes any entire line containing a trigger phrase (case-insensitive). A fixed set of default triggers (e.g. "Join channel", "Subscribe", "Click here") is always active when enabled; you can add/remove your own custom triggers on top. |
| 💉 **Caption Injector** | Appends your own custom text to the bottom of every processed caption. |
| 🔗 **Add Hyperlink** | Wraps the **entire final caption** in one clickable link to a configured URL — always the last step, replacing any existing hyperlinks. |
| ▶️ **Preview & Run** | The only execution point. Runs one dry-run scan reflecting every currently enabled feature, shows would-edit/skip/fail counts plus an Active Features checklist, then asks for confirmation before the real run starts. A cached preview is reused if nothing changed since the last scan. |

**Fixed processing pipeline (in order):**
```
1. Remove Direct URLs
2. Remove Hyperlink Formatting
3. Quote Removal
4. Promotional Line Remover
5. Find & Replace
6. Caption Injector
7. Add Hyperlink
```

> ⚠️ Saving a value (a find/replace word, injector text, a URL) only stores it — nothing runs until you explicitly tap **Enable** on that feature's own screen.

### 🗑 Post Manager

Post Manager is fully independent of Caption Manager — separate target, separate range, separate configuration.

| Feature | What it does |
|---|---|
| 🎯 **Configure Target** | Three supported source types: **Channel** (forward a post to detect it), **Normal Group** (send `/settarget` inside the group), or **Forum Topic** (send `/settarget` inside that specific topic). |
| 🗂️ **Delete Range** | **Channel:** forward the first and last post of the range. **Group/Forum Topic:** paste the first and last message **links** instead (the Bot API can't expose original message IDs for forwarded group/topic posts). |
| 👁️ **Preview** | Shows target, operation type, total message count, range, configured delay, and an estimated completion time before you confirm. |
| Sequential deletion | Messages are deleted one at a time, respecting the configured delay — no bulk-delete API call is used. |

### 🌐 Shared Across Both Modules

- ⏸️ Pause / ▶️ Resume / ⏹️ Stop (with confirmation) any running job.
- 🔄 Automatic resume after a crash/restart, continuing from the last processed message.
- 📊 One shared **Job Status** screen for both Caption Manager and Post Manager jobs.
- ⚙️ Configurable global **delay** (1.0–3.0s between actions), shared by both modules.
- 🔒 **Single active job** — only one job (of either type) can run at a time; configuration screens are blocked with a friendly alert while a job runs.
- 📝 Per-message logs (edited/skipped/failed), with a "recent failures" view.
- 🛡 **Keep Alive** — configurable background health-pinging to help keep the Render free-tier service from sleeping (see below).
- Fully **button-driven UI** — only `/start`, `/cancel`, `/help`, `/settarget`, and the owner-only `/addauth`, `/removeauth`, `/listauth` are typed commands.
- 🔐 Owner + authorized-users access, enforced on every incoming update.

---

## 🧭 Complete UI / Button Guide

Every screen includes a way back to the Main Menu (or its parent screen) — you're never forced to retype `/start`.

### Main Menu
```
📡 Configure Channel
📂 Caption Manager
🎯 Set Processing Range
▶️ Preview & Run
📮 Post Manager
📊 Job Status
🟢 Keep Alive
⚙️ Settings
❓ Help
```
| Button | Purpose | Leads to |
|---|---|---|
| 📡 Configure Channel | Set/replace Caption Manager's target channel | Forward-a-post flow |
| 📂 Caption Manager | Open the Caption Manager feature list | Caption Manager submenu |
| 🎯 Set Processing Range | Set Caption Manager's start/end message range | Two-step forward flow |
| ▶️ Preview & Run | Scan the configured range with all enabled features, then confirm to start | Preview screen → confirm → job starts |
| 📮 Post Manager | Open the Post Manager submenu | Post Manager submenu |
| 📊 Job Status | View the current/most recent job's progress | Job Status screen |
| 🟢 Keep Alive | Open Keep Alive controls | Keep Alive submenu |
| ⚙️ Settings | Adjust shared settings (delay) | Settings submenu |
| ❓ Help | Open the in-bot guide | Help menu |

### ✏️ Caption Manager Submenu
```
🔤 Find & Replace
🧹 Caption Cleanup
🚫 Promotional Line Remover
💉 Caption Injector
🔗 Add Hyperlink
🏠 Main Menu
```
Each feature screen shows its current status (Enabled ✅ / Disabled ❌), an Enable/Disable toggle, and any feature-specific config button (e.g. "Set Text", "Set URL"). Caption Cleanup's screen bundles **three** independent toggles: Remove Direct URLs, Remove Hyperlink Formatting, and Quote Removal.

### 🗑 Post Manager Submenu
```
🎯 Configure Target
🗂️ Delete Range
👁️ Preview
📊 Status
🏠 Main Menu
```

### 📊 Job Status Screen
Shows: job ID + status (🚀 Running / ⏸️ Paused / ⏹️ Stopped / ✅ Completed / ❌ Failed), current operation, progress (`processed/total`, %), edited/deleted, skipped, failed, remaining count, elapsed time, and the range or find/replace words. While a job is running or paused, this screen shows **⏸️ Pause / ▶️ Resume** and **⏹️ Stop** (Stop asks for confirmation first).

### 🟢 Keep Alive Submenu
```
🔄 Ping Now
⚙️ Keep Alive Settings
📊 Status
🏠 Main Menu
```
**⚙️ Keep Alive Settings** opens a mode-selection screen (🖐 Manual / 🤖 Auto / 🛡 Task Protection) plus interval presets (5/7/9/10/12/15 min), each showing the currently selected option with a ✅ marker.

### 🆘 Help Menu
```
📖 About This Bot
🔤 Caption Manager Guide
📮 Post Manager Guide
🟢 Keep Alive & Settings Guide
🏠 Main Menu
```

### ⚙️ Settings Submenu
```
⏱️ Delay
🏠 Main Menu
```

### While a Job Is Active
```
⏸️ Pause  /  ▶️ Resume
⏹️ Stop (asks for confirmation)
```

---

## 🤖 Commands

| Command | Who | Purpose |
|---|---|---|
| `/start` | Anyone (owner/authorized see the menu; others get a "private bot" notice) | Opens the Main Menu |
| `/cancel` | Owner/authorized | Aborts whatever setup step is in progress, returns to Main Menu |
| `/help` | Owner/authorized | Opens the Help menu |
| `/settarget` | Owner/authorized, sent inside a group or forum topic | Sets that group/topic as Post Manager's target |
| `/addauth <user_id>` | Owner only | Grants another Telegram user access to the bot |
| `/removeauth <user_id>` | Owner only | Revokes a previously authorized user's access |
| `/listauth` | Owner only | Lists all currently authorized users |

---

## 🆘 Help Button

The **❓ Help** button (also reachable via `/help`) is purely informational — it doesn't touch any job or database logic. It opens an index with four guides:

- **📖 About This Bot** — what the bot does, and the two-tool split (Caption Manager vs. Post Manager).
- **🔤 Caption Manager Guide** — setup order (Configure Channel → Set Range), a walkthrough of the available features, the "Enable ≠ Saved" rule, and how to run.
- **📮 Post Manager Guide** — target types (Channel / Group / Topic), how to set the delete range for each, and the preview-then-run flow.
- **🟢 Keep Alive & Settings Guide** — a short reference for Ping Now, Status, and the Delay setting.

---

## 🛡 Keep Alive

A supporting feature that helps prevent Render's free-tier Web Service from idling out, by periodically pinging the bot's own `/health` endpoint.

**Three modes** (persisted in the database, survive restarts):

| Mode | Behavior |
|---|---|
| 🖐 **Manual** | No background pinging. Tap **🔄 Ping Now** any time to send one ping on demand. |
| 🤖 **Auto** | Background loop pings `/health` on a fixed interval, regardless of whether a job is running. |
| 🛡 **Task Protection** | Background loop pings `/health` on the fixed interval **only while a Caption Manager or Post Manager job is actively running** — idle otherwise. |

- **Interval presets:** 5 / 7 / 9 / 10 / 12 / 15 minutes (default: 9 minutes), selectable from **⚙️ Keep Alive Settings**.
- **Status screen** shows: last ping time, last HTTP status (or the error, if the last ping failed), the `/health` endpoint, current mode, interval, and — in Task Protection mode — whether protection is currently 🟢 Active or ⚪ Idle.
- **Failure isolation:** a failed automatic ping only updates the status display — it never affects Caption Manager, Post Manager, or the background loop itself; the next scheduled ping continues normally.
- Manual Ping's original behavior is completely unchanged by any of this.

---

## 📊 Job / Progress UI

While a Caption Manager job runs, the same message is edited in place to show a live status card:

```
╔════❰ Caption editing status ❱═❍⊱❁۪۪
║╭━━━━━━━━━━━━━━━➣
║┣⪼📍 Current/Total : 42/500
║┃
║┣⪼✅ Edited : 40
║┃
║┣⪼👥 Skipped : 2
║┃
║┣⪼❌ Failed : 0
║┃
║┣⪼📊 Status: ✏️ Editing
║┃
║┣⪼𖨠 Percentage: 8 %
║┃
║┣⪼⏱ ETA: 3m 12s
║╰━━━━━━━━━━━━━━━➣
╚════❰ ᴘʀᴏɢʀᴇssɪɴɢ ❱══❍⊱❁۪۪
```

The **Status** line switches to `😴 Sleeping Ns` during the inter-message delay pause, and back to `✏️ Editing` once processing resumes. On completion or failure, the same card is edited one last time with `✅ Completed` / `❌ Failed` and the footer `ᴄᴏᴍᴘʟᴇᴛᴇᴅ` / `ꜰᴀɪʟᴇᴅ`.

Post Manager progress updates are sent as separate messages (not edited in place) showing `Progress`, `Deleted`, `Skipped`, and `Failed` counts every 20 messages.

**Job statuses:** 🚀 Running → ⏸️ Paused (resumable) → ▶️ Resumed, or ⏹️ Stopped (permanent, not resumable), ✅ Completed, ❌ Failed — all visible from **📊 Job Status**.

---

## 📖 How to Use

### Caption Editing
1. Open the bot with `/start`.
2. Tap **📡 Configure Channel** — forward any post from your channel (bot must be admin there with Edit Message rights).
3. Tap **🎯 Set Processing Range** — forward the first post, then the last post of the range.
4. Open **📂 Caption Manager** and configure/enable whichever features you need (Find & Replace, Caption Cleanup, Promotional Line Remover, Caption Injector, Add Hyperlink).
5. Tap **▶️ Preview & Run** — review the scan results and Active Features checklist.
6. Confirm to start the run.
7. Monitor live progress on the job card, or check **📊 Job Status** anytime; Pause/Resume/Stop as needed.
8. View final stats (Edited/Skipped/Failed) on the completion card.

### Post Deletion
1. Open **📮 Post Manager**.
2. Tap **🎯 Configure Target** — pick Channel (forward a post), Normal Group, or Forum Topic (`/settarget` inside the group/topic).
3. Tap **🗂️ Delete Range** — forward first/last post (channel) or paste first/last message link (group/topic).
4. Tap **👁️ Preview** — review target, total messages, range, delay, and estimated time.
5. Confirm to start deleting.
6. Monitor progress via the periodic progress messages or **📊 Job Status**.
7. View final stats once the job completes.

---

## ⚙️ Requirements / Setup

| Requirement | Notes |
|---|---|
| Python | 3.11+ (pinned to `3.11.0` in `.python-version`) |
| Telegram Bot Token | From [@BotFather](https://t.me/BotFather) |
| PostgreSQL database | Any reachable Postgres instance (Neon free tier is the intended target) |
| Public HTTPS URL | Required for the webhook — Render provides this automatically |
| Scratch chat (Caption Manager only) | A private group the bot admins, used only to read captions via `forwardMessage` (see below) |

---

## 🔐 Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `BOT_TOKEN` | ✅ Yes | — | Bot token from @BotFather |
| `OWNER_ID` | ✅ Yes | — | Your numeric Telegram user ID — the primary owner |
| `WEBHOOK_URL` | ✅ Yes | — | Public base URL of the deployed service (no trailing slash) |
| `WEBHOOK_SECRET` | ✅ Yes | — | Random secret validating that webhook requests come from Telegram |
| `DATABASE_URL` | ✅ Yes | — | PostgreSQL connection string |
| `SCRATCH_CHAT_ID` | ✅ Yes* | — | Private group chat ID used to read captions (*required for Caption Manager to function; the bot starts without it but logs a warning and Caption Manager operations will fail until it's set) |
| `WEBHOOK_PATH` | ❌ No | `/webhook` | URL path Telegram POSTs updates to |
| `PORT` | ❌ No | `10000` | Port the server binds to (Render sets this automatically) |

> Delay, Caption Manager feature toggles/values, Post Manager's target/range, and Keep Alive's mode/interval are all configured **through the bot's UI** and stored in the database — no extra environment variables needed for those.

---

## 🚀 Deployment Guide

### Render (primary supported platform)

1. Push this repository to GitHub.
2. Create a new **Web Service** on [Render](https://render.com) and connect the repo.
3. Set the required environment variables from the table above (or use the included `render.yaml` — `WEBHOOK_SECRET` auto-generates).

**Build Command:**
```bash
pip install -r requirements.txt
```

**Start Command:**
```bash
python main.py
```

4. Deploy. On first startup the bot automatically:
   - Registers its webhook using `WEBHOOK_URL` + `WEBHOOK_PATH`.
   - Runs idempotent database schema migrations (`ALTER TABLE ADD COLUMN IF NOT EXISTS`) — safe on every redeploy.
   - Resumes any job that was `RUNNING` when the process last stopped.
   - Loads the saved Keep Alive mode/interval and starts its background loop if configured to Auto or Task Protection.

**Notes:**
- `render.yaml` already sets `healthCheckPath: /health`.
- Free-tier services sleep after inactivity and cold-start on the next request; **Keep Alive**'s Auto/Task Protection modes exist specifically to reduce this.
- No other hosting platform is tested against this codebase, but any platform supporting a persistent Python web process, a public HTTPS URL, and outbound Postgres connectivity should work in principle with the same build/start commands.

### Local Setup

```bash
git clone <your-repo-url>
cd post-utility--main
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
python main.py
```
Telegram webhooks require a public HTTPS URL — use a tunnel (e.g. `ngrok http 10000`) for local testing and set `WEBHOOK_URL` to the tunnel URL.

---

## 🧰 Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Telegram Framework | [aiogram](https://docs.aiogram.dev/) `>=3.29,<4.0` (Bot API, webhook-based) |
| Database | PostgreSQL, via [asyncpg](https://magicstack.github.io/asyncpg/) `>=0.30,<1.0` |
| HTTP/Web server | [aiohttp](https://docs.aiohttp.org/) `>=3.14,<4.0` |
| Deployment | Render (Web Service, free tier) |
| Update delivery | Webhook (not long-polling) |

---

## 📦 Important Libraries / APIs

| Purpose | Library / API |
|---|---|
| Telegram bot framework | `aiogram` (Bot API wrapper, FSM, routers, inline keyboards) |
| Async web server & HTTP client | `aiohttp` (serves the webhook + `/health`; also used for Keep Alive's own outbound pings) |
| Database driver | `asyncpg` (connection-pooled PostgreSQL access) |
| Standard library | `hashlib` (preview cache fingerprinting), `re`, `datetime`, `zoneinfo` (IST timestamps for Keep Alive) |

**Key endpoints/mechanisms actually used:**
- Telegram Bot API `editMessageCaption`, `forwardMessage`, `deleteMessage`, `setWebhook`, `sendMessage`.
- Internal `GET /health` — a lightweight, dependency-free endpoint (`web.Response("ok")`) used by Render's own health checks, Manual Ping, and Keep Alive's Auto/Task Protection loop.
- Internal `POST {WEBHOOK_PATH}` — receives Telegram updates.

No dependency is used beyond these three packages — everything else is Python standard library.

---

## 📁 Repository Structure

```text
post-utility--main/
├── main.py                        # Entrypoint: aiohttp app, webhook + /health routes, startup/shutdown
├── config.py                      # Loads and validates environment variables
├── requirements.txt
├── render.yaml                    # Render service definition
├── .env.example                   # Environment variable template
├── .python-version
│
├── bot/                            # Telegram-facing layer
│   ├── dispatcher.py                # Registers auth middleware + all routers
│   ├── keyboards.py                 # All inline keyboard builders
│   ├── progress_ui.py               # Pure progress/status card text formatting
│   ├── middlewares/
│   │   └── auth.py                    # Owner + authorized-users access control
│   └── handlers/
│       ├── start.py                   # /start, /cancel, main menu
│       ├── help.py                    # /help + Help menu screens
│       ├── auth_commands.py           # /addauth, /removeauth, /listauth
│       ├── channel_setup.py           # Caption Manager channel configuration
│       ├── range_setup.py             # Caption Manager processing range
│       ├── words_setup.py             # Find & Replace configuration
│       ├── job_control.py             # Caption Manager features, Preview & Run, pause/resume/stop, Settings
│       ├── job_status.py              # Shared Job Status screen
│       ├── post_manager_setup.py      # Post Manager target + range configuration
│       ├── post_manager_control.py    # Post Manager preview + job start
│       └── keep_alive.py              # Manual Ping, Keep Alive Settings, Status
│
├── core/                            # Business logic (no Telegram-specific code)
│   ├── caption_engine.py              # All caption transformation logic (7-step pipeline)
│   ├── job_manager.py                 # Job lifecycle: start/pause/resume/stop, single-active-job enforcement
│   ├── job_runner.py                  # Executes a job message-by-message, delay handling, dry-run preview
│   ├── keep_alive_manager.py          # Independent Keep Alive background service (modes, interval, loop)
│   └── telegram_ops.py                # Wrapped Telegram API calls (retry/backoff)
│
└── db/                              # Persistence layer
    ├── connection.py                  # asyncpg pool management
    ├── models.py                      # Dataclasses mirroring the schema
    └── queries.py                     # Schema DDL (idempotent migrations) + all SQL queries
```

---

## 🩺 Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot doesn't respond at all | `OWNER_ID` mismatch, or webhook not registered | Confirm your numeric Telegram ID matches `OWNER_ID`; check startup logs for webhook registration |
| Bot replies "This bot is private" | Your user ID isn't the owner and isn't authorized | Ask the owner to run `/addauth <your_user_id>` |
| "Please set a processing range first" despite setting it | Range wasn't actually saved, or channel was reconfigured after | Re-check **🎯 Set Processing Range** |
| "No features enabled" on Preview & Run | None of the Caption Manager features are toggled **Enabled** | Saving a value doesn't enable a feature — tap Enable explicitly on that feature's screen |
| Job stuck / can't start a new one | A previous job is still `RUNNING`/`PAUSED` | Check **📊 Job Status**, then Stop or Resume the existing job first |
| Bot can't read/edit captions | Bot isn't admin in the channel, or the scratch group is misconfigured | Recheck both sets of admin permissions (see below) |
| Keep Alive ping shows "Failed" | Transient network issue or `WEBHOOK_URL` misconfigured | Check the `Error` line on the Keep Alive Status screen; this never affects other bot functionality |
| Render free-tier cold start delay | Free Web Services sleep after inactivity | Expected on the free tier; enable Keep Alive's Auto or Task Protection mode to reduce it |
| `DATABASE_URL` / connection errors on startup | Invalid or unreachable Postgres connection string | Verify the connection string and that the database allows external connections |

---

## ⚠️ Important Notes

- **Access control:** the owner (`OWNER_ID`) always has access; additional users can be granted access via `/addauth`. Everyone else is silently ignored (except a one-time "private bot" notice on `/start`).
- **Required Telegram permissions:**
  | Component | Required permission |
  |---|---|
  | Caption Manager's target channel | Bot must be admin with **Edit Messages** rights |
  | Scratch chat (private group) | Bot must be admin with **Delete Messages** rights, non-anonymous admin mode |
  | Post Manager's target (channel/group/topic) | Bot must be admin with **Delete Messages** rights |
- **Single active job:** enforced across both Caption Manager and Post Manager — only one job (of either type) can be `RUNNING`/`PAUSED` at a time, checked against the database.
- **Processing range:** message-ID based, inclusive on both ends. IDs in the range are assumed contiguous — gaps or already-deleted messages are handled as "Skipped" during the run, not detected beforehand.
- **Delay:** shared by both modules, adjustable from 1.0–3.0 seconds via **Settings → Delay**, to reduce Telegram flood-limit risk.
- **Resume after restart:** a job left `RUNNING` when the process stops (crash/redeploy) is automatically resumed from its last persisted cursor on the next startup. A job the user deliberately `PAUSED` stays paused until manually resumed.

---

## 📄 License

No `LICENSE` file is present in this repository. Licensing terms are not specified — check with the repository owner before reuse or redistribution.
