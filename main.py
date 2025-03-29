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
from datetime import datetime

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
PHANTOM_PUBLIC_KEY = os.getenv("PHANTOM_PUBLIC_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
WALLET_ENABLED = os.getenv("WALLET_ENABLED", "false").lower() == "true"

if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN is missing. Check your .env file.")
    exit(1)

openai.api_key = OPENAI_API_KEY

# Conditionally import Solana functionality
phantom_keypair = None
phantom_wallet = None
if WALLET_ENABLED:
    try:
        from solders.keypair import Keypair
        from solders.pubkey import Pubkey
        from solana.rpc.api import Client
        from solana.transaction import Transaction
        from solana.system_program import TransferParams, transfer

        solana_client = Client("https://api.mainnet-beta.solana.com")

        if PHANTOM_SECRET_KEY:
            phantom_keypair = Keypair.from_base58_string(PHANTOM_SECRET_KEY.strip())
            phantom_wallet = PHANTOM_PUBLIC_KEY
        else:
            logging.warning("⚠️ PHANTOM_SECRET_KEY not provided. Wallet features will be disabled.")
    except Exception as e:
        logging.warning(f"⚠️ Solana wallet setup failed: {e}")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

discord.utils.setup_logging(level=logging.INFO)

watchlist = set()
sniped_contracts = []
gain_tracking = {}
posted_social_placeholders = False
profit_log = {}

trusted_accounts = {"elonmusk", "binance", "coinbase"}
blacklisted_accounts = {"rugpull_alert", "fake_crypto_news"}

# --- HELPER FUNCTIONS ---
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers or {"User-Agent": "HabibiBot/1.0"}, timeout=10)
        logging.info(f"✅ Fetched URL: {url}")
        return res.json()
    except Exception as e:
        logging.error(f"❌ Error fetching {url}: {e}")
        return {}

def extract_contract_addresses(text):
    return re.findall(r"0x[a-fA-F0-9]{40}", text)

def create_trade_buttons(ca=None):
    view = View()
    if ca:
        view.add_item(Button(label="Buy 0.5 SOL", style=discord.ButtonStyle.green, custom_id=f"buy_0.5_{ca}"))
        view.add_item(Button(label="Buy 1 SOL", style=discord.ButtonStyle.green, custom_id=f"buy_1_{ca}"))
        view.add_item(Button(label="Buy 5 SOL", style=discord.ButtonStyle.green, custom_id=f"buy_5_{ca}"))
    view.add_item(Button(label="Sell Token", style=discord.ButtonStyle.red, custom_id="sell_token"))
    return view

def fetch_headlines():
    url = f"https://newsapi.org/v2/top-headlines?q=crypto&apiKey={NEWSAPI_KEY}&language=en&pageSize=5"
    data = safe_json_request(url)
    logging.info(f"📰 NewsAPI response: {data}")
    headlines = data.get("articles", [])
    if not headlines:
        fallback = [
            "📰 **Fallback Headline 1** - https://example.com/news1",
            "📰 **Fallback Headline 2** - https://example.com/news2"
        ]
        return fallback
    return [f"📰 **{article['title']}**\n{article['url']}" for article in headlines]

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    try:
        synced = await tree.sync()
        logging.info(f"✅ Synced {len(synced)} commands with Discord")
    except Exception as e:
        logging.error(f"❌ Command sync failed: {e}")
    logging.info(f"🤖 Logged in as {bot.user} and ready.")
    print(f"🤖 Habibi is online as {bot.user}")

    try:
        post_hourly_news.start()
    except NameError:
        logging.warning("⚠️ post_hourly_news task not defined")

    try:
        monitor_gains.start()
    except NameError:
        logging.warning("⚠️ monitor_gains task not defined")

    try:
        scan_x.start()
    except NameError:
        logging.warning("⚠️ scan_x task not defined")

@bot.event
async def on_error(event, *args, **kwargs):
    logging.exception(f"Unhandled error in event: {event}")

@bot.event
async def on_disconnect():
    logging.warning("⚠️ Bot disconnected from Discord")

@bot.event
async def on_resumed():
    logging.info("🔄 Bot resumed connection with Discord")

@bot.event
async def on_connect():
    logging.info("🔌 Bot connecting to Discord...")

bot.run(DISCORD_TOKEN)
