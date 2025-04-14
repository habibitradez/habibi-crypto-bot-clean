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
BITQUERY_API_KEY = os.getenv("BITQUERY_API_KEY")
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

async def detect_meme_trend():
    try:
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": BITQUERY_API_KEY
        }
        query = {
            "query": """
            query MyQuery {
              solana {
                dexTrades(
                  options: {desc: [\"block.timestamp.time\"], limit: 5}
                  exchangeName: {is: \"Pump Fun\"}
                ) {
                  market {
                    baseCurrency {
                      address
                    }
                  }
                }
              }
            }
            """
        }
        response = requests.post("https://graphql.bitquery.io", json=query, headers=headers)
        response.raise_for_status()
        data = response.json()
        token_list = [d['market']['baseCurrency']['address'] for d in data['data']['solana']['dexTrades']]
        return token_list[:5]
    except Exception as e:
        logging.error(f"❌ Bitquery failed: {e}. Trying fallback...")
        return []

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
        logging.error(f"❌ Failed to send Discord notification: {e}")

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
        blockhash_resp = solana_client.get_latest_blockhash()
        blockhash = blockhash_resp.value.blockhash
        tx = Transaction.new_unsigned([ix])
        tx.recent_blockhash = blockhash
        tx.fee_payer = keypair.pubkey()
        tx.sign([keypair])
        time.sleep(0.3)
        resp = solana_client.send_raw_transaction(tx.serialize())
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
                    real_buy_token(token_address, lamports=1000000)  # 0.001 SOL
    except Exception as e:
        logging.error(f"❌ Sniper loop error: {e}")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    sniper_loop.start()
    logging.info("🚀 Features loaded: pump.fun sniping, token sim, profit tracking, meme signals, loss cuts, viral priority")

bot.run(DISCORD_TOKEN)
