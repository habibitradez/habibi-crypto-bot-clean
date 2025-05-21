import gc
import os
import time
import json
import random
import logging
import datetime
import requests
import base64
import traceback
import subprocess
from typing import Dict, List, Tuple, Optional, Any
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

# Solana imports using solders instead of solana
from solders.keypair import Keypair
from solders.pubkey import Pubkey as PublicKey
from solders.transaction import Transaction, VersionedTransaction
from solders.system_program import transfer, TransferParams
from base58 import b58decode, b58encode

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
    'JUPITER_API_URL': 'https://quote-api.jup.ag',  # Base URL
    'WALLET_ADDRESS': os.environ.get('WALLET_ADDRESS', ''),
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'HELIUS_API_KEY': os.environ.get('HELIUS_API_KEY', ''),
    'PROFIT_TARGET_PCT': int(os.environ.get('PROFIT_TARGET_PERCENT', '20')),  # 2x return
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '50')),  # Adding this for backward compatibility
    'PARTIAL_PROFIT_TARGET_PCT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '10')),
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '50')),  # Adding this for backward compatibility
    'STOP_LOSS_PCT': int(os.environ.get('STOP_LOSS_PERCENT', '5')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '15')),  # Adding this for backward compatibility
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '2')),
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '60')),
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '1000')),
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '3')),
    'MAX_HOLD_TIME_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '2')),
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.25')),  # Reduced to 0.10 SOL
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '100')),
    'RETRY_ATTEMPTS': int(os.environ.get('RETRY_ATTEMPTS', '3')),
    'JUPITER_RATE_LIMIT_PER_MIN': int(os.environ.get('JUPITER_RATE_LIMIT_PER_MIN', '50'))
    'TOKENS_PER_DAY': int(os.environ.get('TOKENS_PER_DAY', '20')),        # Target 20 tokens per day
    'PROFIT_PER_TOKEN': int(os.environ.get('PROFIT_PER_TOKEN', '50')),    # Target $50 profit per token
    'MIN_PROFIT_PCT': int(os.environ.get('MIN_PROFIT_PCT', '20')),        # Take profit at just 20% gain
    'MAX_HOLD_TIME_SECONDS': int(os.environ.get('MAX_HOLD_TIME_SECONDS', '60')), # Only hold for 60 seconds max
    'USE_PUMP_FUN_API': os.environ.get('USE_PUMP_FUN_API', 'true').lower() == 'true', # Use pump.fun API
    'MAX_TOKEN_AGE_MINUTES': int(os.environ.get('MAX_TOKEN_AGE_MINUTES', '5')),  # Only buy very new tokens
    'QUICK_FLIP_MODE': os.environ.get('QUICK_FLIP_MODE', 'true').lower() == 'true', # Enable quick flip mode

    # Memory optimization
    'RPC_CALL_DELAY_MS': int(os.environ.get('RPC_CALL_DELAY_MS', '300')),
    'SKIP_ZERO_BALANCE_TOKENS': os.environ.get('SKIP_ZERO_BALANCE_TOKENS', 'true').lower() == 'true',
    'ZERO_BALANCE_TOKEN_CACHE': {},
    'ZERO_BALANCE_CACHE_EXPIRY': int(os.environ.get('ZERO_BALANCE_CACHE_EXPIRY', '3600'))
}


def check_solders_version():
    """Check the installed version of Solders library."""
    try:
        import solders
        version = getattr(solders, '__version__', 'Unknown')
        logging.info(f"Solders version: {version}")
        return version
    except Exception as e:
        logging.error(f"Error checking Solders version: {str(e)}")
        return None

# Diagnostics flag - set to True for very verbose logging
ULTRA_DIAGNOSTICS = True

# Meme token pattern detection
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

# Global Variables Section
circuit_breaker_active = False
error_count_window = []
last_circuit_reset_time = time.time()
MAX_ERRORS_BEFORE_PAUSE = 10
ERROR_WINDOW_SECONDS = 300  # 5 minutes
CIRCUIT_BREAKER_COOLDOWN = 600  # 10 minutes
# --------------------------------------------------
# Rate limiting variables
last_api_call_time = 0
api_call_delay = 2.0  # Start with 1.5 seconds between calls

# Track tokens we're monitoring
daily_profit = 0
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
    {"symbol": "WIF", "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "tradable": True},
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

# Define verified tokens list
VERIFIED_TOKENS = [
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
    "EKpQGSJtJMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"   # WIF
]
def decode_transaction_blob(blob_str: str) -> bytes:
    """Try to decode a transaction blob using multiple formats."""
    try:
        return base64.b64decode(blob_str)
    except Exception:
        try:
            return b58decode(blob_str)
        except Exception as e:
            logging.error(f"Failed to decode transaction blob: {e}")
            raise

def get_secure_keypair():
    """Get secure keypair for transaction signing."""
    try:
        private_key = CONFIG['WALLET_PRIVATE_KEY'].strip()
        secret_bytes = b58decode(private_key)
        
        # Check key length and handle accordingly
        if len(secret_bytes) == 64:
            logging.info("Using 64-byte secret key format")
            return Keypair.from_bytes(secret_bytes)
        elif len(secret_bytes) == 32:
            logging.info("Using 32-byte seed format")
            return Keypair.from_seed(secret_bytes)
        else:
            raise ValueError(f"Invalid private key length: {len(secret_bytes)} bytes. Expected 32 or 64 bytes.")
    except Exception as e:
        logging.error(f"Error creating secure keypair: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def fallback_rpc():
    """Switch to alternate RPC endpoints if the primary one fails."""
    rpc_endpoints = [
        CONFIG['SOLANA_RPC_URL'], 
        "https://api.mainnet-beta.solana.com",
        "https://solana-mainnet.g.alchemy.com/v2/demo"
    ]
    
    for endpoint in rpc_endpoints[1:]:
        try:
            logging.info(f"Trying fallback RPC endpoint: {endpoint}")
            
            # Test the endpoint with a simple request
            headers = {"Content-Type": "application/json"}
            test_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getHealth",
                "params": []
            }
            
            response = requests.post(endpoint, headers=headers, json=test_request, timeout=10)
            
            if response.status_code == 200 and "result" in response.json():
                logging.info(f"✅ Successfully switched to fallback RPC: {endpoint}")
                # Update config with new RPC URL
                CONFIG['SOLANA_RPC_URL'] = endpoint
                return True
        except Exception as e:
            logging.warning(f"❌ Fallback RPC {endpoint} failed: {e}")
    
    logging.error("All fallback RPCs failed")
    return False

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
        logging.info(f"Wallet initialized with public key: {self.public_key}")
    
    def _create_keypair_from_private_key(self, private_key: str) -> Keypair:
        """Create a Solana keypair from a base58 encoded private key string."""
        try:
            logging.info(f"Creating keypair from private key (length: {len(private_key)})")
            
            # Decode the private key from base58
            secret_bytes = b58decode(private_key)
            
            logging.info(f"Secret bytes length: {len(secret_bytes)}")
            
            # Create keypair based on secret length
            if len(secret_bytes) == 64:
                logging.info("Using 64-byte secret key")
                return Keypair.from_bytes(secret_bytes)
            elif len(secret_bytes) == 32:
                logging.info("Using 32-byte seed")
                return Keypair.from_seed(secret_bytes)
            else:
                raise ValueError(f"Secret key must be 32 or 64 bytes. Got {len(secret_bytes)} bytes.")
        except Exception as e:
            logging.error(f"Error creating keypair: {str(e)}")
            logging.error(traceback.format_exc())
            raise
    
    def get_balance(self) -> float:
        """Get the SOL balance of the wallet in SOL units."""
        try:
            logging.info("Getting wallet balance...")
            response = self._rpc_call("getBalance", [str(self.public_key)])
            
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
        try:
            response = requests.post(self.rpc_url, json=payload, headers=headers, timeout=15)
            
            if response.status_code == 200:
                response_data = response.json()
                
                if 'error' in response_data:
                    logging.error(f"RPC error in response: {response_data['error']}")
                    
                return response_data
            else:
                error_text = f"RPC call failed with status {response.status_code}: {response.text}"
                logging.error(error_text)
                raise Exception(error_text)
        except Exception as e:
            logging.error(f"Error in RPC call {method}: {str(e)}")
            
            # Try fallback RPC if primary fails
            if fallback_rpc():
                # Update RPC URL and retry the call
                self.rpc_url = CONFIG['SOLANA_RPC_URL']
                return self._rpc_call(method, params)
            else:
                raise  # Re-raise if all fallbacks failed
                
    def get_latest_blockhash(self):
        """Get the latest blockhash from the Solana network."""
        try:
            response = self._rpc_call("getLatestBlockhash", [])
            
            if 'result' in response and 'value' in response['result']:
                blockhash = response['result']['value']['blockhash']
                logging.info(f"Got latest blockhash: {blockhash}")
                return blockhash
            else:
                logging.error(f"Failed to get latest blockhash: {response}")
                return None
        except Exception as e:
            logging.error(f"Error getting latest blockhash: {str(e)}")
            logging.error(traceback.format_exc())
            return None

    def sign_and_submit_transaction_bytes(self, tx_bytes):
        """Sign and submit transaction bytes directly."""
        try:
            logging.info("Signing and submitting transaction bytes...")
            
            # Encode the raw bytes in base64
            serialized_tx = base64.b64encode(tx_bytes).decode("utf-8")
            
            # Get latest blockhash for the transaction
            blockhash_response = self._rpc_call("getLatestBlockhash", [])
            if 'result' not in blockhash_response or 'value' not in blockhash_response['result']:
                logging.error("Failed to get blockhash for transaction")
                return None
                
            blockhash = blockhash_response['result']['value']['blockhash']
            
            # Submit the transaction with optimized parameters
            response = self._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,  # Skip client-side simulation
                    "maxRetries": 5,
                    "preflightCommitment": "processed"
                }
            ])
            
            if "result" in response:
                signature = response["result"]
                
                # Check for all 1's signature pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    
                    # Try again with different parameters
                    logging.info("Retrying with modified parameters...")
                    return self._retry_transaction_submission(serialized_tx, blockhash)
                
                logging.info(f"Transaction submitted successfully with signature: {signature}")
                return signature
            else:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                return None
        except Exception as e:
            logging.error(f"Error in sign_and_submit_transaction_bytes: {str(e)}")
            logging.error(traceback.format_exc())
            return None
            
    def _retry_transaction_submission(self, serialized_tx, blockhash):
        """Retry transaction submission with different parameters."""
        try:
            # Try with skipPreflight=False and higher priority fee
            response = self._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "maxRetries": 10,
                    "preflightCommitment": "confirmed"
                }
            ])
            
            if "result" in response:
                signature = response["result"]
                if signature == "1" * len(signature):
                    logging.error("Still received all 1's signature on retry")
                    return None
                    
                logging.info(f"Retry submission successful: {signature}")
                return signature
            else:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Retry submission failed: {error_message}")
                return None
        except Exception as e:
            logging.error(f"Error in retry transaction submission: {str(e)}")
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
    
    def create_token_account(self, token_address: str) -> bool:
        """Create an Associated Token Account explicitly before trading."""
        logging.info(f"Creating Associated Token Account for {token_address}...")
        
        try:
            # Check if account already exists
            check_response = self._rpc_call("getTokenAccountsByOwner", [
                str(self.public_key),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ])
            
            if "result" in check_response and "value" in check_response["result"] and check_response["result"]["value"]:
                logging.info(f"Token account already exists for {token_address}")
                return True
            
            logging.info(f"No token account exists for {token_address}. Creating one...")
            
            # Create account through Jupiter minimal swap
            try:
                # Use a minimal amount to create the account via swap
                minimal_amount = 5000000  # 0.005 SOL
                
                # Get Jupiter quote
                quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
                params = {
                    "inputMint": SOL_TOKEN_ADDRESS,
                    "outputMint": token_address,
                    "amount": str(minimal_amount),
                    "slippageBps": "5000"  # 50% slippage
                }
                
                quote_response = requests.get(quote_url, params=params, timeout=15)
                
                if quote_response.status_code != 200:
                    logging.error(f"Quote failed for account creation: {quote_response.status_code}")
                    return False
                
                quote_data = quote_response.json()
                
                # Prepare swap
                swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
                payload = {
                    "quoteResponse": quote_data,
                    "userPublicKey": str(self.public_key),
                    "wrapUnwrapSOL": True  # Correct parameter name
                }
                
                swap_response = requests.post(
                    swap_url, 
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15
                )
                
                if swap_response.status_code != 200:
                    logging.error(f"Swap preparation failed for account creation: {swap_response.status_code}")
                    return False
                
                swap_data = swap_response.json()
                
                if "swapTransaction" not in swap_data:
                    logging.error("Swap response missing transaction data for account creation")
                    return False
                
                # Submit transaction directly with optimized parameters
                serialized_tx = swap_data["swapTransaction"]
                
                response = self._rpc_call("sendTransaction", [
                    serialized_tx,
                    {
                        "encoding": "base64",
                        "skipPreflight": True,
                        "maxRetries": 5,
                        "preflightCommitment": "processed"
                    }
                ])
                
                if "result" not in response:
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Account creation error: {error_message}")
                    return False
                
                signature = response["result"]
                logging.info(f"Account creation transaction submitted: {signature}")
                
                # Wait for confirmation
                time.sleep(30)
                
                # Verify account was created
                verify_response = self._rpc_call("getTokenAccountsByOwner", [
                    str(self.public_key),
                    {"mint": token_address},
                    {"encoding": "jsonParsed"}
                ])
                
                if "result" in verify_response and "value" in verify_response["result"] and verify_response["result"]["value"]:
                    logging.info(f"Token account successfully created and verified for {token_address}")
                    return True
                else:
                    logging.error(f"Failed to create token account for {token_address}")
                    return False
                    
            except Exception as e:
                logging.error(f"Error in account creation swap: {str(e)}")
                logging.error(traceback.format_exc())
                return False
                
        except Exception as e:
            logging.error(f"Error creating token account: {str(e)}")
            logging.error(traceback.format_exc())
            return False

# Global wallet instance
wallet = None

def get_token_price(token_address: str) -> Optional[float]:
    """Get token price in SOL using Jupiter API with multiple fallback methods."""
    # Implement multiple attempts with different strategies
    for attempt in range(3):
        try:
            # ATTEMPT 1: Try the standard method first
            if attempt == 0:
                price = get_token_price_standard(token_address)
                if price and price > 0:
                    return price
                    
            # ATTEMPT 2: Try with a different amount and approach
            elif attempt == 1:
                price = get_token_price_alternative(token_address)
                if price and price > 0:
                    return price
                    
            # ATTEMPT 3: Try with a more aggressive method
            elif attempt == 2:
                price = get_token_price_aggressive(token_address)
                if price and price > 0:
                    return price
                    
            # Add delay between attempts
            time.sleep(1)
        except Exception as e:
            logging.error(f"Error in price check attempt {attempt+1}: {str(e)}")
            time.sleep(1)
    
    # If all methods fail, log error and try one last fallback
    logging.error(f"All price retrieval methods failed for {token_address}")
    return get_token_price_fallback(token_address)


def get_token_price_standard(token_address: str) -> Optional[float]:
    """Standard method for getting token price - your original implementation."""
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
                
    except Exception as e:
        logging.error(f"Error in standard price retrieval: {str(e)}")
    
    return None


def get_token_price_alternative(token_address: str) -> Optional[float]:
    """Alternative method to get token price from Jupiter API."""
    try:
        # Check basic cases
        if token_address == SOL_TOKEN_ADDRESS:
            return 1.0
            
        # Use a different amount and slippage
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "100000000",  # 0.1 SOL in lamports
            "slippageBps": "1000"   # Higher slippage
        }
        
        logging.info(f"Getting alternative price for {token_address} using Jupiter API...")
        
        # Rate limiting
        global last_api_call_time, api_call_delay
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            time.sleep(sleep_time)
        
        # Make API call with different User-Agent
        last_api_call_time = time.time()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(quote_url, params=params, headers=headers, timeout=15)
        
        # Handle rate limiting
        if response.status_code == 429:
            logging.warning(f"Rate limited by Jupiter API (429). Waiting and retrying...")
            time.sleep(5)  # Longer delay
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=params, headers=headers, timeout=15)
        
        # Process successful response
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data:
                out_amount = int(data["outAmount"])
                token_price = 0.1 / (out_amount / 1000000000)  # Adjusted for 0.1 SOL
                
                logging.info(f"Got alternative price for {token_address}: {token_price} SOL")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                return token_price
        
        # Try Jupiter price endpoint as another alternative
        try:
            price_url = f"{CONFIG['JUPITER_API_URL']}/v6/price"
            price_params = {
                "ids": token_address,
                "vsToken": SOL_TOKEN_ADDRESS
            }
            
            response = requests.get(price_url, params=price_params, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data and token_address in data:
                    price = float(data[token_address].get("price", 0))
                    if price > 0:
                        # Update cache
                        price_cache[token_address] = price
                        price_cache_time[token_address] = time.time()
                        return price
        except Exception as e:
            logging.error(f"Error in price endpoint: {str(e)}")
                
    except Exception as e:
        logging.error(f"Error in alternative price retrieval: {str(e)}")
    
    return None


def get_token_price_aggressive(token_address: str) -> Optional[float]:
    """More aggressive method to get token price."""
    try:
        # Check basic cases
        if token_address == SOL_TOKEN_ADDRESS:
            return 1.0
            
        # Try to get token from Raydium API
        try:
            raydium_url = "https://api.raydium.io/v2/main/pairs"
            response = requests.get(raydium_url, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                for pair in data:
                    if pair.get("base_mint") == token_address or pair.get("quote_mint") == token_address:
                        if pair.get("base_mint") == token_address and pair.get("quote_mint") == SOL_TOKEN_ADDRESS:
                            price = float(pair.get("price", 0))
                            if price > 0:
                                price_cache[token_address] = price
                                price_cache_time[token_address] = time.time()
                                return price
                        elif pair.get("quote_mint") == token_address and pair.get("base_mint") == SOL_TOKEN_ADDRESS:
                            price = 1.0 / float(pair.get("price", 0))
                            if price > 0:
                                price_cache[token_address] = price
                                price_cache_time[token_address] = time.time()
                                return price
        except Exception as e:
            logging.error(f"Error with Raydium API: {str(e)}")
            
        # Try with minimum amount (useful for very low liquidity tokens)
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "10000000",  # 0.01 SOL
            "slippageBps": "2000"  # Very high slippage (20%)
        }
        
        logging.info(f"Getting aggressive price for {token_address} using Jupiter API...")
        
        # Rate limiting
        global last_api_call_time, api_call_delay
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            time.sleep(sleep_time)
        
        # Make API call
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=params, timeout=15)
        
        # Process successful response
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data:
                out_amount = int(data["outAmount"])
                token_price = 0.01 / (out_amount / 1000000000)  # Adjusted for 0.01 SOL
                
                logging.info(f"Got aggressive price for {token_address}: {token_price} SOL")
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                return token_price
                
    except Exception as e:
        logging.error(f"Error in aggressive price retrieval: {str(e)}")
    
    return None


def get_token_price_fallback(token_address: str) -> Optional[float]:
    """Last resort fallback for token price."""
    try:
        # Try to use cached value even if older
        if token_address in price_cache:
            logging.warning(f"Using cached price for {token_address} as fallback: {price_cache[token_address]} SOL")
            return price_cache[token_address]
            
        # Check if we have a predefined price
        for token in KNOWN_TOKENS:
            if token["address"] == token_address and "price_estimate" in token:
                logging.warning(f"Using predefined price estimate for {token_address}: {token['price_estimate']} SOL")
                return token["price_estimate"]
                
        # In simulation mode, generate a random price
        if CONFIG['SIMULATION_MODE']:
            random_price = random.uniform(0.00000001, 0.001)
            price_cache[token_address] = random_price
            price_cache_time[token_address] = time.time()
            logging.warning(f"Using randomly generated price for {token_address} (simulation only): {random_price} SOL")
            return random_price
                
    except Exception as e:
        logging.error(f"Error in fallback price retrieval: {str(e)}")
    
    return None

def get_raydium_price(token_address: str) -> Optional[float]:
    """Get token price from Raydium pools."""
    try:
        # Raydium API has a list of all their pools
        raydium_url = "https://api.raydium.io/v2/main/pairs"
        
        # Add header to prevent rate limiting
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        logging.info(f"Getting Raydium price for {token_address}...")
        
        # Make API call with retries
        for retry in range(3):
            try:
                response = requests.get(raydium_url, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Look for direct token/SOL pairs
                    for pair in data:
                        # Check for token/SOL pairs in both directions
                        if (pair.get("base_mint") == token_address and 
                            pair.get("quote_mint") == SOL_TOKEN_ADDRESS):
                            
                            # Base is our token, quote is SOL
                            price = 1.0 / float(pair.get("price", 0))
                            if price > 0:
                                logging.info(f"Found Raydium base/quote pair for {token_address}, price: {price} SOL")
                                return price
                                
                        elif (pair.get("quote_mint") == token_address and 
                               pair.get("base_mint") == SOL_TOKEN_ADDRESS):
                            
                            # Base is SOL, quote is our token
                            price = float(pair.get("price", 0))
                            if price > 0:
                                logging.info(f"Found Raydium quote/base pair for {token_address}, price: {price} SOL")
                                return price
                    
                    # If no direct SOL pair, try to find a USDC path and convert
                    usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC mint address
                    
                    # First find token/USDC price if exists
                    token_usdc_price = None
                    for pair in data:
                        if (pair.get("base_mint") == token_address and 
                            pair.get("quote_mint") == usdc_mint):
                            
                            # Calculate token price in USDC
                            token_usdc_price = 1.0 / float(pair.get("price", 0))
                            break
                            
                        elif (pair.get("quote_mint") == token_address and 
                               pair.get("base_mint") == usdc_mint):
                            
                            # Calculate token price in USDC
                            token_usdc_price = float(pair.get("price", 0))
                            break
                    
                    # Now find SOL/USDC price if we found a token/USDC price
                    if token_usdc_price:
                        sol_usdc_price = None
                        for pair in data:
                            if (pair.get("base_mint") == SOL_TOKEN_ADDRESS and 
                                pair.get("quote_mint") == usdc_mint):
                                
                                # Calculate SOL price in USDC
                                sol_usdc_price = 1.0 / float(pair.get("price", 0))
                                break
                                
                            elif (pair.get("quote_mint") == SOL_TOKEN_ADDRESS and 
                                   pair.get("base_mint") == usdc_mint):
                                
                                # Calculate SOL price in USDC
                                sol_usdc_price = float(pair.get("price", 0))
                                break
                        
                        # If we found both prices, calculate token price in SOL
                        if sol_usdc_price and sol_usdc_price > 0:
                            token_sol_price = token_usdc_price / sol_usdc_price
                            logging.info(f"Calculated Raydium price via USDC for {token_address}: {token_sol_price} SOL")
                            return token_sol_price
                    
                    logging.warning(f"No Raydium pairs found for {token_address}")
                    break
                    
                elif response.status_code == 429:
                    # Rate limited, wait longer
                    wait_time = (retry + 1) * 2
                    logging.warning(f"Rate limited when getting Raydium price. Waiting {wait_time}s.")
                    time.sleep(wait_time)
                else:
                    logging.warning(f"Failed to get Raydium price: {response.status_code}")
                    break
            except Exception as e:
                logging.error(f"Error getting Raydium price (attempt {retry+1}): {str(e)}")
                time.sleep(1)
                
        return None
    except Exception as e:
        logging.error(f"Error in get_raydium_price: {str(e)}")
        return None


def calculate_price_from_liquidity(token_address: str) -> Optional[float]:
    """Calculate token price from liquidity pools using RPC."""
    try:
        if not wallet:
            return None
            
        # Step 1: Get token supply info for decimals
        token_supply = get_token_supply(token_address)
        if not token_supply:
            logging.warning(f"Couldn't get token supply for {token_address}")
            return None
            
        token_decimals = int(token_supply.get('decimals', 9))
        
        # Step 2: Try to find liquidity pools for this token
        # We can do this by looking for token accounts with large balances
        try:
            # Find largest token accounts
            largest_accounts_response = wallet._rpc_call("getTokenLargestAccounts", [token_address])
            
            if ('result' not in largest_accounts_response or 
                'value' not in largest_accounts_response['result']):
                logging.warning(f"Couldn't get largest accounts for {token_address}")
                return None
                
            largest_accounts = largest_accounts_response['result']['value']
            
            # Look for liquidity pools among these largest accounts
            for account in largest_accounts[:3]:  # Check top 3 accounts
                account_address = account['address']
                
                # Get account info to check if it's a pool
                account_info = wallet._rpc_call("getAccountInfo", [
                    account_address, 
                    {"encoding": "jsonParsed"}
                ])
                
                if ('result' not in account_info or 
                    'value' not in account_info['result']):
                    continue
                
                # For Raydium pools, we'd analyze the account data
                # This is a simplified approach - full implementation would be more complex
                
                # Instead, we'll try to determine if this is likely a pool account 
                # by checking owner program
                owner = account_info['result']['value'].get('owner')
                
                # Raydium pool programs
                raydium_programs = [
                    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM program
                    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"   # Raydium pool program
                ]
                
                if owner in raydium_programs:
                    # This is likely a Raydium pool
                    logging.info(f"Found potential Raydium pool for {token_address}")
                    
                    # For a real implementation, we would decode the account data
                    # and extract the token reserves to calculate price
                    
                    # Since that's quite complex, let's use a simplification:
                    # Check if we can get the price from Raydium API instead
                    price = get_raydium_price(token_address)
                    if price:
                        return price
                    
                    # In an actual implementation, we would:
                    # 1. Decode the pool data structure
                    # 2. Extract token A and B reserves
                    # 3. Calculate price based on the ratio
            
            logging.warning(f"Couldn't find liquidity pools for {token_address} through account analysis")
            return None
            
        except Exception as e:
            logging.error(f"Error analyzing liquidity pools: {str(e)}")
            return None
            
    except Exception as e:
        logging.error(f"Error in calculate_price_from_liquidity: {str(e)}")
        return None


def get_jupiter_price_alternative(token_address: str) -> Optional[float]:
    """Alternative method to get token price from Jupiter API."""
    try:
        # Use a different Jupiter endpoint or parameters
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/price"
        params = {
            "ids": token_address,
            "vsToken": SOL_TOKEN_ADDRESS
        }
        
        logging.info(f"Getting Jupiter price (alternative method) for {token_address}...")
        
        # Add header to simulate browser request
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "application/json"
        }
        
        # Rate limiting
        global last_api_call_time, api_call_delay
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Make the API request with retries
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=params, headers=headers, timeout=10)
        
        # Handle rate limiting
        if response.status_code == 429:
            logging.warning(f"Rate limited on alternative Jupiter endpoint (429). Waiting and retrying...")
            time.sleep(3)
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=params, headers=headers, timeout=10)
            
            if response.status_code == 429:
                api_call_delay += 1.0
                logging.warning(f"Still rate limited. Increased delay to {api_call_delay}s")
                return None
        
        if response.status_code == 200:
            data = response.json()
            
            # Process different response format
            if data and token_address in data:
                price_data = data[token_address]
                if "price" in price_data:
                    price = float(price_data["price"])
                    
                    # Update cache
                    price_cache[token_address] = price
                    price_cache_time[token_address] = time.time()
                    
                    logging.info(f"Got Jupiter alternative price for {token_address}: {price} SOL")
                    return price
            
            logging.warning(f"Invalid response format from alternative Jupiter endpoint")
        else:
            logging.warning(f"Failed to get Jupiter alternative price: {response.status_code}")
            
        # Try a different amount if the first attempt failed
        alternate_quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        alternate_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "50000000",  # 0.05 SOL in lamports
            "slippageBps": "1000"  # 10% slippage
        }
        
        # Rate limiting
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            sleep_time = api_call_delay - time_since_last_call
            if ULTRA_DIAGNOSTICS:
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
            time.sleep(sleep_time)
        
        # Make alternate API call
        last_api_call_time = time.time()
        response = requests.get(alternate_quote_url, params=alternate_params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data:
                out_amount = int(data["outAmount"])
                token_price = 0.05 / (out_amount / 1000000000)  # Adjusted for 0.05 SOL
                
                # Update cache
                price_cache[token_address] = token_price
                price_cache_time[token_address] = time.time()
                
                logging.info(f"Got Jupiter alternate quote price for {token_address}: {token_price} SOL")
                return token_price
                
        return None
    except Exception as e:
        logging.error(f"Error in get_jupiter_price_alternative: {str(e)}")
        return None

def initialize():
    """Initialize the bot and verify connectivity."""
    global wallet
    
    logging.info(f"Starting bot initialization...")
    
    # Add additional logging for critical configuration values
    logging.info(f"SOLANA_RPC_URL: {CONFIG['SOLANA_RPC_URL']}")
    logging.info(f"WALLET_ADDRESS: {CONFIG['WALLET_ADDRESS']}")
    
    # Mask most of the private key for security
    masked_key = CONFIG['WALLET_PRIVATE_KEY'][:5] + "..." + CONFIG['WALLET_PRIVATE_KEY'][-5:] if CONFIG['WALLET_PRIVATE_KEY'] else "None"
    logging.info(f"WALLET_PRIVATE_KEY: {masked_key}")
    
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

def get_recent_transactions_for_token(token_address, limit=5):
    """Get recent transactions for a token."""
    try:
        if not wallet:
            return None
            
        # Use the RPC client to get recent transactions for the token
        result = wallet._rpc_call("getSignaturesForAddress", [
            token_address,
            {
                "limit": limit
            }
        ])
        
        if 'result' in result:
            return result['result']
        return None
    except Exception as e:
        logging.error(f"Error getting recent transactions: {str(e)}")
        return None

def is_recent_token(token_address):
    """Check if a token was created very recently."""
    try:
        # Try to get recent transactions for this token
        recent_txs = get_recent_transactions_for_token(token_address, limit=5)
        
        # If we can't get transactions, be conservative and assume it's not recent
        if not recent_txs:
            return False
            
        # Check the first transaction timestamp (token creation)
        first_tx = recent_txs[0]
        if 'blockTime' not in first_tx:
            return False
            
        # Calculate how many minutes ago the token was created
        creation_time = first_tx['blockTime']
        minutes_since_creation = (time.time() - creation_time) / 60
        
        # Consider a token "recent" if it was created in the last 30 minutes
        return minutes_since_creation <= 30
    except Exception as e:
        logging.error(f"Error checking token age: {str(e)}")
        return False

def check_token_socials(token_address):
    """Check if a token has verified social profiles or website."""
    try:
        # For genuine token verification, we could look for:
        # 1. Website presence
        # 2. Social media accounts (Twitter, Telegram)
        # 3. Verified contracts
        
        # We can skip detailed checks for now - focus on trading performance
        
        # For tokens we know are legitimate
        known_legitimate_tokens = [
            "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
            "EKpQGSJtJMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
            "So11111111111111111111111111111111111111112"   # SOL
        ]
        
        if token_address in known_legitimate_tokens:
            return True
            
        # We could implement actual social checks later using APIs
        # For now, return True to allow trading to continue
        return True
        
    except Exception as e:
        logging.error(f"Error checking token socials: {str(e)}")
        # On error, allow the token to be traded
        return True

def get_token_supply(token_address):
    """Get the total supply of a token."""
    try:
        if not wallet:
            return None
        
        # Make RPC call to get token supply information
        response = wallet._rpc_call("getTokenSupply", [token_address])
        
        if 'result' not in response or 'value' not in response['result']:
            logging.warning(f"Could not get token supply for {token_address}")
            return None
            
        supply_info = response['result']['value']
        return {
            'amount': supply_info.get('amount'),
            'decimals': supply_info.get('decimals'),
            'uiAmount': supply_info.get('uiAmount'),
            'uiAmountString': supply_info.get('uiAmountString')
        }
    except Exception as e:
        logging.error(f"Error getting token supply: {str(e)}")
        return None

def circuit_breaker_check(error=False):
    """Check if we should pause trading due to too many errors."""
    global circuit_breaker_active, error_count_window, last_circuit_reset_time
    
    current_time = time.time()
    
    # Add error to window if one occurred
    if error:
        error_count_window.append(current_time)
    
    # Remove old errors from window
    error_count_window = [t for t in error_count_window if current_time - t < ERROR_WINDOW_SECONDS]
    
    # Check if we need to activate circuit breaker
    if len(error_count_window) >= MAX_ERRORS_BEFORE_PAUSE and not circuit_breaker_active:
        circuit_breaker_active = True
        last_circuit_reset_time = current_time
        logging.warning(f"🛑 CIRCUIT BREAKER ACTIVATED: Too many errors ({len(error_count_window)}) in last {ERROR_WINDOW_SECONDS/60} minutes")
        return True
        
    # Check if we should reset circuit breaker
    if circuit_breaker_active and current_time - last_circuit_reset_time > CIRCUIT_BREAKER_COOLDOWN:
        circuit_breaker_active = False
        error_count_window = []
        logging.info("✅ CIRCUIT BREAKER RESET: Resuming normal operations")
        
    return circuit_breaker_active

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

def get_token_info(token_address):
    """Get information about a token."""
    try:
        if not wallet:
            return None
            
        # Get token supply info which includes decimals, etc.
        response = wallet._rpc_call("getTokenSupply", [token_address])
        
        if 'result' not in response or 'value' not in response['result']:
            logging.warning(f"Could not get token supply for {token_address}")
            return None
            
        token_supply_info = response['result']['value']
        
        # Try to get the token's metadata
        token_metadata = None
        try:
            metadata_response = wallet._rpc_call("getTokenLargestAccounts", [token_address])
            if 'result' in metadata_response and 'value' in metadata_response['result'] and len(metadata_response['result']['value']) > 0:
                largest_account = metadata_response['result']['value'][0]['address']
                token_metadata = wallet._rpc_call("getAccountInfo", [largest_account, {"encoding": "jsonParsed"}])
            
        except Exception as e:
            logging.debug(f"Could not get token metadata: {str(e)}")
        
        # Return a dictionary of token info
        token_info = {
            'address': token_address,
            'supply': token_supply_info.get('amount'),
            'decimals': token_supply_info.get('decimals'),
            'metadata': token_metadata
        }
        
        return token_info
    except Exception as e:
        logging.error(f"Error getting token info: {str(e)}")
        return None

def ensure_token_account_exists(token_address: str, max_attempts: int = 3) -> bool:
    """Ensure a token account exists with better retry handling."""
    logging.info(f"Checking if token account exists for {token_address}...")
    
    # First check if it already exists
    response = wallet._rpc_call("getTokenAccountsByOwner", [
        str(wallet.public_key),
        {"mint": token_address},
        {"encoding": "jsonParsed"}
    ])
    
    if 'result' in response and 'value' in response['result'] and response['result']['value']:
        logging.info(f"Token account already exists for {token_address}")
        return True
    
    logging.info(f"No token account exists for {token_address}. Creating one...")
    
    # Try a specific approach that's more reliable
    for attempt in range(max_attempts):
        try:
            logging.info(f"Account creation attempt #{attempt+1}/{max_attempts}")
            
            # Make a minimal swap to create the token account
            minimal_amount = 5000000  # 0.005 SOL
            
            # Get a quote
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            params = {
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": token_address,
                "amount": str(minimal_amount),
                "slippageBps": "3000"  # 30% slippage for higher chance of success
            }
            
            quote_response = requests.get(quote_url, params=params, timeout=15)
            
            if quote_response.status_code != 200:
                logging.error(f"Quote failed: {quote_response.status_code}")
                # Wait and try again
                time.sleep(5)
                continue
            
            quote_data = quote_response.json()
            
            # Prepare swap transaction
            swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,  # Correct parameter name
                "computeUnitPriceMicroLamports": 0,
                "prioritizationFeeLamports": 100000  # 0.0001 SOL priority fee
            }
            
            swap_response = requests.post(
                swap_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Swap preparation failed: {swap_response.status_code}")
                # Wait and try again
                time.sleep(5)
                continue
            
            swap_data = swap_response.json()
            
            if "swapTransaction" not in swap_data:
                logging.error("Swap response missing transaction data")
                # Wait and try again
                time.sleep(5)
                continue
            
            # Submit transaction
            serialized_tx = swap_data["swapTransaction"]
            
            response = wallet._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,  # Skip preflight checks
                    "maxRetries": 3,
                    "preflightCommitment": "processed"
                }
            ])
            
            if "result" in response:
                signature = response["result"]
                logging.info(f"Account creation transaction submitted: {signature}")
                
                # Wait for confirmation
                logging.info("Waiting 20 seconds for confirmation...")
                time.sleep(20)
                
                # Check if account was created
                check_response = wallet._rpc_call("getTokenAccountsByOwner", [
                    str(wallet.public_key),
                    {"mint": token_address},
                    {"encoding": "jsonParsed"}
                ])
                
                if 'result' in check_response and 'value' in check_response['result'] and check_response['result']['value']:
                    logging.info(f"Token account successfully created for {token_address}")
                    return True
                else:
                    logging.warning("Account creation transaction submitted but account not found")
                    # Wait a bit longer and check again
                    logging.info("Waiting another 10 seconds...")
                    time.sleep(10)
                    
                    final_check = wallet._rpc_call("getTokenAccountsByOwner", [
                        str(wallet.public_key),
                        {"mint": token_address},
                        {"encoding": "jsonParsed"}
                    ])
                    
                    if 'result' in final_check and 'value' in final_check['result'] and final_check['result']['value']:
                        logging.info(f"Token account confirmed for {token_address}")
                        return True
            else:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Account creation error: {error_message}")
            
        except Exception as e:
            logging.error(f"Error in account creation attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
        
        # Wait before next attempt with increasing delay
        wait_time = 5 * (attempt + 1)
        logging.info(f"Waiting {wait_time}s before next attempt...")
        time.sleep(wait_time)
    
    logging.error(f"Failed to create token account for {token_address} after {max_attempts} attempts")
    return False
        
def check_transaction_status(signature: str, max_attempts: int = 5) -> bool:
    """Check the status of a transaction by its signature with exponential backoff."""
    logging.info(f"Checking status of transaction: {signature}")
    
    for attempt in range(max_attempts):
        try:
            # Calculate wait time with exponential backoff
            wait_time = 2 ** attempt  # 1, 2, 4, 8, 16 seconds
            if attempt > 0:
                logging.info(f"Waiting {wait_time} seconds before retry {attempt+1}/{max_attempts}...")
                time.sleep(wait_time)
            
            logging.info(f"Status check attempt {attempt+1}/{max_attempts}...")
            
            response = wallet._rpc_call("getTransaction", [
                signature,
                {"encoding": "json", "commitment": "confirmed"}
            ])
            
            if "result" in response and response["result"]:
                # Transaction found
                if response["result"].get("meta", {}).get("err") is None:
                    logging.info(f"Transaction confirmed successfully!")
                    
                    # Get post-balances to verify token transfer
                    post_balances = response["result"].get("meta", {}).get("postTokenBalances", [])
                    if post_balances:
                        for balance in post_balances:
                            owner = balance.get("owner")
                            mint = balance.get("mint")
                            amount = balance.get("uiTokenAmount", {}).get("amount")
                            
                            if owner == str(wallet.public_key):
                                logging.info(f"Post-transaction token balance: {amount} for mint {mint}")
                                if int(amount) > 0:
                                    return True
                    
                    return True
                else:
                    error = response["result"]["meta"]["err"]
                    logging.error(f"Transaction failed with error: {error}")
                    return False
            else:
                logging.info(f"Transaction not found yet or still processing...")
                
                # Continue to next attempt
                continue
            
        except Exception as e:
            logging.error(f"Error checking transaction status: {str(e)}")
            logging.error(traceback.format_exc())
            
            # If last attempt, return False
            if attempt == max_attempts - 1:
                return False
    
    logging.warning(f"Could not confirm transaction status after {max_attempts} attempts")
    return False

def check_token_liquidity(token_address):
    """Streamlined liquidity check optimized for quick-flip strategy."""
    try:
        # For known tokens, assume they have liquidity
        for token in KNOWN_TOKENS:
            if token["address"] == token_address and token.get("tradable", False):
                return True
        
        # Simple liquidity check using Jupiter API with a minimal SOL amount
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": "10000000",  # Only 0.01 SOL - minimal test
            "slippageBps": "3000"  # 30% slippage - extremely lenient for newest tokens
        }
        
        # Add header and delay to avoid rate limits
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # Rate limiting
        global last_api_call_time, api_call_delay
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            time.sleep(api_call_delay - time_since_last_call)
        
        # Make API call
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=params, headers=headers, timeout=5)
        
        # Process response
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data and int(data["outAmount"]) > 0:
                return True
        
        # Try reverse direction as backup
        reverse_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": "1000000",  # Small token amount
            "slippageBps": "3000"
        }
        
        # Rate limiting
        time_since_last_call = time.time() - last_api_call_time
        if time_since_last_call < api_call_delay:
            time.sleep(api_call_delay - time_since_last_call)
        
        # Make reverse API call
        last_api_call_time = time.time()
        response = requests.get(quote_url, params=reverse_params, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if "outAmount" in data and int(data["outAmount"]) > 0:
                return True
        
        return False
        
    except Exception as e:
        logging.error(f"Error checking liquidity for {token_address}: {str(e)}")
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

# Add this function to track daily performance
def log_daily_performance():
    """Log detailed performance of the bot's operations."""
    global buy_successes, sell_successes, daily_profit
    
    try:
        logging.info("========== DAILY PERFORMANCE REPORT ==========")
        logging.info(f"Total buys successful: {buy_successes}")
        logging.info(f"Total sells successful: {sell_successes}")
        logging.info(f"Current profit: ${daily_profit:.2f}")
        logging.info(f"Currently monitored tokens: {len(monitored_tokens)}")
        
        # Show details of monitored tokens
        if monitored_tokens:
            logging.info("Currently monitored tokens details:")
            current_time = time.time()
            for token_address, token_data in monitored_tokens.items():
                minutes_held = (current_time - token_data['buy_time']) / 60
                initial_price = token_data['initial_price']
                current_price = get_token_price(token_address) or 0
                if current_price > 0:
                    price_change_pct = ((current_price / initial_price) - 1) * 100
                else:
                    price_change_pct = 0
                
                token_symbol = get_token_symbol(token_address) or token_address[:8]
                logging.info(f"  - {token_symbol}: Held for {minutes_held:.1f} min, Change: {price_change_pct:.2f}%")
        
        logging.info("===============================================")
    except Exception as e:
        logging.error(f"Error generating performance report: {str(e)}")

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

def get_pump_fun_tokens_from_quicknode():
    """Get newest tokens from pump.fun using QuickNode's pump.fun API with retries."""
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            if not CONFIG['SOLANA_RPC_URL']:
                logging.error("SOLANA_RPC_URL not configured")
                return []
                
            # Use QuickNode's pump.fun API endpoint
            url = f"{CONFIG['SOLANA_RPC_URL']}/pump-fun/tokens/newest"
            headers = {
                "Content-Type": "application/json"
            }
            
            logging.info(f"Fetching newest tokens from pump.fun (attempt {attempt+1}/{max_retries})")
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract tokens from the response
                tokens = []
                if "data" in data and isinstance(data["data"], list):
                    for token in data["data"]:
                        if "mint" in token:
                            token_data = {
                                "address": token["mint"],
                                "symbol": token.get("symbol", "Unknown"),
                                "name": token.get("name", "Unknown"),
                                "created_at": token.get("createdAt", 0)
                            }
                            tokens.append(token_data)
                            
                logging.info(f"Found {len(tokens)} tokens from QuickNode pump.fun API")
                return tokens
            elif response.status_code == 429:
                logging.warning(f"Rate limited (429) when fetching pump.fun tokens")
                if attempt < max_retries - 1:
                    # Exponential backoff
                    wait_time = retry_delay * (2 ** attempt)
                    logging.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
            elif response.status_code == 503:
                logging.warning(f"Service unavailable (503) when fetching pump.fun tokens")
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logging.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
            else:
                logging.error(f"Error fetching pump.fun tokens: {response.status_code} - {response.text}")
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logging.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                    
            # If we get here, all retries failed
            return []
                
        except Exception as e:
            logging.error(f"Error fetching pump.fun tokens (attempt {attempt+1}/{max_retries}): {str(e)}")
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                logging.info(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
            else:
                return []
    
    # If we get here, all retries failed
    return []
def get_pump_fun_trending_tokens(limit=20):
    """Get trending tokens from pump.fun API."""
    try:
        # Updated URL based on network inspection of pump.fun website
        url = "https://backend.pump.fun/tokens/trending"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, params={"limit": limit})
        
        if response.status_code != 200:
            logging.error(f"Error fetching trending tokens: {response.status_code}")
            return []
            
        data = response.json()
        if not data:
            return []
            
        tokens = []
        for token in data:
            # Extract token address and other details
            if "mint" in token:
                token_data = {
                    "address": token["mint"],
                    "symbol": token.get("symbol", "Unknown"),
                    "name": token.get("name", "Unknown"),
                    "price": token.get("price", 0),
                    "createdAt": token.get("createdAt", 0)
                }
                tokens.append(token_data)
                
        return tokens
    except Exception as e:
        logging.error(f"Error in pump.fun API: {str(e)}")
        return []
        
def get_newest_pump_fun_tokens(limit=20):
    """Get newest tokens from pump.fun API."""
    try:
        # Updated URL based on network inspection of pump.fun website
        url = "https://backend.pump.fun/tokens/newest"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, params={"limit": limit})
        
        if response.status_code != 200:
            logging.error(f"Error fetching newest tokens: {response.status_code}")
            return []
            
        data = response.json()
        if not data:
            return []
            
        tokens = []
        for token in data:
            # Extract token address and other details
            if "mint" in token:
                # Calculate how recent the token is (in minutes)
                created_at = token.get("createdAt", 0) / 1000  # Convert ms to seconds
                minutes_ago = (time.time() - created_at) / 60
                
                token_data = {
                    "address": token["mint"],
                    "symbol": token.get("symbol", "Unknown"),
                    "name": token.get("name", "Unknown"),
                    "price": token.get("price", 0),
                    "minutes_old": minutes_ago,
                    "createdAt": created_at
                }
                tokens.append(token_data)
                
        return tokens
    except Exception as e:
        logging.error(f"Error in pump.fun API: {str(e)}")
        return []

def get_newest_pump_fun_tokens(limit=20):
    """Get newest tokens from pump.fun API with improved error handling."""
    try:
        # Updated URL based on network inspection of pump.fun website
        url = "https://backend.pump.fun/tokens/newest"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # Implement retries with backoff
        max_retries = 3
        base_delay = 2
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, params={"limit": limit}, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if not data:
                        logging.warning("Empty response from pump.fun API")
                        return []
                    
                    tokens = []
                    for token in data:
                        # Extract token address and other details
                        if "mint" in token:
                            # Calculate precise age in minutes
                            created_at = token.get("createdAt", 0) / 1000  # Convert ms to seconds
                            minutes_ago = (time.time() - created_at) / 60
                            
                            token_data = {
                                "address": token["mint"],
                                "symbol": token.get("symbol", "Unknown"),
                                "name": token.get("name", "Unknown"),
                                "price": token.get("price", 0),
                                "minutes_old": minutes_ago,
                                "createdAt": created_at
                            }
                            
                            # Log very new tokens
                            if minutes_ago <= 5:
                                logging.info(f"Found very fresh token: {token_data['symbol']} - {minutes_ago:.1f} minutes old")
                            
                            tokens.append(token_data)
                    
                    # Sort by age (newest first)
                    tokens.sort(key=lambda x: x.get('minutes_old', 999))
                    
                    logging.info(f"Retrieved {len(tokens)} tokens from pump.fun API")
                    return tokens
                
                elif response.status_code == 429:
                    wait_time = base_delay * (2 ** attempt)
                    logging.warning(f"Rate limited by pump.fun API. Waiting {wait_time}s before retry.")
                    time.sleep(wait_time)
                
                else:
                    logging.error(f"Error from pump.fun API: {response.status_code} - {response.text}")
                    return []
                    
            except requests.exceptions.Timeout:
                wait_time = base_delay * (2 ** attempt)
                logging.warning(f"Timeout connecting to pump.fun. Waiting {wait_time}s before retry.")
                time.sleep(wait_time)
                
            except requests.exceptions.RequestException as e:
                logging.error(f"Request error for pump.fun: {str(e)}")
                return []
        
        logging.error(f"Failed to get data from pump.fun after {max_retries} attempts")
        return []
            
    except Exception as e:
        logging.error(f"Error in pump.fun API: {str(e)}")
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

def check_token_age(token_address):
    """Estimate token age in minutes based on recent transactions."""
    try:
        recent_txs = get_recent_transactions_for_token(token_address, limit=1)
        if not recent_txs:
            return None
            
        # Get the first (most recent) transaction
        first_tx = recent_txs[0]
        if 'blockTime' not in first_tx:
            return None
            
        # Calculate minutes since creation
        creation_time = first_tx['blockTime']
        minutes_old = (time.time() - creation_time) / 60
        return minutes_old
    except Exception as e:
        logging.debug(f"Error checking token age: {str(e)}")  # Use debug level to reduce log spam
        return None

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

def verify_token(token_address):
    """Verify if a token is suitable for trading."""
    try:
        # Check if the token is in our verified list (skip verification)
        if token_address in VERIFIED_TOKENS:
            logging.info(f"Token {token_address} is in verified list")
            return True
            
        # Check if token is already being monitored
        if token_address in monitored_tokens:
            logging.info(f"Token {token_address} is already being monitored")
            return False
            
        # Check if we've recently traded this token
        if token_address in token_buy_timestamps:
            minutes_since_last_buy = (time.time() - token_buy_timestamps[token_address]) / 60
            if minutes_since_last_buy < CONFIG['BUY_COOLDOWN_MINUTES']:
                logging.info(f"Token {token_address} was recently traded ({minutes_since_last_buy:.1f} minutes ago)")
                return False

        # Prioritize recent tokens
        if not is_recent_token(token_address):
            # Only continue with older tokens if they're on our verified list
            if token_address not in [
                "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
                "EKpQGSJtJMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm"   # WIF
            ]:
                logging.info(f"Token {token_address} is not recent and not in verified list - skipping")
                return False

        # Get token info from chain
        token_info = get_token_info(token_address)
        if not token_info:
            logging.warning(f"Could not get token info for {token_address}")
            return False
            
        # Check if token is tradable on Jupiter
        is_tradable = check_token_tradability(token_address)
        if not is_tradable:
            logging.info(f"Token {token_address} is not tradable on Jupiter")
            return False
            
        # Check token supply
        token_supply = get_token_supply(token_address)
        if not token_supply:
            logging.warning(f"Could not get token supply for {token_address}")
            return False
            
        # Check if token has a website, socials, etc.
        has_socials = check_token_socials(token_address)
        
        # Additional checks here if needed
        
        # If we get this far, the token is considered valid
        logging.info(f"Token {token_address} is verified as tradable")
        return True
    except Exception as e:
        logging.error(f"Error verifying token {token_address}: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def get_jupiter_quote_and_swap(input_mint, output_mint, amount, is_buy=True):
    """Get Jupiter quote and swap data with better error handling."""
    try:
        # 1. Prepare quote parameters
        slippage = "100" if is_buy else "500"  # Lower slippage for buys, higher for sells
        
        quote_params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": slippage,
            "onlyDirectRoutes": "false",  # Allow any route
            "asLegacyTransaction": "true"  # Use legacy transaction format
        }
        
        logging.info(f"Getting Jupiter quote: {json.dumps(quote_params)}")
        
        # 2. Make quote request
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_response = requests.get(quote_url, params=quote_params, timeout=15)
        
        # 3. Check for quote errors
        if quote_response.status_code != 200:
            logging.error(f"Failed to get Jupiter quote: {quote_response.status_code} - {quote_response.text}")
            return None, None
            
        quote_data = quote_response.json()
        
        # 4. Verify quote data
        if "outAmount" not in quote_data:
            logging.error(f"Invalid quote response: {quote_data}")
            return None, None
        
        logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount')}")
        
        # 5. Prepare swap with all required parameters
        public_key = str(wallet.public_key)
        
        # Get fresh blockhash
        blockhash = get_fresh_blockhash()
        if not blockhash:
            logging.error("Failed to get blockhash for swap")
            return quote_data, None
        
        # Prepare swap params - EXACT FORMAT IS CRITICAL
        swap_params = {
            "quoteResponse": quote_data,
            "userPublicKey": public_key,
            "wrapUnwrapSOL": True,  # Boolean, not string
            "computeUnitPriceMicroLamports": 1000,  # Reasonable priority fee
            "asLegacyTransaction": True,  # Boolean, not string
            "useSharedAccounts": True,  # Use Jupiter's shared accounts
            "dynamicComputeUnitLimit": True,  # Let Jupiter handle compute limits
            "skipUserAccountsCheck": True  # Skip extra checks that might fail
        }
        
        if blockhash:
            swap_params["blockhash"] = blockhash
        
        logging.info(f"Preparing swap with params: {json.dumps(swap_params)}")
        
        # 6. Make swap request
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_response = requests.post(
            swap_url,
            json=swap_params,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        # 7. Check for swap errors
        if swap_response.status_code != 200:
            logging.error(f"Failed to prepare swap: {swap_response.status_code} - {swap_response.text}")
            return quote_data, None
            
        swap_data = swap_response.json()
        
        # 8. Verify swap data
        if "swapTransaction" not in swap_data:
            logging.error(f"Swap response missing transaction data: {swap_data}")
            return quote_data, None
        
        return quote_data, swap_data
        
    except Exception as e:
        logging.error(f"Error in Jupiter quote/swap: {str(e)}")
        logging.error(traceback.format_exc())
        return None, None

def execute_optimized_trade(token_address: str, amount_sol: float = 0.1) -> Tuple[bool, Optional[str]]:
    """Execute trade with optimized transaction handling."""
    global buy_attempts, buy_successes
    
    buy_attempts += 1
    logging.info(f"Starting optimized trade for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True, "simulation-signature"
    
    try:
        # Check wallet balance
        balance = wallet.get_balance()
        if balance < amount_sol + 0.05:  # Include buffer for fees
            logging.error(f"Insufficient balance: {balance} SOL")
            return False, None
            
        logging.info(f"Wallet balance: {balance} SOL")
        
        # 1. Get secure keypair
        keypair = get_secure_keypair()
        
        # 2. Get fresh blockhash (critical for success)
        blockhash = wallet.get_latest_blockhash()
        if not blockhash:
            logging.error("Failed to get latest blockhash")
            return False, None
        
        # 3. Get Jupiter quote with proper parameters
        quote_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": str(int(amount_sol * 1000000000)),
            "slippageBps": "100",  # Use string format
            "onlyDirectRoutes": "true"  # Use string format
        }
        
        # Get quote from Jupiter
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_response = requests.get(quote_url, params=quote_params, timeout=15)
        
        if quote_response.status_code != 200:
            logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
            logging.error(f"Response: {quote_response.text}")
            return False, None
            
        quote_data = quote_response.json()
        logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
        
        # 4. Prepare swap with critical parameter fixes
        swap_params = {
            "quoteResponse": quote_data,
            "userPublicKey": str(keypair.pubkey()),
            "wrapUnwrapSOL": True,  # Correct parameter name
            "computeUnitPriceMicroLamports": 1000,  # Add priority fee
            "prioritizationFeeLamports": 10000,  # Additional priority
            "asLegacyTransaction": True,  # Use legacy format
            "blockhash": blockhash  # Include fresh blockhash
        }
        
        # Get swap transaction from Jupiter
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_response = requests.post(
            swap_url,
            json=swap_params,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
            logging.error(f"Response: {swap_response.text}")
            return False, None
            
        swap_data = swap_response.json()
        
        if "swapTransaction" not in swap_data:
            logging.error(f"Swap response missing transaction data: {list(swap_data.keys())}")
            return False, None
        
        # 5. Get transaction and submit
        tx_base64 = swap_data["swapTransaction"]
        
        # 6. Submit with optimized parameters
        response = wallet._rpc_call("sendTransaction", [
            tx_base64,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        # 7. Handle response
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's pattern
            if signature == "1" * len(signature):
                logging.warning("Got all 1's signature, attempting alternate submission")
                # Try again with different parameters
                alt_response = wallet._rpc_call("sendTransaction", [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,  # Try with skipPreflight=false
                        "maxRetries": 10,
                        "preflightCommitment": "confirmed"
                    }
                ])
                
                if "result" in alt_response:
                    alt_signature = alt_response["result"]
                    if alt_signature == "1" * len(alt_signature):
                        logging.error("Still got all 1's signature on alternate submission")
                        return False, None
                    
                    signature = alt_signature
                else:
                    logging.error(f"Alternate submission failed: {alt_response.get('error')}")
                    return False, None
                
            # Verify success
            logging.info(f"Transaction submitted with signature: {signature}")
            
            # Check transaction status
            success = check_transaction_status(signature, max_attempts=8)
            
            if success:
                logging.info(f"Transaction confirmed successfully!")
                
                # Record transaction success
                token_buy_timestamps[token_address] = time.time()
                buy_successes += 1
                
                # Record initial price for monitoring
                try:
                    initial_price = get_token_price(token_address)
                    if initial_price:
                        monitored_tokens[token_address] = {
                            'initial_price': initial_price,
                            'highest_price': initial_price,
                            'partial_profit_taken': False,
                            'buy_time': time.time()
                        }
                    else:
                        # Fallback: use a placeholder price
                        monitored_tokens[token_address] = {
                            'initial_price': 0.01,  # Placeholder
                            'highest_price': 0.01,
                            'partial_profit_taken': False,
                            'buy_time': time.time()
                        }
                except Exception as e:
                    logging.warning(f"Error getting token price: {str(e)}")
                    # Use placeholder price
                    monitored_tokens[token_address] = {
                        'initial_price': 0.01,  # Placeholder
                        'highest_price': 0.01,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                
                logging.info(f"✅ Trade successful! Token: {token_address}")
                return True, signature
            else:
                logging.error(f"Transaction failed or could not be confirmed")
                return False, None
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission failed: {error_message}")
            return False, None
            
    except Exception as e:
        logging.error(f"Error executing trade: {str(e)}")
        logging.error(traceback.format_exc())
        return False, None

def execute_via_javascript(token_address, amount_sol, is_sell=False):
    """Execute a swap using the JavaScript implementation."""
    global buy_successes, sell_successes
    
    try:
        operation = "sell" if is_sell else "buy"
        logging.info(f"Starting JavaScript {operation} for {token_address} with {amount_sol} SOL")
        
        # Before selling, double-check token balance directly in Python
        if is_sell:
            token_accounts = wallet.get_token_accounts(token_address)
            token_amount = 0
            if token_accounts:
                for account in token_accounts:
                    # Parse token amount from account data
                    parsed_data = account['account']['data']['parsed']
                    if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                        token_amount += int(parsed_data['info']['tokenAmount']['amount'])
                
                if token_amount == 0:
                    logging.error(f"Zero balance for {token_address} - skipping sell operation")
                    return False, None
            else:
                logging.error(f"No token accounts found for {token_address} - skipping sell operation")
                return False, None
        
        # Call the JavaScript script with Node.js
        command = ['node', 'swap.js', token_address, str(amount_sol), 'true' if is_sell else 'false']
        
        logging.info(f"Executing command: {' '.join(command)}")
        result = subprocess.run(
            command,
            env=os.environ,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Log the output
        logging.info(f"STDOUT: {result.stdout}")
        if result.stderr:
            logging.error(f"STDERR: {result.stderr}")
        
        # Check if the script executed successfully
        if result.returncode == 0:
            # Extract the transaction signature from the output
            output_lines = result.stdout.strip().split('\n')
            for line in output_lines:
                if line.startswith('SUCCESS'):
                    signature = line.split(' ')[1].strip()
                    logging.info(f"JavaScript {operation} successful: {signature}")
                    
                    if is_sell:
                        # Handle sell success
                        sell_successes += 1
                        
                        # If selling 100%, remove from monitored tokens
                        if token_address in monitored_tokens:
                            logging.info(f"Removing {token_address} from monitored tokens after successful sell")
                            del monitored_tokens[token_address]
                    else:
                        # Handle buy success
                        token_buy_timestamps[token_address] = time.time()
                        buy_successes += 1
                        
                        # Initialize token monitoring
                        initial_price = get_token_price(token_address)
                        if initial_price:
                            monitored_tokens[token_address] = {
                                'initial_price': initial_price,
                                'highest_price': initial_price,
                                'partial_profit_taken': False,
                                'buy_time': time.time()
                            }
                        else:
                            logging.warning(f"Could not get initial price for {token_address}")
                    
                    return True, signature
            
            logging.error(f"Could not find transaction signature in output")
            return False, None
        else:
            logging.error(f"JavaScript execution failed with code {result.returncode}")
            return False, None
            
    except Exception as e:
        logging.error(f"Error in JavaScript execution: {str(e)}")
        logging.error(traceback.format_exc())
        return False, None

def get_token_symbol(token_address):
    """Get symbol for token address, or return None if not found."""
    try:
        # Common tokens mapping - you can expand this list
        known_tokens = {
            "DezXAZ8z7PnrnRJjz3wXBoRpiXCa6xjnB7YaBipPB263": "BONK",
            "EKpQGSJtJMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "WIF",
            "So11111111111111111111111111111111111111112": "SOL",
            # Add more token mappings as you discover them
        }
        
        # Check if we know this token
        if token_address in known_tokens:
            return known_tokens[token_address]
            
        # If not in our mapping, try to get from RPC
        if wallet:
            try:
                # This is a simplified approach - you may need to adjust based on your wallet implementation
                token_info = wallet._rpc_call("getTokenSupply", [token_address])
                if 'result' in token_info and 'value' in token_info['result'] and 'symbol' in token_info['result']['value']:
                    return token_info['result']['value']['symbol']
            except Exception as e:
                logging.debug(f"Error getting token symbol from RPC: {str(e)}")
                
        # Return a shortened address if we couldn't get a symbol
        return token_address[:8]
        
    except Exception as e:
        logging.error(f"Error in get_token_symbol: {str(e)}")
        return token_address[:8]  # Return shortened address as fallback

def force_sell_all_tokens():
    """Force sell all tokens in the wallet (one-time cleanup)."""
    logging.info("Starting force sell of all tokens in wallet")
    
    try:
        # Get all token accounts
        if not wallet:
            logging.error("Wallet not initialized")
            return
            
        all_tokens = wallet.get_all_token_accounts()
        logging.info(f"Found {len(all_tokens)} token accounts")
        
        for token in all_tokens:
            try:
                mint = token['account']['data']['parsed']['info']['mint']
                balance = int(token['account']['data']['parsed']['info']['tokenAmount']['amount'])
                
                if balance > 0:
                    logging.info(f"Attempting to sell token: {mint} with balance {balance}")
                    success, signature = execute_via_javascript(mint, 0.001, is_sell=True)
                    
                    if success:
                        logging.info(f"Successfully sold token: {mint}")
                    else:
                        logging.warning(f"Failed to sell token: {mint}")
                    
                    # Wait a bit between sells to avoid rate limits
                    time.sleep(5)
            except Exception as e:
                logging.error(f"Error selling token: {str(e)}")
        
        logging.info("Force sell complete")
    except Exception as e:
        logging.error(f"Error in force sell: {str(e)}")

def force_sell_stale_tokens():
    """Sell any tokens that have been held for too long regardless of profit/loss."""
    logging.info("Force selling stale tokens")
    
    stale_tokens = []
    current_time = time.time()
    
    # Find tokens that have been held for too long (> 5 minutes)
    for token_address, token_data in list(monitored_tokens.items()):
        minutes_held = (current_time - token_data['buy_time']) / 60
        if minutes_held > 5:  # 5 minutes max hold time
            stale_tokens.append(token_address)
            logging.warning(f"Token {token_address} held for {minutes_held:.1f} minutes - forcing sell")
    
    # Sell all stale tokens
    for token_address in stale_tokens:
        try:
            success, signature = execute_via_javascript(token_address, 0.001, is_sell=True)
            if success:
                logging.info(f"Successfully force-sold stale token: {token_address}")
                if token_address in monitored_tokens:
                    del monitored_tokens[token_address]
            else:
                logging.error(f"Failed to force-sell stale token: {token_address}")
                # Remove from monitoring anyway to prevent getting stuck
                if token_address in monitored_tokens:
                    del monitored_tokens[token_address]
        except Exception as e:
            logging.error(f"Error force-selling token {token_address}: {str(e)}")
            # Remove from monitoring on error
            if token_address in monitored_tokens:
                del monitored_tokens[token_address]
        
        # Add delay between sells
        time.sleep(5)

def execute_optimized_sell(token_address: str, percentage: int = 100) -> Tuple[bool, Optional[str]]:
    """Execute optimized sell transaction with better handling for small tokens."""
    global sell_attempts, sell_successes
    
    sell_attempts += 1
    logging.info(f"Starting optimized sell for {token_address} - Percentage: {percentage}%")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Sold {percentage}% of {token_address}")
        sell_successes += 1
        
        # If selling 100%, remove from monitored tokens
        if percentage == 100 and token_address in monitored_tokens:
            del monitored_tokens[token_address]
            
        return True, "simulation-signature"
    
    # Maximum number of sell attempts
    max_retries = 5
    
    for attempt in range(max_retries):
        try:
            # Check token balance
            token_accounts = wallet.get_token_accounts(token_address)
            if not token_accounts:
                logging.error(f"No token accounts found for {token_address} - Attempt {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    logging.info(f"Waiting {(attempt+1)*5} seconds before retry...")
                    time.sleep((attempt+1)*5)
                    continue
                else:
                    # On final attempt, mark as sold anyway
                    logging.warning(f"Could not find token accounts after {max_retries} attempts. Marking as sold.")
                    if token_address in monitored_tokens:
                        del monitored_tokens[token_address]
                    return True, None  # Return true to stop retrying
            
            token_amount = 0
            for account in token_accounts:
                # Parse token amount from account data
                parsed_data = account['account']['data']['parsed']
                if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                    token_amount += int(parsed_data['info']['tokenAmount']['amount'])
            
            if token_amount == 0:
                logging.error(f"Zero balance for {token_address} - Attempt {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    logging.info(f"Waiting {(attempt+1)*5} seconds before retry...")
                    time.sleep((attempt+1)*5)
                    continue
                else:
                    # On final attempt, mark as sold anyway
                    logging.warning(f"Zero token balance after {max_retries} attempts. Marking as sold.")
                    if token_address in monitored_tokens:
                        del monitored_tokens[token_address]
                    return True, None  # Return true to stop retrying
                
            logging.info(f"Found token balance: {token_amount} - Attempt {attempt+1}/{max_retries}")
            
            # For very small balances, try a different approach
            if token_amount < 1000:
                logging.info(f"Small token balance detected ({token_amount}). Using special small token handling.")
                success = handle_small_token_sell(token_address, token_amount)
                if success:
                    sell_successes += 1
                    if token_address in monitored_tokens:
                        del monitored_tokens[token_address]
                    return True, "small-token-cleanup"
            
            # Calculate amount to sell based on percentage
            amount_to_sell = int(token_amount * percentage / 100)
            logging.info(f"Selling {amount_to_sell} tokens ({percentage}% of {token_amount})")
            
            # Use JavaScript for selling with exponential retry delay
            success, signature = execute_via_javascript(token_address, 0.001, is_sell=True)
            
            if success:
                sell_successes += 1
                
                # If we're selling 100%, remove from monitored tokens
                if percentage == 100 and token_address in monitored_tokens:
                    logging.info(f"Removing {token_address} from monitored tokens after complete sell")
                    del monitored_tokens[token_address]
                
                logging.info(f"✅ Sell successful! Token: {token_address}, Percentage: {percentage}%")
                return True, signature
            else:
                logging.error(f"Sell transaction failed on attempt {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)
                    logging.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    # On final attempt, mark as sold anyway
                    logging.warning(f"Failed to sell after {max_retries} attempts. Marking as sold to avoid being stuck.")
                    if token_address in monitored_tokens:
                        del monitored_tokens[token_address]
                    return True, None  # Return true to stop retrying
                
        except Exception as e:
            logging.error(f"Error executing sell (attempt {attempt+1}/{max_retries}): {str(e)}")
            logging.error(traceback.format_exc())
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logging.info(f"Waiting {wait_time} seconds before retry...")
                time.sleep(wait_time)
                continue
            else:
                # On final attempt, mark as sold anyway
                logging.warning(f"Error selling after {max_retries} attempts. Marking as sold to avoid being stuck.")
                if token_address in monitored_tokens:
                    del monitored_tokens[token_address]
                return True, None  # Return true to stop retrying
    
    # If we reach here, all retries failed - still remove from monitoring
    if token_address in monitored_tokens:
        del monitored_tokens[token_address]
    return True, None  # Return true to prevent further retries

def handle_small_token_sell(token_address, token_amount):
    """Special handling for very small token balances that might be hard to sell."""
    try:
        logging.info(f"Attempting to clean up small token balance: {token_amount} of {token_address}")
        
        # For extremely small balances, we might just want to remove from monitoring
        if token_amount < 100:
            logging.info(f"Token amount ({token_amount}) too small to sell effectively. Marking as cleaned up.")
            return True
            
        # Try direct selling with very high slippage for small tokens
        try:
            # Modify swap.js parameters for small tokens
            os.environ['SMALL_TOKEN_SELL'] = 'true'  # Set an env variable our JavaScript can check
            
            # Try to sell with JavaScript implementation
            success, signature = execute_via_javascript(token_address, 0.001, is_sell=True)
            
            # Reset env variable
            os.environ.pop('SMALL_TOKEN_SELL', None)
            
            if success:
                logging.info(f"Successfully sold small token balance: {token_address}")
                return True
        except Exception as e:
            logging.error(f"Error selling small token: {str(e)}")
        
        # If we get here, we couldn't sell - mark as handled anyway
        logging.info(f"Could not sell small token. Marking as handled to avoid being stuck.")
        return True
            
    except Exception as e:
        logging.error(f"Error handling small token sell: {str(e)}")
        # Return true anyway to prevent retrying
        return True

def is_recent_token(token_address):
    """Check if a token was created very recently."""
    try:
        # Try to get recent transactions for this token
        recent_txs = get_recent_transactions_for_token(token_address, limit=5)
        
        # If we can't get transactions, be conservative and assume it's not recent
        if not recent_txs:
            return False
            
        # Check the first transaction timestamp (token creation)
        first_tx = recent_txs[0]
        if 'blockTime' not in first_tx:
            return False
            
        # Calculate how many minutes ago the token was created
        creation_time = first_tx['blockTime']
        minutes_since_creation = (time.time() - creation_time) / 60
        
        # Consider a token "recent" if it was created in the last 30 minutes
        return minutes_since_creation <= 30
    except Exception as e:
        logging.error(f"Error checking token age: {str(e)}")
        return False

def find_newest_tokens():
    """Find the absolute newest tokens for quick flips."""
    try:
        # Try to get newest tokens from pump.fun API
        newest_tokens = get_newest_pump_fun_tokens(limit=20)
        
        if not newest_tokens:
            logging.warning("Failed to get tokens from pump.fun API")
            return []
            
        # Filter for VERY new tokens (under 5 minutes old)
        very_new_tokens = []
        for token in newest_tokens:
            if token.get('minutes_old', 999) <= CONFIG.get('MAX_TOKEN_AGE_MINUTES', 5):
                # Only add if it has a mint address
                if 'address' in token:
                    very_new_tokens.append({
                        'address': token['address'],
                        'age_minutes': token.get('minutes_old', 999),
                        'name': token.get('name', 'Unknown'),
                        'symbol': token.get('symbol', 'Unknown')
                    })
        
        if very_new_tokens:
            # Sort by age, newest first
            very_new_tokens.sort(key=lambda x: x['age_minutes'])
            logging.info(f"Found {len(very_new_tokens)} brand new tokens under {CONFIG.get('MAX_TOKEN_AGE_MINUTES', 5)} minutes old")
            
            # Extract just the addresses
            return [t['address'] for t in very_new_tokens]
        else:
            logging.info(f"No tokens found under {CONFIG.get('MAX_TOKEN_AGE_MINUTES', 5)} minutes old")
            return []
            
    except Exception as e:
        logging.error(f"Error finding newest tokens: {str(e)}")
        return []

def monitor_token_peak_price(token_address):
    """Track token peak price and sell if it drops significantly after gains."""
    global daily_profit
    
    if token_address not in monitored_tokens:
        return
        
    token_data = monitored_tokens[token_address]
    current_price = get_token_price(token_address)
    
    if not current_price:
        return
        
    # Update initial price if this is first check
    if 'initial_price' not in token_data:
        token_data['initial_price'] = current_price
        token_data['peak_price'] = current_price
        return
    
    initial_price = token_data['initial_price']
    
    # Update peak price if current is higher
    if current_price > token_data.get('peak_price', 0):
        token_data['peak_price'] = current_price
        token_data['highest_price'] = current_price  # For compatibility
    
    peak_price = token_data['peak_price']
    
    # Calculate price changes
    pct_gain_from_initial = ((current_price / initial_price) - 1) * 100
    pct_drop_from_peak = ((peak_price - current_price) / peak_price) * 100
    
    # Sell if gained at least 30% but then dropped 10% from peak
    if pct_gain_from_initial >= 30 and pct_drop_from_peak >= 10:
        logging.info(f"Trend-based exit: Token gained {pct_gain_from_initial:.2f}% but dropped {pct_drop_from_peak:.2f}% from peak")
        
        # Execute sell
        success, signature = execute_optimized_sell(token_address)
        
        if success:
            profit_amount = (current_price - initial_price) * CONFIG['BUY_AMOUNT_SOL']
            logging.info(f"Trend-based profit taken: ${profit_amount:.2f}")
            daily_profit += profit_amount
            
            # Token will be removed from monitored_tokens in execute_optimized_sell
    
    # Update token data with latest info
    monitored_tokens[token_address] = token_data

def monitor_token_price(token_address):
    """Ultra-aggressive token monitoring for quick flips."""
    global daily_profit
    
    try:
        if token_address not in monitored_tokens:
            return
            
        token_data = monitored_tokens[token_address]
        current_time = time.time()
        
        # Get current price 
        current_price = get_token_price(token_address)
        if not current_price:
            # Force sell after just 1 failed price check in quick flip mode
            if CONFIG.get('QUICK_FLIP_MODE', False):
                logging.warning(f"Quick flip mode: Can't get price - forcing immediate sell for {token_address}")
                execute_optimized_sell(token_address)
            return
            
        # Update highest price if current is higher
        if current_price > token_data.get('highest_price', 0):
            token_data['highest_price'] = current_price
            
        # Calculate price change percentage
        initial_price = token_data['initial_price']
        price_change_pct = ((current_price / initial_price) - 1) * 100
        
        # Calculate time elapsed since buy (in seconds for more precision)
        seconds_since_buy = current_time - token_data['buy_time']
        
        # Log current status
        token_symbol = get_token_symbol(token_address) or token_address[:8]
        logging.info(f"Token {token_symbol} - Current: {price_change_pct:.2f}% change, Time: {seconds_since_buy:.1f} sec")
        
        # EXTREME QUICK FLIP STRATEGY:
        
        # 1. Take any profit after 20 seconds
        if seconds_since_buy >= 20 and price_change_pct > 0:
            logging.info(f"⏱️ Taking {price_change_pct:.2f}% profit after 20 seconds for {token_symbol}")
            execute_optimized_sell(token_address)
            return
            
        # 2. Take tiny profits (just 20%)
        if price_change_pct >= CONFIG.get('MIN_PROFIT_PCT', 20):
            logging.info(f"🔥 Taking {price_change_pct:.2f}% profit for {token_symbol} - quick flip!")
            execute_optimized_sell(token_address)
            return
        
        # 3. Ultra-quick drop detection - just 3% from peak
        peak_price = token_data.get('highest_price', initial_price)
        drop_from_peak_pct = ((peak_price - current_price) / peak_price) * 100
        
        if price_change_pct > 5 and drop_from_peak_pct > 3:
            logging.info(f"📉 Quick flip: Selling {token_symbol} due to 3% drop from peak after initial 5% gain")
            execute_optimized_sell(token_address)
            return
                
        # 4. Very quick stop loss at 5%
        if price_change_pct <= -CONFIG.get('STOP_LOSS_PCT', 5):
            logging.info(f"🛑 Quick stop loss triggered for {token_symbol} with {price_change_pct:.2f}% loss")
            execute_optimized_sell(token_address)
            return
                
        # 5. Ultra-short hold time - sell after just 60 seconds regardless
        if seconds_since_buy >= CONFIG.get('MAX_HOLD_TIME_SECONDS', 60):
            logging.info(f"⏰ Maximum hold time reached for {token_symbol}: {seconds_since_buy:.1f} seconds")
            execute_optimized_sell(token_address)
            return
        
        # Update token data with latest info
        monitored_tokens[token_address] = token_data
        
    except Exception as e:
        logging.error(f"Error monitoring token {token_address}: {str(e)}")
        # Force sell on any error in quick flip mode
        if CONFIG.get('QUICK_FLIP_MODE', False):
            logging.warning(f"Quick flip mode: Error during monitoring - forcing sell for {token_address}")
            execute_optimized_sell(token_address)

def cleanup_memory():
    """Force garbage collection to free up memory."""
    logging.info("Cleaning up memory...")
    
    # Force garbage collection
    gc.collect()
    
    # Clear unnecessary memory
    global price_cache, price_cache_time
    
    # Only keep essential token prices
    if len(price_cache) > 25:  # Reduced threshold
        logging.info(f"Clearing price cache (size: {len(price_cache)})")
        
        # Keep only recent and currently monitored tokens
        current_time = time.time()
        keep_tokens = set(monitored_tokens.keys())
        keep_tokens.update([t["address"] for t in KNOWN_TOKENS])
        
        # Find old cache entries to remove
        old_keys = []
        for key, timestamp in price_cache_time.items():
            if key not in keep_tokens and current_time - timestamp > 600:  # 10 minutes
                old_keys.append(key)
        
        # Remove old keys
        for key in old_keys:
            if key in price_cache:
                del price_cache[key]
            if key in price_cache_time:
                del price_cache_time[key]
        
        logging.info(f"Price cache reduced to {len(price_cache)} items")
    
    # Clear other large dictionaries
    global token_buy_timestamps
    
    # Keep only recent token timestamps (last 4 hours)
    if len(token_buy_timestamps) > 50:
        current_time = time.time()
        old_timestamps = [
            addr for addr, timestamp in token_buy_timestamps.items()
            if current_time - timestamp > 14400  # 4 hours
        ]
        
        for addr in old_timestamps:
            if addr in token_buy_timestamps:
                del token_buy_timestamps[addr]
    
    # Log memory status (Linux only)
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        logging.info(f"Memory usage: {usage.ru_maxrss / 1024} MB")
    except (ImportError, AttributeError):
        pass

def trading_loop():
    """Trading loop optimized for Quick-Flip Strategy."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay, daily_profit
    global buy_attempts, buy_successes, sell_attempts, sell_successes, tokens_scanned
    global circuit_breaker_active
    
    logging.info("==================== QUICK-FLIP TRADING LOOP STARTING ====================")
    logging.info("Bot ready for rapid meme token trading with quick-flip strategy")
    logging.info("Target: $1,000 daily profit with small, frequent gains")
    logging.info("Strategy: Buy newest tokens, sell quickly at 20% profit")
    logging.info("Circuit breaker status: " + ("ACTIVE" if circuit_breaker_active else "INACTIVE"))
    logging.info("=======================================================================")
    
    # Initialize tracking variables
    daily_profit = 0
    daily_profit_start_time = time.time()
    last_performance_report_time = time.time()
    last_memory_cleanup_time = time.time()
    memory_cleanup_interval = 300  # Clean up every 5 minutes
    last_token_search_time = 0
    token_search_interval = 60  # Search for new tokens every 60 seconds
    
    # Set a maximum runtime to automatically restart (prevent memory leaks)
    max_runtime = 6 * 3600  # 6 hours
    start_time = time.time()
    
    while True:
        # Check if we've been running too long (prevent memory bloat)
        if time.time() - start_time > max_runtime:
            logging.info(f"Maximum runtime of {max_runtime/3600} hours reached, exiting for clean restart")
            # Exit with success code so the service restarts clean
            return
            
        iteration_count += 1
        
        try:
            # Perform memory cleanup periodically
            if time.time() - last_memory_cleanup_time > memory_cleanup_interval:
                cleanup_memory()
                last_memory_cleanup_time = time.time()
            
            # Check circuit breaker before starting operations
            if circuit_breaker_check():
                logging.warning("Circuit breaker active - pausing operations")
                time.sleep(60)  # Check every minute if we can resume
                continue
            
            # MONITORING EXISTING TOKENS - Check VERY frequently (every iteration)
            for token_address in list(monitored_tokens.keys()):
                try:
                    # Use ultra-aggressive monitoring function for quick flips
                    monitor_token_price(token_address)
                except Exception as e:
                    logging.error(f"Error monitoring token {token_address}: {str(e)}")
                    circuit_breaker_check(error=True)
                    
                    # Force sell on any error in quick flip mode
                    if CONFIG.get('QUICK_FLIP_MODE', True):
                        try:
                            execute_optimized_sell(token_address)
                        except Exception as sell_error:
                            logging.error(f"Error force selling after monitoring error: {str(sell_error)}")
            
            # LOOKING FOR NEW TOKENS - Check periodically
            current_time = time.time()
            if (current_time - last_token_search_time > token_search_interval and
                    len(monitored_tokens) < CONFIG.get('MAX_CONCURRENT_TOKENS', 3)):
                
                logging.info("Searching for brand new tokens to trade...")
                last_token_search_time = current_time
                
                try:
                    # Use specialized function to find the absolute newest tokens
                    newest_tokens = find_newest_tokens()
                    
                    if newest_tokens:
                        logging.info(f"Found {len(newest_tokens)} very new tokens")
                        
                        # Process up to 2 new tokens per search
                        tokens_to_try = newest_tokens[:2]
                        
                        for token_address in tokens_to_try:
                            # Skip if we're at max concurrent tokens
                            if len(monitored_tokens) >= CONFIG.get('MAX_CONCURRENT_TOKENS', 3):
                                break
                                
                            # Skip tokens we're already monitoring
                            if token_address in monitored_tokens:
                                continue
                            
                            # Skip if we've bought this token recently
                            if token_address in token_buy_timestamps:
                                minutes_since_last_buy = (current_time - token_buy_timestamps[token_address]) / 60
                                if minutes_since_last_buy < 30:  # Only 30 min cooldown
                                    continue
                            
                            try:
                                # Quick liquidity check before buying
                                has_liquidity = check_token_liquidity(token_address)
                                
                                if has_liquidity:
                                    logging.info(f"Found very new token with liquidity: {token_address}")
                                    
                                    # Use JavaScript for buying with higher amount
                                    success, signature = execute_via_javascript(token_address, CONFIG.get('BUY_AMOUNT_SOL', 0.25))
                                    
                                    buy_attempts += 1
                                    if success:
                                        logging.info(f"Successfully bought new token: {token_address}")
                                        buy_successes += 1
                                        
                                        # Log potential profit target
                                        target_pct = CONFIG.get('MIN_PROFIT_PCT', 20)
                                        logging.info(f"🎯 Target: {target_pct}% profit or max {CONFIG.get('MAX_HOLD_TIME_SECONDS', 60)} seconds")
                                        
                                        # Break after one successful buy per search
                                        break
                                    else:
                                        logging.warning(f"Failed to buy token: {token_address}")
                            except Exception as e:
                                logging.error(f"Error buying token {token_address}: {str(e)}")
                                circuit_breaker_check(error=True)
                except Exception as e:
                    logging.error(f"Error searching for new tokens: {str(e)}")
                    circuit_breaker_check(error=True)
            
            # Check if it's time for a performance report (every hour)
            if current_time - last_performance_report_time > 3600:  # 1 hour
                log_daily_performance()
                last_performance_report_time = current_time
                
                # Calculate daily projection based on current profit
                hours_running = (current_time - daily_profit_start_time) / 3600
                if hours_running > 0:
                    projected_daily = (daily_profit / hours_running) * 24
                    logging.info(f"Current daily profit projection: ${projected_daily:.2f}")
                    
                    # Calculate needed profit per hour to reach $1k
                    if projected_daily < 1000:
                        hours_left = 24 - hours_running
                        profit_needed = 1000 - daily_profit
                        hourly_needed = profit_needed / hours_left if hours_left > 0 else 0
                        logging.info(f"Need ${hourly_needed:.2f}/hour for remaining {hours_left:.1f} hours to reach $1k daily target")
            
            # Check if it's a new day for profit tracking
            if current_time - daily_profit_start_time > 86400:  # 24 hours
                logging.info(f"Daily profit reset - Previous total: ${daily_profit:.2f}")
                daily_profit = 0
                daily_profit_start_time = current_time
            
            # Print status update (every 5 minutes)
            if current_time - last_status_time > 300:  # 5 minutes
                logging.info(f"===== QUICK-FLIP STATUS UPDATE =====")
                logging.info(f"Tokens monitored: {len(monitored_tokens)}")
                logging.info(f"Buy/Sell: {buy_successes}/{sell_successes}")
                logging.info(f"Daily profit: ${daily_profit:.2f}")
                logging.info(f"Circuit breaker: {'ACTIVE' if circuit_breaker_active else 'INACTIVE'}")
                
                # Report on token ages in monitoring
                if monitored_tokens:
                    logging.info(f"Currently monitored tokens:")
                    for addr, data in monitored_tokens.items():
                        symbol = get_token_symbol(addr) or addr[:8]
                        seconds_held = current_time - data['buy_time']
                        pct_change = ((get_token_price(addr) or 0) / data['initial_price'] - 1) * 100
                        logging.info(f"  - {symbol}: Held for {seconds_held:.1f}s, Change: {pct_change:.2f}%")
                
                last_status_time = current_time
            
            # Sleep very briefly between iterations to prevent CPU thrashing
            # We want to check very frequently with quick-flip strategy
            time.sleep(0.5)
            
        except Exception as e:
            errors_encountered += 1
            logging.error(f"Error in main loop: {str(e)}")
            
            # Register error with circuit breaker
            circuit_breaker_check(error=True)
            
            # Longer sleep on error
            time.sleep(5)

def simplified_buy_token(token_address: str, amount_sol: float = 0.01) -> bool:
    """Simplified token purchase function with minimal steps."""
    try:
        logging.info(f"Starting simplified buy for {token_address} - Amount: {amount_sol} SOL")
        
        # Convert SOL to lamports
        amount_lamports = int(amount_sol * 1_000_000_000)
        
        # Ensure token account exists
        if not CONFIG['SIMULATION_MODE']:
            ensure_token_account_exists(token_address)
        
        # Get fresh blockhash
        blockhash = None
        if not CONFIG['SIMULATION_MODE']:
            blockhash = wallet.get_latest_blockhash()
        
        # 1. Get Jupiter quote
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": "100"  # 1% slippage
        }
        
        quote_response = requests.get(quote_url, params=params, timeout=15)
        
        if quote_response.status_code != 200:
            logging.error(f"Failed to get quote: {quote_response.status_code}")
            return False
            
        quote_data = quote_response.json()
        logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
        
        # 2. Prepare swap transaction
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_params = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapUnwrapSOL": True,  # Correct parameter name
            "prioritizationFeeLamports": 10000  # 0.00001 SOL fee
        }
        
        # Add blockhash if available
        if blockhash:
            swap_params["blockhash"] = blockhash
        
        swap_response = requests.post(
            swap_url,
            json=swap_params,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to prepare swap: {swap_response.status_code}")
            return False
            
        swap_data = swap_response.json()
        
        if "swapTransaction" not in swap_data:
            logging.error("Swap response missing transaction data")
            return False
        
        # 3. Submit transaction
        tx_base64 = swap_data["swapTransaction"]
        
        if CONFIG['SIMULATION_MODE']:
            logging.info("[SIMULATION] Transaction would be submitted here")
            return True
        
        # Submit transaction with optimized parameters
        response = wallet._rpc_call("sendTransaction", [
            tx_base64,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's pattern
            if signature == "1" * len(signature):
                logging.error("Transaction produced 'all 1's' signature - not executed")
                
                # Try again with different parameters
                logging.info("Retrying with modified parameters...")
                alt_response = wallet._rpc_call("sendTransaction", [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "maxRetries": 10,
                        "preflightCommitment": "confirmed"
                    }
                ])
                
                if "result" in alt_response:
                    alt_signature = alt_response["result"]
                    if alt_signature == "1" * len(alt_signature):
                        logging.error("Still received all 1's signature - transaction failed")
                        return False
                        
                    signature = alt_signature
                    logging.info(f"Retry successful with signature: {signature}")
                else:
                    logging.error(f"Retry failed: {alt_response.get('error')}")
                    return False
            
            logging.info(f"Transaction submitted with signature: {signature}")
            
            # Check transaction status
            success = check_transaction_status(signature)
            
            if success:
                logging.info(f"Transaction confirmed successfully!")
                
                # Update monitoring data
                token_buy_timestamps[token_address] = time.time()
                initial_price = get_token_price(token_address)
                
                if initial_price:
                    monitored_tokens[token_address] = {
                        'initial_price': initial_price,
                        'highest_price': initial_price,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                else:
                    # Use placeholder price
                    monitored_tokens[token_address] = {
                        'initial_price': 0.01,
                        'highest_price': 0.01,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                
                return True
            else:
                logging.error("Transaction failed or could not be confirmed")
                return False
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction error: {error_message}")
            return False
            
    except Exception as e:
        logging.error(f"Error in simplified buy: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def test_basic_swap():
    """Test a basic swap using USDC, a well-established token."""
    logging.info("===== TESTING BASIC SWAP WITH USDC =====")
    
    # USDC token address
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    # Use the simplified buy function
    result = simplified_buy_token(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ USDC swap test successful!")
        return True
    else:
        logging.error("❌ USDC swap test failed.")
        return False

def extract_instructions_from_jupiter(quote_data):
    """Extract swap instructions from Jupiter API response."""
    try:
        logging.info(f"Extracting swap instructions from Jupiter quote")
        
        # If this is a string, parse it
        if isinstance(quote_data, str):
            quote_data = json.loads(quote_data)
            
        # Extract the swap instructions based on the Jupiter API response structure
        if "swapTransaction" in quote_data:
            # For Jupiter v6 format
            tx_data = quote_data["swapTransaction"]
            tx_bytes = base64.b64decode(tx_data)
            
            # Use solders to parse transaction
            from solders.transaction import VersionedTransaction
            try:
                tx = VersionedTransaction.from_bytes(tx_bytes)
                return tx.message.instructions
            except:
                # Fall back to legacy transaction format
                from solders.transaction import Transaction
                tx = Transaction.from_bytes(tx_bytes)
                return tx.message.instructions
                
        elif "data" in quote_data and "instructions" in quote_data["data"]:
            # For older Jupiter API format
            return quote_data["data"]["instructions"]
            
        logging.error(f"Unknown Jupiter quote format: {list(quote_data.keys())}")
        return None
    except Exception as e:
        logging.error(f"Error extracting instructions: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def create_priority_fee_instruction(micro_lamports=50000):
    """Create an instruction to set priority fee."""
    try:
        from solders.instruction import Instruction
        from solders.pubkey import Pubkey
        
        # ComputeBudget program ID
        compute_budget_program_id = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
        
        # Set compute unit price instruction (0x03)
        data = bytes([0x03]) + micro_lamports.to_bytes(4, 'little')
        
        # Create instruction with no accounts
        return Instruction(
            program_id=compute_budget_program_id,
            accounts=[],
            data=data
        )
    except Exception as e:
        logging.error(f"Error creating priority fee instruction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def create_transaction(instructions, recent_blockhash, payer):
    """Create a new transaction with the given instructions."""
    try:
        from solders.transaction import Transaction
        from solders.message import Message
        
        message = Message.new_with_blockhash(
            instructions=instructions,
            payer=payer,
            blockhash=recent_blockhash
        )
        
        return Transaction(message=message, signatures=[])
    except Exception as e:
        logging.error(f"Error creating transaction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def get_fresh_blockhash():
    """Get a fresh blockhash from the Solana network."""
    try:
        response = wallet._rpc_call("getLatestBlockhash", [])
        
        if 'result' in response and 'value' in response['result']:
            blockhash = response['result']['value']['blockhash']
            logging.info(f"Got fresh blockhash: {blockhash}")
            return blockhash
        else:
            logging.error(f"Failed to get latest blockhash: {response}")
            return None
    except Exception as e:
        logging.error(f"Error getting fresh blockhash: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_transaction_with_special_params(signed_transaction):
    """Submit transaction with optimized parameters for higher success rate."""
    try:
        # Encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # First attempt with skipPreflight=True
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature
            if signature == "1" * len(signature):
                logging.warning("Got all 1's signature, retrying with different parameters")
                
                # Try with Helius if configured
                if CONFIG.get('HELIUS_API_KEY'):
                    return submit_via_helius(signed_transaction)
                
                # Otherwise try again with different parameters
                return retry_transaction_submission(serialized_tx)
            
            logging.info(f"Transaction submitted successfully: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission failed: {error_message}")
            
            # Try with different parameters
            return retry_transaction_submission(serialized_tx)
    except Exception as e:
        logging.error(f"Error submitting transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def retry_transaction_submission(serialized_tx):
    """Retry transaction submission with alternate parameters."""
    try:
        # Try with skipPreflight=False and higher retry count
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "maxRetries": 10,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature again
            if signature == "1" * len(signature):
                logging.error("Still received all 1's signature - transaction failed")
                return None
            
            logging.info(f"Retry transaction submission successful: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Retry transaction submission failed: {error_message}")
            return None
    except Exception as e:
        logging.error(f"Error in retry transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_via_helius(signed_transaction):
    """Submit transaction via Helius for better execution success."""
    try:
        helius_endpoint = f"https://mainnet.helius-rpc.com/?api-key={CONFIG.get('HELIUS_API_KEY')}"
        
        # Serialize and encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # Prepare request
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 5,
                    "preflightCommitment": "confirmed"
                }
            ]
        }
        
        # Send request
        headers = {"Content-Type": "application/json"}
        response = requests.post(helius_endpoint, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data:
                signature = data["result"]
                logging.info(f"Helius transaction submission successful: {signature}")
                return signature
            else:
                error_message = data.get("error", {}).get("message", "Unknown error")
                logging.error(f"Helius transaction submission failed: {error_message}")
                return None
        else:
            logging.error(f"Helius API request failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Error in Helius transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def extract_instructions_from_jupiter(quote_data):
    """Extract swap instructions from Jupiter API response."""
    try:
        logging.info(f"Extracting swap instructions from Jupiter quote")
        
        # If this is a string, parse it
        if isinstance(quote_data, str):
            quote_data = json.loads(quote_data)
            
        # Extract the swap instructions based on the Jupiter API response structure
        if "swapTransaction" in quote_data:
            # For Jupiter v6 format
            tx_data = quote_data["swapTransaction"]
            tx_bytes = base64.b64decode(tx_data)
            
            # Use solders to parse transaction
            from solders.transaction import VersionedTransaction
            try:
                tx = VersionedTransaction.from_bytes(tx_bytes)
                return tx.message.instructions
            except:
                # Fall back to legacy transaction format
                from solders.transaction import Transaction
                tx = Transaction.from_bytes(tx_bytes)
                return tx.message.instructions
                
        elif "data" in quote_data and "instructions" in quote_data["data"]:
            # For older Jupiter API format
            return quote_data["data"]["instructions"]
            
        logging.error(f"Unknown Jupiter quote format: {list(quote_data.keys())}")
        return None
    except Exception as e:
        logging.error(f"Error extracting instructions: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def create_priority_fee_instruction(micro_lamports=50000):
    """Create an instruction to set priority fee."""
    try:
        from solders.instruction import Instruction
        from solders.pubkey import Pubkey
        
        # ComputeBudget program ID
        compute_budget_program_id = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
        
        # Set compute unit price instruction (0x03)
        data = bytes([0x03]) + micro_lamports.to_bytes(4, 'little')
        
        # Create instruction with no accounts
        return Instruction(
            program_id=compute_budget_program_id,
            accounts=[],
            data=data
        )
    except Exception as e:
        logging.error(f"Error creating priority fee instruction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def create_transaction(instructions, recent_blockhash, payer):
    """Create a new transaction with the given instructions."""
    try:
        from solders.transaction import Transaction
        from solders.message import Message
        
        message = Message.new_with_blockhash(
            instructions=instructions,
            payer=payer,
            blockhash=recent_blockhash
        )
        
        return Transaction(message=message, signatures=[])
    except Exception as e:
        logging.error(f"Error creating transaction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def get_fresh_blockhash():
    """Get a fresh blockhash from the Solana network."""
    try:
        response = wallet._rpc_call("getLatestBlockhash", [])
        
        if 'result' in response and 'value' in response['result']:
            blockhash = response['result']['value']['blockhash']
            logging.info(f"Got fresh blockhash: {blockhash}")
            return blockhash
        else:
            logging.error(f"Failed to get latest blockhash: {response}")
            return None
    except Exception as e:
        logging.error(f"Error getting fresh blockhash: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_transaction_with_special_params(signed_transaction):
    """Submit transaction with optimized parameters for higher success rate."""
    try:
        # Encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # First attempt with skipPreflight=True
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature
            if signature == "1" * len(signature):
                logging.warning("Got all 1's signature, retrying with different parameters")
                
                # Try with Helius if configured
                if CONFIG.get('HELIUS_API_KEY'):
                    return submit_via_helius(signed_transaction)
                
                # Otherwise try again with different parameters
                return retry_transaction_submission(serialized_tx)
            
            logging.info(f"Transaction submitted successfully: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission failed: {error_message}")
            
            # Try with different parameters
            return retry_transaction_submission(serialized_tx)
    except Exception as e:
        logging.error(f"Error submitting transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def retry_transaction_submission(serialized_tx):
    """Retry transaction submission with alternate parameters."""
    try:
        # Try with skipPreflight=False and higher retry count
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "maxRetries": 10,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature again
            if signature == "1" * len(signature):
                logging.error("Still received all 1's signature - transaction failed")
                return None
            
            logging.info(f"Retry transaction submission successful: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Retry transaction submission failed: {error_message}")
            return None
    except Exception as e:
        logging.error(f"Error in retry transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_via_helius(signed_transaction):
    """Submit transaction via Helius for better execution success."""
    try:
        helius_endpoint = f"https://mainnet.helius-rpc.com/?api-key={CONFIG.get('HELIUS_API_KEY')}"
        
        # Serialize and encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # Prepare request
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 5,
                    "preflightCommitment": "confirmed"
                }
            ]
        }
        
        # Send request
        headers = {"Content-Type": "application/json"}
        response = requests.post(helius_endpoint, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data:
                signature = data["result"]
                logging.info(f"Helius transaction submission successful: {signature}")
                return signature
            else:
                error_message = data.get("error", {}).get("message", "Unknown error")
                logging.error(f"Helius transaction submission failed: {error_message}")
                return None
        else:
            logging.error(f"Helius API request failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Error in Helius transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def execute_optimized_transaction(token_address, amount_sol):
    """Execute an optimized transaction using robust Jupiter API handling."""
    try:
        logging.info(f"Starting optimized transaction for {token_address} with {amount_sol} SOL")
        
        if CONFIG['SIMULATION_MODE']:
            logging.info(f"[SIMULATION] Bought token {token_address}")
            token_buy_timestamps[token_address] = time.time()
            return "simulation-signature"
        
        # 1. Check wallet balance
        balance = wallet.get_balance()
        if balance < amount_sol + 0.05:  # Include buffer for fees
            logging.error(f"Insufficient balance: {balance} SOL")
            return None
            
        logging.info(f"Wallet balance: {balance} SOL")
        
        # 2. Convert SOL amount to lamports
        amount_lamports = int(amount_sol * 1_000_000_000)
        
        # 3. Get Jupiter quote and swap data with better error handling
        quote_data, swap_data = get_jupiter_quote_and_swap(
            SOL_TOKEN_ADDRESS,  # Input mint
            token_address,      # Output mint
            amount_lamports,    # Amount
            is_buy=True         # This is a buy
        )
        
        if not swap_data:
            logging.error("Failed to prepare swap")
            return None
        
        # 4. Submit transaction
        tx_base64 = swap_data["swapTransaction"]
        
        # Submit with specialized parameters
        signature = wallet._rpc_call("sendTransaction", [
            tx_base64,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        # 5. Check for errors
        if "result" not in signature:
            error_message = signature.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission failed: {error_message}")
            return None
        
        tx_signature = signature["result"]
        
        # 6. Check for all 1's signature
        if tx_signature == "1" * len(tx_signature):
            logging.warning("Got all 1's signature, trying alternative approach")
            
            # Try with skipPreflight=False
            alt_signature = wallet._rpc_call("sendTransaction", [
                tx_base64,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "maxRetries": 10,
                    "preflightCommitment": "confirmed"
                }
            ])
            
            if "result" not in alt_signature:
                error_message = alt_signature.get("error", {}).get("message", "Unknown error")
                logging.error(f"Alternative submission failed: {error_message}")
                return None
                
            tx_signature = alt_signature["result"]
            
            if tx_signature == "1" * len(tx_signature):
                logging.error("Still received all 1's signature - transaction failed")
                return None
        
        # 7. Verify transaction success
        logging.info(f"Transaction submitted with signature: {tx_signature}")
        success = check_transaction_status(tx_signature, max_attempts=8)
        
        if success:
            logging.info(f"Transaction confirmed successfully!")
            
            # Record transaction success
            token_buy_timestamps[token_address] = time.time()
            
            # Initialize token monitoring
            try:
                initial_price = get_token_price(token_address)
                if initial_price:
                    monitored_tokens[token_address] = {
                        'initial_price': initial_price,
                        'highest_price': initial_price,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                else:
                    # Use placeholder value
                    monitored_tokens[token_address] = {
                        'initial_price': 0.0001,
                        'highest_price': 0.0001,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
            except Exception as e:
                logging.warning(f"Error setting initial price: {str(e)}")
                # Use placeholder value
                monitored_tokens[token_address] = {
                    'initial_price': 0.0001,
                    'highest_price': 0.0001,
                    'partial_profit_taken': False,
                    'buy_time': time.time()
                }
            
            return tx_signature
        else:
            logging.error(f"Transaction failed or could not be confirmed")
            return None
    except Exception as e:
        logging.error(f"Error in optimized transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def create_priority_fee_instruction(micro_lamports=50000):
    """Create an instruction to set priority fee."""
    try:
        from solders.instruction import Instruction
        from solders.pubkey import Pubkey
        
        # ComputeBudget program ID
        compute_budget_program_id = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
        
        # Set compute unit price instruction (0x03)
        data = bytes([0x03]) + micro_lamports.to_bytes(4, 'little')
        
        # Create instruction with no accounts
        return Instruction(
            program_id=compute_budget_program_id,
            accounts=[],
            data=data
        )
    except Exception as e:
        logging.error(f"Error creating priority fee instruction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def create_transaction(instructions, recent_blockhash, payer):
    """Create a new transaction with the given instructions."""
    try:
        from solders.transaction import Transaction
        from solders.message import Message
        
        message = Message.new_with_blockhash(
            instructions=instructions,
            payer=payer,
            blockhash=recent_blockhash
        )
        
        return Transaction(message=message, signatures=[])
    except Exception as e:
        logging.error(f"Error creating transaction: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def get_fresh_blockhash():
    """Get a fresh blockhash from the Solana network."""
    try:
        response = wallet._rpc_call("getLatestBlockhash", [])
        
        if 'result' in response and 'value' in response['result']:
            blockhash = response['result']['value']['blockhash']
            logging.info(f"Got fresh blockhash: {blockhash}")
            return blockhash
        else:
            logging.error(f"Failed to get latest blockhash: {response}")
            return None
    except Exception as e:
        logging.error(f"Error getting fresh blockhash: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_transaction_with_special_params(signed_transaction):
    """Submit transaction with optimized parameters for higher success rate."""
    try:
        # Encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # First attempt with skipPreflight=True
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "processed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature
            if signature == "1" * len(signature):
                logging.warning("Got all 1's signature, retrying with different parameters")
                
                # Try with Helius if configured
                if CONFIG.get('HELIUS_API_KEY'):
                    return submit_via_helius(signed_transaction)
                
                # Otherwise try again with different parameters
                return retry_transaction_submission(serialized_tx)
            
            logging.info(f"Transaction submitted successfully: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission failed: {error_message}")
            
            # Try with different parameters
            return retry_transaction_submission(serialized_tx)
    except Exception as e:
        logging.error(f"Error submitting transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def retry_transaction_submission(serialized_tx):
    """Retry transaction submission with alternate parameters."""
    try:
        # Try with skipPreflight=False and higher retry count
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "maxRetries": 10,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            
            # Check for all 1's signature again
            if signature == "1" * len(signature):
                logging.error("Still received all 1's signature - transaction failed")
                return None
            
            logging.info(f"Retry transaction submission successful: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Retry transaction submission failed: {error_message}")
            return None
    except Exception as e:
        logging.error(f"Error in retry transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def submit_via_helius(signed_transaction):
    """Submit transaction via Helius for better execution success."""
    try:
        helius_endpoint = f"https://mainnet.helius-rpc.com/?api-key={CONFIG.get('HELIUS_API_KEY')}"
        
        # Serialize and encode transaction
        serialized_tx = base64.b64encode(signed_transaction.serialize()).decode("utf-8")
        
        # Prepare request
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 5,
                    "preflightCommitment": "confirmed"
                }
            ]
        }
        
        # Send request
        headers = {"Content-Type": "application/json"}
        response = requests.post(helius_endpoint, json=payload, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data:
                signature = data["result"]
                logging.info(f"Helius transaction submission successful: {signature}")
                return signature
            else:
                error_message = data.get("error", {}).get("message", "Unknown error")
                logging.error(f"Helius transaction submission failed: {error_message}")
                return None
        else:
            logging.error(f"Helius API request failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logging.error(f"Error in Helius transaction submission: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def execute_optimized_transaction(token_address, amount_sol):
    """Execute an optimized transaction with direct construction and submission."""
    try:
        logging.info(f"Starting optimized transaction for {token_address} with {amount_sol} SOL")
        
        # 1. Get secure keypair
        keypair = get_secure_keypair()
        
        # 2. Convert SOL amount to lamports
        amount_lamports = int(amount_sol * 1_000_000_000)
        
        # 3. Get Jupiter quote
        quote_params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": "100"
        }
        
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        quote_response = requests.get(quote_url, params=quote_params, timeout=15)
        
        if quote_response.status_code != 200:
            logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
            return None
            
        quote_data = quote_response.json()
        logging.info(f"Got Jupiter quote for {amount_sol} SOL -> {token_address}")
        
        # 4. Prepare swap with all parameters
        swap_params = {
            "quoteResponse": quote_data,
            "userPublicKey": str(keypair.pubkey()),
            "wrapUnwrapSOL": True,
            "computeUnitPriceMicroLamports": 50000,  # Higher priority fee
            "prioritizationFeeLamports": 10000
        }
        
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        swap_response = requests.post(
            swap_url,
            json=swap_params,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to prepare swap: {swap_response.status_code}")
            return None
            
        swap_data = swap_response.json()
        
        if "swapTransaction" not in swap_data:
            logging.error(f"Swap response missing transaction data")
            return None
            
        # 5. Directly submit the transaction with optimized parameters
        tx_base64 = swap_data["swapTransaction"]
        
        # Submit with special parameters
        signature = submit_transaction_with_special_params(tx_base64)
        
        if signature:
            # Check transaction success
            success = check_transaction_status(signature)
            if success:
                logging.info(f"Transaction confirmed successfully: {signature}")
                
                # Record transaction success
                token_buy_timestamps[token_address] = time.time()
                
                # Initialize token monitoring
                initial_price = get_token_price(token_address)
                if initial_price:
                    monitored_tokens[token_address] = {
                        'initial_price': initial_price,
                        'highest_price': initial_price,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                
                return signature
            else:
                logging.error(f"Transaction failed or could not be confirmed")
                return None
        else:
            logging.error("Failed to submit transaction")
            return None
    except Exception as e:
        logging.error(f"Error in optimized transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def main():
    """Main entry point with proper JavaScript testing."""
    logging.info("============ BOT STARTING ============")
    
    # Check Solders version at startup
    solders_version = check_solders_version()
    logging.info(f"Solders version: {solders_version}")
    
    if initialize():
        # Try using the JavaScript implementation with better error handling
        test_token = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
        test_amount = 0.001  # Very small test amount
        
        logging.info(f"Testing JavaScript transaction with {test_amount} SOL...")
        try:
            # Verify Node.js is installed
            logging.info("Checking Node.js installation...")
            node_check = subprocess.run(
                ['node', '--version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            logging.info(f"Node.js version: {node_check.stdout.strip()}")
            
            # Check if swap.js exists
            import os
            if os.path.exists('swap.js'):
                logging.info("swap.js file found")
                file_size = os.path.getsize('swap.js')
                logging.info(f"swap.js file size: {file_size} bytes")
            else:
                logging.error("swap.js file not found in current directory")
                logging.info(f"Current directory: {os.getcwd()}")
                logging.info(f"Directory contents: {os.listdir('.')}")
                return
            
            # Try a JavaScript call with minimal parameters
            logging.info("Executing test JavaScript call...")
            command = ['node', 'swap.js', test_token, str(test_amount), 'false']
            
            logging.info(f"Command: {' '.join(command)}")
            test_result = subprocess.run(
                command,
                env=os.environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Log the output in detail
            logging.info(f"JavaScript test STDOUT: {test_result.stdout}")
            if test_result.stderr:
                logging.error(f"JavaScript test STDERR: {test_result.stderr}")
            
            logging.info(f"JavaScript test return code: {test_result.returncode}")
            
            success = test_result.returncode == 0
            
            if success:
                logging.info("JavaScript test successful!")
                logging.info("Starting trading loop...")
                trading_loop()
            else:
                logging.error("JavaScript test failed. Cannot start trading.")
                logging.error("Please verify JavaScript setup and configuration.")
        except Exception as e:
            logging.error(f"Error during JavaScript test: {str(e)}")
            logging.error(traceback.format_exc())
    else:
        logging.error("Failed to initialize bot. Please check configurations.")

# Add this at the end of your file
if __name__ == "__main__":
    main()
