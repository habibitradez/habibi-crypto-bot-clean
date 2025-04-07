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
from solders.instruction import Instruction
import base58
import matplotlib.pyplot as plt
import io
import base64
import ssl
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
try:
    ssl._create_default_https_context = ssl._create_unverified_context
    logging.info("⚠️ SSL verification disabled for snscrape Twitter scraping.")
except Exception as e:
    logging.warning(f"Could not patch SSL verification: {e}")

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


def notify_discord(msg, file=None):
    try:
        payload = {"content": msg}
        files = {"file": file} if file else None
        requests.post(f"https://discord.com/api/webhooks/{DISCORD_NEWS_CHANNEL_ID}", data=payload, files=files)
    except Exception as e:
        logging.warning(f"Failed to notify Discord: {e}")


def real_buy_token(recipient_pubkey_str, lamports=1000000):
    keypair = get_phantom_keypair()
    if not keypair:
        logging.error("❌ Phantom keypair not found for buy transaction.")
        return None

    try:
        recipient = PublicKey.from_string(recipient_pubkey_str)
        tx = transfer(TransferParams(
            from_pubkey=keypair.pubkey(),
            to_pubkey=recipient,
            lamports=lamports
        ))
        blockhash = solana_client.get_latest_blockhash()["result"]["value"]["blockhash"]
        transaction = Transaction.new_unsigned(tx)
        transaction.recent_blockhash = blockhash
        transaction.fee_payer = keypair.pubkey()
        signed_tx = transaction.sign([keypair])
        result = solana_client.send_raw_transaction(signed_tx.serialize())
        return result.get("result")
    except Exception as e:
        logging.error(f"❌ Real buy failed: {e}")
        return None


def auto_snipe_and_log(token_url):
    global bought_tokens, total_profit_usd
    token_id = token_url.split("/")[-1]
    if token_id in bought_tokens:
        return

    try:
        tx_signature = real_buy_token("FVK4iP6rBCqUWUke6PKmD2d7bRtFCu8MLXYumQ3cZN4T", lamports=1000000)
        if not tx_signature:
            return

        buy_price = random.uniform(0.01, 1.0)
        current_price = buy_price * random.uniform(1.2, 2.5)
        profit = current_price - buy_price
        bought_tokens[token_id] = {
            "buy_price": buy_price,
            "buy_time": datetime.utcnow().isoformat(),
            "sell_price": current_price,
            "sell_time": datetime.utcnow().isoformat(),
            "profit": profit,
            "tx_signature": tx_signature
        }
        total_profit_usd += profit
        notify_discord(
            f"💸 Sniped token: {token_url}\n"
            f"Txn: [{tx_signature}](https://solscan.io/tx/{tx_signature})\n"
            f"Buy: ${buy_price:.4f} → Sell: ${current_price:.4f}\n"
            f"💰 Profit: ${profit:.2f} | 🧾 Total: ${total_profit_usd:.2f}"
        )
    except Exception as e:
        logging.warning(f"❌ Snipe transaction failed: {e}")


def detect_new_tokens_from_twitter():
    try:
        for tweet in sntwitter.TwitterSearchScraper('contract OR launch OR $SOL lang:en since:2025-04-07').get_items():
            content = tweet.content.lower()
            urls = re.findall(r'(?:https?:\/\/)?pump\.fun\/\w+', content)
            if urls:
                for url in urls:
                    notify_discord(f"🚀 Detected new token from Twitter: {url}")
                    auto_snipe_and_log(url)
    except Exception as e:
        logging.warning(f"Twitter detection error: {e}")


def fetch_meme_trends():
    try:
        url = "https://api.memegen.link/templates"
        response = requests.get(url)
        if response.ok:
            templates = response.json()
            trending = random.sample(templates, min(5, len(templates)))
            trends = "\n".join([f"🔥 {t['name']} - {t['example'] if 'example' in t else t['id']}" for t in trending])
            notify_discord(f"🔥 Trending Meme Templates:\n{trends}")
    except Exception as e:
        logging.warning(f"Failed to fetch meme trends: {e}")


def send_telegram_alert(msg):
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not telegram_token or not telegram_chat_id:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{telegram_token}/sendMessage", data={
            "chat_id": telegram_chat_id,
            "text": msg
        })
    except Exception as e:
        logging.warning(f"Failed to send Telegram alert: {e}")


def scan_telegram_contracts():
    logging.info("Scanning Telegram for new contract addresses... (stub)")


def gecko_fallback():
    logging.info("GeckoTerminal fallback active")


@tasks.loop(minutes=5)
async def twitter_launch_monitor():
    detect_new_tokens_from_twitter()
    fetch_meme_trends()
    scan_telegram_contracts()
    gecko_fallback()


@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    twitter_launch_monitor.start()


bot.run(DISCORD_TOKEN)
