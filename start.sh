#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

echo "⚡ Starting ConPass..."

# 1. Setup Virtual Environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "⬇️ Checking dependencies..."
pip install -r requirements.txt > /dev/null 2>&1

# 2. Check for .env file
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found! Please rename .env.template to .env and add your DISCORD_TOKEN."
    exit 1
fi

# 3. Start Streamlit Dashboard in the background
echo "🌐 Starting Dashboard on http://localhost:8501..."
nohup streamlit run dashboard.py > dashboard.log 2>&1 &
DASHBOARD_PID=$!

# 4. Start the Discord Bot in the foreground
echo "🤖 Starting Discord Bot (Press Ctrl+C to stop)..."
python -u bot.py

# 5. Cleanup when the bot is stopped (Ctrl+C)
echo ""
echo "🛑 Shutting down Dashboard (PID: $DASHBOARD_PID)..."
kill $DASHBOARD_PID 2>/dev/null
echo "✅ Goodbye!"
