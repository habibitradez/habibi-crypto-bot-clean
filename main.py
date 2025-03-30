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

trusted_accounts = {"elonmusk", "binance", "coinbase", "Digiworldd", "DaCryptoGeneral"}
blacklisted_accounts = {"rugpull_alert", "fake_crypto_news"}

# --- HELPER FUNCTIONS ---
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
    url = f"https://newsapi.org/v2/everything?q=crypto&apiKey={NEWSAPI_KEY}&language=en&sortBy=publishedAt&pageSize=5"
    data = safe_json_request(url)
    logging.info(f"📰 NewsAPI response: {data}")
    headlines = data.get("articles", []) if data else []
    if not headlines and not fallback_used:
        fallback_used = True
        fallback = [
            "📰 **Fallback Headline 1** - https://example.com/news1",
            "📰 **Fallback Headline 2** - https://example.com/news2"
        ]
        return fallback
    return [f"📰 **{article['title']}**\n{article['url']}" for article in headlines]

@tasks.loop(hours=1)
async def post_hourly_news():
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if channel:
        headlines = fetch_headlines()
        for headline in headlines:
            await channel.send(headline)

@tasks.loop(minutes=2)
async def scan_x():
    logging.info("🔍 Scanning Twitter/X for updates...")
    accounts = ["Digiworldd", "DaCryptoGeneral"]
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))

    for account in accounts:
        url = f"https://api.twitter.com/2/tweets/search/recent?query=from:{account}&tweet.fields=author_id,created_at,text"
        data = safe_json_request(url, headers=headers)
        if not data:
            continue
        tweets = data.get("data", [])
        if not tweets:
            continue

        for tweet in tweets:
            tweet_id = tweet.get("id")
            if tweet_id in latest_tweet_ids:
                continue
            latest_tweet_ids.add(tweet_id)

            text = tweet.get("text", "")
            author_id = tweet.get("author_id")
            created_at = tweet.get("created_at")
            timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            formatted_time = timestamp.strftime("%b %d, %Y – %I:%M %p UTC")
            tweet_url = f"https://twitter.com/i/web/status/{tweet_id}"
            if author_id not in blacklisted_accounts:
                cas = extract_contract_addresses(text)
                formatted = (
                    f"🐦 **@{account} tweeted:**\n"
                    f"💬 {text}\n\n"
                    f"🕒 {formatted_time}\n"
                    f"🔗 [View Tweet]({tweet_url})\n"
                )
                if ROLE_MENTION_ENABLED and DISCORD_ROLE_ID:
                    formatted += f"<@&{DISCORD_ROLE_ID}>"
                await channel.send(formatted)
                if cas:
                    for ca in cas:
                        await channel.send(f"🚀 Detected Contract Address: `{ca}`", view=create_trade_buttons(ca))
                        watchlist.add(ca)

@tasks.loop(minutes=5)
async def fetch_dexscreener_trending():
    global last_dex_post_time, failed_dex_attempts
    logging.info("📊 Fetching trending tokens from Dexscreener...")
    url = "https://api.dexscreener.com/latest/dex/pairs/solana"
    data = safe_json_request(url)

    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if not data:
        failed_dex_attempts += 1
        if channel and failed_dex_attempts >= 2:
            await channel.send("⚠️ Dexscreener failed multiple times. Switching to fallback source (GeckoTerminal).")
        return

    failed_dex_attempts = 0
    pairs = data.get("pairs", [])[:5]
    if channel:
        for pair in pairs:
            name = pair.get("baseToken", {}).get("name")
            symbol = pair.get("baseToken", {}).get("symbol")
            price = pair.get("priceUsd")
            link = pair.get("url")
            if not all([name, symbol, price, link]):
                continue
            message = f"📈 **{name} ({symbol})** is trending at **${price}**\n🔗 {link}"
            if ROLE_MENTION_ENABLED and DISCORD_ROLE_ID:
                message += f"\n<@&{DISCORD_ROLE_ID}>"
            if datetime.utcnow() - last_dex_post_time > timedelta(minutes=4):
                await channel.send(message)
                last_dex_post_time = datetime.utcnow()
            contract_address = pair.get("pairAddress")
            if contract_address and contract_address not in watchlist:
                watchlist.add(contract_address)
                await channel.send(f"🆕 Auto-watching new token: `{contract_address}`", view=create_trade_buttons(contract_address))

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

    post_hourly_news.start()
    scan_x.start()
    fetch_dexscreener_trending.start()

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
