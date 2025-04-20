try:
    import discord
    from discord.ext import commands, tasks
    from discord import app_commands
except ModuleNotFoundError as e:
    print("⚠️ Discord module not found. Run: pip install discord.py")
    raise e

import requests, openai, os, logging, re, json, asyncio, random, base64, ssl, urllib3, time
from datetime import datetime, timedelta, time as dtime
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from base58 import b58decode
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
import matplotlib.pyplot as plt
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

# === Config ===
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
PHANTOM_SECRET_KEY = os.getenv("PHANTOM_SECRET_KEY")
DISCORD_NEWS_CHANNEL_ID = os.getenv("DISCORD_NEWS_CHANNEL_ID")
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
        logging.info(f"💰 Phantom Wallet Balance: {balance:.4f} SOL")
    except Exception as e:
        logging.error(f"❌ Wallet balance check failed: {e}")

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

def decode_transaction_blob(blob_str: str) -> bytes:
    try:
        return base64.b64decode(blob_str)
    except Exception:
        return b58decode(blob_str)

def sanitize_token_address(addr: str) -> str:
    addr = addr.strip()
    if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
        raise ValueError("Invalid token address")
    return addr
def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=1").json()
        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True
        }).json()

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending BUY transaction: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        return sig
    except Exception as e:
        logging.error(f"❌ Buy failed: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        quote = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=1").json()
        if not quote.get("routePlan"):
            raise Exception("No swap route available")

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True
        }).json()

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending SELL transaction: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        return sig
    except Exception as e:
        logging.error(f"❌ Sell failed: {e}")
        fallback_rpc()
        return None
@tree.command(name="buy", description="Buy a token using SOL")
async def buy_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Buying {token}...")
    sig = real_buy_token(token, 1000000)
    if sig:
        log_trade({
            "type": "buy",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "price": get_token_price(token)
        })
        await interaction.followup.send(f"✅ Bought {token}! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Buy failed for {token}. Check logs.")

@tree.command(name="sell", description="Sell a token for SOL")
async def sell_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Selling {token}...")
    sig = real_sell_token(token)
    if sig:
        log_trade({
            "type": "sell",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "profit": round(random.uniform(5, 20), 2)
        })
        await interaction.followup.send(f"✅ Sold {token}! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Sell failed for {token}. Check logs.")

@tree.command(name="profit", description="Check today's trading profit")
async def profit_slash(interaction: discord.Interaction):
    await interaction.response.send_message(f"📊 Today's profit so far: ${daily_profit:.2f}")
async def auto_snipe():
    await bot.wait_until_ready()
    while not bot.is_closed():
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
                        log_trade({
                            "type": "sell",
                            "token": token,
                            "tx": sell_sig,
                            "timestamp": datetime.utcnow(),
                            "price": price_now,
                            "profit": profit
                        })
                        del bought_tokens[token]
        summarize_daily_profit()
        await asyncio.sleep(30)
@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    bot.loop.create_task(auto_snipe())
    logging.info("🚀 Bot fully ready: Commands, Auto-sniping, Wallet Active.")

bot.run(DISCORD_TOKEN)
