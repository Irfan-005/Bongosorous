# main.py
"""
bongosorous - Ultra-ready Discord bot (main.py)
Paste this file into your project root. Add required env vars and requirements.txt.
"""

import os
import sys
import time
import math
import random
import logging
import threading
import sqlite3
import asyncio
from pathlib import Path
from typing import Optional, Tuple, Dict

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, jsonify

# Optional Hugging Face client (will work only if huggingface_hub is installed and key provided)
try:
    from huggingface_hub import InferenceClient
except Exception:
    InferenceClient = None

# -------------------------
# Config
# -------------------------
BOT_NAME = "bongosorous"
DB_FILE = os.environ.get("BOT_DB_PATH", "bongobot.db")
HF_MODEL = "meta-llama/Llama-3.2-3B-Instruct"
MAX_RESPONSE_LENGTH = 1900
REMINDER_INTERVAL = 5  # seconds
SLASH_SYNC_RETRIES = 3
SLASH_SYNC_WAIT = 2

# emojis for polls
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(BOT_NAME)

# -------------------------
# Environment
# -------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
HF_KEY = os.environ.get("HUGGINGFACE_API_KEY")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0") or 0)
PORT = int(os.environ.get("PORT", 5000))

if not DISCORD_TOKEN:
    logger.critical("DISCORD_BOT_TOKEN missing. Exiting.")
    sys.exit(1)

# -------------------------
# Hugging Face client (optional)
# -------------------------
hf_client = None
if HF_KEY and InferenceClient:
    try:
        hf_client = InferenceClient(token=HF_KEY)
        logger.info("Hugging Face client initialized.")
    except Exception:
        logger.exception("Failed to initialize HF client, AI disabled.")
        hf_client = None
else:
    if HF_KEY and not InferenceClient:
        logger.warning("HUGGINGFACE_API_KEY set but huggingface_hub not installed.")
    else:
        logger.info("Hugging Face not configured — /ask will be disabled.")

def hf_sync(prompt: str) -> Tuple[Optional[str], Optional[str]]:
    """Synchronous HF call (safe wrapper)"""
    if not hf_client:
        return None, "HF not configured"
    try:
        messages = [
            {"role": "system", "content": "You are a friendly, concise assistant."},
            {"role": "user", "content": prompt}
        ]
        resp = hf_client.chat_completion(messages=messages, model=HF_MODEL, max_tokens=400, temperature=0.8)
        # robust extraction
        try:
            text = resp.choices[0].message.content
        except Exception:
            try:
                text = resp.choices[0].text
            except Exception:
                text = str(resp)
        return text, None
    except Exception as e:
        logger.exception("HF request failed")
        return None, str(e)

async def hf_query(prompt: str, timeout: int = 20) -> Tuple[Optional[str], Optional[str]]:
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, lambda: hf_sync(prompt))
    try:
        return await asyncio.wait_for(fut, timeout=timeout)
    except asyncio.TimeoutError:
        return None, "HF timeout"
    except Exception as e:
        logger.exception("Error during HF query")
        return None, str(e)

# -------------------------
# SQLite DB init
# -------------------------
Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        coins INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        last_daily INTEGER DEFAULT 0
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        guild_id INTEGER,
        channel_id INTEGER,
        remind_at INTEGER,
        content TEXT
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS reaction_roles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        message_id INTEGER,
        emoji TEXT,
        role_id INTEGER
    );
    """)
    conn.commit()
    conn.close()

init_db()

# -------------------------
# Bot setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# remove default help (prevent duplicate)
try:
    bot.remove_command("help")
except Exception:
    pass

# -------------------------
# Utility DB helpers
# -------------------------
def ensure_user(uid: int):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))
    conn.commit(); conn.close()

def add_xp(uid: int, amount: int = 1) -> Optional[int]:
    ensure_user(uid)
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET xp = xp + ? WHERE user_id = ?", (amount, uid))
    conn.commit()
    c.execute("SELECT xp, level FROM users WHERE user_id = ?", (uid,))
    row = c.fetchone()
    xp, lvl = row["xp"], row["level"]
    new_level = int(math.sqrt(xp))
    if new_level > lvl:
        c.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_level, uid))
        conn.commit(); conn.close()
        return new_level
    conn.close()
    return None

def change_coins(uid: int, delta: int) -> int:
    ensure_user(uid)
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (delta, uid))
    conn.commit()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (uid,))
    coins = c.fetchone()["coins"]
    conn.close()
    return coins

# -------------------------
# Reminder background worker
# -------------------------
async def reminder_worker():
    await bot.wait_until_ready()
    logger.info("Reminder worker started.")
    while not bot.is_closed():
        try:
            now = int(time.time())
            conn = get_conn(); c = conn.cursor()
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
            logger.exception("Reminder worker top-level error")
        await asyncio.sleep(REMINDER_INTERVAL)

# schedule reminder worker after ready
@bot.event
async def on_ready():
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    # set status
    try:
        await bot.change_presence(activity=discord.Game(f"{BOT_NAME} — /help"))
    except Exception:
        pass
    # start reminder worker if not running
    if not any(t.get_name() == "reminder_worker" for t in asyncio.all_tasks(loop=asyncio.get_running_loop())):
        bot.loop.create_task(reminder_worker(), name="reminder_worker")
    # sync slash commands with retries
    for attempt in range(SLASH_SYNC_RETRIES):
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %d slash commands", len(synced))
            break
        except Exception:
            logger.exception("Slash sync attempt failed")
            await asyncio.sleep(SLASH_SYNC_WAIT)
    else:
        logger.error("Failed to sync slash commands after retries.")

# XP gain on messages and process commands
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # XP
    try:
        lvl = add_xp(message.author.id, amount=random.randint(1,3))
        if lvl:
            await message.channel.send(f"🎉 {message.author.mention} reached level **{lvl}**!")
    except Exception:
        logger.exception("XP error")
    await bot.process_commands(message)

# -------------------------
# Commands
# -------------------------

HELP_TEXT = f"""
**{BOT_NAME} — Help**
/ask <question> — Ask the AI (requires HF key)
/trivia — Play trivia
/rps <choice> — Rock Paper Scissors
/poll <question> <opt1,opt2,...> — Poll
!remindme 10m message — Reminder (prefix)
/daily, !balance, !give @user amount — Economy
!kick, !ban, !warn, !purge — Moderation (requires perms)
"""

# slash help
@bot.tree.command(name="help", description="Show help")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT)

# prefix help
@bot.command(name="help")
async def help_prefix(ctx):
    await ctx.send(HELP_TEXT)

# ---------- /ask and !ask ----------
@bot.tree.command(name="ask", description="Ask the AI (Hugging Face)")
@app_commands.describe(question="Your question")
async def ask_slash(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)
    text, err = await hf_query(question)
    if text:
        if len(text) > MAX_RESPONSE_LENGTH:
            text = text[:MAX_RESPONSE_LENGTH] + "..."
        await interaction.followup.send(f"✨ {text}")
    else:
        await interaction.followup.send(f"❌ AI error: {err}")

@bot.command(name="ask")
async def ask_prefix(ctx, *, question: str):
    thinking = await ctx.send("🤖 Thinking...")
    text, err = await hf_query(question)
    if text:
        if len(text) > MAX_RESPONSE_LENGTH:
            text = text[:MAX_RESPONSE_LENGTH] + "..."
        await thinking.edit(content=f"✨ {text}")
    else:
        await thinking.edit(content=f"❌ AI error: {err}")

# ---------- Trivia ----------
TRIVIA = [
    ("What is the capital of France?", "paris"),
    ("What planet is known as the Red Planet?", "mars"),
    ("Who wrote 'Hamlet'?", "shakespeare"),
    ("What is 12 * 12?", "144"),
    ("What is water's chemical formula?", "h2o"),
    ("What year did Titanic sink?", "1912"),
]

active_trivia: Dict[int,str] = {}

@bot.tree.command(name="trivia", description="Start a trivia question")
async def trivia_slash(interaction: discord.Interaction):
    q,a = random.choice(TRIVIA)
    active_trivia[interaction.channel_id] = a.lower()
    await interaction.response.send_message(f"🧠 Trivia: {q} (answer in chat)")

@bot.command(name="trivia")
async def trivia_prefix(ctx):
    q,a = random.choice(TRIVIA)
    active_trivia[ctx.channel.id] = a.lower()
    await ctx.send(f"🧠 Trivia: {q} (answer in chat)")

# ---------- RPS ----------
@bot.tree.command(name="rps", description="Play rock-paper-scissors")
@app_commands.describe(choice="rock/paper/scissors")
async def rps_slash(interaction: discord.Interaction, choice: str):
    opts = ["rock","paper","scissors"]
    choice = choice.lower()
    if choice not in opts:
        await interaction.response.send_message("Choose rock / paper / scissors")
        return
    bot_choice = random.choice(opts)
    if choice == bot_choice:
        res = "Tie!"
    elif (choice=="rock" and bot_choice=="scissors") or (choice=="paper" and bot_choice=="rock") or (choice=="scissors" and bot_choice=="paper"):
        res = "You win!"
    else:
        res = "I win!"
    await interaction.response.send_message(f"You: {choice} | Bot: {bot_choice} — {res}")

@bot.command(name="rps")
async def rps_prefix(ctx, choice: str):
    # reuse same logic
    await rps_slash.callback(interaction=ctx, choice=choice)

# ---------- Poll ----------
@bot.tree.command(name="poll", description="Create a poll (2-5 options)")
@app_commands.describe(question="Question", options="Comma separated options", duration="seconds")
async def poll_slash(interaction: discord.Interaction, question: str, options: str, duration: int = 30):
    opts = [o.strip() for o in options.split(",") if o.strip()]
    if not 2 <= len(opts) <= 5:
        await interaction.response.send_message("Provide 2-5 options.")
        return
    desc = "\n".join(f"{NUMBER_EMOJIS[i]} {opts[i]}" for i in range(len(opts)))
    embed = discord.Embed(title=question, description=desc)
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    for i in range(len(opts)):
        try:
            await msg.add_reaction(NUMBER_EMOJIS[i])
            await asyncio.sleep(0.2)
        except Exception:
            pass
    await asyncio.sleep(max(5, min(duration, 600)))
    try:
        fetched = await msg.channel.fetch_message(msg.id)
    except Exception:
        fetched = msg
    results = []
    for i in range(len(opts)):
        r = discord.utils.get(fetched.reactions, emoji=NUMBER_EMOJIS[i])
        count = (r.count - 1) if r else 0
        results.append((opts[i], count))
    await msg.channel.send("🗳️ Poll results:\n" + "\n".join(f"**{o}** — {c} vote(s)" for o,c in results))

# ---------- Remindme (prefix) ----------
@bot.command(name="remindme")
async def remindme_cmd(ctx, when: str, *, text: str):
    try:
        unit = when[-1]
        num = int(when[:-1])
        mult = {"s":1,"m":60,"h":3600,"d":86400}.get(unit)
        if not mult:
            raise ValueError()
    except Exception:
        await ctx.send("Time format: 10m, 2h, 1d etc.")
        return
    remind_at = int(time.time()) + num * mult
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO reminders (user_id, guild_id, channel_id, remind_at, content) VALUES (?, ?, ?, ?, ?)",
              (ctx.author.id, ctx.guild.id if ctx.guild else None, ctx.channel.id, remind_at, text))
    conn.commit(); conn.close()
    await ctx.send(f"✅ Reminder set for <t:{remind_at}:R>")

# ---------- Economy (balance, daily, give) ----------
@bot.command(name="balance")
async def balance_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author
    ensure_user(member.id)
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (member.id,))
    row = c.fetchone(); conn.close()
    coins = row["coins"] if row else 0
    await ctx.send(f"{member.mention} has **{coins}** coins")

@bot.command(name="daily")
async def daily_cmd(ctx):
    ensure_user(ctx.author.id)
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT last_daily FROM users WHERE user_id = ?", (ctx.author.id,))
    row = c.fetchone(); last = row["last_daily"] or 0
    now = int(time.time())
    if now - last < 86400:
        await ctx.send("You already claimed daily.")
    else:
        reward = random.randint(50,150)
        c.execute("UPDATE users SET coins = coins + ?, last_daily = ? WHERE user_id = ?", (reward, now, ctx.author.id))
        conn.commit(); conn.close()
        await ctx.send(f"🎉 You claimed **{reward}** coins!")

@bot.command(name="give")
async def give_cmd(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        await ctx.send("Amount must be > 0.")
        return
    ensure_user(ctx.author.id); ensure_user(member.id)
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id = ?", (ctx.author.id,))
    if c.fetchone()["coins"] < amount:
        await ctx.send("Not enough coins.")
        conn.close(); return
    c.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (amount, ctx.author.id))
    c.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, member.id))
    conn.commit(); conn.close()
    await ctx.send(f"{ctx.author.mention} gave {member.mention} **{amount}** coins!")

# ---------- Moderation (basic) ----------
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick_cmd(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.kick(reason=reason)
        await ctx.send(f"👢 {member.mention} was kicked.")
    except Exception as e:
        await ctx.send(f"Kick failed: {e}")

@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban_cmd(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    try:
        await member.ban(reason=reason)
        await ctx.send(f"🔨 {member.mention} was banned.")
    except Exception as e:
        await ctx.send(f"Ban failed: {e}")

@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def purge_cmd(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Amount must be 1-100.")
        return
    deleted = await ctx.channel.purge(limit=amount)
    await ctx.send(f"Deleted {len(deleted)} messages.", delete_after=5)

# Reaction-role creation (admin)
@bot.command(name="createreactionrole")
@commands.has_permissions(manage_roles=True)
async def create_reaction_role(ctx, message_id: int, emoji: str, role: discord.Role):
    conn = get_conn(); c = conn.cursor()
    c.execute("INSERT INTO reaction_roles (guild_id, message_id, emoji, role_id) VALUES (?, ?, ?, ?)",
              (ctx.guild.id, message_id, emoji, role.id))
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
    conn = get_conn(); c = conn.cursor()
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
                logger.exception("Failed to add role")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    conn = get_conn(); c = conn.cursor()
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
                logger.exception("Failed to remove role")

# -------------------------
# Flask heartbeat (so uptimerobot can ping /health)
# -------------------------
app = Flask(__name__)

@app.route("/")
def root():
    return jsonify({"status": "online", "bot": BOT_NAME})

@app.route("/health")
def health():
    return jsonify({"ok": True, "bot": BOT_NAME})

def run_flask():
    logger.info("Flask starting on port %s", PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)

# -------------------------
# Start bot + heartbeat
# -------------------------
def start():
    thr = threading.Thread(target=run_flask, daemon=True)
    thr.start()
    logger.info("Heartbeat started.")
    try:
        bot.run(DISCORD_TOKEN)
    except Exception:
        logger.exception("Bot stopped")
        sys.exit(1)

if __name__ == "__main__":
    start()
