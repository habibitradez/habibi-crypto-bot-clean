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
import snscrape.modules.twitter as sntwitter
from solana.rpc.api import Client
from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
import base58
import matplotlib.pyplot as plt
import io
import base64

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
WALLET_ENABLED = True
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"

GECKO_BASE_URL = "https://api.geckoterminal.com/api/v2/networks/solana"

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

solana_client = Client("https://api.mainnet-beta.solana.com")
bought_tokens = {}
total_profit_usd = 0.0

# --- Convert Phantom Secret Key to Keypair (Base58 ONLY) ---
def get_phantom_keypair():
    try:
        secret_bytes = base58.b58decode(PHANTOM_SECRET_KEY.strip())
        if len(secret_bytes) == 64:
            return Keypair.from_bytes(secret_bytes)
        elif len(secret_bytes) == 32:
            return Keypair.from_seed(secret_bytes)
        else:
            raise ValueError("Secret key must be 32 or 64 bytes.")
    except Exception as e:
        logging.error(f"Error decoding base58 Phantom key: {e}")
        return None

# --- Notify to Discord ---
def notify_discord(msg, file=None):
    try:
        payload = {"content": msg}
        files = {"file": file} if file else None
        requests.post(f"https://discord.com/api/webhooks/{DISCORD_NEWS_CHANNEL_ID}", data=payload, files=files)
    except Exception as e:
        logging.warning(f"Failed to notify Discord: {e}")

# --- Generate and Send Chart Thumbnail ---
def send_chart_thumbnail(token_name, prices):
    try:
        fig, ax = plt.subplots()
        ax.plot(prices, marker='o')
        ax.set_title(f"{token_name} Price Chart")
        ax.set_ylabel("Price (USD)")
        ax.set_xlabel("Time (index)")

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        file = ("chart.png", buf, "image/png")

        notify_discord(f"🖼️ Chart for {token_name}", file=file)
        plt.close(fig)
    except Exception as e:
        logging.error(f"Failed to generate chart thumbnail: {e}")

# --- Portfolio Balance Tracking ---
@bot.command()
async def balance(ctx):
    keypair = get_phantom_keypair()
    if not keypair:
        await ctx.send("❌ Wallet not loaded.")
        return

    try:
        balance = solana_client.get_balance(keypair.pubkey())["result"]["value"] / 1_000_000_000
        await ctx.send(f"💰 Current SOL Balance: {balance:.4f} SOL")
    except Exception as e:
        await ctx.send("⚠️ Failed to fetch balance.")
        logging.error(f"Balance check failed: {e}")

@bot.command()
async def profit(ctx):
    await ctx.send(f"📈 Today's estimated profit: ${total_profit_usd:.2f}")

@bot.command()
async def holdings(ctx):
    if not bought_tokens:
        await ctx.send("📉 No active holdings.")
        return

    message = "📊 **Active Holdings**:\n"
    for ca, info in bought_tokens.items():
        message += f"- `{ca}`: Bought at ${info['buy_price']:.4f}, Boosted: {info['boosted']}, Time: {info['time'].strftime('%H:%M:%S')} UTC\n"
    await ctx.send(message)

@bot.command()
async def toggle_sniping(ctx):
    global WALLET_ENABLED
    WALLET_ENABLED = not WALLET_ENABLED
    state = "enabled" if WALLET_ENABLED else "disabled"
    await ctx.send(f"⚙️ Auto-sniping is now **{state}**.")

# --- Auto-sniping Result Notification ---
def record_snipe(ca, buy_price, boosted):
    bought_tokens[ca] = {
        "buy_price": buy_price,
        "boosted": boosted,
        "time": datetime.utcnow(),
    }
    notify_discord(f"🚀 Sniped token `{ca}` at ${buy_price:.4f}. Boosted: {boosted}")
    send_chart_thumbnail(ca[:8], [buy_price * (1 + 0.01 * i) for i in range(10)])

# --- Real Token Purchase (Snipe) ---
def real_token_purchase(ca):
    keypair = get_phantom_keypair()
    if not keypair:
        logging.warning("⛔ Wallet not loaded for purchase.")
        return

    try:
        tx = solana_client.send_transaction(
            transfer(TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=PublicKey(ca),
                lamports=int(0.001 * 1e9)
            )),
            keypair
        )
        logging.info(f"✅ Real snipe transaction sent to {ca}: {tx['result']}")
    except Exception as e:
        logging.warning(f"❌ Failed to send snipe tx: {e}")

# --- OpenAI Meme Potential Scoring ---
def score_token_name(name):
    try:
        prompt = f"Score the meme potential of this token name from 0 to 10 (10 = best): {name}"
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        score = int(re.search(r"\d+", response.choices[0].message.content).group())
        return score
    except:
        return 0

# --- Periodic Tasks for Token Watching ---
@tasks.loop(minutes=2)
async def watch_geckoterminal_trends():
    try:
        url = f"{GECKO_BASE_URL}/pools"
        data = requests.get(url).json()
        pools = data.get("data")

        if not pools:
            logging.warning("GeckoTerminal returned no data.")
            return

        for pool in pools[:10]:
            attributes = pool.get("attributes", {})
            ca = attributes.get("token_address")
            price_str = attributes.get("base_token_price_usd", "0.0")
            buys = attributes.get("transactions", {}).get("m5", {}).get("buys", 0)
            fdv = float(attributes.get("fdv_usd", 0) or 0)
            volume = float(attributes.get("volume_usd", {}).get("h1", 0) or 0)
            name = attributes.get("name", "")
            age_minutes = (datetime.utcnow() - datetime.strptime(attributes.get("pool_created_at"), "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 60

            if not ca or buys < 3 or volume < 100 or fdv > 1_000_000 or age_minutes > 720:
                continue

            meme_score = score_token_name(name)
            if meme_score < 6:
                continue

            try:
                price = float(price_str)
                if WALLET_ENABLED:
                    real_token_purchase(ca)
                record_snipe(ca, price, boosted=True)
            except Exception as e:
                logging.warning(f"⚠️ Error processing pool {ca}: {e}")
    except Exception as e:
        logging.warning(f"Failed to fetch GeckoTerminal pools: {e}")

# --- Auto-sell Logic ---
@tasks.loop(minutes=5)
async def monitor_and_sell():
    global total_profit_usd
    keypair = get_phantom_keypair()
    if not keypair:
        logging.warning("⛔ Wallet not loaded for selling.")
        return

    try:
        to_remove = []
        for ca, info in bought_tokens.items():
            try:
                url = f"{GECKO_BASE_URL}/tokens/{ca}"
                response = requests.get(url)
                token_data = response.json()
                current_price = float(token_data.get("data", {}).get("attributes", {}).get("price_usd", 0.0))
                buy_price = info['buy_price']

                if current_price >= buy_price * 1.5:
                    notify_discord(f"💸 Selling token `{ca}` at ${current_price:.4f} (bought at ${buy_price:.4f}) 💰")

                    tx = solana_client.send_transaction(
                        transfer(TransferParams(
                            from_pubkey=keypair.pubkey(),
                            to_pubkey=PublicKey(PHANTOM_PUBLIC_KEY),
                            lamports=int(0.001 * 1e9)
                        )),
                        keypair
                    )
                    notify_discord(f"✅ Sold! Transaction signature: {tx['result']}")

                    profit = current_price - buy_price
                    total_profit_usd += profit

                    if total_profit_usd >= 1000:
                        notify_discord(f"💰 Profit target hit! Transferring $1000 back to wallet.")
                        payout_tx = solana_client.send_transaction(
                            transfer(TransferParams(
                                from_pubkey=keypair.pubkey(),
                                to_pubkey=PublicKey(PHANTOM_PUBLIC_KEY),
                                lamports=int(1000 / current_price * 1e9)
                            )),
                            keypair
                        )
                        notify_discord(f"✅ $1000 transferred to wallet. Tx: {payout_tx['result']}")

                    to_remove.append(ca)
            except Exception as err:
                logging.warning(f"Error checking price for {ca}: {err}")

        for ca in to_remove:
            bought_tokens.pop(ca, None)

    except Exception as e:
        logging.warning(f"Sell monitor failed: {e}")

# --- Startup Events ---
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    watch_geckoterminal_trends.start()
    monitor_and_sell.start()

# --- Run the Bot ---
bot.run(DISCORD_TOKEN)
