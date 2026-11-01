# Cosmic ki baddie — All-in-One Discord Server Management Bot

**Bot Name:** Cosmic ki baddie

**Community:** Side Quest Studio

**Runtime:** Python 3.10+ (`discord.py >= 2.3.2`)

**Database:** Asynchronous SQLite (`aiosqlite` / threaded `sqlite3`) with built-in PostgreSQL (`asyncpg`) migration support

**Hosting:** Pre-configured for **Bot-Hosting.net / Bot-Hosting.com (Pterodactyl Panel)**, Railway, Render, and Linux VPS

**Cosmic ki baddie** is a complete, modular, production-ready Discord management, moderation, security, ticket, role-permission, and automation system engineered specifically for **Side Quest Studio**. Every setting—prefixes, role permissions, command cooldowns, moderation escalation rules, logging channels, ticket categories, custom embeds, welcome templates, activity rewards, and anti-nuke thresholds—is managed **100% inside Discord** through interactive slash/hybrid commands, dropdowns, buttons, and modals without editing Python source files.

## 1. Architecture & Project Structure (Flat Root Layout for Bot-Hosting)

All files sit directly at the root of the archive so when you click **Unarchive** in Bot-Hosting's File Manager (`/home/container`), `main.py` and `app.py` are immediately in the right place:

```
/home/container/ (Archive Root)
├── main.py                         # Primary Bot entrypoint, auto-pip installer & CosmicTree gatekeeper
├── app.py                          # Bot-Hosting default entrypoint alias (delegates to main.py)
├── bot_config.json                 # Visible JSON config for easy token pasting in Bot-Hosting File Manager
├── config.env                      # Visible env config for Bot-Hosting File Manager
├── config.py                       # Multi-source config loader (.env, config.env, bot_config.json)
├── requirements.txt                # Production Python dependencies
├── .env.example                    # Environment configuration template
├── Procfile                        # Process definition for Railway / Heroku / Render workers
├── railway.json                    # Railway Nixpacks deployment manifest
├── render.yaml                     # Render background worker deployment manifest
├── README.md                       # Complete setup, deployment, command & recovery guide
│
├── cogs/                           # 17 Modular Discord Extensions
│   ├── moderation.py               # 25 moderation commands, cases, warning escalation & modperm
│   ├── logging.py                  # 19 server/member/voice/message/ticket/config log trackers
│   ├── command_manager.py          # Enable/disable commands, cooldowns, channels, roles & aliases
│   ├── permissions.py              # Interactive role tier & granular allow/deny permission panel
│   ├── roles.py                    # Role CRUD, hierarchy, temp roles & persistent button panels
│   ├── automation.py               # Message/Voice XP, level role progression & scheduled announcements
│   ├── autoresponder.py            # Rule-based keyword autoresponders & role/reaction workflows
│   ├── welcome.py                  # Welcome & goodbye embeds, template rotation & join auto-roles
│   ├── tickets.py                  # Multi-category ticket panels, modal questions & HTML transcripts
│   ├── embeds.py                   # Interactive Embed Studio (modals, fields, link buttons, templates)
│   ├── antinuke.py                 # Sliding-window anti-nuke detectors, audit verification & quarantine
│   ├── protection.py               # Safe Mode lockdown & Normal/Enhanced/Emergency protection modes
│   ├── giveaways.py                # Giveaways with role/account-age/server-tenure requirements
│   ├── events.py                   # Community event scheduler, persistent RSVPs & automated reminders
│   ├── polls.py                    # Multiple-choice & Yes/No polls (anonymous & public progress bars)
│   ├── setup_wizard.py             # 5-step interactive /setup wizard with Prev/Next navigation
│   └── help.py                     # Dynamic /help center reflecting live command status
│
├── database/
│   ├── database.py                 # Async SQLite + PostgreSQL unified database manager & backups
│   └── migrations/
│       └── 001_initial_schema.sql  # Full 18-domain relational schema
│
├── utils/
│   ├── checks.py                   # Dynamic per-guild, per-command cooldown bucket tracker
│   ├── embeds.py                   # Branded embed builders & JSON serializer
│   ├── helpers.py                  # Duration parser, XP math, progress bars & template renderer
│   ├── permissions.py              # Central PermissionEngine & hierarchy validator
│   └── security.py                 # SlidingWindowTracker & false-positive safeguards
│
├── tests/
│   └── test_bot_verification.py    # Automated AST, database, cog, permission & command test suite
│
└── data/
    ├── cosmic_ki_baddie.db         # Pre-initialized SQLite database (persists on container disk)
    ├── backups/                    # Automatic daily SQLite database snapshots
    └── transcripts/                # Generated self-contained HTML ticket transcripts
```

## 2. Deploying on Bot-Hosting.net / Bot-Hosting.com (Step-by-Step)

**Cosmic ki baddie** is specially packaged for [Bot-Hosting.net](https://bot-hosting.net/) (`control.bot-hosting.net` Pterodactyl panel):

1. **Create or Select Your Python Server on Bot-Hosting:**
   * Log in to your panel at [Bot-Hosting.net](https://bot-hosting.net/) and open your server on `control.bot-hosting.net`.
   * Make sure the server language/egg is set to **Python** (if not, go to **Settings → Change Egg** and select **Python**).

2. **Upload & Unarchive `CosmicKiBaddie.zip`:**
   * Click the **Files** tab in the top navigation bar (this opens `/home/container`).
   * Delete any default placeholder files if present, then drag-and-drop **`CosmicKiBaddie.zip`** into the File Manager (or click **Upload**).
   * Click the **`...` (three dots)** menu on the right side of `CosmicKiBaddie.zip` and click **Unarchive**.
   * Because `CosmicKiBaddie.zip` uses a flat root layout, `main.py`, `app.py`, `bot_config.json`, `config.env`, `requirements.txt`, `cogs/`, `database/`, `utils/`, and `data/` will extract directly into `/home/container/`!

3. **Paste Your Discord Bot Token (No Hidden `.env` Needed!):**
   * In the **Files** tab, click on **`bot_config.json`** (or **`config.env`**) to open it in the built-in web editor.
   * Replace `"PASTE_YOUR_DISCORD_BOT_TOKEN_HERE"` with your actual Discord Bot Token from the Discord Developer Portal:
     ```json
     {
       "DISCORD_TOKEN": "YOUR_ACTUAL_BOT_TOKEN_HERE",
       "BOT_NAME": "Cosmic ki baddie",
       "COMMUNITY_NAME": "Side Quest Studio",
       "DEFAULT_PREFIX": "!",
       "OWNER_IDS": "YOUR_DISCORD_USER_ID"
     }
     ```
   * Click **Save Content** in the top right.

4. **Check the Startup Tab:**
   * Click the **Startup** tab in the panel.
   * **App py file:** Set to **`main.py`** (or leave as **`app.py`** — both work automatically!).
   * **Additional Python packages (optional):** You can enter `discord.py aiosqlite python-dotenv aiohttp` or leave it blank—`main.py` automatically checks and installs `requirements.txt` on first startup if `discord.py` is missing!

5. **Start the Bot:**
   * Go to the **Console** tab and click **Start** (or **Restart**).
   * Once you see `Cosmic ki baddie is online and connected to 1 guild(s)!`, head into **Side Quest Studio** on Discord and run **`/setup`**!

## 3. Discord Developer Portal Configuration & Intents

Before starting **Cosmic ki baddie**, configure your application in the [Discord Developer Portal](https://discord.com/developers/applications):

1. **Create Application & Bot:**
   * Click **New Application**, name it `Cosmic ki baddie`, and navigate to the **Bot** tab.
   * Click **Reset Token** to copy your `DISCORD_TOKEN` (paste this into `bot_config.json`, `config.env`, or `.env`).

2. **Enable Privileged Gateway Intents (REQUIRED):**
   Under **Bot → Privileged Gateway Intents**, toggle **ON** all three intents:
   * **Presence Intent**
   * **Server Members Intent** (Required for welcome/goodbye, role progression, hierarchy checks, and anti-nuke)
   * **Message Content Intent** (Required for custom prefix commands, autoresponders, message XP, and snipe/editsnipe)

3. **OAuth2 Bot Invite URL:**
   * Navigate to **OAuth2 → URL Generator**.
   * Under **Scopes**, select `bot` and `applications.commands`.
   * Under **Bot Permissions**, select `Administrator` (or at minimum: `Manage Server`, `Manage Roles`, `Manage Channels`, `Kick Members`, `Ban Members`, `Moderate Members`, `Manage Messages`, `Manage Webhooks`, `View Audit Log`, `Read Messages/View Channels`, `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`).
   * Copy the generated URL, invite **Cosmic ki baddie** to **Side Quest Studio**, and drag the bot's role near the **top of your Server Role Hierarchy** so it can manage lower roles and moderate members.

## 4. Alternative Hosting: Local, VPS, Railway & Render

### Local & VPS Installation
```
# 1. Create and activate a Python 3.11+ virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure token in bot_config.json, config.env, or .env
# 4. Run verification tests
python3 tests/test_bot_verification.py

# 5. Start Cosmic ki baddie
python3 main.py
```

### Railway & Render Deployment (`railway.json`, `render.yaml` & `Procfile` included)
* **Railway:** Connect your GitHub repo in [Railway](https://railway.app/), set `DISCORD_TOKEN` in **Variables**, and attach either a Railway PostgreSQL database (`DATABASE_URL`) or a Volume mounted at `/app/data`.
* **Render:** Connect your repo in [Render](https://render.com/) as a Background Worker and set `DISCORD_TOKEN`.

## 5. Complete Command Documentation

All commands are **Hybrid Commands**—they work both as slash commands (`/command`) and with your server's custom prefixes (`!command`).

### Initial Setup & Help

| Command | Description | 
| ----- | ----- | 
| `/setup` | Launch the 5-step interactive Setup Wizard (channels, roles, prefix, welcome, anti-nuke, summary) | 
| `/help` | Open the interactive Help Center with live category dropdown navigation | 
| `/help moderation` | View Moderation & Logging commands and real-time enabled states | 
| `/help automation` | View Automation, Activity XP, Autoresponders & Welcome commands | 
| `/help tickets` | View Ticket Management & Embed Studio commands | 
| `/help roles` | View Role Management & Permission Manager commands | 
| `/help security` | View Anti-Nuke, Safe Mode & Server Protection commands | 
| `/help events` | View Giveaways, Community Events & Polls commands | 
| `/help configuration` | View Setup Wizard, Command Manager & Prefix commands | 

### Custom Prefix System

| Command | Description | 
| ----- | ----- | 
| `/prefix view` | Display all active prefixes for Side Quest Studio | 
| `/prefix set <prefixes>` | Set primary prefix or comma-separated multiple prefixes (e.g. `!, ?, cs!`) | 
| `/prefix add <prefix>` | Add an additional prefix to the server | 
| `/prefix remove <prefix>` | Remove a prefix from the server | 

### Role Permission Manager (`/permissions`) & Command Manager (`/command`)

| Command | Description | 
| ----- | ----- | 
| `/permissions panel` | Open interactive Discord UI to assign Core Role Tiers and command rules | 
| `/permissions tier <tier> <@Role>` | Assign server role to `admin`, `moderator`, `support`, `event`, or `member` tier | 
| `/permissions set <@Role> <cmd> <allow\|deny>` | Grant or deny a role access to a command, `category:<name>`, or `*` | 
| `/permissions remove <@Role> <cmd>` | Remove a custom permission rule for a role | 
| `/permissions view` | View all configured role tiers and custom command permission rules | 
| `/permissions reset` | Reset all custom command overrides back to default server tiers | 
| `/command list [category]` | List all bot commands and their live enabled/disabled status | 
| `/command enable <target>` | Enable a command or `category:<CogName>` | 
| `/command disable <target>` | Disable a command or category (essential recovery commands are protected) | 
| `/command cooldown <cmd> <seconds>` | Set a per-user cooldown in seconds for any command | 
| `/command permissions <cmd> [@Role] [#ch]` | Inspect or update allowed roles and channels for a command | 
| `/command settings <cmd>` | Open an interactive control panel for a command (roles, channels, aliases) | 

### Moderation Suite (All 25 Core Commands + Escalation)

| Command | Description | 
| ----- | ----- | 
| `/ban <user> [delete_days] [reason]` | Ban a member or user ID with hierarchy check, DM notice & Case ID | 
| `/unban <user_id> [reason]` | Unban a user ID and log moderation case | 
| `/kick <@member> [reason]` | Kick a member from the server | 
| `/timeout <@member> <duration> [reason]` | Timeout a member (e.g. `10m`, `2h`, `7d`) | 
| `/untimeout <@member> [reason]` | Remove an active timeout from a member | 
| `/warn <@member> <reason>` | Issue an official warning and trigger automatic escalation if threshold met | 
| `/warnings <@member>` | View all active warnings for a member | 
| `/clearwarnings <@member> [reason]` | Pardon and clear all active warnings for a member | 
| `/escalation set <count> <action> [dur]` | Auto-punish (`timeout`, `mute`, `kick`, `ban`) at N active warnings | 
| `/escalation remove <count>` | Remove a warning escalation rule | 
| `/modperm <@Role> <actions>` | Configure which punishment actions a specific moderator role can run | 
| `/purge <amount> [@member]` | Bulk-delete 1–200 messages in the channel | 
| `/slowmode <seconds> [#channel]` | Set slowmode delay on a text channel | 
| `/lock [#channel] [reason]` | Lock a channel so regular members cannot send messages | 
| `/unlock [#channel] [reason]` | Unlock a previously locked channel | 
| `/hide [#channel] [reason]` | Hide a channel from `@everyone` | 
| `/unhide [#channel] [reason]` | Make a hidden channel visible to `@everyone` | 
| `/nickname <@member> [new_nickname]` | Change or reset a member's server nickname | 
| `/role <@member> <@Role>` | Toggle (assign or remove) a role on a member | 
| `/mute <@member> [duration] [reason]` | Mute a member using the server Mute role (with optional auto-expiry) | 
| `/unmute <@member> [reason]` | Unmute a member by removing the server Mute role | 
| `/snipe` | View the most recently deleted message in the current channel | 
| `/editsnipe` | View the most recently edited message's before/after text | 
| `/userinfo [@member]` | Display profile, roles, warnings, and XP/level stats | 
| `/serverinfo` | Display server overview, channel/role counts, and security posture | 
| `/modlogs [@user]` | View recent moderation cases for the server or a specific user | 
| `/case <case_number>` | Inspect full details of a moderation Case ID | 
| `/reason <case_number> <new_reason>` | Update the reason for an existing moderation Case ID | 

### Role Management, Activity Progression, Automation & Autoresponders

| Command | Description | 
| ----- | ----- | 
| `/roles create <name> [color] [hoist] [mentionable]` | Create a new server role | 
| `/roles edit <@Role> [new_name] [color] [hoist]` | Edit an existing role's properties | 
| `/roles color <@Role> <hex_color>` | Modify a role's hex color | 
| `/roles position <@Role> <position>` | Reorder a role in the hierarchy | 
| `/roles delete <@Role>` | Delete a server role | 
| `/roles temp <@member> <@Role> <duration>` | Assign a temporary role that automatically expires | 
| `/roles menu_create <name> [mode] [title]` | Create a persistent self-assignable role panel (`multiple`, `single`, `verify`) | 
| `/roles menu_add_role <menu> <@Role> <label> [emoji]` | Add a role button to a role menu | 
| `/roles menu_publish <menu> [#channel]` | Publish a persistent button role menu | 
| `/rank [@member]` | View your current activity XP, level, voice minutes, and progress bar | 
| `/leaderboard` | Display the top 15 most active members in Side Quest Studio | 
| `/activity config ...` | Configure message XP, voice XP/min, cooldown, and level-up channel | 
| `/activity reward_add <@Role> <type> <threshold>` | Award a role at `level`, `messages`, `voice_minutes`, or `xp` milestone | 
| `/activity reward_remove <@Role>` | Remove an activity role reward | 
| `/automation schedule <name> <#ch> <interval> <repeat> <msg>` | Create a one-time or repeating scheduled announcement | 
| `/automation rule_add <name> <event> [@Role] [#ch] [msg]` | Create an event-driven workflow (`member_join`, `reaction_add`, etc.) | 
| `/automation list` / `/automation delete <id>` | View or remove scheduled tasks and workflow rules | 
| `/autoresponder create <name> <triggers> <response> ...` | Create a keyword trigger (`contains`, `exact`, `startswith`, `regex`) | 
| `/autoresponder edit` / `delete` / `list` / `enable` / `disable` | Manage all keyword autoresponders in Discord | 

### Tickets, Embed Studio, Welcome/Goodbye, Logging, Security & Community Events

| Command | Description | 
| ----- | ----- | 
| `/ticket setup <#category> <#transcripts> <@SupportRole>` | Configure ticket category, HTML transcript channel & support role | 
| `/ticket panel [#channel]` | Publish a persistent multi-category Ticket Creation Panel | 
| `/ticket create [category] [topic]` | Open a private support ticket | 
| `/embed create <name>` / `/embed edit <name>` | Launch the interactive Embed Studio with Modals, Fields & Link Buttons | 
| `/embed publish <name> [#channel]` / `list` / `delete` | Publish, list, or delete saved embed templates | 
| `/welcome channel <#ch>` / `message` / `autorole` / `template_add` / `test` | Configure welcome messages, templates, and auto-roles | 
| `/goodbye config <#ch> <enabled> <message>` | Configure goodbye messages | 
| `/logging channel <category> <#ch>` / `toggle` / `style` | Route 19 log events across dedicated channels and customize styles | 
| `/antinuke view` / `threshold` / `punishment` / `trust_user` / `trust_role` / `incidents` | Configure anti-nuke sliding-window thresholds and trusted bypass lists | 
| `/safemode enable` / `disable` / `status` | Engage or lift Safe Mode temporary security lockdown | 
| `/protection mode <normal\|enhanced\|emergency>` / `lockdown` / `unlockdown` | Switch server protection posture or lock/unlock public channels | 
| `/giveaway create` / `end` / `reroll` / `list` / `cancel` | Run giveaways with role, account-age, and server-tenure checks | 
| `/events create` / `list` / `cancel` / `history` | Host community events with persistent RSVP buttons and auto-reminders | 
| `/poll create` / `yesno` / `end` / `list` | Run anonymous or public polls with live Unicode progress bars | 

## 6. Database Backups, Recovery & PostgreSQL Migration

* **Automatic Daily Backups:** When running on SQLite, `CosmicBot` automatically creates timestamped snapshots in `data/backups/cosmic_ki_baddie_YYYYMMDD_HHMMSS.db` every 24 hours and retains the most recent `MAX_BACKUP_RETENTION` (default `15`) copies.

* **Restoring from a Backup on Bot-Hosting:**
  1. Stop the server in the **Console** tab.
  2. In the **Files** tab, open `data/backups/`, download or copy your desired snapshot, and replace `data/cosmic_ki_baddie.db`.
  3. Start the server—all persistent views (role menus, ticket panels, giveaways, events, polls) automatically re-bind on startup.

* **Migrating to PostgreSQL:**
  Set `DATABASE_URL=postgresql://user:password@host:5432/dbname` in `bot_config.json`, `config.env`, or `.env`. On startup, `DatabaseManager` automatically adapts the schema DDL and executes all queries through an `asyncpg` connection pool.

## 7. Troubleshooting Guide

* **`can't open file '/home/container/main.py'` or `app.py` on Bot-Hosting:** Make sure you clicked **Unarchive** on `CosmicKiBaddie.zip` directly inside `/home/container` so `main.py` and `app.py` are in the root folder, not inside a nested subfolder.
* **Slash commands don't appear immediately:** Global slash commands can take up to 1 hour to propagate across Discord's CDN. For instant sync in Side Quest Studio, set `"DEV_GUILD_ID": "YOUR_SERVER_ID"` in `bot_config.json` and restart.
* **`discord.errors.Forbidden: 403 Forbidden (error code: 50013): Missing Permissions` when assigning roles or moderating:** Open **Server Settings → Roles** in Discord and drag the **Cosmic ki baddie** role above all staff/member roles it needs to manage.
* **Prefix commands or Autoresponders aren't responding:** Verify that **Message Content Intent** is toggled **ON** in the Discord Developer Portal under **Bot → Privileged Gateway Intents**.