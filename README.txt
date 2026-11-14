BLOXBERRY LABS DISCORD BOT - RENDER DEPLOYMENT

1. Upload bot.py and requirements.txt to a GitHub repository.
2. In Render, create a Background Worker and connect that repository.
3. Runtime: Python 3
4. Build Command:
   pip install -r requirements.txt
5. Start Command:
   python bot.py

RENDER ENVIRONMENT VARIABLE
Key: DISCORD_TOKEN
Value: Your newly regenerated Discord bot token

IMPORTANT
- Do not commit your bot token to GitHub.
- Enable Server Members Intent and Message Content Intent in the Discord Developer Portal.
- Give the bot the permissions needed for its configured features.
- This is a Background Worker; no Flask server or HTTP keep-alive endpoint is needed.
- The bot activity is Watching BloxBerry Labs.
- Welcome channel: 1567899844062740510
