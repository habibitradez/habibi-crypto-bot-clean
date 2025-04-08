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
from solana.rpc.api import Client
from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
import base58
import matplotlib.pyplot as plt
import io
import base64
import ssl
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
try:
    ssl._create_default_https_context = ssl._create_unverified_context
    logging.info("⚠️ SSL verification disabled for legacy scraping fallback.")
except Exception as e:
    logging.warning(f"Could not patch SSL verification: {e}")

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
DISCORD_ROLE_ID = os.getenv("DISCORD_ROLE_ID")
BITQUERY_API_KEY = os.getenv("BITQUERY_API_KEY")
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"

GECKO_BASE_URL = "https://api.geckoterminal.com/api/v2/networks/solana"
TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
TWITTER_HEADERS = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

solana_client = Client("https://api.mainnet-beta.solana.com")
bought_tokens = {}
total_profit_usd = 0.0
SELL_PROFIT_TRIGGER = 2.0
MIN_BUYERS_FOR_SELL = 5

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2), retry=retry_if_exception_type(Exception))
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
        logging.error(f"Error decoding Phantom key: {e}")
        raise

def log_wallet_balance():
    try:
        kp = get_phantom_keypair()
        balance_lamports = solana_client.get_balance(kp.pubkey()).value
        balance_sol = balance_lamports / 1_000_000_000
        logging.info(f"💰 Phantom Wallet Balance: {balance_sol:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Failed to get wallet balance: {e}")

def notify_discord(content=None, tx_sig=None):
    async def _send():
        try:
            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
            if channel and content:
                if tx_sig:
                    content_msg = f"{content}\n🔗 [View Transaction](https://solscan.io/tx/{tx_sig})"
                else:
                    content_msg = content
                await channel.send(content_msg)
        except Exception as e:
            logging.error(f"❌ Failed to send Discord notification: {e}")
    asyncio.create_task(_send())

def real_buy_token(token_address, lamports=1000000):
    try:
        token_address = token_address.replace("solana_", "")
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(token_address)
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        transaction = Transaction.new_unsigned([ix])
        transaction.recent_blockhash = blockhash
        transaction.fee_payer = keypair.pubkey()
        transaction.sign([keypair])
        time.sleep(0.3)  # Throttle to avoid 429
        tx_response = solana_client.send_raw_transaction(transaction.serialize())
        tx_sig = tx_response.value if hasattr(tx_response, 'value') else None
        if not isinstance(tx_sig, str):
            raise ValueError("Invalid tx signature returned")
        logging.info(f"📈 Real buy executed: TX Signature = {tx_sig}")
        notify_discord(f"✅ Bought token: solana_{token_address}", tx_sig)
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Real buy failed: {e}")
        return None

def real_sell_token(recipient_pubkey_str, lamports=1000000):
    try:
        recipient_pubkey_str = recipient_pubkey_str.replace("solana_", "")
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(recipient_pubkey_str)
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        transaction = Transaction.new_unsigned([ix])
        transaction.recent_blockhash = blockhash
        transaction.fee_payer = keypair.pubkey()
        transaction.sign([keypair])
        time.sleep(0.3)  # Throttle
        tx_response = solana_client.send_raw_transaction(transaction.serialize())
        tx_sig = tx_response.value if hasattr(tx_response, 'value') else None
        if not isinstance(tx_sig, str):
            raise ValueError("Invalid tx signature returned")
        logging.info(f"📉 Real sell executed: TX Signature = {tx_sig}")
        notify_discord(f"💸 Sold token: solana_{recipient_pubkey_str}", tx_sig)
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Real sell failed: {e}")
        return None

def get_recent_contract_mentions():
    try:
        query = {"query": "contract OR launch OR $SOL", "max_results": 10, "tweet.fields": "created_at"}
        response = requests.get(TWITTER_SEARCH_URL, headers=TWITTER_HEADERS, params=query)
        data = response.json()
        texts = [tweet["text"] for tweet in data.get("data", [])]
        cas = set(re.findall(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b', " ".join(texts)))
        return list(cas)
    except Exception as e:
        logging.warning(f"Twitter fetch error: {e}")
        return []

def get_trending_gecko_tokens():
    try:
        url = f"{GECKO_BASE_URL}/trending_pools"
        resp = requests.get(url)
        data = resp.json()
        return [item["id"] for item in data["data"]]
    except Exception as e:
        logging.warning(f"⚠️ GeckoTerminal trending token fetch failed: {e}")
        return []

@tasks.loop(seconds=30)
async def monitor_tokens():
    cas = set(get_recent_contract_mentions())
    trending = set(get_trending_gecko_tokens())
    combined = list(cas.union(trending))
    for token_address in combined:
        if token_address not in bought_tokens:
            logging.info(f"💰 Sniping token: {token_address}")
            real_buy_token(token_address)
            bought_tokens[token_address] = {"bought_price": 1.0, "buyer_count": 1}
        else:
            if random.random() > 0.5:
                real_sell_token(token_address)
                notify_discord(f"💸 Sold `{token_address}` due to simulation trigger.")
                del bought_tokens[token_address]

@bot.command()
async def wallet(ctx):
    try:
        kp = get_phantom_keypair()
        balance_lamports = solana_client.get_balance(kp.pubkey()).value
        balance_sol = balance_lamports / 1_000_000_000
        await ctx.send(f"💼 Phantom Wallet: `{kp.pubkey()}`\n💰 Balance: `{balance_sol:.4f}` SOL")
    except Exception as e:
        await ctx.send("❌ Failed to fetch wallet balance.")
        logging.error(f"Wallet command error: {e}")

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user.name}")
    log_wallet_balance()
    monitor_tokens.start()

bot.run(DISCORD_TOKEN)

