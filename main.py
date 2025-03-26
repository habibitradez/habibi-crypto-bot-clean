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
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")  # Changed to use .env properly

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

def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = safe_json_request(url)
    articles = res.get("articles", [])
    if len(articles) >= 2:
        second_news = articles[1]
        return f"**{second_news['title']}**\n{second_news['url']}"
    return None

def get_crypto_memes():
    reddit_url = "https://www.reddit.com/r/cryptomemes/top.json?limit=5&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = safe_json_request(reddit_url, headers)
    memes = res.get("data", {}).get("children", [])
    if memes:
        meme = memes[0]["data"]
        return f"{meme['title']}\nhttps://reddit.com{meme['permalink']}"
    return None

def ask_habibi(prompt):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are Habibi, the Crypto Trading God. Answer with swagger, wisdom, and memes if needed."},
            {"role": "user", "content": prompt},
        ]
    )
    return response["choices"][0]["message"]["content"]

def get_whale_alert():
    url = "https://api.whale-alert.io/v1/transactions?api_key=demo&min_value=500000&currency=btc"
    res = safe_json_request(url)
    txns = res.get("transactions", [])
    if txns:
        txn = txns[0]
        return f"🐋 Whale Alert! {txn['amount']} {txn['symbol'].upper()} moved from {txn['from']['owner_type']} to {txn['to']['owner_type']}\nTransaction: {txn['hash']}"
    return None

def get_price_alert():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    res = safe_json_request(url)
    btc = res.get("bitcoin", {})
    eth = res.get("ethereum", {})
    alert = []
    if btc.get("usd_24h_change", 0) > 5:
        alert.append(f"🚀 BTC is pumping! +{btc['usd_24h_change']:.2f}%")
    if btc.get("usd_24h_change", 0) < -5:
        alert.append(f"📉 BTC is dumping! {btc['usd_24h_change']:.2f}%")
    if eth.get("usd_24h_change", 0) > 5:
        alert.append(f"🚀 ETH is pumping! +{eth['usd_24h_change']:.2f}%")
    if eth.get("usd_24h_change", 0) < -5:
        alert.append(f"📉 ETH is dumping! {eth['usd_24h_change']:.2f}%")
    return "\n".join(alert) if alert else None

def get_celeb_mentions():
    keywords = ["bitcoin", "crypto", "ethereum"]
    celebs = ["kanyewest", "joerogan", "elonmusk", "mrbeast", "barackobama"]
    mentions = []
    for celeb in celebs:
        url = f"https://www.reddit.com/user/{celeb}/submitted.json?limit=5"
        headers = {"User-agent": "HabibiBot"}
        res = safe_json_request(url, headers)
        posts = res.get("data", {}).get("children", [])
        for post in posts:
            title = post["data"].get("title", "")
            if any(keyword in title.lower() for keyword in keywords):
                mentions.append(f"🎤 {celeb.title()} mentioned crypto: {title}\nhttps://reddit.com{post['data']['permalink']}")
    return "\n\n".join(mentions) if mentions else None

def get_ca_mentions():
    search_url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    headers = {"User-agent": "HabibiBot"}
    res = safe_json_request(search_url, headers)
    posts = res.get("data", {}).get("children", [])
    mentions = []
    for post in posts[:5]:
        title = post["data"].get("title", "")
        if "0x" in title:
            link = f"https://reddit.com{post['data']['permalink']}"
            mentions.append(f"📢 Contract Mention: {title}\n{link}")
    return "\n\n".join(mentions) if mentions else None

def get_twitter_mentions():
    headers = {
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN.strip()}"
    }
    users = [
        "blknoiz06", "larpvontrier", "poe_ether", "thecexoffernder",
        "arrogantfrfr", "larpalt", "iambroots", "uniswapvillain", "crashiusclay69"
    ]
    mentions = []
    for user in users:
        url = f"https://api.twitter.com/2/users/by/username/{user}"
        res = safe_json_request(url, headers)
        user_id = res.get("data", {}).get("id")
        if user_id:
            timeline_url = f"https://api.twitter.com/2/users/{user_id}/tweets?max_results=5&tweet.fields=created_at"
            tweets = safe_json_request(timeline_url, headers).get("data", [])
            for tweet in tweets:
                mentions.append(f"🐦 @{user}: {tweet['text']}")
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

@tasks.loop(seconds=30)
async def run_all_alerts():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if not channel:
        return

    funcs = [
        get_trending_news,
        get_whale_alert,
        get_price_alert,
        get_celeb_mentions,
        get_ca_mentions,
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


