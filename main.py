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
TWITTER_BEARER_TOKEN = "AAAAAAAAAAAAAAAAAAAAAAOb0AEAAAAACMYR%2BPqdDgB5XreJwpWAmHsRidU%3DlrpGW9u4lbPouN05th1j804d8ZBEYxQB8LdFKvTvazXd43NK43"

openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO)

# --- HELPER FUNCTIONS ---

def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = requests.get(url).json()
    articles = res.get("articles", [])
    if len(articles) >= 2:
        second_news = articles[1]
        return f"**{second_news['title']}**\n{second_news['url']}"
    return None

def get_crypto_memes():
    reddit_url = "https://www.reddit.com/r/cryptomemes/top.json?limit=5&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(reddit_url, headers=headers).json()
    memes = res["data"]["children"]
    meme = memes[0]["data"]
    return f"{meme['title']}\nhttps://reddit.com{meme['permalink']}"

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
    res = requests.get(url).json()
    txns = res.get("transactions", [])
    if txns:
        txn = txns[0]
        return f"🐋 Whale Alert! {txn['amount']} {txn['symbol'].upper()} moved from {txn['from']['owner_type']} to {txn['to']['owner_type']}\nTransaction: {txn['hash']}"
    return None

def get_price_alert():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    res = requests.get(url).json()
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
        res = requests.get(url, headers=headers).json()
        posts = res.get("data", {}).get("children", [])
        for post in posts:
            title = post["data"].get("title", "")
            if any(keyword in title.lower() for keyword in keywords):
                mentions.append(f"🎤 {celeb.title()} mentioned crypto: {title}\nhttps://reddit.com{post['data']['permalink']}")
    return "\n\n".join(mentions) if mentions else None

def get_ca_mentions():
    search_url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(search_url, headers=headers).json()
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
    query = "(0x OR contract OR launch OR presale) -is:retweet lang:en"
    url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&max_results=10&tweet.fields=created_at,author_id"
    res = requests.get(url, headers=headers).json()
    tweets = res.get("data", [])
    output = []
    for tweet in tweets:
        output.append(f"🐦 Twitter Mention: {tweet['text']}")
    return "\n\n".join(output) if output else None

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

    run_all_alerts.start()  # Start the background loop

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

def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = requests.get(url).json()
    articles = res.get("articles", [])
    if len(articles) >= 2:
        second_news = articles[1]
        return f"**{second_news['title']}**\n{second_news['url']}"
    return None

def get_crypto_memes():
    reddit_url = "https://www.reddit.com/r/cryptomemes/top.json?limit=5&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(reddit_url, headers=headers).json()
    memes = res["data"]["children"]
    meme = memes[0]["data"]
    return f"{meme['title']}\nhttps://reddit.com{meme['permalink']}"

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
    res = requests.get(url).json()
    txns = res.get("transactions", [])
    if txns:
        txn = txns[0]
        return f"🐋 Whale Alert! {txn['amount']} {txn['symbol'].upper()} moved from {txn['from']['owner_type']} to {txn['to']['owner_type']}\nTransaction: {txn['hash']}"
    return None

def get_price_alert():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    res = requests.get(url).json()
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
        res = requests.get(url, headers=headers).json()
        posts = res.get("data", {}).get("children", [])
        for post in posts:
            title = post["data"].get("title", "")
            if any(keyword in title.lower() for keyword in keywords):
                mentions.append(f"🎤 {celeb.title()} mentioned crypto: {title}\nhttps://reddit.com{post['data']['permalink']}")
    return "\n\n".join(mentions) if mentions else None

def get_ca_mentions():
    search_url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(search_url, headers=headers).json()
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
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"
    }
    query = "(0x OR contract OR launch OR presale) -is:retweet lang:en"
    url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&max_results=10&tweet.fields=created_at,author_id"
    res = requests.get(url, headers=headers).json()
    tweets = res.get("data", [])
    output = []
    for tweet in tweets:
        output.append(f"🐦 Twitter Mention: {tweet['text']}")
    return "\n\n".join(output) if output else None

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

    await run_all_alerts()

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

def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = requests.get(url).json()
    articles = res.get("articles", [])
    if len(articles) >= 2:
        second_news = articles[1]
        return f"**{second_news['title']}**\n{second_news['url']}"
    return "No trending news found."

def get_crypto_memes():
    reddit_url = "https://www.reddit.com/r/cryptomemes/top.json?limit=5&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(reddit_url, headers=headers).json()
    memes = res["data"]["children"]
    meme = memes[0]["data"]
    return f"{meme['title']}\nhttps://reddit.com{meme['permalink']}"

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
    res = requests.get(url).json()
    txns = res.get("transactions", [])
    if txns:
        txn = txns[0]
        return f"🐋 Whale Alert! {txn['amount']} {txn['symbol'].upper()} moved from {txn['from']['owner_type']} to {txn['to']['owner_type']}\nTransaction: {txn['hash']}"
    return "No whale moves detected recently."

def get_price_alert():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    res = requests.get(url).json()
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
    return "\n".join(alert) if alert else "No major price movements right now."

def get_celeb_mentions():
    keywords = ["bitcoin", "crypto", "ethereum"]
    celebs = ["kanyewest", "joerogan"]
    mentions = []
    for celeb in celebs:
        url = f"https://www.reddit.com/user/{celeb}/submitted.json?limit=5"
        headers = {"User-agent": "HabibiBot"}
        res = requests.get(url, headers=headers).json()
        posts = res.get("data", {}).get("children", [])
        for post in posts:
            title = post["data"].get("title", "")
            if any(keyword in title.lower() for keyword in keywords):
                mentions.append(f"🎤 {celeb.title()} mentioned crypto: {title}\nhttps://reddit.com{post['data']['permalink']}")
    return "\n\n".join(mentions) if mentions else "No celeb mentions found."

def get_ca_mentions():
    search_url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    headers = {"User-agent": "HabibiBot"}
    res = requests.get(search_url, headers=headers).json()
    posts = res.get("data", {}).get("children", [])
    mentions = []
    for post in posts[:5]:
        title = post["data"].get("title", "")
        if "0x" in title:
            link = f"https://reddit.com{post['data']['permalink']}"
            mentions.append(f"📢 Contract Mention: {title}\n{link}")
    return "\n\n".join(mentions) if mentions else "No contract mentions found."

def get_twitter_mentions():
    headers = {
        "Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"
    }
    query = "(0x OR contract OR launch OR presale) -is:retweet lang:en"
    url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&max_results=10&tweet.fields=created_at,author_id"
    res = requests.get(url, headers=headers).json()
    tweets = res.get("data", [])
    output = []
    for tweet in tweets:
        output.append(f"🐦 Twitter Mention: {tweet['text']}")
    return "\n\n".join(output) if output else "No recent Twitter mentions."

# --- EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔁 Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Sync failed: {e}")

    for channel in bot.get_all_channels():
        print(f"📢 Found channel: {channel.name}")

    news_alert.change_interval(seconds=60)
    whale_alert.change_interval(seconds=60)
    price_alert.change_interval(seconds=60)
    celeb_alert.change_interval(seconds=60)
    ca_alert.change_interval(seconds=60)
    twitter_alert.change_interval(seconds=60)

    news_alert.start()
    whale_alert.start()
    price_alert.start()
    celeb_alert.start()
    ca_alert.start()
    twitter_alert.start()

# --- TASKS ---
@tasks.loop(seconds=60)
async def news_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            news = get_trending_news()
            await channel.send(f"📰 Habibi's News Drop:\n{news}")
        except Exception as e:
            logging.error(f"Error posting news: {e}")

@tasks.loop(seconds=60)
async def whale_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            alert = get_whale_alert()
            await channel.send(alert)
        except Exception as e:
            logging.error(f"Error in whale alert: {e}")

@tasks.loop(seconds=60)
async def price_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            alert = get_price_alert()
            await channel.send(alert)
        except Exception as e:
            logging.error(f"Error in price alert: {e}")

@tasks.loop(seconds=60)
async def celeb_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            alert = get_celeb_mentions()
            await channel.send(alert)
        except Exception as e:
            logging.error(f"Error in celeb alert: {e}")

@tasks.loop(seconds=60)
async def ca_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            alert = get_ca_mentions()
            await channel.send(alert)
        except Exception as e:
            logging.error(f"Error in CA alert: {e}")

@tasks.loop(seconds=60)
async def twitter_alert():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if channel:
        try:
            alert = get_twitter_mentions()
            await channel.send(alert)
        except Exception as e:
            logging.error(f"Error in Twitter alert: {e}")

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)
import discord
from discord.ext import commands
import requests, openai, os
from dotenv import load_dotenv

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.command()
async def news(ctx):
    await ctx.send("Crypto news coming soon...")

@bot.command()
async def ask(ctx, *, question):
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": question}]
    )
    await ctx.send(response["choices"][0]["message"]["content"])

bot.run(DISCORD_TOKEN)
