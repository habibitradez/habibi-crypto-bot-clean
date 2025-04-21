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
SELL_PROFIT_TRIGGER = 2.0  # Changed back to 2.0x for better profits
STOP_LOSS_TRIGGER = 0.85   # Kept at 0.85x for tighter stop loss
MAX_TOKENS_TO_HOLD = 20    # Increased from 10 to 20 for more concurrent positions
BUY_AMOUNT_LAMPORTS = 150000000  # Changed to 0.15 SOL per trade as requested
DAILY_PROFIT_TARGET = 1000  # $1000 daily profit target
MAX_BUYS_PER_CYCLE = 10    # Increased from 5 to 10 buys per cycle

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

def calculate_dynamic_buy_amount():
    """
    Dynamically adjust buy amount based on daily profits
    As the bot makes more money, it can increase position sizes
    """
    global daily_profit
    
    # Base amount is 0.15 SOL
    base_amount = 150000000  # 0.15 SOL in lamports
    
    # If we're making profits, scale up gradually
    if daily_profit > 0:
        # Convert profit to SOL (rough approximation)
        profit_in_sol = daily_profit / 100  # Assuming average SOL price of $100
        
        # Scale factor based on profit (increases as profit grows)
        if daily_profit < 200:  # Less than $200 profit
            scale_factor = 1.0  # No increase
        elif daily_profit < 500:  # $200-500 profit
            scale_factor = 1.2  # 20% increase
        elif daily_profit < 1000:  # $500-1000 profit
            scale_factor = 1.5  # 50% increase
        else:  # $1000+ profit (hit target)
            scale_factor = 2.0  # Double the position size
            
        # Apply the scaling factor
        adjusted_amount = int(base_amount * scale_factor)
        
        # Cap at a reasonable maximum (0.5 SOL)
        max_amount = 500000000  # 0.5 SOL in lamports
        
        # Return the calculated amount, capped at the maximum
        return min(adjusted_amount, max_amount)
    else:
        # If no profit or loss, use the base amount
        return base_amount

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
            
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=2"  # Increased slippage to 2%
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

        # Randomize compute unit price to avoid front-running
        compute_price = random.randint(500, 1500)  # Random price between 500-1500 micro-lamports
        
        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": compute_price,
            "asLegacyTransaction": True
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
            
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=2"  # Increased slippage for sells too
        logging.info(f"🔍 Getting sell quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No sell route available for {to_addr}")
            return None

        # Randomize compute unit price for sell as well
        compute_price = random.randint(500, 1500)  # Random price between 500-1500 micro-lamports

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": compute_price,
            "asLegacyTransaction": True
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
    except Exception as e:
        logging.error(f"❌ Sell failed for {to_addr}: {e}")
        fallback_rpc()
        return None

async def check_and_sell_token(token, token_data):
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
        
        # If we still can't get any price data
        if price_now <= 0:
            # Check how long we've been waiting
            minutes_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 60
            
            # Force-check every 5 minutes in logs
            if int(minutes_since_buy) % 5 == 0:
                logging.info(f"⏳ Waiting for price data for {token} - held for {minutes_since_buy:.1f} minutes")
            
            # If we've been waiting too long with no price (2 hours), consider selling
            if minutes_since_buy >= 120:
                logging.info(f"⚠️ No price data available for {token} after {minutes_since_buy:.1f} minutes - considering force sell")
                
                # Before force-selling, make one more attempt with a different price source
                # Try using a DEX directly as last resort
                try:
                    # Special code to estimate price directly from Jupiter swap
                    r = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={token}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=1", timeout=5)
                    if r.status_code == 200 and 'outAmount' in r.json():
                        out_amount = int(r.json()['outAmount'])
                        # Rough price estimate
                        if out_amount > 0:
                            # We have some value, convert to price
                            estimated_price = out_amount / 1000000000 / 1000000
                            logging.info(f"✅ Estimated price ${estimated_price:.8f} for {token} from Jupiter swap")
                            price_now = estimated_price
                except Exception as e:
                    logging.error(f"❌ Last-resort price check failed: {e}")
                
                # If still no price, force sell after 2 hours 
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
                            "price": initial_price,  # Use initial price as estimate
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
            
        # Skip if can't determine initial price - try again next cycle
        if initial_price <= 0:
            # Store the price if we didn't have one before
            token_data['initial_price'] = price_now
            logging.info(f"✅ Updated initial price for {token} to ${price_now:.8f}")
            return
            
        price_ratio = price_now / initial_price
        minutes_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 60
        
        # Sell conditions:
        # 1. Hit profit target - now back to 2.0x
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
            
            # Calculate the current buy amount based on profits made
            current_buy_amount = calculate_dynamic_buy_amount()
            
            # Try to buy new tokens - increased for more buys per cycle
            buy_counter = 0  # Count successful buys
            for token in target_tokens[:15]:  # Try up to 15 tokens per cycle
                if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                    break
                
                if buy_counter >= MAX_BUYS_PER_CYCLE:  # Limit on successful buys per cycle (now 10)
                    break
                    
                logging.info(f"💰 Attempting to buy token: {token} with {current_buy_amount/1000000000:.3f} SOL")
                # Buy the token but don't block on confirmation
                sig = real_buy_token(token, current_buy_amount)  # Use the dynamic amount
                if sig:
                    buy_counter += 1
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
                
                # Add a short delay between buy attempts to avoid rate limits
                # Using asyncio.sleep to avoid blocking
                await asyncio.sleep(2)
            
            # Check existing positions for selling
            for token, token_data in list(bought_tokens.items()):
                await check_and_sell_token(token, token_data)
            
            # Summarize current status
            daily_profit_amount = summarize_daily_profit()
            log_wallet_balance()
            
            # Special message when hitting profit target
            if daily_profit_amount >= DAILY_PROFIT_TARGET:
                logging.info(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
                if DISCORD_NEWS_CHANNEL_ID:
                    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                    if channel:
                        await channel.send(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
            
        except Exception as e:
            logging.error(f"❌ Error in auto_snipe: {e}")
            
        # Use asyncio.sleep to avoid blocking
        await asyncio.sleep(10)  # Reduced from 30 to 10 seconds for more aggressive trading

@tree.command(name="buy", description="Buy a token using SOL")
async def buy_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Buying {token}...")
    
    # Use dynamic amount based on profit performance
    current_buy_amount = calculate_dynamic_buy_amount()
    
    # Additional protection: check token format
    try:
        token = sanitize_token_address(token)
    except ValueError as e:
        await interaction.followup.send(f"❌ Invalid token address format: {str(e)}")
        return
        
    sig = real_buy_token(token, current_buy_amount)
    if sig:
        price = get_token_price(token)
        # Handle string prices
        if isinstance(price, str):
            try:
                price = float(price)
            except:
                price = 0
                
        log_trade({
            "type": "buy",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "price": price,
            "amount_lamports": current_buy_amount
        })
        await interaction.followup.send(f"✅ Bought {token} at ${price:.6f} with {current_buy_amount/1000000000:.3f} SOL! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Buy failed for {token}. Check logs.")

@tree.command(name="sell", description="Sell a token for SOL")
async def sell_slash(interaction: discord.Interaction, token: str):
    await interaction.response.send_message(f"Selling {token}...")
    
    # Additional validation for token address
    try:
        token = sanitize_token_address(token)
    except ValueError as e:
        await interaction.followup.send(f"❌ Invalid token address format: {str(e)}")
        return
        
    initial_price = 0
    buy_amount = BUY_AMOUNT_LAMPORTS
    if token in bought_tokens:
        initial_price = bought_tokens[token]['initial_price']
        buy_amount = bought_tokens[token].get('buy_amount', BUY_AMOUNT_LAMPORTS)
        if isinstance(initial_price, str):
            try:
                initial_price = float(initial_price)
            except:
                initial_price = 0
    
    sig = real_sell_token(token)
    if sig:
        current_price = get_token_price(token)
        if isinstance(current_price, str):
            try:
                current_price = float(current_price)
            except:
                current_price = 0
                
        profit = 0
        if initial_price > 0 and current_price > 0:
            profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
        
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
    await interaction.response.send_message(f"📊 Today's profit so far: ${total_profit:.2f} / ${DAILY_PROFIT_TARGET:.2f} target")

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
    total_value = 0
    
    for token, data in bought_tokens.items():
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
        
        profit_percent = ((current_price - initial_price) / initial_price * 100) if initial_price > 0 else 0
        buy_time = data['buy_time'].strftime("%H:%M:%S")
        minutes_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
        estimated_value = 0
        
        if current_price > 0:
            estimated_value = buy_amount_sol * (current_price / initial_price) if initial_price > 0 else 0
            total_value += estimated_value
        
        holdings_text += f"- {token}: Bought at ${initial_price:.8f} with {buy_amount_sol:.3f} SOL, Now ${current_price:.8f} ({profit_percent:.2f}%) - Held for {minutes_held:.1f} min - Est. Value: ${estimated_value:.2f}\n"
    
    holdings_text += f"\nTotal Estimated Holdings Value: ${total_value:.2f}"
        
    await interaction.response.send_message(holdings_text)

@tree.command(name="debug", description="Debug token fetching and pricing")
async def debug_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Running enhanced debug...")
    try:
        # Test the ultra-new token finder
        ultra_new_start = time.time()
        r = requests.get("https://public-api.birdeye.so/public/tokenlist?sort_by=created_at&sort_type=desc&offset=0&limit=50", 
                        timeout=10)
        ultra_new_tokens = []
        if r.status_code == 200:
            data = r.json()
            if 'data' in data:
                current_time = time.time() * 1000
                for token in data['data']:
                    if 'address' in token and 'createdAt' in token:
                        creation_time = token.get('createdAt', 0)
                        if (current_time - creation_time) < 3600000:
                            ultra_new_tokens.append(token['address'])
        ultra_new_time = time.time() - ultra_new_start
        
        # Test fetch_new_tokens
        new_start = time.time()
        new_tokens = fetch_new_tokens()
        new_time = time.time() - new_start
        
        # Test multi-source price checking
        price_results = {}
        price_success = 0
        for token in (ultra_new_tokens + new_tokens)[:5]:  # Test on up to 5 tokens
            # Test each price source
            birdeye_price = try_birdeye_price(token)
            jupiter_price = try_jupiter_price(token)
            raydium_price = try_raydium_price(token)
            
            if birdeye_price > 0 or jupiter_price > 0 or raydium_price > 0:
                price_success += 1
                
            price_results[token] = {
                "birdeye": birdeye_price,
                "jupiter": jupiter_price,
                "raydium": raydium_price,
                "final": get_token_price(token)
            }
        
        debug_info = f"""Enhanced Debug Results:
        
🔥 Ultra-new tokens (created in last hour): {len(ultra_new_tokens)} in {ultra_new_time:.2f}s
Sample ultra-new tokens:
{', '.join(ultra_new_tokens[:3]) if ultra_new_tokens else 'None'}

🚀 All potential tokens: {len(new_tokens)} in {new_time:.2f}s
Sample tokens:
{', '.join(new_tokens[:5]) if new_tokens else 'None'}

📊 Multi-source price checking results:
Success rate: {price_success}/{len(price_results)} tokens have at least one price source
"""
        for token, prices in price_results.items():
            debug_info += f"\n- {token}:\n"
            debug_info += f"  Birdeye: ${prices['birdeye']:.8f}\n"
            debug_info += f"  Jupiter: ${prices['jupiter']:.8f}\n"
            debug_info += f"  Raydium: ${prices['raydium']:.8f}\n"
            debug_info += f"  Final used price: ${prices['final']:.8f}\n"
        
        # Trade statistics
        profit_sum = sum(entry.get("profit", 0) for entry in trade_log if entry.get("type") == "sell")
        sell_count = sum(1 for entry in trade_log if entry.get("type") == "sell")
        buy_count = sum(1 for entry in trade_log if entry.get("type") == "buy")
        avg_profit = profit_sum / sell_count if sell_count > 0 else 0
        
        debug_info += f"""
📈 Trading Statistics:
Total buys: {buy_count}
Total sells: {sell_count}
Total profit: ${profit_sum:.2f}
Average profit per trade: ${avg_profit:.2f}
Current buy amount: ${calculate_dynamic_buy_amount()/1000000000:.3f} SOL
"""
        
        await interaction.followup.send(debug_info)
    except Exception as e:
        await interaction.followup.send(f"Debug failed: {e}")

@tree.command(name="chart", description="Generate a profit chart")
async def chart_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Generating profit chart...")
    try:
        generate_profit_chart()
        await interaction.followup.send(file=discord.File('profit_chart.png'))
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to generate chart: {e}")

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
        plt.axhline(y=DAILY_PROFIT_TARGET, color='r', linestyle='--', label=f'Target: ${DAILY_PROFIT_TARGET}')
        plt.title('Cumulative Trading Profit')
        plt.xlabel('Time')
        plt.ylabel('Profit (USD)')
        plt.grid(True)
        plt.legend()
        plt.savefig('profit_chart.png')
        logging.info("📊 Generated profit chart")
    except Exception as e:
        logging.error(f"❌ Error generating profit chart: {e}")

# Custom helper command to force refresh token data
@tree.command(name="refresh", description="Force refresh price data for tokens")
async def refresh_slash(interaction: discord.Interaction):
    await interaction.response.send_message("Refreshing price data for all tokens...")
    
    if not bought_tokens:
        await interaction.followup.send("No tokens currently held.")
        return
    
    refresh_results = []
    for token, data in list(bought_tokens.items()):
        old_price = data.get('initial_price', 0)
        # Try all price sources
        new_price = get_token_price(token)
        
        if new_price > 0 and old_price <= 0:
            # We got a price for a token that didn't have one before
            data['initial_price'] = new_price
            refresh_results.append(f"✅ {token}: Updated initial price to ${new_price:.8f}")
        elif new_price > 0:
            # Just report current price vs. initial
            price_ratio = new_price / old_price if old_price > 0 else 0
            refresh_results.append(f"📊 {token}: Current ${new_price:.8f} vs Initial ${old_price:.8f} ({price_ratio:.2f}x)")
        else:
            # Still no price
            minutes_since_buy = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
            refresh_results.append(f"⛔ {token}: No price data available after {minutes_since_buy:.1f} minutes")
    
    await interaction.followup.send("\n".join(refresh_results))

@tree.command(name="reset", description="Reset daily profit counter")
async def reset_slash(interaction: discord.Interaction):
    global daily_profit
    old_profit = daily_profit
    daily_profit = 0
    await interaction.response.send_message(f"Daily profit counter reset from ${old_profit:.2f} to $0.00")

# Load existing trade log if available
try:
    with open("trade_log.json", "r") as f:
        trade_log = json.load(f)
    logging.info(f"✅ Loaded {len(trade_log)} previous trades")
    
    # Calculate daily profit from loaded trades
    daily_profit = sum(entry.get("profit", 0) for entry in trade_log if entry.get("type") == "sell")
except FileNotFoundError:
    logging.info("No previous trade log found. Starting fresh.")

@bot.event
async def on_ready():
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    log_wallet_balance()
    bot.loop.create_task(auto_snipe())
    logging.info(f"🚀 ENHANCED Bot fully ready: Commands, Auto-sniping, Wallet Active. Daily profit target: ${DAILY_PROFIT_TARGET:.2f}")

# Start the bot
if __name__ == "__main__":
    logging.info("🚀 Starting Enhanced Solana trading bot...")
    bot.run(DISCORD_TOKEN)
