# main.py
"""
bongosorous - All-in-one Discord bot (AI-enabled)
Features:
- Hugging Face chat (/ask) using HUGGINGFACE_API_KEY (optional, recommended)
- SQLite persistence for users, infractions, reminders, reaction-roles, guild config
- XP/leveling, economy (balance/daily/give)
- Trivia, RPS, Poll (safe number emojis)
- Reaction roles, welcome messages
- Auto-react & Auto-reply (config via env)
- Reminders worker (started in setup_hook)
- Flask heartbeat for /health (binds to PORT)
- Robust logging and safe startup for Render
"""

import os
import sys
import time
import random
import asyncio
import threading
import logging
import sqlite3
import signal
from pathlib import Path
from typing import Optional, Tuple

from flask import Flask, jsonify
import discord
from discord import app_commands
from discord.ext import commands

# Optional HF import
try:
    from huggingface_hub import InferenceClient
except Exception:
    InferenceClient = None

# --------------------
# Configuration
# --------------------
BOT_NAME = "bongosorous"
DEFAULT_FLASK_PORT = 5000
DB_PATH = os.environ.get("BOT_DB_PATH", "chatterous.db")
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0") or 0)
MAX_RESPONSE_LENGTH = 1900
HF_TIMEOUT_SECONDS = 25

# --------------------
# Logging
# --------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(BOT_NAME)

# --------------------
# Environment
# --------------------
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
HF_KEY = os.environ.get("HUGGINGFACE_API_KEY")
if not DISCORD_TOKEN:
    logger.critical("DISCORD_BOT_TOKEN is missing in environment. Exiting.")
    sys.exit(1)
if not HF_KEY:
    logger.info("HUGGINGFACE_API_KEY not provided — AI features will be disabled until set.")

# --------------------
# DB helper (sqlite)
# --------------------
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

def get_db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id INTEGER PRIMARY KEY,
        welcome_channel INTEGER,
        welcome_message TEXT,
        modlog_channel INTEGER
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        coins INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        last_daily INTEGER DEFAULT 0
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS infractions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        user_id INTEGER,
        mod_id INTEGER,
        action TEXT,
        reason TEXT,
        created_at INTEGER
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        guild_id INTEGER,
        channel_id INTEGER,
        remind_at INTEGER,
        content TEXT,
        created_at INTEGER
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS reaction_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        channel_id INTEGER,
        message_id INTEGER,
        emoji TEXT,
        role_id INTEGER
    )""")
    conn.commit()
    conn.close()

init_db()

# --------------------
# Hugging Face client (optional)
# --------------------
hf_client = None
HF_MODEL = "meta-llama/Llama-3.2-3B-Instruct"
if HF_KEY and InferenceClient:
    try:
        hf_client = InferenceClient(token=HF_KEY)
        logger.info("Hugging Face client initialized.")
    except Exception as e:
        logger.exception("Failed to init Hugging Face client: %s", e)
        hf_client = None
else:
    if HF_KEY and not InferenceClient:
        logger.warning("huggingface_hub not installed; install it to enable HF AI.")
    else:
        logger.info("No Hugging Face key; AI disabled.")

# --------------------
# HF query helpers
# --------------------
def query_huggingface_sync(prompt: str) -> Tuple[Optional[str], Optional[str]]:
    if not hf_client:
        return None, "HF not configured"
    try:
        messages = [
            {"role":"system", "content": "You are a friendly helpful assistant. Keep replies concise and natural."},
            {"role":"user", "content": prompt}
        ]
        resp = hf_client.chat_completion(messages=messages, model=HF_MODEL, max_tokens=400, temperature=0.8)
        text = None
        if hasattr(resp, "choices") and resp.choices:
            try:
                msg = resp.choices[0].message
                if isinstance(msg, dict):
                    text = msg.get("content")
                else:
                    text = getattr(msg, "content", None)
            except Exception:
                text = None
        if not text:
            text = getattr(resp, "generated_text", None) or str(resp)
        return text, None
    except Exception as e:
        logger.exception("HF call failed")
        return None, str(e)

async def query_huggingface(prompt: str, timeout: int = HF_TIMEOUT_SECONDS) -> Tuple[Optional[str], Optional[str]]:
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(None, lambda: query_huggingface_sync(prompt))
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return None, "HF timeout"
    except Exception as e:
        logger.exception("HF executor error")
        return None, str(e)

# --------------------
# Utilities (DB-backed)
# --------------------
def ensure_user(user_id: int):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id, coins, xp, level, last_daily) VALUES (?, ?, ?, ?, ?)", (user_id, 0, 0, 0, 0))
    conn.commit()
    conn.close()

def add_xp(user_id: int, amount: int = 1) -> Optional[int]:
    ensure_user(user_id)
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    c.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    xp, lvl = row["xp"], row["level"]
    new_level = int((xp ** 0.5))
    if new_level > lvl:
        c.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, user_id))
        conn.commit()
        conn.close()
        return new_level
    conn.close()
    return None

def get_user_row(user_id: int):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def change_coins(user_id: int, delta: int):
    ensure_user(user_id)
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    coins = c.fetchone()["coins"]
    conn.close()
    return coins

def log_infraction(guild_id: int, user_id: int, mod_id: int, action: str, reason: str = ""):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("INSERT INTO infractions (guild_id, user_id, mod_id, action, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (guild_id, user_id, mod_id, action, reason, int(time.time())))
    conn.commit()
    conn.close()

def schedule_reminder(user_id:int, guild_id:Optional[int], channel_id:int, remind_at:int, content:str):
    conn = get_db_conn()
    c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, guild_id, channel_id, remind_at, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (user_id, guild_id, channel_id, remind_at, content, int(time.time())))
    conn.commit()
    conn.close()

# --------------------
# Reminders worker
# --------------------
async def reminders_worker():
    await bot.wait_until_ready()
    logger.info("Reminders worker started.")
    while not bot.is_closed():
        try:
            now = int(time.time())
            conn = get_db_conn()
            c = conn.cursor()
            c.execute("SELECT id, user_id, channel_id, content FROM reminders WHERE remind_at <= ?", (now,))
            rows = c.fetchall()
            for r in rows:
                try:
                    ch = bot.get_channel(r["channel_id"])
                    if ch:
                        await ch.send(f"<@{r['user_id']}> ⏰ Reminder: {r['content']}")
                    c.execute("DELETE FROM reminders WHERE id = ?", (r["id"],))
                    conn.commit()
                except Exception:
                    logger.exception("Failed to send reminder")
            conn.close()
        except Exception:
            logger.exception("Reminder worker error")
        await asyncio.sleep(10)

# --------------------
# Bot class and instantiation
# --------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bongobot(commands.Bot):
    async def setup_hook(self):
        # start background tasks safely
        asyncio.create_task(reminders_worker())
        logger.info("Reminder worker scheduled in setup_hook()")

bot = Bongobot(command_prefix="!", intents=intents)

# --------------------
# Safe number emojis
# --------------------
NUMBER_EMOJIS = ["\u0031\u20E3","\u0032\u20E3","\u0033\u20E3","\u0034\u20E3","\u0035\u20E3"]

# --------------------
# Auto-react / auto-reply env config
# --------------------
AUTO_REACT_CHANNELS = os.environ.get("AUTO_REACT_CHANNELS", "")
AUTO_REACT_CHANNEL_IDS = [int(x) for x in AUTO_REACT_CHANNELS.split(",") if x.strip().isdigit()]
AUTO_REACT_EMOJIS = [e.strip() for e in os.environ.get("AUTO_REACT_EMOJIS", "👍,🤖,🔥").split(",") if e.strip()]
AUTO_REACT_KEYWORDS = [k.strip().lower() for k in os.environ.get("AUTO_REACT_KEYWORDS", "").split(",") if k.strip()]
AUTO_REACT_COOLDOWN = int(os.environ.get("AUTO_REACT_COOLDOWN", "10"))

AUTO_REPLY_CHANNELS = os.environ.get("AUTO_REPLY_CHANNELS", "")
AUTO_REPLY_CHANNEL_IDS = [int(x) for x in AUTO_REPLY_CHANNELS.split(",") if x.strip().isdigit()]
AUTO_REPLY_KEYWORDS = [k.strip().lower() for k in os.environ.get("AUTO_REPLY_KEYWORDS", "").split(",") if k.strip()]
AUTO_REPLY_CHANCE = int(os.environ.get("AUTO_REPLY_CHANCE", "15"))
AUTO_REPLY_COOLDOWN = int(os.environ.get("AUTO_REPLY_COOLDOWN", "30"))

FUN_REPLIES = [
    "Lol true! 😂",
    "That’s epic! 🔥",
    "I feel that. 🤝",
    "Wow, tell me more! 👀",
    "Haha, I can't stop laughing 🤣",
    "I'm just a bot, but that made my circuits happy. 🤖💖",
    "Emoji party! 🎉",
]

_last_react_time = {}
_last_reply_time = {}

async def try_add_reactions(message: discord.Message):
    for emoji in AUTO_REACT_EMOJIS:
        try:
            await message.add_reaction(emoji)
            await asyncio.sleep(0.25)
        except discord.Forbidden:
            logger.warning("Missing permission to add reactions in channel %s", message.channel.id)
            return
        except Exception:
            logger.exception("Failed to add reaction")

async def try_send_auto_reply(message: discord.Message):
    reply_text = random.choice(FUN_REPLIES)
    try:
        await message.channel.send(f"{message.author.mention} {reply_text}")
    except Exception:
        logger.exception("Failed to send auto-reply")

# --------------------
# Events & Commands
# --------------------
@bot.event
async def on_ready():
    logger.info("Logged in as %s (id:%s)", bot.user, bot.user.id)
    # set presence
    try:
        activity = discord.Game(f"{BOT_NAME} — /help")
        await bot.change_presence(status=discord.Status.online, activity=activity)
    except Exception:
        pass
    # sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info("Synced %d slash commands", len(synced))
    except Exception:
        logger.exception("Slash command sync failed")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # XP & level
    try:
        lvl_up = add_xp(message.author.id, amount=random.randint(1,3))
        if lvl_up:
            try:
                await message.channel.send(f"🎉 {message.author.mention} leveled up to **{lvl_up}**!")
            except Exception:
                pass
    except Exception:
        logger.exception("XP error")

    # trivia answer handling
    try:
        data = active_trivia.get(message.channel.id)
        if data:
            answer, _ = data
            if message.content.strip().lower() == answer:
                uid = message.author.id
                trivia_scores[uid] = trivia_scores.get(uid, 0) + 1
                await message.channel.send(f"✅ {message.author.mention} — Correct! +1 point. Total: {trivia_scores[uid]}")
                del active_trivia[message.channel.id]
                await bot.process_commands(message)
                return
    except Exception:
        logger.exception("Trivia handling error")

    # auto-react & auto-reply
    now = time.time()
    try:
        if AUTO_REACT_CHANNEL_IDS and message.channel.id in AUTO_REACT_CHANNEL_IDS:
            if AUTO_REACT_KEYWORDS:
                if any(kw in message.content.lower() for kw in AUTO_REACT_KEYWORDS):
                    key = (message.author.id, message.channel.id)
                    if now - _last_react_time.get(key, 0) >= AUTO_REACT_COOLDOWN:
                        _last_react_time[key] = now
                        await try_add_reactions(message)
            else:
                key = (message.author.id, message.channel.id)
                if now - _last_react_time.get(key, 0) >= AUTO_REACT_COOLDOWN:
                    _last_react_time[key] = now
                    await try_add_reactions(message)
    except Exception:
        logger.exception("Auto-react error")
    try:
        if AUTO_REPLY_CHANNEL_IDS and message.channel.id in AUTO_REPLY_CHANNEL_IDS:
            if not AUTO_REPLY_KEYWORDS or any(kw in message.content.lower() for kw in AUTO_REPLY_KEYWORDS):
                key = (message.author.id, message.channel.id)
                if now - _last_reply_time.get(key, 0) >= AUTO_REPLY_COOLDOWN:
                    if random.randint(1,100) <= AUTO_REPLY_CHANCE:
                        _last_reply_time[key] = now
                        await try_send_auto_reply(message)
    except Exception:
        logger.exception("Auto-reply error")

    await bot.process_commands(message)

# --------------------
# Ask (HF) commands
# --------------------
@bot.tree.command(name="ask", description="Chat with the AI assistant")
@app_commands.describe(question="What would you like to ask?")
async def ask_slash(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    text, error = await query_huggingface(question)
    if text:
        out = text.strip()
        if len(out) > MAX_RESPONSE_LENGTH:
            out = out[:MAX_RESPONSE_LENGTH] + "..."
        await interaction.followup.send(f"✨ {out}")
    else:
        logger.info("AI failed: %s", error)
        await interaction.followup.send("❌ AI unavailable or error: " + str(error))

@bot.command(name="ask")
async def ask_command(ctx, *, question: str = None):
    if not question:
        await ctx.send("💭 What's on your mind? Try `/ask your question here`")
        return
    thinking = await ctx.send("🤖 Thinking...")
    text, error = await query_huggingface(question)
    if text:
        out = text.strip()
        if len(out) > MAX_RESPONSE_LENGTH:
            out = out[:MAX_RESPONSE_LENGTH] + "..."
        await thinking.edit(content=f"✨ {out}")
    else:
        await thinking.edit(content="❌ AI unavailable or error: " + str(error))

# --------------------
# Trivia / RPS / Poll
# --------------------
TRIVIA_QUESTIONS = [
    {"q":"What is the capital of France?","a":"paris"},
    {"q":"Which planet is known as the Red Planet?","a":"mars"},
    {"q":"Who wrote Hamlet?","a":"william shakespeare"},
    {"q":"What is 9 * 9?","a":"81"}
]
trivia_scores = {}
active_trivia = {}

@bot.tree.command(name="trivia", description="Start a trivia question")
async def trivia_slash(interaction: discord.Interaction):
    q = random.choice(TRIVIA_QUESTIONS)
    active_trivia[interaction.channel_id] = (q["a"], interaction.user.id)
    await interaction.response.send_message(f"🧠 Trivia: {q['q']} (reply in chat)")

@bot.command(name="trivia")
async def trivia_cmd(ctx):
    q = random.choice(TRIVIA_QUESTIONS)
    active_trivia[ctx.channel.id] = (q["a"], ctx.author.id)
    await ctx.send(f"🧠 Trivia: {q['q']} (reply in chat)")

@bot.tree.command(name="rps", description="Play rock-paper-scissors")
@app_commands.describe(choice="rock/paper/scissors")
async def rps_slash(interaction: discord.Interaction, choice: str):
    choice = choice.lower()
    opts = ["rock","paper","scissors"]
    if choice not in opts:
        await interaction.response.send_message("Invalid choice: rock/paper/scissors")
        return
    bot_choice = random.choice(opts)
    if choice == bot_choice:
        res = "Tie!"
    elif (choice=="rock" and bot_choice=="scissors") or (choice=="paper" and bot_choice=="rock") or (choice=="scissors" and bot_choice=="paper"):
        res = "You win! 🎉"
    else:
        res = "I win! 😈"
    await interaction.response.send_message(f"You: {choice} | Bot: {bot_choice} — {res}")

@bot.command(name="rps")
async def rps_cmd(ctx, choice: str):
    await rps_slash.callback(interaction=ctx, choice=choice)

@bot.tree.command(name="poll", description="Create a poll (up to 5 options)")
@app_commands.describe(question="Question", opts="Comma-separated options", duration="seconds")
async def poll_slash(interaction: discord.Interaction, question: str, opts: str, duration: int = 30):
    options = [o.strip() for o in opts.split(",") if o.strip()]
    if len(options) < 2 or len(options) > 5:
        await interaction.response.send_message("Provide 2-5 comma-separated options.")
        return
    embed = discord.Embed(title=f"📊 {question}", description="\n".join(f"{NUMBER_EMOJIS[i]} {options[i]}" for i in range(len(options))))
    await interaction.response.send_message(embed=embed)
    try:
        sent = await interaction.original_response()
    except Exception:
        sent = await interaction.followup.send(embed=embed)
    if not isinstance(sent, discord.Message):
        try:
            sent = await interaction.channel.fetch_message((await interaction.original_response()).id)
        except Exception:
            pass
    for i in range(len(options)):
        try:
            await sent.add_reaction(NUMBER_EMOJIS[i])
            await asyncio.sleep(0.2)
        except Exception:
            logger.exception("Failed to add poll reaction")
    await asyncio.sleep(duration)
    try:
        sent = await sent.channel.fetch_message(sent.id)
    except Exception:
        logger.exception("Failed to fetch poll message")
        return
    counts = []
    for i in range(len(options)):
        react = discord.utils.get(sent.reactions, emoji=NUMBER_EMOJIS[i])
        counts.append((options[i], (react.count - 1) if react else 0))
    await sent.channel.send("🗳️ Poll results:\n" + "\n".join(f"**{o}** — {c} vote(s)" for o,c in counts))

# --------------------
# Moderation
# --------------------
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def cmd_kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.kick(reason=reason)
        log_infraction(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
        await ctx.send(f"👢 Kicked {member.mention} — {reason}")
    except Exception as e:
        await ctx.send(f"Kick failed: {e}")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def cmd_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.ban(reason=reason)
        log_infraction(ctx.guild.id, member.id, ctx.author.id, "ban", reason)
        await ctx.send(f"🔨 Banned {member.mention} — {reason}")
    except Exception as e:
        await ctx.send(f"Ban failed: {e}")

@bot.command(name="warn")
@commands.has_permissions(manage_messages=True)
async def cmd_warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    log_infraction(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    await ctx.send(f"⚠️ Warned {member.mention} — {reason}")

# --------------------
# Welcome & config
# --------------------
@bot.command(name="setwelcome")
@commands.has_permissions(manage_guild=True)
async def cmd_setwelcome(ctx, channel: discord.TextChannel, *, message: str = "Welcome {user} to {guild}!"):
    conn = get_db_conn(); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO guild_config (guild_id, welcome_channel, welcome_message) VALUES (?, ?, ?)",
              (ctx.guild.id, channel.id, message))
    conn.commit(); conn.close()
    await ctx.send(f"Welcome set to {channel.mention}")

@bot.event
async def on_member_join(member: discord.Member):
    conn = get_db_conn(); c = conn.cursor()
    c.execute("SELECT welcome_channel, welcome_message FROM guild_config WHERE guild_id = ?", (member.guild.id,))
    row = c.fetchone(); conn.close()
    if not row:
        return
    ch_id = row["welcome_channel"]; msg = row["welcome_message"]
    channel = member.guild.get_channel(ch_id)
    if channel:
        try:
            await channel.send(msg.format(user=member.mention, guild=member.guild.name))
        except Exception:
            pass

# --------------------
# Reaction roles
# --------------------
@bot.command(name="createreactionrole")
@commands.has_permissions(manage_roles=True)
async def cmd_createreactionrole(ctx, message_id: int, emoji: str, role: discord.Role):
    conn = get_db_conn(); c = conn.cursor()
    c.execute("INSERT INTO reaction_roles (guild_id, channel_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?, ?)",
              (ctx.guild.id, ctx.channel.id, message_id, emoji, role.id))
    conn.commit(); conn.close()
    try:
        msg = await ctx.channel.fetch_message(message_id)
        await msg.add_reaction(emoji)
    except Exception:
        pass
    await ctx.send("Reaction role registered.")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    conn = get_db_conn(); c = conn.cursor()
    c.execute("SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
              (payload.guild_id, payload.message_id, str(payload.emoji)))
    row = c.fetchone(); conn.close()
    if row:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(row["role_id"])
        member = guild.get_member(payload.user_id)
        if member and role:
            try:
                await member.add_roles(role)
            except Exception:
                pass

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    conn = get_db_conn(); c = conn.cursor()
    c.execute("SELECT role_id FROM reaction_roles WHERE guild_id = ? AND message_id = ? AND emoji = ?",
              (payload.guild_id, payload.message_id, str(payload.emoji)))
    row = c.fetchone(); conn.close()
    if row:
        guild = bot.get_guild(payload.guild_id)
        role = guild.get_role(row["role_id"])
        member = guild.get_member(payload.user_id)
        if member and role:
            try:
                await member.remove_roles(role)
            except Exception:
                pass

# --------------------
# Economy
# --------------------
@bot.command(name="balance")
async def cmd_balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    row = get_user_row(member.id)
    if not row:
        await ctx.send(f"{member.mention} has 0 coins.")
        return
    await ctx.send(f"{member.mention} has **{row['coins']}** coins.")

@bot.command(name="give")
async def cmd_give(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        await ctx.send("Enter an amount > 0")
        return
    row = get_user_row(ctx.author.id)
    if row["coins"] < amount:
        await ctx.send("Not enough coins.")
        return
    change_coins(ctx.author.id, -amount)
    change_coins(member.id, amount)
    await ctx.send(f"{ctx.author.mention} gave {member.mention} **{amount}** coins.")

@bot.command(name="daily")
async def cmd_daily(ctx):
    ensure_user(ctx.author.id)
    row = get_user_row(ctx.author.id)
    now = int(time.time())
    last = row["last_daily"] or 0
    if now - last < 24*3600:
        await ctx.send("Daily already claimed. Try later.")
        return
    reward = random.randint(50,150)
    change_coins(ctx.author.id, reward)
    conn = get_db_conn(); c = conn.cursor()
    c.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (now, ctx.author.id))
    conn.commit(); conn.close()
    await ctx.send(f"{ctx.author.mention} claimed daily **{reward}** coins!")

# --------------------
# Remind me command
# --------------------
@bot.command(name="remindme")
async def cmd_remindme(ctx, when: str, *, content: str):
    try:
        unit = when[-1]
        num = int(when[:-1])
        mult = {"s":1, "m":60, "h":3600, "d":86400}.get(unit)
        if not mult:
            raise ValueError()
        seconds = num * mult
    except Exception:
        await ctx.send("Invalid time format. Use 10m, 2h, 1d, etc.")
        return
    remind_at = int(time.time()) + seconds
    schedule_reminder(ctx.author.id, ctx.guild.id if ctx.guild else None, ctx.channel.id, remind_at, content)
    await ctx.send(f"Reminder set for <t:{remind_at}:R>")

# --------------------
# Image stub (HF)
# --------------------
@bot.command(name="img")
async def cmd_img(ctx, *, prompt: str):
    if not hf_client:
        await ctx.send("Image generation requires HUGGINGFACE_API_KEY and model access.")
        return
    await ctx.send("Image generation requested — integrate per model docs for your chosen endpoint.")

# --------------------
# Owner admin
# --------------------
@bot.command(name="shutdown")
@commands.is_owner()
async def cmd_shutdown(ctx):
    await ctx.send("Shutting down...")
    await bot.close()

@bot.command(name="restart")
@commands.is_owner()
async def cmd_restart(ctx):
    await ctx.send("Restarting...")
    await bot.close()

# --------------------
# Help text
# --------------------
HELP_TEXT = (
    f"**{BOT_NAME} — Help**\n\n"
    "`/ask` or `!ask` — Ask the AI (requires HUGGINGFACE_API_KEY)\n"
    "`/trivia` or `!trivia` — Trivia\n"
    "`/rps` or `!rps <choice>` — Rock Paper Scissors\n"
    "`/poll` — Create a poll (slash)\n"
    "`!balance`, `!give`, `!daily` — Economy\n"
    "`!remindme <time> <text>` — Reminder\n"
    "`!kick`, `!ban`, `!warn` — Moderation\n"
    "`!createreactionrole <message_id> <emoji> <@role>` — Reaction roles\n"
    "`!setwelcome #channel <message>` — Welcome messages\n"
    "Owner: `!shutdown`, `!restart`\n"
)
@bot.tree.command(name="help", description="Show help")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT)

@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.send(HELP_TEXT)

# --------------------
# Flask heartbeat
# --------------------
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"status":"online","bot":BOT_NAME})

@app.route("/health")
def health():
    return jsonify({"status":"healthy","uptime":"running"})

def run_flask():
    port = int(os.environ.get("PORT", DEFAULT_FLASK_PORT))
    logger.info("Starting Flask on 0.0.0.0:%s", port)
    app.run(host="0.0.0.0", port=port, threaded=True)

# --------------------
# Run bot
# --------------------
if __name__ == "__main__":
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()
    logger.info("Flask heartbeat started")
    try:
        logger.info("Starting Discord bot...")
        bot.run(DISCORD_TOKEN)
    except Exception:
        logger.exception("Bot failed to start")
        sys.exit(1)
