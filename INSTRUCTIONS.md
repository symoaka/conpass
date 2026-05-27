# ConPass Bot & Dashboard - Setup Guide

## 1. Quick Start (The "One-Click" Method)
We have provided a `start.sh` script that automates everything.
1. Open your terminal in this folder.
2. Make the script executable (only needed the very first time): 
   ```bash
   chmod +x start.sh
   ```
3. Run the script: 
   ```bash
   ./start.sh
   ```

This script will automatically create your virtual environment, install packages, start the web dashboard in the background, and run your Discord bot in the foreground. When you press `Ctrl+C` to stop the bot, it will automatically shut down the dashboard too!

---

## 2. Optional Bot Presence
You can customize the Discord bot presence from your `.env` file:

```env
DISCORD_PRESENCE_TYPE=watching
DISCORD_PRESENCE_TEXT=/help | ConPass
DISCORD_STATUS=online
```

Supported presence types are `playing`, `watching`, `listening`, and `competing`.
Supported statuses are `online`, `idle`, `dnd`, `do_not_disturb`, and `invisible`.

---

## 3. Manual Start Method
If you prefer to run them separately in different tabs:

**Terminal 1 (The Bot):**
```bash
source .venv/bin/activate
python bot.py
```

**Terminal 2 (The Dashboard):**
```bash
source .venv/bin/activate
streamlit run dashboard.py
```

---

## 4. Running on a Headless Server (VPS, Linux, Ubuntu, etc.)

If you are moving this to a 24/7 headless server, you don't want the bot to stop when you close your SSH terminal window. Here are the two best ways to keep it running forever:

### Method A: Using `tmux` (Easiest)
`tmux` creates a terminal session that stays alive even after you disconnect your SSH client.

1. Install tmux: `sudo apt install tmux` (Ubuntu/Debian)
2. Start a new session: `tmux new -s conpass`
3. Inside the tmux session, run the start script: `./start.sh`
4. **Detach** from the session (leave it running in the background) by pressing: `Ctrl+b`, then release and press `d`.
5. You can now safely close your SSH connection. The bot and dashboard are running!
6. To check on the bot later, SSH back into your server and re-attach: `tmux attach -t conpass`

### Method B: Using `systemd` (Professional/Production)
This ensures the bot automatically turns back on if the server restarts or crashes.

1. Create a service file: `sudo nano /etc/systemd/system/conpass-bot.service`
2. Paste the following configuration (update `/path/to/ConPass` with your actual folder path):
```ini
[Unit]
Description=ConPass Discord Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/path/to/ConPass
ExecStart=/path/to/ConPass/.venv/bin/python /path/to/ConPass/bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```
3. Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable conpass-bot
sudo systemctl start conpass-bot
```
*(You can create a separate similar `.service` file for `streamlit run dashboard.py` using `ExecStart=/path/to/ConPass/.venv/bin/streamlit run /path/to/ConPass/dashboard.py`)*
