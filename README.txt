BLOXBERRY LABS DISCORD BOT — RENDER WEB SERVICE

RENDER SETTINGS
Create a Web Service (not Background Worker).
Runtime: Python 3
Build Command: pip install -r requirements.txt
Start Command: python bot.py

ENVIRONMENT VARIABLE
DISCORD_TOKEN = your regenerated Discord bot token

HTTP ENDPOINT
GET / and GET /health return plain text: anshuman hi
Binds to 0.0.0.0 using Render's PORT variable.

INCLUDED FEATURES
- Welcome in channel 1567899844062740510 with join time and member number
- Automatic Member role assignment
- Recruitment panels, applications, review and seat limits
- Applicant DMs show plain role names, not raw Discord role mentions
- Ticket panel, creation, claim/unclaim, /ticket add, transcripts and close
- purge N command
- Activity: Watching BloxBerry Labs

Enable Server Members Intent and Message Content Intent in Discord Developer Portal.
Never commit your bot token to GitHub.
The HTTP endpoint is for health checks and does not guarantee that a free Render
service stays awake. Use an always-on paid service/worker for reliable 24/7 uptime.
