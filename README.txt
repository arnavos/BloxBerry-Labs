BLOXBERRY LABS — RENDER WEB SERVICE PACKAGE

FILES
- bot.py: complete all-in-one Discord bot (recruitment, applications, ticket system,
  /ticket add, claims, transcripts, welcome messages, auto Member role, purge N,
  and Watching BloxBerry Labs presence).
- requirements.txt: discord.py dependency.
- .gitignore: excludes secrets and runtime state.

RENDER SETTINGS
Service type: Web Service
Runtime: Python 3
Build command: pip install -r requirements.txt
Start command: python bot.py

ENVIRONMENT
Add DISCORD_TOKEN in Render > Environment. Set its value to your regenerated bot token.
Never put the token in bot.py or commit it to GitHub.

DISCORD DEVELOPER PORTAL
Enable Server Members Intent and Message Content Intent.

HTTP HEALTH CHECK
GET /, GET /health, HEAD / and HEAD /health return HTTP 200.
The response body for GET is: anshuman hi
The server binds to 0.0.0.0 and uses Render's PORT environment variable.

429 HANDLING
If Discord temporarily blocks login with HTTP 429, the process now stays alive and
retries with an increasing delay (up to 15 minutes) instead of crashing immediately.
This does not bypass Discord's rate limits; it prevents rapid Render crash/restart loops.
If the block persists, check that only one instance is running and allow the block to clear.

PERSISTENCE NOTE
The bot stores application and ticket state in bloxberry_data.json. Render's normal
ephemeral filesystem can lose this file on replacement/redeploy. Attach a persistent
disk or move state to a database if you need guaranteed long-term retention.

IMPORTANT
The HTTP health endpoint does not guarantee that a free Render service stays awake
or provide 24/7 uptime.
