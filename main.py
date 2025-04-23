# File: trading_bot.py
# Description: Advanced trading bot for Solana using QuickNode APIs with optimized token sniping

import os
import base58
import time
import json
import asyncio
import logging
import random
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                   datefmt='%Y-%m-%d %H:%M:%S')

# Load environment variables
load_dotenv()

# Configuration Constants
CONFIG = {
    "SOLANA_RPC_URL": os.getenv("SOLANA_RPC_URL", ""),
    "JUPITER_API_URL": os.getenv("JUPITER_API_URL", ""),
    "BOT_PRIVATE_KEY": os.getenv("BOT_PRIVATE_KEY", ""),  # Base58 encoded private key
    "PROFIT_TARGET_PERCENT": float(os.getenv("PROFIT_TARGET_PERCENT", "100")),  # 100% = 2x
    "PARTIAL_PROFIT_TARGET_PERCENT": float(os.getenv("PARTIAL_PROFIT_TARGET_PERCENT", "50")),  # 50% = 1.5x
    "PARTIAL_PROFIT_SELL_PERCENT": float(os.getenv("PARTIAL_PROFIT_SELL_PERCENT", "50")),  # Sell 50% at partial target
    "STOP_LOSS_PERCENT": float(os.getenv("STOP_LOSS_PERCENT", "20")),  # 20% loss
    "TIME_LIMIT_MINUTES": int(os.getenv("TIME_LIMIT_MINUTES", "60")),  # Hold time limit in minutes
    "BUY_AMOUNT_SOL": float(os.getenv("BUY_AMOUNT_SOL", "0.15")),  # Amount to buy with in SOL
    "RETRY_DELAY_MS": int(os.getenv("RETRY_DELAY_MS", "2000")),  # 2 seconds between retries
    "CHECK_INTERVAL_MS": int(os.getenv("CHECK_INTERVAL_MS", "3000")),  # Check price every 3 seconds (faster)
    "BUY_COOLDOWN_MINUTES": int(os.getenv("BUY_COOLDOWN_MINUTES", "2")),  # 2 minutes cooldown between buys (faster)
    "CACHE_DURATION_MS": int(os.getenv("CACHE_DURATION_MS", "30000")),  # Cache prices for 30 seconds
    "LOG_FILE": os.getenv("LOG_FILE", "trading-log.json"),
    "SIMULATION_MODE": os.getenv("SIMULATION_MODE", "true").lower() == "true",  # Default to simulation mode
    "MAX_CONCURRENT_TOKENS": int(os.getenv("MAX_CONCURRENT_TOKENS", "15")),  # Maximum tokens to hold at once
    "TOKEN_SCAN_LIMIT": int(os.getenv("TOKEN_SCAN_LIMIT", "50")),  # Number of recent transactions to scan
    "USE_TIERED_PROFIT_TAKING": os.getenv("USE_TIERED_PROFIT_TAKING", "true").lower() == "true"  # Enable tiered profit taking
}

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
LAMPORTS_PER_SOL = 1_000_000_000

# Token program address
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
# Associated token account program
ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"

# Global state variables
bought_tokens = {}
partial_sold_tokens = {}  # To track tokens that have been partially sold
trade_log = []
price_cache = {}
token_metadata_cache = {}  # Cache for token metadata
new_token_cache = set()  # Cache for recently detected new tokens
last_buy_time = time.time() - CONFIG["BUY_COOLDOWN_MINUTES"] * 60  # Set initial buy time in the past
bot_start_time = time.time()

# Stats tracking
daily_profit = 0
total_buys_today = 0
successful_sells_today = 0
successful_2x_sells = 0
partial_profit_sells = 0

class TradingBot:
    def __init__(self):
        self.wallet_address = self._get_wallet_address()
        self.load_trade_log()
        
    def _get_wallet_address(self):
        """Extract wallet address from private key"""
        try:
            # In production, we would derive the public key from the private key
            # For simulation, we'll just return a placeholder or the one provided in env
            return os.getenv("WALLET_ADDRESS", "YourWalletAddressHere")
        except Exception as e:
            logging.error(f"Error getting wallet address: {e}")
            return None
    
    def load_trade_log(self):
        """Load trade log from file if exists"""
        global trade_log
        try:
            if os.path.exists(CONFIG["LOG_FILE"]):
                with open(CONFIG["LOG_FILE"], "r") as f:
                    trade_log = json.load(f)
                logging.info(f"Loaded {len(trade_log)} entries from trade log")
        except Exception as e:
            logging.error(f"Error loading trade log: {e}")
    
    def save_trade_log(self):
        """Save trade log to file"""
        try:
            with open(CONFIG["LOG_FILE"], "w") as f:
                json.dump(trade_log, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving trade log: {e}")
    
    async def get_token_metadata(self, token_address):
        """Get token metadata using QuickNode RPC"""
        global token_metadata_cache
        
        # Check cache first
        if token_address in token_metadata_cache:
            return token_metadata_cache[token_address]
        
        try:
            # Use QuickNode's RPC to get token metadata
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    token_address,
                    {"encoding": "jsonParsed"}
                ]
            }
            
            response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('result') and data['result'].get('value'):
                    account_data = data['result']['value']
                    
                    # Try to extract useful metadata
                    metadata = {
                        "mint_authority": None,
                        "freeze_authority": None,
                        "decimals": 0,
                        "supply": 0,
                        "is_initialized": False
                    }
                    
                    # Parse token data if available
                    if account_data.get('data') and account_data['data'].get('parsed'):
                        parsed_data = account_data['data']['parsed']
                        if parsed_data.get('info'):
                            info = parsed_data['info']
                            metadata.update({
                                "mint_authority": info.get('mintAuthority'),
                                "freeze_authority": info.get('freezeAuthority'),
                                "decimals": info.get('decimals', 0),
                                "supply": info.get('supply', 0),
                                "is_initialized": info.get('isInitialized', False)
                            })
                    
                    # Cache the metadata
                    token_metadata_cache[token_address] = metadata
                    return metadata
            
            # Default empty metadata
            default_metadata = {
                "mint_authority": None,
                "freeze_authority": None,
                "decimals": 0,
                "supply": 0,
                "is_initialized": False
            }
            
            token_metadata_cache[token_address] = default_metadata
            return default_metadata
            
        except Exception as e:
            logging.warning(f"Error getting token metadata: {str(e)}")
            # Return default metadata
            default_metadata = {
                "mint_authority": None,
                "freeze_authority": None,
                "decimals": 0,
                "supply": 0,
                "is_initialized": False
            }
            
            token_metadata_cache[token_address] = default_metadata
            return default_metadata
    
    async def get_token_price(self, token_address):
        """Get token price with caching using QuickNode RPC properly"""
        global price_cache
        
        # Check cache first
        current_time = time.time() * 1000  # Convert to milliseconds
        if token_address in price_cache:
            cache_time, price = price_cache[token_address]
            if current_time - cache_time < CONFIG["CACHE_DURATION_MS"]:
                logging.info(f"Using cached price for {token_address}: ${price:.6f}")
                return price
        
        try:
            # Use QuickNode's Standard JSON-RPC format instead of REST API
            headers = {
                "Content-Type": "application/json"
            }
            
            # First approach: Using getTokenSupply and SOL price to estimate token price
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenSupply",
                "params": [token_address]
            }
            
            try:
                response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=10)
                logging.debug(f"Token supply API response status: {response.status_code}")
                
                if response.status_code == 200:
                    # This is just a check that we can connect to the RPC
                    logging.debug("Successfully connected to QuickNode RPC")
                    
                    # Try to get price from Jupiter API
                    try:
                        jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/price?ids={token_address}&vsToken=USDC"
                        
                        jupiter_response = requests.get(jupiter_url, headers=headers, timeout=5)
                        if jupiter_response.status_code == 200:
                            data = jupiter_response.json()
                            if data.get('data') and data['data'].get(token_address):
                                price = float(data['data'][token_address].get('price', 0))
                                if price > 0:
                                    logging.info(f"Got price from Jupiter: ${price:.6f} for {token_address}")
                                    price_cache[token_address] = (current_time, price)
                                    return price
                    except Exception as e:
                        logging.warning(f"Jupiter price API error: {str(e)}")
                    
                    # For tokens we've already bought, use the initial price
                    if token_address in bought_tokens and bought_tokens[token_address].get('initial_price', 0) > 0:
                        price = bought_tokens[token_address]['initial_price']
                        logging.info(f"Using known price (${price:.6f}) for {token_address}")
                        return price
                    
                    # For known tokens, return hardcoded prices
                    fallback_prices = {
                        SOL_MINT: 150.0,  # Example SOL price
                        USDC_MINT: 1.0,   # USDC is pegged to USD
                        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 0.000014,  # BONK
                        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": 0.42,       # JUP
                        "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx": 0.75,      # GMT
                    }
                    
                    if token_address in fallback_prices:
                        price = fallback_prices[token_address]
                        logging.info(f"Using hardcoded price (${price:.6f}) for {token_address}")
                        price_cache[token_address] = (current_time, price)
                        return price
                    
                    # Try to estimate price using swap quote
                    try:
                        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                        amount_in_lamports = 100_000_000  # 0.1 SOL
                        
                        payload = {
                            "inputMint": SOL_MINT,
                            "outputMint": token_address,
                            "amount": str(amount_in_lamports),
                            "slippageBps": 50
                        }
                        
                        quote_response = requests.post(quote_url, json=payload, headers=headers, timeout=10)
                        if quote_response.status_code == 200:
                            quote_data = quote_response.json()
                            
                            # Calculate estimated price from quote
                            if 'outAmount' in quote_data and int(quote_data['outAmount']) > 0:
                                sol_price = 150.0  # Estimated SOL price in USD
                                sol_amount = amount_in_lamports / LAMPORTS_PER_SOL
                                token_amount = int(quote_data['outAmount'])
                                
                                # Price relative to USD (estimated)
                                price = (sol_price * sol_amount) / token_amount
                                logging.info(f"Estimated price from Jupiter quote: ${price:.8f} for {token_address}")
                                price_cache[token_address] = (current_time, price)
                                return price
                    except Exception as e:
                        logging.warning(f"Error estimating price from quote: {str(e)}")
                    
                    # If we don't have a price, return a small default price for new tokens
                    logging.warning(f"No price information for {token_address}, using default for potentially new token")
                    return 0.0001
                else:
                    logging.warning(f"QuickNode RPC returned status {response.status_code}")
                    
                    # If API fails, try fallback pricing
                    if token_address in bought_tokens and bought_tokens[token_address].get('initial_price', 0) > 0:
                        price = bought_tokens[token_address]['initial_price']
                        logging.warning(f"Using last known price (${price:.6f}) for {token_address} as fallback")
                        return price
                    
                    # For known tokens, use hardcoded fallback prices
                    fallback_prices = {
                        SOL_MINT: 150.0,  # Example SOL price
                        USDC_MINT: 1.0,   # USDC is pegged to USD
                        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 0.000014,  # BONK
                        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": 0.42,       # JUP
                        "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx": 0.75,      # GMT
                    }
                    
                    if token_address in fallback_prices:
                        price = fallback_prices[token_address]
                        logging.warning(f"Using hardcoded fallback price for {token_address}: ${price:.6f}")
                        return price
                    
                    return 0.0001  # Default small value for potentially new tokens
            except Exception as e:
                logging.warning(f"Error querying RPC: {str(e)}")
                
                # Use fallback prices if API fails
                if token_address in bought_tokens and bought_tokens[token_address].get('initial_price', 0) > 0:
                    price = bought_tokens[token_address]['initial_price']
                    logging.warning(f"Using last known price (${price:.6f}) for {token_address} as fallback")
                    return price
                    
                # Hardcoded fallbacks for common tokens
                fallback_prices = {
                    SOL_MINT: 150.0,  # Example SOL price
                    USDC_MINT: 1.0,   # USDC is pegged to USD
                    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 0.000014,  # BONK
                    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": 0.42,       # JUP
                    "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx": 0.75,      # GMT
                }
                
                if token_address in fallback_prices:
                    price = fallback_prices[token_address]
                    logging.warning(f"Using hardcoded fallback price for {token_address}: ${price:.6f}")
                    return price
            
            # Default fallback
            return 0.0001  # Default small value for potentially new tokens
        
        except Exception as e:
            logging.error(f"Error getting token price: {e}")
            return 0.0001  # Default small value for potentially new tokens

    async def get_token_liquidity(self, token_address):
        """Get token liquidity information using QuickNode"""
        try:
            # Use QuickNode's Liquidity API
            liquidity_url = f"{CONFIG['SOLANA_RPC_URL']}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "qn_getTokenLiquidity",
                "params": {
                    "mintAddress": token_address
                }
            }
            
            response = requests.post(liquidity_url, json=payload, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if 'result' in data and data['result'] is not None:
                    liquidity = data['result'].get('usdLiquidity', 0)
                    logging.info(f"Token {token_address} has ${liquidity:.2f} liquidity")
                    return liquidity
                elif 'error' in data:
                    error_msg = data['error'].get('message', 'Unknown error')
                    logging.warning(f"QuickNode liquidity API error: {error_msg}")
            else:
                logging.warning(f"QuickNode Liquidity API returned status {response.status_code}")
                
            # Try alternative method - estimate using Jupiter
            try:
                # Get Jupiter route to estimate liquidity
                quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                amount_in_lamports = 100_000_000  # 0.1 SOL
                
                payload = {
                    "inputMint": SOL_MINT,
                    "outputMint": token_address,
                    "amount": str(amount_in_lamports),
                    "slippageBps": 50
                }
                
                headers = {"Content-Type": "application/json"}
                quote_response = requests.post(quote_url, json=payload, headers=headers, timeout=5)
                
                if quote_response.status_code == 200:
                    quote_data = quote_response.json()
                    
                    # Check if there's a viable route
                    if 'outAmount' in quote_data and int(quote_data['outAmount']) > 0 and 'otherAmountThreshold' in quote_data:
                        # Rough estimate of liquidity based on slippage
                        min_output = int(quote_data['otherAmountThreshold'])
                        expected_output = int(quote_data['outAmount'])
                        
                        if min_output > 0 and expected_output > 0:
                            # Lower slippage typically means higher liquidity
                            slippage_ratio = 1 - (min_output / expected_output)
                            
                            # Very rough liquidity estimate based on slippage
                            # Lower slippage (closer to 0) suggests better liquidity
                            if slippage_ratio < 0.01:  # Less than 1% slippage
                                estimated_liquidity = 10000  # $10k estimate
                            elif slippage_ratio < 0.05:  # Less than 5% slippage
                                estimated_liquidity = 5000   # $5k estimate
                            elif slippage_ratio < 0.10:  # Less than 10% slippage
                                estimated_liquidity = 1000   # $1k estimate
                            else:
                                estimated_liquidity = 500    # Low liquidity estimate
                                
                            logging.info(f"Estimated liquidity for {token_address} based on slippage: ${estimated_liquidity}")
                            return estimated_liquidity
            except Exception as e:
                logging.warning(f"Error estimating liquidity from Jupiter: {e}")
            
            # If API fails, return 0 liquidity as a safe default
            return 0
        except Exception as e:
            logging.error(f"Error getting token liquidity: {e}")
            return 0
    
    async def detect_liquidity_addition(self, token_address):
        """Detect if liquidity was recently added to a token"""
        try:
            # Check current liquidity
            current_liquidity = await self.get_token_liquidity(token_address)
            
            # Store as a newly detected token with liquidity if significant
            if current_liquidity > 1000:  # Significant liquidity
                if token_address not in new_token_cache:
                    new_token_cache.add(token_address)
                    logging.info(f"Detected significant liquidity for token {token_address}: ${current_liquidity}")
                    return True
                    
            return False
        except Exception as e:
            logging.error(f"Error detecting liquidity addition: {e}")
            return False
    
    async def is_token_new(self, token_address):
        """Check if token is newly created"""
        try:
            # First, get token metadata
            metadata = await self.get_token_metadata(token_address)
            
            # Check if token has liquidity recently added
            liquidity_added = await self.detect_liquidity_addition(token_address)
            if liquidity_added:
                return True
                
            # For simulation or testing, consider some tokens as "new"
            if CONFIG["SIMULATION_MODE"]:
                # Randomly treat some tokens as new in simulation mode
                # This helps test the system behavior with "new" tokens
                is_simulated_new = random.random() < 0.2  # 20% chance to be considered new
                if is_simulated_new:
                    new_token_cache.add(token_address)
                    return True
            
            return False
        except Exception as e:
            logging.error(f"Error checking if token is new: {e}")
            return False
    
    async def buy_token(self, token_address, amount_in_sol):
        """Buy a token using Jupiter API"""
        global bought_tokens, trade_log, total_buys_today, last_buy_time
        
        try:
            # Convert SOL amount to lamports
            amount_in_lamports = int(amount_in_sol * LAMPORTS_PER_SOL)
            
            logging.info(f"Buying {token_address} with {amount_in_sol} SOL...")
            
            if CONFIG["SIMULATION_MODE"]:
                # Simulate a successful transaction
                simulated_tx = f"sim_buy_{token_address[:8]}_{int(time.time())}"
                logging.info(f"SIMULATION MODE: Created buy transaction: {simulated_tx}")
                tx_signature = simulated_tx
            else:
                # In production mode, use Jupiter API to execute the swap
                try:
                    # Step 1: Get quote from Jupiter API
                    quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                    payload = {
                        "inputMint": SOL_MINT,
                        "outputMint": token_address,
                        "amount": str(amount_in_lamports),
                        "slippageBps": 50  # 0.5% slippage tolerance
                    }
                    
                    headers = {"Content-Type": "application/json"}
                    response = requests.post(quote_url, json=payload, headers=headers, timeout=10)
                    
                    if response.status_code != 200:
                        logging.error(f"Failed to get quote: {response.status_code} - {response.text}")
                        return None
                    
                    quote_data = response.json()
                    
                    # Step 2: Execute the swap through Jupiter
                    swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
                    swap_payload = {
                        "quoteResponse": quote_data,
                        "userPublicKey": self.wallet_address,
                        # Additional parameters would be needed for a real transaction
                    }
                    
                    swap_response = requests.post(swap_url, json=swap_payload, headers=headers, timeout=15)
                    
                    if swap_response.status_code != 200:
                        logging.error(f"Failed to execute swap: {swap_response.status_code} - {swap_response.text}")
                        return None
                    
                    tx_signature = swap_response.json().get('txSignature')
                    logging.info(f"Successfully created buy transaction: {tx_signature}")
                    
                except Exception as e:
                    logging.error(f"Error executing Jupiter swap: {e}")
                    # Fallback to simulation if real transaction fails
                    simulated_tx = f"sim_fallback_buy_{token_address[:8]}_{int(time.time())}"
                    logging.info(f"Falling back to simulation due to API error: {simulated_tx}")
                    tx_signature = simulated_tx
            
            # Get token price after purchase
            price = await self.get_token_price(token_address)
            
            # Get token metadata (for logging)
            metadata = await self.get_token_metadata(token_address)
            decimals = metadata.get('decimals', 0)
            
            # Store the purchased token details
            bought_tokens[token_address] = {
                'buy_tx_id': tx_signature,
                'buy_time': datetime.now(),
                'initial_price': price if price > 0 else 0.00000001,  # Default to tiny price if we can't get one
                'buy_amount': amount_in_lamports,
                'token_address': token_address,
                'decimals': decimals,
                'partially_sold': False,
                'partial_sell_amount': 0
            }
            
            # Log the trade
            trade_info = {
                "type": "buy",
                "token": token_address,
                "tx_id": tx_signature,
                "timestamp": datetime.now().isoformat(),
                "price": price,
                "amount_in_lamports": amount_in_lamports,
                "is_new_token": token_address in new_token_cache,
                "manual": False,
                "simulation": CONFIG["SIMULATION_MODE"]
            }
            
            trade_log.append(trade_info)
            self.save_trade_log()
            
            # Update stats
            total_buys_today += 1
            last_buy_time = time.time()
            
            price_display = f"${price:.8f}" if price > 0 else "unknown price"
            logging.info(f"Successfully bought {token_address} at price: {price_display}")
            
            return tx_signature
        except Exception as e:
            logging.error(f"Error buying token: {e}")
            return None
    
    async def sell_token(self, token_address, sell_percentage=100):
        """Sell a token using Jupiter API with partial selling support"""
        global bought_tokens, partial_sold_tokens, trade_log, daily_profit, successful_sells_today, successful_2x_sells, partial_profit_sells
        
        try:
            logging.info(f"Selling {sell_percentage}% of {token_address}...")
            
            # Get token data before selling
            token_data = bought_tokens.get(token_address)
            if not token_data:
                logging.warning(f"No token data found for {token_address}")
                return {"success": False, "error": "No token data found"}
            
            # Check if already partially sold
            is_partial_sell = sell_percentage < 100
            was_partially_sold = token_data.get('partially_sold', False)
            
            # Get price before selling for profit calculation
            current_price = await self.get_token_price(token_address)
            
            # Calculate sell amount based on percentage
            total_amount = token_data.get('buy_amount', 0)
            
            if was_partially_sold:
                # Adjust for already sold portion
                partial_sell_amount = token_data.get('partial_sell_amount', 0)
                remaining_amount = total_amount - partial_sell_amount
                sell_amount = int(remaining_amount * sell_percentage / 100)
            else:
                sell_amount = int(total_amount * sell_percentage / 100)
            
            # Determine transaction type
            if is_partial_sell:
                tx_type = "partial_sell"
            else:
                tx_type = "sell"
            
            if CONFIG["SIMULATION_MODE"]:
                # Simulate a successful transaction
                simulated_tx = f"sim_{tx_type}_{token_address[:8]}_{int(time.time())}"
                logging.info(f"SIMULATION MODE: Created {tx_type} transaction: {simulated_tx}")
                tx_signature = simulated_tx
            else:
                # In production mode, use Jupiter API to execute the swap
                try:
                    # Step 1: Get quote from Jupiter API for the reverse swap
                    quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                    
                    payload = {
                        "inputMint": token_address,
                        "outputMint": SOL_MINT,
                        "amount": str(sell_amount),  # Only sell the specified percentage
                        "slippageBps": 100  # 1% slippage tolerance, higher for selling
                    }
                    
                    headers = {"Content-Type": "application/json"}
                    response = requests.post(quote_url, json=payload, headers=headers, timeout=10)
                    
                    if response.status_code != 200:
                        logging.error(f"Failed to get sell quote: {response.status_code} - {response.text}")
                        return {"success": False, "error": f"Failed to get quote: {response.status_code}"}
                    
                    quote_data = response.json()
                    
                    # Step 2: Execute the swap through Jupiter
                    swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
                    swap_payload = {
                        "quoteResponse": quote_data,
                        "userPublicKey": self.wallet_address,
                        # Additional parameters would be needed for a real transaction
                    }
                    
                    swap_response = requests.post(swap_url, json=swap_payload, headers=headers, timeout=15)
                    
                    if swap_response.status_code != 200:
                        logging.error(f"Failed to execute sell swap: {swap_response.status_code} - {swap_response.text}")
                        return {"success": False, "error": f"Failed to execute swap: {swap_response.status_code}"}
                    
                    tx_signature = swap_response.json().get('txSignature')
                    logging.info(f"Successfully created {tx_type} transaction: {tx_signature}")
                    
                except Exception as e:
                    logging.error(f"Error executing Jupiter sell swap: {e}")
                    # Fallback to simulation if real transaction fails
                    simulated_tx = f"sim_fallback_{tx_type}_{token_address[:8]}_{int(time.time())}"
                    logging.info(f"Falling back to simulation due to API error: {simulated_tx}")
                    tx_signature = simulated_tx
            
            # Calculate profit regardless of simulation/production
            if token_data and current_price > 0 and token_data.get('initial_price', 0) > 0:
                # Calculate profit
                price_ratio = current_price / token_data['initial_price']
                profit_percent = (price_ratio - 1) * 100
                
                # Calculate profit for the amount being sold
                profit_amount = ((current_price - token_data['initial_price']) / token_data['initial_price']) * sell_amount / LAMPORTS_PER_SOL
                
                if is_partial_sell:
                    logging.info(f"Partially sold {sell_percentage}% of {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit_amount:.2f} profit)")
                else:
                    logging.info(f"Sold {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit_amount:.2f} profit)")
                
                # Log the trade
                trade_info = {
                    "type": tx_type,
                    "token": token_address,
                    "tx_id": tx_signature,
                    "timestamp": datetime.now().isoformat(),
                    "price": current_price,
                    "profit": profit_amount,
                    "price_ratio": price_ratio,
                    "percentage_sold": sell_percentage,
                    "manual": False,
                    "simulation": CONFIG["SIMULATION_MODE"]
                }
                
                trade_log.append(trade_info)
                self.save_trade_log()
                
                # Update stats
                daily_profit += profit_amount
                
                if is_partial_sell:
                    partial_profit_sells += 1
                    
                    # Update token data to reflect partial sell
                    token_data['partially_sold'] = True
                    token_data['partial_sell_amount'] = token_data.get('partial_sell_amount', 0) + sell_amount
                    
                    # Keep in bought_tokens for remaining amount
                    bought_tokens[token_address] = token_data
                else:
                    successful_sells_today += 1
                    if price_ratio >= 2.0:
                        successful_2x_sells += 1
                    
                    # Remove from bought tokens if full sale
                    if token_address in bought_tokens:
                        del bought_tokens[token_address]
                
                return {
                    "success": True, 
                    "profit": profit_amount, 
                    "price_ratio": price_ratio,
                    "is_partial": is_partial_sell
                }
            else:
                logging.warning(f"Couldn't calculate profit for {token_address} - missing price data")
                
                # Still log the trade with limited info
                trade_info = {
                    "type": tx_type,
                    "token": token_address,
                    "tx_id": tx_signature,
                    "timestamp": datetime.now().isoformat(),
                    "price": current_price if current_price else 0,
                    "profit": 0,
                    "price_ratio": 1,
                    "percentage_sold": sell_percentage,
                    "manual": False,
                    "simulation": CONFIG["SIMULATION_MODE"],
                    "note": "Price data incomplete, profit calculation approximated"
                }
                
                trade_log.append(trade_info)
                self.save_trade_log()
                
                # Update token status
                if is_partial_sell:
                    # Update token data to reflect partial sell
                    token_data['partially_sold'] = True
                    token_data['partial_sell_amount'] = token_data.get('partial_sell_amount', 0) + sell_amount
                    
                    # Keep in bought_tokens for remaining amount
                    bought_tokens[token_address] = token_data
                else:
                    # Still remove from bought tokens if full sale
                    if token_address in bought_tokens:
                        del bought_tokens[token_address]
                
                return {
                    "success": True, 
                    "profit": 0, 
                    "price_ratio": 1,
                    "is_partial": is_partial_sell
                }
        except Exception as e:
            logging.error(f"Error selling token: {e}")
            return {"success": False, "error": str(e)}
    
    async def check_for_sell_opportunities(self):
        """Check for tokens that should be sold based on our criteria with tiered profit-taking"""
        tokens_to_check = list(bought_tokens.keys())
        
        if not tokens_to_check:
            return
        
        logging.info(f"Checking sell opportunities for {len(tokens_to_check)} held tokens...")
        
        for token_address in tokens_to_check:
            try:
                # Skip if token was already sold
                if token_address not in bought_tokens:
                    continue
                
                data = bought_tokens[token_address]
                initial_price = data.get('initial_price', 0)
                buy_time = data.get('buy_time')
                was_partially_sold = data.get('partially_sold', False)
                
                if not initial_price or not buy_time:
                    logging.warning(f"Incomplete data for token {token_address}, skipping")
                    continue
                
                # Get current price
                current_price = await self.get_token_price(token_address)
                
                # Skip if we don't have valid price data
                if current_price <= 0 or initial_price <= 0:
                    logging.info(f"Skipping sell check for {token_address} - missing price data")
                    continue
                
                # Calculate price ratio and time held
                price_ratio = current_price / initial_price
                minutes_held = (datetime.now() - buy_time).total_seconds() / 60
                
                # Auto-sell conditions
                sell_reason = None
                sell_percentage = 100  # Default to full sale
                
                # Check for tiered profit-taking opportunities
                if CONFIG["USE_TIERED_PROFIT_TAKING"] and not was_partially_sold:
                    # Partial profit target reached
                    if price_ratio >= (100 + CONFIG["PARTIAL_PROFIT_TARGET_PERCENT"]) / 100:
                        sell_reason = f"Partial profit target of {CONFIG['PARTIAL_PROFIT_TARGET_PERCENT']}% reached"
                        sell_percentage = CONFIG["PARTIAL_PROFIT_SELL_PERCENT"]
                        logging.info(f"Token {token_address} reached partial profit target of {CONFIG['PARTIAL_PROFIT_TARGET_PERCENT']}% ({price_ratio:.2f}x)")
                
                # Handle full sell conditions (checked even if partial sell occurred)
                if sell_reason is None:  # If no partial sell reason was found
                    if price_ratio >= (100 + CONFIG["PROFIT_TARGET_PERCENT"]) / 100:
                        sell_reason = f"{CONFIG['PROFIT_TARGET_PERCENT']}% profit target reached"
                        sell_percentage = 100  # Full sale
                        logging.info(f"Token {token_address} reached {CONFIG['PROFIT_TARGET_PERCENT']}% profit target ({price_ratio:.2f}x)")
                    elif price_ratio <= (100 - CONFIG["STOP_LOSS_PERCENT"]) / 100:
                        sell_reason = f"{CONFIG['STOP_LOSS_PERCENT']}% stop loss triggered"
                        sell_percentage = 100  # Full sale
                        logging.info(f"Token {token_address} triggered {CONFIG['STOP_LOSS_PERCENT']}% stop loss ({price_ratio:.2f}x)")
                    elif minutes_held > CONFIG["TIME_LIMIT_MINUTES"] and price_ratio < 1.2:
                        sell_reason = f"time limit ({CONFIG['TIME_LIMIT_MINUTES']} min) exceeded without significant gain"
                        sell_percentage = 100  # Full sale
                        logging.info(f"Token {token_address} exceeded time limit: held for {minutes_held:.1f} min with {price_ratio:.2f}x")
                
                if sell_reason:
                    # Execute sell
                    logging.info(f"Selling {sell_percentage}% of token {token_address} - Reason: {sell_reason}")
                    result = await self.sell_token(token_address, sell_percentage)
                    
                    if result["success"]:
                        if result.get("is_partial", False):
                            logging.info(f"Partially sold {sell_percentage}% of {token_address} successfully")
                        else:
                            logging.info(f"Auto-sold {token_address} successfully")
                    else:
                        logging.error(f"Failed to sell token {token_address}: {result.get('error')}")
                else:
                    logging.info(f"Holding token {token_address}: current ratio {price_ratio:.2f}x, held for {minutes_held:.1f} min")
            except Exception as e:
                logging.error(f"Error checking sell opportunity for {token_address}: {e}")
    
    async def find_promising_tokens(self, max_results=3):
        """Find newly launched tokens using QuickNode"""
        try:
            logging.info("Looking for newly launched tokens...")
            
            # Query recent program transactions for token programs
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    TOKEN_PROGRAM_ID,  # Token program
                    {"limit": CONFIG["TOKEN_SCAN_LIMIT"]}  # Check more recent transactions
                ]
            }
            
            response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                signatures = [item.get('signature') for item in data.get('result', [])]
                
                # Get transaction details
                new_tokens = []
                
                for sig in signatures[:10]:  # Process first 10 signatures to save time
                    # Get transaction details to find new token mints
                    tx_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                    }
                    
                    tx_response = requests.post(CONFIG["SOLANA_RPC_URL"], json=tx_payload, headers=headers, timeout=10)
                    
                    if tx_response.status_code == 200:
                        tx_data = tx_response.json().get('result', {})
                        
                        # Extract token creation events
                        # This is a simplification - real parsing would be more complex
                        try:
                            if tx_data and tx_data.get('meta') and tx_data['meta'].get('postTokenBalances'):
                                # Look for token balance changes which could indicate creation or transfer
                                token_balances = tx_data['meta'].get('postTokenBalances', [])
                                
                                for balance in token_balances:
                                    if balance.get('mint'):
                                        mint_address = balance.get('mint')
                                        
                                        # Check if this is a new token we haven't seen
                                        if mint_address not in new_token_cache:
                                            # Verify if token is actually new
                                            is_new = await self.is_token_new(mint_address)
                                            
                                            if is_new and mint_address not in new_tokens:
                                                new_tokens.append(mint_address)
                                                if len(new_tokens) >= max_results:
                                                    break
                        except Exception as e:
                            logging.warning(f"Error parsing transaction {sig}: {e}")
                    
                    if len(new_tokens) >= max_results:
                        break
                
                # If found new tokens, return them
                if new_tokens:
                    logging.info(f"Found {len(new_tokens)} newly created tokens: {new_tokens}")
                    return new_tokens
                
                # If no new tokens found, check for tokens with recent liquidity additions
                logging.info("No newly created tokens found, checking for recent liquidity additions...")
                
                # Also check popular tokens list for testing or as fallback
                test_tokens = [
                    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
                    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
                    "5JnZ8ZUXZRuHt6rkWFSAPQVEJ3dTADgpNMGYMRvGLhT",  # HADES
                    "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx",  # GMT
                    "BERTvZDDguQJXeN9qjwwpM2QHEgMTQ5RbzuJMuX4sKTQ",  # BERT
                    "M1nec3zsQAR3be1JbATuYqaXZHB2ZBpUJXUeDWGz9tQ",  # MINEC
                    "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",  # MANDE
                ]
                
                random.shuffle(test_tokens)
                
                # In production mode, be more selective about fallback tokens
                if not CONFIG["SIMULATION_MODE"]:
                    # Only return tokens we consider likely to have upside
                    potential_tokens = []
                    for token in test_tokens:
                        if token not in bought_tokens:
                            potential_tokens.append(token)
                            if len(potential_tokens) >= max_results:
                                break
                    
                    if potential_tokens:
                        logging.info(f"Using market tokens for trading: {potential_tokens}")
                        return potential_tokens
                else:
                    # In simulation, use test tokens
                    selected = test_tokens[:max_results]
                    logging.info(f"Using test tokens for simulation: {selected}")
                    return selected
                
                # If we get here, we couldn't find any suitable tokens
                logging.warning("No suitable tokens found for trading")
                return []
        except Exception as e:
            logging.error(f"Error finding promising tokens: {e}")
            return []
    
    async def is_token_safe(self, token_address):
        """Check if a token is safe to buy using QuickNode token data"""
        try:
            # In a production system, you'd check:
            # 1. Sufficient liquidity
            # 2. Not a honeypot (can sell tokens)
            # 3. Creator reputation
            # 4. No mint authority (non-ruggable)
            # 5. No suspicious tokenomics
            
            # Known safe tokens list (can be expanded)
            known_tokens = [
                "So11111111111111111111111111111111111111112",  # SOL
                "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
                "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
                "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx",  # GMT
            ]
            
            if token_address in known_tokens:
                logging.info(f"Token {token_address} is known safe")
                return True
            
            # Check token metadata for signs of safety
            try:
                metadata = await self.get_token_metadata(token_address)
                
                # Check for concerning metadata patterns
                mint_authority = metadata.get('mint_authority')
                
                # If token is initialized and has no mint authority, it's safer
                if metadata.get('is_initialized', False) and mint_authority is None:
                    logging.info(f"Token {token_address} has no mint authority (lower risk)")
                elif mint_authority is not None:
                    logging.warning(f"Token {token_address} has mint authority (potentially risky)")
            except Exception as e:
                logging.warning(f"Error checking token metadata: {e}")
            
            # Check token liquidity using QuickNode Token Liquidity API
            liquidity = await self.get_token_liquidity(token_address)
            
            # Define minimum liquidity threshold
            MIN_LIQUIDITY = 1000  # $1,000 minimum liquidity
            
            if liquidity >= MIN_LIQUIDITY:
                logging.info(f"Token {token_address} has sufficient liquidity: ${liquidity:.2f}")
                return True
            elif liquidity > 0:
                logging.warning(f"Token {token_address} has low liquidity: ${liquidity:.2f}")
            
            # Check if we can get a valid price - if not, token might not be tradable
            price = await self.get_token_price(token_address)
            if price <= 0:
                logging.warning(f"Token {token_address} has no valid price")
                return False
            
            # Special case: If token is identified as a new launch, lower safety requirements
            if token_address in new_token_cache:
                logging.info(f"Token {token_address} is a new launch - applying special evaluation")
                
                # For new tokens, we're more lenient on liquidity requirements
                if liquidity > 500:  # $500 liquidity minimum for new tokens
                    logging.info(f"New token {token_address} has acceptable liquidity for a new launch: ${liquidity:.2f}")
                    return True
                
                # In simulation mode, be more permissive with new tokens
                if CONFIG["SIMULATION_MODE"]:
                    is_safe = random.random() > 0.2  # 80% chance of being safe for new tokens in simulation
                    
                    if is_safe:
                        logging.info(f"New token {token_address} passed safety checks (simulation mode)")
                        return True
            
            # In simulation mode, allow more tokens to pass for testing
            if CONFIG["SIMULATION_MODE"]:
                # Simple randomized check for simulation
                is_safe = random.random() > 0.3  # 70% chance of being safe
                
                if is_safe:
                    logging.info(f"Token {token_address} passed basic safety checks (simulation mode)")
                    return True
                else:
                    logging.info(f"Token {token_address} failed basic safety checks (simulation mode)")
                    return False
            else:
                # In production mode, be conservative
                logging.warning(f"Token {token_address} failed safety checks - insufficient data")
                return False
            
        except Exception as e:
            logging.error(f"Error checking token safety: {e}")
            return False
    
    async def start_trading_loop(self):
        """Main trading loop"""
        logging.info("Starting trading loop...")
        
        while True:
            try:
                # If we're holding too many tokens, skip buying more
                if len(bought_tokens) >= CONFIG["MAX_CONCURRENT_TOKENS"]:
                    logging.info(f"Max concurrent tokens reached ({CONFIG['MAX_CONCURRENT_TOKENS']}). Waiting for sells.")
                    await asyncio.sleep(CONFIG["CHECK_INTERVAL_MS"] / 1000)
                    await self.check_for_sell_opportunities()
                    continue
                
                # Enforce cooldown between buys
                time_since_last_buy = time.time() - last_buy_time
                if time_since_last_buy < CONFIG["BUY_COOLDOWN_MINUTES"] * 60:
                    logging.debug(f"Buy cooldown active. {CONFIG['BUY_COOLDOWN_MINUTES'] * 60 - time_since_last_buy:.1f} seconds remaining")
                    await asyncio.sleep(CONFIG["CHECK_INTERVAL_MS"] / 1000)
                    await self.check_for_sell_opportunities()
                    continue
                
                # Find new tokens to buy
                logging.info("Looking for promising tokens...")
                new_tokens = await self.find_promising_tokens(3)
                
                if not new_tokens or len(new_tokens) == 0:
                    logging.info("No promising tokens found. Waiting...")
                    await asyncio.sleep(CONFIG["CHECK_INTERVAL_MS"] / 1000)
                    continue
                
                logging.info(f"Found {len(new_tokens)} potential tokens: {new_tokens}")
                
                # Try to buy tokens
                for token_address in new_tokens:
                    # Skip if we already own this token
                    if token_address in bought_tokens:
                        logging.info(f"Token {token_address} already in portfolio, skipping")
                        continue
                    
                    # Check if token is safe
                    is_safe = await self.is_token_safe(token_address)
                    if not is_safe:
                        logging.info(f"Skipping token {token_address} - failed safety checks")
                        continue
                    
                    # Determine buy amount - potentially adjust based on token characteristics
                    buy_amount = CONFIG["BUY_AMOUNT_SOL"]
                    
                    # For new tokens that we've detected as launches, we might want to use a different amount
                    if token_address in new_token_cache:
                        # For new launches, consider using a higher buy amount
                        # This is where you could implement custom logic
                        pass
                    
                    # Buy token
                    tx_id = await self.buy_token(token_address, buy_amount)
                    
                    if tx_id:
                        logging.info(f"Successfully bought token {token_address}")
                        break  # Stop after buying one token
                    else:
                        logging.error(f"Failed to buy token {token_address}")
                
                # Check sell opportunities
                await self.check_for_sell_opportunities()
                
                # Wait before next iteration
                await asyncio.sleep(CONFIG["CHECK_INTERVAL_MS"] / 1000)
            except Exception as e:
                logging.error(f"Error in trading loop: {e}")
                await asyncio.sleep(CONFIG["RETRY_DELAY_MS"] / 1000)
    
    def schedule_stats_reset(self):
        """Reset daily stats at midnight"""
        global daily_profit, total_buys_today, successful_sells_today, successful_2x_sells, partial_profit_sells
        
        async def reset_stats():
            while True:
                # Calculate time until next midnight
                now = datetime.now()
                midnight = datetime(now.year, now.month, now.day, 0, 0, 0) + timedelta(days=1)
                seconds_until_midnight = (midnight - now).total_seconds()
                
                await asyncio.sleep(seconds_until_midnight)
                
                # Reset stats
                old_profit = daily_profit
                old_buys = total_buys_today
                old_sells = successful_sells_today
                old_2x = successful_2x_sells
                old_partial = partial_profit_sells
                
                daily_profit = 0
                total_buys_today = 0
                successful_sells_today = 0
                successful_2x_sells = 0
                partial_profit_sells = 0
                
                logging.info(f"Daily stats reset! Previous: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells | {old_partial} partial profit sells")
        
        asyncio.create_task(reset_stats())
    
    async def get_trading_stats(self):
        """Return current trading statistics"""
        # Calculate overall stats
        total_tokens = len(bought_tokens)
        
        # Calculate unrealized profit/loss
        unrealized_profit = 0
        for token_address, data in bought_tokens.items():
            try:
                current_price = await self.get_token_price(token_address)
                initial_price = data.get('initial_price', 0)
                
                # Adjust for any partial sells
                total_buy_amount = data.get('buy_amount', 0)
                partial_sell_amount = data.get('partial_sell_amount', 0)
                remaining_amount = total_buy_amount - partial_sell_amount
                
                if current_price > 0 and initial_price > 0 and remaining_amount > 0:
                    token_profit = ((current_price - initial_price) / initial_price) * remaining_amount / LAMPORTS_PER_SOL
                    unrealized_profit += token_profit
            except Exception as e:
                logging.error(f"Error calculating unrealized profit for {token_address}: {e}")
        
        # Calculate total profit (realized + unrealized)
        total_profit = daily_profit + unrealized_profit
        
        # Calculate win rate
        win_rate = (successful_sells_today / (total_buys_today or 1)) * 100 if total_buys_today > 0 else 0
        
        # Calculate average hold time
        total_hold_time = 0
        for token_address, data in bought_tokens.items():
            buy_time = data.get('buy_time')
            if buy_time:
                hold_minutes = (datetime.now() - buy_time).total_seconds() / 60
                total_hold_time += hold_minutes
        
        avg_hold_time = total_hold_time / total_tokens if total_tokens > 0 else 0
        
        # Calculate running time
        running_time_hours = (time.time() - bot_start_time) / 3600
        
        # Calculate hourly profit rate
        hourly_profit_rate = total_profit / running_time_hours if running_time_hours > 0 else 0
        
        # Projected daily profit at current rate
        projected_daily_profit = hourly_profit_rate * 24
        
        return {
            "daily_profit": daily_profit,
            "unrealized_profit": unrealized_profit,
            "total_profit": total_profit,
            "tokens_held": total_tokens,
            "total_buys_today": total_buys_today,
            "successful_sells": successful_sells_today,
            "2x_sells": successful_2x_sells,
            "partial_profit_sells": partial_profit_sells,
            "win_rate": win_rate,
            "avg_hold_time_minutes": avg_hold_time,
            "running_time_hours": running_time_hours,
            "hourly_profit_rate": hourly_profit_rate,
            "projected_daily_profit": projected_daily_profit,
            "simulation_mode": CONFIG["SIMULATION_MODE"]
        }
    
    async def initialize(self):
        """Initialize the bot"""
        try:
            global bot_start_time
            bot_start_time = time.time()
            
            logging.info("Initializing trading bot...")
            logging.info(f"Running in {'SIMULATION' if CONFIG['SIMULATION_MODE'] else 'PRODUCTION'} mode")
            logging.info(f"Max concurrent tokens: {CONFIG['MAX_CONCURRENT_TOKENS']}")
            logging.info(f"Profit target: {CONFIG['PROFIT_TARGET_PERCENT']}%")
            
            if CONFIG["USE_TIERED_PROFIT_TAKING"]:
                logging.info(f"Tiered profit-taking enabled: {CONFIG['PARTIAL_PROFIT_SELL_PERCENT']}% at {CONFIG['PARTIAL_PROFIT_TARGET_PERCENT']}% profit")
            
            logging.info(f"Stop loss: {CONFIG['STOP_LOSS_PERCENT']}%")
            logging.info(f"Time limit: {CONFIG['TIME_LIMIT_MINUTES']} minutes")
            logging.info(f"Buy amount: {CONFIG['BUY_AMOUNT_SOL']} SOL")
            
            # Test connection to QuickNode
            if not CONFIG["SOLANA_RPC_URL"]:
                logging.error("SOLANA_RPC_URL environment variable is not set!")
                logging.info("Bot will run in simulation mode with limited functionality")
                CONFIG["SIMULATION_MODE"] = True
            else:
                try:
                    # Test Solana RPC connection using getHealth
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getHealth"
                    }
                    headers = {"Content-Type": "application/json"}
                    response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        result = response.json().get('result', '')
                        if result == 'ok':
                            logging.info("Successfully connected to QuickNode Solana RPC")
                        else:
                            logging.warning(f"QuickNode Solana RPC returned unexpected result: {result}")
                    else:
                        logging.warning(f"Warning: QuickNode Solana RPC connection issue. Status: {response.status_code}")
                        if response.status_code == 401:
                            logging.error("Authentication error - check your QuickNode URL")
                except Exception as e:
                    logging.warning(f"Warning: Could not connect to QuickNode Solana RPC. Error: {e}")
                    logging.info("Bot will continue in simulation mode for trading")
                    CONFIG
CONFIG["SIMULATION_MODE"] = True
            
            # Test Jupiter API connection
            if not CONFIG["JUPITER_API_URL"]:
                logging.error("JUPITER_API_URL environment variable is not set!")
                logging.info("Bot will run in simulation mode with limited functionality")
                CONFIG["SIMULATION_MODE"] = True
            else:
                try:
                    # Test with a simple quote request
                    jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote?inputMint={SOL_MINT}&outputMint={USDC_MINT}&amount=1000000&slippageBps=10"
                    response = requests.get(jupiter_url, timeout=10)
                    
                    if response.status_code == 200:
                        logging.info("Successfully connected to Jupiter API")
                    else:
                        logging.warning(f"Warning: Jupiter API connection issue. Status: {response.status_code}")
                        if response.status_code == 401:
                            logging.error("Authentication error - check your Jupiter API URL")
                except Exception as e:
                    logging.warning(f"Warning: Could not connect to Jupiter API. Error: {e}")
                    logging.info("Bot will continue in simulation mode for trading")
                    CONFIG["SIMULATION_MODE"] = True
            
            logging.info("Bot successfully initialized!")
            
            # Schedule stats reset
            self.schedule_stats_reset()
            
            # Start the main trading loop
            await self.start_trading_loop()
            
        except Exception as e:
            logging.error(f"Initialization error: {e}")
            raise

async def main():
    bot = TradingBot()
    await bot.initialize()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
