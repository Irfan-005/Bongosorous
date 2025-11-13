# Deploying to Render

This Discord bot is ready to deploy on Render. Follow these steps:

## Files Included

- `main.py` - Your bot code
- `requirements.txt` - Python dependencies
- `Procfile` - Tells Render how to start the bot
- `render.yaml` - Render configuration (optional, for Blueprint deployment)
- `runtime.txt` - Specifies Python version

## Deployment Steps

### Option 1: Quick Deploy (Manual)

1. **Push your code to GitHub**
   - Create a new GitHub repository
   - Push all files to the repository

2. **Create a new Web Service on Render**
   - Go to https://render.com
   - Click "New +" → "Web Service"
   - Connect your GitHub repository

3. **Configure the service**
   - **Name**: `bongosorous-bot` (or any name you like)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`

4. **Add Environment Variables**
   Click "Advanced" and add these environment variables:
   - `DISCORD_BOT_TOKEN` = Your Discord bot token (get from https://discord.com/developers/applications)
   - `HUGGINGFACE_API_KEY` = Your Hugging Face API key (optional, for AI features)
   - `BOT_OWNER_ID` = Your Discord user ID (optional)
   - `PORT` = `5000` (Render will set this automatically, but you can set it)

5. **Deploy**
   - Click "Create Web Service"
   - Render will build and deploy your bot

### Option 2: Blueprint Deploy (Automatic)

1. Push your code to GitHub (including `render.yaml`)
2. Go to Render Dashboard → "Blueprints"
3. Click "New Blueprint Instance"
4. Connect your repository
5. Render will read `render.yaml` and set everything up automatically
6. Add your environment variables in the Render dashboard

## Getting Your Discord Bot Token

1. Go to https://discord.com/developers/applications
2. Create a new application (or select existing)
3. Go to "Bot" section
4. Click "Reset Token" to get your bot token
5. Copy and save it (you won't see it again!)
6. **Important**: Enable these Privileged Gateway Intents:
   - ✅ MESSAGE CONTENT INTENT
   - ✅ SERVER MEMBERS INTENT
7. Go to OAuth2 → URL Generator
8. Select scopes: `bot` and `applications.commands`
9. Select bot permissions you need (Administrator for full features)
10. Use the generated URL to invite the bot to your server

## Health Check

Render will check if your bot is healthy by pinging:
- `http://your-bot-name.onrender.com/` 
- `http://your-bot-name.onrender.com/health`

Both endpoints return JSON confirming the bot is running.

## Troubleshooting

**Bot keeps restarting?**
- Check the logs in Render dashboard
- Make sure `DISCORD_BOT_TOKEN` is set correctly
- Verify bot intents are enabled in Discord Developer Portal

**Slash commands not showing?**
- Wait 1-2 minutes after bot starts (Discord needs time to sync)
- Make sure bot has `applications.commands` scope
- Check Render logs for "Synced X slash commands" message

**Bot goes offline after 15 minutes?**
- This is normal on Render's free tier (spins down after inactivity)
- Upgrade to a paid plan for 24/7 uptime
- Or use a service like UptimeRobot to ping your `/health` endpoint every 5 minutes

## Features

✅ Slash commands (/, /ask, /trivia, /rps, /poll, /daily, /help)
✅ Prefix commands (!, !ask, !balance, !give, !remindme, !kick, !ban, !purge)
✅ XP and leveling system
✅ Economy (coins, daily rewards)
✅ Trivia game
✅ Polls
✅ Reminders
✅ Moderation commands
✅ Reaction roles
✅ AI chat (with Hugging Face API key)
✅ Health check endpoint for monitoring
