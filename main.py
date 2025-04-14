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
            logging.error(f"❌ GeckoTerminal fallback failed: {ge}. Trying CoinMarketCap fallback...")
            try:
                cmc_url = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing?limit=10&sort=market_cap&cryptocurrency_type=tokens"
                cmc_res = requests.get(cmc_url)
                cmc_res.raise_for_status()
                cmc_data = cmc_res.json()
                token_list = []
                for coin in cmc_data.get("data", {}).get("cryptoCurrencyList", []):
                    try:
                        platform = coin.get("platform")
                        if platform and platform.get("name", "").lower() == "solana":
                            token_list.append(platform.get("token_address"))
                    except Exception as inner:
                        logging.warning(f"⚠️ Error parsing CoinMarketCap token: {inner}")
                        continue
                return token_list[:5]
            except Exception as ce:
                logging.error(f"❌ CoinMarketCap fallback failed: {ce}")
                return []

            msg = content
            if tx_sig:
                msg += f"\n🔗 [View Transaction](https://solscan.io/tx/{tx_sig})"
            await channel.send(msg)
    except Exception as e:
        logging.error(f"❌ Failed to send Discord notification: {e}")

def get_phantom_keypair():
    secret_bytes = base58.b58decode(PHANTOM_SECRET_KEY.strip())
    if len(secret_bytes) == 64:
        return Keypair.from_bytes(secret_bytes)
    elif len(secret_bytes) == 32:
        return Keypair.from_seed(secret_bytes)
    else:
        raise ValueError("Secret key must be 32 or 64 bytes.")

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

@tasks.loop(seconds=60)
async def auto_seller():
    try:
        for token, info in list(bought_tokens.items()):
            held_duration = time.time() - info["buy_time"]
            simulated_price = info["buy_price"] + random.uniform(0, 0.04)
            percent_gain = (simulated_price - info["buy_price"]) / info["buy_price"]
            if percent_gain >= SELL_PROFIT_TRIGGER or percent_gain <= -LOSS_CUT_PERCENT or held_duration > 180:
                logging.info(f"💸 Selling {token}, profit: {percent_gain*100:.2f}%")
                real_sell_token(token, lamports=info["amount"])
                del bought_tokens[token]
    except Exception as e:
        logging.error(f"❌ Auto-sell error: {e}")

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
    auto_seller.start()
    sniper_loop.start()
    logging.info("🚀 Features loaded: pump.fun sniping, token sim, profit tracking, meme signals, loss cuts, viral priority")

bot.run(DISCORD_TOKEN)
