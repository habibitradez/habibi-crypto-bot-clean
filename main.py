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

async def monitor_for_contracts():
    await bot.wait_until_ready()
    logging.info("📡 Contract scanner started...")
    seen_posts = set()
    while not bot.is_closed():
        sources = [
            "https://www.reddit.com/r/CryptoCurrency/new.json?limit=5",
            "https://www.reddit.com/r/cryptomemes/new.json?limit=5"
        ]
        for source in sources:
            data = safe_json_request(source)
            posts = data.get("data", {}).get("children", [])
            for post in posts:
                post_data = post.get("data", {})
                post_id = post_data.get("id")
                title = post_data.get("title", "")
                text = post_data.get("selftext", "")
                combined = f"{title} {text}"
                if post_id and post_id not in seen_posts:
                    seen_posts.add(post_id)
                    cas = extract_contract_addresses(combined)
                    if cas:
                        celeb = "verified" in title.lower() or "elon" in title.lower()
                        for ca in cas:
                            if execute_auto_trade(ca, celebrity=celeb):
                                logging.info(f"📈 Sniped {ca} from post: {title}")
        await asyncio.sleep(60)

# --- INTERACTION HANDLER ---
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

# --- SLASH COMMANDS ---
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

@bot.tree.command(name="news", description="Get the latest crypto news")
async def news(interaction: discord.Interaction):
    headlines = fetch_headlines()
    for headline in headlines:
        await interaction.channel.send(headline)
    await interaction.response.send_message("📰 Latest news posted.", ephemeral=True)

# --- BACKGROUND TASK: POST NEWS EVERY HOUR ---
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

@bot.event
async def on_ready():
    await bot.tree.sync()
    post_hourly_news.start()
    bot.loop.create_task(monitor_for_contracts())
    logging.info(f"🤖 Logged in as {bot.user} and ready.")

bot.run(DISCORD_TOKEN)
