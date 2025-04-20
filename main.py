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
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from base58 import b58decode
import ssl
import urllib3
from solders.instruction import Instruction
from solders.system_program import transfer, TransferParams
from solders.message import Message
from solders.transaction import VersionedTransaction

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception as e:
    logging.warning(f"Could not patch SSL verification: {e}")

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
SHYFT_RPC_KEY = os.getenv("SHYFT_RPC_KEY")
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
BUY_AMOUNT_LAMPORTS = 10000000
DAILY_PROFIT_GOAL = 1000

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
        logging.info(f"\U0001f4b0 Phantom Wallet Balance: {balance:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Wallet balance check failed: {e}")

def fetch_tokens():
    try:
        screener = requests.get("https://api.dexscreener.com/latest/dex/pairs/solana", timeout=5)
        data = screener.json()
        pairs = data.get("pairs", [])
        if not pairs:
            logging.warning("🚫 DEX Screener returned no pairs.")
            raise ValueError("No pairs from DEX Screener")
        return [
            {
                "address": pair['baseToken']['address'],
                "priceUsd": float(pair['priceUsd']) if 'priceUsd' in pair else 0.0
            }
            for pair in pairs[:10] if 'baseToken' in pair and 'address' in pair['baseToken']
        ]
    except Exception as e:
        logging.warning(f"⚠️ All APIs failed. Using hardcoded fallback tokens.")
        return [
            {"address": "So11111111111111111111111111111111111111112", "priceUsd": 1.0},
            {"address": "4k3Dyjzvzp8eNYk3uVwPZCzvmmYrFw1DQv3q4U2CGLuM", "priceUsd": 1.0}
        ]

def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        instruction = transfer(
            TransferParams(
                from_pubkey=kp.pubkey(),
                to_pubkey=Pubkey.from_string(to_addr),
                lamports=lamports
            )
        )
        msg = Message([instruction], kp.pubkey(), blockhash)
        tx = VersionedTransaction(msg, [kp])
        res = solana_client.send_transaction(tx, kp)
        return res.value if hasattr(res, 'value') else res
    except Exception as e:
        logging.error(f"❌ Real buy failed: {e}")
        return None

def real_sell_token(to_addr: str):
    try:
        kp = get_phantom_keypair()
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        instruction = transfer(
            TransferParams(
                from_pubkey=kp.pubkey(),
                to_pubkey=Pubkey.from_string(to_addr),
                lamports=BUY_AMOUNT_LAMPORTS
            )
        )
        msg = Message([instruction], kp.pubkey(), blockhash)
        tx = VersionedTransaction(msg, [kp])
        res = solana_client.send_transaction(tx, kp)
        return res.value if hasattr(res, 'value') else res
    except Exception as e:
        logging.error(f"❌ Real sell failed: {e}")
        return None

def log_trade(entry):
    global daily_profit
    trade_log.append(entry)
    if entry.get("type") == "sell":
        daily_profit += entry.get("profit", 0)
    logging.info(f"🧾 TRADE LOG: {entry}")
    try:
        channel = discord.utils.get(bot.get_all_channels(), name="trades")
        if channel:
            asyncio.run_coroutine_threadsafe(channel.send(f"**{entry['type'].upper()}** {entry['token']}\nTX: {entry['tx']}\nProfit: ${entry.get('profit', 0):.2f}"), bot.loop)
    except Exception as e:
        logging.warning(f"⚠️ Could not send Discord alert: {e}")

def summarize_daily_profit():
    logging.info(f"📊 Estimated Daily Profit So Far: ${daily_profit:.2f}")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    logging.info("🚀 Slash commands synced and ready.")
    bot.loop.create_task(auto_snipe())

async def auto_snipe():
    global daily_profit
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            if daily_profit >= DAILY_PROFIT_GOAL:
                logging.info("🎯 Daily profit goal reached. Pausing auto-snipe until next cycle.")
                await asyncio.sleep(60)
                continue

            tokens = fetch_tokens()
            logging.info(f"🔍 Found {len(tokens)} tokens.")
            for token_data in tokens:
                token = token_data['address']
                price = token_data['priceUsd']

                if token not in bought_tokens:
                    logging.info(f"🛒 Attempting to buy {token} at ${price:.4f}...")
                    sig = real_buy_token(token, BUY_AMOUNT_LAMPORTS)
                    if sig:
                        bought_tokens[token] = {
                            'buy_sig': sig,
                            'buy_time': datetime.utcnow(),
                            'token': token,
                            'initial_price': price
                        }
                        log_trade({"type": "buy", "token": token, "tx": sig, "timestamp": datetime.utcnow()})
                else:
                    buy_price = bought_tokens[token]['initial_price']
                    if price / buy_price >= SELL_PROFIT_TRIGGER:
                        logging.info(f"💸 Attempting to sell {token} at ${price:.4f} (bought at ${buy_price:.4f})")
                        sell_sig = real_sell_token(token)
                        if sell_sig:
                            profit = round((price - buy_price) * (BUY_AMOUNT_LAMPORTS / 1_000_000_000), 2)
                            log_trade({"type": "sell", "token": token, "tx": sell_sig, "timestamp": datetime.utcnow(), "profit": profit})
                            del bought_tokens[token]
            summarize_daily_profit()
        except Exception as e:
            logging.error(f"❌ Error in auto-snipe loop: {e}")
        await asyncio.sleep(20)

try:
    bot.run(DISCORD_TOKEN)
except Exception as e:
    logging.error(f"❌ Bot failed to run: {e}")
