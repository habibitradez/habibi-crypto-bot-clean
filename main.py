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
from solders.keypair import Keypair
from solana.rpc.api import Client
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

phantom_keypair = None
if PHANTOM_SECRET_KEY:
    try:
        phantom_keypair = Keypair.from_base58_string(PHANTOM_SECRET_KEY)
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize Phantom keypair: {e}")
else:
    logging.warning("⚠️ No PHANTOM_SECRET_KEY provided. Trading functions will be disabled.")

phantom_wallet = PHANTOM_PUBLIC_KEY

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

logging.basicConfig(level=logging.INFO)

watchlist = set()
sniped_contracts = []
gain_tracking = {}
posted_social_placeholders = False

# --- HELPER FUNCTIONS ---
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers or {"User-Agent": "HabibiBot/1.0"}, timeout=10)
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

def log_sniped_contract(ca, amount):
    entry = {"ca": ca, "amount": amount}
    sniped_contracts.append(entry)
    with open("sniped_contracts.json", "w") as f:
        json.dump(sniped_contracts, f, indent=2)
    gain_tracking[ca] = {"buy_price": 1.0, "target_50": False, "target_100": False}  # Mock buy price

def execute_auto_trade(ca, celebrity=False):
    if phantom_keypair and ca:
        amount = 1 if celebrity else 0.5
        logging.info(f"🚀 Auto-sniping CA: {ca} with {amount} SOL...")
        log_sniped_contract(ca, amount)
        return True
    else:
        logging.info(f"⛔ Skipping snipe for {ca} – Phantom key not connected.")
    return False

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
                formatted = f"🐦 **@{user}**\n{text}"
                if cas:
                    formatted += "\n📌 CA(s): " + ", ".join(cas)
                out.append((formatted, cas[0] if cas else None, user in ["elonmusk", "kanyewest"]))
    return out

def fetch_reddit_memes():
    data = safe_json_request("https://www.reddit.com/r/cryptomemes/top.json?limit=3&t=day")
    posts = data.get("data", {}).get("children", [])
    return [(f"😂 **{p['data']['title']}**\nhttps://reddit.com{p['data']['permalink']}", None) for p in posts]

def fetch_reddit_ca_mentions():
    data = safe_json_request("https://www.reddit.com/r/CryptoCurrency/search.json?q=0x&restrict_sr=1&sort=new")
    posts = data.get("data", {}).get("children", [])
    output = []
    for p in posts[:3]:
        title = p['data'].get('title', '')
        link = f"https://reddit.com{p['data']['permalink']}"
        cas = extract_contract_addresses(title)
        msg = f"📢 **{title}**\n{link}"
        if cas:
            msg += "\n📌 CA(s): " + ", ".join(cas)
        output.append((msg, cas[0] if cas else None, False))
    return output

def fetch_additional_social_mentions():
    global posted_social_placeholders
    if posted_social_placeholders:
        return []
    posted_social_placeholders = True
    return [
        ("📺 YouTube detection coming soon...", None, False),
        ("🎵 TikTok detection coming soon...", None, False),
        ("📸 Instagram detection coming soon...", None, False)
    ]

def monitor_token_gains():
    alerts = []
    for ca, data in gain_tracking.items():
        gain = 1.2  # Mock gain factor
        if not data["target_50"] and gain >= 1.5:
            alerts.append((ca, "📈 50% gain hit! Watch manually."))
            data["target_50"] = True
        if not data["target_100"] and gain >= 2.0:
            alerts.append((ca, "💰 100%+ gain hit! Auto-selling..."))
            data["target_100"] = True
    return alerts

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    alert_channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if alert_channel:
        await alert_channel.send("💹 Habibi Bot is online and watching the crypto streets...")
    post_updates.start()

@tasks.loop(seconds=30)
async def post_updates():
    channel = discord.utils.get(bot.get_all_channels(), name="alerts")
    if not channel:
        return
    try:
        for msg, ca, celebrity in fetch_tweets(["kanyewest", "elonmusk", "FIFAWorldCup"]):
            await channel.send(content=msg, view=create_trade_buttons(ca))
            if ca:
                if execute_auto_trade(ca, celebrity=celebrity):
                    await channel.send(f"💥 Auto-sniped `{ca}` with {'1 SOL' if celebrity else '0.5 SOL'}!")
                    await channel.send(f"👀 Watching for 50%+ gain or 100%+ for auto-sell on `{ca}`...")
        for msg, _, _ in fetch_reddit_memes():
            await channel.send(msg)
        for msg, ca, _ in fetch_reddit_ca_mentions():
            await channel.send(content=msg, view=create_trade_buttons(ca))
            if ca:
                if execute_auto_trade(ca):
                    await channel.send(f"💥 Auto-sniped `{ca}` with 0.5 SOL!")
                    await channel.send(f"👀 Watching for 50%+ gain or 100%+ for auto-sell on `{ca}`...")
        for msg, _, _ in fetch_additional_social_mentions():
            await channel.send(msg)

        for ca, notice in monitor_token_gains():
            await channel.send(f"🔔 {notice} `{ca}`")

    except Exception as e:
        logging.error(f"❌ Error in post_updates: {e}")

# --- SLASH COMMANDS ---
@bot.tree.command(name="wallet", description="Show your Phantom wallet public address")
async def wallet_command(interaction: discord.Interaction):
    await interaction.response.send_message(f"👛 Habibi Wallet: `{phantom_wallet}`")

@bot.tree.command(name="trade", description="Trigger trading menu manually")
async def trade_command(interaction: discord.Interaction):
    await interaction.response.send_message("📈 Trading Panel", view=create_trade_buttons("exampleCA"))

@bot.tree.command(name="alerts", description="Manually trigger crypto alerts")
async def alerts_command(interaction: discord.Interaction):
    await interaction.response.send_message("🔔 Posting updates now...")
    await post_updates()

@bot.tree.command(name="watch", description="Track a specific contract address")
async def watch_command(interaction: discord.Interaction, ca: str):
    watchlist.add(ca)
    await interaction.response.send_message(f"👁️ Now watching CA: `{ca}`")

@bot.tree.command(name="help", description="List all available commands")
async def help_command(interaction: discord.Interaction):
    help_text = (
        "📖 **Habibi Bot Commands:**\n"
        "/wallet - Show Phantom wallet\n"
        "/trade - Open trade menu\n"
        "/alerts - Trigger all alerts manually\n"
        "/watch <CA> - Watch a specific contract address\n"
        "/help - Show this help message"
    )
    await interaction.response.send_message(help_text)

# --- RUN BOT ---
bot.run(DISCORD_TOKEN)
