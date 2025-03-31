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
        trending_memes = fetch_trending_memes()
        for meme in trending_memes:
            await channel.send(meme)
        trending_tiktoks = fetch_trending_tiktoks()
        for tiktok in trending_tiktoks:
            await channel.send(tiktok)

def fetch_memes():
    meme_sources = [
        "https://meme-api.com/gimme/cryptocurrency/3",
        "https://meme-api.com/gimme/Bitcoin/3",
        "https://meme-api.com/gimme/memeeconomy/3",
        "https://meme-api.com/gimme/dankmemes/3"
    ]
    memes = []
    for url in meme_sources:
        data = safe_json_request(url)
        if data:
            posts = data.get("memes") or ([data] if data.get("url") else [])
            for meme in posts:
                if meme.get("url"):
                    title = meme.get("title", "Funny Meme")
                    memes.append(f"🤣 **{title}**\n{meme['url']}")
    return memes

def fetch_trending_memes():
    trending_keywords = ["ashton hall", "morning routine", "woke up to", "trillion dollar", "god soldier"]
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    meme_messages = []

    for keyword in trending_keywords:
        url = f"https://api.twitter.com/2/tweets/search/recent?query={keyword}&max_results=5&tweet.fields=created_at,text,author_id"
        data = safe_json_request(url, headers)
        if data and "data" in data:
            for tweet in data["data"]:
                author_id = tweet["author_id"]
                tweet_url = f"https://x.com/{author_id}/status/{tweet['id']}"
                meme_messages.append(f"🔥 Trending Meme:\n{tweet['text']}\n{tweet_url}")
    return meme_messages

def fetch_trending_tiktoks():
    # Placeholder for TikTok scraping (replace with API/service or updated logic)
    trending = [
        "🎵 Trending TikTok:
https://www.tiktok.com/@crypto_creator/video/7212345678901234567",
        "🎵 Trending TikTok:
https://www.tiktok.com/@memeking/video/7212345678907654321"
    ]
    return trending

@bot.event
async def on_ready():
    print(f"🔥 Bot is online as {bot.user}")
    post_hourly_news.start()
    fetch_and_post_twitter.start()
    fetch_and_post_coins.start()

@tasks.loop(minutes=5)
async def fetch_and_post_twitter():
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    accounts = ",".join(trusted_accounts)
    url = f"https://api.twitter.com/2/users/by?usernames={accounts}"
    users_data = safe_json_request(url, headers)
    if not users_data or "data" not in users_data:
        return

    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    for user in users_data["data"]:
        user_id = user["id"]
        tweet_url = f"https://api.twitter.com/2/users/{user_id}/tweets?max_results=5&tweet.fields=created_at"
        tweets = safe_json_request(tweet_url, headers)
        if not tweets or "data" not in tweets:
            continue
        for tweet in tweets["data"]:
            if tweet["id"] in latest_tweet_ids:
                continue
            latest_tweet_ids.add(tweet["id"])
            text = tweet["text"]
            if any(bl in text.lower() for bl in blacklisted_accounts):
                continue
            msg = f"🐦 **{user['username']}** tweeted:\n{text}\nhttps://x.com/{user['username']}/status/{tweet['id']}"
            await channel.send(msg)

@tasks.loop(minutes=10)
async def fetch_and_post_coins():
    url = "https://api.dexscreener.com/latest/dex/pairs/solana"
    data = safe_json_request(url)
    if not data or "pairs" not in data:
        return

    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    for pair in data["pairs"][:5]:
        name = pair.get("baseToken", {}).get("name")
        symbol = pair.get("baseToken", {}).get("symbol")
        address = pair.get("pairAddress")
        price_usd = pair.get("priceUsd")
        if not all([name, symbol, address, price_usd]):
            continue
        msg = f"💰 Trending Coin: **{name} ({symbol})**\nPrice: ${price_usd}\nhttps://dexscreener.com/solana/{address}"
        view = create_trade_buttons(address)
        await channel.send(msg, view=view)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
