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

posted_items = set()
previous_volumes = {}
urgent_cache = {}

URGENT_KEYWORDS = ["elon", "buy", "moon", "rug", "$", "contract address", "volume surge", "new ca"]

def is_urgent(item):
    lower_item = item.lower()
    return any(keyword in lower_item for keyword in URGENT_KEYWORDS)

def fetch_trending_tweets():
    trending_tweets = []
    try:
        headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
        keywords = "crypto OR solana OR eth OR $btc OR trending OR coin OR CA OR invest OR Buy"
        url = f"https://api.twitter.com/2/tweets/search/recent?query={keywords}&max_results=10&tweet.fields=created_at,text,author_id"
        data = safe_json_request(url, headers)
        logging.info(f"Twitter API Response: {json.dumps(data, indent=2)}")  # Added logging for full API response
        if data and "data" in data:
            for tweet in data["data"]:
                text = tweet.get("text")
                tweet_url = f"https://twitter.com/i/web/status/{tweet['id']}"
                if tweet_url in posted_items:
                    continue
                ca_match = re.findall(r'\b0x[a-fA-F0-9]{40}\b', text)
                ca_notice = f"\n🧾 Contract Address: {ca_match[0]}" if ca_match else ""
                tweet_entry = f"🐦 **Trending Tweet**\n{text}{ca_notice}\n🔗 {tweet_url}"
                trending_tweets.append(tweet_entry)
                posted_items.add(tweet_url)
        else:
            logging.warning("⚠️ No tweet data returned from Twitter API.")
    except Exception as e:
        logging.error(f"❌ Failed to fetch tweets: {e}")
    return trending_tweets

# Other functions remain unchanged

def fetch_trending_crypto():
    ...

def fetch_memes():
    ...

def fetch_news():
    ...

@tasks.loop(minutes=30)
async def post_trending_content():
    ...

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}!")
    post_trending_content.start()

bot.run(DISCORD_TOKEN)
