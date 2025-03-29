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
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.transactions import Transaction
from solana.system_program import TransferParams, transfer
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

openai.api_key = OPENAI_API_KEY
solana_client = Client("https://api.mainnet-beta.solana.com")

phantom_keypair = None
if PHANTOM_SECRET_KEY:
    try:
        phantom_keypair = Keypair.from_base58_string(PHANTOM_SECRET_KEY.strip())
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize Phantom keypair: {e}")
else:
    logging.warning("⚠️ No PHANTOM_SECRET_KEY provided. Trading functions will be disabled.")

phantom_wallet = PHANTOM_PUBLIC_KEY

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree

discord.utils.setup_logging(level=logging.INFO)

watchlist = set()
sniped_contracts = []
gain_tracking = {}
posted_social_placeholders = False
profit_log = {}

# Whitelist and blacklist sets
trusted_accounts = {"elonmusk", "binance", "coinbase"}  # Whitelisted usernames
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

def log_sniped_contract(ca, amount):
    entry = {"ca": ca, "amount": amount, "timestamp": str(datetime.utcnow())}
    sniped_contracts.append(entry)
    with open("sniped_contracts.json", "w") as f:
        json.dump(sniped_contracts, f, indent=2)
    gain_tracking[ca] = {"buy_price": 1.0, "target_50": False, "target_100": False, "celebrity": False}
    profit_log[ca] = {"buy_price": 1.0, "sell_price": 0.0, "profit": 0.0, "status": "holding"}

def execute_auto_trade(ca, celebrity=False):
    if phantom_keypair and ca:
        amount = 1 if celebrity else 0.5
        logging.info(f"🚀 Auto-sniping CA: {ca} with {amount} SOL...")
        log_sniped_contract(ca, amount)
        return True
    else:
        logging.info(f"⛔ Skipping snipe for {ca} – Phantom key not connected.")
    return False

def send_sol(recipient_str: str, amount_sol: float):
    recipient_pubkey = Pubkey.from_string(recipient_str)
    lamports = int(amount_sol * 1_000_000_000)
    tx = Transaction()
    tx.add(transfer(TransferParams(
        from_pubkey=phantom_keypair.pubkey(),
        to_pubkey=recipient_pubkey,
        lamports=lamports
    )))
    try:
        result = solana_client.send_transaction(tx, phantom_keypair)
        logging.info(f"✅ Sent {amount_sol} SOL to {recipient_str}. Result: {result}")
        return True
    except Exception as e:
        logging.error(f"❌ Error sending SOL: {e}")
        return False

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
    post_hourly_news.start()
    monitor_gains.start()
    scan_x.start()

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

