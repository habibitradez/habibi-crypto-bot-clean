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
        logging.error(f"❌ Bitquery failed: {e}. Trying GeckoTerminal fallback...")
        try:
            url = "https://api.geckoterminal.com/api/v2/networks/solana/pools/trending"
            res = requests.get(url)
            res.raise_for_status()
            gecko_data = res.json()
            token_list = []
            for pool in gecko_data.get("data", []):
                try:
                    token_addr = pool["attributes"].get("token_address")
                    if token_addr:
                        token_list.append(token_addr)
                except Exception as inner:
                    logging.warning(f"⚠️ Error parsing GeckoTerminal pool: {inner}")
                    continue
            if token_list:
                return token_list[:5]
        except Exception as ge:
            logging.error(f"❌ GeckoTerminal fallback failed: {ge}. Trying CoinBrain fallback...")
            try:
                cb_url = "https://public-api.coinbrain.com/coins/solana/trending"
                cb_res = requests.get(cb_url)
                cb_res.raise_for_status()
                cb_data = cb_res.json()
                token_list = []
                for coin in cb_data.get("data", []):
                    token_addr = coin.get("tokenAddress")
                    if token_addr:
                        token_list.append(token_addr)
                if token_list:
                    return token_list[:5]
            except Exception as ce:
                logging.error(f"❌ CoinBrain fallback failed: {ce}. Trying CoinMarketCap fallback...")
                try:
                    cmc_url = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing?limit=10&sort=market_cap&cryptocurrency_type=tokens"
                    cmc_res = requests.get(cmc_url)
                    cmc_res.raise_for_status()
                    cmc_data = cmc_res.json()
                    token_list = []
                    for coin in cmc_data.get("data", {}).get("cryptoCurrencyList", []):
                        platform = coin.get("platform")
                        if platform and platform.get("name", "").lower() == "solana":
                            token_list.append(platform.get("token_address"))
                    if token_list:
                        return token_list[:5]
                except Exception as ce:
                    logging.error(f"❌ CoinMarketCap fallback failed: {ce}. Trying Dexscreener fallback...")
                    try:
                        dex_url = "https://api.dexscreener.com/latest/dex/pairs/solana"
                        dex_res = requests.get(dex_url)
                        dex_res.raise_for_status()
                        dex_data = dex_res.json()
                        token_list = []
                        for pair in dex_data.get("pairs", []):
                            base_token = pair.get("baseToken", {})
                            token_addr = base_token.get("address")
                            if token_addr:
                                token_list.append(token_addr)
                        return token_list[:5]
                    except Exception as dex:
                        logging.error(f"❌ Dexscreener fallback failed: {dex}")
                        return []
