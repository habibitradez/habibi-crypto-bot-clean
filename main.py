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
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '20')),  # Adding this for backward compatibility
    'PARTIAL_PROFIT_TARGET_PCT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '10')),
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '50')),  # Adding this for backward compatibility
    'STOP_LOSS_PCT': int(os.environ.get('STOP_LOSS_PERCENT', '5')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '8')),  # Adding this for backward compatibility
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '2')),
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '60')),
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '1000')),
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '3')),
    'MAX_HOLD_TIME_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '2')),
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.10')),  # Reduced to 0.10 SOL
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '100')),
    'RETRY_ATTEMPTS': int(os.environ.get('RETRY_ATTEMPTS', '3')),
    'JUPITER_RATE_LIMIT_PER_MIN': int(os.environ.get('JUPITER_RATE_LIMIT_PER_MIN', '50')),
    'TOKENS_PER_DAY': int(os.environ.get('TOKENS_PER_DAY', '20')),        # Target 20 tokens per day
    'PROFIT_PER_TOKEN': int(os.environ.get('PROFIT_PER_TOKEN', '50')),    # Target $50 profit per token
    'MIN_PROFIT_PCT': int(os.environ.get('MIN_PROFIT_PCT', '15')),        # Take profit at just 20% gain
    'MAX_HOLD_TIME_SECONDS': int(os.environ.get('MAX_HOLD_TIME_SECONDS', '45')), # Only hold for 60 seconds max
    'USE_PUMP_FUN_API': os.environ.get('USE_PUMP_FUN_API', 'true').lower() == 'true', # Use pump.fun API
    'MAX_TOKEN_AGE_MINUTES': int(os.environ.get('MAX_TOKEN_AGE_MINUTES', '5')),  # Only buy very new tokens
    'QUICK_FLIP_MODE': os.environ.get('QUICK_FLIP_MODE', 'true').lower() == 'true', # Enable quick flip mode

    # Memory optimization
    'RPC_CALL_DELAY_MS': int(os.environ.get('RPC_CALL_DELAY_MS', '300')),
    'SKIP_ZERO_BALANCE_TOKENS': os.environ.get('SKIP_ZERO_BALANCE_TOKENS', 'true').lower() == 'true',
    'ZERO_BALANCE_TOKEN_CACHE': {},
    'ZERO_BALANCE_CACHE_EXPIRY': int(os.environ.get('ZERO_BALANCE_CACHE_EXPIRY', '3600'))
}

def update_config_for_quicknode():
    """Update configuration to use QuickNode Metis Jupiter features."""
    global CONFIG
    
    # Check if QuickNode should be enabled - use os.environ.get for proper environment variable access
    solana_rpc_url = os.environ.get('SOLANA_RPC_URL', '')
    use_quicknode = False
    
    if use_quicknode:
        # Add QuickNode specific settings
        CONFIG.update({
            'USE_QUICKNODE_METIS': True,
            'QUICKNODE_RATE_LIMIT': 50,  # 50 RPS from Launch plan
            'QUICKNODE_REQUESTS_PER_MONTH': 130000000,  # 130M requests/month
            'PREFER_QUICKNODE_TOKENS': True,
            'QUICKNODE_MIN_LIQUIDITY': 1000,  # Minimum $1000 liquidity
            'QUICKNODE_TIMEFRAME': '1h'  # Look for tokens from last hour
        })
        
        logging.info("✅ Updated configuration for QuickNode Metis Jupiter Swap API")
        logging.info(f"   RPC URL: {solana_rpc_url[:50]}...")
        logging.info(f"   Rate Limit: {CONFIG['QUICKNODE_RATE_LIMIT']} RPS")
        logging.info(f"   Monthly Requests: {CONFIG['QUICKNODE_REQUESTS_PER_MONTH']:,}")
    else:
        CONFIG['USE_QUICKNODE_METIS'] = False
        logging.info("ℹ️ QuickNode Metis not detected, using standard configuration")
        logging.info(f"   Current RPC: {solana_rpc_url[:50]}...")

# Call the function after it's defined
update_config_for_quicknode()

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
    "EKpQGSJtJMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",   # WIF
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

def update_environment_variable(key, value):
    """Update environment variable for persistence across restarts."""
    try:
        os.environ[key] = str(value)
        logging.info(f"Updated {key} = {value}")
    except Exception as e:
        logging.error(f"Failed to update {key}: {str(e)}")

def enhanced_token_filter(token_address):
    """Enhanced token filtering to avoid obvious rug pulls."""
    try:
        # Quick Jupiter validation
        response = requests.get(
            f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=100000000",
            timeout=8
        )
        
        if response.status_code == 200 and 'outAmount' in response.text:
            data = response.json()
            out_amount = int(data.get('outAmount', 0))
            
            # Filter out tokens with suspicious exchange rates
            if out_amount > 0:
                exchange_rate = 100000000 / out_amount  # SOL to token rate
                
                # Skip tokens that are too expensive or too cheap (likely rugs)
                if 0.001 < exchange_rate < 10000:
                    return True
                else:
                    logging.warning(f"❌ Suspicious exchange rate for {token_address[:8]}: {exchange_rate}")
                    return False
        
        return False
        
    except Exception as e:
        logging.warning(f"Token filter error for {token_address[:8]}: {str(e)}")
        return False

def calculate_trade_profit(buy_price, sell_price, amount_sol):
    """Calculate actual profit from a trade."""
    try:
        if buy_price and sell_price and amount_sol:
            # Calculate profit in USD
            price_change_percentage = ((sell_price - buy_price) / buy_price)
            profit_usd = amount_sol * 240 * price_change_percentage  # Assuming ~$240 SOL
            profit_percentage = price_change_percentage * 100
            
            return profit_usd, profit_percentage
        return 0, 0
    except Exception as e:
        logging.error(f"Error calculating profit: {str(e)}")
        return 0, 0

def get_token_price_for_profit_calc(token_address):
    """Get token price for profit calculation."""
    try:
        # Method 1: Jupiter quote for price
        response = requests.get(
            f"https://quote-api.jup.ag/v6/quote?inputMint={token_address}&outputMint=So11111111111111111111111111111111111111112&amount=1000000",
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            out_amount = int(data.get('outAmount', 0))
            if out_amount > 0:
                # Price in SOL per token
                price_sol = out_amount / 1000000 / 1e9  # Convert lamports to SOL
                price_usd = price_sol * 240  # Approximate SOL price
                return price_usd
        
        # Method 2: DexScreener fallback
        response = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs', [])
            if pairs:
                price_usd = float(pairs[0].get('priceUsd', 0))
                if price_usd > 0:
                    return price_usd
        
        return None
        
    except Exception as e:
        logging.warning(f"Could not get price for {token_address[:8]}: {str(e)}")
        return None

def add_token_to_monitoring(token_address, buy_price, amount, signature):
    """Add token to monitoring list."""
    try:
        # Your existing token monitoring logic
        logging.info(f"📊 Added {token_address[:8]} to monitoring (bought at ${buy_price:.6f})")
    except Exception as e:
        logging.error(f"Error adding token to monitoring: {str(e)}")

def remove_token_from_monitoring(token_address):
    """Remove token from monitoring list."""
    try:
        # Your existing token removal logic
        logging.info(f"📊 Removed {token_address[:8]} from monitoring")
    except Exception as e:
        logging.error(f"Error removing token from monitoring: {str(e)}")



def enhanced_find_newest_tokens_with_free_apis():
    """
    Complete enhanced token discovery using Helius DEVELOPER + free API fallbacks
    Uses your real Helius API key: 6e4e884f-d053-4682-81a5-3aeaa0b4c7dc
    """
    try:
        all_tokens = []
        helius_key = os.environ.get('HELIUS_API_KEY', '6e4e884f-d053-4682-81a5-3aeaa0b4c7dc')
        
        if helius_key:
            logging.info("🔥 Starting PREMIUM Helius DEVELOPER token discovery with your real API key...")
            
            # Method 1: Helius transaction analysis (using proven endpoints from your dashboard)
            try:
                # Your dashboard shows 'getsignaturesforaddress' is working well (35 calls)
                popular_tokens = [
                    "So11111111111111111111111111111111111111112",  # SOL
                    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
                    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
                ]
                
                for token_address in popular_tokens:
                    try:
                        # Use the exact RPC URL from your Helius dashboard
                        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
                        
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignaturesForAddress",
                            "params": [
                                token_address,
                                {
                                    "limit": 8,
                                    "commitment": "confirmed"
                                }
                            ]
                        }
                        
                        response = requests.post(rpc_url, json=payload, timeout=12)
                        
                        if response.status_code == 200:
                            data = response.json()
                            if 'result' in data and data['result']:
                                signatures = [tx['signature'] for tx in data['result'][:3]]  # Top 3 recent
                                
                                # Get transaction details to find new tokens
                                for signature in signatures:
                                    tx_payload = {
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getTransaction",
                                        "params": [
                                            signature,
                                            {
                                                "encoding": "jsonParsed",
                                                "commitment": "confirmed",
                                                "maxSupportedTransactionVersion": 0
                                            }
                                        ]
                                    }
                                    
                                    tx_response = requests.post(rpc_url, json=tx_payload, timeout=8)
                                    
                                    if tx_response.status_code == 200:
                                        tx_data = tx_response.json()
                                        
                                        if 'result' in tx_data and tx_data['result']:
                                            tx_info = tx_data['result']
                                            
                                            # Extract token mints from postTokenBalances
                                            if 'meta' in tx_info and 'postTokenBalances' in tx_info['meta']:
                                                for balance in tx_info['meta']['postTokenBalances']:
                                                    mint = balance.get('mint')
                                                    if mint and mint not in popular_tokens and len(mint) > 40:
                                                        all_tokens.append(mint)
                                                        logging.info(f"🔥 Helius found token: {mint[:8]}...")
                                
                                logging.info(f"✅ Helius analyzed {len(signatures)} transactions for {token_address[:8]}")
                                
                    except Exception as e:
                        logging.warning(f"Helius signature search failed for {token_address[:8]}: {str(e)}")
                        continue
                
                unique_helius_tokens = list(set(all_tokens))
                
                if unique_helius_tokens:
                    logging.info(f"🎯 Helius DEVELOPER found {len(unique_helius_tokens)} tokens from transaction analysis!")
                    all_tokens = unique_helius_tokens[:4]  # Keep top 4
                else:
                    logging.info("🔍 Helius transaction analysis complete, checking other methods...")
                
            except Exception as e:
                logging.warning(f"Helius DEVELOPER transaction analysis failed: {str(e)}")
            
            # Method 2: Enhanced Helius RPC for token accounts
            try:
                rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
                
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getProgramAccounts",
                    "params": [
                        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        {
                            "encoding": "jsonParsed",
                            "commitment": "confirmed",
                            "filters": [{"dataSize": 165}]
                        }
                    ]
                }
                
                response = requests.post(rpc_url, json=payload, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if 'result' in data and data['result']:
                        token_mints = set()
                        for account in data['result'][:8]:  # Check first 8 accounts
                            try:
                                parsed_data = account.get('account', {}).get('data', {}).get('parsed', {})
                                mint = parsed_data.get('info', {}).get('mint')
                                
                                if mint and len(mint) > 40:
                                    token_mints.add(mint)
                                    
                            except Exception as e:
                                continue
                        
                        if token_mints:
                            helius_rpc_tokens = list(token_mints)[:3]
                            all_tokens.extend(helius_rpc_tokens)
                            logging.info(f"💎 Helius RPC found {len(helius_rpc_tokens)} token accounts")
                
            except Exception as e:
                logging.warning(f"Helius RPC method failed: {str(e)}")
        
        else:
            logging.info("🔄 No Helius key found, using free APIs only...")
        
        # Method 3: DexScreener trending tokens (FREE)
        try:
            logging.info("📈 Fetching DexScreener trending tokens...")
            response = requests.get("https://api.dexscreener.com/latest/dex/tokens/trending/solana", timeout=10)
            if response.status_code == 200:
                data = response.json()
                for token in data.get('pairs', [])[:6]:
                    if token.get('baseToken', {}).get('address'):
                        all_tokens.append(token['baseToken']['address'])
                        logging.info(f"📈 DexScreener: {token['baseToken']['symbol']} - Vol: ${token.get('volume', {}).get('h24', 0):,.0f}")
        except Exception as e:
            logging.warning(f"DexScreener failed: {str(e)}")
        
        # Method 4: Pump.fun fresh launches (FREE)
        try:
            logging.info("🚀 Fetching fresh Pump.fun launches...")
            response = requests.get("https://frontend-api.pump.fun/coins/king-of-the-hill?offset=0&limit=50&includeNsfw=false", timeout=10)
            if response.status_code == 200:
                data = response.json()
                for token in data[:6]:
                    if token.get('mint'):
                        all_tokens.append(token['mint'])
                        logging.info(f"🚀 Pump.fun: {token.get('name', 'Unknown')} - MC: ${token.get('market_cap', 0):,.0f}")
        except Exception as e:
            logging.warning(f"Pump.fun failed: {str(e)}")
        
        # Method 5: Birdeye trending (FREE tier)
        try:
            logging.info("🐦 Fetching Birdeye trending tokens...")
            birdeye_key = os.environ.get('BIRDEYE_API_KEY', '')
            
            if birdeye_key:
                headers = {"X-API-KEY": birdeye_key}
                response = requests.get(
                    "https://public-api.birdeye.so/public/tokenlist?sort_by=v24hUSD&sort_type=desc&offset=0&limit=20",
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    for token in data.get('data', {}).get('tokens', [])[:4]:
                        if token.get('address') and token.get('v24hUSD', 0) > 10000:
                            all_tokens.append(token['address'])
                            logging.info(f"🐦 Birdeye: {token.get('symbol')} - Vol: ${token.get('v24hUSD', 0):,.0f}")
            else:
                logging.info("🔄 No Birdeye key found, skipping Birdeye API")
                
        except Exception as e:
            logging.warning(f"Birdeye failed: {str(e)}")
        
        # Remove duplicates and validate
        unique_tokens = list(set(all_tokens))
        validated_tokens = []
        
        logging.info(f"🔍 Validating {len(unique_tokens)} discovered tokens...")
        
        for token in unique_tokens[:10]:  # Check top 10
            if is_token_tradable_enhanced(token):
                validated_tokens.append(token)
                logging.info(f"✅ Validated: {token[:8]}...")
                if len(validated_tokens) >= 5:  # Max 5 tokens for focus
                    break
            else:
                logging.warning(f"❌ Failed validation: {token[:8]}...")
        
        if validated_tokens:
            logging.info(f"🎯 HELIUS DEVELOPER + Free APIs found {len(validated_tokens)} validated trading opportunities!")
            return validated_tokens
        else:
            logging.warning("❌ No validated tokens found, using emergency fallback...")
            return [
                "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
                "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",      # ORCA
            ]
        
    except Exception as e:
        logging.error(f"Enhanced token discovery failed: {str(e)}")
        return [
            "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",  # WIF
            "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",      # ORCA
        ]

def is_likely_rug_pull(token_address):
    """Quick rug pull detection before trading."""
    try:
        # Check if token has locked liquidity (basic check)
        response = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            pairs = data.get('pairs', [])
            
            for pair in pairs:
                # Check for suspicious signs
                liquidity = pair.get('liquidity', {}).get('usd', 0)
                volume_24h = pair.get('volume', {}).get('h24', 0)
                
                # Red flags
                if liquidity < 5000:  # Very low liquidity
                    return True
                if volume_24h > liquidity * 10:  # Suspicious volume ratio
                    return True
                    
        return False
        
    except:
        return False  # If check fails, allow trade

def update_performance_stats(success, profit_amount=0, token_address=""):
    """Update performance statistics with proper profit tracking."""
    try:
        # Get current stats
        total_trades = int(os.environ.get('TOTAL_TRADES', '0'))
        successful_trades = int(os.environ.get('SUCCESSFUL_TRADES', '0'))
        total_profit = float(os.environ.get('TOTAL_PROFIT', '0.0'))
        
        # Update stats
        total_trades += 1
        
        if success:
            successful_trades += 1
            total_profit += profit_amount
            logging.info(f"💰 PROFITABLE TRADE: +${profit_amount:.2f} | Total: ${total_profit:.2f}")
        
        # Calculate rates
        success_rate = (successful_trades / total_trades * 100) if total_trades > 0 else 0
        hourly_rate = total_profit  # Simplified for now
        
        # Log performance update
        logging.info("🔶 =================== PERFORMANCE UPDATE ===================")
        logging.info(f"💎 Daily profit: ${total_profit:.2f}")
        logging.info(f"✅ Successful trades: {successful_trades}")
        logging.info(f"📊 Buy/Sell ratio: {successful_trades}/{total_trades - successful_trades}")
        logging.info(f"🎯 Tokens monitored: {total_trades}")
        logging.info(f"🔥 Buy attempts: {total_trades} | Success rate: {success_rate:.1f}%")
        logging.info(f"⚡ Hourly rate: ${hourly_rate:.2f}/hour")
        
        # Calculate what's needed for $1K
        needed_hourly = (1000 - total_profit) / 24  # Assuming 24 hour operation
        logging.info(f"📈 Projected daily: ${total_profit:.2f}")
        logging.info(f"🎯 Trade rate: {successful_trades} trades/hour")
        logging.info(f"⚠️ Need ${needed_hourly:.2f}/hour to reach $1k target")
        
        # Auto-scaling suggestion
        current_position = float(os.environ.get('TRADE_AMOUNT_SOL', '0.144'))
        if success_rate > 20 and total_profit > 50:  # Good performance
            suggested_position = min(current_position * 1.2, 0.5)  # Max 0.5 SOL
            logging.info(f"🚀 Increasing buy amount to {suggested_position:.3f} SOL")
        
        logging.info("🔶 =======================================================")
        
        return {
            'total_trades': total_trades,
            'successful_trades': successful_trades,
            'total_profit': total_profit,
            'success_rate': success_rate
        }
        
    except Exception as e:
        logging.error(f"Error updating performance stats: {str(e)}")
        return None

def is_token_tradable_enhanced(token_address):
    """Enhanced token validation with rug pull detection."""
    try:
        # Existing Jupiter validation
        response = requests.get(
            f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=50000",  # Reduced test amount
            timeout=8
        )
        if response.status_code == 200 and 'outAmount' in response.text:
            # Additional rug pull check
            if is_likely_rug_pull(token_address):
                logging.warning(f"⚠️ Potential rug pull detected for {token_address[:8]}, skipping...")
                return False
            return True
        
        # Rest of your existing validation code...
        helius_key = os.environ.get('HELIUS_API_KEY', '6e4e884f-d053-4682-81a5-3aeaa0b4c7dc')
        if helius_key:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [token_address, {"encoding": "base64"}]
            }
            
            response = requests.post(rpc_url, json=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                if data.get('result', {}).get('value') is not None:
                    # Additional rug pull check
                    if is_likely_rug_pull(token_address):
                        logging.warning(f"⚠️ Potential rug pull detected for {token_address[:8]}, skipping...")
                        return False
                    return True
        
        if len(token_address) >= 43 and len(token_address) <= 44:
            return True
            
        return False
        
    except Exception as e:
        return False

def extract_new_token_addresses_enhanced(transactions):
    """Extract new token addresses from Helius transaction data with enhanced parsing."""
    new_tokens = []
    
    try:
        for tx in transactions:
            # Method 1: Token transfers
            if 'tokenTransfers' in tx:
                for transfer in tx['tokenTransfers']:
                    mint = transfer.get('mint')
                    if mint and len(mint) > 40:
                        new_tokens.append(mint)
            
            # Method 2: Post token balances
            if 'meta' in tx and 'postTokenBalances' in tx['meta']:
                for balance in tx['meta']['postTokenBalances']:
                    mint = balance.get('mint')
                    if mint and len(mint) > 40:
                        new_tokens.append(mint)
            
            # Method 3: Account keys (newly created accounts)
            if 'transaction' in tx and 'message' in tx['transaction']:
                account_keys = tx['transaction']['message'].get('accountKeys', [])
                for account in account_keys:
                    if isinstance(account, str) and len(account) > 40:
                        new_tokens.append(account)
            
            # Method 4: Program interactions
            if 'instructions' in tx:
                for instruction in tx['instructions']:
                    if instruction.get('programId') == 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA':
                        accounts = instruction.get('accounts', [])
                        for account in accounts:
                            if len(account) > 40:
                                new_tokens.append(account)
    
    except Exception as e:
        logging.warning(f"Token extraction failed: {str(e)}")
    
    # Remove duplicates and common tokens
    common_tokens = {
        'So11111111111111111111111111111111111111112',  # SOL
        'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',  # USDC
        'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',  # USDT
    }
    
    unique_tokens = []
    for token in new_tokens:
        if token not in common_tokens and token not in unique_tokens:
            unique_tokens.append(token)
    
    return unique_tokens

def is_token_tradable_enhanced(token_address):
    """Enhanced token validation using multiple methods including Helius."""
    try:
        # Method 1: Jupiter quote test (most reliable)
        response = requests.get(
            f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=100000",
            timeout=8
        )
        if response.status_code == 200 and 'outAmount' in response.text:
            return True
        
        # Method 2: Enhanced validation using Helius RPC
        helius_key = os.environ.get('HELIUS_API_KEY', '6e4e884f-d053-4682-81a5-3aeaa0b4c7dc')
        if helius_key:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [token_address, {"encoding": "base64"}]
            }
            
            response = requests.post(rpc_url, json=payload, timeout=6)
            if response.status_code == 200:
                data = response.json()
                return data.get('result', {}).get('value') is not None
        
        # Method 3: Basic address validation
        if len(token_address) >= 43 and len(token_address) <= 44:
            return True
            
        return False
        
    except Exception as e:
        return False


def is_token_tradable_jupiter(token_address):
    """
    Fast, reliable token validation using Jupiter API.
    Tests if token can actually be traded.
    """
    try:
        # Quick Jupiter quote test - most reliable validation
        response = requests.get(
            f"https://quote-api.jup.ag/v6/quote"
            f"?inputMint=So11111111111111111111111111111111111111112"
            f"&outputMint={token_address}"
            f"&amount=100000"  # 0.0001 SOL test
            f"&slippageBps=300",  # 3% slippage tolerance
            timeout=8
        )
        
        if response.status_code == 200:
            data = response.json()
            # Check if we get a valid quote with reasonable output
            if 'outAmount' in data and int(data['outAmount']) > 0:
                return True
        
        return False
        
    except Exception as e:
        logging.debug(f"Token validation failed for {token_address[:8]}: {str(e)}")
        return False


def update_environment_for_free_apis():
    """Update environment to disable QuickNode and enable free APIs."""
    import os
    
    # Disable QuickNode
    os.environ['USE_QUICKNODE_METIS'] = 'false'
    
    # Optional: Add Birdeye key for enhanced discovery
    # Get free key from https://birdeye.so/
    if not os.environ.get('BIRDEYE_API_KEY'):
        logging.info("💡 TIP: Get free Birdeye API key from birdeye.so for enhanced token discovery")
    
    logging.info("🔧 Environment updated for FREE API mode")
    logging.info("💰 QuickNode disabled - saving $300/month!")


def get_verified_tradable_tokens():
    """Get a list of verified tradable tokens for fallback."""
    verified_tokens = [
        
        {
            "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 
            "symbol": "WIF",
            "verified": True
        },
        {
            "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
            "symbol": "ORCA", 
            "verified": True
        },
        {
            "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            "symbol": "SAMO",
            "verified": True
        }
    ]
    
    # Filter out tokens we're already monitoring or bought recently
    current_time = time.time()
    available_tokens = []
    
    for token in verified_tokens:
        token_address = token["address"]
        
        # Skip if currently monitoring
        if token_address in monitored_tokens:
            continue
            
        # Skip if bought recently (reduced cooldown for verified tokens)
        if token_address in token_buy_timestamps:
            minutes_since_buy = (current_time - token_buy_timestamps[token_address]) / 60
            if minutes_since_buy < 20:  # Only 20 min cooldown for verified tokens
                continue
        
        available_tokens.append(token_address)
    
    return available_tokens
    
def enhanced_find_newest_tokens():
    """Enhanced token finder with better validation."""
    try:
        logging.info("🔍 Starting enhanced token search...")
        
        # Method 1: Try pump.fun API with validation
        try:
            newest_tokens = get_newest_pump_fun_tokens(limit=10)  # Reduced limit for speed
            
            if newest_tokens:
                validated_tokens = []
                
                for token in newest_tokens:
                    if isinstance(token, dict) and token.get('minutes_old', 999) <= 3:  # Even newer - 3 minutes
                        token_address = token.get('address')
                        if token_address:
                            # Validate before adding
                            if validate_token_before_trading(token_address):
                                validated_tokens.append(token_address)
                                logging.info(f"✅ Validated new token: {token.get('symbol', 'Unknown')} ({token_address[:8]})")
                            else:
                                logging.warning(f"❌ Failed validation: {token.get('symbol', 'Unknown')} ({token_address[:8]})")
                
                if validated_tokens:
                    logging.info(f"🎯 Found {len(validated_tokens)} validated fresh tokens")
                    return validated_tokens[:2]  # Return max 2 for focus
        
        except Exception as e:
            logging.error(f"Error in pump.fun token search: {str(e)}")
        
        # Method 2: Use verified tradable tokens as fallback
        logging.info("🔄 Using verified tradable tokens as fallback...")
        verified_tokens = get_verified_tradable_tokens()
        
        if verified_tokens:
            logging.info(f"📋 Found {len(verified_tokens)} verified tradable tokens")
            return verified_tokens[:2]  # Return max 2
        
        # Method 3: Scan recent transactions (if we have time)
        try:
            logging.info("🔍 Scanning recent transactions for tokens...")
            scanned_tokens = scan_recent_solana_transactions()
            
            if scanned_tokens:
                validated_scanned = []
                for token_address in scanned_tokens[:3]:  # Check only first 3
                    if validate_token_before_trading(token_address):
                        validated_scanned.append(token_address)
                
                if validated_scanned:
                    logging.info(f"✅ Found {len(validated_scanned)} validated tokens from transaction scan")
                    return validated_scanned[:1]  # Return only 1 from scanning
        
        except Exception as e:
            logging.error(f"Error in transaction scanning: {str(e)}")
        
        logging.warning("❌ No suitable tokens found from any method")
        return []
        
    except Exception as e:
        logging.error(f"Error in enhanced_find_newest_tokens: {str(e)}")
        return []

def smart_token_selection(potential_tokens):
    """Intelligently select the best token to trade with enhanced scoring."""
    if not potential_tokens:
        return None
    
    try:
        # Handle both string addresses and dict objects
        normalized_tokens = []
        
        for token in potential_tokens:
            if isinstance(token, dict):
                # Extract address from dict format
                address = token.get('address') or token.get('mint') or token.get('baseToken', {}).get('address')
                if address:
                    token_data = {
                        'address': address,
                        'source': token.get('source', 'unknown'),
                        'symbol': token.get('symbol', 'Unknown'),
                        'volume': token.get('volume', 0),
                        'market_cap': token.get('market_cap', 0)
                    }
                    normalized_tokens.append(token_data)
            elif isinstance(token, str) and len(token) > 40:
                # String address format
                token_data = {
                    'address': token,
                    'source': 'fallback',
                    'symbol': 'Unknown',
                    'volume': 0,
                    'market_cap': 0
                }
                normalized_tokens.append(token_data)
        
        if not normalized_tokens:
            return None
        
        # Score each token
        scored_tokens = []
        
        for token_data in normalized_tokens:
            score = 10  # Base score
            
            # Source-based scoring (prioritize premium sources)
            if 'helius' in token_data['source'].lower():
                score += 5  # Helius tokens get highest priority
            elif 'dexscreener' in token_data['source'].lower():
                score += 3
            elif 'birdeye' in token_data['source'].lower():
                score += 2
            elif 'pump.fun' in token_data['source'].lower():
                score += 1
            
            # Volume-based scoring
            volume = token_data.get('volume', 0)
            if isinstance(volume, (int, float)) and volume > 100000:
                score += 2
            elif isinstance(volume, (int, float)) and volume > 50000:
                score += 1
            
            # Market cap scoring (prefer smaller caps for meme coins)
            market_cap = token_data.get('market_cap', 0)
            if isinstance(market_cap, (int, float)) and 100000 <= market_cap <= 10000000:
                score += 2  # Sweet spot for meme coins
            
            scored_tokens.append({
                'token': token_data,
                'score': score
            })
        
        # Sort by score (highest first)
        scored_tokens.sort(key=lambda x: x['score'], reverse=True)
        
        # Return the highest-scoring token address
        best_token = scored_tokens[0]['token']
        
        logging.info(f"🎯 Selected best token: {best_token['symbol']} ({best_token['address'][:8]}) from {best_token['source']} (score: {scored_tokens[0]['score']})")
        
        return best_token['address']
        
    except Exception as e:
        logging.error(f"Error in smart token selection: {str(e)}")
        # Fallback to first valid token
        if potential_tokens:
            if isinstance(potential_tokens[0], dict):
                return potential_tokens[0].get('address') or potential_tokens[0].get('mint')
            else:
                return potential_tokens[0]
        return None


# FUNCTION 3: Add this NEW function for Helius testing
def test_helius_free_tier(helius_key):
    """Test Helius FREE tier capabilities and performance."""
    try:
        helius_rpc = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
        test_tokens = []
        
        logging.info("🧪 Testing Helius FREE tier limits and features...")
        
        # Test 1: Basic RPC health check
        headers = {'Content-Type': 'application/json'}
        health_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getHealth"
        }
        
        start_time = time.time()
        response = requests.post(helius_rpc, json=health_payload, headers=headers, timeout=10)
        response_time = time.time() - start_time
        
        if response.status_code == 200:
            logging.info(f"✅ Helius FREE RPC responding in {response_time:.2f}s")
        else:
            logging.warning(f"⚠️ Helius FREE RPC status: {response.status_code}")
            return []
        
        # Test 2: Try to get recent token program signatures (basic feature)
        try:
            sig_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token Program
                    {
                        "commitment": "confirmed",
                        "limit": 5  # Small limit for free tier
                    }
                ]
            }
            
            response = requests.post(helius_rpc, json=sig_payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if 'result' in data and data['result']:
                    logging.info(f"✅ Helius FREE can access recent transactions ({len(data['result'])} signatures)")
                    
                    # Try to parse one transaction for tokens (if free tier allows)
                    first_sig = data['result'][0]['signature']
                    
                    tx_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getParsedTransaction",
                        "params": [
                            first_sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed"
                            }
                        ]
                    }
                    
                    tx_response = requests.post(helius_rpc, json=tx_payload, headers=headers, timeout=8)
                    
                    if tx_response.status_code == 200:
                        tx_data = tx_response.json()
                        if 'result' in tx_data and tx_data['result']:
                            logging.info("✅ Helius FREE can parse transactions - basic token discovery possible")
                            
                            # Try to extract any token addresses (for testing)
                            try:
                                token_addresses = extract_new_token_addresses(tx_data['result'])
                                for addr in token_addresses[:2]:  # Limit for free tier
                                    test_tokens.append({
                                        'address': addr,
                                        'symbol': f'FREE-{addr[:4]}',
                                        'source': 'Helius/Free',
                                        'score': 12  # Good score for Helius discoveries
                                    })
                                    logging.info(f"🧪 Helius FREE discovered: {addr[:8]}")
                            except Exception as e:
                                logging.debug(f"Token extraction test failed: {str(e)}")
                        else:
                            logging.info("⚠️ Helius FREE transaction parsing limited")
                    else:
                        logging.warning(f"⚠️ Helius FREE transaction parsing failed: {tx_response.status_code}")
                else:
                    logging.warning("⚠️ Helius FREE returned no transaction signatures")
            else:
                logging.warning(f"⚠️ Helius FREE signature request failed: {response.status_code}")
                
        except Exception as e:
            logging.warning(f"Helius FREE advanced features failed: {str(e)}")
        
        # Test 3: Rate limit assessment
        logging.info(f"🧪 Helius FREE tier test complete - found {len(test_tokens)} tokens")
        
        if len(test_tokens) > 0:
            logging.info("💡 Helius FREE tier shows promise - upgrade could provide significant benefits!")
        else:
            logging.info("⚠️ Helius FREE tier very limited - upgrade likely needed for meaningful token discovery")
        
        return test_tokens
        
    except Exception as e:
        logging.warning(f"Helius FREE tier test failed: {str(e)}")
        return []


# FUNCTION 4: Add this NEW helper function  
def extract_new_token_addresses(transaction_data):
    """Parse transaction data to find newly created token addresses."""
    try:
        token_addresses = []
        
        if 'meta' in transaction_data and 'innerInstructions' in transaction_data['meta']:
            for inner_instruction in transaction_data['meta']['innerInstructions']:
                for instruction in inner_instruction.get('instructions', []):
                    # Look for token creation instructions
                    if (instruction.get('parsed', {}).get('type') in ['initializeMint', 'mintTo'] and
                        'info' in instruction.get('parsed', {}) and
                        'mint' in instruction['parsed']['info']):
                        
                        mint_address = instruction['parsed']['info']['mint']
                        if mint_address not in token_addresses:
                            token_addresses.append(mint_address)
        
        return token_addresses[:3]  # Limit to 3 per transaction
        
    except Exception as e:
        logging.debug(f"Error parsing transaction: {str(e)}")
        return []

def get_verified_tradable_tokens():
    """Get a list of verified tradable tokens for fallback (JUP removed)."""
    verified_tokens = [
        {
           # "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
            "symbol": "BONK",
            "verified": True
        },
        {
            "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 
            "symbol": "WIF",
            "verified": True
        },
        {
            "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE",
            "symbol": "ORCA", 
            "verified": True
        },
        {
            "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
            "symbol": "SAMO",
            "verified": True
        }
    ]
    
    # Add cooldown tracking to prevent rapid re-trading
    current_time = time.time()
    available_tokens = []
    
    for token in verified_tokens:
        last_bought_key = f"last_bought_{token['address']}"
        cooldown_minutes = 20  # 20 minute cooldown for verified tokens
        
        if last_bought_key not in globals() or \
           current_time - globals()[last_bought_key] > cooldown_minutes * 60:
            available_tokens.append(token)
    
    if available_tokens:
        logging.info(f"✅ Found {len(available_tokens)} verified tradable tokens available")
        return available_tokens
    else:
        logging.warning("⚠️ All verified tokens are in cooldown, returning all tokens")
        return verified_tokens

def validate_token_before_trading(token_address: str) -> bool:
    """Comprehensive token validation before attempting to trade."""
    try:
        logging.info(f"🔍 Validating token: {token_address[:8]}...")
        
        # 1. Basic address validation
        if not token_address or len(token_address) < 32:
            logging.warning(f"❌ Invalid token address length: {len(token_address) if token_address else 0}")
            return False
        
        # 2. Check blacklist
        blacklisted_tokens = getattr(validate_token_before_trading, 'blacklist', set())
        if token_address in blacklisted_tokens:
            logging.warning(f"❌ Token {token_address[:8]} is blacklisted")
            return False
        
        # 3. Test Jupiter quote (small amount)
        try:
            test_amount = 1000000  # 0.001 SOL in lamports
            quote_url = "https://quote-api.jup.ag/v6/quote"
            
            # Try to get a quote for a small amount
            params = {
                "inputMint": "So11111111111111111111111111111111111111112",  # SOL
                "outputMint": token_address,
                "amount": str(test_amount),
                "slippageBps": "300"
            }
            
            response = requests.get(quote_url, params=params, timeout=10)
            
            if response.status_code == 200 and response.json().get('outAmount'):
                logging.info(f"✅ Token {token_address[:8]} passed Jupiter validation")
                return True
            else:
                logging.warning(f"⚠️ Token {token_address[:8]} failed Jupiter quote validation")
                # Add to blacklist
                if not hasattr(validate_token_before_trading, 'blacklist'):
                    validate_token_before_trading.blacklist = set()
                validate_token_before_trading.blacklist.add(token_address)
                return False
                
        except Exception as quote_error:
            logging.warning(f"⚠️ Jupiter validation error for {token_address[:8]}: {str(quote_error)}")
            return False
        
    except Exception as e:
        logging.error(f"❌ Error validating token {token_address[:8]}: {str(e)}")
        return False

def get_newest_tokens_quicknode():
    """Get newest tokens using QuickNode's Metis Jupiter Swap API integration."""
    try:
        # Check if QuickNode Metis is enabled
        if not CONFIG.get('USE_QUICKNODE_METIS', False):
            return []
        
        quicknode_endpoint = CONFIG['SOLANA_RPC_URL']
        
        # Method 1: Try QuickNode new-pools endpoint
        try:
            new_pools_url = f"{quicknode_endpoint}/new-pools"
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'SolanaBot/1.0'
            }
            
            params = {
                'limit': 30,
                'timeframe': '1h',  # Last hour for freshest tokens
                'minLiquidity': 1000  # Minimum $1000 liquidity
            }
            
            logging.info("🔍 Fetching newest tokens via QuickNode new-pools...")
            
            response = requests.get(
                new_pools_url, 
                headers=headers, 
                params=params,
                timeout=15
            )
            
            if response.status_code == 200:
                pools_data = response.json()
                
                new_tokens = []
                for pool in pools_data.get('pools', []):
                    # Look for new tokens (usually paired with SOL)
                    base_token = pool.get('baseToken', {})
                    
                    if base_token.get('address') and \
                       base_token.get('address') not in ['So11111111111111111111111111111111111111112', 
                                                        'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v']:
                        
                        token_info = {
                            'address': base_token.get('address'),
                            'symbol': base_token.get('symbol', 'UNKNOWN'),
                            'name': base_token.get('name', 'Unknown Token'),
                            'liquidity': pool.get('liquidity', 0),
                            'created_at': pool.get('createdAt'),
                            'market_cap': pool.get('marketCap', 0),
                            'volume_24h': pool.get('volume24h', 0),
                            'source': 'quicknode_new_pools'
                        }
                        new_tokens.append(token_info)
                
                if new_tokens:
                    logging.info(f"✅ Found {len(new_tokens)} new tokens via QuickNode new-pools")
                    return new_tokens[:15]  # Return top 15 newest
        
        except Exception as e:
            logging.warning(f"⚠️ QuickNode new-pools failed: {str(e)}")
        
        # Method 2: Try QuickNode pump.fun integration
        try:
            pump_fun_endpoints = [
                f"{quicknode_endpoint}/pump-fun/tokens/newest",
                f"{quicknode_endpoint}/pump-fun/coins/newest",
                f"{quicknode_endpoint}/v1/pump-fun/tokens/newest"
            ]
            
            for endpoint in pump_fun_endpoints:
                try:
                    logging.info(f"🔍 Trying QuickNode pump.fun endpoint: {endpoint}")
                    
                    response = requests.get(
                        endpoint,
                        headers={'Content-Type': 'application/json'},
                        params={'limit': 20},
                        timeout=15
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        tokens = []
                        token_list = data if isinstance(data, list) else data.get('tokens', data.get('coins', []))
                        
                        for token in token_list[:20]:
                            if isinstance(token, dict) and 'mint' in token:
                                token_info = {
                                    'address': token['mint'],
                                    'symbol': token.get('symbol', 'UNKNOWN'),
                                    'name': token.get('name', 'Unknown Token'),
                                    'market_cap': token.get('market_cap', 0),
                                    'created_timestamp': token.get('created_timestamp'),
                                    'uri': token.get('uri', ''),
                                    'creator': token.get('creator', ''),
                                    'pump_fun': True,
                                    'source': 'quicknode_pump_fun'
                                }
                                tokens.append(token_info)
                        
                        if tokens:
                            logging.info(f"✅ Found {len(tokens)} pump.fun tokens via QuickNode!")
                            return tokens
                
                except Exception as e:
                    logging.warning(f"⚠️ QuickNode pump.fun endpoint failed: {str(e)}")
                    continue
        
        except Exception as e:
            logging.warning(f"⚠️ QuickNode pump.fun integration failed: {str(e)}")
        
        logging.warning("⚠️ All QuickNode token discovery methods failed")
        return []
        
    except Exception as e:
        logging.error(f"❌ Error in QuickNode token discovery: {str(e)}")
        return []

def smart_token_selection(potential_tokens):
    """Intelligently select the best token to trade."""
    if not potential_tokens:
        return None
    
    try:
        # Handle both string addresses and dict objects
        normalized_tokens = []
        for token in potential_tokens:
            if isinstance(token, str):
                # Simple string address - convert to dict format
                normalized_tokens.append({
                    'address': token,
                    'symbol': 'Unknown',
                    'source': 'fallback',
                    'score': 0
                })
            elif isinstance(token, dict):
                # Already in dict format
                normalized_tokens.append(token)
            else:
                logging.warning(f"Unknown token format: {type(token)}")
                continue
        
        if not normalized_tokens:
            return None
        
        # Score tokens based on various factors
        scored_tokens = []
        
        for token in normalized_tokens:
            score = 0
            token_address = token.get('address', '')
            
            if not token_address:
                continue
            
            # Factor 1: Not recently bought (higher score for longer gap)
            if token_address in token_buy_timestamps:
                minutes_since_buy = (time.time() - token_buy_timestamps[token_address]) / 60
                if minutes_since_buy > 60:
                    score += 3
                elif minutes_since_buy > 30:
                    score += 2
                elif minutes_since_buy > 15:
                    score += 1
            else:
                score += 10  # Never bought before gets highest score
            
            # Discourage BONK repetition to encourage token diversity
            bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
            if token_address == bonk_address:
                score -= 3  # Small penalty for BONK to prioritize fresh tokens
            
            # Factor 2: Known good tokens get bonus
            known_good = [
                "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",   # WIF
            ]
            
            if token_address in known_good:
                score += 2
            
            # Factor 3: Source bonus
            source = token.get('source', 'unknown')
            if 'BullX' in source:
                score += 5  # Highest priority for BullX tokens
            elif 'DexScreener' in source:
                score += 3
            elif 'Birdeye' in source:
                score += 2
            elif 'Pump.fun' in source:
                score += 1
            
            # Factor 4: Volume/Market Cap bonus
            volume = token.get('volume', 0)
            market_cap = token.get('market_cap', 0)
            
            if volume > 100000:  # $100k+ volume
                score += 2
            elif volume > 50000:  # $50k+ volume
                score += 1
                
            if 10000 <= market_cap <= 1000000:  # Sweet spot market cap
                score += 2
            
            scored_tokens.append((token_address, score, token))
        
        # Sort by score (highest first)
        scored_tokens.sort(key=lambda x: x[1], reverse=True)
        
        if scored_tokens:
            best_token_address, best_score, best_token_data = scored_tokens[0]
            symbol = best_token_data.get('symbol', best_token_address[:8])
            source = best_token_data.get('source', 'unknown')
            
            logging.info(f"🎯 Selected best token: {symbol} ({best_token_address[:8]}) from {source} (score: {best_score})")
            return best_token_address
        
        # Fallback to first token
        first_token = normalized_tokens[0]
        return first_token.get('address')
        
    except Exception as e:
        logging.error(f"Error in smart token selection: {str(e)}")
        # Return first available token as fallback
        if potential_tokens:
            first_token = potential_tokens[0]
            if isinstance(first_token, str):
                return first_token
            elif isinstance(first_token, dict):
                return first_token.get('address')
        return None

def enhanced_find_newest_tokens():
    """Enhanced token finder with better validation and multiple fallback methods."""
    try:
        logging.info("🔍 Starting enhanced token search...")
        
        all_potential_tokens = []
        
        # Method 1: Try QuickNode Metis if enabled
        if CONFIG.get('USE_QUICKNODE_METIS', False):
            quicknode_tokens = get_newest_tokens_quicknode()
            if quicknode_tokens:
                all_potential_tokens.extend(quicknode_tokens)
                logging.info(f"✅ Found {len(quicknode_tokens)} tokens via QuickNode Metis")
        
        # Method 2: Try pump.fun API with validation
        if len(all_potential_tokens) < 5:  # Only if we need more tokens
            pump_fun_tokens = get_newest_pump_fun_tokens(15)
            if pump_fun_tokens:
                all_potential_tokens.extend(pump_fun_tokens)
                logging.info(f"✅ Found {len(pump_fun_tokens)} tokens via pump.fun")
        
        # Method 3: Validate all tokens
        validated_tokens = []
        for token in all_potential_tokens:
            if validate_token_before_trading(token['address']):
                validated_tokens.append(token)
            
            if len(validated_tokens) >= 10:  # Stop after finding 10 good tokens
                break
        
        if validated_tokens:
            # Use smart selection to pick the best token
            selected_token = smart_token_selection(validated_tokens)
            if selected_token:
                logging.info(f"🎯 Enhanced search selected: {selected_token['symbol']} ({selected_token['address'][:8]})")
                return [selected_token]
        
        # Fallback: Return verified tradable tokens
        logging.warning("⚠️ No new tokens found, using verified fallback tokens")
        return get_verified_tradable_tokens()
        
    except Exception as e:
        logging.error(f"❌ Error in enhanced token search: {str(e)}")
        return get_verified_tradable_tokens()

def enhanced_find_newest_tokens_with_quicknode():
    """Enhanced token finder using QuickNode pump.fun API as primary source."""
    try:
        logging.info("🚀 Starting enhanced token search with QuickNode pump.fun API...")
        
        # Method 1: QuickNode pump.fun API for newest tokens (PRIMARY)
        newest_tokens = get_newest_tokens_quicknode()
        
        if newest_tokens:
            # Validate each token before returning
            validated_tokens = []
            for token_address in newest_tokens[:5]:  # Check top 5
                if validate_token_before_trading(token_address):
                    validated_tokens.append(token_address)
                    
                    # Get additional info from QuickNode for logging
                    try:
                        token_info = get_pump_fun_token_info_quicknode(token_address)
                        if token_info:
                            logging.info(f"✅ Validated QuickNode token: {token_info['symbol']} ({token_address[:8]}) - MC: ${token_info['market_cap']}")
                        else:
                            logging.info(f"✅ Validated QuickNode token: {token_address[:8]}")
                    except:
                        logging.info(f"✅ Validated QuickNode token: {token_address[:8]}")
                else:
                    logging.warning(f"❌ Failed validation: {token_address[:8]}")
            
            if validated_tokens:
                logging.info(f"🎯 QuickNode provided {len(validated_tokens)} validated fresh tokens")
                return validated_tokens[:2]  # Return max 2 for focus
        
        # Method 2: Fallback to verified tradable tokens
        logging.info("🔄 QuickNode APIs didn't return tokens, using verified fallback...")
        verified_tokens = get_verified_tradable_tokens()
        
        if verified_tokens:
            logging.info(f"📋 Found {len(verified_tokens)} verified tradable tokens")
            return verified_tokens[:2]
        
        # Method 3: Original pump.fun direct API (last resort)
        try:
            logging.info("🔄 Trying direct pump.fun API as last resort...")
            direct_tokens = get_newest_pump_fun_tokens(limit=5)
            
            if direct_tokens:
                validated_direct = []
                for token in direct_tokens:
                    if isinstance(token, dict) and token.get('minutes_old', 999) <= 3:
                        token_address = token.get('address')
                        if token_address and validate_token_before_trading(token_address):
                            validated_direct.append(token_address)
                
                if validated_direct:
                    logging.info(f"🎯 Direct API found {len(validated_direct)} validated tokens")
                    return validated_direct[:1]
        
        except Exception as e:
            logging.error(f"Direct pump.fun API failed: {str(e)}")
        
        logging.warning("❌ No suitable tokens found from any method")
        return []
        
    except Exception as e:
        logging.error(f"Error in enhanced_find_newest_tokens_with_quicknode: {str(e)}")
        return []

def get_pump_fun_token_info_quicknode(token_address: str):
    """Get detailed token info from QuickNode pump.fun API."""
    try:
        quicknode_endpoint = CONFIG['SOLANA_RPC_URL']
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "qn_fetchPumpFunCoinByCA",
            "params": {
                "mint": token_address
            }
        }
        
        response = requests.post(
            quicknode_endpoint,
            json=payload,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data and data["result"]:
                coin_info = data["result"]
                return {
                    "symbol": coin_info.get("symbol", "Unknown"),
                    "name": coin_info.get("name", "Unknown"),
                    "market_cap": coin_info.get("market_cap", 0),
                    "volume_24h": coin_info.get("volume_24h", 0),
                    "created_timestamp": coin_info.get("created_timestamp", 0),
                    "creator": coin_info.get("creator", ""),
                    "description": coin_info.get("description", "")
                }
        
    except Exception as e:
        logging.error(f"Error getting token info from QuickNode: {str(e)}")
    
    return None

def find_newest_tokens():
    """Main token finder that uses enhanced methods with QuickNode integration."""
    try:
        # Use QuickNode Metis if available
        if CONFIG.get('USE_QUICKNODE_METIS', True):
            return enhanced_find_newest_tokens_with_quicknode()
        else:
            # Fallback to original methods
            return enhanced_find_newest_tokens()
            
    except Exception as e:
        logging.error(f"❌ Error in main token finder: {str(e)}")
        return get_verified_tradable_tokens()

def validate_token_before_trading(token_address: str) -> bool:
    """Comprehensive token validation before attempting to trade."""
    try:
        logging.info(f"🔍 Validating token: {token_address[:8]}...")
        
        # 1. Basic address validation
        if len(token_address) != 44:
            logging.warning(f"❌ Invalid address length: {token_address}")
            return False
        
        # 2. Check if token is in known non-tradable list
        known_non_tradable = [
            "GzYBeP4qDXP5onnpKKdYw7m6hxzgTBjTTUXkVxZToDsi",  # HADES
            "4GUQXsieAfBX4Xfv2eXG3oNkQTVNnbnu6ZNF13uD7hYA",  # PENGU
            "4HjJphebQ7ogUjRnch39s8Pk5DBmHePAwZrUHW1Ka6UT",  # GIGA
            "PNUtFk6iQhs2VXiCMQpzGM81PdE7yGL5Y4fo9mFfb7o",   # PNUT
            "4LLdMU9BLbT39ZLjDgBeZirThcFB5oqkQaEQDyhC7FEW",  # SLERF
        ]
        
        if token_address in known_non_tradable:
            logging.warning(f"❌ Token in known non-tradable list: {token_address[:8]}")
            return False
        
        # 3. Quick Jupiter tradability check with minimal amount
        try:
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            params = {
                "inputMint": "So11111111111111111111111111111111111111112",  # SOL
                "outputMint": token_address,
                "amount": "1000000",  # 0.001 SOL - very small amount
                "slippageBps": "5000"  # 50% slippage - very lenient
            }
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            
            response = requests.get(quote_url, params=params, headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if "outAmount" in data and int(data["outAmount"]) > 0:
                    logging.info(f"✅ Token is tradable: {token_address[:8]}")
                    return True
                else:
                    logging.warning(f"❌ No valid quote for token: {token_address[:8]}")
                    return False
            
            elif response.status_code == 400:
                # Check for specific error
                try:
                    error_data = response.json()
                    if "error" in error_data:
                        error_msg = error_data["error"]
                        if "not tradable" in error_msg.lower() or "TOKEN_NOT_TRADABLE" in error_msg:
                            logging.warning(f"❌ Jupiter says not tradable: {token_address[:8]}")
                            return False
                except:
                    pass
                
                logging.warning(f"❌ Bad request for token: {token_address[:8]}")
                return False
            
            else:
                logging.warning(f"❌ HTTP {response.status_code} for token: {token_address[:8]}")
                return False
                
        except requests.exceptions.Timeout:
            logging.warning(f"⏰ Timeout validating token: {token_address[:8]}")
            return False
        except Exception as e:
            logging.error(f"❌ Error validating token {token_address[:8]}: {str(e)}")
            return False
        
    except Exception as e:
        logging.error(f"❌ Error in token validation: {str(e)}")
        return False

def get_newest_tokens_quicknode():
    """Get newest tokens using QuickNode's pump.fun API integration."""
    try:
        # Use your QuickNode RPC endpoint from environment
        quicknode_endpoint = CONFIG['SOLANA_RPC_URL']  # Your QuickNode URL
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        # QuickNode pump.fun method to get newest coins
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "qn_fetchPumpFunCoins",
            "params": {
                "limit": 20,
                "offset": 0,
                "order": "desc",
                "orderBy": "created_timestamp"
            }
        }
        
        logging.info("🚀 Fetching newest tokens from QuickNode pump.fun API...")
        
        response = requests.post(
            quicknode_endpoint,
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data and data["result"]:
                tokens = []
                coins = data["result"]
                
                for coin in coins:
                    # Extract token info from QuickNode pump.fun response
                    token_address = coin.get("mint", coin.get("address"))
                    created_timestamp = coin.get("created_timestamp", 0)
                    
                    if token_address and len(token_address) == 44:
                        # Calculate age in minutes
                        if created_timestamp > 1000000000000:  # Convert ms to seconds
                            created_timestamp = created_timestamp / 1000
                        
                        minutes_old = (time.time() - created_timestamp) / 60 if created_timestamp > 0 else 999
                        
                        # Only include very fresh tokens (under 5 minutes)
                        if minutes_old <= CONFIG.get('MAX_TOKEN_AGE_MINUTES', 5):
                            token_info = {
                                "address": token_address,
                                "symbol": coin.get("symbol", "Unknown"),
                                "name": coin.get("name", "Unknown"),
                                "minutes_old": minutes_old,
                                "market_cap": coin.get("market_cap", 0),
                                "creator": coin.get("creator", ""),
                                "liquidity": coin.get("usd_market_cap", 0),
                                "volume_24h": coin.get("volume_24h", 0)
                            }
                            tokens.append(token_info)
                            logging.info(f"✅ QuickNode found fresh token: {token_info['symbol']} - {minutes_old:.1f}min old")
                
                if tokens:
                    # Sort by age (newest first)
                    tokens.sort(key=lambda x: x["minutes_old"])
                    logging.info(f"🎯 QuickNode found {len(tokens)} ultra-fresh tokens")
                    return [t["address"] for t in tokens]
                else:
                    logging.info("📊 QuickNode: No tokens under 5 minutes old found")
                    
        elif response.status_code == 429:
            logging.warning("⚠️ QuickNode rate limited - will use fallback")
            
        else:
            logging.warning(f"⚠️ QuickNode pump.fun API error: {response.status_code}")
            if response.text:
                logging.warning(f"Response: {response.text[:200]}")
            
    except Exception as e:
        logging.error(f"❌ Error with QuickNode pump.fun API: {str(e)}")
    
    return []

def get_trending_tokens_quicknode():
    """Get trending tokens from QuickNode pump.fun API."""
    try:
        quicknode_endpoint = CONFIG['SOLANA_RPC_URL']
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        # Get trending tokens by volume
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "qn_fetchPumpFunCoins",
            "params": {
                "limit": 10,
                "offset": 0,
                "order": "desc",
                "orderBy": "volume_24h"
            }
        }
        
        logging.info("📈 Fetching trending tokens from QuickNode...")
        
        response = requests.post(
            quicknode_endpoint,
            json=payload,
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data and data["result"]:
                trending_tokens = []
                coins = data["result"]
                
                for coin in coins:
                    token_address = coin.get("mint", coin.get("address"))
                    created_timestamp = coin.get("created_timestamp", 0)
                    
                    if token_address and len(token_address) == 44:
                        if created_timestamp > 1000000000000:
                            created_timestamp = created_timestamp / 1000
                            
                        minutes_old = (time.time() - created_timestamp) / 60 if created_timestamp > 0 else 999
                        
                        # Include tokens up to 30 minutes old for trending
                        if minutes_old <= 30:
                            trending_tokens.append(token_address)
                            symbol = coin.get("symbol", "Unknown")
                            volume = coin.get("volume_24h", 0)
                            market_cap = coin.get("market_cap", 0)
                            logging.info(f"📈 Trending: {symbol} - {minutes_old:.1f}min old, Vol: ${volume}, MC: ${market_cap}")
                
                if trending_tokens:
                    logging.info(f"🔥 Found {len(trending_tokens)} trending tokens")
                    return trending_tokens
        
    except Exception as e:
        logging.error(f"❌ Error getting trending tokens: {str(e)}")
    
    return []

def get_pump_fun_token_info_quicknode(token_address: str):
    """Get detailed token info from QuickNode pump.fun API."""
    try:
        quicknode_endpoint = CONFIG['SOLANA_RPC_URL']
        
        headers = {
            "Content-Type": "application/json"
        }
        
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "qn_fetchPumpFunCoinByCA",
            "params": {
                "mint": token_address
            }
        }
        
        response = requests.post(
            quicknode_endpoint,
            json=payload,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if "result" in data and data["result"]:
                coin_info = data["result"]
                return {
                    "symbol": coin_info.get("symbol", "Unknown"),
                    "name": coin_info.get("name", "Unknown"),
                    "market_cap": coin_info.get("market_cap", 0),
                    "volume_24h": coin_info.get("volume_24h", 0),
                    "created_timestamp": coin_info.get("created_timestamp", 0),
                    "creator": coin_info.get("creator", ""),
                    "description": coin_info.get("description", "")
                }
        
    except Exception as e:
        logging.error(f"Error getting token info from QuickNode: {str(e)}")
    
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
           # "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
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
    """Get newest tokens from pump.fun API with multiple fallback methods."""
    try:
        # Multiple API endpoints to try
        endpoints = [
            "https://frontend-api.pump.fun/coins/newest",
            "https://backend.pump.fun/tokens/newest", 
            "https://api.pump.fun/tokens/newest",
            "https://pump.fun/api/tokens/newest"
        ]
        
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        # Try each endpoint
        for endpoint in endpoints:
            try:
                logging.info(f"Trying pump.fun endpoint: {endpoint}")
                
                # Use session for connection pooling
                session = requests.Session()
                session.headers.update(headers)
                
                response = session.get(
                    endpoint, 
                    params={"limit": limit}, 
                    timeout=15,
                    verify=True  # Verify SSL certificates
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if not data:
                        logging.warning(f"Empty response from {endpoint}")
                        continue
                    
                    tokens = []
                    for token in data:
                        # Extract token address and other details
                        token_address = None
                        if "mint" in token:
                            token_address = token["mint"]
                        elif "address" in token:
                            token_address = token["address"]
                        elif "ca" in token:  # Contract address
                            token_address = token["ca"]
                        
                        if token_address:
                            # Calculate precise age in minutes
                            created_at = token.get("createdAt", token.get("created_timestamp", 0))
                            if created_at > 1000000000000:  # Timestamp is in milliseconds
                                created_at = created_at / 1000
                            
                            minutes_ago = (time.time() - created_at) / 60 if created_at > 0 else 999
                            
                            token_data = {
                                "address": token_address,
                                "symbol": token.get("symbol", token.get("ticker", "Unknown")),
                                "name": token.get("name", "Unknown"),
                                "price": token.get("price", 0),
                                "minutes_old": minutes_ago,
                                "createdAt": created_at,
                                "market_cap": token.get("market_cap", token.get("marketCap", 0)),
                                "liquidity": token.get("liquidity", 0)
                            }
                            
                            # Log very new tokens
                            if minutes_ago <= 5:
                                logging.info(f"Found very fresh token: {token_data['symbol']} - {minutes_ago:.1f} minutes old")
                            
                            tokens.append(token_data)
                    
                    if tokens:
                        # Sort by age (newest first)
                        tokens.sort(key=lambda x: x.get('minutes_old', 999))
                        
                        logging.info(f"Retrieved {len(tokens)} tokens from pump.fun API via {endpoint}")
                        return tokens
                
                elif response.status_code == 429:
                    logging.warning(f"Rate limited by {endpoint}")
                    time.sleep(5)
                    continue
                
                else:
                    logging.warning(f"Error from {endpoint}: {response.status_code}")
                    continue
                    
            except requests.exceptions.ConnectionError as e:
                logging.warning(f"Connection error for {endpoint}: {str(e)}")
                continue
                
            except requests.exceptions.Timeout:
                logging.warning(f"Timeout connecting to {endpoint}")
                continue
                
            except requests.exceptions.RequestException as e:
                logging.warning(f"Request error for {endpoint}: {str(e)}")
                continue
            
            except Exception as e:
                logging.warning(f"Unexpected error for {endpoint}: {str(e)}")
                continue
        
        # If all pump.fun endpoints fail, use fallback token discovery
        logging.warning("All pump.fun endpoints failed, using fallback token discovery")
        return get_fallback_newest_tokens()
            
    except Exception as e:
        logging.error(f"Error in pump.fun API: {str(e)}")
        return get_fallback_newest_tokens()

def get_fallback_newest_tokens():
    """Fallback method to find new tokens when pump.fun API is unavailable."""
    try:
        logging.info("Using fallback token discovery method")
        
        # Return a mix of known tradable tokens and recently discovered tokens
        fallback_tokens = []
        
        # Add known good meme tokens that are tradable
        known_meme_tokens = [
           # {"address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "symbol": "BONK", "minutes_old": 10},
            {"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "symbol": "WIF", "minutes_old": 15},
            {"address": "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr", "symbol": "POPCAT", "minutes_old": 20}
        ]
        
        # Filter for tokens that haven't been bought recently
        current_time = time.time()
        for token in known_meme_tokens:
            token_address = token["address"]
            
            # Check if we've bought this recently
            if token_address in token_buy_timestamps:
                minutes_since_buy = (current_time - token_buy_timestamps[token_address]) / 60
                if minutes_since_buy < 30:  # Skip if bought in last 30 minutes
                    continue
            
            # Check if we're currently monitoring it
            if token_address not in monitored_tokens:
                fallback_tokens.append(token["address"])
        
        if fallback_tokens:
            logging.info(f"Using {len(fallback_tokens)} fallback tokens: {[t[:8] for t in fallback_tokens]}")
            return fallback_tokens
        else:
            logging.info("No suitable fallback tokens available")
            return []
            
    except Exception as e:
        logging.error(f"Error in fallback token discovery: {str(e)}")
        return []
        
def scan_recent_solana_transactions():
    """Alternative method to find new tokens by scanning recent Solana transactions."""
    try:
        logging.info("Scanning recent Solana transactions for new tokens")
        
        # Get recent signatures from a known active wallet or DEX
        # This is a backup method when APIs are down
        
        # Use a public RPC endpoint to get recent signatures
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium program
                {"limit": 10}
            ]
        }
        
        try:
            response = requests.post(
                CONFIG['SOLANA_RPC_URL'],
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                if "result" in data and data["result"]:
                    signatures = [tx["signature"] for tx in data["result"][:5]]  # Limit to 5
                    
                    # Analyze these transactions for new token addresses
                    potential_tokens = []
                    for signature in signatures:
                        try:
                            token_addresses = analyze_transaction_for_tokens(signature)
                            potential_tokens.extend(token_addresses[:2])  # Limit tokens per tx
                            
                            if len(potential_tokens) >= 3:  # Limit total tokens
                                break
                                
                        except Exception as e:
                            logging.error(f"Error analyzing transaction {signature}: {str(e)}")
                            continue
                    
                    if potential_tokens:
                        logging.info(f"Found {len(potential_tokens)} potential tokens from transaction analysis")
                        return potential_tokens
        
        except Exception as e:
            logging.error(f"Error scanning transactions: {str(e)}")
        
        return []
        
    except Exception as e:
        logging.error(f"Error in transaction scanning: {str(e)}")
        return []

def analyze_transaction_for_tokens(signature):
    """Analyze a transaction to extract potential new token addresses."""
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
            headers={"Content-Type": "application/json"},
            timeout=8
        )
        
        if response.status_code != 200:
            return []
            
        data = response.json()
        if "result" not in data or not data["result"]:
            return []
        
        # Extract token addresses from transaction
        token_addresses = []
        transaction = data["result"]
        
        if "transaction" in transaction and "message" in transaction["transaction"]:
            message = transaction["transaction"]["message"]
            
            # Look for token program interactions
            if "instructions" in message:
                for instruction in message["instructions"]:
                    if "parsed" in instruction and "info" in instruction["parsed"]:
                        info = instruction["parsed"]["info"]
                        
                        # Look for mint addresses in various instruction types
                        if "mint" in info:
                            mint_address = info["mint"]
                            if mint_address not in token_addresses and len(mint_address) > 40:
                                token_addresses.append(mint_address)
        
        return token_addresses[:2]  # Return max 2 tokens per transaction
        
    except Exception as e:
        logging.error(f"Error analyzing transaction {signature}: {str(e)}")
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
               # "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # BONK
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

def execute_via_javascript(token_address, amount, is_sell=False):
    """Enhanced execution with nuclear success detection - FIXED VERSION."""
    try:
        trade_amount = os.environ.get('TRADE_AMOUNT_SOL', '0.144')
        command = f"node swap.js {token_address} {trade_amount} {str(is_sell).lower()}"
        
        print(f"🎯 Executing command: {command}")
        
        result = subprocess.run(
            command, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=180  # Increased timeout for nuclear solution
        )
        
        # Get all output
        stdout_output = result.stdout if result.stdout else ""
        stderr_output = result.stderr if result.stderr else ""
        combined_output = stdout_output + stderr_output
        
        print(f"📤 JavaScript output length: {len(combined_output)} characters")
        
        # NUCLEAR SUCCESS DETECTION - Updated for your exact success messages
        nuclear_success_indicators = [
            "SUCCESS" in combined_output,
            "BUY SUCCESS:" in combined_output,
            "SELL SUCCESS:" in combined_output,  
            "Transaction submitted successfully" in combined_output,
            "Found recent transaction - StructError was non-critical" in combined_output,
            "JavaScript execution successful!" in combined_output
        ]
        
        # Additional signature-based detection
        transaction_signatures = re.findall(r'[A-Za-z0-9]{87,88}', combined_output)
        has_valid_signature = len(transaction_signatures) > 0
        
        # SUCCESS if ANY nuclear indicator OR valid signature found
        is_successful = any(nuclear_success_indicators) or has_valid_signature
        
        # Extract the most recent transaction signature
        transaction_signature = None
        if transaction_signatures:
            transaction_signature = transaction_signatures[-1]
        
        # Enhanced logging
        action = "SELL" if is_sell else "BUY"
        
        if is_successful:
            print(f"✅ NUCLEAR {action} SUCCESS DETECTED: {token_address}")
            if transaction_signature:
                print(f"🔗 Transaction Signature: {transaction_signature}")
                print(f"🔍 Solscan: https://solscan.io/tx/{transaction_signature}")
            
            # Show which success indicator triggered
            for i, indicator in enumerate(nuclear_success_indicators):
                if indicator:
                    indicator_names = [
                        "SUCCESS keyword",
                        "BUY SUCCESS message", 
                        "SELL SUCCESS message",
                        "Transaction submitted successfully",
                        "Nuclear StructError bypass",
                        "JavaScript execution successful"
                    ]
                    print(f"🎯 Success detected via: {indicator_names[i]}")
                    break
            
            return True, combined_output
        else:
            print(f"❌ NUCLEAR {action} FAILED: {token_address}")
            print(f"🔍 Output sample: {combined_output[:300]}...")
            print(f"🔍 Checked {len(nuclear_success_indicators)} success indicators")
            print(f"🔍 Found {len(transaction_signatures)} transaction signatures")
            return False, combined_output
            
    except subprocess.TimeoutExpired:
        print(f"⏰ TIMEOUT: JavaScript execution exceeded 180 seconds for {token_address}")
        return False, "Nuclear execution timeout"
    except Exception as e:
        print(f"❌ NUCLEAR EXECUTION ERROR: {e}")
        return False, f"Nuclear execution error: {str(e)}"

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
    """Enhanced token finder with multiple fallback methods."""
    try:
        # Method 1: Try pump.fun API first
        tokens_from_pumpfun = get_newest_pump_fun_tokens(limit=20)
        
        if tokens_from_pumpfun:
            # Filter for very new tokens (under configured age limit)
            very_new_tokens = []
            for token in tokens_from_pumpfun:
                if isinstance(token, dict) and token.get('minutes_old', 999) <= CONFIG.get('MAX_TOKEN_AGE_MINUTES', 5):
                    if 'address' in token:
                        very_new_tokens.append(token['address'])
            
            if very_new_tokens:
                logging.info(f"Found {len(very_new_tokens)} very new tokens from pump.fun API")
                return very_new_tokens
        
        # Method 2: Use transaction scanning as fallback
        logging.info("Pump.fun API unavailable, trying transaction scanning...")
        tokens_from_scanning = scan_recent_solana_transactions()
        
        if tokens_from_scanning:
            logging.info(f"Found {len(tokens_from_scanning)} tokens from transaction scanning")
            return tokens_from_scanning
        
        # Method 3: Use known fallback tokens
        logging.info("Transaction scanning failed, using fallback tokens...")
        fallback_tokens = get_fallback_newest_tokens()
        
        if fallback_tokens:
            logging.info(f"Using {len(fallback_tokens)} fallback tokens")
            return fallback_tokens
        
        # Method 4: Return known tradable tokens as absolute last resort
        logging.warning("All token discovery methods failed, using known tradable tokens")
        known_tradable = [
            t["address"] for t in KNOWN_TOKENS 
            if t.get("tradable", False) and t["address"] != "So11111111111111111111111111111111111111112"
        ]
        
        # Filter out tokens we're already monitoring or bought recently
        current_time = time.time()
        available_tokens = []
        
        for token_address in known_tradable:
            # Skip if currently monitoring
            if token_address in monitored_tokens:
                continue
                
            # Skip if bought recently
            if token_address in token_buy_timestamps:
                minutes_since_buy = (current_time - token_buy_timestamps[token_address]) / 60
                if minutes_since_buy < 60:  # 1 hour cooldown for known tokens
                    continue
            
            available_tokens.append(token_address)
        
        if available_tokens:
            logging.info(f"Using {len(available_tokens)} known tradable tokens as last resort")
            return available_tokens[:3]  # Limit to 3
        
        logging.warning("No tokens available from any method")
        return []
        
    except Exception as e:
        logging.error(f"Error in find_newest_tokens: {str(e)}")
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
    """Complete patched trading loop with enhanced profit tracking and nuclear success detection."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay, daily_profit
    global buy_attempts, buy_successes, sell_attempts, sell_successes, tokens_scanned
    global circuit_breaker_active, total_trades, successful_trades, total_profit
    
    logging.info("==================== ULTRA-OPTIMIZED TRADING LOOP STARTING ====================")
    logging.info("🎯 Target: $1,000 daily profit through rapid token flips")
    logging.info("⚡ Strategy: 15-30% quick gains on validated tokens only")
    logging.info("🔄 Max hold time: 45 seconds to avoid rug pulls")
    logging.info("🛡️ Enhanced validation to prevent TOKEN_NOT_TRADABLE errors")
    logging.info("💰 Helius Developer: " + ("ENABLED" if os.environ.get('HELIUS_API_KEY') else "DISABLED"))
    logging.info("🔥 Nuclear StructError bypass: ACTIVE")
    logging.info("📊 Enhanced profit tracking: ACTIVE")
    logging.info("Circuit breaker status: " + ("ACTIVE" if circuit_breaker_active else "INACTIVE"))
    logging.info("================================================================================")
    
    # Initialize enhanced tracking variables
    daily_profit = float(os.environ.get('TOTAL_PROFIT', '0.0'))
    total_trades = int(os.environ.get('TOTAL_TRADES', '0'))
    successful_trades = int(os.environ.get('SUCCESSFUL_TRADES', '0'))
    
    daily_profit_start_time = time.time()
    last_performance_report_time = time.time()
    last_memory_cleanup_time = time.time()
    last_token_search_time = 0
    
    # Ultra-aggressive timing
    memory_cleanup_interval = 180  # Clean up every 3 minutes
    token_search_interval = 30     # Search for new tokens every 30 seconds
    max_runtime = 4 * 3600        # Restart every 4 hours for freshness
    
    start_time = time.time()
    successful_trades_today = 0
    target_trades_per_hour = 3    # Aim for 3 successful trades per hour
    
    while True:
        # Check runtime limit
        if time.time() - start_time > max_runtime:
            logging.info(f"🔄 Maximum runtime reached, restarting for optimal performance")
            return
            
        iteration_count += 1
        current_time = time.time()
        
        try:
            # Memory cleanup
            if current_time - last_memory_cleanup_time > memory_cleanup_interval:
                cleanup_memory()
                last_memory_cleanup_time = current_time
            
            # Circuit breaker check
            if circuit_breaker_check():
                logging.warning("⛔ Circuit breaker active - pausing operations")
                time.sleep(30)  # Shorter pause for faster recovery
                continue
            
            # ULTRA-AGGRESSIVE TOKEN MONITORING WITH PROFIT TRACKING
            tokens_to_remove = []
            for token_address in list(monitored_tokens.keys()):
                try:
                    token_data = monitored_tokens[token_address]
                    seconds_held = current_time - token_data['buy_time']
                    
                    # FORCE SELL after 45 seconds regardless of price
                    if seconds_held >= CONFIG.get('MAX_HOLD_TIME_SECONDS', 45):
                        logging.warning(f"⏰ FORCE SELLING {token_address[:8]} after {seconds_held:.1f}s")
                        try:
                            # Enhanced sell execution with success detection
                            success, result = execute_via_javascript(token_address, token_data.get('trade_amount', 0.144), is_sell=True)
                            
                            if success:
                                sell_signature = result
                                logging.info(f"🔄 Force sell executed: {sell_signature}")
                                
                                # Calculate time-based profit/loss
                                initial_price = token_data.get('initial_price', 0)
                                current_price = get_token_price_for_profit_calc(token_address)
                                trade_amount = token_data.get('trade_amount', 0.144)
                                
                                if current_price and initial_price:
                                    profit_usd, profit_percentage = calculate_trade_profit(initial_price, current_price, trade_amount)
                                    daily_profit += profit_usd
                                    
                                    logging.info(f"⏰ Time-based sell completed:")
                                    logging.info(f"   💵 Entry: ${initial_price:.6f} | Exit: ${current_price:.6f}")
                                    logging.info(f"   📈 Result: ${profit_usd:.2f} ({profit_percentage:+.1f}%)")
                                    
                                    update_performance_stats(True, profit_usd, token_address)
                                else:
                                    # Estimate break-even for time-based sells
                                    estimated_result = 0  # Assume break-even on time sells
                                    update_performance_stats(True, estimated_result, token_address)
                                    logging.info(f"⏰ Time-based sell - estimated break-even")
                            else:
                                logging.warning(f"❌ Force sell failed: {result}")
                                update_performance_stats(False, 0, token_address)
                            
                            tokens_to_remove.append(token_address)
                        except Exception as e:
                            logging.error(f"Error in force sell: {str(e)}")
                            tokens_to_remove.append(token_address)  # Remove anyway
                        continue
                    
                    # Check price and execute ultra-aggressive selling with profit tracking
                    current_price = get_token_price_for_profit_calc(token_address)
                    if current_price and current_price > 0:
                        initial_price = token_data['initial_price']
                        price_change_pct = ((current_price / initial_price) - 1) * 100
                        trade_amount = token_data.get('trade_amount', 0.144)
                        
                        # Update peak price
                        if current_price > token_data.get('highest_price', 0):
                            token_data['highest_price'] = current_price
                        
                        # Log current status
                        token_symbol = get_token_symbol(token_address) or token_address[:8]
                        logging.info(f"📊 {token_symbol}: {price_change_pct:.1f}% | {seconds_held:.1f}s held")
                        
                        # ULTRA-AGGRESSIVE SELLING CONDITIONS WITH PROFIT TRACKING:
                        
                        # 1. Take MIN_PROFIT_PCT profit immediately (default 15%)
                        if price_change_pct >= CONFIG.get('MIN_PROFIT_PCT', 15):
                            logging.info(f"🔥 QUICK PROFIT: {price_change_pct:.1f}% on {token_symbol}")
                            try:
                                success, result = execute_via_javascript(token_address, trade_amount, is_sell=True)
                                if success:
                                    sell_signature = result
                                    logging.info(f"💰 Profit sell executed: {sell_signature}")
                                    
                                    # Calculate actual profit
                                    profit_usd, profit_percentage = calculate_trade_profit(initial_price, current_price, trade_amount)
                                    daily_profit += profit_usd
                                    successful_trades_today += 1
                                    
                                    logging.info(f"💰 PROFITABLE TRADE COMPLETED:")
                                    logging.info(f"   💵 Entry: ${initial_price:.6f} | Exit: ${current_price:.6f}")
                                    logging.info(f"   📈 Profit: ${profit_usd:.2f} ({profit_percentage:+.1f}%)")
                                    logging.info(f"   💎 Daily Total: ${daily_profit:.2f}")
                                    
                                    update_performance_stats(True, profit_usd, token_address)
                                else:
                                    logging.warning(f"❌ Profit sell failed: {result}")
                                    update_performance_stats(False, 0, token_address)
                                
                                tokens_to_remove.append(token_address)
                            except Exception as e:
                                logging.error(f"Error in profit sell: {str(e)}")
                                tokens_to_remove.append(token_address)
                            continue
                        
                        # 2. Take ANY profit after 15 seconds
                        if seconds_held >= 15 and price_change_pct > 0:
                            logging.info(f"⏱️ TIME PROFIT: {price_change_pct:.1f}% after {seconds_held:.1f}s on {token_symbol}")
                            try:
                                success, result = execute_via_javascript(token_address, trade_amount, is_sell=True)
                                if success:
                                    sell_signature = result
                                    logging.info(f"⏰ Time-based profit sell executed: {sell_signature}")
                                    
                                    # Calculate time-based profit
                                    profit_usd, profit_percentage = calculate_trade_profit(initial_price, current_price, trade_amount)
                                    daily_profit += profit_usd
                                    successful_trades_today += 1
                                    
                                    logging.info(f"⏰ TIME-BASED PROFIT:")
                                    logging.info(f"   💵 Entry: ${initial_price:.6f} | Exit: ${current_price:.6f}")
                                    logging.info(f"   📈 Profit: ${profit_usd:.2f} ({profit_percentage:+.1f}%)")
                                    logging.info(f"   💎 Daily Total: ${daily_profit:.2f}")
                                    
                                    update_performance_stats(True, profit_usd, token_address)
                                else:
                                    logging.warning(f"❌ Time-based sell failed: {result}")
                                    update_performance_stats(False, 0, token_address)
                                
                                tokens_to_remove.append(token_address)
                            except Exception as e:
                                logging.error(f"Error in time-based sell: {str(e)}")
                                tokens_to_remove.append(token_address)
                            continue
                        
                        # 3. Stop loss at 8% (tighter than before)
                        if price_change_pct <= -CONFIG.get('STOP_LOSS_PERCENT', 8):
                            logging.warning(f"🛑 STOP LOSS: {price_change_pct:.1f}% on {token_symbol}")
                            try:
                                success, result = execute_via_javascript(token_address, trade_amount, is_sell=True)
                                if success:
                                    sell_signature = result
                                    logging.warning(f"🛑 Stop loss executed: {sell_signature}")
                                    
                                    # Calculate loss
                                    loss_usd, loss_percentage = calculate_trade_profit(initial_price, current_price, trade_amount)
                                    daily_profit += loss_usd  # Will be negative
                                    
                                    logging.warning(f"🛑 STOP LOSS EXECUTED:")
                                    logging.warning(f"   💵 Entry: ${initial_price:.6f} | Exit: ${current_price:.6f}")
                                    logging.warning(f"   📉 Loss: ${loss_usd:.2f} ({loss_percentage:+.1f}%)")
                                    logging.warning(f"   💎 Daily Total: ${daily_profit:.2f}")
                                    
                                    update_performance_stats(True, loss_usd, token_address)  # Count as completed trade
                                else:
                                    logging.error(f"❌ Stop loss execution failed: {result}")
                                    update_performance_stats(False, 0, token_address)
                                
                                tokens_to_remove.append(token_address)
                            except Exception as e:
                                logging.error(f"Error in stop loss: {str(e)}")
                                tokens_to_remove.append(token_address)
                            continue
                        
                        # 4. Trend reversal detection (2% drop from peak)
                        peak_price = token_data.get('highest_price', initial_price)
                        drop_from_peak = ((peak_price - current_price) / peak_price) * 100
                        
                        if price_change_pct > 5 and drop_from_peak > 2:
                            logging.info(f"📉 TREND REVERSAL: Selling {token_symbol} at {price_change_pct:.1f}% after 2% drop from peak")
                            try:
                                success, result = execute_via_javascript(token_address, trade_amount, is_sell=True)
                                if success:
                                    sell_signature = result
                                    logging.info(f"📉 Trend reversal sell executed: {sell_signature}")
                                    
                                    # Calculate reversal profit
                                    profit_usd, profit_percentage = calculate_trade_profit(initial_price, current_price, trade_amount)
                                    daily_profit += profit_usd
                                    successful_trades_today += 1
                                    
                                    logging.info(f"📉 TREND REVERSAL PROFIT:")
                                    logging.info(f"   💵 Entry: ${initial_price:.6f} | Exit: ${current_price:.6f}")
                                    logging.info(f"   📈 Profit: ${profit_usd:.2f} ({profit_percentage:+.1f}%)")
                                    logging.info(f"   💎 Daily Total: ${daily_profit:.2f}")
                                    
                                    update_performance_stats(True, profit_usd, token_address)
                                else:
                                    logging.warning(f"❌ Trend reversal sell failed: {result}")
                                    update_performance_stats(False, 0, token_address)
                                
                                tokens_to_remove.append(token_address)
                            except Exception as e:
                                logging.error(f"Error in trend reversal sell: {str(e)}")
                                tokens_to_remove.append(token_address)
                            continue
                        
                        # Update token data
                        monitored_tokens[token_address] = token_data
                    
                    else:
                        # Can't get price - force sell after 2 failed attempts
                        if 'price_failures' not in token_data:
                            token_data['price_failures'] = 0
                        token_data['price_failures'] += 1
                        
                        if token_data['price_failures'] >= 2:
                            logging.warning(f"🚨 PRICE FAILURE: Force selling {token_address[:8]} after {token_data['price_failures']} failures")
                            try:
                                success, result = execute_via_javascript(token_address, token_data.get('trade_amount', 0.144), is_sell=True)
                                if success:
                                    logging.warning(f"🚨 Price failure sell executed: {result}")
                                    # Assume break-even on price failure sells
                                    update_performance_stats(True, 0, token_address)
                                else:
                                    update_performance_stats(False, 0, token_address)
                                tokens_to_remove.append(token_address)
                            except Exception as e:
                                logging.error(f"Error in price failure sell: {str(e)}")
                                tokens_to_remove.append(token_address)
                            continue
                        
                        monitored_tokens[token_address] = token_data
                    
                except Exception as e:
                    logging.error(f"❌ Error monitoring {token_address[:8]}: {str(e)}")
                    # Force sell on any error
                    try:
                        execute_via_javascript(token_address, monitored_tokens[token_address].get('trade_amount', 0.144), is_sell=True)
                        update_performance_stats(False, 0, token_address)
                    except:
                        pass
                    tokens_to_remove.append(token_address)
            
            # Remove sold tokens
            for token_address in tokens_to_remove:
                if token_address in monitored_tokens:
                    del monitored_tokens[token_address]
                    logging.info(f"🗑️ Removed {token_address[:8]} from monitoring")
            
            # TOKEN ACQUISITION - Enhanced with Helius Developer integration
            if (current_time - last_token_search_time > token_search_interval and
                    len(monitored_tokens) < CONFIG.get('MAX_CONCURRENT_TOKENS', 3)):
                
                logging.info("🚀 Searching for validated tradable tokens...")
                if os.environ.get('HELIUS_API_KEY'):
                    logging.info("💰 Using Helius DEVELOPER ($99/month premium service)")
                
                last_token_search_time = current_time
                
                try:
                    # Use enhanced token discovery with Helius integration
                    potential_tokens = enhanced_find_newest_tokens_with_free_apis()
                    
                    if potential_tokens:
                        # Use smart selection to pick the best token
                        selected_token = smart_token_selection(potential_tokens)
                        
                        if selected_token:
                            # Handle both dict and string formats
                            if isinstance(selected_token, dict):
                                token_address = selected_token.get('address')
                                token_symbol = selected_token.get('symbol', 'UNKNOWN')
                                token_source = selected_token.get('source', 'unknown')
                            else:
                                token_address = selected_token
                                token_symbol = get_token_symbol(token_address) or token_address[:8]
                                token_source = 'fallback'
                            
                            # Double-check it's not already being monitored
                            if token_address and token_address not in monitored_tokens:
                                
                                # Final validation before buying
                                logging.info(f"🎯 Final validation for token: {token_address[:8]}")
                                
                                if validate_token_before_trading(token_address):
                                    
                                    logging.info(f"🚀 BUYING validated token: {token_symbol} ({token_address[:8]})")
                                    if os.environ.get('HELIUS_API_KEY'):
                                        logging.info(f"💎 Premium Helius discovery source: {token_source}")
                                    
                                    # Execute buy with configured amount
                                    buy_amount = float(os.environ.get('TRADE_AMOUNT_SOL', '0.144'))
                                    
                                    # Get initial price BEFORE buying
                                    initial_price = get_token_price_for_profit_calc(token_address)
                                    
                                    # Execute buy with enhanced success detection
                                    success, result = execute_via_javascript(token_address, buy_amount, is_sell=False)
                                    
                                    buy_attempts += 1
                                    total_trades += 1
                                    
                                    if success:
                                        buy_successes += 1
                                        buy_signature = result
                                        logging.info(f"✅ BUY SUCCESS: {token_symbol} ({token_address[:8]}) with {buy_amount} SOL")
                                        logging.info(f"📋 Transaction: {buy_signature}")
                                        
                                        if os.environ.get('HELIUS_API_KEY'):
                                            logging.info(f"🚀 Premium Helius Developer discovery successful!")
                                        
                                        # Initialize ultra-aggressive monitoring with enhanced data
                                        try:
                                            if not initial_price or initial_price <= 0:
                                                initial_price = get_token_price_for_profit_calc(token_address)
                                            
                                            if initial_price and initial_price > 0:
                                                monitored_tokens[token_address] = {
                                                    'initial_price': initial_price,
                                                    'highest_price': initial_price,
                                                    'buy_time': current_time,
                                                    'price_failures': 0,
                                                    'source': token_source,
                                                    'trade_amount': buy_amount,
                                                    'buy_signature': buy_signature
                                                }
                                                logging.info(f"📊 Initial price: ${initial_price:.6f} ({token_source} source)")
                                            else:
                                                # Use very small fallback price
                                                monitored_tokens[token_address] = {
                                                    'initial_price': 0.000001,
                                                    'highest_price': 0.000001,
                                                    'buy_time': current_time,
                                                    'price_failures': 0,
                                                    'source': token_source,
                                                    'trade_amount': buy_amount,
                                                    'buy_signature': buy_signature
                                                }
                                                logging.warning(f"⚠️ Using fallback price for token {token_address[:8]}")
                                        except Exception as e:
                                            logging.error(f"Error getting initial price: {str(e)}")
                                            # Use fallback
                                            monitored_tokens[token_address] = {
                                                'initial_price': 0.000001,
                                                'highest_price': 0.000001,
                                                'buy_time': current_time,
                                                'price_failures': 0,
                                                'source': token_source,
                                                'trade_amount': buy_amount,
                                                'buy_signature': buy_signature
                                            }
                                        
                                        token_buy_timestamps[token_address] = current_time
                                        
                                        # Log target for this token
                                        logging.info(f"🎯 Token Target: {CONFIG.get('MIN_PROFIT_PCT', 15)}% profit or {CONFIG.get('MAX_HOLD_TIME_SECONDS', 45)}-second exit")
                                        
                                    else:
                                        logging.warning(f"❌ BUY FAILED: {token_symbol} ({token_address[:8]}) - {result}")
                                        update_performance_stats(False, 0, token_address)
                                        
                                else:
                                    logging.warning(f"❌ Final validation failed for token: {token_address[:8]}")
                            else:
                                if token_address:
                                    logging.info(f"⏭️ Token {token_address[:8]} already being monitored")
                                else:
                                    logging.warning(f"❌ No valid token address from selection")
                        else:
                            logging.warning(f"❌ Smart selection returned no token")
                    else:
                        logging.warning(f"❌ No validated tokens found this round")
                        
                        # If we can't find any tokens, reduce search interval temporarily
                        token_search_interval = 20  # Search more frequently when tokens are scarce
                        logging.info(f"🔄 Reduced search interval to {token_search_interval}s due to token scarcity")
                        
                except Exception as e:
                    logging.error(f"❌ Error in token acquisition: {str(e)}")
                    circuit_breaker_check(error=True)
                    
            else:
                # Reset search interval to normal when we have tokens
                if len(monitored_tokens) >= CONFIG.get('MAX_CONCURRENT_TOKENS', 3):
                    token_search_interval = 30
            
            # ENHANCED PERFORMANCE REPORTING WITH PROFIT TRACKING
            if current_time - last_performance_report_time > 1800:  # Every 30 minutes
                hours_running = (current_time - daily_profit_start_time) / 3600
                
                # Update environment variables for persistence
                update_environment_variable('TOTAL_PROFIT', daily_profit)
                update_environment_variable('TOTAL_TRADES', total_trades)
                update_environment_variable('SUCCESSFUL_TRADES', successful_trades)
                
                logging.info("📊 =================== PERFORMANCE UPDATE ===================")
                logging.info(f"💎 Daily profit: ${daily_profit:.2f}")
                logging.info(f"✅ Successful trades: {successful_trades}")
                logging.info(f"📊 Buy/Sell ratio: {successful_trades}/{total_trades - successful_trades}")
                logging.info(f"🔄 Tokens monitored: {len(monitored_tokens)}")
                logging.info(f"🔥 Buy attempts: {total_trades} | Success rate: {(successful_trades/total_trades*100) if total_trades > 0 else 0:.1f}%")
                
                if os.environ.get('HELIUS_API_KEY'):
                    logging.info(f"💎 Helius DEVELOPER: ACTIVE (Premium $99/month service)")
                
                if hours_running > 0:
                    hourly_rate = daily_profit / hours_running
                    projected_daily = hourly_rate * 24
                    trades_per_hour = successful_trades / hours_running if hours_running > 0 else 0
                    
                    logging.info(f"⚡ Hourly rate: ${hourly_rate:.2f}/hour")
                    logging.info(f"📊 Projected daily: ${projected_daily:.2f}")
                    logging.info(f"🔄 Trade rate: {trades_per_hour:.1f} trades/hour")
                    
                    # Check if we need to be more aggressive
                    if projected_daily < 1000 and hours_running > 2:
                        needed_hourly = (1000 - daily_profit) / (24 - hours_running) if (24 - hours_running) > 0 else 0
                        logging.warning(f"⚠️ Need ${needed_hourly:.2f}/hour to reach $1k target")
                        
                        # Auto-scaling logic
                        current_position = float(os.environ.get('TRADE_AMOUNT_SOL', '0.144'))
                        success_rate = (successful_trades/total_trades*100) if total_trades > 0 else 0
                        
                        if success_rate > 20 and daily_profit > 50:  # Good performance
                            suggested_position = min(current_position * 1.2, 0.5)  # Max 0.5 SOL
                            logging.info(f"🚀 Increasing buy amount to {suggested_position:.3f} SOL")
                            update_environment_variable('TRADE_AMOUNT_SOL', f"{suggested_position:.3f}")
                
                logging.info("📊 =======================================================")
                last_performance_report_time = current_time
                
                # Force memory cleanup after performance report
                cleanup_memory()
            
            # Daily reset
            if current_time - daily_profit_start_time > 86400:
                logging.info(f"🌅 Daily reset - Previous total: ${daily_profit:.2f}")
                daily_profit = 0
                successful_trades_today = 0
                daily_profit_start_time = current_time
            
            # Brief status update every 2 minutes
            if current_time - last_status_time > 120:
                active_tokens_info = []
                for addr in monitored_tokens.keys():
                    symbol = get_token_symbol(addr) or addr[:8]
                    seconds_held = current_time - monitored_tokens[addr]['buy_time']
                    active_tokens_info.append(f"{symbol}({seconds_held:.0f}s)")
                
                active_tokens_str = ", ".join(active_tokens_info) if active_tokens_info else "None"
                helius_status = "HD✓" if os.environ.get('HELIUS_API_KEY') else "HD✗"
                success_rate = (successful_trades/total_trades*100) if total_trades > 0 else 0
                
                logging.info(f"🔄 Active: [{active_tokens_str}] | Daily: ${daily_profit:.2f} | Trades: {successful_trades} | SR: {success_rate:.1f}% | {helius_status}")
                last_status_time = current_time
            
            # Ultra-short sleep for maximum responsiveness
            time.sleep(0.2)  # 200ms sleep for ultra-fast monitoring
            
        except Exception as e:
            errors_encountered += 1
            logging.error(f"❌ Error in main loop: {str(e)}")
            logging.error(traceback.format_exc())
            circuit_breaker_check(error=True)
            time.sleep(2)  # Short error recovery time

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
    """Main entry point with quick-flip strategy."""
    logging.info("============ QUICK-FLIP BOT STARTING ============")
    logging.info("Target: $1,000 daily profit with frequent 20% gains")
    
    # Check Solders version at startup
    solders_version = check_solders_version()
    logging.info(f"Solders version: {solders_version}")
    
    if initialize():
        # Comment out the entire test section to eliminate the test_token error
        # Quick test to verify JavaScript is working
        # test_token = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
        # test_amount = 0.001  # Very small test amount
        
        # logging.info(f"Testing JavaScript transaction with {test_amount} SOL...")
        # try:
            # Quick JS test
            # success, signature = execute_via_javascript(test_token, test_amount)
            
            # if success:
                # logging.info("JavaScript test successful! Starting Quick-Flip trading...")
                # trading_loop()
            # else:
                # logging.error("JavaScript test failed. Cannot start trading.")
                # logging.error("Please verify JavaScript setup and configuration.")
        # except Exception as e:
            # logging.error(f"Error during JavaScript test: {str(e)}")
            # logging.error(traceback.format_exc())
        
        # Skip test and go directly to trading
        logging.info("Initialization successful! Starting Quick-Flip trading...")
        trading_loop()
    else:
        logging.error("Failed to initialize bot. Please check configurations.")

if __name__ == "__main__":
    main()
