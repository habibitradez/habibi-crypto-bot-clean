import os
import time
import json
import random
import logging
import datetime
import requests
import base64
from typing import Dict, List, Tuple, Optional, Any
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Configuration from environment variables with fallbacks
CONFIG = {
    'SOLANA_RPC_URL': os.environ.get('SOLANA_RPC_URL', ''),
    'JUPITER_API_URL': 'https://quote-api.jup.ag/v6',  # Updated to v6 API
    'WALLET_ADDRESS': os.environ.get('WALLET_ADDRESS', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '100')),
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '40')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '15')),
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '30')),
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '2')),
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '1500')),
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '15')),
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.1')),
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '200'))
}

# Meme token pattern detection - Enhanced with latest trends
MEME_TOKEN_PATTERNS = [
    # Classic meme terms
    "pump", "moon", "pepe", "doge", "shib", "inu", "cat", "elon", "musk", 
    "trump", "biden", "wojak", "chad", "frog", "dog", "puppy", "kitty", 
    "meme", "coin", "stonk", "ape", "rocket", "mars", "lambo", "diamond", 
    "hand", "hodl", "rich", "poor", "trader", "crypto", "token", 
    
    # Popular Solana meme coins
    "bonk", "wif", "dogwifhat", "popcat", "pnut", "peanut", "slerf",
    "myro", "giga", "gigachad", "moodeng", "pengu", "pudgy", "would",
    
    # Animal themes
    "bull", "bear", "hippo", "squirrel", "cat", "doge", "shiba", 
    "monkey", "ape", "panda", "fox", "bird", "eagle", "penguin",
    
    # Internet culture
    "viral", "trend", "hype", "fomo", "mochi", "michi", "ai", "gpt",
    "official", "og", "based", "alpha", "shill", "gem", "baby", "daddy",
    "mini", "mega", "super", "hyper", "ultra", "king", "queen", "lord",
    
    # Solana specific
    "sol", "solana", "solaxy", "solama", "moonlana", "soldoge", "fronk",
    "smog", "sunny", "saga", "spx", "degods", "wepe", "bab"
]

# Track tokens we're monitoring
monitored_tokens = {}
token_buy_timestamps = {}
price_cache = {}
price_cache_time = {}

# SOL token address (used as base currency)
SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"

# Predefined list of known tokens
KNOWN_TOKENS = [
    {"symbol": "SOL", "address": SOL_TOKEN_ADDRESS},
    {"symbol": "BONK", "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"},
    {"symbol": "WIF", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"},
    {"symbol": "HADES", "address": "GzYBeP4qDXP5onnpKKdYw7m6hxzgTBjTTUXkVxZToDsi"},
    {"symbol": "PENGU", "address": "4GUQXsieAfBX4Xfv2eXG3oNkQTVNnbnu6ZNF13uD7hYA"},
    {"symbol": "GIGA", "address": "4HjJphebQ7ogUjRnch39s8Pk5DBmHePAwZrUHW1Ka6UT"},
    {"symbol": "PNUT", "address": "PNUtFk6iQhs2VXiCMQpzGM81PdE7yGL5Y4fo9mFfb7o"},
    {"symbol": "SLERF", "address": "4LLdMU9BLbT39ZLjDgBeZirThcFB5oqkQaEQDyhC7FEW"},
    {"symbol": "WOULD", "address": "WoUDYBcg9YWY5KRrfKwJ3XHMEQWvGZvK7B2B9f11rpiJ"},
    {"symbol": "MOODENG", "address": "7xd71KP4HwQ4sM936xL8JQZHVnrEKcMDDvajdYfJBJCF"}
]

def initialize():
    """Initialize the bot and verify connectivity."""
    logging.info(f"Starting bot initialization...")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info("Running in SIMULATION mode")
    else:
        logging.info("Running in PRODUCTION mode")
    
    # Display configured parameters
    logging.info(f"Profit target: {CONFIG['PROFIT_TARGET_PERCENT']}%")
    logging.info(f"Partial profit target: {CONFIG['PARTIAL_PROFIT_PERCENT']}%")
    logging.info(f"Stop loss: {CONFIG['STOP_LOSS_PERCENT']}%")
    logging.info(f"Time limit: {CONFIG['TIME_LIMIT_MINUTES']} minutes")
    logging.info(f"Buy cooldown: {CONFIG['BUY_COOLDOWN_MINUTES']} minutes")
    logging.info(f"Check interval: {CONFIG['CHECK_INTERVAL_MS']}ms")
    logging.info(f"Max concurrent tokens: {CONFIG['MAX_CONCURRENT_TOKENS']}")
    logging.info(f"Buy amount: {CONFIG['BUY_AMOUNT_SOL']} SOL")
    
    # Verify Solana RPC connection
    try:
        rpc_response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            headers={"Content-Type": "application/json"}
        )
        if rpc_response.status_code == 200:
            logging.info(f"Successfully connected to QuickNode RPC (status {rpc_response.status_code})")
        else:
            logging.error(f"Failed to connect to QuickNode RPC: {rpc_response.status_code}")
            return False
    except Exception as e:
        logging.error(f"Error connecting to QuickNode RPC: {str(e)}")
        return False
    
    # Test Jupiter API connection
    try:
        jupiter_test_url = f"{CONFIG['JUPITER_API_URL']}/quote"
        test_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
            "amount": "100000000",  # 0.1 SOL in lamports
            "slippageBps": "100"
        }
        jupiter_response = requests.get(jupiter_test_url, params=test_params)
        
        if jupiter_response.status_code == 200:
            logging.info(f"Successfully tested Jupiter API integration (status {jupiter_response.status_code})")
        else:
            logging.warning(f"Jupiter API connection warning: status {jupiter_response.status_code}")
            # Not returning False here - we can continue with fallbacks
    except Exception as e:
        logging.warning(f"Jupiter API connection warning: {str(e)}")
        # Not returning False here - we can continue with fallbacks
    
    # Initialize price cache with known tokens
    for token in KNOWN_TOKENS:
        token_price = get_token_price(token['address'])
        if token_price:
            price_cache[token['address']] = token_price
            price_cache_time[token['address']] = time.time()
    
    logging.info("Bot successfully initialized!")
    return True

def is_meme_token(token_address: str, token_name: str = "", token_symbol: str = "") -> bool:
    """Determine if a token is likely a meme token based on patterns."""
    token_address_lower = token_address.lower()
    
    # Check the address itself for meme patterns
    for pattern in MEME_TOKEN_PATTERNS:
        if pattern.lower() in token_address_lower:
            return True
    
    # Check token name and symbol if available
    if token_name or token_symbol:
        token_info = (token_name + token_symbol).lower()
        for pattern in MEME_TOKEN_PATTERNS:
            if pattern.lower() in token_info:
                return True
    
    # Special case for tokens with high potential meme indicators
    high_potential_indicators = ["pump", "moon", "pepe", "doge", "wif", "bonk", "cat", "inu"]
    for indicator in high_potential_indicators:
        if indicator in token_address_lower:
            logging.info(f"High potential meme token detected: {token_address} (contains '{indicator}')")
            return True
            
    # Check for numeric patterns that might indicate a meme (like 420, 69, etc.)
    if "420" in token_address_lower or "69" in token_address_lower or "1337" in token_address_lower:
        logging.info(f"Meme number pattern detected in token: {token_address}")
        return True
        
    # If token was created very recently (within last few hours)
    # This requires transaction analysis which we're already doing in scan_for_new_tokens
    
    return False

def get_token_price(token_address: str) -> Optional[float]:
    """Get token price in SOL using Jupiter API with fallback methods."""
    # Check cache first if it's recent (less than 30 seconds old)
    if token_address in price_cache and token_address in price_cache_time:
        if time.time() - price_cache_time[token_address] < 30:  # 30 second cache
            return price_cache[token_address]
    
    # For SOL token, price is always 1 SOL
    if token_address == SOL_TOKEN_ADDRESS:
        return 1.0
    
    # For other tokens, try Jupiter API
    try:
        # Use Jupiter v6 quote API
        quote_url = f"{CONFIG['JUPITER_API_URL']}/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "1000000000",  # 1 SOL in lamports
            "slippageBps": "100"
        }
        
        response = requests.get(quote_url, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "outAmount" in data["data"]:
                # Calculate price as 1 SOL / outAmount (in token's smallest unit)
                token_price = 1.0 / (int(data["data"]["outAmount"]) / 1000000000)
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                return token_price
        
        # Method 2: Try reverse direction (token to SOL)
        reverse_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": "1000000000",  # 1 unit of token in lamports (adjust if needed)
            "slippageBps": "100"
        }
        
        response = requests.get(quote_url, params=reverse_params)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "outAmount" in data["data"]:
                # Calculate price directly from the quote
                token_price = int(data["data"]["outAmount"]) / 1000000000
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                return token_price
        
        # If we have a cached price, use that as fallback
        if token_address in price_cache:
            logging.warning(f"Using cached price for {token_address} due to API issue")
            return price_cache[token_address]
            
        # Last resort: Known tokens table
        for token in KNOWN_TOKENS:
            if token["address"] == token_address and "price_estimate" in token:
                return token["price_estimate"]
                
        # Really last resort: Generate a random price (only in simulation)
        if CONFIG['SIMULATION_MODE']:
            random_price = random.uniform(0.00000001, 0.001)
            price_cache[token_address] = random_price
            price_cache_time[token_address] = time.time()
            logging.warning(f"Using randomly generated price for {token_address} (simulation only)")
            return random_price
                
    except Exception as e:
        logging.error(f"Error getting price for {token_address}: {str(e)}")
    
    return None

def check_token_liquidity(token_address: str) -> bool:
    """Check if a token has sufficient liquidity."""
    try:
        # Try to get a quote for a small amount to check liquidity
        quote_url = f"{CONFIG['JUPITER_API_URL']}/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "100000000",  # 0.1 SOL in lamports
            "slippageBps": "100"
        }
        
        response = requests.get(quote_url, params=params)
        
        if response.status_code == 200:
            data = response.json()
            # If we got a valid quote, token has liquidity
            if "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                return True
        
        # If Jupiter API fails, assume no liquidity to be safe
        return False
        
    except Exception as e:
        logging.error(f"Error checking liquidity for {token_address}: {str(e)}")
        return False

def get_recent_transactions(limit: int = 100) -> List[Dict]:
    """Get recent transactions from Solana blockchain."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token program address
                {"limit": limit}
            ]
        }
        
        response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code == 200:
            data = response.json()
            if "result" in data:
                return data["result"]
        
        logging.warning(f"Failed to get recent transactions: {response.status_code}")
        return []
        
    except Exception as e:
        logging.error(f"Error getting recent transactions: {str(e)}")
        return []

def analyze_transaction(signature: str) -> List[str]:
    """Analyze a transaction to find new token addresses."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        
        response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        
        if response.status_code != 200:
            return []
            
        data = response.json()
        if "result" not in data or data["result"] is None:
            return []
            
        result = data["result"]
        
        # Look for token creation or mint instructions
        found_tokens = []
        
        if "meta" in result and "innerInstructions" in result["meta"]:
            for inner_instruction in result["meta"]["innerInstructions"]:
                for instruction in inner_instruction.get("instructions", []):
                    # Check for MintTo or InitializeMint instructions
                    if "parsed" in instruction and "type" in instruction["parsed"]:
                        if instruction["parsed"]["type"] in ["mintTo", "initializeMint"]:
                            if "info" in instruction["parsed"] and "mint" in instruction["parsed"]["info"]:
                                token_address = instruction["parsed"]["info"]["mint"]
                                found_tokens.append(token_address)
        
        # Also look for token accounts in account keys
        if "transaction" in result and "message" in result["transaction"]:
            for account in result["transaction"]["message"].get("accountKeys", []):
                if "pubkey" in account and len(account["pubkey"]) == 44:  # Typical Solana address length
                    token_address = account["pubkey"]
                    if token_address not in found_tokens:
                        found_tokens.append(token_address)
        
        return found_tokens
        
    except Exception as e:
        logging.error(f"Error analyzing transaction {signature}: {str(e)}")
        return []

# Monitor token sniping strategy
def scan_for_new_tokens() -> List[str]:
    """Scan blockchain for new token addresses with enhanced detection for promising meme tokens."""
    logging.info(f"Scanning for new tokens (limit: {CONFIG['TOKEN_SCAN_LIMIT']})")
    potential_tokens = []
    promising_meme_tokens = []
    
    # Get recent transactions involving token program
    recent_txs = get_recent_transactions(CONFIG['TOKEN_SCAN_LIMIT'])
    logging.info(f"Found {len(recent_txs)} transactions to analyze")
    
    # Analyze each transaction to find potential new tokens
    for tx in recent_txs:
        if "signature" in tx:
            token_addresses = analyze_transaction(tx["signature"])
            for token_address in token_addresses:
                if token_address not in potential_tokens:
                    potential_tokens.append(token_address)
                    
                    # Immediately check if it's likely a meme token
                    if is_meme_token(token_address):
                        promising_meme_tokens.append(token_address)
    
    # First check and log if we found promising meme tokens
    if promising_meme_tokens:
        logging.info(f"Found {len(promising_meme_tokens)} promising meme tokens out of {len(potential_tokens)} total potential tokens")
        # Return the promising meme tokens first for faster processing
        return promising_meme_tokens
    
    # If no promising meme tokens, return all potential tokens
    logging.info(f"Found {len(potential_tokens)} potential new tokens")
    return potential_tokens

def verify_token(token_address: str) -> bool:
    """Verify if a token is valid, has liquidity, and is worth trading."""
    # Skip SOL token
    if token_address == SOL_TOKEN_ADDRESS:
        return False
        
    # Check if token has a price
    token_price = get_token_price(token_address)
    if token_price is None:
        return False
        
    # Check if token has liquidity
    if not check_token_liquidity(token_address):
        return False
        
    # Token passes verification
    return True

def buy_token(token_address: str, amount_sol: float) -> bool:
    """Buy a token using Jupiter API."""
    if CONFIG['SIMULATION_MODE']:
        token_price = get_token_price(token_address)
        if token_price:
            estimated_tokens = amount_sol / token_price
            logging.info(f"[SIMULATION] Auto-bought {estimated_tokens:.2f} tokens of {token_address} for {amount_sol} SOL")
            # Record buy timestamp
            token_buy_timestamps[token_address] = time.time()
            return True
        else:
            logging.error(f"[SIMULATION] Failed to buy {token_address}: Could not determine price")
            return False
    
    # Real trading logic for production mode
    try:
        # Step 1: Get a quote
        amount_lamports = int(amount_sol * 1000000000)  # Convert SOL to lamports
        
        quote_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": "100",
            "onlyDirectRoutes": "false"
        }
        
        quote_response = requests.get(
            f"{CONFIG['JUPITER_API_URL']}/quote",
            params=quote_params
        )
        
        if quote_response.status_code != 200:
            logging.error(f"Failed to get quote for buying {token_address}: {quote_response.status_code} - {quote_response.text}")
            return False
            
        quote_data = quote_response.json()
        
        # Step 2: Get swap transaction
        swap_payload = {
            "quoteResponse": quote_data["data"],
            "userPublicKey": CONFIG['WALLET_ADDRESS'],
            "wrapUnwrapSOL": True
        }
        
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/swap",
            json=swap_payload,
            headers={"Content-Type": "application/json"}
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to get swap instructions for {token_address}: {swap_response.status_code} - {swap_response.text}")
            return False
            
        swap_data = swap_response.json()
        
        # In a real implementation, you would now:
        # 1. Use the swap instructions to create a transaction
        # 2. Sign the transaction with your wallet's private key
        # 3. Submit the signed transaction to the blockchain
        
        # Since we don't have wallet integration in this code, we'll log the details
        logging.info(f"Successfully prepared swap to buy {token_address} for {amount_sol} SOL")
        logging.info(f"Transaction would need to be signed and submitted with proper wallet integration")
        
        # Record buy timestamp
        token_buy_timestamps[token_address] = time.time()
        return True
        
    except Exception as e:
        logging.error(f"Error buying {token_address}: {str(e)}")
        return False

def sell_token(token_address: str, percentage: int = 100) -> bool:
    """Sell a percentage of token holdings using Jupiter API."""
    if CONFIG['SIMULATION_MODE']:
        token_price = get_token_price(token_address)
        if token_price:
            # In simulation, we assume we've bought CONFIG['BUY_AMOUNT_SOL'] worth of the token
            initial_investment = CONFIG['BUY_AMOUNT_SOL']
            current_value = initial_investment  # In a real scenario, this would be calculated based on current price
            
            if percentage == 100:
                logging.info(f"[SIMULATION] Sold 100% of {token_address} for {current_value} SOL")
            else:
                partial_value = current_value * (percentage / 100)
                logging.info(f"[SIMULATION] Sold {percentage}% of {token_address} for {partial_value} SOL")
            
            return True
        else:
            logging.error(f"[SIMULATION] Failed to sell {token_address}: Could not determine price")
            return False
    
    # Real trading logic for production mode
    try:
        # In a real implementation, you would:
        # 1. Get the token balance from your wallet
        # 2. Calculate the amount to sell based on the percentage
        # 3. Get a quote for selling that amount
        # 4. Get swap instructions
        # 5. Sign and submit the transaction
        
        # Since we don't have full wallet integration, we'll use a simplified approach
        
        # Step 1: Calculate estimated token balance (this would be different in production)
        token_price = get_token_price(token_address)
        if not token_price:
            logging.error(f"Failed to get price for {token_address} during sell")
            return False
            
        estimated_tokens = CONFIG['BUY_AMOUNT_SOL'] / token_price
        tokens_to_sell = estimated_tokens * (percentage / 100)
        
        # Step 2: Get a quote
        token_mint_decimals = 9  # Most tokens use 9 decimals, this would be retrieved in production
        token_amount = int(tokens_to_sell * (10 ** token_mint_decimals))
        
        quote_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": str(token_amount),
            "slippageBps": "100",
            "onlyDirectRoutes": "false"
        }
        
        quote_response = requests.get(
            f"{CONFIG['JUPITER_API_URL']}/quote",
            params=quote_params
        )
        
        if quote_response.status_code != 200:
            logging.error(f"Failed to get quote for selling {token_address}: {quote_response.status_code} - {quote_response.text}")
            return False
            
        quote_data = quote_response.json()
        
        # Step 3: Get swap transaction
        swap_payload = {
            "quoteResponse": quote_data["data"],
            "userPublicKey": CONFIG['WALLET_ADDRESS'],
            "wrapUnwrapSOL": True
        }
        
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/swap",
            json=swap_payload,
            headers={"Content-Type": "application/json"}
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to get swap instructions for selling {token_address}: {swap_response.status_code} - {swap_response.text}")
            return False
            
        swap_data = swap_response.json()
        
        # Log the details since we don't have wallet integration
        if percentage == 100:
            logging.info(f"Successfully prepared swap to sell 100% of {token_address}")
        else:
            logging.info(f"Successfully prepared swap to sell {percentage}% of {token_address}")
            
        logging.info(f"Transaction would need to be signed and submitted with proper wallet integration")
        
        return True
        
    except Exception as e:
        logging.error(f"Error selling {token_address}: {str(e)}")
        return False

def monitor_token_price(token_address: str) -> None:
    """Monitor a token's price and execute the trading strategy."""
    # If we don't have a buy timestamp, record now
    if token_address not in token_buy_timestamps:
        token_buy_timestamps[token_address] = time.time()
    
    # Get initial price if not already monitored
    if token_address not in monitored_tokens:
        initial_price = get_token_price(token_address)
        if initial_price:
            monitored_tokens[token_address] = {
                'initial_price': initial_price,
                'highest_price': initial_price,
                'partial_profit_taken': False,
                'buy_time': token_buy_timestamps[token_address]
            }
        else:
            logging.warning(f"Could not get initial price for {token_address}")
            return
    
    # Get current price
    current_price = get_token_price(token_address)
    if not current_price:
        logging.warning(f"Could not get current price for {token_address}")
        return
    
    # Update highest price if current price is higher
    if current_price > monitored_tokens[token_address]['highest_price']:
        monitored_tokens[token_address]['highest_price'] = current_price
    
    # Calculate price change percentage
    initial_price = monitored_tokens[token_address]['initial_price']
    price_change_pct = ((current_price - initial_price) / initial_price) * 100
    
    # Check if enough time has passed
    time_elapsed_minutes = (time.time() - monitored_tokens[token_address]['buy_time']) / 60
    time_limit_hit = time_elapsed_minutes >= CONFIG['TIME_LIMIT_MINUTES']
    
    # Strategy execution
    if not monitored_tokens[token_address]['partial_profit_taken'] and price_change_pct >= CONFIG['PARTIAL_PROFIT_PERCENT']:
        # Take partial profit at PARTIAL_PROFIT_PERCENT
        logging.info(f"Taking partial profit for {token_address} at {price_change_pct:.2f}% gain")
        if sell_token(token_address, 50):  # Sell 50% of holdings
            monitored_tokens[token_address]['partial_profit_taken'] = True
    
    if price_change_pct >= CONFIG['PROFIT_TARGET_PERCENT']:
        # Take full profit at PROFIT_TARGET_PERCENT
        logging.info(f"Taking full profit for {token_address} at {price_change_pct:.2f}% gain")
        sell_token(token_address)  # Sell 100% of remaining holdings
        # Remove from monitoring
        del monitored_tokens[token_address]
        return
    
    if price_change_pct <= -CONFIG['STOP_LOSS_PERCENT']:
        # Stop loss hit
        logging.info(f"Stop loss triggered for {token_address} at {price_change_pct:.2f}% loss")
        sell_token(token_address)  # Sell 100% of holdings
        # Remove from monitoring
        del monitored_tokens[token_address]
        return
    
    if time_limit_hit:
        if price_change_pct > 0:
            # Time limit hit with profit
            logging.info(f"Time limit reached for {token_address} with {price_change_pct:.2f}% gain")
            sell_token(token_address)  # Sell 100% of holdings
        else:
            # Time limit hit with loss
            logging.info(f"Time limit reached for {token_address} with {price_change_pct:.2f}% loss")
            sell_token(token_address)  # Sell 100% of holdings
        
        # Remove from monitoring
        del monitored_tokens[token_address]
        return

def can_buy_token() -> bool:
    """Check if we can buy another token based on concurrent limits and cooldown."""
    # Check concurrent token limit
    if len(monitored_tokens) >= CONFIG['MAX_CONCURRENT_TOKENS']:
        return False
    
    # Check buy cooldown
    current_time = time.time()
    for timestamp in token_buy_timestamps.values():
        time_since_last_buy = (current_time - timestamp) / 60  # in minutes
        if time_since_last_buy < CONFIG['BUY_COOLDOWN_MINUTES']:
            return False
    
    return True

def trading_loop():
    """Main trading loop."""
    logging.info("Trading loop started")
    
    while True:
        try:
            # If we can buy more tokens
            if can_buy_token():
                # First, check for new tokens
                potential_tokens = scan_for_new_tokens()
                
                # Filter for potentially profitable tokens (especially meme tokens)
                profitable_tokens = []
                
                # Process tokens in batches for efficiency
                token_batch_size = min(10, len(potential_tokens))  # Process up to 10 tokens at once
                
                # First pass: Quick check for obvious meme tokens
                for token_address in potential_tokens[:token_batch_size]:
                    if token_address not in monitored_tokens and is_meme_token(token_address):
                        # Quick check for liquidity before full verification
                        if check_token_liquidity(token_address):
                            profitable_tokens.append({
                                'address': token_address,
                                'is_meme': True,
                                'priority': 1  # High priority for obvious meme tokens
                            })
                
                # If we found high priority tokens, process them first
                if profitable_tokens:
                    sorted_tokens = sorted(profitable_tokens, key=lambda x: x['priority'])
                    token_to_buy = sorted_tokens[0]['address']
                    logging.info(f"Found high potential meme token: {token_to_buy}")
                    
                    # Verify fully before buying
                    if verify_token(token_to_buy):
                        logging.info(f"Verified high potential token, attempting to buy: {token_to_buy}")
                        if buy_token(token_to_buy, CONFIG['BUY_AMOUNT_SOL']):
                            initial_price = get_token_price(token_to_buy)
                            if initial_price:
                                monitored_tokens[token_to_buy] = {
                                    'initial_price': initial_price,
                                    'highest_price': initial_price,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            else:
                                logging.warning(f"Bought token {token_to_buy} but couldn't get initial price")
                
                # If no high priority tokens or buying failed, check the rest
                elif len(potential_tokens) > token_batch_size:
                    remaining_tokens = potential_tokens[token_batch_size:]
                    for token_address in remaining_tokens:
                        if token_address not in monitored_tokens:
                            if verify_token(token_address):
                                profitable_tokens.append({
                                    'address': token_address,
                                    'is_meme': is_meme_token(token_address),
                                    'priority': 2 if is_meme_token(token_address) else 3
                                })
                    
                    # Sort by priority (meme tokens first)
                    if profitable_tokens:
                        sorted_tokens = sorted(profitable_tokens, key=lambda x: x['priority'])
                        token_to_buy = sorted_tokens[0]['address']
                        logging.info(f"Found promising token: {token_to_buy}")
                        if buy_token(token_to_buy, CONFIG['BUY_AMOUNT_SOL']):
                            initial_price = get_token_price(token_to_buy)
                            if initial_price:
                                monitored_tokens[token_to_buy] = {
                                    'initial_price': initial_price,
                                    'highest_price': initial_price,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                
                # If no new tokens found, check the predefined list
                elif len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
                    for token in KNOWN_TOKENS:
                        if token["address"] not in monitored_tokens:
                            if is_meme_token(token["address"], token.get("symbol", "")):
                                logging.info(f"Checking known token: {token['symbol']} ({token['address']})")
                                if verify_token(token["address"]):
                                    if buy_token(token["address"], CONFIG['BUY_AMOUNT_SOL']):
                                        monitored_tokens[token["address"]] = {
                                            'initial_price': get_token_price(token["address"]),
                                            'highest_price': get_token_price(token["address"]),
                                            'partial_profit_taken': False,
                                            'buy_time': time.time()
                                        }
                                    break
            
            # Monitor existing tokens
            tokens_to_monitor = list(monitored_tokens.keys())
            for token_address in tokens_to_monitor:
                monitor_token_price(token_address)
            
            # Sleep before next check
            time.sleep(CONFIG['CHECK_INTERVAL_MS'] / 1000)
            
        except Exception as e:
            logging.error(f"Error in trading loop: {str(e)}")
            time.sleep(5)  # Sleep a bit longer on error

def main():
    """Main entry point."""
    if initialize():
        trading_loop()
    else:
        logging.error("Failed to initialize bot. Please check configurations.")

if __name__ == "__main__":
    main()
