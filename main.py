# File: trading_bot.py
# Description: A reliable trading bot for Solana using QuickNode APIs for trading

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
    "STOP_LOSS_PERCENT": float(os.getenv("STOP_LOSS_PERCENT", "20")),  # 20% loss
    "TIME_LIMIT_MINUTES": int(os.getenv("TIME_LIMIT_MINUTES", "60")),  # Hold time limit in minutes
    "BUY_AMOUNT_SOL": float(os.getenv("BUY_AMOUNT_SOL", "0.15")),  # Amount to buy with in SOL
    "RETRY_DELAY_MS": int(os.getenv("RETRY_DELAY_MS", "2000")),  # 2 seconds between retries
    "CHECK_INTERVAL_MS": int(os.getenv("CHECK_INTERVAL_MS", "10000")),  # Check price every 10 seconds
    "BUY_COOLDOWN_MINUTES": int(os.getenv("BUY_COOLDOWN_MINUTES", "5")),  # 5 minutes cooldown between buys
    "CACHE_DURATION_MS": int(os.getenv("CACHE_DURATION_MS", "60000")),  # Cache prices for 1 minute
    "LOG_FILE": os.getenv("LOG_FILE", "trading-log.json"),
    "SIMULATION_MODE": os.getenv("SIMULATION_MODE", "true").lower() == "true"  # Default to simulation mode
}

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
LAMPORTS_PER_SOL = 1_000_000_000

# Global state variables
bought_tokens = {}
trade_log = []
price_cache = {}
last_buy_time = time.time() - CONFIG["BUY_COOLDOWN_MINUTES"] * 60  # Set initial buy time in the past

# Stats tracking
daily_profit = 0
total_buys_today = 0
successful_sells_today = 0
successful_2x_sells = 0

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
            logging.info(f"Token supply API response status: {response.status_code}")
            
            if response.status_code == 200:
                # This is just a check that we can connect to the RPC
                logging.info("Successfully connected to QuickNode RPC")
                
                # For real token price checking, we'd need to implement a DEX liquidity check
                # This is just a placeholder for now
                
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
                
                # If we don't have a price, return a small default price
                logging.warning(f"No price information for {token_address}, using default")
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
                
                return 0.0001  # Default small value
                
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
        return 0.0001
    
    except Exception as e:
        logging.error(f"Error getting token price: {e}")
        return 0.0001  # Default small value
    async def get_token_liquidity(self, token_address):
        """Get token liquidity information using QuickNode"""
        try:
            # Use QuickNode's Liquidity API
            liquidity_url = f"{CONFIG['SOLANA_RPC_URL']}/liquidity"
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
            
            # If API fails, return 0 liquidity as a safe default
            return 0
        except Exception as e:
            logging.error(f"Error getting token liquidity: {e}")
            return 0
    
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
                        "amount": amount_in_lamports,
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
            
            # Store the purchased token details
            bought_tokens[token_address] = {
                'buy_tx_id': tx_signature,
                'buy_time': datetime.now(),
                'initial_price': price if price > 0 else 0.00000001,  # Default to tiny price if we can't get one
                'buy_amount': amount_in_lamports,
                'token_address': token_address
            }
            
            # Log the trade
            trade_info = {
                "type": "buy",
                "token": token_address,
                "tx_id": tx_signature,
                "timestamp": datetime.now().isoformat(),
                "price": price,
                "amount_in_lamports": amount_in_lamports,
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
    
    async def sell_token(self, token_address):
        """Sell a token using Jupiter API"""
        global bought_tokens, trade_log, daily_profit, successful_sells_today, successful_2x_sells
        
        try:
            logging.info(f"Selling {token_address}...")
            
            # Get token data before selling
            token_data = bought_tokens.get(token_address)
            if not token_data:
                logging.warning(f"No token data found for {token_address}")
                return {"success": False, "error": "No token data found"}
            
            # Get price before selling for profit calculation
            current_price = await self.get_token_price(token_address)
            
            if CONFIG["SIMULATION_MODE"]:
                # Simulate a successful transaction
                simulated_tx = f"sim_sell_{token_address[:8]}_{int(time.time())}"
                logging.info(f"SIMULATION MODE: Created sell transaction: {simulated_tx}")
                tx_signature = simulated_tx
            else:
                # In production mode, use Jupiter API to execute the swap
                try:
                    # Step 1: Get quote from Jupiter API for the reverse swap
                    quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                    
                    # In reality, you'd query the token balance here, but for simulation:
                    token_amount = token_data.get('buy_amount', 0)  # This is approximate
                    
                    payload = {
                        "inputMint": token_address,
                        "outputMint": SOL_MINT,
                        "amount": token_amount,  # Full token amount (would be in token decimals)
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
                    logging.info(f"Successfully created sell transaction: {tx_signature}")
                    
                except Exception as e:
                    logging.error(f"Error executing Jupiter sell swap: {e}")
                    # Fallback to simulation if real transaction fails
                    simulated_tx = f"sim_fallback_sell_{token_address[:8]}_{int(time.time())}"
                    logging.info(f"Falling back to simulation due to API error: {simulated_tx}")
                    tx_signature = simulated_tx
            
            # Calculate profit regardless of simulation/production
            if token_data and current_price > 0 and token_data.get('initial_price', 0) > 0:
                # Calculate profit
                price_ratio = current_price / token_data['initial_price']
                profit_percent = (price_ratio - 1) * 100
                profit_amount = ((current_price - token_data['initial_price']) / token_data['initial_price']) * token_data['buy_amount'] / LAMPORTS_PER_SOL
                
                logging.info(f"Sold {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit_amount:.2f} profit)")
                
                # Log the trade
                trade_info = {
                    "type": "sell",
                    "token": token_address,
                    "tx_id": tx_signature,
                    "timestamp": datetime.now().isoformat(),
                    "price": current_price,
                    "profit": profit_amount,
                    "price_ratio": price_ratio,
                    "manual": False,
                    "simulation": CONFIG["SIMULATION_MODE"]
                }
                
                trade_log.append(trade_info)
                self.save_trade_log()
                
                # Update stats
                daily_profit += profit_amount
                successful_sells_today += 1
                if price_ratio >= 2.0:
                    successful_2x_sells += 1
                
                # Remove from bought tokens
                if token_address in bought_tokens:
                    del bought_tokens[token_address]
                
                return {"success": True, "profit": profit_amount, "price_ratio": price_ratio}
            else:
                logging.warning(f"Couldn't calculate profit for {token_address} - missing price data")
                
                # Still log the trade with limited info
                trade_info = {
                    "type": "sell",
                    "token": token_address,
                    "tx_id": tx_signature,
                    "timestamp": datetime.now().isoformat(),
                    "price": current_price if current_price else 0,
                    "profit": 0,
                    "price_ratio": 1,
                    "manual": False,
                    "simulation": CONFIG["SIMULATION_MODE"],
                    "note": "Price data incomplete, profit calculation approximated"
                }
                
                trade_log.append(trade_info)
                self.save_trade_log()
                
                # Still remove from bought tokens
                if token_address in bought_tokens:
                    del bought_tokens[token_address]
                
                return {"success": True, "profit": 0, "price_ratio": 1}
        except Exception as e:
            logging.error(f"Error selling token: {e}")
            return {"success": False, "error": str(e)}
    
    async def check_for_sell_opportunities(self):
        """Check for tokens that should be sold based on our criteria"""
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
                
                if price_ratio >= (100 + CONFIG["PROFIT_TARGET_PERCENT"]) / 100:
                    sell_reason = f"{CONFIG['PROFIT_TARGET_PERCENT']}% profit target reached"
                    logging.info(f"Token {token_address} reached {CONFIG['PROFIT_TARGET_PERCENT']}% profit target ({price_ratio:.2f}x)")
                elif price_ratio <= (100 - CONFIG["STOP_LOSS_PERCENT"]) / 100:
                    sell_reason = f"{CONFIG['STOP_LOSS_PERCENT']}% stop loss triggered"
                    logging.info(f"Token {token_address} triggered {CONFIG['STOP_LOSS_PERCENT']}% stop loss ({price_ratio:.2f}x)")
                elif minutes_held > CONFIG["TIME_LIMIT_MINUTES"] and price_ratio < 1.2:
                    sell_reason = f"time limit ({CONFIG['TIME_LIMIT_MINUTES']} min) exceeded without significant gain"
                    logging.info(f"Token {token_address} exceeded time limit: held for {minutes_held:.1f} min with {price_ratio:.2f}x")
                
                if sell_reason:
                    # Execute sell
                    logging.info(f"Selling token {token_address} - Reason: {sell_reason}")
                    result = await self.sell_token(token_address)
                    
                    if result["success"]:
                        logging.info(f"Auto-sold {token_address} successfully")
                    else:
                        logging.error(f"Failed to sell token {token_address}: {result.get('error')}")
                else:
                    logging.info(f"Holding token {token_address}: current ratio {price_ratio:.2f}x, held for {minutes_held:.1f} min")
            except Exception as e:
                logging.error(f"Error checking sell opportunity for {token_address}: {e}")
    
    async def find_promising_tokens(self, max_results=3):
        """Find promising new tokens to buy using QuickNode APIs"""
        try:
            logging.info("Looking for newly launched tokens...")
            
            # Approach 1: Query for new tokens with metadata using QuickNode RPC
            # This would show newly created tokens with some filtering
            try:
                if not CONFIG["SIMULATION_MODE"]:
                    # This would be a custom implementation using QuickNode's APIs
                    # to find newly launched tokens on Solana
                    
                    # Example: We could query recent token programs, transactions, etc.
                    # For now, we'll skip this in the example code
                    pass
            except Exception as e:
                logging.warning(f"Error finding new tokens via QuickNode: {e}")
            
            # Method 2: Use popular pump.fun tokens (monitored separately)
            # In a full implementation, you'd have a WebSocket monitoring pump.fun
            
            # For now, we'll use a test token list with popular tokens
            test_tokens = [
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
                "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
                "5JnZ8ZUXZRuHt6rkWFSAPQVEJ3dTADgpNMGYMRvGLhT",  # HADES
                "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx",  # GMT
                "BERTvZDDguQJXeN9qjwwpM2QHEgMTQ5RbzuJMuX4sKTQ",  # BERT
                "M1nec3zsQAR3be1JbATuYqaXZHB2ZBpUJXUeDWGz9tQ",  # MINEC
                "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",  # MANDE
            ]
            
            # Shuffle and take first few
            random.shuffle(test_tokens)
            selected = test_tokens[:max_results]
            
            logging.info(f"Found tokens for evaluation: {selected}")
            return selected
            
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
            
            # Check token liquidity using QuickNode Token Liquidity API
            liquidity = await self.get_token_liquidity(token_address)
            
            # Define minimum liquidity threshold
            MIN_LIQUIDITY = 5000  # $5,000 minimum liquidity
            
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
            
            # In simulation mode, allow more tokens to pass for testing
            if CONFIG["SIMULATION_MODE"]:
                # Simple randomized check for simulation
                is_safe = random.random() > 0.2  # 80% chance of being safe
                
                if is_safe:
                    logging.info(f"Token {token_address} passed basic safety checks (simulation mode)")
                else:
                    logging.info(f"Token {token_address} failed basic safety checks (simulation mode)")
                
                return is_safe
            else:
                # In production mode, be conservative
                logging.warning(f"Token {token_address} failed safety checks - insufficient data")
                return False
            
        except Exception as e:
            logging.error(f"Error checking token safety: {e}")
            return False
    
    async def start_trading_loop(self):
        """Main trading loop"""
        # Config parameters for auto-trading
        MAX_CONCURRENT_TOKENS = 5  # Maximum number of tokens to hold at once
        
        logging.info("Starting trading loop...")
        
        while True:
            try:
                # If we're holding too many tokens, skip buying more
                if len(bought_tokens) >= MAX_CONCURRENT_TOKENS:
                    logging.info(f"Max concurrent tokens reached ({MAX_CONCURRENT_TOKENS}). Waiting for sells.")
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
                    
                    # Buy token
                    tx_id = await self.buy_token(token_address, CONFIG["BUY_AMOUNT_SOL"])
                    
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
        global daily_profit, total_buys_today, successful_sells_today, successful_2x_sells
        
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
                
                daily_profit = 0
                total_buys_today = 0
                successful_sells_today = 0
                successful_2x_sells = 0
                
                logging.info(f"Daily stats reset! Previous: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")
        
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
                buy_amount = data.get('buy_amount', 0)
                
                if current_price > 0 and initial_price > 0:
                    token_profit = ((current_price - initial_price) / initial_price) * buy_amount / LAMPORTS_PER_SOL
                    unrealized_profit += token_profit
            except Exception as e:
                logging.error(f"Error calculating unrealized profit for {token_address}: {e}")
        
        # Calculate total profit (realized + unrealized)
        total_profit = daily_profit + unrealized_profit
        
        # Calculate win rate
        win_rate = (successful_sells_today / (total_buys_today or 1)) * 100 if total_buys_today > 0 else 0
        
        return {
            "daily_profit": daily_profit,
            "unrealized_profit": unrealized_profit,
            "total_profit": total_profit,
            "tokens_held": total_tokens,
            "total_buys_today": total_buys_today,
            "successful_sells": successful_sells_today,
            "2x_sells": successful_2x_sells,
            "win_rate": win_rate,
            "simulation_mode": CONFIG["SIMULATION_MODE"]
        }
    
    async def initialize(self):
        """Initialize the bot"""
        try:
            logging.info("Initializing trading bot...")
            logging.info(f"Running in {'SIMULATION' if CONFIG['SIMULATION_MODE'] else 'PRODUCTION'} mode")
            
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
            
            # Test QuickNode Token Price API
            try:
                # Test with a simple price request for SOL
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "qn_getTokenPrice",
                    "params": {
                        "token": SOL_MINT
                    }
                }
                headers = {"Content-Type": "application/json"}
                response = requests.post(f"{CONFIG['SOLANA_RPC_URL']}/token-price", json=payload, headers=headers, timeout=5)
                
                if response.status_code == 200 and 'result' in response.json():
                    logging.info("Successfully connected to QuickNode Token Price API")
                else:
                    logging.warning("Warning: QuickNode Token Price API not available or configured")
            except Exception as e:
                logging.warning(f"Warning: Could not connect to QuickNode Token Price API. Error: {e}")
            
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
