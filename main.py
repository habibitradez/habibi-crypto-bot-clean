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
WALLET_ENABLED = os.getenv("WALLET_ENABLED", "false").lower() == "true"
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"

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

logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

watchlist = set()
sniped_contracts = []
gain_tracking = {}
posted_social_placeholders = False
profit_log = {}
latest_tweet_ids = set()
last_dex_post_time = datetime.min
failed_dex_attempts = 0
fallback_used = False

trusted_accounts = {"elonmusk", "binance", "coinbase", "Digiworldd", "DaCryptoGeneral", "joerogan", "kanyewest"}
blacklisted_accounts = {"rugpull_alert", "fake_crypto_news"}

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), retry=retry_if_exception_type(requests.exceptions.RequestException))
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers or {"User-Agent": "HabibiBot/1.0"}, timeout=10)
        logging.info(f"✅ Fetched URL: {url}")
        content_type = res.headers.get('Content-Type', '')
        if 'application/json' not in content_type:
            logging.warning(f"⚠️ Non-JSON response. Content-Type: {content_type}, Body: {res.text[:300]}")
            return None
        return res.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error fetching {url}: {e}")
        raise

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
    global fallback_used
    news_sources = [
        f"https://newsapi.org/v2/everything?q=crypto&apiKey={NEWSAPI_KEY}&language=en&sortBy=publishedAt&pageSize=5",
        f"https://cryptopanic.com/api/v1/posts/?auth_token={NEWSAPI_KEY}&public=true&currencies=BTC,ETH,SOL,DOGE"
    ]
    headlines = []
    for url in news_sources:
        data = safe_json_request(url)
        if not data:
            continue
        if "articles" in data:
            headlines.extend([f"📰 **{article['title']}**\n{article['url']}" for article in data.get("articles", [])])
        elif "results" in data:
            headlines.extend([f"📰 **{item['title']}**\n{item['url']}" for item in data.get("results", [])])
    if not headlines and not fallback_used:
        fallback_used = True
        fallback = [
            "📰 **Fallback Headline 1** - https://example.com/news1",
            "📰 **Fallback Headline 2** - https://example.com/news2"
        ]
        return fallback
    return headlines[:10]

@tasks.loop(minutes=30)
async def post_hourly_news():
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if channel:
        headlines = fetch_headlines()
        for headline in headlines:
            await channel.send(headline)
        memes = fetch_memes()
        for meme in memes:
            await channel.send(meme)

def fetch_memes():
    meme_sources = [
        "https://meme-api.com/gimme/cryptocurrency",
        "https://meme-api.com/gimme/Bitcoin",
        "https://meme-api.com/gimme/memeeconomy",
        "https://meme-api.com/gimme/dankmemes"
    ]
    memes = []
    for url in meme_sources:
        data = safe_json_request(url)
        if data and data.get("url"):
            title = data.get("title", "Funny Meme")
            memes.append(f"🤣 **{title}**\n{data['url']}")
    return memes

# ... [rest of your unchanged code continues below] ...
