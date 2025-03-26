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

def extract_contract_address(text):
    matches = re.findall(r"0x[a-fA-F0-9]{40}", text)
    return matches[0] if matches else None

def auto_snipe_token(ca):
    recipient = phantom_wallet
    lamports = int(0.1 * 1_000_000_000)
    txn = Transaction().add(
        transfer(
            TransferParams(
                from_pubkey=phantom_keypair.public_key,
                to_pubkey=recipient,
                lamports=lamports
            )
        )
    )
    try:
        res = solana_client.send_transaction(txn, phantom_keypair)
        sig = res["result"]
        return f"🚀 **Auto-Snipe Executed**\n📄 Token: `{ca}`\n🔐 [TX on Solscan](https://solscan.io/tx/{sig})"
    except Exception as e:
        return f"❌ Auto-Snipe failed: {e}"

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
                ca = extract_contract_address(text)
                snipe_result = auto_snipe_token(ca) if ca else ""
                out.append(f"🐦 **@{user}** posted:\n{text}\n{snipe_result}")
    return out

def fetch_reddit_memes():
    headers = {"User-agent": "HabibiBot/1.0"}
    url = "https://www.reddit.com/r/cryptomemes/top.json?limit=3&t=day"
    data = safe_json_request(url, headers)
    posts = data.get("data", {}).get("children", [])
    return [f"😂 {p['data']['title']}\nhttps://reddit.com{p['data']['permalink']}" for p in posts]

def fetch_reddit_ca_mentions():
    headers = {"User-agent": "HabibiBot/1.0"}
    url = "https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new"
    data = safe_json_request(url, headers)
    posts = data.get("data", {}).get("children", [])
    output = []
    for p in posts[:3]:
        title = p['data'].get('title', '')
        link = f"https://reddit.com{p['data']['permalink']}"
        ca = extract_contract_address(title)
        snipe = auto_snipe_token(ca) if ca else ""
        output.append(f"📢 {title}\n{link}\n{snipe}")
    return output

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
        for tweet in fetch_tweets(["kanyewest", "elonmusk", "FIFAWorldCup"]):
            await channel.send(tweet)

        for meme in fetch_reddit_memes():
            await channel.send(meme)

        for ca_post in fetch_reddit_ca_mentions():
            await channel.send(ca_post)

    except Exception as e:
        logging.error(f"❌ Error in post_updates: {e}")

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)
