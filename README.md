# ConPass

ConPass is a Discord FAQ, insights, and leveling bot with a Streamlit dashboard.
It is built for communities that want quick slash-command answers, usage
analytics, configurable member XP, leaderboards, level-up announcements, and role
rewards from one lightweight Python project.

Current version: **v0.1.6**

## What It Does

- Creates FAQ slash commands from your question/answer database.
- Tracks command usage insights.
- Awards configurable per-server XP from normal Discord messages.
- Supports `/rank`, `/leaderboard`, `/version`, and `/levelconfig`.
- Lets admins configure XP range, cooldown, message length, level curve, announcements, and reward roles.
- Shows and edits FAQs, insights, leveling settings, leaderboards, and rewards from a Streamlit dashboard.
- Uses SQLite locally, so there is no separate database server to install.

## Project Files

- `bot.py`: Discord bot, slash commands, message XP, role rewards, rich presence.
- `dashboard.py`: Streamlit admin dashboard.
- `storage.py`: SQLite tables, migrations, settings, XP math, FAQ and stats helpers.
- `version.py`: Current app version and release text.
- `faq.json`: Starter FAQ data that can be imported into SQLite.
- `.env.example`: Safe example config. Copy this to `.env`.
- `start.sh`: One-command launcher for the dashboard and bot.

## Requirements

- Python 3.10 or newer.
- A Discord bot token from the Discord Developer Portal.
- Message Content Intent enabled for the bot if you want message-based XP.

Install dependencies with:

```bash
pip install -r requirements.txt
```

The `start.sh` script creates and uses `.venv` automatically, so manual install is optional for normal local use.

## Setup

1. Clone the repo:

```bash
git clone https://github.com/symoaka/conpass.git
cd conpass
```

2. Create your local environment file:

```bash
cp .env.example .env
```

3. Open `.env` and set your Discord token:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=
DISCORD_SYNC_MODE=guild
DISCORD_PRESENCE_TYPE=watching
DISCORD_PRESENCE_TEXT=/help | ConPass
DISCORD_STATUS=online
```

`DISCORD_GUILD_ID` is optional. If set, slash commands sync faster to that one test server. If empty, commands sync globally and may take longer to appear.

`DISCORD_SYNC_MODE=guild` also syncs commands directly into connected servers, which makes FAQ commands added through the dashboard appear much faster than global Discord command sync. Set it to `global` if you only want global command sync.

## How To Open Everything

### One-Command Start

Run:

```bash
chmod +x start.sh
./start.sh
```

This will:

- create `.venv` if needed
- install dependencies
- start the dashboard in the background
- start the Discord bot in the foreground

Open the dashboard here:

[http://localhost:8501](http://localhost:8501)

Stop everything by pressing `Ctrl+C` in the terminal where `./start.sh` is running.

### Manual Start

Terminal 1, start the bot:

```bash
source .venv/bin/activate
python bot.py
```

Terminal 2, start the dashboard:

```bash
source .venv/bin/activate
streamlit run dashboard.py
```

Then open:

[http://localhost:8501](http://localhost:8501)

## Discord Commands

User commands:

- `/help`: show FAQ and community commands.
- `/rank`: show your rank or another member's rank.
- `/level`: alias for `/rank`.
- `/leaderboard`: show the server XP leaderboard.
- `/insights`: show command usage stats.
- `/version`: show the current ConPass version and release notes.

Admin commands:

- `/levelconfig status`
- `/levelconfig toggle`
- `/levelconfig xp`
- `/levelconfig cooldown`
- `/levelconfig min-message-length`
- `/levelconfig model`
- `/levelconfig announcements`
- `/levelconfig reward-mode`
- `/levelconfig reward add`
- `/levelconfig reward remove`
- `/levelconfig reward list`

Admin commands require Discord's **Manage Server** permission.

## Dashboard Tabs

- **FAQs**: view and delete FAQ commands.
- **Add or Update**: create or edit FAQ answers.
- **Insights**: view command usage stats.
- **Insights** also separates command usage into FAQ, Leveling, Level Admin, and General sections.
- **Leveling**: view leaderboards and configure guild leveling settings.

For dashboard leveling settings, enter a Discord guild ID and click **Load Guild**.
When the bot can resolve the server, the dashboard shows the server name beside the guild ID.
Role and channel fields use Discord numeric IDs in the dashboard; Discord slash commands provide nicer native role/channel pickers.

## Data And Safety

ConPass stores runtime data in `conpass.sqlite3`.

Ignored local files include:

- `.env`
- `.venv/`
- `conpass.sqlite3`
- logs
- runtime command stats

Do not commit your real `.env` file or bot token.

## Versioning

ConPass is currently pre-release. Updates use `v0.1.x`.

`v1.0.0` is reserved for the first public/stable release.

Every meaningful update should include:

- a version bump in `version.py`
- a new entry in `CHANGELOG.md`

## License

MIT License. See [LICENSE](LICENSE).
