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
SELL_PROFIT_TRIGGER = 2.0  # Strict 2.0x minimum profit target 
STOP_LOSS_TRIGGER = 0.85   # Kept at 0.85x for tighter stop loss
MAX_TOKENS_TO_HOLD = 30    # Increased from 20 to 30 for more concurrent positions
BUY_AMOUNT_LAMPORTS = 150000000  # Keep at 0.15 SOL per trade
DAILY_PROFIT_TARGET = 1000  # $1000 daily profit target
MAX_BUYS_PER_CYCLE = 15    # Increased from 10 to 15 buys per cycle
FORCE_SELL_MINUTES = 180   # Increased from 120 to 180 minutes for more patience 
MAX_TRANSACTION_ATTEMPTS = 3  # Number of attempts for each transaction
PROFIT_CHECK_INTERVAL = 3  # Check profits every 3 minutes

# Track performance stats
total_buys_today = 0
successful_sells_today = 0
successful_2x_sells = 0

def get_token_price(token_address):
    """
    Enhanced token price function that tries multiple sources
    """
    # Try Birdeye price API first
    price_value = try_birdeye_price(token_address)
    if price_value > 0:
        return price_value
        
    # Try Jupiter price API as fallback
    price_value = try_jupiter_price(token_address)
    if price_value > 0:
        return price_value
        
    # Try Raydium price API as another fallback
    price_value = try_raydium_price(token_address)
    if price_value > 0:
        return price_value
        
    # If all else fails, check for liquidity directly through Solana RPC
    price_value = estimate_price_from_liquidity(token_address)
    return price_value

def try_birdeye_price(token_address):
    """Try to get price from Birdeye API"""
    try:
        r = requests.get(f"https://public-api.birdeye.so/public/price?address={token_address}", timeout=5)
        if r.status_code != 200:
            logging.info(f"🔍 Birdeye Price API returned status code: {r.status_code} for {token_address}")
            return 0
            
        price_data = r.json()
        price_value = price_data.get('data', {}).get('value', 0)
        
        # Ensure we're returning a float
        if isinstance(price_value, str):
            try:
                price_value = float(price_value)
            except:
                price_value = 0
                
        if price_value > 0:
            logging.info(f"✅ Got price ${price_value:.8f} for {token_address} from Birdeye")
                
        return price_value
    except Exception as e:
        logging.info(f"🔍 Birdeye price fetch failed for {token_address}: {e}")
        return 0

def try_jupiter_price(token_address):
    """Try to get price from Jupiter API"""
    try:
        # Jupiter price API uses a different endpoint
        r = requests.get(f"https://price.jup.ag/v4/price?ids={token_address}", timeout=5)
        if r.status_code != 200:
            logging.info(f"🔍 Jupiter Price API returned status code: {r.status_code} for {token_address}")
            return 0
            
        price_data = r.json()
        data = price_data.get('data', {})
        token_data = data.get(token_address, {})
        price_value = token_data.get('price', 0)
        
        # Ensure we're returning a float
        if isinstance(price_value, str):
            try:
                price_value = float(price_value)
            except:
                price_value = 0
                
        if price_value > 0:
            logging.info(f"✅ Got price ${price_value:.8f} for {token_address} from Jupiter")
                
        return price_value
    except Exception as e:
        logging.info(f"🔍 Jupiter price fetch failed for {token_address}: {e}")
        return 0
        
def try_raydium_price(token_address):
    """Try to get price from Raydium API"""
    try:
        # Simplified approach for Raydium price estimate
        r = requests.get(f"https://api.raydium.io/v2/sdk/liquidity/mainnet.json", timeout=5)
        if r.status_code != 200:
            logging.info(f"🔍 Raydium API returned status code: {r.status_code}")
            return 0
            
        pools_data = r.json()
        pools = pools_data.get('official', []) + pools_data.get('unOfficial', [])
        
        # Find pools that contain our token
        for pool in pools:
            if pool.get('baseMint') == token_address or pool.get('quoteMint') == token_address:
                # Rough price estimation - would need refinement in production
                if 'price' in pool:
                    price_value = float(pool['price'])
                    logging.info(f"✅ Got price ${price_value:.8f} for {token_address} from Raydium")
                    return price_value
        
        return 0
    except Exception as e:
        logging.info(f"🔍 Raydium price fetch failed for {token_address}: {e}")
        return 0
        
def estimate_price_from_liquidity(token_address):
    """
    Last resort: Estimate price based on liquidity pools directly
    This is a simplified implementation - would need enhancement for production
    """
    try:
        # Try to find USDC/SOL pairing
        usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC mint
        sol_mint = "So11111111111111111111111111111111111111112"    # SOL mint
        
        # For tokens with no price data yet but have been bought recently
        # we can assume a small initial price for tracking
        if token_address in bought_tokens:
            time_since_buy = (datetime.utcnow() - bought_tokens[token_address]['buy_time']).total_seconds()
            if time_since_buy < 1800:  # Less than 30 minutes since buy
                logging.info(f"⚠️ No price data yet for recent token {token_address}, using placeholder")
                return 0.00000001  # Very small placeholder price
        
        return 0
    except Exception as e:
        logging.info(f"🔍 Liquidity price estimate failed for {token_address}: {e}")
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
        amount = trade_data.get("amount_lamports", BUY_AMOUNT_LAMPORTS) / 1_000_000_000
        logging.info(f"💰 BUY: {token} at ${price:.6f} with {amount:.3f} SOL - {timestamp}")
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
    
    # Special mechanism to track recently launched tokens from several sources
    try:
        # 1. Get tokens launched in the past hour from Birdeye
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=created_at&sort_type=desc&offset=0&limit=50", 
                        timeout=10)
        if r.status_code == 200:
            data = r.json()
            birdeye_count = 0
            if 'data' in data:
                current_time = time.time() * 1000  # Current time in milliseconds
                for token in data['data']:
                    # Focus on tokens created in the last hour
                    if 'address' in token and 'createdAt' in token:
                        creation_time = token.get('createdAt', 0)
                        if (current_time - creation_time) < 3600000:  # 1 hour in milliseconds
                            tokens.append(token['address'])
                            birdeye_count += 1
                            logging.info(f"🔥 Found BRAND NEW token (created within last hour): {token['address']}")
            logging.info(f"✅ Added {birdeye_count} ULTRA-NEW tokens from Birdeye recent")
    except Exception as e:
        logging.error(f"❌ Birdeye ultra-new token fetch failed: {str(e)}")
    
    # Focus more on most recent tokens
    # Try Birdeye's recent tokens endpoint (increased limit from 10 to 30)
    if len(tokens) < 10:  # Only if we don't have enough ultra-new tokens
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
    
    # Try using tokens with high 24h percent change - these are often new launches with momentum
    try:
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=v24hPercent&sort_type=desc&offset=0&limit=20", 
                         timeout=10)
        if r.status_code == 200:
            data = r.json()
            hot_tokens_count = 0
            if 'data' in data:
                for token in data['data']:
                    if 'address' in token and token['address'] not in tokens:
                        percent_change = token.get('v24hPercent', 0)
                        if isinstance(percent_change, str):
                            try:
                                percent_change = float(percent_change)
                            except:
                                percent_change = 0
                                
                        if float(percent_change) > 10:  # Only add tokens with >10% 24h change
                            tokens.append(token['address'])
                            hot_tokens_count += 1
                            if float(percent_change) > 50:  # Log tokens with massive gains
                                logging.info(f"🚀 Found HOT token with {percent_change}% 24h change: {token['address']}")
            logging.info(f"✅ Added {hot_tokens_count} tokens from Birdeye hot tokens")
    except Exception as e:
        logging.error(f"❌ Birdeye hot tokens fetch failed: {str(e)}")
    
    # Try using tokens with rapidly increasing holders - often indicates new community projects
    try:
        # Get tokens with rapidly growing holder counts
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=holders&sort_type=desc&offset=0&limit=20", 
                         timeout=10)
        if r.status_code == 200:
            data = r.json()
            holder_tokens_count = 0
            if 'data' in data:
                for token in data['data']:
                    if 'address' in token and token['address'] not in tokens:
                        tokens.append(token['address'])
                        holder_tokens_count += 1
            logging.info(f"✅ Added {holder_tokens_count} popular tokens by holder count")
    except Exception as e:
        logging.error(f"❌ Holder popularity token fetch failed: {str(e)}")
    
    # Add other API sources as backup
    if len(tokens) < 20:  # Only if we need more tokens
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
                    logging.info(f"✅ Added backup tokens from Solscan")
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
            # Even if API fails, let's try to buy anyway - could be a very new token
            logging.info(f"🚀 API returned non-200 status but attempting to buy {token_address} anyway - could be very new")
            return True
            
        token_data = r.json().get('data', {})
        
        # Super permissive validation - almost any token with data is valid
        if not token_data:
            # Even with no data, we'll still try to buy - it might be so new the APIs haven't indexed it
            logging.info(f"🚀 Token {token_address} has no data but attempting to buy anyway - could be ultra-new")
            return True
            
        # We'll allow any token with even minimal liquidity or any volume
        liquidity = token_data.get('liquidity', 0)
        volume = token_data.get('volume', {}).get('h24', 0)
        creation_time = token_data.get('createdAt', 0)
        current_time = time.time() * 1000  # Current time in milliseconds
        
        # Make sure we convert string values to float for comparison
        if isinstance(liquidity, str):
            try:
                liquidity = float(liquidity)
            except:
                liquidity = 0
                
        if isinstance(volume, str):
            try:
                volume = float(volume)
            except:
                volume = 0
        
        # Auto-approve if token was created very recently (within 1 hour)
        if creation_time and (current_time - creation_time) < 3600000:  # 1 hour in milliseconds
            logging.info(f"🚀 New token detected! {token_address} was created less than 1 hour ago - buying without further checks")
            return True
        
        # If it has any liquidity or volume at all, approve it
        if float(liquidity) > 0 or float(volume) > 0:
            logging.info(f"✅ Token {token_address} approved (liquidity: ${float(liquidity):.2f}, volume: ${float(volume):.2f})")
            return True
            
        # Last resort - even if there's zero liquidity/volume, we'll still try if it has actual data
        logging.info(f"🚀 Token {token_address} has minimal data but attempting to buy anyway")
        return True
        
    except Exception as e:
        # Even if validation fails, we'll still attempt to buy
        logging.error(f"❌ Token validation error for {token_address}: {e} - but attempting buy anyway")
        return True

def real_buy_token(to_addr: str, lamports: int):
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # First check if the token is valid - now with much more permissive validation
        if not is_valid_token(to_addr):
            logging.warning(f"❌ Token {to_addr} failed validation checks. Skipping buy.")
            return None
            
        # First try with simpler route to reduce transaction size
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=2&maxAccounts=10"
        logging.info(f"🔍 Getting buy quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No swap route available for {to_addr}")
            return None

        # Check price impact - now much more permissive (up to 10%)
        price_impact = quote.get('priceImpactPct', 0)
        if isinstance(price_impact, str):
            try:
                price_impact = float(price_impact)
            except:
                price_impact = 0
        
        price_impact = price_impact * 100  # Convert to percentage
                
        if price_impact > 10:  # Increased from 5% to 10% tolerance
            logging.warning(f"❌ Price impact too high ({price_impact:.2f}%) for {to_addr}")
            return None

        # Try getting a smaller transaction by adjusting options
        try:
            swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                "userPublicKey": str(kp.pubkey()),
                "wrapUnwrapSOL": True,
                "quoteResponse": quote,
                "computeUnitPriceMicroLamports": 1000,  # Fixed compute price
                "asLegacyTransaction": False,  # Use versioned transaction
                "prioritizationFeeLamports": 0,  # No priority fee
                "dynamicComputeUnitLimit": True  # Allow dynamic compute limit
            }, timeout=10).json()
            
            if "swapTransaction" not in swap:
                logging.error(f"❌ No swap transaction returned for {to_addr}")
                return None

            tx_data = decode_transaction_blob(swap["swapTransaction"])
            logging.info(f"🚀 Sending BUY transaction for {to_addr}: {tx_data.hex()[:80]}...")
            
            # Send the transaction but don't wait for confirmation here
            sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
            logging.info(f"✅ Buy transaction sent for {to_addr}, sig: {sig.value}")
            
            # Return the signature immediately without waiting for confirmation
            return sig.value
            
        except Exception as first_attempt_error:
            # If the first attempt failed, try with a reduced size transaction by using a simpler route
            logging.warning(f"🔄 First buy attempt failed: {str(first_attempt_error)[:100]}... - trying simplified route")
            
            # Try with a simpler route and lower amount
            reduced_lamports = int(lamports * 0.9)  # Reduce amount by 10%
            quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={reduced_lamports}&slippage=2&maxAccounts=5&onlyDirectRoutes=true"
            r = requests.get(quote_url, timeout=10)
            
            if r.status_code != 200:
                logging.error(f"❌ Simplified route quote API returned {r.status_code}")
                return None
                
            quote = r.json()
            
            if not quote.get("routePlan"):
                logging.error(f"❌ No simplified swap route available for {to_addr}")
                return None
                
            swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                "userPublicKey": str(kp.pubkey()),
                "wrapUnwrapSOL": True,
                "quoteResponse": quote,
                "computeUnitPriceMicroLamports": 1000,
                "asLegacyTransaction": False,
                "prioritizationFeeLamports": 0,
                "dynamicComputeUnitLimit": True
            }, timeout=10).json()
            
            if "swapTransaction" not in swap:
                logging.error(f"❌ No simplified swap transaction returned for {to_addr}")
                return None
                
            tx_data = decode_transaction_blob(swap["swapTransaction"])
            logging.info(f"🚀 Sending simplified BUY transaction for {to_addr}: {tx_data.hex()[:80]}...")
            
            # Send the transaction but don't wait for confirmation here
            sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
            logging.info(f"✅ Simplified buy transaction sent for {to_addr}, sig: {sig.value}")
            
            # Return the signature immediately without waiting for confirmation
            return sig.value
        
    except Exception as e:
        logging.error(f"❌ Buy failed for {to_addr}: {e}")
        fallback_rpc()
        return None

def real_sell_token(to_addr: str):
    """
    Enhanced sell function with transaction size optimization and retry logic
    """
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
            
        # First try with normal settings
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=2&maxAccounts=10"
        logging.info(f"🔍 Getting sell quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No sell route available for {to_addr}")
            return None

        # Try getting transaction and handling size limitations
        try:
            swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                "userPublicKey": str(kp.pubkey()),
                "wrapUnwrapSOL": True,
                "quoteResponse": quote,
                "computeUnitPriceMicroLamports": 1000,  # Fixed compute price
                "asLegacyTransaction": False,  # Use versioned transaction
                "prioritizationFeeLamports": 0,  # No priority fee
                "dynamicComputeUnitLimit": True  # Allow dynamic compute limit
            }, timeout=10).json()
            
            if "swapTransaction" not in swap:
                logging.error(f"❌ No swap transaction returned for selling {to_addr}")
                return None

            tx_data = decode_transaction_blob(swap["swapTransaction"])
            logging.info(f"🚀 Sending SELL transaction for {to_addr}: {tx_data.hex()[:80]}...")
            
            # Send the transaction but don't wait for confirmation here
            sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
            logging.info(f"✅ Sell transaction sent for {to_addr}, sig: {sig.value}")
            
            # Return the signature immediately without waiting for confirmation
            return sig.value
            
        except Exception as first_attempt_error:
            # If the first attempt failed, try with a reduced size transaction by selling 95% of balance
            logging.warning(f"🔄 First sell attempt failed: {str(first_attempt_error)[:100]}... - trying simplified route")
            
            # Try with a simpler route and slightly lower amount
            reduced_balance = int(token_balance * 0.95)  # Reduce amount by 5%
            quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={reduced_balance}&slippage=2&maxAccounts=5&onlyDirectRoutes=true"
            r = requests.get(quote_url, timeout=10)
            
            if r.status_code != 200:
                logging.error(f"❌ Simplified route quote API returned {r.status_code}")
                
                # Last resort - try selling in smaller batches (50% of balance)
                logging.warning(f"🔄 Attempting final sell with 50% of balance for {to_addr}")
                half_balance = int(token_balance * 0.5)
                quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={half_balance}&slippage=3&maxAccounts=3&onlyDirectRoutes=true"
                r = requests.get(quote_url, timeout=10)
                
                if r.status_code != 200:
                    logging.error(f"❌ All sell attempts failed for {to_addr}")
                    return None
                
            quote = r.json()
            
            if not quote.get("routePlan"):
                logging.error(f"❌ No simplified swap route available for {to_addr}")
                return None
                
            swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                "userPublicKey": str(kp.pubkey()),
                "wrapUnwrapSOL": True,
                "quoteResponse": quote,
                "computeUnitPriceMicroLamports": 1000,
                "asLegacyTransaction": False,
                "prioritizationFeeLamports": 0,
                "dynamicComputeUnitLimit": True
            }, timeout=10).json()
            
            if "swapTransaction" not in swap:
                logging.error(f"❌ No simplified swap transaction returned for {to_addr}")
                return None
                
            tx_data = decode_transaction_blob(swap["swapTransaction"])
            logging.info(f"🚀 Sending simplified SELL transaction for {to_addr}: {tx_data.hex()[:80]}...")
            
            # Send the transaction but don't wait for confirmation here
            sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed"))
            logging.info(f"✅ Simplified sell transaction sent for {to_addr}, sig: {sig.value}")
            
            # Return the signature immediately without waiting for confirmation
            return sig.value
        
    except Exception as e:
        logging.error(f"❌ Sell failed for {to_addr}: {e}")
        fallback_rpc()
        return None

async def check_and_sell_token(token, token_data):
    """
    Enhanced check and sell function focused on achieving 2x gains
    and meeting $1000 daily profit target
    """
    global daily_profit, successful_sells_today, successful_2x_sells
    
    try:
        # Get the price using our enhanced multi-source price checker
        price_now = get_token_price(token)
        initial_price = token_data.get('initial_price', 0)
        
        # Fix type issues by ensuring we have proper numeric values
        if isinstance(price_now, str):
            try:
                price_now = float(price_now)
            except:
                price_now = 0
                
        if isinstance(initial_price, str):
            try:
                initial_price = float(initial_price)
            except:
                initial_price = 0
        
        # Get the buy amount (default to BUY_AMOUNT_LAMPORTS if not stored)
        buy_amount = token_data.get('buy_amount', BUY_AMOUNT_LAMPORTS)
        
        # Calculate time since purchase
        minutes_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 60
        
        # If we still can't get any price data
        if price_now <= 0:
            # Log checking attempts based on time intervals
            if int(minutes_since_buy) % PROFIT_CHECK_INTERVAL == 0:
                logging.info(f"⏳ Waiting for price data for {token} - held for {minutes_since_buy:.1f} minutes")
            
            # If we've been waiting too long with no price, consider force selling
            if minutes_since_buy >= FORCE_SELL_MINUTES:
                logging.info(f"⚠️ No price data available for {token} after {minutes_since_buy:.1f} minutes - considering force sell")
                
                # Before force-selling, try ALL available price sources as a last resort
                try:
                    # Try Jupiter's quote API to estimate value
                    r = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={token}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=1", timeout=5)
                    if r.status_code == 200 and 'outAmount' in r.json():
                        out_amount = int(r.json().get('outAmount', 0))
                        # Rough price estimate
                        if out_amount > 0:
                            # We have some value, convert to price
                            estimated_price = out_amount / 1000000000 / 1000000
                            logging.info(f"✅ Estimated price ${estimated_price:.8f} for {token} from Jupiter swap")
                            price_now = estimated_price
                except Exception as e:
                    logging.error(f"❌ Jupiter estimate failed: {e}")
                    
                # Try Raydium last chance
                if price_now <= 0:
                    try:
                        # Try Raydium for pools directly
                        r = requests.get(f"https://api.raydium.io/v2/sdk/liquidity/mainnet.json", timeout=5)
                        if r.status_code == 200:
                            data = r.json()
                            for pool in (data.get('official', []) + data.get('unOfficial', [])):
                                if pool.get('baseMint') == token or pool.get('quoteMint') == token:
                                    pool_price = pool.get('price', 0)
                                    if pool_price:
                                        logging.info(f"✅ Found Raydium pool price: ${pool_price} for {token}")
                                        try:
                                            price_now = float(pool_price)
                                            break
                                        except:
                                            pass
                    except Exception as e:
                        logging.error(f"❌ Raydium last chance failed: {e}")
                
                # If still no price, force sell after waiting period
                if price_now <= 0:
                    logging.info(f"⚠️ FORCE SELLING {token} after {minutes_since_buy:.1f} minutes with no price data")
                    sell_sig = real_sell_token(token)
                    
                    if sell_sig:
                        # Since we don't know price, assume break-even
                        log_trade({
                            "type": "sell", 
                            "token": token,
                            "tx": sell_sig,
                            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                            "price": initial_price if initial_price > 0 else 0.00000001,  # Use initial price as estimate
                            "profit": 0,  # Assume break-even
                            "reason": f"force sold after {minutes_since_buy:.1f} minutes with no price data"
                        })
                        
                        # Notify in Discord
                        if DISCORD_NEWS_CHANNEL_ID:
                            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                            if channel:
                                await channel.send(f"⚠️ Force-sold {token} after {minutes_since_buy:.1f} minutes with no price data! https://solscan.io/tx/{sell_sig}")
                        
                        del bought_tokens[token]
                    
            return
            
        # Skip if can't determine initial price - update it and try again next cycle
        if initial_price <= 0:
            # Store the price if we didn't have one before
            token_data['initial_price'] = price_now
            logging.info(f"✅ Updated initial price for {token} to ${price_now:.8f}")
            
            # If this price update is after waiting a while, log it specially
            if minutes_since_buy > 10:
                logging.info(f"📊 Finally got price for {token} after {minutes_since_buy:.1f} minutes")
                
                # Check if we're at a profit already - could have pumped while we waited
                if 'buy_amount' in token_data:
                    current_value = token_data['buy_amount'] / 1_000_000_000 * price_now
                    logging.info(f"💰 Token {token} estimated value: ${current_value:.2f}")
            return
            
        # Calculate current profit ratio
        price_ratio = price_now / initial_price
        
        # Log status at regular intervals
        if int(minutes_since_buy) % PROFIT_CHECK_INTERVAL == 0:
            approx_profit = ((price_now - initial_price) / initial_price) * buy_amount / 1_000_000_000
            logging.info(f"📈 Token {token} price ratio: {price_ratio:.2f}x (${approx_profit:.2f} profit) - held for {minutes_since_buy:.1f} minutes")
        
        # Track if we're close to our 2x target
        if price_ratio >= 1.8 and price_ratio < 2.0:
            # If we're getting close to 2x, check more frequently and be patient
            logging.info(f"🚀 Token {token} approaching 2x target! Current: {price_ratio:.2f}x")
            
        # Sell conditions (prioritized by importance):
        # 1. Hit profit target - strict 2.0x
        # 2. Hit stop loss to avoid bigger losses
        # 3. Been holding more than 30 minutes and profitable at 1.2x+
        # 4. Force sell after 3 hours regardless
        should_sell = False
        sell_reason = ""
        
        if price_ratio >= SELL_PROFIT_TRIGGER:
            should_sell = True
            sell_reason = f"hit 2x target ({price_ratio:.2f}x)"
        elif price_ratio <= STOP_LOSS_TRIGGER:
            should_sell = True
            sell_reason = f"stop loss triggered ({price_ratio:.2f}x)"
        elif minutes_since_buy >= 30:
            # If token has been held for 30+ minutes, check if it's reasonably profitable
            if price_ratio >= 1.2:  # 20% profit or more - higher threshold than before
                should_sell = True
                sell_reason = f"profit taking after {minutes_since_buy:.1f} minutes ({price_ratio:.2f}x)"
            elif minutes_since_buy >= FORCE_SELL_MINUTES:  # Force sell after extended period
                should_sell = True
                sell_reason = f"held for {minutes_since_buy:.1f} minutes"
            
        if should_sell:
            logging.info(f"🔄 Selling {token} - {sell_reason}")
            # Sell token but don't wait for confirmation
            sell_sig = real_sell_token(token)
            
            if sell_sig:
                profit = ((price_now - initial_price) / initial_price) * buy_amount / 1_000_000_000
                
                log_trade({
                    "type": "sell", 
                    "token": token,
                    "tx": sell_sig,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                    "price": price_now,
                    "profit": profit,
                    "reason": sell_reason
                })
                
                # Increment success counters
                successful_sells_today += 1
                if price_ratio >= 2.0:
                    successful_2x_sells += 1
                
                # Notify in Discord
                if DISCORD_NEWS_CHANNEL_ID:
                    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                    if channel:
                        await channel.send(f"💰 Auto-sold {token} at ${price_now:.6f} ({price_ratio:.2f}x, ${profit:.2f} profit) - {sell_reason}! https://solscan.io/tx/{sell_sig}")
                
                del bought_tokens[token]
                
                # Special notifications for high profit trades
                if price_ratio >= 2.0:
                    logging.info(f"💎 2X TARGET REACHED! {token} sold at {price_ratio:.2f}x return!")
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"💎 2X TARGET REACHED! {token} sold at {price_ratio:.2f}x return! Profit: ${profit:.2f}")
                            
                # Daily profit target notification
                daily_profit_amount = summarize_daily_profit()
                if daily_profit_amount >= DAILY_PROFIT_TARGET:
                    logging.info(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
    
    except Exception as e:
        logging.error(f"❌ Error checking token {token}: {e}")

async def auto_snipe():
    """
    Optimized auto-snipe function focused on maximizing daily profits
    to reach $1000/day target
    """
    global total_buys_today
    
    await bot.wait_until_ready()
    logging.info("🔍 Auto-snipe task started")
    logging.info(f"🎯 Daily profit target: ${DAILY_PROFIT_TARGET:.2f}")
    
    while not bot.is_closed():
        try:
            # First check if we've hit daily profit target
            daily_profit_amount = summarize_daily_profit()
            if daily_profit_amount >= DAILY_PROFIT_TARGET:
                logging.info(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
                logging.info(f"📊 Today's stats: {successful_sells_today} successful sells with {successful_2x_sells} 2x+ sells")
                
                # Just check existing positions for profit taking
                for token, token_data in list(bought_tokens.items()):
                    await check_and_sell_token(token, token_data)
                    
                await asyncio.sleep(30)  # Check less frequently after target is reached
                continue
                
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
            
            # Randomize tokens slightly to avoid buying same tokens as other bots
            if len(target_tokens) > 15:
                random.shuffle(target_tokens[:15])
                
            logging.info(f"🔍 Found {len(target_tokens)} potential tokens to snipe")
            
            # Calculate the current buy amount based on profits made
            current_buy_amount = calculate_dynamic_buy_amount()
            
            # Try to buy new tokens - increased for more buys per cycle
            buy_counter = 0  # Count successful buys
            for token in target_tokens[:20]:  # Try up to 20 tokens per cycle for higher success rate
                if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                    break
                
                if buy_counter >= MAX_BUYS_PER_CYCLE:  # Limit on successful buys per cycle (now 15)
                    break
                    
                logging.info(f"💰 Attempting to buy token: {token} with {current_buy_amount/1000000000:.3f} SOL")
                # Buy the token but don't block on confirmation
                sig = real_buy_token(token, current_buy_amount)  # Use the dynamic amount
                if sig:
                    buy_counter += 1
                    total_buys_today += 1
                    price = get_token_price(token)
                    # Handle string prices
                    if isinstance(price, str):
                        try:
                            price = float(price)
                        except:
                            price = 0
                            
                    bought_tokens[token] = {
                        'buy_sig': sig,
                        'buy_time': datetime.utcnow(),
                        'token': token,
                        'initial_price': price,
                        'buy_amount': current_buy_amount  # Store the buy amount for calculating profit
                    }
                    log_trade({
                        "type": "buy", 
                        "token": token, 
                        "tx": sig, 
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
                        "price": price,
                        "amount_lamports": current_buy_amount
                    })
                    
                    # Notify in Discord
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🚀 Auto-bought {token} at ${price:.6f} with {current_buy_amount/1000000000:.3f} SOL! https://solscan.io/tx/{sig}")
                    
                    # Extra logging for tracking throughput
                    logging.info(f"📈 Buy #{total_buys_today} today | Current stats: {successful_sells_today} sells, {successful_2x_sells} 2x+ sells")
                
                # Add a short delay between buy attempts to avoid rate limits
                # Using asyncio.sleep to avoid blocking
                await asyncio.sleep(2)
            
            # Check existing positions for selling
            for token, token_data in list(bought_tokens.items()):
                await check_and_sell_token(token, token_data)
            
            # Summarize current status
            log_wallet_balance()
            
            # Calculate and display stats
            if total_buys_today > 0:
                conversion_rate = successful_sells_today / total_buys_today * 100 if total_buys_today > 0 else 0
                x2_rate = successful_2x_sells / successful_sells_today * 100 if successful_sells_today > 0 else 0
                avg_profit = daily_profit_amount / successful_sells_today if successful_sells_today > 0 else 0
                
                if total_buys_today % 10 == 0:  # Log stats every 10 buys
                    logging.info(f"📊 Stats: {conversion_rate:.1f}% success rate | {x2_rate:.1f}% 2x+ rate | ${avg_profit:.2f} avg profit")
                    logging.info(f"🔢 Totals: {total_buys_today} buys | {successful_sells_today} sells | ${daily_profit_amount:.2f} profit")
            
        except Exception as e:
            logging.error(f"❌ Error in auto_snipe: {e}")
            
        # Use asyncio.sleep to avoid blocking
        await asyncio.sleep(10)  # Reduced from 30 to 10 seconds for more aggressive trading

def calculate_dynamic_buy_amount():
    """
    Dynamically adjust buy amount based on daily profits and success rates
    """
    global daily_profit, successful_sells_today, successful_2x_sells
    
    # Base amount is 0.15 SOL
    base_amount = 150000000  # 0.15 SOL in lamports
    
    # If we're making profits, scale up gradually
    if daily_profit > 0:
        # Scale factor based on profit (increases as profit grows)
        if daily_profit < 200:  # Less than $200 profit
            scale_factor = 1.0  # No increase
        elif daily_profit < 500:  # $200-500 profit
            scale_factor = 1.2  # 20% increase
        elif daily_profit < 800:  # $500-800 profit
            scale_factor = 1.5  # 50% increase
        elif daily_profit < DAILY_PROFIT_TARGET:  # Getting close to target
            scale_factor = 1.8  # 80% increase
        else:  # $1000+ profit (hit target)
            scale_factor = 2.0  # Double the position size
            
        # Additional boost if we have a high 2x success rate
        if successful_sells_today > 10:  # Only apply after we have enough data
            x2_rate = successful_2x_sells / successful_sells_today if successful_sells_today > 0 else 0
            if x2_rate >= 0.3:  # 30%+ of sells are 2x or better
                scale_factor *= 1.2  # Additional 20% boost
            
        # Apply the scaling factor
        adjusted_amount = int(base_amount * scale_factor)
        
        # Cap at a reasonable maximum (0.5 SOL)
        max_amount = 500000000  # 0.5 SOL in lamports
        
        # Return the calculated amount, capped at the maximum
        return min(adjusted_amount, max_amount)
    else:
        # If no profit or loss, use the base amount
        return base_amount

# Add this function to reset stats daily
async def reset_daily_stats():
    """Reset daily stats at midnight UTC"""
    global daily_profit, total_buys_today, successful_sells_today, successful_2x_sells
    
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.utcnow()
        # Calculate time until next midnight UTC
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_midnight = (tomorrow - now).total_seconds()
        
        # Sleep until midnight
        await asyncio.sleep(seconds_until_midnight)
        
        # Reset daily stats
        old_profit = daily_profit
        daily_profit = 0
        old_buys = total_buys_today
        total_buys_today = 0
        old_sells = successful_sells_today
        successful_sells_today = 0
        old_2x = successful_2x_sells
        successful_2x_sells = 0
        
        # Log the reset
        logging.info(f"🔄 Daily stats reset! Previous: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")
        
        # Notify in Discord
        if DISCORD_NEWS_CHANNEL_ID:
            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
            if channel:
                await channel.send(f"🔄 Daily stats reset! Previous day: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")

@tree.command(name="profit", description="Check today's trading profit")
async def profit_slash(interaction: discord.Interaction):
    """Enhanced profit command with detailed stats"""
    total_profit = summarize_daily_profit()
    
    # Calculate stats
    conversion_rate = successful_sells_today / total_buys_today * 100 if total_buys_today > 0 else 0
    x2_rate = successful_2x_sells / successful_sells_today * 100 if successful_sells_today > 0 else 0
    avg_profit = total_profit / successful_sells_today if successful_sells_today > 0 else 0
    
    # Estimate time to target based on current rate
    remaining_profit = max(0, DAILY_PROFIT_TARGET - total_profit)
    hours_passed = datetime.utcnow().hour + datetime.utcnow().minute / 60
    if hours_passed > 0 and total_profit > 0:
        profit_per_hour = total_profit / hours_passed
        hours_to_target = remaining_profit / profit_per_hour if profit_per_hour > 0 else 0
        eta_msg = f"ETA to ${DAILY_PROFIT_TARGET:.0f} target: {hours_to_target:.1f} hours"
    else:
        eta_msg = "Insufficient data to estimate target ETA"
    
    stats = f"""📊 **Profit Stats:**
Today's profit: ${total_profit:.2f} / ${DAILY_PROFIT_TARGET:.2f} target ({(total_profit/DAILY_PROFIT_TARGET*100):.1f}%)

📈 **Performance:**
- Buys: {total_buys_today}
- Successful sells: {successful_sells_today} ({conversion_rate:.1f}% success rate)
- 2x+ sells: {successful_2x_sells} ({x2_rate:.1f}% of sells)
- Average profit: ${avg_profit:.2f} per successful trade

🎯 {eta_msg}

Current buy amount: {calculate_dynamic_buy_amount()/1000000000:.3f} SOL
"""
    await interaction.response.send_message(stats)

@tree.command(name="holdings", description="Check current token holdings")
async def holdings_slash(interaction: discord.Interaction):
    """Enhanced holdings command with detailed metrics"""
    if not bought_tokens:
        await interaction.response.send_message("No tokens currently held.")
        return
        
    holdings_text = "**Current Holdings:**\n"
    total_value = 0
    tokens_with_prices = 0
    tokens_without_prices = 0
    potential_profit = 0
    
    # Sort tokens by time held (oldest first)
    sorted_tokens = sorted(bought_tokens.items(), key=lambda x: x[1]['buy_time'])
    
    for token, data in sorted_tokens:
        current_price = get_token_price(token)
        initial_price = data['initial_price']
        buy_amount = data.get('buy_amount', BUY_AMOUNT_LAMPORTS)
        buy_amount_sol = buy_amount / 1_000_000_000
        
        # Handle string conversion
        if isinstance(current_price, str):
            try:
                current_price = float(current_price)
            except:
                current_price = 0
                
        if isinstance(initial_price, str):
            try:
                initial_price = float(initial_price)
            except:
                initial_price = 0
        
        minutes_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
        
        if current_price > 0 and initial_price > 0:
            price_ratio = current_price / initial_price
            profit_percent = (price_ratio - 1) * 100
            estimated_value = buy_amount_sol * price_ratio
            total_value += estimated_value
            tokens_with_prices += 1
            
            token_profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
            potential_profit += token_profit
            
            # Color coding based on performance
            if price_ratio >= 1.8:  # Almost 2x
                emoji = "🔥"  # Fire for near target
            elif price_ratio >= 1.2:  # Good profit
                emoji = "💰"  # Money bag for profit
            elif price_ratio >= 0.9:  # Near break-even
                emoji = "⚖️"  # Balance for near break-even
            else:  # Loss
                emoji = "📉"  # Chart down for loss
                
            holdings_text += f"{emoji} {token}: {price_ratio:.2f}x ({profit_percent:.1f}%) - ${token_profit:.2f} profit - Held {minutes_held:.1f}min\n"
        else:
            tokens_without_prices += 1
            if initial_price > 0:
                holdings_text += f"⏳ {token}: No current price (initial ${initial_price:.8f}) - Held {minutes_held:.1f}min\n"
            else:
                holdings_text += f"⏳ {token}: No price data yet - Held {minutes_held:.1f}min\n"
    
    # Summary stats
    holdings_text += f"\n**Summary:**\n"
    holdings_text += f"Total tokens: {len(bought_tokens)}
