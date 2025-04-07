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
    logging.info("⚠️ SSL verification disabled for legacy scraping fallback.")
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
BITQUERY_API_KEY = os.getenv("BITQUERY_API_KEY")
WALLET_ENABLED = True
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"

GECKO_BASE_URL = "https://api.geckoterminal.com/api/v2/networks/solana"
BITQUERY_URL = "https://graphql.bitquery.io"

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

solana_client = Client("https://api.mainnet-beta.solana.com")
bought_tokens = {}
total_profit_usd = 0.0

SELL_PROFIT_TRIGGER = 2.0  # 2x profit trigger
MIN_BUYERS_FOR_SELL = 5    # Sell if more than 5 buyers


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


def real_sell_token(recipient_pubkey_str, lamports=1000000):
    keypair = get_phantom_keypair()
    if not keypair:
        logging.error("❌ Phantom keypair not found for sell transaction.")
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
        logging.error(f"❌ Real sell failed: {e}")
        return None


def fetch_bitquery_data(token_address):
    headers = {"X-API-KEY": BITQUERY_API_KEY}
    query = {
        "query": """
        query MyQuery {
          solana(network: solana) {
            dexTrades(
              smartContractAddress: {is: \"%s\"}
              options: {desc: [\"block.timestamp.time\"], limit: 5}
            ) {
              transaction {
                hash
              }
              tradeAmount(in: USD)
              buyer {
                address
              }
              quotePrice
            }
          }
        }
        """ % token_address
    }
    response = requests.post(BITQUERY_URL, json=query, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        logging.warning(f"⚠️ Bitquery fetch error: {response.status_code}")
        return None


def generate_chart(token_id):
    try:
        chart_url = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{token_id}/chart"
        response = requests.get(chart_url)
        data = response.json()
        prices = [float(p[1]) for p in data['data']['attributes']['series']['usd']]  # Assuming USD series exists

        plt.figure(figsize=(6, 3))
        plt.plot(prices)
        plt.title(f"Price Chart - {token_id}")
        plt.xlabel("Time")
        plt.ylabel("Price ($)")

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return buf
    except Exception as e:
        logging.warning(f"⚠️ Failed to generate chart: {e}")
        return None
# --- Run the Bot ---
bot.run(DISCORD_TOKEN)
