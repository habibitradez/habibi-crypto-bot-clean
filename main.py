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

# Configuration from environment variables with fallbacks
CONFIG = {
    'SOLANA_RPC_URL': os.environ.get('SOLANA_RPC_URL', ''),
    'JUPITER_API_URL': 'https://quote-api.jup.ag',  # Updated to correct base URL
    'WALLET_ADDRESS': os.environ.get('WALLET_ADDRESS', ''),
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '100')),  # 2x return
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '40')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '15')),
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '30')),
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '5')),  # Increased to 5 minutes
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '5000')),  # Increased to 5 seconds
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '5')),  # Reduced from 15 to 5
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.15')),  # Kept at 0.15 SOL
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '100')),  # Reduced from 500 to 100
    'RETRY_ATTEMPTS': int(os.environ.get('RETRY_ATTEMPTS', '3')),
    'JUPITER_RATE_LIMIT_PER_MIN': int(os.environ.get('JUPITER_RATE_LIMIT_PER_MIN', '20'))  # Reduced from 50 to 20
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
api_call_delay = 1.5  # Start with 1.5 seconds between calls (40 calls/min)

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
        """Initialize a Solana wallet using solders library.
        
        Args:
            private_key: Base58 encoded private key string
            rpc_url: URL for the Solana RPC endpoint
        """
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
            
        headers = {"Content-Type": "application/json"}
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

def force_buy_bonk():
    """Force buy BONK token to test trading functionality."""
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK

def force_buy_usdc():
    """Force buy USDC token to test trading functionality."""
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC on Solana
    
    logging.info("=" * 50)
    logging.info("FORCE BUYING USDC TOKEN")
    logging.info("=" * 50)
    
    # First verify USDC is actually tradable
    is_tradable = check_token_tradability(usdc_address)
    if not is_tradable:
        logging.error("USDC token is not tradable on Jupiter! Trying a different token...")
        
        # Try a different token from our updated list
        for token in KNOWN_TOKENS:
            if token.get("tradable", False) and token["address"] != SOL_TOKEN_ADDRESS:
                logging.info(f"Trying to buy {token['symbol']} instead of USDC...")
                
                if buy_token(token["address"], CONFIG['BUY_AMOUNT_SOL']):
                    initial_price = get_token_price(token["address"])
                    if initial_price:
                        monitored_tokens[token["address"]] = {
                            'initial_price': initial_price,
                            'highest_price': initial_price,
                            'partial_profit_taken': False,
                            'buy_time': time.time()
                        }
                        logging.info(f"Successfully bought and monitoring {token['symbol']} at {initial_price}")
                        return True
                
                # If we've tried one token and failed, continue to the next
                logging.warning(f"Failed to buy {token['symbol']}, trying next token...")
        
        logging.error("Failed to buy any test token - Check logs for errors")
        return False
    
    # If USDC is tradable, proceed with the buy
    if buy_token(usdc_address, CONFIG['BUY_AMOUNT_SOL']):
        initial_price = get_token_price(usdc_address)
        if initial_price:
            monitored_tokens[usdc_address] = {
                'initial_price': initial_price,
                'highest_price': initial_price,
                'partial_profit_taken': False,
                'buy_time': time.time()
            }
            logging.info(f"Successfully bought and monitoring USDC at {initial_price}")
            return True
    
    logging.error("Failed to buy USDC - Check logs for errors")
    return False

def buy_token(token_address: str, amount_sol: float) -> bool:
    """Buy a token using Jupiter API with improved transaction handling."""
    global buy_attempts, buy_successes, last_api_call_time, api_call_delay
    
    buy_attempts += 1
    
    logging.info(f"Starting buy process for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
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
    
    # Real trading logic for production mode using solders
    try:
        global wallet, jupiter_handler
        
        if wallet is None or jupiter_handler is None:
            logging.error("Wallet or Jupiter handler not initialized")
            return False
            
        # Step 1: Get a quote
        amount_lamports = int(amount_sol * 1000000000)  # Convert SOL to lamports
        logging.info(f"Getting quote for buying {token_address} with {amount_lamports} lamports ({amount_sol} SOL)")
        
        # Manual rate limiting for quote request
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before quote request")
            time.sleep(sleep_time)
        
        # Get the quote with rate limiting
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": "1000"  # 10% slippage
        }
        
        last_api_call_time = time.time()
        quote_response = requests.get(quote_url, params=quote_params, timeout=10)
        
        if quote_response.status_code == 200:
            quote_data = quote_response.json()
        else:
            logging.error(f"Failed to get quote for buying {token_address}: {quote_response.status_code} - {quote_response.text}")
            return False
        
        if "outAmount" not in quote_data:
            logging.error(f"Invalid quote response for {token_address}: {json.dumps(quote_data)}")
            return False
            
        logging.info(f"Successfully got quote for buying {token_address}")
        
        # Step 2: Prepare swap transaction with minimal payload
        logging.info(f"Preparing swap transaction for {token_address}")
        
        # Minimal payload to avoid "request too big" error
        swap_payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapUnwrapSOL": True
        }
        
        last_api_call_time = time.time()
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/v6/swap",
            json=swap_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to prepare swap transaction: {swap_response.status_code} - {swap_response.text}")
            return False
            
        swap_data = swap_response.json()
        if "swapTransaction" not in swap_data:
            logging.error(f"Swap response does not contain transaction: {json.dumps(swap_data)}")
            return False
            
        logging.info(f"Successfully prepared swap transaction for {token_address}")
        
        # Step 3: Sign and submit the transaction
        logging.info(f"Signing and submitting transaction for {token_address}")
        
        try:
            # Extract the serialized transaction (already in base64)
            serialized_tx = swap_data["swapTransaction"]
            logging.info(f"Got serialized transaction (length: {len(serialized_tx)})")
            
            # Jupiter transactions are usually pre-signed except for user signature
            # Just submit directly without modifying
            response = wallet._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "preflightCommitment": "processed"
                }
            ])
            
            if "result" in response:
                signature = response["result"]
                logging.info(f"Transaction submitted successfully: {signature}")
                token_buy_timestamps[token_address] = time.time()
                buy_successes += 1
                return True
            else:
                if "error" in response:
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    error_code = response.get("error", {}).get("code", "Unknown code")
                    logging.error(f"Transaction error: {error_message} (Code: {error_code})")
                    
                    # Handle specific error cases
                    if error_code == -32007:  # Request is too big
                        logging.error("Transaction too large - this might be a network issue")
                else:
                    logging.error(f"Failed to submit transaction - unexpected response format")
                return False
                
        except Exception as e:
            logging.error(f"Error submitting transaction: {str(e)}")
            logging.error(traceback.format_exc())
            return False
            
    except Exception as e:
        logging.error(f"Error buying {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def sell_token(token_address: str, percentage: int = 100) -> bool:
    """Sell a percentage of token holdings using Jupiter API."""
    global sell_attempts, sell_successes
    
    sell_attempts += 1
    
    logging.info(f"Starting sell process for {token_address} - Percentage: {percentage}%")
    
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
            
            sell_successes += 1
            return True
        else:
            logging.error(f"[SIMULATION] Failed to sell {token_address}: Could not determine price")
            return False
    
    # Real trading logic for production mode
    try:
        global wallet, jupiter_handler
        
        if wallet is None or jupiter_handler is None:
            logging.error("Wallet or Jupiter handler not initialized")
            return False
        
        # Step 1: Find token account
        logging.info(f"Finding token accounts for {token_address}")
        response = wallet._rpc_call("getTokenAccountsByOwner", [
            str(wallet.public_key),
            {"mint": token_address},
            {"encoding": "jsonParsed"}
        ])
        
        token_accounts = []
        if 'result' in response and 'value' in response['result']:
            token_accounts = response['result']['value']
            logging.info(f"Found {len(token_accounts)} token accounts for {token_address}")
            
            if ULTRA_DIAGNOSTICS and token_accounts:
                try:
                    first_account = token_accounts[0]
                    if 'account' in first_account and 'data' in first_account['account'] and 'parsed' in first_account['account']['data']:
                        parsed = first_account['account']['data']['parsed']
                        if 'info' in parsed and 'tokenAmount' in parsed['info']:
                            amount = parsed['info']['tokenAmount'].get('amount', 'unknown')
                            decimals = parsed['info']['tokenAmount'].get('decimals', 'unknown')
                            logging.info(f"Token account has {amount} tokens (decimals: {decimals})")
                except Exception as e:
                    logging.error(f"Error examining token account: {str(e)}")
        else:
            logging.error(f"No token accounts found for {token_address}")
            if 'error' in response:
                logging.error(f"RPC error: {json.dumps(response['error'])}")
            return False
        
        if not token_accounts:
            logging.error(f"No token account found for {token_address}")
            return False
        
        # For simplicity, use the first token account
        token_account = token_accounts[0]
        token_amount = None
        
        # Extract token amount from account data
        if 'account' in token_account and 'data' in token_account['account'] and 'parsed' in token_account['account']['data']:
            parsed_data = token_account['account']['data']['parsed']
            if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                token_amount_info = parsed_data['info']['tokenAmount']
                if 'amount' in token_amount_info:
                    token_amount = token_amount_info['amount']
                    logging.info(f"Found token amount: {token_amount}")
        
        if token_amount is None:
            logging.error(f"Could not determine token amount for {token_address}")
            return False
        
        # Calculate amount to sell based on percentage
        amount_to_sell = int(int(token_amount) * percentage / 100)
        logging.info(f"Selling {amount_to_sell} tokens ({percentage}% of {token_amount})")
        
        if amount_to_sell <= 0:
            logging.error(f"Invalid amount to sell for {token_address}: {amount_to_sell}")
            return False
        
        # Step 2: Get a quote
        logging.info(f"Getting quote for selling {amount_to_sell} tokens of {token_address}")
        quote_data = jupiter_handler.get_quote(
            input_mint=token_address,
            output_mint=SOL_TOKEN_ADDRESS,
            amount=str(amount_to_sell),
            slippage_bps="1000"  # 10% slippage to ensure transaction goes through
        )
        
        if quote_data is None:
            logging.error(f"Failed to get quote for selling {token_address}")
            return False
            
        logging.info(f"Successfully got quote for selling {token_address}")
        
        # Step 3: Prepare swap transaction
        logging.info(f"Preparing swap transaction for selling {token_address}")
        swap_data = jupiter_handler.prepare_swap_transaction(
            quote_data=quote_data,
            user_public_key=str(wallet.public_key)
        )
        
        if swap_data is None:
            logging.error(f"Failed to prepare swap transaction for selling {token_address}")
            return False
            
        logging.info(f"Successfully prepared swap transaction for selling {token_address}")
        
        # Step 4: Deserialize the transaction
        logging.info(f"Deserializing transaction for selling {token_address}")
        transaction = jupiter_handler.deserialize_transaction(swap_data)
        
        if transaction is None:
            logging.error(f"Failed to deserialize transaction for selling {token_address}")
            return False
            
        logging.info(f"Successfully deserialized transaction for selling {token_address}")
        
        # Step 5: Sign and submit the transaction
        logging.info(f"Signing and submitting transaction for selling {token_address}")
        signature = wallet.sign_and_submit_transaction(transaction)
        
        if signature:
            if percentage == 100:
                logging.info(f"Successfully sold 100% of {token_address} - Signature: {signature}")
            else:
                logging.info(f"Successfully sold {percentage}% of {token_address} - Signature: {signature}")
            
            sell_successes += 1
            return True
        else:
            logging.error(f"Failed to submit transaction for selling {token_address}")
            return False
        
    except Exception as e:
        logging.error(f"Error selling {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def monitor_token_price(token_address: str) -> None:
    """Monitor a token's price and execute the trading strategy."""
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
        time_limit_hit = time_elapsed_minutes >= CONFIG['TIME_LIMIT_MINUTES']
        
        # Log current status
        logging.info(f"Token {token_address} - Current: {price_change_pct:.2f}% change, Time: {time_elapsed_minutes:.1f} min")
        
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
    except Exception as e:
        logging.error(f"Error monitoring token {token_address}: {str(e)}")
        logging.error(traceback.format_exc())

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

def trading_loop():
    """Main trading loop."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay
    
    logging.info("Starting main trading loop")
    
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
            
            # Monitor tokens we're already trading
            for token_address in list(monitored_tokens.keys()):
                monitor_token_price(token_address)
                # Add a sleep between token monitoring to avoid rate limits
                time.sleep(3)
            
            # Only look for new tokens if we have capacity and not too frequently
            if len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
                # Add a random delay between 10-20 seconds before scanning for new tokens
                # This prevents hitting Jupiter API too frequently
                scan_delay = random.uniform(10, 20)
                logging.info(f"Delaying token scan for {scan_delay:.2f} seconds to avoid rate limits")
                time.sleep(scan_delay)
                
                # Scan for new tokens
                potential_tokens = scan_for_new_tokens()
                
                # Shuffle tokens to avoid always trying the same ones
                random.shuffle(potential_tokens)
                
                tokens_checked = 0
                for token_address in potential_tokens:
                    # Limit how many tokens we check per iteration
                    if tokens_checked >= 2:
                        break
                        
                    # Skip tokens we're already monitoring
                    if token_address in monitored_tokens:
                        continue
                    
                    # Check if we've bought this token recently (cooldown period)
                    if token_address in token_buy_timestamps:
                        minutes_since_last_buy = (time.time() - token_buy_timestamps[token_address]) / 60
                        if minutes_since_last_buy < CONFIG['BUY_COOLDOWN_MINUTES']:
                            continue
                    
                    # Skip if we're at max concurrent tokens
                    if len(monitored_tokens) >= CONFIG['MAX_CONCURRENT_TOKENS']:
                        break
                    
                    # Verify token is suitable for trading
                    if verify_token(token_address):
                        # Check liquidity before buying
                        if check_token_liquidity(token_address):
                            logging.info(f"Found promising token with liquidity: {token_address}")
                            
                            # Delay before buying to avoid rate limits
                            buy_delay = random.uniform(5, 10)
                            logging.info(f"Delaying buy for {buy_delay:.2f} seconds to avoid rate limits")
                            time.sleep(buy_delay)
                            
                            # Attempt to buy the token
                            if buy_token(token_address, CONFIG['BUY_AMOUNT_SOL']):
                                logging.info(f"Successfully bought token: {token_address}")
                                # Add a longer delay after successful buy to avoid rate limits
                                time.sleep(15)
                            else:
                                logging.warning(f"Failed to buy token: {token_address}")
                                # Add a delay after failed buy
                                time.sleep(5)
                                
                    tokens_checked += 1
            
            # Sleep before next iteration
            sleep_time = CONFIG['CHECK_INTERVAL_MS'] / 1000  # Convert ms to seconds
            logging.info(f"Sleeping for {sleep_time} seconds before next iteration")
            time.sleep(sleep_time)
            
        except Exception as e:
            errors_encountered += 1
            logging.error(f"Error in main loop: {str(e)}")
            logging.error(traceback.format_exc())
            # Longer sleep on error
            logging.info("Error encountered, sleeping for 30 seconds before continuing")
            time.sleep(30)

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
    
    # Don't try to modify api_call_delay here
    
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
    logging.info(f"Using config: {json.dumps({k: v for k, v in CONFIG.items() if k != 'WALLET_PRIVATE_KEY'}, indent=2)}")

    if initialize():
        # Test basic transaction capability first
        logging.info("Testing basic SOL transfer capability...")
        sol_transfer_success = test_simple_sol_transfer()

        if not sol_transfer_success:
            logging.error("CRITICAL: Basic SOL transfers failing - wallet signing may be compromised!")
            logging.error("Please check wallet initialization and RPC provider settings.")
        else:
            logging.info("Basic SOL transfer test successful - wallet signing works correctly!")

        # Check which tokens are tradable
        logging.info("Checking which tokens are tradable before starting trading...")
        has_tradable_tokens = find_tradable_tokens()

        if not has_tradable_tokens:
            logging.warning("No tradable tokens found in KNOWN_TOKENS list!")
            logging.warning("Bot will continue but may not be able to execute trades")

        # Force sell all existing positions
        logging.info("Force selling all existing positions...")
        force_sell_all_positions()

        # Test different RPC configurations
        logging.info("Testing RPC provider requirements...")
        tiny_buy_test()

        # Force buy a token as a test
        logging.info("Attempting to force buy a token as startup test")
        force_buy_token()

        # Start the trading loop
        trading_loop()
    else:
        logging.error("Failed to initialize bot. Please check configurations.")

# Add this at the end of your file
if __name__ == "__main__":
    main()
