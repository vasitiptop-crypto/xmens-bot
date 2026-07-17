#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "============================================="
echo "  Serv00 Telegram Bot Setup Script           "
echo "============================================="

# Detect absolute path of the script directory
APP_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$APP_DIR"

echo "Application directory: $APP_DIR"

# 1. Enable Python 3 virtual environment
echo "--> Creating Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# 2. Install pip requirements
echo "--> Upgrading pip and installing requirements..."
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# 3. Create .env configuration
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "--> Creating .env configuration..."
    read -p "Enter Telegram Bot Token: " BOT_TOKEN
    read -p "Enter Telegram Channel ID: " CHANNEL_ID
    
    cat <<EOF > "$ENV_FILE"
BOT_TOKEN="$BOT_TOKEN"
CHANNEL_ID="$CHANNEL_ID"
EOF
    echo "Saved credentials to $ENV_FILE"
else
    echo "--> .env file already exists. Skipping."
fi

echo "============================================="
echo "  Setup complete!                            "
echo "============================================="
echo "You can test the bot manually by running:"
echo "venv/bin/python batch_run.py"
echo "============================================="
