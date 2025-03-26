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
from solders.keypair import Keypair
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.publickey import PublicKey
from solana.system_program import TransferParams, transfer
from discord.ui import View, Button

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
PHANTOM_PUBLIC_KEY = os.getenv("PHANTOM_PUBLIC_KEY")

openai.api_key = OPENAI_API_KEY
solana_client = Client("https://api.mainnet-beta.solana.com")
phantom_keypair = Keypair.from_base58_string(PHANTOM_SECRET_KEY)
phantom_wallet = PublicKey(PHANTOM_PUBLIC_KEY)

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

def fetch_tweets(users):
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    out = []
    for user in users:
        user_data = safe_json_request(f"https://api.twitter.com/2/users/by/username/{user}", headers)
        uid = user_data.get("data", {}).get("id")
        if uid:
            tweets = safe_json_request(
                f"https://api.twitter.com/2/users/{uid}/tweets?max_results=5&tweet.fields=created_at", headers
            ).get("data", [])
            for tweet in tweets:
                text = tweet.get("text", "")
                cas = extract_contract_addresses(text)
                formatted = f"🐦 **@{user}**:\n{text}"
                if cas:
                    formatted += "\n📌 CA(s): " + ", ".join(cas)
                out.append((formatted, cas[0] if cas else None))
    return out

def fetch_reddit_memes():
    headers = {"User-agent": "HabibiBot/1.0"}
    url = "https://www.reddit.com/r/cryptomemes/top.json?limit=3&t=day"
    data = safe_json_request(url, headers)
    posts = data.get("data", {}).get("children", [])
    return [(f"😂 **{p['data']['title']}**\nhttps://reddit.com{p['data']['permalink']}", None) for p in posts]

def fetch_reddit_ca_mentions():
    headers = {"User-agent": "HabibiBot/1.0"}
    url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    data = safe_json_request(url, headers)
    posts = data.get("data", {}).get("children", [])
    output = []
    for p in posts[:3]:
        title = p['data'].get('title', '')
        link = f"https://reddit.com{p['data']['permalink']}"
        cas = extract_contract_addresses(title)
        msg = f"📢 **{title}**\n{link}"
        if cas:
            msg += "\n📌 CA(s): " + ", ".join(cas)
        output.append((msg, cas[0] if cas else None))
    return output

def fetch_additional_social_mentions():
    results = []
    # YouTube
    try:
        yt_query = safe_json_request("https://www.googleapis.com/youtube/v3/search?q=crypto&part=snippet&type=video&key=YOUR_YOUTUBE_API_KEY")
        for item in yt_query.get("items", [])[:3]:
            title = item['snippet']['title']
            link = f"https://youtube.com/watch?v={item['id']['videoId']}"
            results.append((f"📺 **YouTube**: {title}\n{link}", None))
    except:
        results.append(("📺 YouTube detection coming soon...", None))

    # TikTok (scraped)
    try:
        tiktok_resp = requests.get("https://www.tiktok.com/tag/crypto").text
        tiktok_posts = re.findall(r'https://www\\.tiktok\\.com/@[\w\\.-]+/video/\\d+', tiktok_resp)
        for url in list(set(tiktok_posts))[:3]:
            results.append((f"🎵 TikTok Mention:\n{url}", None))
    except:
        results.append(("🎵 TikTok detection coming soon...", None))

    # Instagram (scraped)
    try:
        insta_resp = requests.get("https://www.instagram.com/explore/tags/crypto/").text
        insta_posts = re.findall(r"https://www\\.instagram\\.com/p/[\w-]+", insta_resp)
        for url in list(set(insta_posts))[:3]:
            results.append((f"📸 Instagram Mention:\n{url}", None))
    except:
        results.append(("📸 Instagram detection coming soon...", None))

    return results

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    alert_channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if alert_channel:
        await alert_channel.send("💹 Habibi Bot is online and watching the crypto streets...")
    post_updates.start()

@tasks.loop(seconds=60)
async def post_updates():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if not channel:
        return

    try:
        for msg, ca in fetch_tweets(["kanyewest", "elonmusk", "FIFAWorldCup"]):
            await channel.send(content=msg, view=create_trade_buttons(ca))

        for msg, _ in fetch_reddit_memes():
            await channel.send(msg)

        for msg, ca in fetch_reddit_ca_mentions():
            await channel.send(content=msg, view=create_trade_buttons(ca))

        for msg, _ in fetch_additional_social_mentions():
            await channel.send(msg)

    except Exception as e:
        logging.error(f"❌ Error in post_updates: {e}")

# --- SLASH COMMANDS ---
@bot.tree.command(name="wallet", description="Show your Phantom wallet public address")
async def wallet_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"👛 Habibi Wallet: `{phantom_wallet}`")

@bot.tree.command(name="trade", description="Trigger trading menu manually")
async def trade_command(interaction: discord.Interaction):
    await interaction.response.send_message("📈 Trading Panel", view=create_trade_buttons("exampleCA"))

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)
