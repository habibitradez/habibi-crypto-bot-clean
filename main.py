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
SHYFT_RPC_KEY = os.getenv("SHYFT_RPC_KEY")

GECKO_BASE_URL = "https://api.geckoterminal.com/api/v2/networks/solana"
TWITTER_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"
TWITTER_HEADERS = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}

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

async def notify_discord(content=None, tx_sig=None):
    try:
        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
        if channel and content:
            content_msg = f"{content}"
            if tx_sig:
                content_msg += f"\n🔗 [View Transaction](https://solscan.io/tx/{tx_sig})"
            await channel.send(content_msg)
    except Exception as e:
        logging.error(f"❌ Failed to send Discord notification: {e}")

def fallback_rpc():
    global solana_client
    for endpoint in rpc_endpoints[1:]:
        try:
            solana_client = Client(endpoint)
            solana_client.get_health()
            logging.info(f"✅ Switched to fallback RPC: {endpoint}")
            break
        except Exception as e:
            logging.warning(f"❌ Fallback RPC {endpoint} failed: {e}")

def log_wallet_balance():
    try:
        kp = get_phantom_keypair()
        balance_lamports = solana_client.get_balance(kp.pubkey()).value
        balance_sol = balance_lamports / 1_000_000_000
        logging.info(f"💰 Phantom Wallet Balance: {balance_sol:.4f} SOL")
        if balance_sol < 0.05:
            asyncio.create_task(notify_discord(f"⚠️ Low wallet balance: {balance_sol:.4f} SOL"))
    except Exception as e:
        logging.error(f"❌ Failed to get wallet balance: {e}")

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
        time.sleep(0.3)
        tx_response = solana_client.send_raw_transaction(transaction.serialize())
        tx_sig = tx_response.value if hasattr(tx_response, 'value') else None
        if isinstance(tx_sig, list):
            tx_sig = tx_sig[0]
        if not tx_sig or not isinstance(tx_sig, str):
            raise ValueError(f"Invalid tx signature returned: {tx_sig}")
        logging.info(f"📈 Real buy executed: TX Signature = {tx_sig}")
        asyncio.create_task(notify_discord(f"✅ Bought token: solana_{token_address}", tx_sig))
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Real buy failed: {e}")
        fallback_rpc()
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
        time.sleep(0.3)
        tx_response = solana_client.send_raw_transaction(transaction.serialize())
        tx_sig = tx_response.value if hasattr(tx_response, 'value') else None
        if isinstance(tx_sig, list):
            tx_sig = tx_sig[0]
        if not tx_sig or not isinstance(tx_sig, str):
            raise ValueError(f"Invalid tx signature returned: {tx_sig}")
        logging.info(f"📉 Real sell executed: TX Signature = {tx_sig}")
        asyncio.create_task(notify_discord(f"💸 Sold token: solana_{recipient_pubkey_str}", tx_sig))
        return tx_sig
    except Exception as e:
        logging.error(f"❌ Real sell failed: {e}")
        fallback_rpc()
        return None

@tree.command(name="wallet", description="Show Phantom wallet balance")
async def wallet_command(interaction: discord.Interaction):
    try:
        kp = get_phantom_keypair()
        balance_lamports = solana_client.get_balance(kp.pubkey()).value
        balance_sol = balance_lamports / 1_000_000_000
        await interaction.response.send_message(f"💼 Phantom Wallet Balance: {balance_sol:.4f} SOL")
    except Exception as e:
        await interaction.response.send_message("❌ Failed to fetch wallet balance.")

@tree.command(name="buy", description="Buy a token by address")
async def buy_command(interaction: discord.Interaction, token_address: str, sol_amount: float = 0.01):
    lamports = int(sol_amount * 1_000_000_000)
    tx = real_buy_token(token_address, lamports)
    if tx:
        await interaction.response.send_message(f"✅ Buy triggered for `{token_address}` with `{sol_amount}` SOL\n🔗 https://solscan.io/tx/{tx}")
    else:
        await interaction.response.send_message("❌ Buy failed.")

@tree.command(name="sell", description="Sell to a wallet by address")
async def sell_command(interaction: discord.Interaction, token_address: str, sol_amount: float = 0.01):
    lamports = int(sol_amount * 1_000_000_000)
    tx = real_sell_token(token_address, lamports)
    if tx:
        await interaction.response.send_message(f"✅ Sell triggered for `{token_address}` with `{sol_amount}` SOL\n🔗 https://solscan.io/tx/{tx}")
    else:
        await interaction.response.send_message("❌ Sell failed.")

@bot.event
async def on_ready():
    try:
        await tree.sync()
        logging.info(f"✅ Logged in as {bot.user}")
        log_wallet_balance()
    except Exception as e:
        logging.error(f"❌ Failed during on_ready: {e}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Command not recognized.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required arguments for this command.")
    else:
        await ctx.send("❌ An unexpected error occurred.")
        logging.error(f"Command error: {error}")

bot.run(DISCORD_TOKEN)
