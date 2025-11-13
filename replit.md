# Bongosorous Discord Bot

## Overview
A feature-rich Discord bot with slash commands, economy system, trivia games, moderation tools, and AI chat capabilities powered by Hugging Face.

## Project Status
**Last Updated:** November 13, 2025
**Status:** Ready for deployment to Render

## Recent Changes
- Fixed slash command registration and syncing issues
- Separated slash and prefix command implementations to prevent duplicate responses
- Fixed !ask command to work properly with context objects
- Added trivia answer detection in message handler
- Created Render deployment files (Procfile, render.yaml, runtime.txt)
- Added comprehensive deployment documentation

## Architecture

### Core Files
- `main.py` - Main bot application with all commands and features
- `requirements.txt` - Python dependencies (discord.py, flask, huggingface-hub)
- `bongobot.db` - SQLite database (auto-created on first run)

### Deployment Files
- `Procfile` - Render start command
- `render.yaml` - Render blueprint configuration
- `runtime.txt` - Python version specification
- `RENDER_DEPLOYMENT.md` - Detailed deployment guide

### Configuration
- `.gitignore` - Excludes cache, database, and environment files from version control

## Features

### Commands
**Slash Commands (/):**
- `/help` - Show all commands
- `/ask <question>` - AI chat (requires Hugging Face API key)
- `/trivia` - Start a trivia question
- `/rps <choice>` - Play rock-paper-scissors
- `/poll <question> <options>` - Create a poll
- `/daily` - Claim daily coin reward

**Prefix Commands (!):**
- `!help` - Show all commands
- `!ask <question>` - AI chat
- `!trivia` - Start trivia
- `!rps <choice>` - Rock-paper-scissors
- `!remindme <time> <message>` - Set a reminder (e.g., !remindme 10m check oven)
- `!balance [@user]` - Check coin balance
- `!daily` - Claim daily coins
- `!give @user <amount>` - Give coins to another user
- `!kick @user [reason]` - Kick a member (requires permissions)
- `!ban @user [reason]` - Ban a member (requires permissions)
- `!purge <amount>` - Delete messages (requires permissions)
- `!createreactionrole <msg_id> <emoji> @role` - Set up reaction roles

### Systems
- **XP & Leveling**: Users gain XP from messages and level up automatically
- **Economy**: Coin system with daily rewards and transfers
- **Trivia**: Interactive trivia game with rewards
- **Reminders**: Background worker checks and sends reminders
- **Reaction Roles**: Auto-assign roles when users react to messages
- **Moderation**: Kick, ban, and message purge commands
- **Health Check**: Flask endpoints for monitoring (/ and /health)

## Environment Variables

### Required
- `DISCORD_BOT_TOKEN` - Your Discord bot token from Discord Developer Portal

### Optional
- `HUGGINGFACE_API_KEY` - For AI chat features (/ask command)
- `BOT_OWNER_ID` - Your Discord user ID for owner commands
- `PORT` - Server port (default: 5000, auto-set by Render)
- `BOT_DB_PATH` - Database file path (default: bongobot.db)

## Database Schema

### Tables
1. **users**: Stores user coins, XP, level, and daily claim timestamp
2. **reminders**: Stores scheduled reminders with user, channel, and content
3. **reaction_roles**: Maps message reactions to role assignments

## Technical Details

### Bot Configuration
- **Prefix**: `!` for text commands
- **Intents**: Message content, members, messages (must be enabled in Discord Developer Portal)
- **Command Tree**: Syncs slash commands on startup with retry logic

### Flask Web Server
- Runs on separate thread alongside Discord bot
- Provides health check endpoints for Render monitoring
- Binds to 0.0.0.0:5000 for external access

### Error Handling
- Comprehensive exception handling in all commands
- Logging system tracks errors and bot activity
- Graceful fallbacks for missing API keys or permissions

## Deployment

### On Render
1. Push code to GitHub
2. Create new Web Service on Render
3. Connect repository
4. Add environment variables (DISCORD_BOT_TOKEN required)
5. Deploy

See `RENDER_DEPLOYMENT.md` for detailed instructions.

### On Replit
1. Add secrets in the Secrets tab (lock icon 🔒)
2. Run the bot using the workflow
3. Bot will auto-restart on code changes

## Known Issues & Fixes Applied

### Fixed Issues
✅ Slash commands not showing - Fixed command registration and syncing
✅ !ask not working - Created separate handler instead of reusing slash callback
✅ Bot replying twice - Separated slash and prefix implementations
✅ RPS command broken - Fixed callback reuse pattern

### Notes
- LSP warnings about imports are false positives (packages are installed via requirements.txt)
- Bot requires DISCORD_BOT_TOKEN to start (will exit gracefully if missing)
- Slash commands take 1-2 minutes to appear in Discord after sync

## Future Enhancements
- Custom shop system for economy
- More trivia questions and categories
- Advanced moderation logging
- Custom embeds for better UI
- Music player functionality
- Server statistics dashboard
