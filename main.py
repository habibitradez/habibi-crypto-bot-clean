import os
import time
import json
import random
import logging
import datetime
import requests
import base64
import base58
import traceback
from typing import Dict, List, Tuple, Optional, Any
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

# Solana imports using solders instead of solana
from solders.keypair import Keypair
from solders.pubkey import Pubkey as PublicKey
from solders.transaction import Transaction, VersionedTransaction
from solders.system_program import transfer, TransferParams
import base58

# Configure logging with both file and console output
current_time = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"bot_log_{current_time}.log"),
        logging.StreamHandler()
    ],
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Configuration from environment variables with fallbacks - UPDATED FOR FAST RUG SNIPING
CONFIG = {
    'SOLANA_RPC_URL': os.environ.get('SOLANA_RPC_URL', ''),
    'JUPITER_API_URL': 'https://quote-api.jup.ag',  # Updated to correct base URL
    'WALLET_ADDRESS': os.environ.get('WALLET_ADDRESS', ''),
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '100')),  # 2x target
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '50')),  # Take half at 50% gain
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '30')),  # Wider stop for volatile new coins
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '5')),  # Very quick exit
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '5')),  # Faster cooldown
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '1000')),  # Check every second
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '10')),  # More positions with smaller amounts
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.15')),  # Keep small to minimize rug risk
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '100')),
    'RETRY_ATTEMPTS': int(os.environ.get('RETRY_ATTEMPTS', '3')),
    'JUPITER_RATE_LIMIT_PER_MIN': int(os.environ.get('JUPITER_RATE_LIMIT_PER_MIN', '3000')),
    'PUMPFUN_API_URL': 'https://api-metis.jup.ag/pump',  # Pump.fun API endpoint through Metis
    'PUMPFUN_SCAN_INTERVAL_SEC': int(os.environ.get('PUMPFUN_SCAN_INTERVAL_SEC', '10'))
}
# Diagnostics flag - set to True for very verbose logging
ULTRA_DIAGNOSTICS = True

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

# Global Variables Section - Defined at the top level
# --------------------------------------------------
# Rate limiting variables
last_api_call_time = 0
api_call_delay = 0.1  # Start with 1.5 seconds between calls (40 calls/min)

# Track tokens we're monitoring
monitored_tokens = {}
token_buy_timestamps = {}
price_cache = {}
price_cache_time = {}

# Stats tracking
tokens_scanned = 0
buy_attempts = 0
buy_successes = 0
sell_attempts = 0
sell_successes = 0
errors_encountered = 0
last_status_time = time.time()
iteration_count = 0

# SOL token address (used as base currency)
SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"

# Predefined list of known tokens
KNOWN_TOKENS = [
    {"symbol": "SOL", "address": SOL_TOKEN_ADDRESS},
    {"symbol": "BONK", "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "tradable": True},
    {"symbol": "WIF", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "tradable": True},  # Updated: WIF is tradable based on logs
    {"symbol": "HADES", "address": "GzYBeP4qDXP5onnpKKdYw7m6hxzgTBjTTUXkVxZToDsi", "tradable": False},
    {"symbol": "PENGU", "address": "4GUQXsieAfBX4Xfv2eXG3oNkQTVNnbnu6ZNF13uD7hYA", "tradable": False},
    {"symbol": "GIGA", "address": "4HjJphebQ7ogUjRnch39s8Pk5DBmHePAwZrUHW1Ka6UT", "tradable": False},
    {"symbol": "PNUT", "address": "PNUtFk6iQhs2VXiCMQpzGM81PdE7yGL5Y4fo9mFfb7o", "tradable": False},
    {"symbol": "SLERF", "address": "4LLdMU9BLbT39ZLjDgBeZirThcFB5oqkQaEQDyhC7FEW", "tradable": False},
    {"symbol": "WOULD", "address": "WoUDYBcg9YWY5KRrfKwJ3XHMEQWvGZvK7B2B9f11rpiJ", "tradable": False},
    {"symbol": "MOODENG", "address": "7xd71KP4HwQ4sM936xL8JQZHVnrEKcMDDvajdYfJBJCF", "tradable": False},
    # Updated list of known tradable tokens
    {"symbol": "JUP", "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "tradable": True},
    {"symbol": "ORCA", "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", "tradable": True},
    {"symbol": "SAMO", "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "tradable": True},
    {"symbol": "RAY", "address": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "tradable": True},
    {"symbol": "STEP", "address": "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT", "tradable": True},
    {"symbol": "RENDER", "address": "RNDRxx6LYgjvGdgkTKYbJ3y4KMqZyWawN7GpfSZJT3z", "tradable": True}
]

class SolanaWallet:
    """Solana wallet implementation for the trading bot."""
    
    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None):
        """Initialize a Solana wallet using solders library."""
        self.rpc_url = rpc_url or CONFIG['SOLANA_RPC_URL']
        
        # Initialize the keypair
        if private_key:
            self.keypair = self._create_keypair_from_private_key(private_key)
        else:
            # Get private key from environment or config
            private_key_env = CONFIG['WALLET_PRIVATE_KEY']
            if private_key_env:
                self.keypair = self._create_keypair_from_private_key(private_key_env)
            else:
                raise ValueError("No private key provided. Set WALLET_PRIVATE_KEY in environment variables or pass it directly.")
        
        self.public_key = self.keypair.pubkey()
        
    def _create_keypair_from_private_key(self, private_key: str) -> Keypair:
        """Create a Solana keypair from a base58 encoded private key string."""
        if ULTRA_DIAGNOSTICS:
            logging.info(f"Creating keypair from private key (length: {len(private_key)})")
            
        secret_bytes = base58.b58decode(private_key)
        
        if ULTRA_DIAGNOSTICS:
            logging.info(f"Secret bytes length: {len(secret_bytes)}")
            
        if len(secret_bytes) == 64:
            logging.info("Using 64-byte secret key")
            return Keypair.from_bytes(secret_bytes)
        elif len(secret_bytes) == 32:
            logging.info("Using 32-byte seed")
            return Keypair.from_seed(secret_bytes)
        else:
            raise ValueError(f"Secret key must be 32 or 64 bytes. Got {len(secret_bytes)} bytes.")
            
    def get_balance(self) -> float:
        """Get the SOL balance of the wallet in SOL units."""
        try:
            logging.info("Getting wallet balance...")
            response = self._rpc_call("getBalance", [str(self.public_key)])
        
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Balance response: {json.dumps(response, indent=2)}")
                
            if 'result' in response and 'value' in response['result']:
                # Convert lamports to SOL (1 SOL = 10^9 lamports)
                balance = response['result']['value'] / 1_000_000_000
                logging.info(f"Wallet balance: {balance} SOL")
                return balance
            
            logging.error(f"Unexpected balance response format: {response}")
            return 0.0
        except Exception as e:
            logging.error(f"Error getting wallet balance: {str(e)}")
            logging.error(traceback.format_exc())
            return 0.0
    
    def _rpc_call(self, method: str, params: List) -> Dict:
        """Make an RPC call to the Solana network."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
    
        if ULTRA_DIAGNOSTICS:
            logging.info(f"Making RPC call: {method} with params {json.dumps(params)}")
        
        # Update headers with QuickNode optimized settings
        headers = {
            "Content-Type": "application/json",
            # No need for QB-CLIENT-ID since you don't have one
        }
    
        response = requests.post(self.rpc_url, json=payload, headers=headers, timeout=10)
    
        if response.status_code == 200:
            response_data = response.json()
        
        if 'error' in response_data:
            logging.error(f"RPC error in response: {response_data['error']}")
            
            return response_data
        else:
            error_text = f"RPC call failed with status {response.status_code}: {response.text}"
            logging.error(error_text)
            raise Exception(error_text)
    
    def sign_and_submit_transaction(self, transaction: Transaction) -> Optional[str]:
        """Sign and submit a transaction to the Solana blockchain."""
        try:
            logging.info("Signing and submitting transaction...")
            
            # Check if this is a versioned transaction
            is_versioned = hasattr(transaction, 'message') and not isinstance(transaction, Transaction)
            
            # Serialize and submit transaction
            logging.info("Serializing and submitting transaction...")
            serialized_tx = base64.b64encode(transaction.serialize()).decode("utf-8")
            
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Serialized tx (first 100 chars): {serialized_tx[:100]}...")
                
            response = self._rpc_call("sendTransaction", [
                serialized_tx, 
                {"encoding": "base64", "skipPreflight": False}
            ])
            
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Transaction submission response: {json.dumps(response, indent=2)}")
                
            if "result" in response:
                signature = response["result"]
                logging.info(f"Transaction submitted successfully: {signature}")
                return signature
            else:
                if "error" in response:
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Transaction error: {error_message}")
                else:
                    logging.error(f"Failed to submit transaction - unexpected response format: {response}")
                return None
                
        except Exception as e:
            logging.error(f"Error signing and submitting transaction: {str(e)}")
            logging.error(traceback.format_exc())
            return None
    
    def get_token_accounts(self, token_address: str) -> List[dict]:
        """Get token accounts owned by this wallet for a specific token."""
        try:
            logging.info(f"Getting token accounts for {token_address}...")
            response = self._rpc_call("getTokenAccountsByOwner", [
                str(self.public_key),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ])
        
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Token accounts response: {json.dumps(response, indent=2)}")
            
            if 'result' in response and 'value' in response['result']:
                accounts = response['result']['value']
                logging.info(f"Found {len(accounts)} token accounts for {token_address}")
                return accounts
            
            logging.warning(f"No token accounts found for {token_address} or unexpected response format")
            return []
        except Exception as e:
            logging.error(f"Error getting token accounts: {str(e)}")
            logging.error(traceback.format_exc())
            return []

    class JupiterSwapHandler:
        """Handler for Jupiter API swap transactions."""
    
    def __init__(self, jupiter_api_url: str):
        """Initialize the Jupiter swap handler.
        
        Args:
            jupiter_api_url: The URL for the Jupiter API
        """
        self.api_url = jupiter_api_url
        logging.info(f"Initialized Jupiter handler with API URL: {jupiter_api_url}")
    
    def get_quote(self, input_mint: str, output_mint: str, amount: str, slippage_bps: str = "500") -> Optional[Dict]:
        """Get a swap quote from Jupiter API."""
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": "false",
                # Remove asLegacyTransaction parameter if it exists in other locations
            }
            
            logging.info(f"Getting quote: {input_mint} → {output_mint}, amount: {amount}, slippage: {slippage_bps}bps")
            
            # For Jupiter v6, the quote endpoint is at /v6/quote
            quote_url = f"{self.api_url}/v6/quote"
            response = requests.get(quote_url, params=params, timeout=10)
            
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Quote response status: {response.status_code}")
                if response.status_code == 200:
                    try:
                        logging.info(f"Quote response preview: {response.text[:200]}...")
                    except Exception as e:
                        logging.error(f"Error logging response preview: {str(e)}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    
                    # Debug log the response structure
                    if ULTRA_DIAGNOSTICS:
                        logging.info(f"Quote response keys: {list(data.keys())}")
                    
                    # Check if the response is directly the quote data (v6 API format)
                    if "outAmount" in data:
                        logging.info(f"Quote received successfully (v6 format)")
                        return data
                    # Check for v4/v5 API format
                    elif "data" in data and "outAmount" in data["data"]:
                        logging.info(f"Quote received successfully (v4/v5 format)")
                        return data["data"]
                    else:
                        # Log the full response for debugging
                        if ULTRA_DIAGNOSTICS:
                            logging.warning(f"Unexpected quote response format: {json.dumps(data)}")
                        logging.warning(f"Quote response has unexpected format")
                        return None
                    
                except json.JSONDecodeError:
                    logging.error(f"Failed to parse quote response as JSON: {response.text[:200]}...")
                    return None
            
            # Better error logging for non-200 responses
            if response.status_code == 404:
                logging.error(f"API endpoint not found (404). URL: {quote_url}")
            elif response.status_code == 400:
                try:
                    error_data = response.json()
                    logging.error(f"Bad request (400): {error_data}")
                except:
                    logging.error(f"Bad request (400): {response.text[:200]}")
            else:
                logging.warning(f"Failed to get quote: {response.status_code} - {response.text[:200]}")
            
            return None
        except Exception as e:
            logging.error(f"Error getting quote: {str(e)}")
            logging.error(traceback.format_exc())
            return None
    
    def prepare_swap_transaction(self, quote_data: Dict, user_public_key: str) -> Optional[Dict]:
        """Prepare a swap transaction using the quote data."""
        try:
            # Add more diagnostic logging
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Preparing swap with quote data keys: {list(quote_data.keys())}")
                
            # For Jupiter v6 API, the payload format is different
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": user_public_key,
                "wrapUnwrapSOL": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto"
            }
            
            logging.info(f"Preparing swap transaction for user: {user_public_key}")
            logging.info(f"Using quote data with outAmount: {quote_data.get('outAmount')}")
            
            # Log the payload structure
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Swap request payload keys: {list(payload.keys())}")
                if 'quoteResponse' in payload:
                    logging.info(f"quoteResponse keys: {list(payload['quoteResponse'].keys())}")
            
            # For Jupiter v6, the swap endpoint is /v6/swap
            response = requests.post(
                f"{self.api_url}/v6/swap",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Swap preparation response status: {response.status_code}")
                if response.status_code == 200:
                    try:
                        logging.info(f"Swap response preview: {response.text[:200]}...")
                    except Exception as e:
                        logging.error(f"Error logging swap response preview: {str(e)}")
            
            if response.status_code == 200:
                try:
                    swap_response = response.json()
                    
                    # Debug log the response structure
                    if ULTRA_DIAGNOSTICS:
                        logging.info(f"Swap response keys: {list(swap_response.keys())}")
                    
                    # Check if the response contains the transaction
                    if "swapTransaction" in swap_response:
                        logging.info("Swap transaction prepared successfully")
                        return swap_response
                    else:
                        if ULTRA_DIAGNOSTICS:
                            logging.warning(f"Swap response does not contain transaction: {json.dumps(swap_response)}")
                        logging.warning(f"Swap response does not contain swapTransaction key")
                        return None
                        
                except json.JSONDecodeError:
                    logging.error(f"Failed to parse swap response as JSON: {response.text[:200]}...")
                    return None
            
            logging.warning(f"Failed to prepare swap transaction: {response.status_code} - {response.text[:200]}")
            return None
        except Exception as e:
            logging.error(f"Error preparing swap transaction: {str(e)}")
            logging.error(traceback.format_exc())
            return None
    
    def deserialize_transaction(self, transaction_data: Dict) -> Optional[Transaction]:
        """Deserialize a transaction from Jupiter API."""
        try:
            # Extract the serialized transaction
            if "swapTransaction" in transaction_data:
                serialized_tx = transaction_data["swapTransaction"]
                logging.info("Deserializing transaction from Jupiter API...")
                
                # Decode the base64 transaction data
                tx_bytes = base64.b64decode(serialized_tx)
                
                # Create a transaction from the bytes
                try:
                    # Try the from_bytes method first
                    transaction = Transaction.from_bytes(tx_bytes)
                except Exception as e:
                    logging.error(f"Error using from_bytes: {str(e)}")
                    # If that fails, try deserialize method with BytesIO
                    from io import BytesIO
                    transaction = Transaction.deserialize(BytesIO(tx_bytes))
                
                logging.info(f"Transaction deserialized successfully")
                return transaction
            else:
                logging.warning("No swapTransaction found in transaction data")
                if ULTRA_DIAGNOSTICS:
                    logging.warning(f"Transaction data keys: {list(transaction_data.keys())}")
                return None
        except Exception as e:
            logging.error(f"Error deserializing transaction: {str(e)}")
            logging.error(traceback.format_exc())
            return None

# Initialize global wallet and Jupiter swap handler
wallet = None
jupiter_handler = None

def get_token_price(token_address: str) -> Optional[float]:
    """Get token price in SOL using Jupiter API with fallback methods."""
    # Check cache first if it's recent (less than 30 seconds old)
    if token_address in price_cache and token_address in price_cache_time:
        if time.time() - price_cache_time[token_address] < 30:  # 30 second cache
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Using cached price for {token_address}: {price_cache[token_address]} SOL")
            return price_cache[token_address]
    
    # For SOL token, price is always 1 SOL
    if token_address == SOL_TOKEN_ADDRESS:
        return 1.0
    
    # Skip tokens we know are not tradable from our KNOWN_TOKENS list
    for token in KNOWN_TOKENS:
        if token["address"] == token_address and token.get("tradable") is False:
            logging.info(f"Skipping price check for known non-tradable token: {token_address} ({token.get('symbol', '')})")
            return None
    
    # For other tokens, try Jupiter API
    try:
        # Use Jupiter v6 quote API
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "1000000000",  # 1 SOL in lamports
            "slippageBps": "500"
        }
        
        logging.info(f"Getting price for {token_address} using Jupiter API...")
        
        # Rate limiting
        global last_api_call_time, api_call_delay
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Make API call
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=params, timeout=10)
        
        # Handle rate limiting
        if response.status_code == 429:
            logging.warning(f"Rate limited by Jupiter API (429). Waiting and retrying...")
            time.sleep(2)
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=params, timeout=10)
            
            if response.status_code == 429:
                api_call_delay += 0.5
                logging.warning(f"Still rate limited. Increased delay to {api_call_delay}s")
        
        # Process successful response
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data:
                out_amount = int(data["outAmount"])
                token_price = 1.0 / (out_amount / 1000000000)
                
                logging.info(f"Got price for {token_address}: {token_price} SOL (1 SOL = {out_amount} tokens)")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                # Mark as tradable
                for token in KNOWN_TOKENS:
                    if token["address"] == token_address:
                        token["tradable"] = True
                        break
                
                return token_price
            elif "data" in data and "outAmount" in data["data"]:
                out_amount = int(data["data"]["outAmount"])
                token_price = 1.0 / (out_amount / 1000000000)
                
                logging.info(f"Got price for {token_address}: {token_price} SOL (1 SOL = {out_amount} tokens)")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                # Mark as tradable
                for token in KNOWN_TOKENS:
                    if token["address"] == token_address:
                        token["tradable"] = True
                        break
                
                return token_price
            else:
                logging.warning(f"Invalid quote response for {token_address}")
        
        # Try reverse direction
        logging.info(f"Trying reverse direction for {token_address} price...")
        reverse_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": "1000000000",
            "slippageBps": "500"
        }
        
        # Rate limiting for reverse call
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Make reverse API call
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=reverse_params, timeout=10)
        
        # Handle rate limiting
        if response.status_code == 429:
            logging.warning(f"Rate limited by Jupiter API (429). Waiting and retrying...")
            time.sleep(2)
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=reverse_params, timeout=10)
            
            if response.status_code == 429:
                api_call_delay += 0.5
                logging.warning(f"Still rate limited. Increased delay to {api_call_delay}s")
        
        # Process successful reverse response
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data:
                out_amount = int(data["outAmount"])
                token_price = out_amount / 1000000000
                
                logging.info(f"Got reverse price for {token_address}: {token_price} SOL (1 token = {out_amount} lamports)")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                # Mark as tradable
                for token in KNOWN_TOKENS:
                    if token["address"] == token_address:
                        token["tradable"] = True
                        break
                
                return token_price
            elif "data" in data and "outAmount" in data["data"]:
                out_amount = int(data["data"]["outAmount"])
                token_price = out_amount / 1000000000
                
                logging.info(f"Got reverse price for {token_address}: {token_price} SOL (1 token = {out_amount} lamports)")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                # Mark as tradable
                for token in KNOWN_TOKENS:
                    if token["address"] == token_address:
                        token["tradable"] = True
                        break
                
                return token_price
        
        # Use fallbacks
        if token_address in price_cache:
            logging.warning(f"Using cached price for {token_address} due to API issue: {price_cache[token_address]} SOL")
            return price_cache[token_address]
            
        for token in KNOWN_TOKENS:
            if token["address"] == token_address and "price_estimate" in token:
                logging.warning(f"Using predefined price estimate for {token_address}: {token['price_estimate']} SOL")
                return token["price_estimate"]
                
        if CONFIG['SIMULATION_MODE']:
            random_price = random.uniform(0.00000001, 0.001)
            price_cache[token_address] = random_price
            price_cache_time[token_address] = time.time()
            logging.warning(f"Using randomly generated price for {token_address} (simulation only): {random_price} SOL")
            return random_price
            
    except Exception as e:
        logging.error(f"Error getting price for {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
    
    logging.error(f"All price retrieval methods failed for {token_address}")
    return None
    
def get_latest_pumpfun_tokens() -> List[Dict]:
    """Get the most recent tokens launched on pump.fun."""
    try:
        logging.info("Fetching recent tokens from pump.fun...")
        
        response = requests.get(
            f"{CONFIG['PUMPFUN_API_URL']}/latest",
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            tokens = response.json()
            logging.info(f"Found {len(tokens)} recent tokens on pump.fun")
            return tokens
        else:
            logging.error(f"Failed to get tokens from pump.fun: {response.status_code}")
            return []
            
    except Exception as e:
        logging.error(f"Error fetching pump.fun tokens: {str(e)}")
        return []
            
    except Exception as e:
        logging.error(f"Error fetching pump.fun tokens: {str(e)}")
        return []

def get_token_details(token_address: str) -> Optional[Dict]:
    """Get detailed information about a token from pump.fun."""
    try:
        response = requests.get(
            f"{CONFIG['PUMPFUN_API_URL']}/token/{token_address}",
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            token_data = response.json()
            return token_data
        else:
            logging.warning(f"Failed to get token details for {token_address}: {response.status_code}")
            return None
            
    except Exception as e:
        logging.error(f"Error getting token details: {str(e)}")
        return None
    
def handle_api_error(response, endpoint: str) -> bool:
    """Handle API errors with improved diagnostics."""
    if response.status_code == 429:
        logging.warning(f"Rate limited on {endpoint} despite paid plan! Retrying once...")
        time.sleep(1)  # Brief wait
        return True  # Should retry
        
    elif response.status_code == 403:
        logging.error(f"Authentication error on {endpoint}. Check your Metis API key configuration")
        return False  # Should not retry
        
    elif response.status_code >= 500:
        logging.warning(f"Server error on {endpoint}: {response.status_code}. Will retry")
        time.sleep(2)
        return True  # Should retry
        
    else:
        logging.error(f"API error on {endpoint}: {response.status_code} - {response.text[:200]}")
        return False  # Should not retry
        
def get_token_details(token_address: str) -> Optional[Dict]:
    """Get detailed information about a token from pump.fun."""
    try:
        response = requests.get(
            f"{CONFIG['PUMPFUN_API_URL']}/token/{token_address}",
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            token_data = response.json()
            return token_data
        else:
            logging.warning(f"Failed to get token details for {token_address}: {response.status_code}")
            return None
            
    except Exception as e:
        logging.error(f"Error getting token details: {str(e)}")
        return None

def initialize():
    """Initialize the bot and verify connectivity."""
    global wallet, jupiter_handler
    
    logging.info(f"Starting bot initialization...")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info("Running in SIMULATION mode")
    else:
        logging.info("Running in PRODUCTION mode")
        
        # Initialize wallet if not in simulation mode
        try:
            logging.info(f"Initializing wallet with RPC URL: {CONFIG['SOLANA_RPC_URL']}")
            if ULTRA_DIAGNOSTICS:
                masked_key = CONFIG['WALLET_PRIVATE_KEY'][:5] + "..." + CONFIG['WALLET_PRIVATE_KEY'][-5:] if CONFIG['WALLET_PRIVATE_KEY'] else "None"
                logging.info(f"Using private key: {masked_key}")
                
            wallet = SolanaWallet(
                private_key=CONFIG['WALLET_PRIVATE_KEY'],
                rpc_url=CONFIG['SOLANA_RPC_URL']
            )
            
            # Check wallet balance
            balance = wallet.get_balance()
            logging.info(f"Wallet connected: {wallet.public_key}")
            logging.info(f"Wallet balance: {balance} SOL")
            
            if balance < CONFIG['BUY_AMOUNT_SOL']:
                logging.warning(f"Wallet balance is lower than buy amount ({CONFIG['BUY_AMOUNT_SOL']} SOL)")
                logging.warning("Trades may fail due to insufficient funds")
        except Exception as e:
            logging.error(f"Failed to initialize wallet: {str(e)}")
            logging.error(traceback.format_exc())
            return False
    
    # Initialize Jupiter handler
    logging.info(f"Initializing Jupiter handler with API URL: {CONFIG['JUPITER_API_URL']}")
    jupiter_handler = JupiterSwapHandler(CONFIG['JUPITER_API_URL'])
    
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
        logging.info("Verifying RPC connection...")
        rpc_response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if rpc_response.status_code == 200:
            logging.info(f"Successfully connected to Solana RPC (status {rpc_response.status_code})")
            # No need to check getLatestBlockhash here since we'll use it during transactions
            # Just verify we got a valid response from getHealth
            if "result" in rpc_response.json():
                logging.info("RPC connection fully verified")
            else:
                logging.warning(f"RPC connection might have issues: {rpc_response.text}")
                # Still continue, as this might just be a format issue
        else:
            logging.error(f"Failed to connect to Solana RPC: {rpc_response.status_code}")
            return False
    except Exception as e:
        logging.error(f"Error connecting to Solana RPC: {str(e)}")
        logging.error(traceback.format_exc())
        return False
    
    # Test Jupiter API connection
    try:
        logging.info("Testing Jupiter API connection...")
        jupiter_test_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        # Use BONK for testing as we know it works
        test_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
            "amount": "100000000",  # 0.1 SOL in lamports
            "slippageBps": "500"
        }
        
        logging.info(f"Testing API at URL: {jupiter_test_url}")
        logging.info(f"Test params: {test_params}")
        
        jupiter_response = requests.get(jupiter_test_url, params=test_params, timeout=10)
        
        if jupiter_response.status_code == 200:
            logging.info(f"Successfully tested Jupiter API integration (status {jupiter_response.status_code})")
            
            if ULTRA_DIAGNOSTICS:
                try:
                    response_data = jupiter_response.json()
                    if "outAmount" in response_data:
                        logging.info(f"Jupiter API test quote: SOL → BONK. Input: {response_data.get('inAmount')} Output: {response_data.get('outAmount')}")
                    elif "data" in response_data:
                        logging.info(f"Jupiter API test quote: SOL → BONK. Input: {response_data['data'].get('inputAmount')} Output: {response_data['data'].get('outAmount')}")
                    else:
                        logging.info(f"Jupiter test response: {json.dumps(response_data, indent=2)}")
                except Exception as e:
                    logging.error(f"Error parsing Jupiter test response: {str(e)}")
        else:
            logging.warning(f"Jupiter API connection warning: status {jupiter_response.status_code}")
            # Not returning False here - we can continue with fallbacks
    except Exception as e:
        logging.warning(f"Jupiter API connection warning: {str(e)}")
        # Not returning False here - we can continue with fallbacks
    
    # Initialize price cache with known tokens
    logging.info("Initializing price cache with known tokens...")
    for token in KNOWN_TOKENS:
        if token["address"] != SOL_TOKEN_ADDRESS:  # Skip SOL, it's always 1.0
            logging.info(f"Getting initial price for {token['symbol']} ({token['address']})...")
            token_price = get_token_price(token['address'])
            if token_price:
                price_cache[token['address']] = token_price
                price_cache_time[token['address']] = time.time()
                logging.info(f"Cached price for {token['symbol']}: {token_price} SOL")
            else:
                logging.warning(f"Could not get initial price for {token['symbol']}")
    
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
    
    return False

def check_token_liquidity(token_address: str) -> bool:
    """Check if a token has sufficient liquidity."""
    # For known tokens like BONK, assume they have liquidity
    for token in KNOWN_TOKENS:
        if token["address"] == token_address:
            logging.info(f"Known token {token_address} ({token.get('symbol', '')}) - Assuming it has liquidity")
            return True
            
    try:
        logging.info(f"Checking liquidity for {token_address}...")
        
        # Try to get a quote for a tiny amount to check liquidity
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "1000000",  # Only 0.001 SOL in lamports - extremely small amount
            "slippageBps": "2000"  # 20% slippage - extremely lenient
        }
        
        logging.info(f"Liquidity check 1: 0.001 SOL → {token_address} with 20% slippage")
        response = requests.get(quote_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # If we got a valid quote, token has liquidity
            if "outAmount" in data and int(data["outAmount"]) > 0:
                logging.info(f"Liquidity check PASSED for {token_address} - Found liquidity")
                return True
            elif "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Liquidity check PASSED for {token_address} - Found liquidity")
                return True
            else:
                logging.info(f"First liquidity check failed - trying reverse direction")
        else:
            logging.info(f"First liquidity check failed with status {response.status_code} - trying reverse direction")
        
        # Try reverse direction (token to SOL) as a backup
        reverse_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": "1000000",  # Small amount of token
            "slippageBps": "2000"  # 20% slippage
        }
        
        logging.info(f"Liquidity check 2: {token_address} → SOL with 20% slippage")
        response = requests.get(quote_url, params=reverse_params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data and int(data["outAmount"]) > 0:
                logging.info(f"Reverse liquidity check PASSED for {token_address}")
                return True
            elif "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Reverse liquidity check PASSED for {token_address}")
                return True
            else:
                logging.info(f"Second liquidity check failed - trying even smaller amount")
        else:
            logging.info(f"Second liquidity check failed with status {response.status_code} - trying even smaller amount")
                
        # Third attempt - try with even smaller amount
        ultra_small_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "500000",  # Only 0.0005 SOL - extremely tiny
            "slippageBps": "3000"  # 30% slippage - super lenient
        }
        
        logging.info(f"Liquidity check 3: 0.0005 SOL → {token_address} with 30% slippage")
        response = requests.get(quote_url, params=ultra_small_params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data and int(data["outAmount"]) > 0:
                logging.info(f"Ultra-small liquidity check PASSED for {token_address}")
                return True
            elif "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Ultra-small liquidity check PASSED for {token_address}")
                return True
            else:
                logging.info(f"Ultra-small liquidity check failed - no quote data returned")
        else:
            logging.info(f"Ultra-small liquidity check failed with status {response.status_code}")
        
        logging.info(f"All liquidity checks FAILED for {token_address}")
        return False
        
    except Exception as e:
        logging.error(f"Error checking liquidity for {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def get_recent_transactions(limit: int = 100) -> List[Dict]:
    """Get recent transactions from Solana blockchain."""
    try:
        logging.info(f"Getting recent transactions (limit: {limit})...")
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
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if "result" in data:
                transactions = data["result"]
                logging.info(f"Retrieved {len(transactions)} recent transactions")
                return transactions
                
            logging.warning(f"Unexpected response format from getSignaturesForAddress: {data}")
        
        logging.warning(f"Failed to get recent transactions: {response.status_code}")
        return []
        
    except Exception as e:
        logging.error(f"Error getting recent transactions: {str(e)}")
        logging.error(traceback.format_exc())
        return []

def analyze_transaction(signature: str) -> List[str]:
    """Analyze a transaction to find new token addresses."""
    try:
        if ULTRA_DIAGNOSTICS:
            logging.info(f"Analyzing transaction: {signature}")
            
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
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code != 200:
            if ULTRA_DIAGNOSTICS:
                logging.warning(f"Failed to get transaction {signature}: {response.status_code}")
            return []
            
        data = response.json()
        if "result" not in data or data["result"] is None:
            if ULTRA_DIAGNOSTICS:
                logging.warning(f"No result in transaction data for {signature}")
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
                                if ULTRA_DIAGNOSTICS:
                                    logging.info(f"Found token in mint instruction: {token_address}")
        
        # Also look for token accounts in account keys
        if "transaction" in result and "message" in result["transaction"]:
            for account in result["transaction"]["message"].get("accountKeys", []):
                if "pubkey" in account and len(account["pubkey"]) == 44:  # Typical Solana address length
                    token_address = account["pubkey"]
                    if token_address not in found_tokens:
                        found_tokens.append(token_address)
                        if ULTRA_DIAGNOSTICS:
                            logging.info(f"Found potential token in account keys: {token_address}")
        
        if found_tokens and ULTRA_DIAGNOSTICS:
            logging.info(f"Found {len(found_tokens)} potential tokens in transaction {signature}")
            
        return found_tokens
        
    except Exception as e:
        logging.error(f"Error analyzing transaction {signature}: {str(e)}")
        if ULTRA_DIAGNOSTICS:
            logging.error(traceback.format_exc())
        return []

# NEW FUNCTION: Scan for ultra-new token launches
def scan_for_new_launches():
    """Scan for newly launched tokens (< 5 minutes old)."""
    try:
        logging.info("Scanning for ultra-new token launches...")
        recent_txs = get_recent_transactions(100)
        new_tokens = []
        
        for tx in recent_txs:
            if "signature" in tx and "blockTime" in tx:
                token_age_minutes = (time.time() - tx["blockTime"]) / 60
                
                # Only look at very recent tokens (< 5 minutes old)
                if token_age_minutes < 5:
                    token_addresses = analyze_transaction(tx["signature"])
                    for token_address in token_addresses:
                        if token_address not in new_tokens:
                            new_tokens.append(token_address)
                            logging.info(f"Found NEW token: {token_address} ({token_age_minutes:.1f} minutes old)")
        
        return new_tokens
    except Exception as e:
        logging.error(f"Error scanning for new launches: {str(e)}")
        return []

def scan_for_new_tokens() -> List[str]:
    """Scan blockchain for new token addresses with enhanced detection for promising meme tokens."""
    global tokens_scanned
    
    logging.info(f"Scanning for new tokens (limit: {CONFIG['TOKEN_SCAN_LIMIT']})")
    potential_tokens = []
    promising_meme_tokens = []
    
    # Add known tradable tokens to potential list to ensure we always have options
    for token in KNOWN_TOKENS:
        if token["address"] not in potential_tokens and token["address"] != SOL_TOKEN_ADDRESS:
            if token.get("tradable", False):
                potential_tokens.append(token["address"])
                if is_meme_token(token["address"], token.get("symbol", "")):
                    promising_meme_tokens.append(token["address"])
                    logging.info(f"Added known tradable meme token to scan results: {token['symbol']} ({token['address']})")
    
    # Only scan for new tokens if we don't have enough tradable ones
    if len(promising_meme_tokens) < 3:
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
                        tokens_scanned += 1
                        
                        # Immediately check if it's likely a meme token
                        if is_meme_token(token_address):
                            # Quick check if it's tradable
                            if check_token_tradability(token_address):
                                promising_meme_tokens.append(token_address)
                                logging.info(f"Found promising tradable meme token: {token_address}")
    
    # First check and log if we found promising meme tokens
    if promising_meme_tokens:
        logging.info(f"Found {len(promising_meme_tokens)} promising meme tokens out of {len(potential_tokens)} total potential tokens")
        # Return the promising meme tokens first for faster processing
        return promising_meme_tokens
    
    # If no promising meme tokens, return only known tradable tokens
    tradable_tokens = [t["address"] for t in KNOWN_TOKENS if t.get("tradable", False) and t["address"] != SOL_TOKEN_ADDRESS]
    if tradable_tokens:
        logging.info(f"No promising meme tokens found, returning {len(tradable_tokens)} known tradable tokens")
        return tradable_tokens
    
    # If no tradable tokens at all, return all potential tokens as a last resort
    logging.info(f"No tradable tokens found, returning all {len(potential_tokens)} potential tokens")
    return potential_tokens
    
def get_latest_pumpfun_tokens() -> List[Dict]:
    """Get the most recent tokens launched on pump.fun."""
    try:
        logging.info("Fetching recent tokens from pump.fun...")
        
        response = requests.get(
            f"{CONFIG['PUMPFUN_API_URL']}/latest",
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            tokens = response.json()
            logging.info(f"Found {len(tokens)} recent tokens on pump.fun")
            return tokens
        else:
            logging.error(f"Failed to get tokens from pump.fun: {response.status_code}")
            return []
            
    except Exception as e:
        logging.error(f"Error fetching pump.fun tokens: {str(e)}")
        return []

def scan_for_new_tokens_pumpfun() -> List[str]:
    """Scan pump.fun for newly launched tokens."""
    global tokens_scanned
    
    logging.info("Scanning pump.fun for newly launched tokens...")
    potential_tokens = []
    
    # Get recent tokens from pump.fun
    recent_tokens = get_latest_pumpfun_tokens()
    
    for token in recent_tokens:
        # Extract token address
        if "address" in token:
            token_address = token["address"]
            # Check if it's new (launched in last 5 minutes)
            if "launchTime" in token:
                launch_time = token["launchTime"]
                current_time = int(time.time() * 1000)  # Convert to milliseconds
                age_minutes = (current_time - launch_time) / (1000 * 60)
                
                if age_minutes <= 5:  # Only tokens less than 5 minutes old
                    logging.info(f"Found new token on pump.fun: {token_address} (age: {age_minutes:.2f} minutes)")
                    potential_tokens.append(token_address)
                    tokens_scanned += 1
    
    logging.info(f"Found {len(potential_tokens)} new tokens on pump.fun in the last 5 minutes")
    return potential_tokens
    
# Update your trading_loop function to include pump.fun scanning
def trading_loop():
    """Main trading loop with pump.fun integration."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay
    
    logging.info("Starting enhanced trading loop with pump.fun integration")
    
    while True:
        iteration_count += 1
        
        try:
            # Print status every 5 minutes
            if time.time() - last_status_time > 300:
                # Status logging unchanged
                pass
            
            # Force sell stale positions
            force_sell_stale_positions()
            
            # Monitor existing positions
            for token_address in list(monitored_tokens.keys()):
                monitor_token_price(token_address)
                time.sleep(0.2)  # Faster with paid API
            
            # Look for new tokens if we have capacity
            if len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
                # IMPORTANT NEW PART: First check pump.fun for the newest tokens
                new_pump_tokens = scan_for_new_tokens_pumpfun()
                
                for token_address in new_pump_tokens:
                    if len(monitored_tokens) >= CONFIG['MAX_CONCURRENT_TOKENS']:
                        break
                    
                    # Skip tokens we're already monitoring
                    if token_address in monitored_tokens:
                        continue
                        
                    # Check cooldown
                    if token_address in token_buy_timestamps:
                        minutes_since_last_buy = (time.time() - token_buy_timestamps[token_address]) / 60
                        if minutes_since_last_buy < CONFIG['BUY_COOLDOWN_MINUTES']:
                            continue
                    
                    # Quick check and buy for tokens from pump.fun
                    if check_token_liquidity(token_address):
                        logging.info(f"SNIPING new pump.fun token: {token_address}")
                        if buy_token(token_address, CONFIG['BUY_AMOUNT_SOL']):
                            logging.info(f"Successfully sniped new pump.fun token: {token_address}")
                            time.sleep(1)  # Brief delay between buys
                
                # If we still have capacity, use regular blockchain scanning as backup
                if len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
                    # Original scanning code unchanged
                    new_tokens = scan_for_new_launches()
                    # Rest unchanged
                    pass
            
            # Faster iteration for quick trades
            time.sleep(0.5)  # Check twice per second with paid API
            
        except Exception as e:
            # Error handling unchanged
            pass

def check_token_tradability(token_address: str) -> bool:
    """Check if a token is tradable on Jupiter API."""
    try:
        # Try to get a quote for a tiny amount to check tradability
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "1000000",  # Only 0.001 SOL in lamports - extremely small amount
            "slippageBps": "2000"  # 20% slippage - extremely lenient
        }
        
        logging.info(f"Checking tradability for {token_address}")
        response = requests.get(quote_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # If we got a valid quote, token is tradable
            if "outAmount" in data and int(data["outAmount"]) > 0:
                logging.info(f"Token {token_address} is tradable on Jupiter")
                return True
            elif "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Token {token_address} is tradable on Jupiter")
                return True
        
        # Check if there's a specific error about tradability
        if response.status_code == 400:
            try:
                error_data = response.json()
                if "error" in error_data and "TOKEN_NOT_TRADABLE" in error_data.get("errorCode", ""):
                    logging.info(f"Token {token_address} explicitly marked as not tradable by Jupiter")
                    return False
            except:
                pass
        
        logging.info(f"Token {token_address} appears to not be tradable on Jupiter")
        return False
        
    except Exception as e:
        logging.error(f"Error checking tradability for {token_address}: {str(e)}")
        return False

def verify_token(token_address: str) -> bool:
    """Verify if a token is valid, has liquidity, and is worth trading."""
    # Skip SOL token
    if token_address == SOL_TOKEN_ADDRESS:
        return False
    
    # Log verification steps
    logging.info(f"Verifying token {token_address}")
    
    # List of tokens we've verified to be tradable
    TRADABLE_TOKENS = [
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
        # Add more tokens here that you've verified are tradable
    ]
    
    if token_address in TRADABLE_TOKENS:
        logging.info(f"Token {token_address} is in verified tradable list")
        return True
    
    # For known tokens, check tradability
    for token in KNOWN_TOKENS:
        if token["address"] == token_address:
            # Explicitly check tradability for all tokens
            is_tradable = check_token_tradability(token_address)
            if is_tradable:
                logging.info(f"Known token {token_address} ({token.get('symbol', '')}) is tradable")
                return True
            else:
                logging.info(f"Known token {token_address} ({token.get('symbol', '')}) is NOT tradable")
                return False
    
    # Check if token has a price
    token_price = get_token_price(token_address)
    if token_price is None:
        logging.info(f"Token {token_address} verification failed: No price available")
        return False
    else:
        logging.info(f"Token {token_address} price: {token_price} SOL")
    
    # Check tradability
    is_tradable = check_token_tradability(token_address)
    if not is_tradable:
        logging.info(f"Token {token_address} verification failed: Not tradable on Jupiter")
        return False
    
    logging.info(f"Token {token_address} PASSED verification")
    return True

def find_and_buy_promising_tokens():
    """Aggressively scan for and buy promising meme tokens."""
    logging.info("Scanning for promising meme tokens...")
    
    # Limit how many tokens we'll buy in one go
    max_buys = 3
    buys_this_round = 0
    
    # Scan for potential tokens
    potential_tokens = scan_for_new_tokens()
    meme_tokens = [t for t in potential_tokens if is_meme_token(t)]
    
    logging.info(f"Found {len(meme_tokens)} potential meme tokens")
    
    for token_address in meme_tokens:
        # Skip if we've reached max buys for this round
        if buys_this_round >= max_buys:
            break
            
        # Skip tokens we're already monitoring
        if token_address in monitored_tokens:
            continue
        
        # Check cooldown
        if token_address in token_buy_timestamps:
            minutes_since_last_buy = (time.time() - token_buy_timestamps[token_address]) / 60
            if minutes_since_last_buy < CONFIG['BUY_COOLDOWN_MINUTES']:
                continue
        
        # Verify token is suitable
        if verify_token(token_address):
            # Check liquidity 
            if check_token_liquidity(token_address):
                logging.info(f"Found promising meme token with liquidity: {token_address}")
                
                # Buy token with more aggressive buy amount
                aggressive_buy_amount = CONFIG['BUY_AMOUNT_SOL'] * 1.5  # 50% more than normal
                if buy_token(token_address, aggressive_buy_amount):
                    logging.info(f"Successfully bought promising meme token: {token_address}")
                    buys_this_round += 1
                else:
                    logging.warning(f"Failed to buy promising meme token: {token_address}")
    
    logging.info(f"Completed aggressive token buying round, bought {buys_this_round} tokens")
    return buys_this_round

def test_buy_flow(token_address="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"):  # Default to BONK
    """Test the entire buy flow with detailed logging."""
    logging.info(f"====== TESTING BUY FLOW FOR {token_address} ======")
    
    # Step 1: Get token price
    logging.info("Step 1: Getting token price")
    token_price = get_token_price(token_address)
    logging.info(f"Token price: {token_price} SOL")
    
    if not token_price:
        logging.error("Failed at step 1: Could not get token price")
        return False
    
    # Step 2: Check liquidity
    logging.info("Step 2: Checking liquidity")
    has_liquidity = check_token_liquidity(token_address)
    logging.info(f"Has liquidity: {has_liquidity}")
    
    if not has_liquidity:
        logging.error("Failed at step 2: Token does not have liquidity")
        return False
    
    # Step 3: Get Jupiter quote
    logging.info("Step 3: Getting Jupiter quote")
    amount_lamports = int(CONFIG['BUY_AMOUNT_SOL'] * 1000000000)
    
    quote_data = jupiter_handler.get_quote(
        input_mint=SOL_TOKEN_ADDRESS,
        output_mint=token_address,
        amount=str(amount_lamports),
        slippage_bps="1000"
    )
    
    if quote_data:
        logging.info(f"Quote data received with keys: {list(quote_data.keys())}")
    else:
        logging.error("Failed at step 3: Could not get Jupiter quote")
        return False
    
    # Step 4: Prepare swap transaction
    logging.info("Step 4: Preparing swap transaction")
    swap_data = jupiter_handler.prepare_swap_transaction(
        quote_data=quote_data,
        user_public_key=str(wallet.public_key)
    )
    
    if swap_data:
        logging.info(f"Swap data received with keys: {list(swap_data.keys())}")
    else:
        logging.error("Failed at step 4: Could not prepare swap transaction")
        return False
    
    # Step 5: Deserialize transaction
    logging.info("Step 5: Deserializing transaction")
    transaction = jupiter_handler.deserialize_transaction(swap_data)
    
    if transaction:
        logging.info(f"Transaction deserialized successfully")
    else:
        logging.error("Failed at step 5: Could not deserialize transaction")
        return False
    
    # Step 6: Sign and submit transaction (only if not in simulation)
    if not CONFIG['SIMULATION_MODE']:
        logging.info("Step 6: Signing and submitting transaction")
        signature = wallet.sign_and_submit_transaction(transaction)
        
        if signature:
            logging.info(f"Transaction submitted successfully: {signature}")
        else:
            logging.error("Failed at step 6: Could not sign and submit transaction")
            return False
    
    logging.info("====== BUY FLOW TEST COMPLETED SUCCESSFULLY ======")
    return True

def debug_buy_bonk():
    """Debug function to analyze transaction structure when buying BONK."""
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    tiny_amount = 0.03  # Small test amount
    
    logging.info(f"DEBUG: Starting detailed transaction analysis for BONK purchase")
    
    try:
        # Calculate amount in lamports
        amount_lamports = int(tiny_amount * 1000000000)
        
        # Step 1: Get quote and log complete response
        logging.info(f"DEBUG: Getting quote for SOL → {bonk_address}, amount: {amount_lamports}")
        quote_response = requests.get(
            f"{CONFIG['JUPITER_API_URL']}/v6/quote",
            params={
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": bonk_address,
                "amount": str(amount_lamports),
                "slippageBps": "500"
            },
            timeout=10
        )
        
        if quote_response.status_code != 200:
            logging.error(f"DEBUG: Quote API failed: {quote_response.status_code} - {quote_response.text}")
            return False
        
        quote_data = quote_response.json()
        logging.info(f"DEBUG: Quote response structure: {json.dumps(quote_data, indent=2)[:1000]}...")
        
        # Step 2: Create swap transaction and log complete request/response
        logging.info(f"DEBUG: Preparing swap transaction")
        swap_payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapUnwrapSOL": True
        }
        logging.info(f"DEBUG: Swap payload structure: {json.dumps(swap_payload, indent=2)[:1000]}...")
        
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/v6/swap",
            json=swap_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if swap_response.status_code != 200:
            logging.error(f"DEBUG: Swap API failed: {swap_response.status_code} - {swap_response.text}")
            return False
        
        swap_data = swap_response.json()
        logging.info(f"DEBUG: Swap response keys: {list(swap_data.keys())}")
        
        # Step 3: Analyze transaction
        serialized_tx = swap_data["swapTransaction"]
        logging.info(f"DEBUG: Serialized transaction length: {len(serialized_tx)}")
        logging.info(f"DEBUG: Transaction preview: {serialized_tx[:100]}...")
        
        # Step 4: Get wallet information
        balance = wallet.get_balance()
        logging.info(f"DEBUG: Current wallet balance: {balance} SOL")
        
        # Step 5: Log transaction fee structure if available
        if "feeStructure" in swap_data:
            logging.info(f"DEBUG: Fee structure: {json.dumps(swap_data['feeStructure'], indent=2)}")
        
        # Step 6: Submit with minimal parameters and detailed logging
        logging.info(f"DEBUG: Submitting transaction")
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "preflightCommitment": "processed"
            }
        ])
        
        # Step 7: Analyze response in detail
        if "result" in response:
            signature = response["result"]
            logging.info(f"DEBUG: Transaction submitted with signature: {signature}")
            
            # Check for dummy signature
            if signature == "1111111111111111111111111111111111111111111111111111111111111111":
                logging.error("DEBUG: Received dummy signature")
                
                # Try to get detailed error information
                status_response = wallet._rpc_call("getTransaction", [
                    signature,
                    {"encoding": "json"}
                ])
                logging.error(f"DEBUG: Transaction status details: {json.dumps(status_response, indent=2)}")
            
            return signature != "1111111111111111111111111111111111111111111111111111111111111111"
        else:
            if "error" in response:
                error_data = response.get("error", {})
                error_message = error_data.get("message", "Unknown")
                error_code = error_data.get("code", "Unknown")
                logging.error(f"DEBUG: Transaction error: {error_message} (Code: {error_code})")
                logging.error(f"DEBUG: Full error data: {json.dumps(error_data, indent=2)}")
            else:
                logging.error(f"DEBUG: Unexpected response format: {json.dumps(response, indent=2)}")
            return False
    
    except Exception as e:
        logging.error(f"DEBUG: Error in transaction debug process: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
def tiny_buy_test():
    """Super minimal test with tiny amount to check RPC provider requirements."""
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    micro_amount = 0.01  # Just 0.01 SOL - extremely small test amount
    
    logging.info(f"RPC TEST: Attempting minimal BONK purchase with {micro_amount} SOL")
    
    try:
        # Get super simple quote
        amount_lamports = int(micro_amount * 1000000000)
        quote_response = requests.get(
            f"{CONFIG['JUPITER_API_URL']}/v6/quote",
            params={
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": bonk_address,
                "amount": str(amount_lamports),
                "slippageBps": "2000"
            },
            timeout=10
        )
        
        if quote_response.status_code != 200:
            logging.error(f"RPC TEST: Quote API failed: {quote_response.status_code}")
            return False
        
        quote_data = quote_response.json()
        
        # Create absolutely minimal swap payload
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/v6/swap",
            json={
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True
            },
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if swap_response.status_code != 200:
            logging.error(f"RPC TEST: Swap API failed: {swap_response.status_code}")
            return False
        
        swap_data = swap_response.json()
        serialized_tx = swap_data["swapTransaction"]
        
        # Try different RPC submission configurations
        configs_to_try = [
            {"name": "Basic", "params": {"encoding": "base64"}},
            {"name": "SkipPreflight", "params": {"encoding": "base64", "skipPreflight": True}},
            {"name": "Processed", "params": {"encoding": "base64", "preflightCommitment": "processed"}},
            {"name": "SkipPreflight+Processed", "params": {"encoding": "base64", "skipPreflight": True, "preflightCommitment": "processed"}},
            {"name": "WithRetries", "params": {"encoding": "base64", "skipPreflight": True, "preflightCommitment": "processed", "maxRetries": 5}}
        ]
        
        for config in configs_to_try:
            logging.info(f"RPC TEST: Trying configuration: {config['name']}")
            
            response = wallet._rpc_call("sendTransaction", [
                serialized_tx,
                config["params"]
            ])
            
            if "result" in response:
                signature = response["result"]
                if signature == "1111111111111111111111111111111111111111111111111111111111111111":
                    logging.error(f"RPC TEST: Configuration {config['name']} - Received dummy signature")
                else:
                    logging.info(f"RPC TEST: SUCCESS with configuration {config['name']} - Signature: {signature}")
                    logging.info(f"FOUND WORKING CONFIGURATION: {config['params']}")
                    return True
            else:
                if "error" in response:
                    error_data = response.get("error", {})
                    error_message = error_data.get("message", "Unknown")
                    error_code = error_data.get("code", "Unknown")
                    logging.error(f"RPC TEST: Configuration {config['name']} - Error: {error_message} (Code: {error_code})")
                else:
                    logging.error(f"RPC TEST: Configuration {config['name']} - Unexpected response")
        
        logging.error("RPC TEST: All configurations failed")
        return False
            
    except Exception as e:
        logging.error(f"RPC TEST: Error in test: {str(e)}")
        return False
        
def test_sol_transfer():
    """Test a basic SOL transfer to see if any transactions work with this RPC provider."""
    logging.info("RPC TEST: Testing basic SOL transfer")
    
    try:
        # Create a simple SOL transfer instruction to your own wallet
        # This is the simplest possible transaction type
        
        amount_lamports = 100000  # Just 0.0001 SOL
        transfer_instruction = transfer(
            TransferParams(
                from_pubkey=wallet.public_key,
                to_pubkey=wallet.public_key,  # Send to self
                lamports=amount_lamports
            )
        )
        
        # Create a basic transaction
        from solders.message import Message
        
        # Get a recent blockhash
        blockhash_response = wallet._rpc_call("getLatestBlockhash", [])
        blockhash = blockhash_response["result"]["value"]["blockhash"]
        
        # Create legacy transaction
        message = Message.new_with_blockhash(
            [transfer_instruction], 
            wallet.public_key,
            blockhash
        )
        
        # Sign the transaction
        transaction = Transaction(
            message=message,
            signatures=[wallet.keypair.sign_message(message.serialize())]
        )
        
        # Try different submission methods
        configs_to_try = [
            {"name": "Minimal", "params": {"encoding": "base64"}},
            {"name": "WithFees", "params": {"encoding": "base64", "maxRetries": 3, "skipPreflight": True}}
        ]
        
        for config in configs_to_try:
            logging.info(f"RPC TEST: Trying SOL transfer with configuration: {config['name']}")
            
            serialized_tx = base64.b64encode(transaction.serialize()).decode("utf-8")
            response = wallet._rpc_call("sendTransaction", [
                serialized_tx,
                config["params"]
            ])
            
            if "result" in response:
                signature = response["result"]
                if signature == "1111111111111111111111111111111111111111111111111111111111111111":
                    logging.error(f"RPC TEST: SOL transfer - Received dummy signature")
                else:
                    logging.info(f"RPC TEST: SOL transfer SUCCEEDED with configuration {config['name']} - Signature: {signature}")
                    logging.info(f"FOUND WORKING CONFIGURATION for basic transactions: {config['params']}")
                    return True
            else:
                if "error" in response:
                    error_data = response.get("error", {})
                    error_message = error_data.get("message", "Unknown")
                    error_code = error_data.get("code", "Unknown")
                    logging.error(f"RPC TEST: SOL transfer - Error: {error_message} (Code: {error_code})")
        
        logging.error("RPC TEST: All SOL transfer configurations failed")
        return False
            
    except Exception as e:
        logging.error(f"RPC TEST: Error in SOL transfer test: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def force_buy_token():
    """Force buy a token to test trading functionality by trying multiple tokens in sequence."""
    # List of tokens to try, in order of preference
    tokens_to_try = [
        {"symbol": "BONK", "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"},
        {"symbol": "JUP", "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"},
        {"symbol": "SAMO", "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"},
        {"symbol": "WIF", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"},
        {"symbol": "ORCA", "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE"},
        {"symbol": "RAY", "address": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R"}
    ]
    
    logging.info("=" * 50)
    logging.info("ATTEMPTING TO BUY A TEST TOKEN")
    logging.info("=" * 50)
    
    for token in tokens_to_try:
        symbol = token["symbol"]
        address = token["address"]
        
        logging.info(f"Trying to buy {symbol} ({address})...")
        
        # First verify if token is tradable
        is_tradable = check_token_tradability(address)
        if not is_tradable:
            logging.warning(f"{symbol} is not tradable on Jupiter - trying next token")
            continue
        
        # If tradable, try to buy it
        logging.info(f"{symbol} is tradable! Attempting to buy...")
        
        if buy_token(address, CONFIG['BUY_AMOUNT_SOL']):
            initial_price = get_token_price(address)
            if initial_price:
                monitored_tokens[address] = {
                    'initial_price': initial_price,
                    'highest_price': initial_price,
                    'partial_profit_taken': False,
                    'buy_time': time.time()
                }
                logging.info(f"SUCCESS! Bought and monitoring {symbol} at {initial_price}")
                return True
        
        logging.warning(f"Failed to buy {symbol} - trying next token")
    
    logging.error("Failed to buy any test token after trying all options")
    return False
    
import re  # Make sure to add this import at the top if it's not there

def test_simple_sol_transfer():
    """Test a basic SOL self-transfer to verify wallet signing capability."""
    try:
        logging.info("Testing wallet signing capability with simple SOL transfer...")
        amount = 100  # Just 0.0000001 SOL (100 lamports)
        
        # Get a recent blockhash
        blockhash_response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getLatestBlockhash"
            },
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if blockhash_response.status_code != 200:
            logging.error(f"Failed to get recent blockhash: {blockhash_response.status_code}")
            return False
            
        blockhash_data = blockhash_response.json()
        blockhash_str = blockhash_data["result"]["value"]["blockhash"]
        
        # Convert string blockhash to Hash object
        from solders.hash import Hash
        blockhash = Hash.from_string(blockhash_str)
        
        # Create transfer instruction
        from solders.message import Message
        from solders.system_program import transfer, TransferParams
        
        transfer_instruction = transfer(
            TransferParams(
                from_pubkey=wallet.public_key,
                to_pubkey=wallet.public_key,  # Transfer to self
                lamports=amount
            )
        )
        
        # Create transaction message
        message = Message.new_with_blockhash(
            [transfer_instruction],
            wallet.public_key,
            blockhash
        )
        
        # Sign message and create transaction
        from solders.signature import Signature
        signature = wallet.keypair.sign_message(bytes(message))
        tx = Transaction(
            message=message,
            signatures=[signature]
        )
        
        # Serialize and submit
        serialized_tx = base64.b64encode(tx.serialize()).decode("utf-8")
        
        response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    serialized_tx,
                    {"encoding": "base64", "skipPreflight": True}
                ]
            },
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        response_data = response.json()
        
        if "result" in response_data:
            signature = response_data["result"]
            logging.info(f"Simple SOL transfer successful! Signature: {signature}")
            logging.info("Wallet signing capability confirmed.")
            return True
        else:
            error = response_data.get("error", {})
            error_message = error.get("message", "Unknown error")
            logging.error(f"Simple SOL transfer failed: {error_message}")
            logging.error("Wallet signing capability test failed.")
            return False
            
    except Exception as e:
        logging.error(f"Error testing SOL transfer: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
def buy_token(token_address: str, amount_sol: float) -> bool:
    """Buy a token using Jupiter API with direct transaction submission approach."""
    global buy_attempts, buy_successes, last_api_call_time, api_call_delay
    
    buy_attempts += 1
    logging.info(f"Starting buy process for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        # Simulation mode unchanged
        token_price = get_token_price(token_address)
        if token_price:
            estimated_tokens = amount_sol / token_price
            logging.info(f"[SIMULATION] Auto-bought {estimated_tokens:.2f} tokens of {token_address} for {amount_sol} SOL")
            token_buy_timestamps[token_address] = time.time()
            buy_successes += 1
            return True
        else:
            logging.error(f"[SIMULATION] Failed to buy {token_address}: Could not determine price")
            return False
    
    try:
        # Use smaller amount for testing
        test_amount = 0.01  # Just 0.01 SOL for testing
        amount_lamports = int(test_amount * 1000000000)
        
        # Step 1: Get quote
        logging.info(f"Getting quote for SOL → {token_address}, amount: {amount_lamports} lamports")
        
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        last_api_call_time = time.time()
        quote_response = requests.get(
            f"{CONFIG['JUPITER_API_URL']}/v6/quote",
            params={
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": "1000"  # Using 10% slippage
            },
            timeout=10
        )
        
        # Handle rate limiting with exponential backoff
        if quote_response.status_code == 429:
            backoff_time = min(api_call_delay * 2, 10)  # Double the delay but cap at 10 seconds
            logging.warning(f"Rate limited (429). Backing off for {backoff_time} seconds...")
            time.sleep(backoff_time)
            
            # Retry the request
            last_api_call_time = time.time()
            quote_response = requests.get(
                f"{CONFIG['JUPITER_API_URL']}/v6/quote",
                params={
                    "inputMint": SOL_TOKEN_ADDRESS,
                    "outputMint": token_address,
                    "amount": str(amount_lamports),
                    "slippageBps": "1000"
                },
                timeout=10
            )
        
        if quote_response.status_code != 200:
            logging.error(f"Quote API failed: {quote_response.status_code} - {quote_response.text[:200]}")
            return False
        
        quote_data = quote_response.json()
        
        # Step 2: Create swap transaction with absolute minimal parameters
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
            
        last_api_call_time = time.time()
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/v6/swap",
            json={
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "asLegacyTransaction": True  # Use legacy transaction format like in successful tx
            },
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        # Handle rate limiting with exponential backoff
        if swap_response.status_code == 429:
            backoff_time = min(api_call_delay * 2, 10)  # Double the delay but cap at 10 seconds
            logging.warning(f"Rate limited (429). Backing off for {backoff_time} seconds...")
            time.sleep(backoff_time)
            
            # Retry the request
            last_api_call_time = time.time()
            swap_response = requests.post(
                f"{CONFIG['JUPITER_API_URL']}/v6/swap",
                json={
                    "quoteResponse": quote_data,
                    "userPublicKey": str(wallet.public_key),
                    "wrapUnwrapSOL": True,
                    "asLegacyTransaction": True
                },
                headers={"Content-Type": "application/json"},
                timeout=10
            )
        
        if swap_response.status_code != 200:
            logging.error(f"Swap API failed: {swap_response.status_code} - {swap_response.text[:200]}")
            return False
        
        swap_data = swap_response.json()
        if "swapTransaction" not in swap_data:
            logging.error(f"No transaction in swap response: {swap_data}")
            return False
        
        # Step 3: Submit transaction with bare minimum parameters
        serialized_tx = swap_data["swapTransaction"]
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {"encoding": "base64", "skipPreflight": True}
        ])
        
        if "result" in response:
            signature = response["result"]
            
            if signature == "1111111111111111111111111111111111111111111111111111111111111111":
                logging.error("Transaction failed - received dummy signature")
                return False
            
            logging.info(f"Transaction submitted: {signature}")
            
            # Record as success
            token_buy_timestamps[token_address] = time.time()
            buy_successes += 1
            return True
        else:
            error_info = response.get("error", {})
            error_message = error_info.get("message", "Unknown error")
            logging.error(f"Transaction error: {error_message}")
            return False
            
    except Exception as e:
        logging.error(f"Error buying {token_address}: {str(e)}")
        return False

# IMPROVED SELL FUNCTION with better retry logic and error handling
def sell_token(token_address: str, percentage: int = 100) -> bool:
    """Sell token using direct Jupiter API approach with enhanced verification."""
    global sell_attempts, sell_successes, last_api_call_time, api_call_delay
    
    sell_attempts += 1
    logging.info(f"===== STARTING SELL PROCESS =====")
    logging.info(f"Token to sell: {token_address}")
    logging.info(f"Percentage to sell: {percentage}%")
    
    if CONFIG['SIMULATION_MODE']:
        # Simulation mode code unchanged
        logging.info(f"[SIMULATION] Sold {percentage}% of {token_address}")
        sell_successes += 1
        return True
    
    try:
        # Validate wallet
        if wallet is None:
            logging.error("Wallet not initialized")
            return False
        
        # Step 1: Verify token ownership and amount
        logging.info(f"Getting token accounts for {token_address}")
        token_accounts = wallet.get_token_accounts(token_address)
        
        if not token_accounts or len(token_accounts) == 0:
            logging.error(f"No token accounts found for {token_address}")
            return False
        
        # Get token amount from the first account
        token_account = token_accounts[0]
        token_account_address = token_account['pubkey']
        
        try:
            token_amount = int(token_account['account']['data']['parsed']['info']['tokenAmount']['amount'])
            logging.info(f"Token amount in wallet: {token_amount}")
        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Failed to get token amount: {str(e)}")
            return False
        
        if token_amount <= 0:
            logging.error(f"Zero token balance for {token_address}")
            return False
        
        # Calculate amount to sell
        amount_to_sell = int(token_amount * percentage / 100)
        logging.info(f"Amount to sell: {amount_to_sell} ({percentage}% of {token_amount})")
        
        # Apply rate limiting
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            time.sleep(api_call_delay - time_since_last_call)
        
        # Step 2: Get Jupiter quote for selling
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_params = {
            "inputMint": token_address,  
            "outputMint": SOL_TOKEN_ADDRESS,  # Selling back to SOL
            "amount": str(amount_to_sell),
            "slippageBps": "1000",
            "onlyDirectRoutes": "true"  # Added for simpler routing
        }
        
        last_api_call_time = time.time()
        logging.info(f"Getting quote: {token_address} → SOL, amount: {amount_to_sell}")
        quote_response = requests.get(quote_url, params=quote_params, timeout=10)
        
        if quote_response.status_code != 200:
            logging.error(f"Quote API failed: {quote_response.status_code} - {quote_response.text[:200]}")
            return False
        
        quote_data = quote_response.json()
        if "outAmount" not in quote_data:
            logging.error(f"Invalid quote response: {quote_data}")
            return False
            
        logging.info(f"Quote received successfully")
        
        # Step 3: Prepare swap transaction
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_payload = {
            "userPublicKey": str(wallet.public_key),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote_data,
            "asLegacyTransaction": True,  # Important: Using legacy transaction format
            "prioritizationFeeLamports": "auto",  # Add priority fee
            "dynamicComputeUnitLimit": True  # Add compute limit adjustment
        }
        
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            time.sleep(api_call_delay - time_since_last_call)
            
        last_api_call_time = time.time()
        logging.info(f"Preparing swap transaction for selling {token_address}")
        swap_response = requests.post(swap_url, json=swap_payload, 
                                     headers={"Content-Type": "application/json"}, 
                                     timeout=10)
        
        if swap_response.status_code != 200:
            logging.error(f"Swap preparation failed: {swap_response.status_code} - {swap_response.text[:200]}")
            return False
            
        swap_data = swap_response.json()
        if "swapTransaction" not in swap_data:
            logging.error(f"No swap transaction in response: {swap_data}")
            return False
        
        # Step 4: Submit transaction directly (without deserializing)
        serialized_tx = swap_data["swapTransaction"]
        logging.info(f"Got serialized transaction (length: {len(serialized_tx)})")
        
        # Submit transaction with skipPreflight=true
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,  # Skip preflight for higher success rate
                "preflightCommitment": "confirmed",  # Use confirmed instead of processed
                "maxRetries": 3  # Add retries
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            # Check for dummy signature (all 1's)
            if signature == "1111111111111111111111111111111111111111111111111111111111111111":
                logging.error("Transaction failed - received dummy signature")
                return False
                
            logging.info(f"Transaction submitted successfully: {signature}")
            logging.info(f"Check transaction on Solscan: https://solscan.io/tx/{signature}")
            
            # Wait for confirmation with retries
            confirmed = False
            for i in range(5):  # Try 5 times
                logging.info(f"Waiting for confirmation (attempt {i+1}/5)...")
                time.sleep(3)  # Wait between checks
                
                status_response = wallet._rpc_call("getSignatureStatuses", [
                    [signature],
                    {"searchTransactionHistory": True}  # Important: search history
                ])
                
                if "result" in status_response and status_response["result"]["value"][0]:
                    status = status_response["result"]["value"][0]
                    if status.get("confirmationStatus") in ["confirmed", "finalized"]:
                        confirmed = True
                        logging.info(f"Transaction confirmed with status: {status.get('confirmationStatus')}")
                        break
            
            if not confirmed:
                logging.warning("Transaction not confirmed within timeout - checking token account anyway")
            
            # Verify the tokens were actually sold - check token balance after sale
            for i in range(3):
                time.sleep(2)  # Wait before checking
                logging.info(f"Verifying token sale (attempt {i+1}/3)...")
                new_token_accounts = wallet.get_token_accounts(token_address)
                
                # If selling 100%, account might be closed
                if percentage == 100 and (not new_token_accounts or len(new_token_accounts) == 0):
                    logging.info("Token account closed after 100% sale - success!")
                    sell_successes += 1
                    return True
                
                # If account still exists, check if balance is reduced
                if new_token_accounts and len(new_token_accounts) > 0:
                    try:
                        new_amount = int(new_token_accounts[0]['account']['data']['parsed']['info']['tokenAmount']['amount'])
                        expected_remaining = token_amount - amount_to_sell
                        
                        # Allow some tolerance for fees
                        if percentage == 100 and new_amount < token_amount * 0.05:  # Less than 5% remaining
                            logging.info(f"Almost all tokens sold (remaining: {new_amount}) - success!")
                            sell_successes += 1
                            return True
                        elif percentage < 100 and abs(new_amount - expected_remaining) < expected_remaining * 0.05:
                            logging.info(f"Partial sale verified: {token_amount} -> {new_amount}")
                            sell_successes += 1
                            return True
                    except (KeyError, IndexError, ValueError) as e:
                        logging.error(f"Error checking updated token amount: {e}")
            
            # If we can't verify the token sale, but transaction appeared to succeed
            logging.warning("Could not verify token sale completely, but transaction was sent")
            sell_successes += 1
            return True
        else:
            if "error" in response:
                error_message = response.get("error", {}).get("message", "Unknown error")
                error_code = response.get("error", {}).get("code", "Unknown code")
                logging.error(f"Transaction error: {error_message} (Code: {error_code})")
            else:
                logging.error(f"Failed to submit transaction - unexpected response format")
            return False
    
    except Exception as e:
        logging.error(f"Error selling {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
def force_sell_all_positions():  # THIS IS CORRECT - SAME LEVEL AS OTHER FUNCTIONS
    """Force sell all current positions."""
    logging.info("Force selling all current positions...")
    
    for token_address in list(monitored_tokens.keys()):
        logging.info(f"Force selling {token_address}")
        if sell_token(token_address):
            logging.info(f"Successfully force sold {token_address}")
            del monitored_tokens[token_address]
        else:
            logging.error(f"Failed to force sell {token_address}")

# UPDATED MONITOR FUNCTION for fast 2x exits
def monitor_token_price(token_address: str) -> None:
    """Monitor token with fast exit strategy."""
    try:
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
        
        # Log current status
        logging.info(f"Token {token_address} - Current: {price_change_pct:.2f}% change, Time: {time_elapsed_minutes:.1f} min")
        
        # Quick 2x exit
        if price_change_pct >= 100:  # 2x achieved
            logging.info(f"2X ACHIEVED! Selling {token_address} at {price_change_pct:.2f}% gain")
            sell_token(token_address, 100)
            del monitored_tokens[token_address]
            return
        
        # Partial profit at 50%
        if not monitored_tokens[token_address]['partial_profit_taken'] and price_change_pct >= 50:
            logging.info(f"Taking partial profit for {token_address} at {price_change_pct:.2f}% gain")
            if sell_token(token_address, 50):
                monitored_tokens[token_address]['partial_profit_taken'] = True
        
        # Quick time-based exit (5 minutes)
        if time_elapsed_minutes >= 5:  # This line should be indented to match the other 'if' statements
            logging.info(f"TIME EXIT: Selling {token_address} after {time_elapsed_minutes:.1f} minutes")
            sell_token(token_address, 100)
            del monitored_tokens[token_address]
            return
            
    except Exception as e:
        logging.error(f"Error monitoring {token_address}: {str(e)}")

def startup_test_buy():
    """Perform a test buy of BONK at startup to verify trading works."""
    if CONFIG['SIMULATION_MODE']:
        logging.info("Skipping startup test buy in simulation mode")
        return
    
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK token
    
    logging.info("======= STARTUP TEST =======")
    logging.info(f"Attempting to buy BONK token as startup test")
    
    # Try to buy a small amount of BONK
    test_amount = CONFIG['BUY_AMOUNT_SOL'] / 5  # Use 1/5 of normal buy amount
    
    result = buy_token(bonk_address, test_amount)
    
    if result:
        logging.info("STARTUP TEST PASSED - Successfully bought BONK!")
        # Add to monitored tokens
        initial_price = get_token_price(bonk_address)
        if initial_price:
            monitored_tokens[bonk_address] = {
                'initial_price': initial_price,
                'highest_price': initial_price,
                'partial_profit_taken': False,
                'buy_time': time.time()
            }
    else:
        logging.error("STARTUP TEST FAILED - Could not buy BONK!")
        logging.error("This suggests there may be issues with the trading functionality.")
    
    logging.info("======= END TEST =======")

# FORCE SELL function for stale positions - FIXED INDENTATION
def force_sell_stale_positions():
    """Force sell positions held too long."""
    current_time = time.time()
    for token_address, token_data in list(monitored_tokens.items()):
        time_held = (current_time - token_data['buy_time']) / 3600  # in hours
        if time_held > 2:  # Force sell after 2 hours
            logging.warning(f"FORCE SELLING: {token_address} held for {time_held:.1f} hours")
            sell_token(token_address)
            if token_address in monitored_tokens:
                del monitored_tokens[token_address]

# UPDATED TRADING LOOP focused on new coin sniping
def trading_loop():
    """Main trading loop focused on new meme coins."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay
    
    logging.info("Starting NEW COIN SNIPING trading loop")
    
    while True:
        iteration_count += 1
        
        try:
            # Print status every 5 minutes
            if time.time() - last_status_time > 300:  # 5 minutes
                logging.info(f"===== STATUS UPDATE =====")
                logging.info(f"Tokens scanned: {tokens_scanned}")
                logging.info(f"Tokens monitored: {len(monitored_tokens)}")
                logging.info(f"Buy attempts: {buy_attempts}, successes: {buy_successes}")
                logging.info(f"Sell attempts: {sell_attempts}, successes: {sell_successes}")
                logging.info(f"Errors encountered: {errors_encountered}")
                logging.info(f"Iteration count: {iteration_count}")
                logging.info(f"Current API delay: {api_call_delay}s")
                
                # Also log wallet balance in production mode
                if not CONFIG['SIMULATION_MODE'] and wallet:
                    balance = wallet.get_balance()
                    logging.info(f"Current wallet balance: {balance} SOL")
                
                last_status_time = time.time()
            
            # FIRST: Force sell stale positions
            force_sell_stale_positions()
            
            # Monitor existing positions
            for token_address in list(monitored_tokens.keys()):
                monitor_token_price(token_address)
                time.sleep(1)  # Faster monitoring for quick exits
            
            # Look for new launches if we have capacity
            if len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
                new_tokens = scan_for_new_launches()
                
                for token_address in new_tokens:
                    if len(monitored_tokens) >= CONFIG['MAX_CONCURRENT_TOKENS']:
                        break
                    
                    # Quick liquidity check and buy
                    if check_token_liquidity(token_address):
                        logging.info(f"SNIPING new token: {token_address}")
                        if buy_token(token_address, CONFIG['BUY_AMOUNT_SOL']):
                            logging.info(f"Successfully sniped new token: {token_address}")
                            time.sleep(2)  # Brief delay between buys
            
            # Faster iteration for quick trades
            time.sleep(1)  # Check every second
            
        except Exception as e:
            errors_encountered += 1
            logging.error(f"Error in main loop: {str(e)}")
            logging.error(traceback.format_exc())
            # Shorter error recovery
            time.sleep(5)

def find_tradable_tokens():
    """Find and update which tokens are actually tradable."""
    logging.info("Checking tradability status of known tokens...")
    
    # Calculate delay based on rate limit
    rate_limit = CONFIG['JUPITER_RATE_LIMIT_PER_MIN']
    delay_between_calls = 60.0 / (rate_limit * 0.8)  # Use 80% of limit for safety
    logging.info(f"Using {delay_between_calls:.2f}s delay between API calls (rate limit: {rate_limit}/min)")
    
    # Update the global rate limit
    global last_api_call_time
    last_api_call_time = time.time()  # Reset timer
    
    # Create a list of additional tokens to check
    additional_tokens = [
        {"symbol": "COPE", "address": "8HGyAAB1yoM1ttS7pXjHMa3dukTFGQggnFFH3hJZgzQh", "tradable": True},
        {"symbol": "MNGO", "address": "MangoCzJ36AjZyKwVj3VnYU4GTonjfVEnJmvvWaxLac", "tradable": True},
        {"symbol": "SLND", "address": "SLNDpmoWTVADgEdndyvWzroNL7zSi1dF9PC3xHGtPwp", "tradable": True},
        {"symbol": "DUST", "address": "DUSTawucrTsGU8hcqRdHDCbuYhCPADMLM2VcCb8VnFnQ", "tradable": True},
        {"symbol": "BERN", "address": "3j7SXnkP1BkxvKwUYLKJsZj88zQDZxA8MkUiHSp57o2g", "tradable": True},
        {"symbol": "LDO", "address": "HZRCwxP2Vq9PCpPXooayhJ2bxTpo5xfpQrwB1svh332p", "tradable": True}
    ]
    
    # First check existing tokens
    tradable_count = 0
    for token in KNOWN_TOKENS:
        if token["address"] != SOL_TOKEN_ADDRESS:
            is_tradable = check_token_tradability(token["address"])
            # Update the token's tradability status
            token["tradable"] = is_tradable
            if is_tradable:
                tradable_count += 1
                logging.info(f"{token['symbol']} ({token['address']}) is TRADABLE")
            else:
                logging.info(f"{token['symbol']} ({token['address']}) is NOT tradable")
    
    # If no tradable tokens found, check the additional tokens
    if tradable_count == 0:
        logging.info("No tradable tokens found in KNOWN_TOKENS, checking additional tokens...")
        for token in additional_tokens:
            is_tradable = check_token_tradability(token["address"])
            token["tradable"] = is_tradable
            if is_tradable:
                # Add this token to KNOWN_TOKENS
                KNOWN_TOKENS.append(token)
                tradable_count += 1
                logging.info(f"Added new tradable token: {token['symbol']} ({token['address']})")
    
    logging.info(f"Found {tradable_count} tradable tokens out of {len(KNOWN_TOKENS)-1} known tokens")
    return tradable_count > 0

def main():
    """Main entry point."""
    logging.info("============ BOT STARTING ============")
    
    if initialize():
        # Test basic transaction capability first
        logging.info("Testing basic SOL transfer capability...")
        sol_transfer_success = test_simple_sol_transfer()
        
        if not sol_transfer_success:
            logging.error("CRITICAL: Basic SOL transfers failing - wallet signing may be compromised!")
            logging.error("Please check wallet initialization and RPC provider settings.")
        else:
            logging.info("Basic SOL transfer test successful - wallet signing works correctly!")
        
        # Continue with the rest of your code...
        # First check which tokens are tradable
        logging.info("Checking which tokens are tradable before starting trading...")
        has_tradable_tokens = find_tradable_tokens()
        
        if not has_tradable_tokens:
            logging.warning("No tradable tokens found in KNOWN_TOKENS list!")
            logging.warning("Bot will continue but may not be able to execute trades")
        
        # Force sell all existing positions first
        logging.info("Force selling all existing positions...")
        force_sell_all_positions()
        
        # Test different RPC configurations
        logging.info("Testing RPC provider requirements...")
        tiny_buy_test()
        
        # Force buy a token as a test
        logging.info("Attempting to force buy a token as startup test")
        force_buy_token()
        
        # Continue with normal trading loop
        trading_loop()
    else:
        logging.error("Failed to initialize bot. Please check configurations.")
# Add this at the end of your file
if __name__ == "__main__":
    main()
