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
from dotenv import load_dotenv

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_WALLET_ADDRESS = os.getenv("PHANTOM_WALLET_ADDRESS")

openai.api_key = OPENAI_API_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO)

user_wallets = {}  # Simulated wallet balances

# --- HELPER FUNCTIONS ---
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return res.json()
    except Exception as e:
        logging.error(f"❌ Error fetching {url}: {e}")
        return {}

def extract_contract_address(text):
    matches = re.findall(r"0x[a-fA-F0-9]{40}", text)
    return matches[0] if matches else None

def auto_snipe_token(ca):
    # Simulate a Phantom wallet auto-buy
    return f"🚀 Auto-sniped token at {ca} using Phantom Wallet {PHANTOM_WALLET_ADDRESS}"

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

                # Auto-snipe if CA detected
                ca = extract_contract_address(text)
                if ca:
                    mentions.append(auto_snipe_token(ca))
    return "\n\n".join(mentions) if mentions else None

def get_trending_news():
    url = f"https://newsapi.org/v2/top-headlines?category=business&q=crypto&apiKey={NEWSAPI_KEY}"
    res = safe_json_request(url)
    articles = res.get("articles", [])
    if articles:
        return f"📰 **{articles[0]['title']}**\n{articles[0]['url']}"
    return None

def get_crypto_memes():
    url = "https://www.reddit.com/r/cryptomemes/top.json?limit=3&t=day"
    headers = {"User-agent": "HabibiBot"}
    res = safe_json_request(url, headers)
    posts = res.get("data", {}).get("children", [])
    if posts:
        post = posts[0]["data"]
        return f"😂 {post['title']}\nhttps://reddit.com{post['permalink']}"
    return None

def get_auto_snipes():
    url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    headers = {"User-agent": "HabibiBot"}
    res = safe_json_request(url, headers)
    posts = res.get("data", {}).get("children", [])
    snipes = []
    for post in posts[:5]:
        title = post["data"].get("title", "")
        if "0x" in title:
            link = f"https://reddit.com{post['data']['permalink']}"
            snipes.append(f"🎯 Auto-snipe detected: {title}\n{link}")
            ca = extract_contract_address(title)
            if ca:
                snipes.append(auto_snipe_token(ca))
    return "\n\n".join(snipes) if snipes else None

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

@tasks.loop(seconds=15)
async def run_all_alerts():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if not channel:
        return
    funcs = [
        get_twitter_mentions,
        get_trending_news,
        get_crypto_memes,
        get_auto_snipes
    ]
    for func in funcs:
        try:
            result = func()
            if result:
                await channel.send(result)
        except Exception as e:
            logging.error(f"❌ Error in {func.__name__}: {e}")

# --- PHANTOM WALLET & BUY/SELL FEATURES ---
@bot.command()
async def connectwallet(ctx):
    user_wallets[ctx.author.id] = user_wallets.get(ctx.author.id, {"SOL": 2.5})
    await ctx.send("🔐 Phantom Wallet connected! You can now use buttons to buy/sell tokens.")

@bot.command()
async def balance(ctx):
    wallet = user_wallets.get(ctx.author.id, {"SOL": 0})
    await ctx.send(f"💰 Balance: {wallet['SOL']} SOL")

@bot.command()
async def trade(ctx):
    view = discord.ui.View()
    for amount in [0.5, 1, 2, 3, 5]:
        view.add_item(discord.ui.Button(label=f"Buy {amount} SOL", style=discord.ButtonStyle.green, custom_id=f"buy_{amount}"))
        view.add_item(discord.ui.Button(label=f"Sell {amount} SOL", style=discord.ButtonStyle.red, custom_id=f"sell_{amount}"))
    await ctx.send("🪙 Choose your trade action:", view=view)

@bot.event
async def on_socket_response(payload):
    if payload.get("t") != "INTERACTION_CREATE":
        return
    data = payload.get("d", {})
    custom_id = data.get("data", {}).get("custom_id")
    user_id = int(data.get("member", {}).get("user", {}).get("id", 0))
    if custom_id and user_id:
        action, amount = custom_id.split("_")
        amount = float(amount)
        wallet = user_wallets.setdefault(user_id, {"SOL": 2.5})

        if action == "buy":
            if wallet["SOL"] + amount > 10:
                message = f"⚠️ Cannot hold more than 10 SOL."
            else:
                wallet["SOL"] += amount
                message = f"🟢 Bought {amount} SOL. New balance: {wallet['SOL']} SOL"

        elif action == "sell":
            if wallet["SOL"] < amount:
                message = f"❌ Not enough SOL to sell."
            else:
                wallet["SOL"] -= amount
                message = f"🔴 Sold {amount} SOL. New balance: {wallet['SOL']} SOL"

        channel_id = int(data.get("channel_id"))
        channel = bot.get_channel(channel_id)
        if channel:
            await channel.send(message)

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)

