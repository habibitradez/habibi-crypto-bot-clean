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

# --- Add missing buy command ---
@bot.command()
async def buy(ctx, token: str):
    await ctx.send(f"Buying {token}...")
    sig = real_buy_token(token, 1000000)
    if sig:
        await ctx.send(f"✅ Bought {token}! https://solscan.io/tx/{sig}")
    else:
        await ctx.send(f"❌ Buy failed for {token}. Check logs.")

# --- Add missing sell command ---
@bot.command()
async def sell(ctx, token: str):
    await ctx.send(f"Selling {token}...")
    sig = real_sell_token(token)
    if sig:
        await ctx.send(f"✅ Sold {token}! https://solscan.io/tx/{sig}")
    else:
        await ctx.send(f"❌ Sell failed for {token}. Check logs.")

# --- Keep existing bot event ---
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
            for token in tokens:
                if token not in bought_tokens:
                    sig = real_buy_token(token, 1000000)
                    if sig:
                        price = get_token_price(token)
                        bought_tokens[token] = {
                            'buy_sig': sig,
                            'buy_time': datetime.utcnow(),
                            'token': token,
                            'initial_price': price
                        }
                        log_trade({"type": "buy", "token": token, "tx": sig, "timestamp": datetime.utcnow(), "price": price})
                else:
                    price_now = get_token_price(token)
                    token_data = bought_tokens[token]
                    if price_now and token_data['initial_price'] and price_now >= token_data['initial_price'] * SELL_PROFIT_TRIGGER:
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
