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
from dotenv import load_dotenv

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO)

# --- HELPER FUNCTIONS ---
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        logging.error(f"❌ Error fetching {url}: {e}")
        return {}

def get_twitter_mentions():
    headers = {
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN.strip()}"
    }
    users = [
        "blknoiz06", "larpvontrier", "poe_ether", "thecexoffernder",
        "arrogantfrfr", "larpalt", "iambroots", "uniswapvillain", "crashiusclay69",
        "kanyewest", "elonmusk", "FIFAWorldCup"
    ]
    mentions = []
    for user in users:
        user_url = f"https://api.twitter.com/2/users/by/username/{user}"
        res = safe_json_request(user_url, headers)
        user_data = res.get("data", {})
        user_id = user_data.get("id")
        if user_id:
            timeline_url = f"https://api.twitter.com/2/users/{user_id}/tweets?max_results=5&expansions=attachments.media_keys&media.fields=url,preview_image_url,type&tweet.fields=created_at,text"
            tweets = safe_json_request(timeline_url, headers)
            tweet_data = tweets.get("data", [])
            media_map = {m["media_key"]: m.get("url") or m.get("preview_image_url") for m in tweets.get("includes", {}).get("media", [])}
            for tweet in tweet_data:
                text = tweet.get("text", "")
                tweet_url = f"https://twitter.com/{user}/status/{tweet['id']}"
                media_url = ""
                media_keys = tweet.get("attachments", {}).get("media_keys", [])
                if media_keys:
                    media_url = media_map.get(media_keys[0], "")
                formatted = f"🐦 **@{user}**:\n{text}\n{tweet_url}"
                if media_url:
                    formatted += f"\n📸 {media_url}"
                mentions.append(formatted)
    return "\n\n".join(mentions) if mentions else None

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    run_all_alerts.start()

@tasks.loop(seconds=15)  # Post faster: every 15 seconds
async def run_all_alerts():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if not channel:
        return
    funcs = [
        get_twitter_mentions
    ]
    for func in funcs:
        try:
            result = func()
            if result:
                await channel.send(result)
        except Exception as e:
            logging.error(f"❌ Error in {func.__name__}: {e}")

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)

