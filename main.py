# --- Fallback for environments missing micropip or standard modules ---
try:
    import discord
    from discord.ext import commands, tasks
    from discord import app_commands
except ModuleNotFoundError as e:
    print("⚠️ Discord module not found. This code must be run in a Python environment where 'discord.py' is installed.")
    print("Run: pip install discord.py")
    raise e

import requests
import openai
import os
import logging
import re
import json
from dotenv import load_dotenv
from discord.ui import View, Button
import asyncio
from datetime import datetime, timedelta
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import random
from bs4 import BeautifulSoup
import snscrape.modules.twitter as sntwitter
from solana.rpc.api import Client
from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
import base58
import matplotlib.pyplot as plt
import io
import base64

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
PHANTOM_PUBLIC_KEY = os.getenv("PHANTOM_PUBLIC_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
DISCORD_ROLE_ID = os.getenv("DISCORD_ROLE_ID")
WALLET_ENABLED = True
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"
TOKAPI_KEY = os.getenv("TOKAPI_KEY")

if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN is missing. Check your .env file.")
    exit(1)

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

solana_client = Client("https://api.mainnet-beta.solana.com")
bought_tokens = {}

# --- Convert Phantom Secret Key to Keypair ---
def get_phantom_keypair():
    try:
        secret = json.loads(PHANTOM_SECRET_KEY)
        return Keypair.from_bytes(bytes(secret))
    except Exception as e:
        logging.error(f"Error loading Phantom secret key: {e}")
        return None

# --- Notify to Discord ---
def notify_discord(msg, file=None):
    try:
        payload = {"content": msg}
        files = {"file": file} if file else None
        requests.post(f"https://discord.com/api/webhooks/{DISCORD_NEWS_CHANNEL_ID}", data=payload, files=files)
    except Exception as e:
        logging.warning(f"Failed to notify Discord: {e}")

# --- Generate and Send Chart Thumbnail ---
def send_chart_thumbnail(token_name, prices):
    try:
        fig, ax = plt.subplots()
        ax.plot(prices, marker='o')
        ax.set_title(f"{token_name} Price Chart")
        ax.set_ylabel("Price (USD)")
        ax.set_xlabel("Time (index)")

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        file = ("chart.png", buf, "image/png")

        notify_discord(f"🖼️ Chart for {token_name}", file=file)
        plt.close(fig)
    except Exception as e:
        logging.error(f"Failed to generate chart thumbnail: {e}")

# --- Portfolio Balance Tracking ---
@bot.command()
async def balance(ctx):
    keypair = get_phantom_keypair()
    if not keypair:
        await ctx.send("❌ Wallet not loaded.")
        return

    try:
        balance = solana_client.get_balance(keypair.pubkey())['result']['value'] / 1_000_000_000
        await ctx.send(f"💰 Current SOL Balance: {balance:.4f} SOL")
    except Exception as e:
        await ctx.send("⚠️ Failed to fetch balance.")
        logging.error(f"Balance check failed: {e}")

# --- Bot Commands ---
@bot.command()
async def holdings(ctx):
    if not bought_tokens:
        await ctx.send("📉 No active holdings.")
        return

    message = "📊 **Active Holdings**:\n"
    for ca, info in bought_tokens.items():
        message += f"- `{ca}`: Bought at ${info['buy_price']:.4f}, Boosted: {info['boosted']}, Time: {info['time'].strftime('%H:%M:%S')} UTC\n"
    await ctx.send(message)

@bot.command()
async def toggle_sniping(ctx):
    global WALLET_ENABLED
    WALLET_ENABLED = not WALLET_ENABLED
    state = "enabled" if WALLET_ENABLED else "disabled"
    await ctx.send(f"⚙️ Auto-sniping is now **{state}**.")

# --- Auto-sniping Result Notification ---
def record_snipe(ca, buy_price, boosted):
    bought_tokens[ca] = {
        "buy_price": buy_price,
        "boosted": boosted,
        "time": datetime.utcnow(),
    }
    notify_discord(f"🚀 Sniped token `{ca}` at ${buy_price:.4f}. Boosted: {boosted}")
    send_chart_thumbnail(ca[:8], [buy_price * (1 + 0.01 * i) for i in range(10)])

# --- Startup Events ---
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    watch_new_pumpfun_tokens.start()
    watch_birdeye_trends.start()
    watch_geckoterminal_trends.start()
    monitor_and_sell.start()

# --- Run the Bot ---
bot.run(DISCORD_TOKEN)
