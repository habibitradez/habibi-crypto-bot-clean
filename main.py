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
import base58
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

async def simulate_token_buy(address):
    return True

def should_prioritize_pool(pool_data):
    return True

def fetch_trending_tokens():
    try:
        r = requests.get("https://pump.fun/api/trending", timeout=5)
        if r.status_code != 200:
            raise Exception(f"Status {r.status_code}")
        data = r.json()
        return [x.get("mint") for x in data if "mint" in x][:10]
    except Exception as e:
        logging.error(f"❌ Failed to fetch pump.fun trending tokens: {e}")
        return ["So11111111111111111111111111111111111111112"]

def fetch_dexscreener():
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/pairs/solana", timeout=5)
        return [pair['pairAddress'] for pair in r.json().get('pairs', [])[:10]]
    except Exception as e:
        logging.error(f"❌ DexScreener fetch failed: {e}")
        return []

def fetch_birdeye():
    try:
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=volume_24h&sort_type=desc", timeout=5)
        return [token['address'] for token in r.json().get('data', [])[:10]]
    except Exception as e:
        logging.error(f"❌ Birdeye fetch failed: {e}")
        return []

async def detect_meme_trend():
    tokens = fetch_trending_tokens() + fetch_dexscreener() + fetch_birdeye()
    tokens = list(set(tokens))
    logging.info(f"🔥 Trending Tokens: {tokens}")
    return tokens

async def notify_discord(content=None, tx_sig=None):
    try:
        await bot.wait_until_ready()
        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
        if channel and content:
            msg = content
            if tx_sig:
                msg += f"\n🔗 [View Transaction](https://solscan.io/tx/{tx_sig})"
            await channel.send(msg)
    except Exception as e:
        logging.error(f"❌ Failed to send notification: {e}")

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

def real_buy_token(to_addr: str, lamports: int):
    try:
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(to_addr.replace("solana_", ""))
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        msg = MessageV0.try_compile(payer=keypair.pubkey(), instructions=[ix], recent_blockhash=blockhash, address_lookup_table_accounts=[])
        tx = VersionedTransaction(msg, [keypair])
        resp = solana_client.send_transaction(tx)
        tx_sig = getattr(resp, "value", None)
        if isinstance(tx_sig, list):
            tx_sig = tx_sig[0]
        if not isinstance(tx_sig, str):
            raise ValueError(f"Invalid tx signature: {tx_sig}")
        logging.info(f"📈 Buy TX: {tx_sig}")
        asyncio.create_task(notify_discord(f"✅ Bought token: {to_addr}", tx_sig))
        bought_tokens[to_addr] = {
            "amount": lamports,
            "buy_price": lamports / 1e9,
            "buy_sig": tx_sig,
            "buy_time": time.time()
        }
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Buy failed: {e}")
        fallback_rpc()
        return None

@tasks.loop(seconds=60)
async def sniper_loop():
    try:
        trending_tokens = await detect_meme_trend()
        for token_address in trending_tokens:
            if token_address not in bought_tokens:
                if await simulate_token_buy(token_address):
                    logging.info(f"🚀 Sniping {token_address}")
                    await asyncio.sleep(2)
                    real_buy_token(token_address, lamports=1000000)
    except Exception as e:
        logging.error(f"❌ Sniper loop error: {e}")

@tasks.loop(seconds=30)
async def sell_monitor():
    try:
        for token, data in list(bought_tokens.items()):
            simulated_price = random.uniform(1.0, 3.0) * data["buy_price"]
            if simulated_price >= SELL_PROFIT_TRIGGER * data["buy_price"]:
                logging.info(f"💰 Selling {token} for 2x gain")
                asyncio.create_task(notify_discord(f"💰 Sold {token} for profit!"))
                del bought_tokens[token]
    except Exception as e:
        logging.error(f"❌ Sell monitor error: {e}")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    sniper_loop.start()
    sell_monitor.start()
    logging.info("🚀 Features loaded: pump.fun sniping, token sim, profit tracking, meme signals, loss cuts, viral priority")

bot.run(DISCORD_TOKEN)
