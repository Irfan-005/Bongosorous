# ==========================
# bongosorous - Discord Bot
# COMPLETE + AI ENABLED
# ==========================

import os
import sys
import time
import random
import asyncio
import threading
import logging
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from flask import Flask, jsonify

# Optional HuggingFace
try:
    from huggingface_hub import InferenceClient
except:
    InferenceClient = None


# -------------------------
# CONFIG
# -------------------------
BOT_NAME = "bongosorous"
DEFAULT_FLASK_PORT = 5000
DB_PATH = "bongo.db"
HF_MODEL = "meta-llama/Llama-3.2-3B-Instruct"
MAX_RESPONSE_LENGTH = 1900


# -------------------------
# LOGGING
# -------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(BOT_NAME)


# -------------------------
# ENVIRONMENT
# -------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
HF_KEY = os.environ.get("HUGGINGFACE_API_KEY")

if not DISCORD_TOKEN:
    logger.critical("DISCORD_BOT_TOKEN missing. Exiting.")
    sys.exit(1)

if HF_KEY and InferenceClient:
    hf_client = InferenceClient(token=HF_KEY)
    logger.info("Hugging Face initialized.")
else:
    hf_client = None
    logger.warning("Hugging Face disabled.")


# -------------------------
# DATABASE
# -------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        coins INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 0,
        last_daily INTEGER DEFAULT 0
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_id INTEGER,
        remind_at INTEGER,
        content TEXT
    )
    """)

    conn.commit()
    conn.close()

init_db()


# -------------------------
# HF QUERY
# -------------------------
def hf_sync(prompt):
    if not hf_client:
        return None, "HF not enabled"
    try:
        msgs = [
            {"role": "system", "content": "You are friendly and helpful."},
            {"role": "user", "content": prompt}
        ]
        resp = hf_client.chat_completion(messages=msgs, model=HF_MODEL)
        txt = resp.choices[0].message["content"]
        return txt, None
    except Exception as e:
        return None, str(e)


async def hf_query(prompt):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: hf_sync(prompt))


# -------------------------
# REMINDER WORKER
# -------------------------
async def reminder_worker():
    await bot.wait_until_ready()
    logger.info("Reminder worker started.")
    while not bot.is_closed():
        try:
            now = int(time.time())
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM reminders WHERE remind_at <= ?", (now,))
            rows = c.fetchall()

            for r in rows:
                ch = bot.get_channel(r["channel_id"])
                if ch:
                    await ch.send(f"<@{r['user_id']}> ⏰ Reminder: {r['content']}")
                c.execute("DELETE FROM reminders WHERE id=?", (r["id"],))
                conn.commit()

            conn.close()
        except Exception:
            logger.exception("Reminder worker error")

        await asyncio.sleep(5)


# -------------------------
# BOT CLASS
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class BongoBot(commands.Bot):
    async def setup_hook(self):
        asyncio.create_task(reminder_worker())


bot = BongoBot(command_prefix="!", intents=intents)

# REMOVE DEFAULT HELP to avoid conflict
bot.remove_command("help")


# -------------------------
# SIMPLE HELP
# -------------------------
HELP_TEXT = """
**bongosorous Help**

`/ask` - Ask AI  
`/help` - Show help  
`!trivia` - Play trivia  
`!rps <choice>` - Rock paper scissors  
`/poll` - Poll system  
`!daily` - Daily coins  
`!balance` - Check coins  
`!give @user amount` - Give coins  
`!remindme 10m something` - Reminder
"""


@bot.tree.command(name="help", description="Show help menu")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT)


@bot.command(name="help")
async def help_cmd(ctx):
    await ctx.send(HELP_TEXT)


# -------------------------
# AI ASK
# -------------------------
@bot.tree.command(name="ask", description="Ask the AI")
@app_commands.describe(question="Your question")
async def ask(interaction: discord.Interaction, question: str):
    await interaction.response.defer(thinking=True)

    txt, err = await hf_query(question)
    if not txt:
        await interaction.followup.send("❌ AI error: " + err)
        return

    if len(txt) > MAX_RESPONSE_LENGTH:
        txt = txt[:MAX_RESPONSE_LENGTH] + "..."

    await interaction.followup.send("✨ " + txt)


# -------------------------
# ECONOMY
# -------------------------
def ensure_user(uid):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (uid,))
    conn.commit()
    conn.close()

@bot.command()
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    ensure_user(member.id)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT coins FROM users WHERE user_id=?", (member.id,))
    coins = c.fetchone()["coins"]
    conn.close()
    await ctx.send(f"{member.mention} has **{coins}** coins.")

@bot.command()
async def daily(ctx):
    ensure_user(ctx.author.id)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT last_daily FROM users WHERE user_id=?", (ctx.author.id,))
    last = c.fetchone()["last_daily"]
    now = int(time.time())

    if now - (last or 0) < 86400:
        await ctx.send("You already claimed daily.")
    else:
        reward = random.randint(50, 150)
        c.execute("UPDATE users SET coins=coins+?, last_daily=? WHERE user_id=?",
                  (reward, now, ctx.author.id))
        conn.commit()
        await ctx.send(f"You got **{reward}** coins!")
    conn.close()


# -------------------------
# REMINDERS
# -------------------------
@bot.command()
async def remindme(ctx, time_str: str, *, text: str):
    unit = time_str[-1]
    num = int(time_str[:-1])
    mult = {"s":1, "m":60, "h":3600, "d":86400}.get(unit)

    if not mult:
        await ctx.send("Use format like `10m`, `2h`, etc.")
        return

    remind_at = int(time.time()) + num * mult

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO reminders(user_id, channel_id, remind_at, content) VALUES (?, ?, ?, ?)",
        (ctx.author.id, ctx.channel.id, remind_at, text)
    )
    conn.commit()
    conn.close()

    await ctx.send(f"Reminder set for <t:{remind_at}:R>")


# -------------------------
# TRIVIA
# -------------------------
TRIVIA = [
    ("Capital of France?", "paris"),
    ("5 + 7?", "12"),
    ("Planet known as Red Planet?", "mars"),
]

active_trivia = {}

@bot.command()
async def trivia(ctx):
    q, a = random.choice(TRIVIA)
    active_trivia[ctx.channel.id] = a.lower()
    await ctx.send(f"🧠 Trivia: {q}")


@bot.event
async def on_message(msg):
    if msg.author.bot:
        return

    # Trivia check
    ans = active_trivia.get(msg.channel.id)
    if ans and msg.content.lower().strip() == ans:
        await msg.channel.send(f"{msg.author.mention} Correct! 🎉")
        del active_trivia[msg.channel.id]

    await bot.process_commands(msg)


# -------------------------
# RPS
# -------------------------
@bot.command()
async def rps(ctx, choice: str):
    choice = choice.lower()
    options = ["rock", "paper", "scissors"]

    if choice not in options:
        await ctx.send("Choose rock/paper/scissors")
        return

    bot_ch = random.choice(options)

    if choice == bot_ch:
        res = "Tie!"
    elif (choice=="rock" and bot_ch=="scissors") or \
         (choice=="paper" and bot_ch=="rock") or \
         (choice=="scissors" and bot_ch=="paper"):
        res = "You win!"
    else:
        res = "I win!"

    await ctx.send(f"You: {choice}\nBot: {bot_ch}\n**{res}**")


# -------------------------
# POLL
# -------------------------
NUMBER_EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣"]

@bot.tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Your question", options="Comma separated list", duration="Seconds")
async def poll(interaction: discord.Interaction, question: str, options: str, duration: int = 30):
    opts = [o.strip() for o in options.split(",") if o.strip()]
    if not 2 <= len(opts) <= 5:
        await interaction.response.send_message("Provide 2–5 options.")
        return

    desc = "\n".join(f"{NUMBER_EMOJI[i]} {opts[i]}" for i in range(len(opts)))
    embed = discord.Embed(title=question, description=desc)

    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()

    for i in range(len(opts)):
        await msg.add_reaction(NUMBER_EMOJI[i])

    await asyncio.sleep(duration)

    msg = await msg.channel.fetch_message(msg.id)
    result = []
    for i, opt in enumerate(opts):
        r = discord.utils.get(msg.reactions, emoji=NUMBER_EMOJI[i])
        result.append(f"{opt}: {r.count - 1} votes")

    await msg.channel.send("**Results:**\n" + "\n".join(result))


# -------------------------
# FLASK SERVER
# -------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"status": "online", "bot": BOT_NAME})

@app.route("/health")
def health():
    return jsonify({"ok": True})


def run_flask():
    port = int(os.environ.get("PORT", DEFAULT_FLASK_PORT))
    app.run(host="0.0.0.0", port=port)


# -------------------------
# RUN BOT
# -------------------------
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("Flask started.")

    bot.run(DISCORD_TOKEN)
