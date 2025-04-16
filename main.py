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
from solders.transaction import VersionedTransaction
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.hash import Hash
from solders.transaction_status import EncodedTransaction
import base58
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
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
SHYFT_RPC_KEY = os.getenv("SHYFT_RPC_KEY")
BITQUERY_API_KEY = os.getenv("BITQUERY_API_KEY", "H1FlmA.MxT2zi3Zm~~eohOFKv8")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

rpc_endpoints = [
    f"https://rpc.shyft.to?api_key={SHYFT_RPC_KEY}",
    "https://api.mainnet-beta.solana.com",
    "https://solana-mainnet.g.alchemy.com/v2/demo"
]
solana_client = Client(rpc_endpoints[0])

bought_tokens = {}
SELL_PROFIT_TRIGGER = 2.0
LOSS_CUT_PERCENT = 0.4
SIMULATED_GAIN_CAP = 2.0
bitquery_unauthorized = False

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2), retry=retry_if_exception_type(Exception))
def get_phantom_keypair():
    secret_bytes = base58.b58decode(PHANTOM_SECRET_KEY.strip())
    if len(secret_bytes) == 64:
        return Keypair.from_bytes(secret_bytes)
    elif len(secret_bytes) == 32:
        return Keypair.from_seed(secret_bytes)
    else:
        raise ValueError("Secret key must be 32 or 64 bytes.")

def log_wallet_balance():
    try:
        kp = get_phantom_keypair()
        lamports = solana_client.get_balance(kp.pubkey()).value
        balance = lamports / 1_000_000_000
        logging.info(f"💰 Phantom Wallet Balance: {balance:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Wallet balance check failed: {e}")

def fetch_birdeye():
    try:
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=volume_24h&sort_type=desc", timeout=5)
        return [token['address'] for token in r.json().get('data', [])[:10]]
    except Exception as e:
        logging.error(f"❌ Birdeye fetch failed: {e}")
        return []

async def detect_meme_trend():
    tokens = fetch_birdeye()
    logging.info(f"🔥 Trending Tokens: {tokens}")
    return tokens

def fallback_rpc():
    global solana_client
    for endpoint in rpc_endpoints[1:]:
        try:
            test_client = Client(endpoint)
            test_key = get_phantom_keypair().pubkey()
            test_client.get_balance(test_key)
            solana_client = test_client
            logging.info(f"✅ Switched to fallback RPC: {endpoint}")
            return
        except Exception as e:
            logging.warning(f"❌ Fallback RPC {endpoint} failed: {e}")

def sanitize_token_address(addr: str) -> str:
    addr = addr.strip()
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
        raise ValueError("Invalid token address")
    return addr

def decode_transaction_blob(blob_str: str) -> bytes:
    try:
        return base64.b64decode(blob_str)
    except Exception:
        return base58.b58decode(blob_str)

def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=1").json()
        logging.info(f"📊 Quote fetched: {quote}")

        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0
        }).json()
        logging.info(f"🔄 Swap generated: {swap}")

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        tx = VersionedTransaction.from_bytes(tx_data)
        tx.sign([kp])
        logging.info(f"📝 TX signed: {base58.b58encode(tx.serialize()).decode()}")

        sig = solana_client.send_transaction(tx)
        logging.info(f"✅ Buy tx: {sig}")
        return sig
    except Exception as e:
        logging.error(f"❌ Buy failed: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=1").json()
        logging.info(f"📊 Quote fetched: {quote}")

        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0
        }).json()
        logging.info(f"🔄 Swap generated: {swap}")

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        tx = VersionedTransaction.from_bytes(tx_data)
        tx.sign([kp])
        logging.info(f"📝 TX signed: {base58.b58encode(tx.serialize()).decode()}")

        sig = solana_client.send_transaction(tx)
        logging.info(f"✅ Sell tx: {sig}")
        return sig
    except Exception as e:
        logging.error(f"❌ Sell failed: {e}")
        fallback_rpc()
        return None

@bot.command()
async def buy(ctx, token: str):
    try:
        await ctx.send(f"Buying {token}...")
        sig = real_buy_token(token, 1000000)
        if sig:
            await ctx.send(f"✅ Bought {token}! https://solscan.io/tx/{sig}")
        else:
            await ctx.send(f"❌ Buy failed for {token}. Check logs for details.")
    except Exception as e:
        await ctx.send(f"🚫 Error: {e}")

@bot.command()
async def sell(ctx, token: str):
    try:
        await ctx.send(f"Selling {token}...")
        sig = real_sell_token(token)
        if sig:
            await ctx.send(f"✅ Sold {token}! https://solscan.io/tx/{sig}")
        else:
            await ctx.send(f"❌ Sell failed for {token}. Check logs for details.")
    except Exception as e:
        await ctx.send(f"🚫 Error: {e}")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    logging.info("🚀 Features loaded: real buy/sell via Jupiter API, Discord buy/sell commands active")

bot.run(DISCORD_TOKEN)
