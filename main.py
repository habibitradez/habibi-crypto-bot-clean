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

try:
    import discord
    from discord.ext import commands, tasks
    from discord import app_commands
except ModuleNotFoundError as e:
    print("⚠️ Discord module not found. Run: pip install discord.py")
    raise e

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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
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
SELL_PROFIT_TRIGGER = 1.3  # Lowered to 1.3x for faster profits
STOP_LOSS_TRIGGER = 0.85   # Raised to 0.85x for tighter stop loss
MAX_TOKENS_TO_HOLD = 10    # Increased to hold more tokens simultaneously
BUY_AMOUNT_LAMPORTS = 200000000  # 0.2 SOL per trade as requested

def get_token_price(token_address):
    try:
        r = requests.get(f"https://public-api.birdeye.so/public/price?address={token_address}", timeout=5)
        if r.status_code != 200:
            logging.warning(f"❌ Price API returned status code: {r.status_code}")
            return 0
            
        price_data = r.json()
        return price_data.get('data', {}).get('value', 0)
    except Exception as e:
        logging.error(f"❌ Price fetch failed for {token_address}: {e}")
        return 0

def log_trade(trade_data):
    global trade_log, daily_profit
    trade_log.append(trade_data)
    
    # Update daily profit if it's a sell transaction with profit data
    if trade_data.get("type") == "sell" and "profit" in trade_data:
        daily_profit += trade_data["profit"]
    
    # Log the trade details
    trade_type = trade_data.get("type", "unknown")
    token = trade_data.get("token", "unknown")
    timestamp = trade_data.get("timestamp", "unknown")
    
    if trade_type == "buy":
        price = trade_data.get("price", 0)
        logging.info(f"💰 BUY: {token} at ${price:.6f} - {timestamp}")
    elif trade_type == "sell":
        profit = trade_data.get("profit", 0)
        logging.info(f"💵 SELL: {token} with profit ${profit:.2f} - {timestamp}")
    
    # Save trade log to file
    with open("trade_log.json", "w") as f:
        json.dump(trade_log, f, default=str)

def summarize_daily_profit():
    global trade_log
    total = sum(entry.get("profit", 0) for entry in trade_log if entry.get("type") == "sell")
    logging.info(f"📊 Estimated Daily Profit So Far: ${total:.2f}")
    return total

def fetch_birdeye():
    try:
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=v24hUSD&sort_type=desc&offset=0&limit=20", timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Birdeye API returned status code: {r.status_code}")
            return []
            
        data = r.json()
        if 'data' not in data:
            logging.warning("❌ Birdeye API response missing 'data' field")
            return []
            
        tokens = [token['address'] for token in data.get('data', []) if 'address' in token]
        logging.info(f"✅ Fetched {len(tokens)} tokens from Birdeye")
        return tokens
    except Exception as e:
        logging.error(f"❌ Birdeye fetch failed: {e}")
        return []

def fetch_new_tokens():
    tokens = []
    
    # Focus more on most recent tokens
    # Try Birdeye's recent tokens endpoint (increased limit from 10 to 30)
    try:
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=created_at&sort_type=desc&offset=0&limit=30", 
                        timeout=10)
        if r.status_code == 200:
            data = r.json()
            birdeye_count = 0
            if 'data' in data:
                for token in data['data']:
                    if 'address' in token and token['address'] not in tokens:
                        tokens.append(token['address'])
                        birdeye_count += 1
            logging.info(f"✅ Added {birdeye_count} tokens from Birdeye recent")
    except Exception as e:
        logging.error(f"❌ Birdeye recent token fetch failed: {str(e)}")
    
    try:
        # Also try a special endpoint for hot new tokens/DeFi launches
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=v24hPercent&sort_type=desc&offset=0&limit=20", 
                         timeout=10)
        if r.status_code == 200:
            data = r.json()
            hot_tokens_count = 0
            if 'data' in data:
                for token in data['data']:
                    if 'address' in token and token['address'] not in tokens:
                        tokens.append(token['address'])
                        hot_tokens_count += 1
            logging.info(f"✅ Added {hot_tokens_count} tokens from Birdeye hot tokens")
    except Exception as e:
        logging.error(f"❌ Birdeye hot tokens fetch failed: {str(e)}")
    
    try:
        # Try Solana FM API
        headers = {"accept": "application/json"}
        r = requests.get("https://api.solscan.io/v2/token/list?sortBy=marketCapRank&direction=desc&limit=15", 
                         headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if 'data' in data and 'list' in data['data']:
                for token in data['data']['list']:
                    if 'mintAddress' in token and token['mintAddress'] not in tokens:
                        tokens.append(token['mintAddress'])
                logging.info(f"✅ Fetched {len(tokens) - hot_tokens_count - birdeye_count} tokens from Solscan")
        else:
            logging.warning(f"❌ Solscan API returned status code: {r.status_code}")
    except Exception as e:
        logging.error(f"❌ Solscan token fetch failed: {str(e)}")
    
    try:
        # Also try Jupiter API for tokens
        r = requests.get("https://token.jup.ag/all", timeout=10)
        if r.status_code == 200:
            jupiter_tokens = r.json()
            # Get recent tokens from Jupiter
            jupiter_count = 0
            for token in jupiter_tokens[:30]:  # Limit to first 30
                if 'address' in token and token['address'] not in tokens:
                    tokens.append(token['address'])
                    jupiter_count += 1
            logging.info(f"✅ Added {jupiter_count} tokens from Jupiter")
    except Exception as e:
        logging.error(f"❌ Jupiter token fetch failed: {str(e)}")
        
    # Shuffle the tokens list to add randomness - this helps avoid competing with other bots
    random.shuffle(tokens)
    
    logging.info(f"✅ Total: Found {len(tokens)} tokens from all APIs")
    return tokens

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
        return balance
    except Exception as e:
        logging.error(f"❌ Wallet balance check failed: {e}")
        return 0

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

def is_valid_token(token_address):
    try:
        # Check if token has liquidity
        r = requests.get(f"https://public-api.birdeye.so/public/token/{token_address}?cluster=solana", timeout=5)
        if r.status_code != 200:
            logging.info(f"🔍 Token {token_address} API returned status {r.status_code}")
            return False
            
        token_data = r.json().get('data', {})
        
        # Basic validation checks
        if not token_data:
            logging.info(f"🔍 Token {token_address} has no data")
            return False
            
        # Check if there's liquidity - lowered threshold for new launches
        liquidity = token_data.get('liquidity', 0)
        if not liquidity or float(liquidity) < 100:  # Lower threshold to $100 in liquidity
            logging.info(f"🔍 Token {token_address} has very low liquidity: ${liquidity}, but continuing for potential early snipe")
            
            # For very new tokens, we'll still allow trading if they have any liquidity at all
            if float(liquidity) > 0:
                logging.info(f"🚀 Potential new launch detected! Token {token_address} has minimal liquidity: ${float(liquidity):.2f}")
                return True
            return False
            
        # For new tokens, we'll be more lenient with volume requirements
        volume = token_data.get('volume', {}).get('h24', 0)
        creation_time = token_data.get('createdAt', 0)
        current_time = time.time() * 1000  # Current time in milliseconds
        
        # If token was created in the last hour, ignore volume requirement
        is_new_token = False
        if creation_time and (current_time - creation_time) < 3600000:  # 1 hour in milliseconds
            is_new_token = True
            logging.info(f"🚀 New token detected! {token_address} was created less than 1 hour ago")
            
        # Only check volume for tokens that aren't brand new
        if not is_new_token and (not volume or float(volume) <= 0):
            logging.info(f"🔍 Token {token_address} has no recent trading volume")
            return False
            
        logging.info(f"✅ Token {token_address} passed validation (liquidity: ${float(liquidity):.2f})")
        return True
    except Exception as e:
        logging.error(f"❌ Token validation failed for {token_address}: {e}")
        return False

def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # First check if the token is valid
        if not is_valid_token(to_addr):
            logging.warning(f"❌ Token {to_addr} failed validation checks. Skipping buy.")
            return None
            
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=1"
        logging.info(f"🔍 Getting buy quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No swap route available for {to_addr}")
            return None

        # Check price impact
        price_impact = quote.get('priceImpactPct', 0) * 100
        if price_impact > 5:  # If price impact is greater than 5%
            logging.warning(f"❌ Price impact too high ({price_impact:.2f}%) for {to_addr}")
            return None

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True
        }, timeout=10).json()

        if "swapTransaction" not in swap:
            logging.error(f"❌ No swap transaction returned for {to_addr}")
            return None

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending BUY transaction for {to_addr}: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        
        # Wait for confirmation
        for _ in range(10):  # Try 10 times
            try:
                conf = solana_client.confirm_transaction(sig.value)
                if conf.value:
                    logging.info(f"✅ Buy transaction confirmed for {to_addr}")
                    return sig.value
                time.sleep(1)
            except Exception:
                time.sleep(1)
                continue
                
        return sig.value
    except Exception as e:
        logging.error(f"❌ Buy failed for {to_addr}: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # Get token balance to sell all
        token_accounts = solana_client.get_token_accounts_by_owner(
            kp.pubkey(),
            {"mint": Pubkey.from_string(to_addr)}
        ).value
        
        if not token_accounts:
            logging.warning(f"❌ No token account found for {to_addr}")
            return None
            
        token_balance = int(token_accounts[0].account.data.parsed['info']['tokenAmount']['amount'])
        if token_balance <= 0:
            logging.warning(f"❌ Zero balance for {to_addr}")
            return None
            
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=1"
        logging.info(f"🔍 Getting sell quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No sell route available for {to_addr}")
            return None

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 0,
            "asLegacyTransaction": True
        }, timeout=10).json()

        if "swapTransaction" not in swap:
            logging.error(f"❌ No swap transaction returned for selling {to_addr}")
            return None

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending SELL transaction for {to_addr}: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
        
        # Wait for confirmation
        for _ in range(10):  # Try 10 times
            try:
                conf = solana_client.confirm_transaction(sig.value)
                if conf.value:
                    logging.info(f"✅ Sell transaction confirmed for {to_addr}")
                    return sig.value
                time.sleep(1)
            except Exception:
                time.sleep(1)
                continue
                
        return sig.value
    except Exception as e:
        logging.error(f"❌ Sell failed for {to_addr}: {e}")
        fallback_rpc()
        return None

@tree.command(name="buy", description="Buy a token using SOL")
async def buy_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Buying {token}...")
    sig = real_buy_token(token, BUY_AMOUNT_LAMPORTS)
    if sig:
        price = get_token_price(token)
        log_trade({
            "type": "buy",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "price": price
        })
        await interaction.followup.send(f"✅ Bought {token} at ${price:.6f}! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Buy failed for {token}. Check logs.")

@tree.command(name="sell", description="Sell a token for SOL")
async def sell_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Selling {token}...")
    initial_price = 0
    if token in bought_tokens:
        initial_price = bought_tokens[token]['initial_price']
    
    sig = real_sell_token(token)
    if sig:
        current_price = get_token_price(token)
        profit = 0
        if initial_price > 0 and current_price > 0:
            profit = ((current_price - initial_price) / initial_price) * BUY_AMOUNT_LAMPORTS / 1_000_000_000
        
        log_trade({
            "type": "sell",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "price": current_price,
            "profit": profit
        })
        
        if token in bought_tokens:
            del bought_tokens[token]
            
        await interaction.followup.send(f"✅ Sold {token} at ${current_price:.6f} (Profit: ${profit:.2f})! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Sell failed for {token}. Check logs.")

@tree.command(name="profit", description="Check today's trading profit")
async def profit_slash(interaction: discord.Interaction):
    total_profit = summarize_daily_profit()
    await interaction.response.send_message(f"📊 Today's profit so far: ${total_profit:.2f}")

@tree.command(name="balance", description="Check wallet balance")
async def balance_slash(interaction: discord.Interaction):
    balance = log_wallet_balance()
    await interaction.response.send_message(f"💰 Current wallet balance: {balance:.4f} SOL")

@tree.command(name="holdings", description="Check current token holdings")
async def holdings_slash(interaction: discord.Interaction):
    if not bought_tokens:
        await interaction.response.send_message("No tokens currently held.")
        return
        
    holdings_text = "Current Holdings:\n"
    for token, data in bought_tokens.items():
        current_price = get_token_price(token)
        initial_price = data['initial_price']
        profit_percent = ((current_price - initial_price) / initial_price * 100) if initial_price > 0 else 0
        buy_time = data['buy_time'].strftime("%H:%M:%S")
        
        holdings_text += f"- {token}: Bought at ${initial_price:.6f}, Now ${current_price:.6f} ({profit_percent:.2f}%) - Bought at {buy_time}\n"
        
    await interaction.response.send_message(holdings_text)

async def auto_snipe():
    await bot.wait_until_ready()
    logging.info("🔍 Auto-snipe task started")
    
    while not bot.is_closed():
        try:
            # Skip if we're already holding max tokens
            if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                logging.info(f"🛑 Already holding maximum of {MAX_TOKENS_TO_HOLD} tokens. Checking existing positions...")
                
                # Just check existing holdings
                for token, token_data in list(bought_tokens.items()):
                    await check_and_sell_token(token, token_data)
                
                await asyncio.sleep(10)  # Reduced sleep time for more frequent checking
                continue
            
            # Get both high volume and new tokens - prioritize new tokens
            logging.info("🔍 Fetching tokens from APIs...")
            new_tokens = fetch_new_tokens()  # Get new tokens first
            volume_tokens = fetch_birdeye()
            
            # Combine and prioritize new tokens (avoiding duplicates)
            all_tokens = new_tokens + [t for t in volume_tokens if t not in new_tokens]
            target_tokens = []
            
            # Filter out tokens we already hold
            for token in all_tokens:
                if token not in bought_tokens and token not in target_tokens:
                    target_tokens.append(token)
            
            logging.info(f"🔍 Found {len(target_tokens)} potential tokens to snipe")
            
            # Try to buy new tokens - increased from 3 to 5 per cycle
            for token in target_tokens[:5]:  
                if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                    break
                    
                logging.info(f"💰 Attempting to buy token: {token}")
                # Buy the token
                sig = real_buy_token(token, BUY_AMOUNT_LAMPORTS)
                if sig:
                    price = get_token_price(token)
                    bought_tokens[token] = {
                        'buy_sig': sig,
                        'buy_time': datetime.utcnow(),
                        'token': token,
                        'initial_price': price
                    }
                    log_trade({
                        "type": "buy", 
                        "token": token, 
                        "tx": sig, 
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
                        "price": price
                    })
                    
                    # Notify in Discord
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🚀 Auto-bought {token} at ${price:.6f}! https://solscan.io/tx/{sig}")
                
                # Add a short delay between buy attempts to avoid rate limits
                await asyncio.sleep(2)
            
            # Check existing positions for selling
            for token, token_data in list(bought_tokens.items()):
                await check_and_sell_token(token, token_data)
            
            # Summarize current status
            summarize_daily_profit()
            log_wallet_balance()
            
        except Exception as e:
            logging.error(f"❌ Error in auto_snipe: {e}")
            
        await asyncio.sleep(10)  # Reduced from 30 to 10 seconds for more aggressive trading

async def check_and_sell_token(token, token_data):
    try:
        price_now = get_token_price(token)
        initial_price = token_data['initial_price']
        
        if price_now <= 0 or initial_price <= 0:
            return
            
        price_ratio = price_now / initial_price
        minutes_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 60
        
        # Sell conditions:
        # 1. Hit profit target - using faster target
        # 2. Hit stop loss - tighter stop to avoid bigger losses
        # 3. Been holding more than 30 minutes - much faster cycling
        should_sell = False
        sell_reason = ""
        
        if price_ratio >= SELL_PROFIT_TRIGGER:
            should_sell = True
            sell_reason = f"profit target reached ({price_ratio:.2f}x)"
        elif price_ratio <= STOP_LOSS_TRIGGER:
            should_sell = True
            sell_reason = f"stop loss triggered ({price_ratio:.2f}x)"
        elif minutes_since_buy >= 30:
            # If token has been held for 30 minutes, check if it's profitable at all
            if price_ratio > 1.05:  # 5% profit or more
                should_sell = True
                sell_reason = f"profit taking after {minutes_since_buy:.1f} minutes ({price_ratio:.2f}x)"
            elif minutes_since_buy >= 60:  # Force sell after 1 hour regardless
                should_sell = True
                sell_reason = f"held for {minutes_since_buy:.1f} minutes"
            
        if should_sell:
            logging.info(f"🔄 Selling {token} - {sell_reason}")
            sell_sig = real_sell_token(token)
            
            if sell_sig:
                profit = ((price_now - initial_price) / initial_price) * BUY_AMOUNT_LAMPORTS / 1_000_000_000
                
                log_trade({
                    "type": "sell", 
                    "token": token,
                    "tx": sell_sig,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                    "price": price_now,
                    "profit": profit,
                    "reason": sell_reason
                })
                
                # Notify in Discord
                if DISCORD_NEWS_CHANNEL_ID:
                    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                    if channel:
                        await channel.send(f"💰 Auto-sold {token} at ${price_now:.6f} ({price_ratio:.2f}x, ${profit:.2f} profit) - {sell_reason}! https://solscan.io/tx/{sell_sig}")
                
                del bought_tokens[token]
                
                # If this was a very profitable trade (over 50% gain), log it specially
                if price_ratio >= 1.5:
                    logging.info(f"💎 HIGHLY PROFITABLE TRADE: {token} at {price_ratio:.2f}x return!")
    except Exception as e:
        logging.error(f"❌ Error checking token {token}: {e}")

@tree.command(name="debug", description="Debug token fetching")
async def debug_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Running token fetch debug...")
    try:
        # Test fetch_birdeye
        birdeye_start = time.time()
        birdeye_tokens = fetch_birdeye()
        birdeye_time = time.time() - birdeye_start
        
        # Test fetch_new_tokens
        new_start = time.time()
        new_tokens = fetch_new_tokens()
        new_time = time.time() - new_start
        
        debug_info = f"""Debug Results:
        
Birdeye API: {len(birdeye_tokens)} tokens in {birdeye_time:.2f}s
New Tokens APIs: {len(new_tokens)} tokens in {new_time:.2f}s

Total Unique Tokens: {len(set(birdeye_tokens + new_tokens))}

Sample Birdeye tokens:
{', '.join(birdeye_tokens[:3]) if birdeye_tokens else 'None'}

Sample New tokens:
{', '.join(new_tokens[:3]) if new_tokens else 'None'}
"""
        await interaction.followup.send(debug_info)
    except Exception as e:
        await interaction.followup.send(f"Debug failed: {e}")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    bot.loop.create_task(auto_snipe())
    logging.info("🚀 Bot fully ready: Commands, Auto-sniping, Wallet Active.")

def generate_profit_chart():
    try:
        # Extract profit data from trade log
        timestamps = []
        profits = []
        cumulative_profit = 0
        
        for trade in trade_log:
            if trade.get("type") == "sell" and "profit" in trade:
                timestamps.append(datetime.strptime(trade.get("timestamp"), "%H:%M:%S"))
                cumulative_profit += trade.get("profit", 0)
                profits.append(cumulative_profit)
        
        if not timestamps:
            return
            
        # Create the chart
        plt.figure(figsize=(10, 6))
        plt.plot(timestamps, profits, marker='o', linestyle='-', color='green')
        plt.title('Cumulative Trading Profit')
        plt.xlabel('Time')
        plt.ylabel('Profit (USD)')
        plt.grid(True)
        plt.savefig('profit_chart.png')
        logging.info("📊 Generated profit chart")
    except Exception as e:
        logging.error(f"❌ Error generating profit chart: {e}")

@tree.command(name="chart", description="Generate a profit chart")
async def chart_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Generating profit chart...")
    try:
        generate_profit_chart()
        await interaction.followup.send(file=discord.File('profit_chart.png'))
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to generate chart: {e}")

# Load existing trade log if available
try:
    with open("trade_log.json", "r") as f:
        trade_log = json.load(f)
    logging.info(f"✅ Loaded {len(trade_log)} previous trades")
except FileNotFoundError:
    logging.info("No previous trade log found. Starting fresh.")

# Start the bot
if __name__ == "__main__":
    logging.info("🚀 Starting Solana trading bot...")
    bot.run(DISCORD_TOKEN)
