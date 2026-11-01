#!/bin/zsh
# перезапускает бота, если он упал (например, при сбое Telegram)
cd "$(dirname "$0")"
while true; do
  .venv/bin/python -m app.bot >> data/bot.log 2>&1
  sleep 15
done
