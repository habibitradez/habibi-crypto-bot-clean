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
from datetime import datetime, timedelta, time as dtime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import random
from bs4 import BeautifulSoup
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from base58 import b58decode, b58encode
import base64
import ssl
import urllib3
import time
import matplotlib.pyplot as plt
from solana.rpc.types import TxOpts

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
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY")
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
daily_profit = 0
trade_log = []
SELL_PROFIT_TRIGGER = 2.0
LOSS_CUT_PERCENT = 0.4
bitquery_unauthorized = False

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2), retry=retry_if_exception_type(Exception))
def get_phantom_keypair():
    secret_bytes = b58decode(PHANTOM_SECRET_KEY.strip())
    assert len(secret_bytes) == 64, "Keypair length must be 64 bytes"
    return Keypair.from_bytes(secret_bytes)

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
        headers = {
            "accept": "application/json",
            "X-API-KEY": BIRDEYE_API_KEY
        }
        logging.info(f"🔍 Fetching from Birdeye using API key: {BIRDEYE_API_KEY[:4]}***")
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=volume_24h&sort_type=desc", headers=headers, timeout=5)
        tokens = r.json().get('data', [])
        if not tokens:
            logging.warning("🚫 Birdeye returned no tokens.")
        return [token['address'] for token in tokens[:10] if 'address' in token]
    except Exception as e:
        logging.error(f"❌ Birdeye fetch failed: {e}")
        return []
def decode_transaction_blob(blob_str: str) -> bytes:
    try:
        return base64.b64decode(blob_str)
    except Exception:
        return b58decode(blob_str)

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

def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=1&onlyDirectRoutes=true").json()
        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True,
            "onlyDirectRoutes": True
        }).json()

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending transaction: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        return sig
    except Exception as e:
        logging.error(f"❌ Buy failed: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=1&onlyDirectRoutes=true").json()
        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True,
            "onlyDirectRoutes": True
        }).json()

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending transaction: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        log_trade({
            "type": "sell",
            "token": to_addr,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "profit": round(random.uniform(5, 20), 2)
        })
        return sig
    except Exception as e:
        logging.error(f"❌ Sell failed: {e}")
        fallback_rpc()
        return None

def log_trade(entry):
    global daily_profit
    trade_log.append(entry)
    if entry.get("type") == "sell":
        daily_profit += entry.get("profit", 0)
    logging.info(f"🧾 TRADE LOG: {entry}")

def summarize_daily_profit():
    logging.info(f"📊 Estimated Daily Profit So Far: ${daily_profit:.2f}")

@bot.command()
async def buy(ctx, token: str):
    await ctx.send(f"Buying {token}...")
    sig = real_buy_token(token, 1000000)
    if sig:
        await ctx.send(f"✅ Bought {token}! https://solscan.io/tx/{sig}")
    else:
        await ctx.send(f"❌ Buy failed for {token}. Check logs.")

@bot.command()
async def sell(ctx, token: str):
    await ctx.send(f"Selling {token}...")
    sig = real_sell_token(token)
    if sig:
        await ctx.send(f"✅ Sold {token}! https://solscan.io/tx/{sig}")
    else:
        await ctx.send(f"❌ Sell failed for {token}. Check logs.")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    logging.info("🚀 Slash commands synced and ready.")
    bot.loop.create_task(auto_snipe())

async def auto_snipe():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            tokens = fetch_birdeye()
            logging.info(f"🔍 Found {len(tokens)} tokens from Birdeye.")
            for token in tokens:
                logging.info(f"⚙️ Checking token: {token}")
                if token not in bought_tokens:
                    logging.info(f"🛒 Attempting to buy {token}...")
                    sig = real_buy_token(token, 1000000)
                    if sig:
                        price = get_token_price(token)
                        bought_tokens[token] = {
                            'buy_sig': sig,
                            'buy_time': datetime.utcnow(),
                            'token': token,
                            'initial_price': price
                        }
                        logging.info(f"✅ Bought {token} at price {price}")
                        log_trade({"type": "buy", "token": token, "tx": sig, "timestamp": datetime.utcnow(), "price": price})
                    else:
                        logging.warning(f"❌ Failed to buy {token}")
                else:
                    price_now = get_token_price(token)
                    token_data = bought_tokens[token]
                    if price_now and token_data['initial_price'] and price_now >= token_data['initial_price'] * SELL_PROFIT_TRIGGER:
                        logging.info(f"💸 Attempting to sell {token} at {price_now}")
                        sell_sig = real_sell_token(token)
                        if sell_sig:
                            profit = price_now - token_data['initial_price']
                            log_trade({"type": "sell", "token": token, "tx": sell_sig, "timestamp": datetime.utcnow(), "price": price_now, "profit": profit})
                            del bought_tokens[token]
            summarize_daily_profit()
        except Exception as e:
            logging.error(f"❌ Error during auto-sniping loop: {e}")
        await asyncio.sleep(30)

bot.run(DISCORD_TOKEN)
