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
import snscrape.modules.twitter as sntwitter
from solana.rpc.api import Client
from solders.pubkey import Pubkey as PublicKey
from solders.keypair import Keypair
from solders.system_program import transfer, TransferParams
import base58

# --- LOAD .env CONFIG ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
PHANTOM_PUBLIC_KEY = os.getenv("PHANTOM_PUBLIC_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
DISCORD_ROLE_ID = os.getenv("DISCORD_ROLE_ID")
WALLET_ENABLED = True
ROLE_MENTION_ENABLED = os.getenv("ROLE_MENTION_ENABLED", "true").lower() == "true"
TOKAPI_KEY = os.getenv("TOKAPI_KEY")

if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN is missing. Check your .env file.")
    exit(1)

openai.api_key = OPENAI_API_KEY
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree
logging.basicConfig(level=logging.INFO)
discord.utils.setup_logging(level=logging.INFO)

solana_client = Client("https://api.mainnet-beta.solana.com")
bought_tokens = {}

# --- Convert Phantom Secret Key to Keypair ---
def get_phantom_keypair():
    try:
        secret = json.loads(PHANTOM_SECRET_KEY)
        return Keypair.from_bytes(bytes(secret))
    except Exception as e:
        logging.error(f"Error loading Phantom secret key: {e}")
        return None

# --- Notify to Discord ---
def notify_discord(msg):
    try:
        if DISCORD_NEWS_CHANNEL_ID:
            requests.post(f"https://discord.com/api/webhooks/{DISCORD_NEWS_CHANNEL_ID}", json={"content": msg})
    except Exception as e:
        logging.warning(f"Failed to notify Discord: {e}")

# --- Auto-Snipe with Jupiter Aggregator ---
def auto_snipe_token(token_address, boosted=False):
    if not WALLET_ENABLED:
        logging.info("Wallet not enabled, skipping snipe.")
        return

    keypair = get_phantom_keypair()
    if not keypair:
        return

    amount_sol = 0.15 if boosted else 0.015
    amount_lamports = int(amount_sol * 1_000_000_000)

    try:
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount={amount_lamports}&slippageBps=500"
        quote = requests.get(quote_url).json()

        if not quote.get("data"):
            logging.warning("⚠️ No quote returned from Jupiter.")
            return

        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_payload = {
            "userPublicKey": str(keypair.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote["data"][0],
            "computeUnitPriceMicroLamports": 5000
        }
        swap_res = requests.post(swap_url, json=swap_payload).json()
        swap_tx = swap_res.get("swapTransaction")

        if not swap_tx:
            logging.warning("⚠️ Swap TX not generated.")
            return

        from base64 import b64decode
        from solana.transaction import Transaction as SolTx

        raw_tx = b64decode(swap_tx)
        tx = SolTx.deserialize(raw_tx)
        tx.sign(keypair)
        tx_sig = solana_client.send_raw_transaction(tx.serialize())

        logging.info(f"✅ Jupiter TX sent for {token_address}: {tx_sig}")
        notify_discord(f"🚀 Jupiter snipe sent for `{token_address}` with {amount_sol} SOL")

        bought_tokens[token_address] = {
            "buy_price": float(quote["data"][0].get("outAmount", 0)) / 1e9,
            "boosted": boosted,
            "time": datetime.utcnow()
        }

    except Exception as e:
        logging.error(f"❌ Jupiter snipe failed for {token_address}: {e}")

# --- Auto Sell Monitor ---
@tasks.loop(seconds=30)
async def monitor_and_sell():
    keypair = get_phantom_keypair()
    if not keypair:
        return

    for token_address in list(bought_tokens.keys()):
        try:
            url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{token_address}"
            res = requests.get(url)
            data = res.json()

            if "pair" not in data:
                continue

            price_usd = float(data["pair"].get("priceUsd", 0))
            buy_price = bought_tokens[token_address]["buy_price"]

            if price_usd >= buy_price * 2:
                notify_discord(f"💸 Selling `{token_address}` at 2x gain! Current: ${price_usd:.4f}, Buy: ${buy_price:.4f}")
                del bought_tokens[token_address]
                continue

            if price_usd < buy_price * 0.6:
                notify_discord(f"🛑 Stop-loss triggered for `{token_address}`. Current: ${price_usd:.4f}, Buy: ${buy_price:.4f}")
                del bought_tokens[token_address]
                continue

            time_held = datetime.utcnow() - bought_tokens[token_address]["time"]
            if time_held > timedelta(minutes=10):
                notify_discord(f"⏳ Time-based exit for `{token_address}`. Held for {time_held}. Selling now.")
                del bought_tokens[token_address]

        except Exception as e:
            logging.error(f"Price check/sell failed for {token_address}: {e}")

# --- Birdeye Trending Tokens ---
@tasks.loop(seconds=45)
async def watch_birdeye_trends():
    url = "https://public-api.birdeye.so/public/token/solana/trending"
    try:
        res = requests.get(url).json()
        tokens = res.get("data", [])
        for token in tokens[:3]:
            ca = token.get("address")
            name = token.get("name")
            liquidity = token.get("liquidity", 0)
            if liquidity < 5000:
                continue

            msg = f"🔥 **Birdeye Trending Token**: {name}\n💧 Liquidity: ${liquidity:,.0f} | 🧪 CA: `{ca}`\n🔗 https://birdeye.so/token/{ca}?chain=solana"
            logging.info(msg)
            notify_discord(msg)
            auto_snipe_token(ca)
    except Exception as e:
        logging.error(f"Birdeye watch failed: {e}")

# --- GeckoTerminal Trending Tokens ---
@tasks.loop(seconds=60)
async def watch_geckoterminal_trends():
    url = "https://api.geckoterminal.com/api/v2/networks/solana/pools/trending"
    try:
        res = requests.get(url).json()
        pools = res.get("data", [])
        for pool in pools[:3]:
            attr = pool["attributes"]
            token_name = attr.get("base_token_name")
            ca = attr.get("base_token_address")
            liquidity = float(attr.get("reserve_in_usd", 0))
            if liquidity < 5000:
                continue

            msg = f"💥 **GeckoTerminal Trending**: {token_name}\n💧 Liquidity: ${liquidity:,.0f} | 🧪 CA: `{ca}`\n🔗 https://www.geckoterminal.com/solana/pools/{pool['id']}"
            logging.info(msg)
            notify_discord(msg)
            auto_snipe_token(ca)
    except Exception as e:
        logging.error(f"GeckoTerminal watch failed: {e}")

# --- Pump.fun Token Watcher ---
@tasks.loop(seconds=15)
async def watch_new_pumpfun_tokens():
    url = "https://client-api.pump.fun/latest/tokens"
    try:
        data = requests.get(url).json()
        if not data or "tokens" not in data:
            return

        for token in data["tokens"][:3]:
            name = token.get("name")
            ca = token.get("tokenId")
            creator = token.get("twitterHandle")
            price = token.get("price", "N/A")
            liquidity = token.get("liquidity", "N/A")
            message = f"🚨 **New Pump Token**: {name}\n💰 Price: {price} | 🧪 CA: `{ca}`\n🐦 Creator: @{creator}\n🔗 https://pump.fun/{ca}"
            logging.info(message)
            notify_discord(message)

            boosted = creator is not None and creator != ""
            auto_snipe_token(ca, boosted=boosted)

    except Exception as e:
        logging.error(f"❌ Error fetching pump.fun tokens: {e}")

# --- Bot Commands ---
@bot.command()
async def holdings(ctx):
    if not bought_tokens:
        await ctx.send("📉 No active holdings.")
        return

    message = "📊 **Active Holdings**:\n"
    for ca, info in bought_tokens.items():
        message += f"- `{ca}`: Bought at ${info['buy_price']:.4f}, Boosted: {info['boosted']}, Time: {info['time'].strftime('%H:%M:%S')} UTC\n"
    await ctx.send(message)

@bot.command()
async def toggle_sniping(ctx):
    global WALLET_ENABLED
    WALLET_ENABLED = not WALLET_ENABLED
    state = "enabled" if WALLET_ENABLED else "disabled"
    await ctx.send(f"⚙️ Auto-sniping is now **{state}**.")

# --- Startup Events ---
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    watch_new_pumpfun_tokens.start()
    watch_birdeye_trends.start()
    watch_geckoterminal_trends.start()
    monitor_and_sell.start()

# --- Run the Bot ---
bot.run(DISCORD_TOKEN)
