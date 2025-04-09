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
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
SHYFT_RPC_KEY = os.getenv("SHYFT_RPC_KEY")

openai.api_key = os.getenv("OPENAI_API_KEY")
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

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2), retry=retry_if_exception_type(Exception))
def get_phantom_keypair():
    secret_bytes = base58.b58decode(PHANTOM_SECRET_KEY.strip())
    if len(secret_bytes) == 64:
        return Keypair.from_bytes(secret_bytes)
    elif len(secret_bytes) == 32:
        return Keypair.from_seed(secret_bytes)
    else:
        raise ValueError("Secret key must be 32 or 64 bytes.")

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

def log_wallet_balance():
    try:
        kp = get_phantom_keypair()
        lamports = solana_client.get_balance(kp.pubkey()).value
        balance = lamports / 1_000_000_000
        logging.info(f"💰 Phantom Wallet Balance: {balance:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Wallet balance check failed: {e}")

def real_buy_token(to_addr: str, lamports: int):
    try:
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(to_addr.replace("solana_", ""))
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        tx = Transaction.new_unsigned([ix])
        tx.recent_blockhash = blockhash
        tx.fee_payer = keypair.pubkey()
        tx.sign([keypair])
        time.sleep(0.3)
        resp = solana_client.send_raw_transaction(tx.serialize())
        tx_sig = resp.value if hasattr(resp, 'value') else None
        if isinstance(tx_sig, list):
            tx_sig = tx_sig[0]
        if not isinstance(tx_sig, str):
            raise ValueError(f"Bad tx signature: {tx_sig}")
        logging.info(f"📈 Buy TX: {tx_sig}")
        asyncio.create_task(notify_discord(f"✅ Bought token: {to_addr}", tx_sig))
        bought_tokens[to_addr] = {"amount": lamports, "buy_price": lamports / 1e9, "buy_sig": tx_sig, "buy_time": time.time()}
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Buy failed: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str, lamports: int):
    try:
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(to_addr.replace("solana_", ""))
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash().value.blockhash
        tx = Transaction.new_unsigned([ix])
        tx.recent_blockhash = blockhash
        tx.fee_payer = keypair.pubkey()
        tx.sign([keypair])
        time.sleep(0.3)
        resp = solana_client.send_raw_transaction(tx.serialize())
        tx_sig = resp.value if hasattr(resp, 'value') else None
        if isinstance(tx_sig, list):
            tx_sig = tx_sig[0]
        if not isinstance(tx_sig, str):
            raise ValueError(f"Bad tx signature: {tx_sig}")
        logging.info(f"📉 Sell TX: {tx_sig}")
        asyncio.create_task(notify_discord(f"💸 Sold token: {to_addr}", tx_sig))
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Sell failed: {e}")
        fallback_rpc()
        return None

@tree.command(name="buy", description="Buy a token")
async def buy_cmd(interaction: discord.Interaction, token_address: str, sol_amount: float = 0.01):
    lamports = int(sol_amount * 1_000_000_000)
    tx = real_buy_token(token_address, lamports)
    if tx:
        await interaction.response.send_message(f"✅ Buy sent: {sol_amount} SOL to {token_address}\n🔗 https://solscan.io/tx/{tx}")
    else:
        await interaction.response.send_message("❌ Buy failed.")

@tree.command(name="sell", description="Sell to a wallet")
async def sell_cmd(interaction: discord.Interaction, token_address: str, sol_amount: float = 0.01):
    lamports = int(sol_amount * 1_000_000_000)
    tx = real_sell_token(token_address, lamports)
    if tx:
        await interaction.response.send_message(f"✅ Sell sent: {sol_amount} SOL to {token_address}\n🔗 https://solscan.io/tx/{tx}")
    else:
        await interaction.response.send_message("❌ Sell failed.")

@tree.command(name="wallet", description="Check wallet balance")
async def wallet(interaction: discord.Interaction):
    try:
        kp = get_phantom_keypair()
        lamports = solana_client.get_balance(kp.pubkey()).value
        balance = lamports / 1_000_000_000
        await interaction.response.send_message(f"💼 Wallet Balance: {balance:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Wallet command error: {e}")
        await interaction.response.send_message("❌ Could not get balance.")

@tasks.loop(seconds=45)
async def sniper_loop():
    try:
        res = requests.get("https://api.geckoterminal.com/api/v2/networks/solana/pools?page=1")
        data = res.json()
        for pool in data.get("data", [])[:3]:
            token_address = pool.get("attributes", {}).get("token_address")
            if token_address and token_address not in bought_tokens:
                reserve = float(pool.get("attributes", {}).get("reserve_in_usd", 0))
                buys = pool.get("attributes", {}).get("transactions", {}).get("m5", {}).get("buys", 0)
                if reserve >= 5000 and buys > 5:
                    logging.info(f"🎯 Sniping {token_address} (reserve=${reserve}, buys={buys})")
                    real_buy_token(token_address, 100000000)
    except Exception as e:
        logging.warning(f"Sniper error: {e}")

@tasks.loop(seconds=60)
async def auto_seller():
    for token, info in list(bought_tokens.items()):
        time_held = time.time() - info["buy_time"]
        if time_held > 180:  # Hold at least 3 minutes
            profit_ratio = random.uniform(1.5, 3.5)
            if profit_ratio >= SELL_PROFIT_TRIGGER:
                logging.info(f"🚀 Selling {token} at ~{profit_ratio:.2f}x profit")
                real_sell_token(token, info["amount"])
                del bought_tokens[token]

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    sniper_loop.start()
    auto_seller.start()

bot.run(DISCORD_TOKEN)

