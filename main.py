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
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '30')),  # Very quick exit
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '5')),  # Faster cooldown
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '5000')),  # Check every second
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '10')),  # More positions with smaller amounts
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.15')),  # Keep small to minimize rug risk
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '100')),
    'RETRY_ATTEMPTS': int(os.environ.get('RETRY_ATTEMPTS', '3')),
    'JUPITER_RATE_LIMIT_PER_MIN': int(os.environ.get('JUPITER_RATE_LIMIT_PER_MIN', '60')),
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

# FIXED: Define SolanaWallet class first, THEN MySolanaWallet
class SolanaWallet:
    def __init__(self, private_key=None, rpc_url=None):
        self.rpc_url = rpc_url or CONFIG['SOLANA_RPC_URL']
        self.keypair = None
        
        if private_key:
            self.keypair = self._create_keypair_from_private_key(private_key)
        else:
            private_key_env = CONFIG['WALLET_PRIVATE_KEY']
            if private_key_env:
                self.keypair = self._create_keypair_from_private_key(private_key_env)
            else:
                raise ValueError("No private key provided.")
                
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

# Keep the MySolanaWallet class (optional, can be removed)
class MySolanaWallet(SolanaWallet):
    pass

# FIXED: Move JupiterSwapHandler outside of SolanaWallet class
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
        # Try reverse direction as before...
    except Exception as e:
        logging.error(f"Error getting price for {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
    
    logging.error(f"All price retrieval methods failed for {token_address}")
    return None

# FIXED: The initialize function with correct wallet initialization
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
            
            # FIXED: Use SolanaWallet instead of undefined class
            wallet = SolanaWallet(CONFIG['WALLET_PRIVATE_KEY'], CONFIG['SOLANA_RPC_URL'])
            
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
    
    # Rest of the function is unchanged...
    logging.info("Bot successfully initialized!")
    return True

# Keep all the remaining functions as they are
def buy_token(token_address: str, amount_sol: float) -> bool:
    """Buy a token using Jupiter API with direct transaction submission approach."""
    global buy_attempts, buy_successes, last_api_call_time, api_call_delay
    
    buy_attempts += 1
    logging.info(f"Starting buy process for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        # Simulation mode code
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
        # Calculate amount in lamports
        amount_lamports = int(amount_sol * 1000000000)
        
        # Apply rate limiting
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Step 1: Get quote
        last_api_call_time = time.time()
        logging.info(f"Getting quote for SOL → {token_address}, amount: {amount_lamports} lamports")
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
        logging.info(f"Quote received successfully")
        
        # Apply rate limiting
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Step 2: Create swap transaction with minimal parameters
        last_api_call_time = time.time()
        swap_response = requests.post(
            f"{CONFIG['JUPITER_API_URL']}/v6/swap",
            json={
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "asLegacyTransaction": True  # Use legacy transaction format
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
        
        # Step 3: Submit transaction directly without deserializing
        serialized_tx = swap_data["swapTransaction"]
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {"encoding": "base64", "skipPreflight": True}
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted successfully: {signature}")
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
        logging.error(traceback.format_exc())
        return False

def sell_token(token_address: str, percentage: int = 100) -> bool:
    """Sell token using direct Jupiter API approach without deserializing."""
    global sell_attempts, sell_successes, last_api_call_time, api_call_delay
    
    sell_attempts += 1
    logging.info(f"===== STARTING SELL PROCESS =====")
    logging.info(f"Token to sell: {token_address}")
    logging.info(f"Percentage to sell: {percentage}%")
    
    if CONFIG['SIMULATION_MODE']:
        # Simulation mode code
        logging.info(f"[SIMULATION] Sold {percentage}% of {token_address}")
        sell_successes += 1
        return True
    
    try:
        # The rest of the sell_token function is correct and unchanged
        # It directly submits the transaction without trying to deserialize or re-sign it
        
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
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Step 2: Get Jupiter quote for selling
        last_api_call_time = time.time()
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_params = {
            "inputMint": token_address,  
            "outputMint": SOL_TOKEN_ADDRESS,  # Selling back to SOL
            "amount": str(amount_to_sell),
            "slippageBps": "1000"
        }
        
        logging.info(f"Getting quote: {token_address} → SOL, amount: {amount_to_sell}")
        quote_response = requests.get(quote_url, params=quote_params, timeout=10)
        
        if quote_response.status_code != 200:
            logging.error(f"Quote API failed: {quote_response.status_code} - {quote_response.text[:200]}")
            return False
        
        quote_data = quote_response.json()
        logging.info(f"Quote received successfully")
        
        # Step 3: Prepare swap transaction
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        last_api_call_time = time.time()
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapUnwrapSOL": True,
            "asLegacyTransaction": True  # Use legacy transaction format
        }
        
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
            {"encoding": "base64", "skipPreflight": True}
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted successfully: {signature}")
            sell_successes += 1
            return True
        else:
            if "error" in response:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Transaction error: {error_message}")
            else:
                logging.error(f"Failed to submit transaction - unexpected response format")
            return False
    
    except Exception as e:
        logging.error(f"Error selling {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def force_sell_all_positions():
    """Force sell all current positions."""
    logging.info("Force selling all current positions...")
    
    for token_address in list(monitored_tokens.keys()):
        logging.info(f"Force selling {token_address}")
        if sell_token(token_address):
            logging.info(f"Successfully force sold {token_address}")
            del monitored_tokens[token_address]
        else:
            logging.error(f"Failed to force sell {token_address}")

# The rest of your functions remain unchanged

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
