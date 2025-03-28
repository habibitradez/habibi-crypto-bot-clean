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
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer
from discord.ui import View, Button
import asyncio
from datetime import datetime

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
PHANTOM_PUBLIC_KEY = os.getenv("PHANTOM_PUBLIC_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")

openai.api_key = OPENAI_API_KEY
solana_client = Client("https://api.mainnet-beta.solana.com")

phantom_keypair = None
if PHANTOM_SECRET_KEY:
    try:
        phantom_keypair = Keypair.from_base58_string(PHANTOM_SECRET_KEY.strip())
    except Exception as e:
        logging.warning(f"⚠️ Could not initialize Phantom keypair: {e}")
else:
    logging.warning("⚠️ No PHANTOM_SECRET_KEY provided. Trading functions will be disabled.")

phantom_wallet = PHANTOM_PUBLIC_KEY

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.guild_messages = True
bot = commands.Bot(command_prefix="/", intents=intents)

discord.utils.setup_logging(level=logging.INFO)

watchlist = set()
sniped_contracts = []
gain_tracking = {}
posted_social_placeholders = False
profit_log = {}

# Whitelist and blacklist sets
trusted_accounts = {"elonmusk", "binance", "coinbase"}  # Whitelisted usernames
blacklisted_accounts = {"rugpull_alert", "fake_crypto_news"}

# --- HELPER FUNCTIONS ---
def safe_json_request(url, headers=None):
    try:
        res = requests.get(url, headers=headers or {"User-Agent": "HabibiBot/1.0"}, timeout=10)
        logging.info(f"✅ Fetched URL: {url}")
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
    entry = {"ca": ca, "amount": amount, "timestamp": str(datetime.utcnow())}
    sniped_contracts.append(entry)
    with open("sniped_contracts.json", "w") as f:
        json.dump(sniped_contracts, f, indent=2)
    gain_tracking[ca] = {"buy_price": 1.0, "target_50": False, "target_100": False, "celebrity": False}
    profit_log[ca] = {"buy_price": 1.0, "sell_price": 0.0, "profit": 0.0, "status": "holding"}

def execute_auto_trade(ca, celebrity=False):
    if phantom_keypair and ca:
        amount = 1 if celebrity else 0.5
        logging.info(f"🚀 Auto-sniping CA: {ca} with {amount} SOL...")
        log_sniped_contract(ca, amount)
        return True
    else:
        logging.info(f"⛔ Skipping snipe for {ca} – Phantom key not connected.")
    return False

def send_sol(recipient_str: str, amount_sol: float):
    recipient_pubkey = Pubkey.from_string(recipient_str)
    lamports = int(amount_sol * 1_000_000_000)
    tx = Transaction()
    tx.add(transfer(TransferParams(
        from_pubkey=phantom_keypair.pubkey(),
        to_pubkey=recipient_pubkey,
        lamports=lamports
    )))
    try:
        result = solana_client.send_transaction(tx, phantom_keypair)
        logging.info(f"✅ Sent {amount_sol} SOL to {recipient_str}. Result: {result}")
        return True
    except Exception as e:
        logging.error(f"❌ Error sending SOL: {e}")
        return False

def fetch_headlines():
    url = f"https://newsapi.org/v2/top-headlines?q=crypto&apiKey={NEWSAPI_KEY}&language=en&pageSize=5"
    data = safe_json_request(url)
    logging.info(f"📰 NewsAPI response: {data}")
    headlines = data.get("articles", [])
    if not headlines:
        fallback = [
            "📰 **Fallback Headline 1** - https://example.com/news1",
            "📰 **Fallback Headline 2** - https://example.com/news2"
        ]
        return fallback
    return [f"📰 **{article['title']}**\n{article['url']}" for article in headlines]

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type.name == "component":
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("buy_"):
            parts = custom_id.split("_")
            if len(parts) == 3:
                amount = float(parts[1])
                recipient = parts[2]
                if send_sol(recipient, amount):
                    await interaction.response.send_message(f"🛒 Sent {amount} SOL to `{recipient}`", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ Failed to send SOL.", ephemeral=True)
        elif custom_id == "sell_token":
            await interaction.response.send_message("💸 Selling token (mocked)", ephemeral=True)

@bot.tree.command(name="wallet", description="Show Phantom wallet balance")
async def wallet(interaction: discord.Interaction):
    try:
        if not phantom_wallet:
            await interaction.response.send_message("❌ Phantom wallet not configured.", ephemeral=True)
            return
        balance = solana_client.get_balance(phantom_wallet)["result"]["value"] / 1_000_000_000
        await interaction.response.send_message(f"💰 Phantom wallet balance: `{balance:.4f} SOL`", ephemeral=True)
    except Exception as e:
        logging.error(f"❌ Error in /wallet command: {e}")
        await interaction.response.send_message(f"❌ Error fetching balance: {e}", ephemeral=True)

@bot.tree.command(name="profits", description="Show tracked token profits")
async def profits(interaction: discord.Interaction):
    if not profit_log:
        await interaction.response.send_message("📉 No profits to show yet.", ephemeral=True)
        return
    lines = []
    for ca, info in profit_log.items():
        lines.append(f"`{ca[:6]}...`: Buy {info['buy_price']} | Sell {info['sell_price']} | Status: {info['status']} | PnL: {info['profit']} SOL")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="news", description="Get the latest crypto news")
async def news(interaction: discord.Interaction):
    headlines = fetch_headlines()
    for headline in headlines:
        await interaction.channel.send(headline)
    await interaction.response.send_message("📰 Latest news posted.", ephemeral=True)

@tasks.loop(minutes=60)
async def post_hourly_news():
    if not DISCORD_NEWS_CHANNEL_ID:
        logging.warning("⚠️ DISCORD_NEWS_CHANNEL_ID not set.")
        return
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if channel:
        headlines = fetch_headlines()
        for headline in headlines:
            await channel.send(headline)

@tasks.loop(seconds=30)
async def monitor_gains():
    for ca, info in gain_tracking.items():
        price = 1.0  # Mock price logic
        if not info["target_50"] and price >= info["buy_price"] * 1.5:
            info["target_50"] = True
            logging.info(f"📢 ALERT: {ca} has reached +50% gain!")
        if not info["target_100"] and price >= info["buy_price"] * 2.0:
            info["target_100"] = True
            if info.get("celebrity"):
                logging.info(f"💰 Sold initial for {ca} (celebrity). Alerting for manual profit sell.")
                profit_log[ca]["sell_price"] = price
                profit_log[ca]["profit"] = price - profit_log[ca]["buy_price"]
                profit_log[ca]["status"] = "partial sell"
            else:
                logging.info(f"💰 Auto-sold {ca} at +100% profit.")
                profit_log[ca]["sell_price"] = price
                profit_log[ca]["profit"] = price - profit_log[ca]["buy_price"]
                profit_log[ca]["status"] = "sold"

@tasks.loop(seconds=60)
async def scan_x():
    logging.info("🔍 Scanning X for contract mentions...")
    headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
    query = "(CA OR contract OR token) (0x) lang:en -is:retweet"
    url = f"https://api.twitter.com/2/tweets/search/recent?query={query}&tweet.fields=author_id,text,created_at&expansions=author_id&user.fields=username,public_metrics"

    try:
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            logging.error(f"❌ Twitter API error: {res.status_code} {res.text}")
            return

        data = res.json()
        tweets = data.get("data", [])
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

        for tweet in tweets:
            text = tweet.get("text", "")
            ca_list = extract_contract_addresses(text)
            if not ca_list:
                continue

            author_id = tweet.get("author_id")
            user = users.get(author_id, {})
            username = user.get("username", "")
            followers = user.get("public_metrics", {}).get("followers_count", 0)

            for ca in ca_list:
                if username.lower() in blacklisted_accounts:
                    logging.info(f"🚫 Skipping blacklisted user {username}")
                    continue

                celebrity = username.lower() in trusted_accounts or followers > 50_000
                if execute_auto_trade(ca, celebrity=celebrity):
                    logging.info(f"✅ Sniped CA {ca} from @{username} ({followers} followers)")

    except Exception as e:
        logging.error(f"❌ Failed to scan Twitter: {e}")

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    try:
        synced = await bot.tree.sync()
        logging.info(f"✅ Synced {len(synced)} commands with Discord")
    except Exception as e:
        logging.error(f"❌ Command sync failed: {e}")
    post_hourly_news.start()
    monitor_gains.start()
    scan_x.start()
    logging.info(f"🤖 Logged in as {bot.user} and ready.")

bot.run(DISCORD_TOKEN)

