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
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.system_program import TransferParams, transfer
import base58

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
WALLET_ENABLED = True  # Explicitly enabling wallet use
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"
TOKAPI_KEY = os.getenv("TOKAPI_KEY")

if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN is missing. Check your .env file.")
    exit(1)

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

# --- Solana Client ---
solana_client = Client("https://api.mainnet-beta.solana.com")

# --- Price & Token Tracker ---
bought_tokens = {}  # Format: {"CA": {"buy_price": float, "boosted": bool}}

# --- Convert Phantom Secret Key to Keypair ---
def get_phantom_keypair():
    try:
        secret = json.loads(PHANTOM_SECRET_KEY)
        return Keypair.from_secret_key(bytes(secret))
    except Exception as e:
        logging.error(f"Error loading Phantom secret key: {e}")
        return None

# --- Notify to Discord ---
def notify_discord(msg):
    try:
        if DISCORD_NEWS_CHANNEL_ID:
            requests.post(f"https://discord.com/api/webhooks/{DISCORD_NEWS_CHANNEL_ID}", json={"content": msg})
    except Exception as e:
        logging.warning(f"Failed to notify Discord: {e}")

# --- Auto-Snipe with Jupiter Aggregator ---
def auto_snipe_token(token_address, boosted=False):
    if not WALLET_ENABLED:
        logging.info("Wallet not enabled, skipping snipe.")
        return

    keypair = get_phantom_keypair()
    if not keypair:
        return

    amount_sol = 0.15 if boosted else 0.015
    amount_lamports = int(amount_sol * 1_000_000_000)

    try:
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount={amount_lamports}&slippageBps=500"
        quote = requests.get(quote_url).json()

        if not quote.get("data"):
            logging.warning("⚠️ No quote returned from Jupiter.")
            return

        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_payload = {
            "userPublicKey": str(keypair.public_key),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote["data"][0],
            "computeUnitPriceMicroLamports": 5000
        }
        swap_res = requests.post(swap_url, json=swap_payload).json()
        swap_tx = swap_res.get("swapTransaction")

        if not swap_tx:
            logging.warning("⚠️ Swap TX not generated.")
            return

        from base64 import b64decode
        from solana.transaction import Transaction as SolTx

        raw_tx = b64decode(swap_tx)
        tx = SolTx.deserialize(raw_tx)
        tx.sign(keypair)
        tx_sig = solana_client.send_raw_transaction(tx.serialize())

        logging.info(f"✅ Jupiter TX sent for {token_address}: {tx_sig}")
        notify_discord(f"🚀 Jupiter snipe sent for `{token_address}` with {amount_sol} SOL")

        # Track purchase
        bought_tokens[token_address] = {
            "buy_price": float(quote["data"][0].get("outAmount", 0)) / 1e9,
            "boosted": boosted
        }

    except Exception as e:
        logging.error(f"❌ Jupiter snipe failed for {token_address}: {e}")

# --- Auto Sell Monitor ---
@tasks.loop(seconds=30)
async def monitor_and_sell():
    keypair = get_phantom_keypair()
    if not keypair:
        return

    for token_address in list(bought_tokens.keys()):
        try:
            url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{token_address}"
            res = requests.get(url)
            data = res.json()

            if "pair" not in data:
                continue

            price_usd = float(data["pair"].get("priceUsd", 0))
            buy_price = bought_tokens[token_address]["buy_price"]

            if price_usd >= buy_price * 2:
                notify_discord(f"💸 Selling `{token_address}` at 2x gain! Current: ${price_usd:.4f}, Buy: ${buy_price:.4f}")
                # Placeholder for sell logic
                del bought_tokens[token_address]

        except Exception as e:
            logging.error(f"Price check/sell failed for {token_address}: {e}")

# --- Pump.fun Token Watcher ---
@tasks.loop(seconds=15)
async def watch_new_pumpfun_tokens():
    url = "https://client-api.pump.fun/latest/tokens"
    try:
        data = requests.get(url).json()
        if not data or "tokens" not in data:
            return

        for token in data["tokens"][:3]:
            name = token.get("name")
            ca = token.get("tokenId")
            creator = token.get("twitterHandle")
            price = token.get("price", "N/A")
            liquidity = token.get("liquidity", "N/A")
            message = f"🚨 **New Pump Token**: {name}\n💰 Price: {price} | 🧪 CA: `{ca}`\n🐦 Creator: @{creator}\n🔗 https://pump.fun/{ca}"
            logging.info(message)
            notify_discord(message)

            boosted = creator is not None and creator != ""
            auto_snipe_token(ca, boosted=boosted)

    except Exception as e:
        logging.error(f"❌ Error fetching pump.fun tokens: {e}")
