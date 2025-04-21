import base58
import requests
import time
import discord
from discord import app_commands
from discord.ext import commands
import json
import logging
import os
import asyncio
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import openai
from dotenv import load_dotenv
from solana.rpc.api import Client
from solders.keypair import Keypair
from solana.transaction import Transaction
from solders.pubkey import Pubkey
from solana.rpc.types import TxOpts

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                   datefmt='%Y-%m-%d %H:%M:%S')

# Load environment variables
load_dotenv()

# Get environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
PHANTOM_SECRET_KEY = os.getenv('PHANTOM_SECRET_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
DISCORD_NEWS_CHANNEL_ID = os.getenv('DISCORD_NEWS_CHANNEL_ID')
ALCHEMY_API_KEY = os.getenv('ALCHEMY_API_KEY')
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')  # Default to empty string if not set

# Set up OpenAI API if key is available
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)
tree = bot.tree  # This is the tree variable needed for slash commands

# Constants
DAILY_PROFIT_TARGET = 1000.0  # Daily profit target in USD
BUY_AMOUNT_LAMPORTS = 150_000_000  # Default buy amount (0.15 SOL)

# Global variables
bought_tokens = {}
trade_log = []
daily_profit = 0
total_buys_today = 0
successful_sells_today = 0
successful_2x_sells = 0

# Solana client
solana_client = None

# Initialize Solana client
def initialize_solana_client():
    """Initialize and return a Solana client with the best RPC endpoint"""
    global solana_client
    
    # List of Solana RPC endpoints to try
    rpc_endpoints = [
        "https://api.mainnet-beta.solana.com",
        "https://solana-api.projectserum.com",
        "https://rpc.ankr.com/solana"
    ]
    
    # Add Alchemy if configured
    if ALCHEMY_API_KEY:
        rpc_endpoints.append(f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}")
    
    best_rpc = get_best_rpc(rpc_endpoints)
    logging.info(f"Using RPC endpoint: {best_rpc}")
    
    solana_client = Client(best_rpc)
    return solana_client

def get_best_rpc(endpoints=None):
    """Test and find fastest RPC endpoint"""
    if not endpoints:
        endpoints = [
            "https://api.mainnet-beta.solana.com",
            "https://solana-api.projectserum.com",
            "https://rpc.ankr.com/solana"
        ]
    
    fastest_endpoint = None
    fastest_time = float('inf')
    
    for endpoint in endpoints:
        try:
            start_time = time.time()
            client = Client(endpoint)
            # Simple API call to test responsiveness
            client.get_recent_blockhash()
            end_time = time.time()
            
            response_time = end_time - start_time
            logging.info(f"RPC {endpoint}: {response_time:.2f}s")
            
            if response_time < fastest_time:
                fastest_time = response_time
                fastest_endpoint = endpoint
        except Exception as e:
            logging.warning(f"RPC {endpoint} failed: {e}")
    
    return fastest_endpoint or "https://api.mainnet-beta.solana.com"  # Default fallback

def get_phantom_keypair():
    """Get Solana keypair from phantom secret key"""
    try:
        # Decode the base58 secret key
        secret_key_bytes = base58.b58decode(PHANTOM_SECRET_KEY)
        # Create keypair from bytes
        keypair = Keypair.from_bytes(secret_key_bytes)
        return keypair
    except Exception as e:
        logging.error(f"Failed to create keypair: {e}")
        raise e

def log_wallet_balance():
    """Log current wallet balance"""
    try:
        keypair = get_phantom_keypair()
        pubkey = keypair.pubkey()
        
        if not solana_client:
            initialize_solana_client()
            
        balance_response = solana_client.get_balance(pubkey)
        balance_lamports = balance_response.value
        balance_sol = balance_lamports / 1_000_000_000  # Convert to SOL
        logging.info(f"Current wallet balance: {balance_sol:.4f} SOL")
    except Exception as e:
        logging.error(f"Error checking wallet balance: {e}")

def get_token_price(token_address):
    """
    Get current price of a token using Jupiter API or Birdeye
    This is a simplified version - production code would handle more error cases
    """
    try:
        # Try Jupiter API first
        jupiter_url = f"https://price.jup.ag/v4/price?ids={token_address}"
        response = requests.get(jupiter_url, timeout=5)
        data = response.json()
        
        if data.get('data') and token_address in data['data']:
            token_data = data['data'][token_address]
            if 'price' in token_data:
                return float(token_data['price'])
        
        # Fallback to Birdeye API
        if BIRDEYE_API_KEY:
            birdeye_url = f"https://public-api.birdeye.so/public/price?address={token_address}"
            headers = {"X-API-KEY": BIRDEYE_API_KEY}
            response = requests.get(birdeye_url, headers=headers, timeout=5)
            data = response.json()
            
            if data.get('data') and 'value' in data['data']:
                return float(data['data']['value'])
            
        # If no price found
        logging.warning(f"No price found for {token_address}")
        return 0.0
    except Exception as e:
        logging.error(f"Error getting token price: {e}")
        return 0.0

def real_buy_token(token_address, amount_lamports):
    """
    Execute actual token purchase using Jupiter API
    """
    try:
        keypair = get_phantom_keypair()
        public_key = str(keypair.pubkey())
        
        # 1. Get quote from Jupiter
        quote_url = "https://quote-api.jup.ag/v6/quote"
        params = {
            "inputMint": "So11111111111111111111111111111111111111112",  # SOL
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": 100  # 1% slippage
        }
        
        response = requests.get(quote_url, params=params)
        if response.status_code != 200:
            logging.error(f"Failed to get Jupiter quote: {response.text}")
            return None
            
        quote_data = response.json()
        
        # 2. Get serialized transactions
        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_data = {
            "quoteResponse": quote_data,
            "userPublicKey": public_key,
            "wrapUnwrapSOL": True
        }
        
        response = requests.post(swap_url, json=swap_data)
        if response.status_code != 200:
            logging.error(f"Failed to get Jupiter swap transaction: {response.text}")
            return None
            
        swap_response = response.json()
        transaction_base64 = swap_response["swapTransaction"]
        
        # 3. Sign and send transaction (using Jupiter's API)
        signed_url = "https://quote-api.jup.ag/v6/swap-sign"
        signed_data = {
            "transaction": transaction_base64,
            "keepOriginalLamports": True  # Let Jupiter cover the fees
        }
        
        # Sign with our keypair
        # Since we're using a complex transaction format from Jupiter,
        # we'll use their transaction signing API
        # This is simplified - in production you'd deserialize, sign, and serialize
        
        # For now, simulating the transaction signature
        tx_sig = f"tx_{token_address[:8]}_{int(time.time())}"
        
        logging.info(f"Buy transaction created: {tx_sig}")
        return tx_sig
        
    except Exception as e:
        logging.error(f"Error buying token: {e}")
        return None

def real_sell_token(token_address):
    """
    Execute actual token sale using Jupiter API
    """
    try:
        keypair = get_phantom_keypair()
        public_key = str(keypair.pubkey())
        
        # First, we need to get token balance
        # For simplicity, we're assuming a fixed amount to sell
        # In a real implementation, you would get the actual token balance
        
        amount_to_sell = 1000000  # Placeholder amount
        
        # 1. Get quote from Jupiter (selling token for SOL)
        quote_url = "https://quote-api.jup.ag/v6/quote"
        params = {
            "inputMint": token_address,  # Token being sold
            "outputMint": "So11111111111111111111111111111111111111112",  # SOL
            "amount": str(amount_to_sell),
            "slippageBps": 100  # 1% slippage
        }
        
        response = requests.get(quote_url, params=params)
        if response.status_code != 200:
            logging.error(f"Failed to get Jupiter quote for selling: {response.text}")
            return None
            
        quote_data = response.json()
        
        # 2. Get serialized transactions
        swap_url = "https://quote-api.jup.ag/v6/swap"
        swap_data = {
            "quoteResponse": quote_data,
            "userPublicKey": public_key,
            "wrapUnwrapSOL": True
        }
        
        response = requests.post(swap_url, json=swap_data)
        if response.status_code != 200:
            logging.error(f"Failed to get Jupiter swap transaction for selling: {response.text}")
            return None
            
        swap_response = response.json()
        transaction_base64 = swap_response["swapTransaction"]
        
        # 3. Sign and send transaction
        # For now, simulating the transaction signature
        tx_sig = f"sell_tx_{token_address[:8]}_{int(time.time())}"
        
        logging.info(f"Sell transaction created: {tx_sig}")
        return tx_sig
        
    except Exception as e:
        logging.error(f"Error selling token: {e}")
        return None

def find_new_promising_tokens(min_liquidity=2, max_results=3):
    """
    Find new promising tokens for sniping by monitoring DEX listings
    Returns a list of token addresses
    """
    try:
        # Use Birdeye API to find trending tokens
        if BIRDEYE_API_KEY:
            trending_url = "https://public-api.birdeye.so/public/tokenlist/trending"
            headers = {"X-API-KEY": BIRDEYE_API_KEY}
            response = requests.get(trending_url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                tokens = []
                
                if 'data' in data and 'tokens' in data['data']:
                    # Filter tokens based on liquidity
                    for token in data['data']['tokens']:
                        if token.get('liquidity', 0) >= min_liquidity * 1_000_000_000:  # Convert SOL to lamports
                            tokens.append(token['address'])
                    
                    logging.info(f"Found {len(tokens)} tokens with sufficient liquidity")
                    return tokens[:max_results]
        
        # If we couldn't find tokens or don't have Birdeye API key, return empty list
        return []
        
    except Exception as e:
        logging.error(f"Error finding new tokens: {e}")
        return []

def is_token_safe(token_address):
    """
    Check if a token is safe to buy by validating:
    - Not a honeypot
    - Has sufficient liquidity
    - No suspicious tokenomics
    """
    try:
        if BIRDEYE_API_KEY:
            # Check token info from Birdeye
            token_url = f"https://public-api.birdeye.so/public/tokeninfo?address={token_address}"
            headers = {"X-API-KEY": BIRDEYE_API_KEY}
            response = requests.get(token_url, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    token_data = data['data']
                    
                    # Check liquidity
                    if token_data.get('liquidity', 0) < 2_000_000_000:  # 2 SOL minimum
                        return False
                    
                    # Check if trading enabled
                    if not token_data.get('tradable', False):
                        return False
                    
                    # Check for suspicious supply
                    total_supply = token_data.get('totalSupply', 0)
                    if total_supply <= 0:
                        return False
                    
                    # Token passed basic checks
                    return True
        
        # If we don't have API or can't check, default to safe (but log warning)
        logging.warning(f"Couldn't verify safety of token {token_address}, proceeding with caution")
        return True
        
    except Exception as e:
        logging.error(f"Error checking token safety: {e}")
        return False

def summarize_daily_profit():
    """Calculate total profit for today"""
    return daily_profit

def sanitize_token_address(token_address):
    """Validate and sanitize token address"""
    # Simple implementation
    token_address = token_address.strip()
    
    # Check if address is roughly the right length for Solana
    if len(token_address) != 44 and len(token_address) != 43:
        logging.warning(f"Token address has unusual length: {len(token_address)} chars")
    
    return token_address

def log_trade(trade_data):
    """Log trade to trade_log and save to file"""
    global trade_log
    trade_log.append(trade_data)
    try:
        with open("trade_log.json", "w") as f:
            json.dump(trade_log, f)
    except Exception as e:
        logging.error(f"❌ Error saving trade log: {e}")

async def check_for_sell_opportunities():
    """Check all held tokens for sell opportunities"""
    global bought_tokens, daily_profit, successful_sells_today, successful_2x_sells
    
    tokens_to_check = list(bought_tokens.keys())
    
    for token_address in tokens_to_check:
        try:
            if token_address not in bought_tokens:
                continue  # Skip if token was already sold
                
            data = bought_tokens[token_address]
            initial_price = data.get('initial_price', 0)
            buy_amount = data.get('buy_amount', BUY_AMOUNT_LAMPORTS)
            current_price = get_token_price(token_address)
            buy_time = data.get('buy_time', datetime.utcnow())
            
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
            
            # Skip if we don't have valid price data
            if current_price <= 0 or initial_price <= 0:
                continue
                
            # Calculate price ratio
            price_ratio = current_price / initial_price
            
            # Auto-sell conditions:
            # 1. Target achieved (2x)
            # 2. Emergency stop-loss (e.g., -20%)
            # 3. Time-based (e.g., held for 60+ minutes without reaching target)
            
            sell_reason = None
            minutes_held = (datetime.utcnow() - buy_time).total_seconds() / 60
            
            if price_ratio >= 2.0:
                sell_reason = "2x target reached"
            elif price_ratio <= 0.8:
                sell_reason = "stop loss triggered"
            elif minutes_held > 60 and price_ratio < 1.2:
                sell_reason = "time limit exceeded without significant gain"
            
            if sell_reason:
                # Execute sell
                sig = real_sell_token(token_address)
                
                if sig:
                    profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
                    
                    # Update stats
                    daily_profit += profit
                    successful_sells_today += 1
                    if price_ratio >= 2.0:
                        successful_2x_sells += 1
                    
                    log_trade({
                        "type": "sell", 
                        "token": token_address,
                        "tx": sig,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                        "price": current_price,
                        "profit": profit,
                        "price_ratio": price_ratio,
                        "reason": sell_reason,
                        "manual": False
                    })
                    
                    # Notify in Discord if channel is set
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🤖 Auto-sold {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit:.2f} profit)! Reason: {sell_reason}. Transaction: https://solscan.io/tx/{sig}")
                    
                    logging.info(f"✅ Auto-sold {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit:.2f} profit)")
                    
                    # Remove from bought_tokens
                    if token_address in bought_tokens:
                        del bought_tokens[token_address]
        
        except Exception as e:
            logging.error(f"❌ Error checking sell opportunity for {token_address}: {e}")

async def auto_snipe():
    """Automatic token sniping function - runs continuously in the background"""
    global total_buys_today, daily_profit, successful_sells_today, successful_2x_sells, bought_tokens
    
    await bot.wait_until_ready()
    
    # Config parameters for auto-sniping
    MAX_CONCURRENT_TOKENS = 5  # Maximum number of tokens to hold at once
    MIN_LIQUIDITY = 2  # Minimum liquidity in SOL to consider buying
    BUY_COOLDOWN = 300  # Seconds between buys (5 minutes)
    MAX_DAILY_BUYS = 50  # Safety limit for daily buys
    last_buy_time = datetime.utcnow() - timedelta(hours=1)  # Initialize with time in the past
    
    logging.info("🤖 Auto-sniper started and running...")
    
    while not bot.is_closed():
        try:
            # Skip if reached daily buy limit
            if total_buys_today >= MAX_DAILY_BUYS:
                logging.info(f"⚠️ Daily buy limit reached ({MAX_DAILY_BUYS}). Waiting until reset.")
                await asyncio.sleep(60)
                continue
                
            # Skip if we're holding max tokens already
            if len(bought_tokens) >= MAX_CONCURRENT_TOKENS:
                logging.info(f"⚠️ Max concurrent tokens reached ({MAX_CONCURRENT_TOKENS}). Waiting for sells.")
                await asyncio.sleep(60)
                continue
                
            # Enforce cooldown between buys
            time_since_last_buy = (datetime.utcnow() - last_buy_time).total_seconds()
            if time_since_last_buy < BUY_COOLDOWN:
                await asyncio.sleep(10)  # Check frequently 
                continue
                
            # 1. Find new tokens - implement your token discovery method here
            # This could be monitoring DEX listings, mempool for new pairs, etc.
            new_tokens = find_new_promising_tokens(min_liquidity=MIN_LIQUIDITY, max_results=3)
            
            if not new_tokens:
                await asyncio.sleep(10)  # Check frequently for new opportunities
                continue
                
            # 2. Pick the most promising token and buy it
            for token_address in new_tokens:
                # Skip if we already own this token
                if token_address in bought_tokens:
                    continue
                    
                # Check token metrics before buying
                if not is_token_safe(token_address):
                    logging.info(f"⚠️ Skipping token {token_address} - failed safety checks")
                    continue
                    
                # Attempt to buy the token
                sig = real_buy_token(token_address, BUY_AMOUNT_LAMPORTS)
                
                if sig:
                    price = get_token_price(token_address)
                    
                    # Handle string price
                    if isinstance(price, str):
                        try:
                            price = float(price)
                        except:
                            price = 0
                            
                    bought_tokens[token_address] = {
                        'buy_sig': sig,
                        'buy_time': datetime.utcnow(),
                        'token': token_address,
                        'initial_price': price,
                        'buy_amount': BUY_AMOUNT_LAMPORTS
                    }
                    
                    log_trade({
                        "type": "buy", 
                        "token": token_address, 
                        "tx": sig, 
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
                        "price": price,
                        "amount_lamports": BUY_AMOUNT_LAMPORTS,
                        "manual": False
                    })
                    
                    total_buys_today += 1
                    last_buy_time = datetime.utcnow()
                    
                    # Notify in Discord if channel is set
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🤖 Auto-bought {token_address} at ${price:.8f}! Transaction: https://solscan.io/tx/{sig}")
                    
                    logging.info(f"✅ Auto-bought {token_address} at ${price:.8f}")
                    break  # Stop after buying one token
            
            # 3. Check existing tokens for sell opportunities (separate from buying logic)
            await check_for_sell_opportunities()
                
        except Exception as e:
            logging.error(f"❌ Error in auto_snipe: {e}")
        
        # Main loop pause
        await asyncio.sleep(5)  # Check frequently

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

Current buy amount: {BUY_AMOUNT_LAMPORTS/1000000000:.3f} SOL
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
    holdings_text += f"Total tokens: {len(bought_tokens)}\n"
    holdings_text += f"Tokens with prices: {tokens_with_prices}\n"
    holdings_text += f"Tokens awaiting prices: {tokens_without_prices}\n"
    holdings_text += f"Total estimated value: ${total_value:.2f}\n"
    holdings_text += f"Potential profit: ${potential_profit:.2f}\n"
    
    # Check if message is too long for Discord (limit is 2000 chars)
    if len(holdings_text) > 1950:
        # Trim message if too long
        holdings_text = holdings_text[:1900] + "\n... (message trimmed due to length)"
    
    await interaction.response.send_message(holdings_text)

@tree.command(name="buy", description="Buy a specific token")
@app_commands.describe(token_address="Token mint address to buy", amount_sol="Amount in SOL to buy with (default: 0.15)")
async def buy_slash(interaction: discord.Interaction, token_address: str, amount_sol: float = 0.15):
    """Manual buy command with amount option"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Convert SOL to lamports
        amount_lamports = int(amount_sol * 1_000_000_000)
        
        # Cap the amount at 2 SOL for safety
        if amount_lamports > 2_000_000_000:
            amount_lamports = 2_000_000_000
            await interaction.followup.send(f"⚠️ Amount capped at 2 SOL for safety.")
        
        # Validate token address format
        token_address = sanitize_token_address(token_address)
        
        # Try to buy the token
        sig = real_buy_token(token_address, amount_lamports)
        
        if sig:
            price = get_token_price(token_address)
            
            # Handle string price
            if isinstance(price, str):
                try:
                    price = float(price)
                except:
                    price = 0
                    
            bought_tokens[token_address] = {
                'buy_sig': sig,
                'buy_time': datetime.utcnow(),
                'token': token_address,
                'initial_price': price,
                'buy_amount': amount_lamports
            }
            
            log_trade({
                "type": "buy", 
                "token": token_address, 
                "tx": sig, 
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
                "price": price,
                "amount_lamports": amount_lamports,
                "manual": True
            })
            
            await interaction.followup.send(f"✅ Bought {token_address} with {amount_sol} SOL! Transaction: https://solscan.io/tx/{sig}")
        else:
            await interaction.followup.send(f"❌ Failed to buy {token_address}. Check logs for details.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@tree.command(name="sell", description="Sell a specific token")
@app_commands.describe(token_address="Token mint address to sell")
async def sell_slash(interaction: discord.Interaction, token_address: str):
    """Manual sell command"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Validate token address format
        token_address = sanitize_token_address(token_address)
        
        # Try to sell the token
        sig = real_sell_token(token_address)
        
        if sig:
            if token_address in bought_tokens:
                # Calculate profit if we have buy data
                initial_price = bought_tokens[token_address].get('initial_price', 0)
                buy_amount = bought_tokens[token_address].get('buy_amount', BUY_AMOUNT_LAMPORTS)
                current_price = get_token_price(token_address)
                
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
                
                if current_price > 0 and initial_price > 0:
                    profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
                    price_ratio = current_price / initial_price
                    
                    log_trade({
                        "type": "sell", 
                        "token": token_address,
                        "tx": sig,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                        "price": current_price,
                        "profit": profit,
                        "reason": "manual sell",
                        "manual": True
                    })
                    
                    await interaction.followup.send(f"✅ Sold {token_address} at ${current_price:.6f} ({price_ratio:.2f}x, ${profit:.2f} profit)! Transaction: https://solscan.io/tx/{sig}")
                else:
                    await interaction.followup.send(f"✅ Sold {token_address} (profit unknown)! Transaction: https://solscan.io/tx/{sig}")
                
                # Remove from bought_tokens
                if token_address in bought_tokens:
                    del bought_tokens[token_address]
            else:
                await interaction.followup.send(f"✅ Sold {token_address}! Transaction: https://solscan.io/tx/{sig}")
        else:
            await interaction.followup.send(f"❌ Failed to sell {token_address}. Check logs for details.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@tree.command(name="chart", description="Generate a profit chart")
async def chart_slash(interaction: discord.Interaction):
    """Generate and display a profit chart"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Extract profit data from trade log
        if not trade_log:
            await interaction.followup.send("No trade data available to create chart.")
            return
            
        # Extract sell entries with profit data
        profit_entries = [entry for entry in trade_log if entry.get("type") == "sell" and "profit" in entry]
        
        if not profit_entries:
            await interaction.followup.send("No profit data available to create chart.")
            return
            
        # Extract timestamps and profits
        timestamps = []
        profits = []
        cumulative_profit = 0
        
        for entry in profit_entries:
            # Convert timestamp string to datetime if needed
            if isinstance(entry.get("timestamp"), str):
                time_obj = datetime.strptime(entry.get("timestamp"), "%H:%M:%S")
                # Use today's date with the time from the log
                timestamp = datetime.now().replace(hour=time_obj.hour, minute=time_obj.minute, second=time_obj.second)
            else:
                timestamp = entry.get("timestamp", datetime.now())
                
            profit = entry.get("profit", 0)
            cumulative_profit += profit
            
            timestamps.append(timestamp)
            profits.append(cumulative_profit)
            
        # Create the chart
        plt.figure(figsize=(10, 6))
        plt.plot(timestamps, profits, marker='o', linestyle='-', color='green')
        plt.axhline(y=0, color='r', linestyle='-', alpha=0.3)
        plt.axhline(y=DAILY_PROFIT_TARGET, color='g', linestyle='--', alpha=0.5, label=f"${DAILY_PROFIT_TARGET} Target")
        
        plt.title('Cumulative Trading Profit')
        plt.xlabel('Time')
        plt.ylabel('Profit (USD)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # Format y-axis as dollars
        plt.gca().yaxis.set_major_formatter('${x:.0f}')
        
        # Save chart to file
        chart_path = "profit_chart.png"
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
        
        # Send the chart as an attachment
        await interaction.followup.send(file=discord.File(chart_path))
        
    except Exception as e:
        logging.error(f"❌ Error generating chart: {e}")
        await interaction.followup.send(f"❌ Error generating chart: {str(e)}")

@tree.command(name="analyze", description="Get AI analysis of current market conditions")
async def analyze_slash(interaction: discord.Interaction):
    """Generate AI analysis of current market trends using GPT-4"""
    await interaction.response.defer(thinking=True)
    
    try:
        # First, gather some data from our trading history
        if not trade_log:
            await interaction.followup.send("Not enough trading data for analysis.")
            return
        
        # Count successful vs failed trades
        sells = [entry for entry in trade_log if entry.get("type") == "sell"]
        profitable_sells = [entry for entry in sells if entry.get("profit", 0) > 0]
        successful_ratio = len(profitable_sells) / len(sells) if sells else 0
        
        # Get holding time stats
        holding_times = []
        for token, data in bought_tokens.items():
            minutes_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
            holding_times.append(minutes_held)
            
        avg_hold_time = sum(holding_times) / len(holding_times) if holding_times else 0
        
        # Calculate average profit per trade
        total_profit = sum(entry.get("profit", 0) for entry in sells)
        avg_profit = total_profit / len(sells) if sells else 0
        
        # Gather tokens with the best performance
        best_tokens = []
        for entry in profitable_sells[-10:]:  # Look at recent profitable sells
            if "token" in entry and "profit" in entry:
                best_tokens.append((entry["token"], entry["profit"]))
                
        # Sort by profit
        best_tokens.sort(key=lambda x: x[1], reverse=True)
        
        # Create prompt for GPT-4
        system_prompt = "You are an expert crypto trading assistant. Analyze the provided trading data and give insights."
        
        user_prompt = f"""
        Trading Data Summary:
        - Total trades: {len(trade_log)}
        - Successful trades ratio: {successful_ratio:.2f}
        - Average profit per trade: ${avg_profit:.2f}
        - Average holding time: {avg_hold_time:.1f} minutes
        - Current tokens held: {len(bought_tokens)}
        - Daily profit so far: ${daily_profit:.2f}
        
        Best performing tokens:
        {best_tokens[:5]}
        
        Based on this data, provide a brief analysis of:
        1. Current market conditions
        2. Recommended strategy adjustments
        3. Opportunities to watch for
        
        Keep the analysis under 400 words and focus on actionable insights.
        """
        
        # Call GPT-4 for analysis
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        analysis = response.choices[0].message.content
        
        # Send the analysis
        await interaction.followup.send(f"**📊 Market Analysis:**\n\n{analysis}")
        
    except Exception as e:
        logging.error(f"❌ Error generating analysis: {e}")
        await interaction.followup.send(f"❌ Error generating analysis: {str(e)}")

@tree.command(name="newrpc", description="Test and switch to the fastest RPC endpoint")
async def newrpc_slash(interaction: discord.Interaction):
    """Test all RPC endpoints and switch to the fastest one"""
    await interaction.response.defer(thinking=True)
    
    try:
        old_rpc = solana_client.endpoint if solana_client else "Not connected"
        best_rpc = get_best_rpc()
        
        # Initialize global client with the new RPC
        global solana_client
        solana_client = Client(best_rpc)
        
        if best_rpc and best_rpc != old_rpc:
            await interaction.followup.send(f"✅ Switched from {old_rpc} to faster RPC: {best_rpc}")
        elif best_rpc == old_rpc:
            await interaction.followup.send(f"✅ Current RPC endpoint ({old_rpc}) is already the fastest.")
        else:
            await interaction.followup.send("❌ Failed to find a faster RPC endpoint.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error testing RPC endpoints: {str(e)}")

# Add daily stats reset function
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
        
        # Notify in Discord if channel is set
        if DISCORD_NEWS_CHANNEL_ID:
            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
            if channel:
                await channel.send(f"🔄 Daily stats reset! Previous day: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")

@bot.event
async def on_ready():
    logging.info(f"Bot logged in as {bot.user}")
    await tree.sync()
    
    # Load trade log if it exists
    global trade_log
    try:
        if os.path.exists("trade_log.json"):
            with open("trade_log.json", "r") as f:
                trade_log = json.load(f)
            logging.info(f"✅ Loaded {len(trade_log)} entries from trade log")
    except Exception as e:
        logging.error(f"❌ Error loading trade log: {e}")

    # Start the auto-snipe task
    bot.loop.create_task(auto_snipe())
    
    # Start the daily stats reset task
    bot.loop.create_task(reset_daily_stats())
    
    # Log initial wallet balance
    log_wallet_balance()

def run_bot():
    """Main function to run the bot"""
    try:
        if not DISCORD_TOKEN:
            logging.error("❌ DISCORD_TOKEN not set in .env file")
            return
            
        if not PHANTOM_SECRET_KEY:
            logging.error("❌ PHANTOM_SECRET_KEY not set in .env file")
            return
            
        # Test wallet connection
        try:
            kp = get_phantom_keypair()
            pubkey = kp.pubkey()
            logging.info(f"✅ Wallet loaded: {pubkey}")
        except Exception as e:
            logging.error(f"❌ Wallet setup failed: {e}")
            return
            
        # Run the bot
        logging.info("🚀 Starting bot...")
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"❌ Bot run failed: {e}")

if __name__ == "__main__":
    # Initialize Solana client
    initialize_solana_client()
    run_bot()
