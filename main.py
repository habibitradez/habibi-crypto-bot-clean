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

def fetch_trending_tweets():
    trending_tweets = []
    try:
        headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
        keywords = "crypto OR solana OR eth OR $btc OR trending OR coin OR CA OR invest OR Buy"
        url = f"https://api.twitter.com/2/tweets/search/recent?query={keywords}&max_results=10&tweet.fields=created_at,text,author_id"
        data = safe_json_request(url, headers)
        if data and "data" in data:
            for tweet in data["data"]:
                text = tweet.get("text")
                tweet_url = f"https://twitter.com/i/web/status/{tweet['id']}"
                tweet_entry = f"🐦 **Trending Tweet**\n{text}\n🔗 {tweet_url}"
                logging.info(f"📢 Tweet: {tweet_entry}")
                trending_tweets.append(tweet_entry)
        else:
            logging.warning("⚠️ No tweet data returned from Twitter API.")
    except Exception as e:
        logging.error(f"❌ Failed to fetch tweets: {e}")
    return trending_tweets

def fetch_trending_crypto():
    url = "https://api.geckoterminal.com/api/v2/networks/solana/pools"
    data = safe_json_request(url)
    trending = []
    if data and "data" in data:
        def get_volume_usd(pool):
            try:
                volume = pool["attributes"].get("volume_usd", 0)
                return float(volume) if isinstance(volume, (int, float, str)) else 0.0
            except (ValueError, TypeError):
                return 0.0

        for pool in sorted(data["data"], key=get_volume_usd, reverse=True)[:5]:
            token_name = pool["attributes"].get("name", "Unknown Token")
            price = pool["attributes"].get("price_usd", "N/A")
            link = f"https://www.geckoterminal.com/solana/pools/{pool['id']}"
            trending.append(f"🚀 **{token_name}** - ${price}\n🔗 {link}")
    else:
        logging.warning("⚠️ No trending crypto data fetched.")
    return trending

def fetch_memes():
    url = "https://meme-api.com/gimme/cryptocurrency/3"
    memes = []
    try:
        data = safe_json_request(url)
        if data and "memes" in data:
            for meme in data["memes"]:
                memes.append(f"🤣 **{meme['title']}**\n{meme['url']}")
    except Exception as e:
        logging.warning(f"⚠️ Meme fetch failed: {e}")
    return memes

def fetch_news():
    url = f"https://newsapi.org/v2/everything?q=crypto&apiKey={NEWSAPI_KEY}&language=en&sortBy=publishedAt&pageSize=5"
    news = []
    try:
        data = safe_json_request(url)
        if data and "articles" in data:
            for article in data["articles"]:
                news.append(f"📰 **{article['title']}**\n{article['url']}")
    except Exception as e:
        logging.warning(f"⚠️ News fetch failed: {e}")
    return news

def fetch_tiktoks():
    return ["🎵 TikTok scraping not available. Upgrade with API."]

@tasks.loop(minutes=30)
async def post_trending_content():
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if not channel:
        logging.warning("❌ Discord news channel not found.")
        return

    tweets = fetch_trending_tweets()
    crypto = fetch_trending_crypto()
    memes = fetch_memes()
    news = fetch_news()
    tiktoks = fetch_tiktoks()

    all_content = tweets + crypto + memes + news + tiktoks

    if not all_content:
        logging.warning("⚠️ No content fetched to post.")
        return

    for item in all_content:
        await channel.send(item)

    if any(any(keyword in item.lower() for keyword in ["elon", "$", "crypto", "coin", "ca", "invest", "buy"]) for item in tweets):
        logging.info("🔥 Urgent trending tweet detected, reposting tweets and crypto now.")
        for item in tweets + crypto:
            await channel.send(item)

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}!")
    post_trending_content.start()

bot.run(DISCORD_TOKEN)
