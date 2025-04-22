# File: trading_bot.py
# Description: A reliable trading bot for Solana using QuickNode and Jupiter API for trading

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
    "LOG_FILE": os.getenv("LOG_FILE", "trading-log.json")
}

# SOL mint address
SOL_MINT = "So11111111111111111111111111111111111111112"
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
            # In a real implementation, we would derive the public key from the private key
            # For this example, we'll use a placeholder
            return "YourWalletAddressHere"
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
        """Get token price with caching"""
        global price_cache
        
        # Check cache first
        current_time = time.time() * 1000  # Convert to milliseconds
        if token_address in price_cache:
            cache_time, price = price_cache[token_address]
            if current_time - cache_time < CONFIG["CACHE_DURATION_MS"]:
                logging.info(f"Using cached price for {token_address}: ${price:.6f}")
                return price
        
        try:
            # Try to get price from Jupiter API
            headers = {"Content-Type": "application/json"}
            
            # Construct the API URL for Jupiter price
            jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/price?ids={token_address}&vsToken=USDC"
            
            try:
                response = requests.get(jupiter_url, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'].get(token_address):
                        price = float(data['data'][token_address]['price'])
                        logging.info(f"Got price from Jupiter: ${price:.6f} for {token_address}")
                        price_cache[token_address] = (current_time, price)
                        return price
            except Exception as e:
                logging.warning(f"Jupiter price API error: {str(e)}")
            
            # If Jupiter fails, try QuickNode price API
            quicknode_url = f"{CONFIG['SOLANA_RPC_URL']}/price?ids={token_address}"
            
            try:
                response = requests.get(quicknode_url, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'].get(token_address):
                        price = float(data['data'][token_address]['price'])
                        logging.info(f"Got price from QuickNode: ${price:.6f} for {token_address}")
                        price_cache[token_address] = (current_time, price)
                        return price
            except Exception as e:
                logging.warning(f"QuickNode price API error: {str(e)}")
            
            # If all APIs fail, try to use previously known prices
            if token_address in bought_tokens and bought_tokens[token_address]['initial_price'] > 0:
                price = bought_tokens[token_address]['initial_price']
                logging.warning(f"Using last known price (${price:.6f}) for {token_address} as fallback")
                return price
            
            logging.warning(f"No price found for {token_address}")
            return 0
        except Exception as e:
            logging.error(f"Error getting token price: {e}")
            return 0
    
    async def buy_token(self, token_address, amount_in_sol):
        """Buy a token using Jupiter API"""
        global bought_tokens, trade_log, total_buys_today, last_buy_time
        
        try:
            # Convert SOL amount to lamports
            amount_in_lamports = int(amount_in_sol * LAMPORTS_PER_SOL)
            
            logging.info(f"Buying {token_address} with {amount_in_sol} SOL...")
            
            # In a real implementation, we would use Jupiter API to execute the swap
            # For this example, we'll simulate a successful transaction
            simulated_tx = f"sim_buy_{token_address[:8]}_{int(time.time())}"
            
            logging.info(f"Successfully created buy transaction: {simulated_tx}")
            
            # Get token price after purchase
            price = await self.get_token_price(token_address)
            
            # Store the purchased token details
            bought_tokens[token_address] = {
                'buy_tx_id': simulated_tx,
                'buy_time': datetime.now(),
                'initial_price': price if price > 0 else 0.00000001,  # Default to tiny price if we can't get one
                'buy_amount': amount_in_lamports,
                'token_address': token_address
            }
            
            # Log the trade
            trade_info = {
                "type": "buy",
                "token": token_address,
                "tx_id": simulated_tx,
                "timestamp": datetime.now().isoformat(),
                "price": price,
                "amount_in_lamports": amount_in_lamports,
                "manual": False
            }
            
            trade_log.append(trade_info)
            self.save_trade_log()
            
            # Update stats
            total_buys_today += 1
            last_buy_time = time.time()
            
            price_display = f"${price:.8f}" if price > 0 else "unknown price"
            logging.info(f"Successfully bought {token_address} at price: {price_display}")
            
            return simulated_tx
        except Exception as e:
            logging.error(f"Error buying token: {e}")
            return None
    
    async def sell_token(self, token_address):
        """Sell a token using Jupiter API"""
        global bought_tokens, trade_log, daily_profit, successful_sells_today, successful_2x_sells
        
        try:
            logging.info(f"Selling {token_address}...")
            
            # In a real implementation, we would use Jupiter API to execute the swap
            # For this example, we'll simulate a successful transaction
            simulated_tx = f"sim_sell_{token_address[:8]}_{int(time.time())}"
            
            logging.info(f"Successfully created sell transaction: {simulated_tx}")
            
            # Get current token data and price
            token_data = bought_tokens.get(token_address)
            if not token_data:
                logging.warning(f"No token data found for {token_address}")
                return {"success": False, "error": "No token data found"}
                
            current_price = await self.get_token_price(token_address)
            
            if token_data and current_price > 0 and token_data['initial_price'] > 0:
                # Calculate profit
                price_ratio = current_price / token_data['initial_price']
                profit_percent = (price_ratio - 1) * 100
                profit_amount = ((current_price - token_data['initial_price']) / token_data['initial_price']) * token_data['buy_amount'] / LAMPORTS_PER_SOL
                
                logging.info(f"Sold {token_address} at ${current_price:.8f} ({price_ratio:.2f}x, ${profit_amount:.2f} profit)")
                
                # Log the trade
                trade_info = {
                    "type": "sell",
                    "token": token_address,
                    "tx_id": simulated_tx,
                    "timestamp": datetime.now().isoformat(),
                    "price": current_price,
                    "profit": profit_amount,
                    "price_ratio": price_ratio,
                    "manual": False
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
                initial_price = data['initial_price']
                buy_amount = data['buy_amount']
                buy_time = data['buy_time']
                
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
        """Find promising new tokens to buy"""
        # In a real implementation, we would query APIs like Birdeye or use WebSocket to monitor for new tokens
        # For this example, we'll use a list of test tokens
        
        test_tokens = [
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
            "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
            "5JnZ8ZUXZRuHt6rkWFSAPQVEJ3dTADgpNMGYMRvGLhT",  # HADES
            "BERTvZDDguQJXeN9qjwwpM2QHEgMTQ5RbzuJMuX4sKTQ",  # BERT
            "M1nec3zsQAR3be1JbATuYqaXZHB2ZBpUJXUeDWGz9tQ",  # MINEC
            "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",  # MANDE
        ]
        
        # Shuffle and take first few
        random.shuffle(test_tokens)
        selected = test_tokens[:max_results]
        
        logging.info(f"Using test tokens for demonstration: {selected}")
        return selected
    
    async def is_token_safe(self, token_address):
        """Check if a token is safe to buy"""
        # In a real implementation, we would check various factors like:
        # - Sufficient liquidity
        # - Not a honeypot
        # - No suspicious tokenomics
        
        # For this example, we'll do a simple check
        known_tokens = [
            "So11111111111111111111111111111111111111112",  # SOL
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
            "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
        ]
        
        if token_address in known_tokens:
            logging.info(f"Token {token_address} is known safe")
            return True
        
        # For unknown tokens, simulate safety check
        # In production, you would check liquidity, volume, etc.
        is_safe = random.random() > 0.2  # 80% chance of being safe
        
        if is_safe:
            logging.info(f"Token {token_address} passed safety checks")
        else:
            logging.info(f"Token {token_address} failed safety checks")
        
        return is_safe
    
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
    
    async def initialize(self):
        """Initialize the bot"""
        try:
            logging.info("Initializing trading bot...")
            
            # Test connection to QuickNode
            try:
                # Test Solana RPC connection
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getHealth"
                }
                response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload)
                if response.status_code == 200 and response.json().get('result') == 'ok':
                    logging.info("Successfully connected to QuickNode Solana RPC")
                else:
                    logging.warning(f"Warning: QuickNode Solana RPC connection issue. Status: {response.status_code}")
            except Exception as e:
                logging.warning(f"Warning: Could not connect to QuickNode Solana RPC. Error: {e}")
            
            # Test Jupiter API connection
            try:
                jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote?inputMint={SOL_MINT}&outputMint={SOL_MINT}&amount=1000000&slippageBps=10"
                response = requests.get(jupiter_url)
                if response.status_code == 200:
                    logging.info("Successfully connected to Jupiter API")
                else:
                    logging.warning(f"Warning: Jupiter API connection issue. Status: {response.status_code}")
            except Exception as e:
                logging.warning(f"Warning: Could not connect to Jupiter API. Error: {e}")
                logging.info("Bot will continue in simulation mode for trading")
            
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
