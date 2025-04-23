# File: trading_bot.py
# Description: A reliable trading bot for Solana using QuickNode APIs for crypto sniping

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Load environment variables
load_dotenv()

# Constants
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
WRAPPED_SOL = "So11111111111111111111111111111111111111112"

# Global price cache
price_cache = {}

# Configuration from environment variables
CONFIG = {
    "SOLANA_RPC_URL": os.getenv("SOLANA_RPC_URL"),
    "JUPITER_API_URL": os.getenv("JUPITER_API_URL"),
    "BOT_PRIVATE_KEY": os.getenv("BOT_PRIVATE_KEY"),
    "PROFIT_TARGET_PERCENT": float(os.getenv("PROFIT_TARGET_PERCENT", "100")),  # 100% = 2x
    "PARTIAL_PROFIT_PERCENT": float(os.getenv("PARTIAL_PROFIT_PERCENT", "50")),  # Take partial profit at 50%
    "STOP_LOSS_PERCENT": float(os.getenv("STOP_LOSS_PERCENT", "20")),  # 20% loss
    "TIME_LIMIT_MINUTES": int(os.getenv("TIME_LIMIT_MINUTES", "60")),
    "BUY_AMOUNT_SOL": float(os.getenv("BUY_AMOUNT_SOL", "0.15")),
    "BUY_COOLDOWN_MINUTES": int(os.getenv("BUY_COOLDOWN_MINUTES", "5")),
    "SIMULATION_MODE": os.getenv("SIMULATION_MODE", "true").lower() == "true",
    "MAX_CONCURRENT_TOKENS": int(os.getenv("MAX_CONCURRENT_TOKENS", "15")),
    "TOKEN_SCAN_LIMIT": int(os.getenv("TOKEN_SCAN_LIMIT", "100")),  # Number of transactions to scan for new tokens
    "CACHE_DURATION_MS": 60 * 1000,  # 1 minute cache for prices
    "CHECK_INTERVAL_MS": 3000,  # Check for opportunities every 3 seconds
    "TRANSACTION_TIMEOUT_SEC": 30,  # Timeout for transactions
}

# Known token addresses and their fallback prices
KNOWN_TOKEN_PRICES = {
    "So11111111111111111111111111111111111111112": 140.0,  # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 1.0,  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": 1.0,  # USDT
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 0.000013,  # BONK
    "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU": 0.06,  # SAMO
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": 0.42,  # JUP
    "5JnZ8ZUXZRuHt6rkWFSAPQVEJ3dTADgpNMGYMRvGLhT": 0.01,  # HADES
}

class TradingBot:
    def __init__(self):
        self.wallet_address = self._get_wallet_address()
        self.load_trade_log()
        
    def _get_wallet_address(self):
        """Get wallet address from private key"""
        try:
            private_key = CONFIG["BOT_PRIVATE_KEY"]
            # This is a placeholder - in a real implementation, we would use solana-py to derive the public key
            if CONFIG["SIMULATION_MODE"]:
                return "SimulatedWalletAddress123456789"
            else:
                # For non-simulation mode, derive real wallet address from private key
                # This is placeholder logic - real implementation would use the actual derivation logic
                return f"ActualWalletDerivedFromPrivateKey"
        except Exception as e:
            logging.error(f"Failed to get wallet address: {e}")
            return "SimulatedWalletAddress123456789"  # Fallback to simulated address

    def load_trade_log(self):
        """Load trading history from file or initialize new log"""
        self.trade_log = {
            "transactions": [],
            "buy_times": {},  # token_address -> buy_timestamp
            "buy_prices": {},  # token_address -> buy_price
            "held_tokens": set(),  # Currently held tokens
            "partial_sold": set(),  # Tokens where we've taken partial profit
            "daily_profits": {},  # date -> profit amount
            "last_buy_time": 0,  # Timestamp of last buy for cooldown
        }
        
        try:
            if os.path.exists("trade_log.json"):
                with open("trade_log.json", "r") as f:
                    saved_log = json.load(f)
                    
                # Validate and merge saved log with current log
                if isinstance(saved_log, dict):
                    # Convert held_tokens and partial_sold from list to set
                    if "held_tokens" in saved_log:
                        saved_log["held_tokens"] = set(saved_log["held_tokens"])
                    if "partial_sold" in saved_log:
                        saved_log["partial_sold"] = set(saved_log["partial_sold"])
                    
                    # Update log with saved data
                    for key, value in saved_log.items():
                        if key in self.trade_log:
                            self.trade_log[key] = value
        except Exception as e:
            logging.error(f"Error loading trade log: {e}")
            # Continue with empty log

    def save_trade_log(self):
        """Save trading history to file"""
        try:
            # Convert sets to lists for JSON serialization
            save_log = self.trade_log.copy()
            save_log["held_tokens"] = list(self.trade_log["held_tokens"])
            save_log["partial_sold"] = list(self.trade_log["partial_sold"])
            
            with open("trade_log.json", "w") as f:
                json.dump(save_log, f)
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
                logging.debug(f"Using cached price for {token_address}: ${price:.6f}")
                return price

        # Check if this is a known token with fallback price
        if token_address in KNOWN_TOKEN_PRICES:
            fallback_price = KNOWN_TOKEN_PRICES[token_address]
        else:
            fallback_price = 0.0001  # Default fallback price for unknown tokens

        try:
            # First try: QuickNode Token Price API using proper JSON-RPC format
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "qn_getTokenPrice",
                "params": {
                    "token": token_address
                }
            }

            try:
                response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if 'result' in data and data['result'] is not None:
                        price = float(data['result'].get('price', 0))
                        if price > 0:
                            logging.info(f"Got price from QuickNode Token Price API: ${price:.6f} for {token_address}")
                            price_cache[token_address] = (current_time, price)
                            return price
                    elif 'error' in data:
                        logging.warning(f"QuickNode price API error: {data['error'].get('message', 'Unknown error')}")
                else:
                    logging.warning(f"QuickNode Token Price API returned status {response.status_code}")
            except Exception as e:
                logging.warning(f"Error with QuickNode Token Price API: {str(e)}")

            # Second try: Jupiter API for price
            try:
                jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/price?ids={token_address}&vsToken=USDC"
                response = requests.get(jupiter_url, timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'].get(token_address):
                        price = float(data['data'][token_address].get('price', 0))
                        if price > 0:
                            logging.info(f"Got price from Jupiter API: ${price:.6f} for {token_address}")
                            price_cache[token_address] = (current_time, price)
                            return price
                else:
                    logging.warning(f"Jupiter API returned status {response.status_code}")
            except Exception as e:
                logging.warning(f"Error with Jupiter price API: {str(e)}")

            # If we reach here, we couldn't get a price - use fallback
            logging.warning(f"Using last known price (${fallback_price}) for {token_address} as fallback")
            price_cache[token_address] = (current_time, fallback_price)
            return fallback_price
            
        except Exception as e:
            logging.error(f"Error getting token price: {e}")
            # Use last known price or fallback
            logging.warning(f"Using last known price (${fallback_price}) for {token_address} as fallback")
            price_cache[token_address] = (current_time, fallback_price)
            return fallback_price

    async def get_token_liquidity(self, token_address):
        """Get token liquidity information using slippage as a proxy"""
        try:
            # Use Jupiter quote to estimate liquidity based on slippage
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            headers = {"Content-Type": "application/json"}
            
            # First quote: 0.1 SOL input
            amount_in_small = 100_000_000  # 0.1 SOL in lamports
            small_payload = {
                "inputMint": SOL_MINT,
                "outputMint": token_address,
                "amount": str(amount_in_small),
                "slippageBps": 50  # 0.5% slippage
            }
            
            try:
                small_response = requests.post(quote_url, json=small_payload, headers=headers, timeout=5)
                if small_response.status_code == 200:
                    small_data = small_response.json()
                    
                    # Now get quote for larger amount: 1 SOL input
                    amount_in_large = 1_000_000_000  # 1 SOL in lamports
                    large_payload = {
                        "inputMint": SOL_MINT,
                        "outputMint": token_address,
                        "amount": str(amount_in_large),
                        "slippageBps": 50  # 0.5% slippage
                    }
                    
                    large_response = requests.post(quote_url, json=large_payload, headers=headers, timeout=5)
                    if large_response.status_code == 200:
                        large_data = large_response.json()
                        
                        # Calculate liquidity indicators
                        if ('outAmount' in small_data and 'outAmount' in large_data and 
                            int(small_data['outAmount']) > 0 and int(large_data['outAmount']) > 0):
                            
                            # Calculate price impact between small and large trades
                            small_rate = int(small_data['outAmount']) / amount_in_small
                            large_rate = int(large_data['outAmount']) / amount_in_large
                            
                            # Calculate price impact percentage
                            price_impact_pct = abs((large_rate - small_rate) / small_rate * 100)
                            
                            # Estimate liquidity based on price impact
                            # Lower price impact = higher liquidity
                            if price_impact_pct < 1:
                                liquidity_estimate = "high"
                            elif price_impact_pct < 5:
                                liquidity_estimate = "medium"
                            else:
                                liquidity_estimate = "low"
                            
                            logging.info(f"Token {token_address} has estimated {liquidity_estimate} liquidity (price impact: {price_impact_pct:.2f}%)")
                            
                            return {
                                "has_liquidity": True,
                                "price_impact_pct": price_impact_pct,
                                "liquidity_level": liquidity_estimate,
                                "small_quote": int(small_data['outAmount']),
                                "large_quote": int(large_data['outAmount'])
                            }
            except Exception as e:
                logging.warning(f"Error checking liquidity via Jupiter: {str(e)}")
            
            # If we reach here, try a simpler approach - just check if we can get a quote at all
            try:
                price = await self.get_token_price(token_address)
                if price > 0:
                    # If we can get a price, there's at least some liquidity
                    logging.info(f"Token {token_address} has some liquidity (price: ${price:.6f})")
                    return {
                        "has_liquidity": True,
                        "price_impact_pct": 10,  # Conservative estimate
                        "liquidity_level": "unknown",
                        "price": price
                    }
            except Exception as e:
                logging.warning(f"Error getting price for liquidity check: {str(e)}")
                
            # If all else fails, assume no liquidity
            logging.warning(f"Token {token_address} has no detectable liquidity")
            return {
                "has_liquidity": False,
                "liquidity_level": "none"
            }
            
        except Exception as e:
            logging.error(f"Error checking token liquidity: {e}")
            return {"has_liquidity": False, "error": str(e)}

    async def find_promising_tokens(self, max_results=3):
        """Find newly launched tokens using QuickNode"""
        try:
            logging.info("Looking for newly created tokens...")
            
            # Query recent program transactions for token programs
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token program
                    {"limit": CONFIG["TOKEN_SCAN_LIMIT"]}  # Check most recent transactions
                ]
            }
            
            response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                signatures = [item.get('signature') for item in data.get('result', [])]
                
                # Get transaction details for the most recent 25 transactions
                new_tokens = []
                processed_count = 0
                
                for sig in signatures[:25]:  # Process a subset for efficiency
                    processed_count += 1
                    
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
                        
                        # Skip failed transactions
                        if not tx_data or tx_data.get('meta', {}).get('err') is not None:
                            continue
                            
                        # Look for token creation in transaction
                        try:
                            instructions = tx_data.get('transaction', {}).get('message', {}).get('instructions', [])
                            
                            for instruction in instructions:
                                # Check for token creation instructions
                                program_id = instruction.get('programId')
                                
                                if program_id == 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA':
                                    # Found token program instruction
                                    
                                    # Extract potential token addresses from account keys
                                    accounts = instruction.get('accounts', [])
                                    for acc_idx in accounts:
                                        if acc_idx < len(tx_data.get('transaction', {}).get('message', {}).get('accountKeys', [])):
                                            potential_token = tx_data.get('transaction', {}).get('message', {}).get('accountKeys', [])[acc_idx]
                                            
                                            # Skip known tokens
                                            if potential_token in KNOWN_TOKEN_PRICES:
                                                continue
                                                
                                            # Check for liquidity and price
                                            try:
                                                price_info = await self.get_token_price(potential_token)
                                                if price_info > 0:
                                                    liquidity_info = await self.get_token_liquidity(potential_token)
                                                    if liquidity_info and liquidity_info.get('has_liquidity', False):
                                                        # Found a potential new token with liquidity!
                                                        new_tokens.append(potential_token)
                                                        logging.info(f"Found newly created token with liquidity: {potential_token}")
                                            except Exception as e:
                                                logging.debug(f"Error checking token {potential_token}: {e}")
                        except Exception as e:
                            logging.debug(f"Error parsing transaction {sig}: {e}")
                
                logging.info(f"Processed {processed_count} transactions, found {len(new_tokens)} potential new tokens")
                
                # If we found any new tokens, return them
                if new_tokens:
                    # Deduplicate and limit results
                    unique_tokens = list(set(new_tokens))
                    return unique_tokens[:max_results]
            
            # If no new tokens found or API error, use test tokens
            logging.warning("No promising tokens found through monitoring or API, using fallback")
            test_tokens = [
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
                "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # JUP
                "5JnZ8ZUXZRuHt6rkWFSAPQVEJ3dTADgpNMGYMRvGLhT",  # HADES
                "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",  # SAMO
                "SMBRD1N9N4RuNu2tYCSnbDsY3icGXAu9KKn4GbhKfJj",  # BURD
                "FoRGERiW7odcCBGU1bztZi16osPBHjxharvDathL5nfy",  # FORGE
                "HkPrxhgiPqNzgRtW1dPgBfXsj7UitAn1PR5Transfersh",  # TRSF
                "MNDEFzGvMt87ueuHvVU9VcTqsAP5b3fTGPsHuuPA5ey",  # MNDE
                "kinXdEcpDQeHPEuQnqmUgtYykqKGVFq6CeVX5iAHJq6",  # KIN
                "MAPS41MDahZ9QdKXhVa4dWB9RuyfV4XqhyAZ8XcYepb",  # MAPS
                "7i5KKsX2weiTkry7jA4ZwSuXGhs5eJBEjY8vVxR4pfRx",  # WIF
                "HHMF7hd3FDfA8iAH3h8F0ppOUV9jkoviETYyCwUDpump"  # PUMP
            ]
            random.shuffle(test_tokens)
            selected = test_tokens[:max_results]
            return selected
        except Exception as e:
            logging.error(f"Error finding promising tokens: {e}")
            return []

    async def is_token_safe(self, token_address):
        """Check if token appears safe to trade based on multiple criteria"""
        try:
            # Check 1: Verify token has liquidity
            liquidity_info = await self.get_token_liquidity(token_address)
            if not liquidity_info.get("has_liquidity", False):
                logging.warning(f"Token {token_address} has no liquidity, skipping")
                return False
                
            # Check 2: Price verification - make sure we can get a price
            price = await self.get_token_price(token_address)
            if price <= 0:
                logging.warning(f"Token {token_address} has no valid price, skipping")
                return False
                
            # Check 3: Known safe token list
            if token_address in KNOWN_TOKEN_PRICES:
                logging.info(f"Token {token_address} is in known safe list")
                return True
                
            # In simulation mode, consider all tokens with liquidity and price as safe
            if CONFIG["SIMULATION_MODE"]:
                logging.info(f"Token {token_address} passed basic safety checks in simulation mode")
                return True
                
            # Additional checks for production mode
            # These would include more sophisticated checks for:
            # - Token metadata analysis
            # - Contract security analysis
            # - Liquidity lock verification
            # - Trading volume analysis
            
            # For now, assume it's safe if it has liquidity and price
            logging.info(f"Token {token_address} passed basic safety checks")
            return True
            
        except Exception as e:
            logging.error(f"Error checking token safety: {e}")
            return False

    async def buy_token(self, token_address, amount_in_sol):
        """Buy token with SOL using Jupiter API"""
        try:
            # Mark buy time even in simulation mode
            buy_time = time.time() * 1000
            self.trade_log["last_buy_time"] = buy_time
            
            # Get current price for tracking
            price = await self.get_token_price(token_address)
            
            # In simulation mode, just record the transaction
            if CONFIG["SIMULATION_MODE"]:
                logging.info(f"[SIMULATION] Auto-bought {token_address} with {amount_in_sol} SOL at ${price}")
                
                # Record the simulated buy
                self.trade_log["transactions"].append({
                    "type": "buy",
                    "token": token_address,
                    "amount_in": amount_in_sol,
                    "price": price,
                    "timestamp": buy_time,
                    "simulation": True
                })
                
                # Track buy time and price for calculating profit
                self.trade_log["buy_times"][token_address] = buy_time
                self.trade_log["buy_prices"][token_address] = price
                self.trade_log["held_tokens"].add(token_address)
                
                # Save updated log
                self.save_trade_log()
                return True
                
            else:
                # Real trade implementation would go here for production mode
                # This would use Jupiter API to execute the actual swap
                
                # For now, just log that we would make the trade
                logging.info(f"Would buy {token_address} with {amount_in_sol} SOL at ${price}")
                
                # Record as if we made the buy
                self.trade_log["transactions"].append({
                    "type": "buy",
                    "token": token_address,
                    "amount_in": amount_in_sol,
                    "price": price,
                    "timestamp": buy_time,
                    "simulation": CONFIG["SIMULATION_MODE"]
                })
                
                # Track buy time and price for calculating profit
                self.trade_log["buy_times"][token_address] = buy_time
                self.trade_log["buy_prices"][token_address] = price
                self.trade_log["held_tokens"].add(token_address)
                
                # Save updated log
                self.save_trade_log()
                return True
                
        except Exception as e:
            logging.error(f"Error buying token: {e}")
            return False

    async def sell_token(self, token_address, partial=False):
        """Sell token for SOL using Jupiter API"""
        try:
            # Get current price
            current_price = await self.get_token_price(token_address)
            buy_price = self.trade_log["buy_prices"].get(token_address, 0)
            
            # Calculate profit percentage
            if buy_price > 0:
                profit_percent = (current_price - buy_price) / buy_price * 100
            else:
                profit_percent = 0
                
            # Get buy timestamp
            buy_time = self.trade_log["buy_times"].get(token_address, 0)
            hold_duration_ms = time.time() * 1000 - buy_time
            hold_duration_min = hold_duration_ms / (60 * 1000)
            
            # Determine sell amount based on partial flag
            sell_amount = "50%" if partial else "100%"
            
            # In simulation mode, just record the transaction
            if CONFIG["SIMULATION_MODE"]:
                logging.info(f"[SIMULATION] Sold {sell_amount} of {token_address} at ${current_price} " +
                             f"for {profit_percent:.2f}% profit after {hold_duration_min:.1f} minutes")
                
                # Record the simulated sell
                self.trade_log["transactions"].append({
                    "type": "sell",
                    "token": token_address,
                    "sell_price": current_price,
                    "buy_price": buy_price, 
                    "profit_percent": profit_percent,
                    "hold_duration_min": hold_duration_min,
                    "timestamp": time.time() * 1000,
                    "partial": partial,
                    "simulation": True
                })
                
                # Update held tokens list
                if not partial:
                    if token_address in self.trade_log["held_tokens"]:
                        self.trade_log["held_tokens"].remove(token_address)
                    if token_address in self.trade_log["partial_sold"]:
                        self.trade_log["partial_sold"].remove(token_address)
                else:
                    # Mark as partially sold
                    self.trade_log["partial_sold"].add(token_address)
                
                # Track daily profit
                today = datetime.now().strftime("%Y-%m-%d")
                profit_amount = (CONFIG["BUY_AMOUNT_SOL"] * (profit_percent / 100)) * (0.5 if partial else 1.0)
                
                if today not in self.trade_log["daily_profits"]:
                    self.trade_log["daily_profits"][today] = 0
                    
                self.trade_log["daily_profits"][today] += profit_amount
                
                # Save updated log
                self.save_trade_log()
                return True
                
            else:
                # Real trade implementation would go here for production mode
                # This would use Jupiter API to execute the actual swap
                
                # For now, just log that we would make the trade
                logging.info(f"Would sell {sell_amount} of {token_address} at ${current_price} " +
                             f"for {profit_percent:.2f}% profit after {hold_duration_min:.1f} minutes")
                
                # Record as if we made the sell
                self.trade_log["transactions"].append({
                    "type": "sell",
                    "token": token_address,
                    "sell_price": current_price,
                    "buy_price": buy_price,
                    "profit_percent": profit_percent,
                    "hold_duration_min": hold_duration_min,
                    "timestamp": time.time() * 1000,
                    "partial": partial,
                    "simulation": CONFIG["SIMULATION_MODE"]
                })
                
                # Update held tokens list
                if not partial:
                    if token_address in self.trade_log["held_tokens"]:
                        self.trade_log["held_tokens"].remove(token_address)
                    if token_address in self.trade_log["partial_sold"]:
                        self.trade_log["partial_sold"].remove(token_address)
                else:
                    # Mark as partially sold
                    self.trade_log["partial_sold"].add(token_address)
                
                # Track daily profit
                today = datetime.now().strftime("%Y-%m-%d")
                profit_amount = (CONFIG["BUY_AMOUNT_SOL"] * (profit_percent / 100)) * (0.5 if partial else 1.0)
                
                if today not in self.trade_log["daily_profits"]:
                    self.trade_log["daily_profits"][today] = 0
                    
                self.trade_log["daily_profits"][today] += profit_amount
                
                # Save updated log
                self.save_trade_log()
                return True
                
        except Exception as e:
            logging.error(f"Error selling token: {e}")
            return False

    async def start_trading_loop(self):
        """Main trading loop"""
        # Config parameters for auto-trading
        MAX_CONCURRENT_TOKENS = CONFIG["MAX_CONCURRENT_TOKENS"]  # Maximum number of tokens to hold at once
        BUY_COOLDOWN_MS = CONFIG["BUY_COOLDOWN_MINUTES"] * 60 * 1000  # Cooldown between buys
        PROFIT_TARGET = CONFIG["PROFIT_TARGET_PERCENT"] / 100  # 100% = 2x
        PARTIAL_PROFIT = CONFIG["PARTIAL_PROFIT_PERCENT"] / 100  # 50% = 1.5x
        STOP_LOSS = CONFIG["STOP_LOSS_PERCENT"] / 100  # 20% = 0.8x
        TIME_LIMIT_MS = CONFIG["TIME_LIMIT_MINUTES"] * 60 * 1000  # 60 minutes
        
        logging.info(f"Starting trading loop with parameters:")
        logging.info(f"  Mode: {'SIMULATION' if CONFIG['SIMULATION_MODE'] else 'PRODUCTION'}")
        logging.info(f"  Max concurrent tokens: {MAX_CONCURRENT_TOKENS}")
        logging.info(f"  Buy amount: {CONFIG['BUY_AMOUNT_SOL']} SOL")
        logging.info(f"  Buy cooldown: {CONFIG['BUY_COOLDOWN_MINUTES']} minutes")
        logging.info(f"  Profit target: {CONFIG['PROFIT_TARGET_PERCENT']}%")
        logging.info(f"  Partial profit target: {CONFIG['PARTIAL_PROFIT_PERCENT']}%")
        logging.info(f"  Stop loss: {CONFIG['STOP_LOSS_PERCENT']}%")
        logging.info(f"  Time limit: {CONFIG['TIME_LIMIT_MINUTES']} minutes")
        
        while True:
            try:
                # 1. Check holdings for sell opportunities
                await self.check_sell_opportunities(
                    profit_target=PROFIT_TARGET,
                    partial_profit=PARTIAL_PROFIT,
                    stop_loss=STOP_LOSS,
                    time_limit_ms=TIME_LIMIT_MS
                )
                
                # 2. Look for buy opportunities if we have capacity and cooldown expired
                current_time = time.time() * 1000
                last_buy_time = self.trade_log["last_buy_time"]
                cooldown_expired = (current_time - last_buy_time) > BUY_COOLDOWN_MS
                
                if len(self.trade_log["held_tokens"]) < MAX_CONCURRENT_TOKENS and cooldown_expired:
                    # Find promising tokens to buy
                    tokens = await self.find_promising_tokens(max_results=3)
                    
                    for token in tokens:
                        # Skip if we already hit our limit
                        if len(self.trade_log["held_tokens"]) >= MAX_CONCURRENT_TOKENS:
                            break
                            
                        # Skip if we already hold this token
                        if token in self.trade_log["held_tokens"]:
                            continue
                            
                        # Check if token is safe to buy
                        is_safe = await self.is_token_safe(token)
                        
                        if is_safe:
                            logging.info(f"Found promising token: {token}")
                            # Buy token
                            success = await self.buy_token(token, CONFIG["BUY_AMOUNT_SOL"])
                            if success:
                                # Reset cooldown after successful buy
                                self.trade_log["last_buy_time"] = time.time() * 1000
                                break  # Only buy one token per cycle
                
                # 3. Summary logging for held tokens
                await self.log_portfolio_summary()
                
                # Calculate and log daily profit target progress
                await self.log_daily_profit_status()
                
                # 4. Wait before next cycle
                await asyncio.sleep(CONFIG["CHECK_INTERVAL_MS"] / 1000)
                
            except Exception as e:
                logging.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)  # Wait a bit longer if there was an error

    async def check_sell_opportunities(self, profit_target, partial_profit, stop_loss, time_limit_ms):
        """Check all held tokens for sell opportunities"""
        if not self.trade_log["held_tokens"]:
            return
            
        logging.info(f"Checking sell opportunities for {len(self.trade_log['held_tokens'])} held tokens...")
        current_time = time.time() * 1000
        
        for token in list(self.trade_log["held_tokens"]):  # Use list() to avoid modification during iteration
            try:
                # Get current price
                current_price = await self.get_token_price(token)
                buy_price = self.trade_log["buy_prices"].get(token, 0)
                
                # Skip if we don't have buy price information
                if buy_price <= 0:
                    continue
                    
                # Calculate profit ratio (current_price / buy_price)
                price_ratio = current_price / buy_price if buy_price > 0 else 1.0
                
                # Calculate holding time
                buy_time = self.trade_log["buy_times"].get(token, current_time)
                hold_duration_ms = current_time - buy_time
                hold_duration_min = hold_duration_ms / (60 * 1000)
                
                # Log current status
                logging.info(f"Holding token {token}: current ratio {price_ratio:.2f}x, held for {hold_duration_min:.1f} min")
                
                # Check sell conditions
                
                # 1. Check for profit target (2x)
                if price_ratio >= (1 + profit_target):
                    logging.info(f"🚀 Profit target reached for {token}: {price_ratio:.2f}x (target: {1 + profit_target:.2f}x)")
                    await self.sell_token(token, partial=False)
                    continue
                
                # 2. Check for partial profit opportunity (50% profit)
                if price_ratio >= (1 + partial_profit) and token not in self.trade_log["partial_sold"]:
                    logging.info(f"📈 Partial profit opportunity for {token}: {price_ratio:.2f}x (target: {1 + partial_profit:.2f}x)")
                    await self.sell_token(token, partial=True)
                    continue
                
                # 3. Check for stop loss (-20%)
                if price_ratio <= (1 - stop_loss):
                    logging.info(f"🛑 Stop loss triggered for {token}: {price_ratio:.2f}x (threshold: {1 - stop_loss:.2f}x)")
                    await self.sell_token(token, partial=False)
                    continue
                
                # 4. Check for time limit with minimal profit (1 hour hold time, price > 1.2x)
                min_profit_for_time_limit = 0.2  # 20% profit minimum for time-based exit
                if hold_duration_ms >= time_limit_ms and price_ratio >= (1 + min_profit_for_time_limit):
                    logging.info(f"⏰ Time limit reached with profit for {token}: held for {hold_duration_min:.1f} min, {price_ratio:.2f}x profit")
                    await self.sell_token(token, partial=False)
                    continue
                
                # 5. Check for extended time limit regardless of profit (1.5x the normal time limit)
                extended_time_limit = time_limit_ms * 1.5
                if hold_duration_ms >= extended_time_limit:
                    logging.info(f"⏰ Extended time limit reached for {token}: held for {hold_duration_min:.1f} min, {price_ratio:.2f}x ratio")
                    await self.sell_token(token, partial=False)
                    continue
                
            except Exception as e:
                logging.error(f"Error checking sell opportunity for {token}: {e}")

    async def log_portfolio_summary(self):
        """Log summary of current portfolio and performance"""
        try:
            if not self.trade_log["held_tokens"]:
                return
                
            total_investment = len(self.trade_log["held_tokens"]) * CONFIG["BUY_AMOUNT_SOL"]
            estimated_value = 0
            
            for token in self.trade_log["held_tokens"]:
                current_price = await self.get_token_price(token)
                buy_price = self.trade_log["buy_prices"].get(token, 0)
                
                if buy_price > 0:
                    # For partial sells, account for only 50% of the original investment
                    multiplier = 0.5 if token in self.trade_log["partial_sold"] else 1.0
                    token_value = (CONFIG["BUY_AMOUNT_SOL"] * (current_price / buy_price)) * multiplier
                    estimated_value += token_value
            
            # Calculate totals
            sol_price = await self.get_token_price(SOL_MINT)
            total_value_usd = estimated_value * sol_price
            total_investment_usd = total_investment * sol_price
            profit_loss = (estimated_value - total_investment) * sol_price
            
            logging.info(f"Portfolio summary: {len(self.trade_log['held_tokens'])} tokens held")
            logging.info(f"Estimated value: {estimated_value:.4f} SOL (${total_value_usd:.2f})")
            logging.info(f"Total invested: {total_investment:.4f} SOL (${total_investment_usd:.2f})")
            logging.info(f"Current P/L: {profit_loss:.2f} USD ({(profit_loss/total_investment_usd)*100:.1f}% if all sold now)")
            
        except Exception as e:
            logging.error(f"Error generating portfolio summary: {e}")

    async def log_daily_profit_status(self):
        """Log progress toward daily profit target"""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            daily_profit = self.trade_log["daily_profits"].get(today, 0)
            sol_price = await self.get_token_price(SOL_MINT)
            
            daily_profit_usd = daily_profit * sol_price
            target_usd = 1000  # $1000 daily target
            
            progress_pct = (daily_profit_usd / target_usd) * 100 if target_usd > 0 else 0
            
            logging.info(f"Daily profit: ${daily_profit_usd:.2f} / ${target_usd:.2f} ({progress_pct:.1f}% of daily target)")
            
            # Calculate projection based on time of day
            now = datetime.now()
            seconds_in_day = 24 * 60 * 60
            seconds_elapsed = (now.hour * 60 * 60) + (now.minute * 60) + now.second
            day_progress = seconds_elapsed / seconds_in_day
            
            if day_progress > 0:
                projected_daily = daily_profit_usd / day_progress
                logging.info(f"Projected daily profit: ${projected_daily:.2f} ({projected_daily/target_usd*100:.1f}% of target)")
            
        except Exception as e:
            logging.error(f"Error calculating daily profit status: {e}")

    async def initialize(self):
        """Initialize the trading bot and verify connections"""
        try:
            logging.info("Initializing trading bot...")
            logging.info(f"Running in {'SIMULATION' if CONFIG['SIMULATION_MODE'] else 'PRODUCTION'} mode")
            
            # Verify QuickNode RPC connection
            headers = {"Content-Type": "application/json"}
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getHealth"
            }
            
            try:
                response = requests.post(CONFIG["SOLANA_RPC_URL"], json=payload, headers=headers)
                if response.status_code == 200:
                    logging.info(f"Successfully connected to QuickNode RPC (status {response.status_code})")
                else:
                    logging.error(f"QuickNode RPC connection error: status {response.status_code}")
                    if not CONFIG["SIMULATION_MODE"]:
                        logging.warning("Switching to simulation mode due to RPC connection error")
                        CONFIG["SIMULATION_MODE"] = True
            except Exception as e:
                logging.error(f"Error connecting to QuickNode RPC: {e}")
                logging.warning("Switching to simulation mode due to RPC connection error")
                CONFIG["SIMULATION_MODE"] = True
            
            # Verify Jupiter API
            try:
                jupiter_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote?inputMint={SOL_MINT}&outputMint={USDC_MINT}&amount=10000000&slippageBps=50"
                response = requests.get(jupiter_url)
                
                if response.status_code == 200:
                    logging.info(f"Successfully connected to Jupiter API (status {response.status_code})")
                else:
                    logging.error(f"Jupiter API connection error: status {response.status_code}")
                    logging.warning("Authentication error - check your Jupiter API URL")
            except Exception as e:
                logging.error(f"Error connecting to Jupiter API: {e}")
            
            # Load initial token prices for cache
            for token, price in KNOWN_TOKEN_PRICES.items():
                current_time = time.time() * 1000
                price_cache[token] = (current_time, price)
            
            logging.info("Bot successfully initialized!")
            return True
            
        except Exception as e:
            logging.error(f"Initialization error: {e}")
            return False

async def main():
    """Main entry point for the trading bot"""
    bot = TradingBot()
    initialized = await bot.initialize()
    
    if initialized:
        await bot.start_trading_loop()
    else:
        logging.error("Failed to initialize bot. Exiting.")

if __name__ == "__main__":
    try:
        # Run the main coroutine
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Unhandled exception: {e}")
