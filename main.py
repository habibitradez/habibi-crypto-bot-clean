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
BITQUERY_URL = "https://graphql.bitquery.io"
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

def send_sol(destination_wallet: str, amount_sol: float):
    try:
        kp = get_phantom_keypair()
        recent_blockhash = solana_client.get_latest_blockhash()["result"]["value"]["blockhash"]
        lamports = int(amount_sol * 1_000_000_000)
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=PublicKey.from_string(destination_wallet), lamports=lamports))
        tx = Transaction.new_unsigned([ix])
        tx.sign([kp])
        resp = solana_client.send_transaction(tx)
        logging.info(f"✅ Sent {amount_sol} SOL to {destination_wallet}, TX: {resp}")
        return resp
    except Exception as e:
        logging.error(f"❌ Failed to send SOL: {e}")
        return None

def receive_sol():
    kp = get_phantom_keypair()
    logging.info(f"💼 Phantom wallet ready to receive: {kp.pubkey()}")
    return str(kp.pubkey())

def real_buy_token(token_address, lamports=1000000):
    try:
        keypair = get_phantom_keypair()
        recipient = PublicKey.from_string(token_address)
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash()["result"]["value"]["blockhash"]
        transaction = Transaction.new_unsigned([ix])
        transaction.recent_blockhash = blockhash
        transaction.fee_payer = keypair.pubkey()
        transaction.sign([keypair])
        return solana_client.send_raw_transaction(transaction.serialize()).get("result")
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
        ix = transfer(TransferParams(from_pubkey=keypair.pubkey(), to_pubkey=recipient, lamports=lamports))
        blockhash = solana_client.get_latest_blockhash()["result"]["value"]["blockhash"]
        transaction = Transaction.new_unsigned([ix])
        transaction.recent_blockhash = blockhash
        transaction.fee_payer = keypair.pubkey()
        transaction.sign([keypair])
        return solana_client.send_raw_transaction(transaction.serialize()).get("result")
    except Exception as e:
        logging.error(f"❌ Real sell failed: {e}")
        return None

def fetch_bitquery_data(token_address):
    headers = {"X-API-KEY": BITQUERY_API_KEY}
    query = {
        "query": f"""
        query MyQuery {{
          solana(network: solana) {{
            dexTrades(
              smartContractAddress: {{is: \"{token_address}\"}}
              options: {{desc: [\"block.timestamp.time\"], limit: 5}}
            ) {{
              transaction {{ hash }}
              tradeAmount(in: USD)
              buyer {{ address }}
              quotePrice
            }}
          }}
        }}
        """
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
        prices = [float(p[1]) for p in data['data']['attributes']['series']['usd']]
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
        token_ids = [item["id"] for item in data["data"]]
        return token_ids
    except Exception as e:
        logging.warning(f"⚠️ GeckoTerminal trending token fetch failed: {e}")
        return []

def notify_discord(content, chart_img=None):
    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
    if not channel:
        logging.warning("⚠️ Could not find target Discord channel to post news/meme.")
        return
    if ROLE_MENTION_ENABLED:
        content = f"<@&{DISCORD_ROLE_ID}>\n" + content
    if chart_img:
        chart_img.seek(0)
        asyncio.create_task(channel.send(content, file=discord.File(chart_img, filename="chart.png")))
    else:
        asyncio.create_task(channel.send(content))

@tasks.loop(minutes=10)
async def post_meme_and_news():
    try:
        meme_sources = [
            "https://www.reddit.com/r/cryptomemes/new/.json",
            "https://www.reddit.com/r/wallstreetbets/new/.json"
        ]
        for source in meme_sources:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(source, headers=headers)
            posts = resp.json().get("data", {}).get("children", [])
            for post in posts[:3]:
                title = post["data"].get("title")
                image_url = post["data"].get("url_overridden_by_dest")
                if image_url and image_url.endswith((".jpg", ".png")):
                    content = f"📰 **{title}**\n{image_url}"
                    notify_discord(content)
    except Exception as e:
        logging.warning(f"⚠️ Meme/news posting failed: {e}")

@tasks.loop(seconds=30)
async def monitor_tokens():
    cas = set(get_recent_contract_mentions())
    trending = set(get_trending_gecko_tokens())
    combined = list(cas.union(trending))
    for token_address in combined:
        chart = generate_chart(token_address)
        if token_address not in bought_tokens:
            logging.info(f"💰 Sniping token: {token_address}")
            real_buy_token(token_address)
            bought_tokens[token_address] = {"bought_price": 1.0, "buyer_count": 1}
            notify_discord(f"✅ Sniped new token: `{token_address}`", chart)
        else:
            result = fetch_bitquery_data(token_address)
            if result:
                trades = result.get("data", {}).get("solana", {}).get("dexTrades", [])
                buyers = list({trade["buyer"]["address"] for trade in trades if trade.get("buyer")})
                buyer_count = len(buyers)
                current_price = float(trades[0]["quotePrice"]) if trades else 0
                initial_price = bought_tokens[token_address]["bought_price"]
                if buyer_count >= MIN_BUYERS_FOR_SELL or (current_price >= initial_price * SELL_PROFIT_TRIGGER):
                    logging.info(f"🚀 Sell condition met for {token_address}")
                    real_sell_token(token_address)
                    notify_discord(f"💸 Sold `{token_address}` after {buyer_count} buyers / profit target met.")
                    del bought_tokens[token_address]

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user.name}")
    await monitor_tokens.start()
    await post_meme_and_news.start()

bot.run(DISCORD_TOKEN)
