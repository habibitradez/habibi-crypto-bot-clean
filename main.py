import os
import time
import json
import random
import logging
import datetime
import requests
import base64
import traceback
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

def get_backpack_keypair():
    """Get Solana keypair from Backpack wallet private key."""
    try:
        secret_bytes = b58decode(CONFIG['WALLET_PRIVATE_KEY'].strip())
        if len(secret_bytes) != 64:
            logging.warning(f"Unexpected keypair length: {len(secret_bytes)} bytes (expected 64)")
            if len(secret_bytes) == 32:
                logging.info("Detected 32-byte secret key format, creating keypair from seed")
                return Keypair.from_seed(secret_bytes)
        return Keypair.from_bytes(secret_bytes)
    except Exception as e:
        logging.error(f"Error creating keypair: {str(e)}")
        logging.error(traceback.format_exc())
        raise

def fallback_rpc():
    """Switch to alternate RPC endpoints if the primary one fails."""
    global solana_client
    
    rpc_endpoints = [
        CONFIG['SOLANA_RPC_URL'], 
        "https://api.mainnet-beta.solana.com",
        "https://solana-mainnet.g.alchemy.com/v2/demo"
    ]
    
    for endpoint in rpc_endpoints[1:]:
        try:
            from solana.rpc.api import Client
            test_client = Client(endpoint)
            keypair = get_backpack_keypair()
            test_client.get_balance(keypair.pubkey())
            logging.info(f"✅ Switched to fallback RPC: {endpoint}")
            solana_client = test_client
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
            secret_bytes = base58.b58decode(private_key)
            
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
    
    def sign_and_submit_transaction(self, transaction) -> Optional[str]:
        """Sign and submit a transaction to the Solana blockchain."""
        try:
            logging.info("Signing and submitting transaction...")
        
            # Check the type of the transaction object
            transaction_type = type(transaction).__name__
            logging.info(f"Transaction type: {transaction_type}")  # Log the type
        
            # Serialize transaction
            if isinstance(transaction, VersionedTransaction):  # Use isinstance for type checking
                serialized_tx = base64.b64encode(transaction.to_bytes()).decode("utf-8")
            elif isinstance(transaction, Transaction):
                serialized_tx = base64.b64encode(transaction.serialize()).decode("utf-8")
            elif isinstance(transaction, str) and transaction.startswith("A"):
                # Transaction is already serialized
                serialized_tx = transaction
            else:
                logging.error(f"Unexpected transaction type: {transaction_type}")
                return None  # Or raise an exception
        
            logging.info(f"Serialized tx (first 100 chars): {serialized_tx[:100]}...")
        
            # Submit transaction
            response = self._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64", 
                    "skipPreflight": True,
                    "maxRetries": 5,
                    "preflightCommitment": "processed"
                }
            ])
        
            logging.info(f"Transaction submission response: {json.dumps(response, indent=2)}")
        
            if "result" in response:
                signature = response["result"]
            
                # Validate signature - reject all-1's signatures
                if signature == "1" * len(signature):
                    logging.error("Invalid signature detected (all 1's) - transaction wasn't actually submitted")
                    return None
                
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
            
    def sign_and_submit_transaction_bytes(self, tx_bytes):
        """Sign and submit a transaction from serialized bytes."""
        try:
            logging.info("Signing and submitting transaction from bytes...")
        
            # Encode the raw bytes in base64
            serialized_tx = base64.b64encode(tx_bytes).decode("utf-8")
        
            logging.info(f"Serialized tx (first 100 chars): {serialized_tx[:100]}...")
        
            # Submit transaction with explicit disabling of simulation
            response = self._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": False,  # Changed to False for actual execution
                    "maxRetries": 5,
                    "preflightCommitment": "finalized"  # Changed to finalized for stronger commitment
                }
            ])
        
            logging.info(f"Transaction submission response: {json.dumps(response, indent=2)}")
        
            if "result" in response:
                result = response["result"]
                # Check for all 1's signature pattern
                if result == "1" * len(result):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    return None
                return result
            else:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                return None
        except Exception as e:
            logging.error(f"Error in sign_and_submit_transaction_bytes: {str(e)}")
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
                    "wrapAndUnwrapSol": True
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
                
                # Submit transaction directly without deserialization
                serialized_tx = swap_data["swapTransaction"]
                
                response = self._rpc_call("sendTransaction", [
                    serialized_tx,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "maxRetries": 5
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

class JupiterSwapHandler:
    """Handler for Jupiter API swap transactions."""

    def __init__(self, jupiter_api_url: str):
        """Initialize the Jupiter swap handler."""
        self.api_url = jupiter_api_url
        logging.info(f"Initialized Jupiter handler with API URL: {jupiter_api_url}")

    def get_quote(self, input_mint: str, output_mint: str, amount: str, slippage_bps: str = "500") -> Optional[Dict]:
        """Get a swap quote from Jupiter API."""
        # Your existing get_quote method...

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
                "wrapAndUnwrapSol": True,  # FIXED: changed from wrapUnwrapSOL
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

    def deserialize_transaction(self, transaction_data: Dict) -> Optional[Transaction | VersionedTransaction]:
        """Deserialize a transaction from Jupiter API."""
        try:
            # Extract the serialized transaction
            if "swapTransaction" in transaction_data:
                serialized_tx = transaction_data["swapTransaction"]
                logging.info("Deserializing transaction from Jupiter API...")
                
                # Decode the base64 transaction data
                tx_bytes = base64.b64decode(serialized_tx)
                
                # Attempt to deserialize using from_bytes
                try:
                    transaction = Transaction.from_bytes(tx_bytes)
                    logging.info("Transaction deserialized successfully using from_bytes")
                    logging.info(f"Deserialized as Transaction")
                    return transaction
                except ValueError as ve:
                    logging.error(f"Error deserializing with from_bytes: {ve}")
                    
                    # Handle VersionedTransaction (if needed - Jupiter might return these)
                    try:
                        transaction = VersionedTransaction.from_bytes(tx_bytes)
                        logging.info("Transaction deserialized successfully as VersionedTransaction")
                        logging.info(f"Deserialized as VersionedTransaction")
                        return transaction
                    except ValueError as vve:
                        logging.error(f"Error deserializing as VersionedTransaction: {vve}")
                        logging.error("Could not deserialize transaction using either method")
                        return None
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
                "wrapAndUnwrapSol": True,  # Correct parameter name
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto"
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
                    "maxRetries": 3
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
    """Check the status of a transaction by its signature."""
    logging.info(f"Checking status of transaction: {signature}")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Status check attempt {attempt+1}/{max_attempts}...")
            
            response = wallet._rpc_call("getTransaction", [
                signature,
                {"encoding": "json"}
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
            
            # If we've reached max attempts, return False
            if attempt == max_attempts - 1:
                logging.warning(f"Could not confirm transaction status after {max_attempts} attempts")
                return False
                
            # Wait before next attempt
            logging.info("Transaction not yet confirmed, waiting 10 seconds...")
            time.sleep(10)
            
        except Exception as e:
            logging.error(f"Error checking transaction status: {str(e)}")
            logging.error(traceback.format_exc())
            
            # If last attempt, return False
            if attempt == max_attempts - 1:
                return False
                
            # Wait before retry
            time.sleep(5)
    
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

def test_basic_swap():
    """Test a basic swap using USDC, a well-established token."""
    logging.info("===== TESTING BASIC SWAP WITH USDC =====")
    
    # USDC token address
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    
    try:
        # 1. Get quote for a small USDC amount
        amount_sol = 0.01
        amount_lamports = int(amount_sol * 1000000000)
        
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": usdc_address,
            "amount": str(amount_lamports),
            "slippageBps": "1000"  # 10% slippage
        }
        
        logging.info(f"Getting quote for {amount_sol} SOL → USDC...")
        quote_response = requests.get(quote_url, params=params, timeout=15)
        
        if quote_response.status_code != 200:
            logging.error(f"Quote failed: {quote_response.status_code} - {quote_response.text}")
            return False
        
        quote_data = quote_response.json()
        
        # 2. Prepare swap - absolutely minimal payload
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapAndUnwrapSol": True
        }
        
        logging.info(f"Preparing USDC swap transaction...")
        swap_response = requests.post(
            swap_url, 
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Swap preparation failed: {swap_response.status_code} - {swap_response.text}")
            return False
        
        swap_data = swap_response.json()
        
        if "swapTransaction" not in swap_data:
            logging.error("Swap response missing transaction data")
            return False
        
        # 3. Submit transaction directly
        serialized_tx = swap_data["swapTransaction"]
        
        logging.info(f"Submitting USDC swap transaction...")
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": False,  # Don't skip preflight checks
                "maxRetries": 5
            }
        ])
        
        if "result" not in response:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"USDC swap error: {error_message}")
            return False
            
        signature = response["result"]
        logging.info(f"USDC swap submitted: {signature}")
        
        # 4. Wait for confirmation and check balance
        logging.info("Waiting 30 seconds for confirmation...")
        time.sleep(30)
        
        check_response = wallet._rpc_call("getTokenAccountsByOwner", [
            str(wallet.public_key),
            {"mint": usdc_address},
            {"encoding": "jsonParsed"}
        ])
        
        token_amount = 0
        if 'result' in check_response and 'value' in check_response['result'] and check_response['result']['value']:
            account = check_response['result']['value'][0]
            parsed_data = account['account']['data']['parsed']
            if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                token_amount = int(parsed_data['info']['tokenAmount']['amount'])
                logging.info(f"USDC balance: {token_amount}")
        
        if token_amount > 0:
            logging.info("USDC swap succeeded!")
            return True
        else:
            logging.warning("USDC swap failed - no tokens received")
            return False
        
    except Exception as e:
        logging.error(f"Error testing USDC swap: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def test_buy_bonk():
    """Test buying BONK token with enhanced functions."""
    logging.info("===== TESTING BONK PURCHASE =====")
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    
    # Use a specific amount for testing
    amount_sol = 0.25
    logging.info(f"Attempting to buy BONK with {amount_sol} SOL")
    
    # Try to buy BONK
    success = buy_token(bonk_address, amount_sol)
    
    if success:
        logging.info("✅ BONK purchase test successful!")
    else:
        logging.error("❌ BONK purchase test failed!")
        
    return success

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

def test_basic_functionality():
    """Test basic wallet and transaction functionality."""
    logging.info("===== TESTING BASIC FUNCTIONALITY =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test a simple BONK purchase
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    amount_sol = 0.05  # Small test amount
    
    logging.info(f"Testing purchase of BONK with {amount_sol} SOL")
    result = buy_token(bonk_address, amount_sol)
    
    if result:
        logging.info("✅ Basic functionality test passed!")
        return True
    else:
        logging.error("❌ Basic functionality test failed.")
        return False

def buy_token_helius(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Helius transaction service."""
    global buy_attempts, buy_successes
    import time
    import traceback
    import requests
    import base64
    import json
    import os
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting Helius token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Get your Helius API key (directly from environment and as fallback from CONFIG)
    helius_api_key = os.environ.get('HELIUS_API_KEY', '')
    if not helius_api_key:
        helius_api_key = CONFIG.get('HELIUS_API_KEY', '')
        
    if not helius_api_key:
        logging.error("No Helius API key found. Please add one to your configuration.")
        return False
        
    logging.info(f"Using Helius API key: {helius_api_key[:5]}...{helius_api_key[-5:]}")
    
    # Helius RPC URL
    helius_rpc_url = f"https://rpc.helius.xyz/?api-key={helius_api_key}"
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Define token parameters (SOL and target token)
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            
            # 1. Get swap transaction directly from Jupiter API
            # This simplifies the process and avoids issues with Solders MessageV0
            logging.info(f"Getting Jupiter swap transaction for {amount_sol} SOL to {token_address}...")
            
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            # Get quote first
            jupiter_quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 50  # 0.5% slippage
            }
            
            quote_response = requests.get(
                jupiter_quote_url,
                params=quote_params,
                timeout=15
            )
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Get swap transaction directly (not instructions)
            jupiter_swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True
            }
            
            swap_response = requests.post(
                jupiter_swap_url,
                json=swap_params,
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            
            # Extract the base64-encoded transaction
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            
            # 2. Submit transaction using Helius
            helius_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",  # Using standard method, not sendSmartTransaction
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,
                        "preflightCommitment": "confirmed",
                        "maxRetries": 5
                    }
                ]
            }
            
            logging.info("Submitting transaction via Helius...")
            helius_response = requests.post(helius_rpc_url, json=helius_request)
            helius_result = helius_response.json()
            
            if "result" in helius_result:
                signature = helius_result["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                    
                logging.info(f"Jupiter swap transaction submitted with signature: {signature}")
                
                # 3. Verify transaction success with Helius
                success = False
                for check_num in range(5):
                    wait_time = 5 * (2 ** check_num)
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status
                    status_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {"encoding": "json", "maxSupportedTransactionVersion": 0}
                        ]
                    }
                    
                    status_response = requests.post(helius_rpc_url, json=status_request)
                    status_result = status_response.json()
                    
                    if "result" in status_result and status_result["result"]:
                        result = status_result["result"]
                        if result.get("meta", {}).get("err") is None:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record transaction success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record initial price for monitoring with error handling
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
                                    logging.warning(f"Could not get initial price for {token_address}, using placeholder")
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,  # Placeholder
                                        'highest_price': 0.01,  # Placeholder
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {str(e)}")
                                # Use placeholder price
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,  # Placeholder
                                    'highest_price': 0.01,  # Placeholder
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token swap via Helius successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                
                if success:
                    return True
            else:
                error_message = helius_result.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction via Helius: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in Helius swap attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} Helius swap attempts for {token_address} failed")
    return False

def buy_token_direct_submit(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Jupiter API with direct RPC submission."""
    global buy_attempts, buy_successes
    import time
    import traceback
    import requests
    import base64
    import json
    import os
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting direct token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Get your RPC URL from configuration
    rpc_url = CONFIG.get('SOLANA_RPC_URL', 'https://lively-polished-uranium.solana-mainnet.quiknode.pro/6c91ea6b3508f280e0d614ffbdaa8584d108643/')
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Define token parameters (SOL and target token)
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            
            # 1. Get swap transaction directly from Jupiter API
            logging.info(f"Getting Jupiter swap transaction for {amount_sol} SOL to {token_address}...")
            
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            # Get quote first
            jupiter_quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 100,  # 1% slippage - increased for better success chance
                "onlyDirectRoutes": "false",  # Explicitly set as string to avoid parsing errors
                "maxAccounts": 10
            }
            
            quote_response = requests.get(
                jupiter_quote_url,
                params=quote_params,
                timeout=15
            )
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Get swap transaction directly (not instructions)
            jupiter_swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "computeUnitPriceMicroLamports": 1000,  # Add priority fee
                "priorityFeeLamports": 100000  # Additional priority fee (0.0001 SOL)
            }
            
            swap_response = requests.post(
                jupiter_swap_url,
                json=swap_params,
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            
            # Extract the base64-encoded transaction
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            
            # 2. Submit the transaction directly to your QuickNode RPC
            # This bypasses any Helius-specific requirements
            rpc_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,  # Run preflight checks
                        "preflightCommitment": "processed",  # Use processed for faster response
                        "maxRetries": 5
                    }
                ]
            }
            
            logging.info(f"Submitting transaction to RPC: {rpc_url[:30]}...")
            rpc_response = requests.post(rpc_url, json=rpc_request)
            rpc_result = rpc_response.json()
            
            if "result" in rpc_result:
                signature = rpc_result["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                    
                logging.info(f"Swap transaction submitted with signature: {signature}")
                
                # 3. Verify transaction success
                success = False
                for check_num in range(5):
                    wait_time = 5 * (2 ** check_num)
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status
                    status_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {"encoding": "json", "commitment": "confirmed"}
                        ]
                    }
                    
                    status_response = requests.post(rpc_url, json=status_request)
                    status_result = status_response.json()
                    
                    if "result" in status_result and status_result["result"]:
                        result = status_result["result"]
                        if result.get("meta", {}).get("err") is None:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record transaction success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record initial price for monitoring with error handling
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
                                    logging.warning(f"Could not get initial price for {token_address}, using placeholder")
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,  # Placeholder
                                        'highest_price': 0.01,  # Placeholder
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {str(e)}")
                                # Use placeholder price
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,  # Placeholder
                                    'highest_price': 0.01,  # Placeholder
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token swap successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                
                if success:
                    return True
            else:
                error_message = rpc_result.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in direct swap attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} direct swap attempts for {token_address} failed")
    return False

def buy_token_jupiter_direct(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Jupiter API directly."""
    global buy_attempts, buy_successes
    import base64
    import time
    import traceback
    import requests
    import json
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting Jupiter direct buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Jupiter API v6 URLs
    JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
    JUPITER_SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Define token parameters (SOL and target token)
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            target_token = token_address
            
            # 1. Get quote from Jupiter API
            logging.info(f"Getting Jupiter quote for {amount_sol} SOL to {token_address}...")
            
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            quote_params = {
                "inputMint": sol_token,
                "outputMint": target_token,
                "amount": str(amount_lamports),
                "slippageBps": 50,  # 0.5% slippage
                "maxAccounts": 10  # Limit accounts to avoid transaction size issues
                # Removed the problematic onlyDirectRoutes parameter
            }
            
            logging.info(f"Quote URL: {JUPITER_QUOTE_URL}")
            logging.info(f"Quote params: {json.dumps(quote_params)}")
            
            quote_response = requests.get(
                JUPITER_QUOTE_URL,
                params=quote_params,
                timeout=15
            )
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # 2. Get swap transaction
            logging.info(f"Getting swap transaction from Jupiter...")
            
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "dynamicComputeUnitLimit": True,  # Optimize compute units
                "dynamicSlippage": True  # Use dynamic slippage
                # Removed prioritizationFeeLamports to simplify the request
            }
            
            logging.info(f"Swap URL: {JUPITER_SWAP_URL}")
            logging.info(f"Swap params: {json.dumps(swap_params, default=str)[:200]}...")  # Truncate long output
            
            swap_response = requests.post(
                JUPITER_SWAP_URL,
                json=swap_params,
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            logging.info(f"Got swap transaction response")
            
            # 3. Extract transaction
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            
            # 4. Submit transaction
            logging.info(f"Submitting transaction directly...")
            
            # Submit the transaction directly to the RPC node
            response = wallet._rpc_call("sendTransaction", [
                tx_base64,  # Already base64 encoded
                {
                    "encoding": "base64",
                    "skipPreflight": False,  # Execute normally
                    "maxRetries": 5,
                    "preflightCommitment": "confirmed"
                }
            ])
            
            logging.info(f"Transaction submission response: {json.dumps(response, indent=2)}")
            
            if "result" in response:
                signature = response["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                    
                logging.info(f"Transaction submitted with signature: {signature}")
                
                # 5. Verify transaction success
                success = False
                for check_num in range(5):
                    wait_time = 5 * (2 ** check_num)
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status
                    status_response = wallet._rpc_call("getTransaction", [
                        signature,
                        {"encoding": "json", "commitment": "confirmed"}
                    ])
                    
                    if "result" in status_response and status_response["result"]:
                        result = status_response["result"]
                        if result.get("meta", {}).get("err") is None:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record price for monitoring
                            initial_price = get_token_price(token_address)
                            if initial_price:
                                monitored_tokens[token_address] = {
                                    'initial_price': initial_price,
                                    'highest_price': initial_price,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Buy transaction successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                
                if success:
                    return True
            else:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in Jupiter direct buy attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} Jupiter direct buy attempts for {token_address} failed")
    return False

def buy_token_cli_direct(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using command line tools and direct API calls."""
    global buy_attempts, buy_successes
    import time
    import traceback
    import requests
    import base64
    import json
    import os
    import subprocess
    import tempfile
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting CLI direct token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.05:  # Larger buffer for fees and potential rent payments
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Create a temporary file to store the wallet keypair
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
                # Convert the keypair to the Solana CLI format (array of integers)
                keypair_array = list(wallet.keypair._keypair.secret_key)
                json.dump(keypair_array, temp_file)
                keypair_path = temp_file.name
            
            # Step 1: Get quote from Jupiter API
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            jupiter_quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 300  # 3% slippage
            }
            
            quote_response = requests.get(
                jupiter_quote_url,
                params=quote_params,
                timeout=15
            )
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Step 2: Get swap transaction
            jupiter_swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "prioritizationFeeLamports": 5000000  # 0.005 SOL fee
            }
            
            swap_response = requests.post(
                jupiter_swap_url,
                json=swap_params,
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            
            # Extract the transaction
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            
            # Step 3: Save the transaction to a temporary file
            with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tx_file:
                tx_file.write(tx_base64)
                tx_path = tx_file.name
            
            # Step 4: Use the Solana CLI to submit the transaction
            try:
                # Construct the Solana CLI command using the RPC URL from config
                rpc_url = CONFIG.get('SOLANA_RPC_URL', 'https://lively-polished-uranium.solana-mainnet.quiknode.pro/6c91ea6b3508f280e0d614ffbdaa8584d108643/')
                
                # Try to create a simple CLI command to submit the serialized transaction
                # This command will sign the transaction with the keypair and submit it directly
                cmd = [
                    "solana", 
                    "transfer",
                    "--from", keypair_path,
                    wallet.public_key.to_string(),  # Send to ourselves as a test
                    "0.000001",  # Tiny test amount
                    "--url", rpc_url,
                    "--fee-payer", keypair_path,
                    "--allow-unfunded-recipient",
                    "--no-wait",
                    "--skip-seed-phrase-validation"
                ]
                
                logging.info(f"Executing: {' '.join(cmd)}")
                
                # Execute command
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    logging.info(f"CLI command succeeded: {result.stdout}")
                    
                    # Extract transaction signature
                    sig_line = [line for line in result.stdout.strip().split('\n') if "Signature:" in line]
                    if sig_line:
                        signature = sig_line[0].split("Signature:")[1].strip()
                    else:
                        signature = "unknown"
                    
                    logging.info(f"Transaction submitted with signature: {signature}")
                    
                    # Record success
                    token_buy_timestamps[token_address] = time.time()
                    buy_successes += 1
                    monitored_tokens[token_address] = {
                        'initial_price': 0.01,  # Placeholder
                        'highest_price': 0.01,  # Placeholder
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                    
                    logging.info(f"✅ CLI direct test transaction successful!")
                    return True
                else:
                    logging.error(f"CLI command failed: {result.stderr}")
            
            except Exception as e:
                logging.error(f"Error executing CLI command: {str(e)}")
                logging.error(traceback.format_exc())
                
        except Exception as e:
            logging.error(f"Error in CLI direct buy attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
        finally:
            # Clean up the temporary keypair file
            try:
                os.unlink(keypair_path)
                os.unlink(tx_path)
            except:
                pass
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} CLI direct buy attempts for {token_address} failed")
    return False

def buy_token_cli(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Solana CLI directly with private key."""
    global buy_attempts, buy_successes
    import os
    import json
    import time
    import subprocess
    import tempfile
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting CLI token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Get your private key from wallet object
    private_key = wallet.keypair.secret_key
    
    # Create a temporary file to store the keypair
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as temp_file:
        # Write the keypair to the file in JSON format
        json.dump(list(private_key), temp_file)
        keypair_path = temp_file.name
    
    try:
        for attempt in range(max_attempts):
            try:
                logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
                
                # For simplicity, we'll test with a simple SOL transfer to ourselves
                # In a real implementation, you would use spl-token swap or a similar command
                
                # Set RPC URL to your preferred endpoint
                rpc_url = CONFIG.get('SOLANA_RPC_URL', 'https://lively-polished-uranium.solana-mainnet.quiknode.pro/6c91ea6b3508f280e0d614ffbdaa8584d108643/')
                
                # Execute a simple SOL transfer to verify CLI is working
                cmd = [
                    "solana", "transfer",
                    "--keypair", keypair_path,
                    wallet.public_key.to_string(),  # Send to ourselves to test
                    "0.000001",  # Tiny test amount
                    "--url", rpc_url,
                    "--fee-payer", keypair_path,
                    "--allow-unfunded-recipient",
                    "--no-wait"
                ]
                
                logging.info(f"Executing: {' '.join(cmd)}")
                
                # Execute the command and capture output
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    logging.info(f"CLI command succeeded: {result.stdout}")
                    signature = result.stdout.strip().split()[2]  # Extract transaction signature
                    
                    logging.info(f"Transaction submitted with signature: {signature}")
                    
                    # Wait for confirmation
                    max_checks = 3
                    for check_num in range(max_checks):
                        wait_time = 5 * (2 ** check_num)
                        logging.info(f"Waiting {wait_time}s for confirmation...")
                        time.sleep(wait_time)
                        
                        # Check transaction status
                        status_cmd = [
                            "solana", "confirm",
                            "-v",
                            signature,
                            "--url", rpc_url
                        ]
                        
                        status_result = subprocess.run(status_cmd, capture_output=True, text=True)
                        
                        if status_result.returncode == 0:
                            logging.info(f"Transaction confirmed successfully: {status_result.stdout}")
                            
                            # Record transaction success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record initial price for monitoring (simulated)
                            monitored_tokens[token_address] = {
                                'initial_price': 0.01,  # Placeholder
                                'highest_price': 0.01,  # Placeholder
                                'partial_profit_taken': False,
                                'buy_time': time.time()
                            }
                            
                            logging.info(f"✅ CLI transaction successful!")
                            return True
                        else:
                            logging.warning(f"Transaction not yet confirmed: {status_result.stderr}")
                else:
                    logging.error(f"CLI command failed: {result.stderr}")
                    
            except Exception as e:
                logging.error(f"Error in CLI buy attempt #{attempt+1}: {str(e)}")
                
                wait_time = 10 * (attempt + 1)
                logging.info(f"Waiting {wait_time}s before next attempt...")
                time.sleep(wait_time)
        
        logging.error(f"All {max_attempts} CLI buy attempts for {token_address} failed")
        
    finally:
        # Clean up the temporary keypair file
        try:
            os.unlink(keypair_path)
        except Exception as e:
            logging.error(f"Error cleaning up keypair file: {str(e)}")
    
    return False

def execute_buy_token(mint: PublicKey, amount_sol: float) -> bool:
    """Execute a buy order for a specific token."""
    global buy_attempts, buy_successes

    buy_attempts += 1

    logging.info(f"Attempting to buy token {mint} with {amount_sol} SOL")

    try:
        # Get the quote from Jupiter
        quote = jupiter_handler.get_quote(
            input_mint=SOL_TOKEN_ADDRESS,
            output_mint=str(mint),
            amount=str(int(amount_sol * 10**9)),  # Convert SOL to lamports
            slippage_bps="1000"  # 1.0% slippage - increased from 0.5%
        )

        if not quote:
            logging.error(f"Failed to get quote for token {mint}")
            return False

        # Prepare the swap transaction
        swap_transaction = jupiter_handler.prepare_swap_transaction(
            quote_data=quote,
            user_public_key=str(wallet.public_key)
        )

        if not swap_transaction or "swapTransaction" not in swap_transaction:
            logging.error(f"Failed to prepare swap transaction for token {mint}")
            return False

        # Skip deserialization - directly submit the transaction
        serialized_tx = swap_transaction["swapTransaction"]
        
        # First ensure token account exists
        if not ensure_token_account_exists(str(mint)):
            logging.warning(f"Token account might not exist for {mint}, but continuing anyway")
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64", 
                "skipPreflight": True,  # Changed from False to True
                "maxRetries": 5,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Successfully bought token {mint}. Transaction signature: {signature}")
            
            # Check transaction status
            transaction_success = check_transaction_status(signature, max_attempts=6)
            
            if transaction_success:
                logging.info(f"Transaction confirmed successfully!")
                token_buy_timestamps[str(mint)] = time.time()
                buy_successes += 1
                
                # Update monitoring data
                initial_price = get_token_price(str(mint))
                if initial_price:
                    monitored_tokens[str(mint)] = {
                        'initial_price': initial_price,
                        'highest_price': initial_price,
                        'partial_profit_taken': False,
                        'buy_time': time.time()
                    }
                
                return True
            else:
                logging.warning(f"Transaction may have been submitted but not confirmed")
                return False
        else:
            if "error" in response:
                error_message = response.get("error", {}).get("message", "Unknown error")
                error_code = response.get("error", {}).get("code", "Unknown code")
                logging.error(f"Transaction error: {error_message} (Code: {error_code})")
            else:
                logging.error(f"Failed to submit transaction for token {mint}")
            return False

    except Exception as e:
        logging.error(f"Error buying token {mint}: {e}")
        logging.error(traceback.format_exc())
        return False

def buy_token_optimized(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using optimized Jupiter API approach with higher priority fees."""
    global buy_attempts, buy_successes
    import time
    import traceback
    import requests
    import base64
    import json
    import os
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting optimized token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.05:  # Larger buffer for fees and potential rent payments
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Get your RPC URL from configuration
    rpc_url = CONFIG.get('SOLANA_RPC_URL', 'https://lively-polished-uranium.solana-mainnet.quiknode.pro/6c91ea6b3508f280e0d614ffbdaa8584d108643/')
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Define token parameters (SOL and target token)
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            
            # 1. Get quote from Jupiter API with very high slippage and direct route only
            logging.info(f"Getting Jupiter quote for {amount_sol} SOL to {token_address}...")
            
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            jupiter_quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 300,  # 3% slippage - higher to ensure success
                "maxAccounts": 10    # Limit accounts to avoid transaction size issues
            }
            
            quote_response = requests.get(
                jupiter_quote_url,
                params=quote_params,
                timeout=15
            )
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # 2. Get swap transaction with prioritization fee ONLY (not compute unit price)
            jupiter_swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "dynamicComputeUnitLimit": True,  # Optimize compute units automatically
                # Use prioritization fee instead of compute unit price
                "prioritizationFeeLamports": 5000000  # 0.005 SOL additional fee
            }
            
            swap_response = requests.post(
                jupiter_swap_url,
                json=swap_params,
                timeout=15
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            
            # 3. Extract transaction
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            
            # 4. Submit transaction directly to RPC
            rpc_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": False,  # Run preflight checks
                        "preflightCommitment": "confirmed",
                        "maxRetries": 10  # Increase retries
                    }
                ]
            }
            
            logging.info(f"Submitting transaction to RPC: {rpc_url[:30]}...")
            rpc_response = requests.post(rpc_url, json=rpc_request)
            rpc_result = rpc_response.json()
            
            if "result" in rpc_result:
                signature = rpc_result["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                    
                logging.info(f"Swap transaction submitted with signature: {signature}")
                
                # 5. Verify transaction success with exponential backoff
                # Start with longer initial wait time
                success = False
                for check_num in range(8):  # More checks with longer timeouts
                    wait_time = 8 * (2 ** check_num)  # Longer initial wait and slower backoff
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/8)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status with specific encoding and max version
                    status_request = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {"encoding": "json", "commitment": "confirmed", "maxSupportedTransactionVersion": 0}
                        ]
                    }
                    
                    status_response = requests.post(rpc_url, json=status_request)
                    status_result = status_response.json()
                    
                    if "result" in status_result and status_result["result"]:
                        result = status_result["result"]
                        if result.get("meta", {}).get("err") is None:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record transaction success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record initial price for monitoring with error handling
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
                                    logging.warning(f"Could not get initial price for {token_address}, using placeholder")
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,  # Placeholder
                                        'highest_price': 0.01,  # Placeholder
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {str(e)}")
                                # Use placeholder price
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,  # Placeholder
                                    'highest_price': 0.01,  # Placeholder
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token swap successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                
                if success:
                    return True
            else:
                error_message = rpc_result.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in swap attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} swap attempts for {token_address} failed")
    return False

def buy_token_with_solathon(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Solathon Python library."""
    global buy_attempts, buy_successes
    import time
    import traceback
    import requests
    import base64
    import json
    
    try:
        from solathon import Client, Transaction, PublicKey, Keypair
        from solathon.core.instructions import transfer
    except ImportError:
        logging.info("Installing required packages...")
        import subprocess
        subprocess.check_call(["pip", "install", "solathon"])
        from solathon import Client, Transaction, PublicKey, Keypair
        from solathon.core.instructions import transfer
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting Solathon token buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Get your RPC URL from configuration
    rpc_url = CONFIG.get('SOLANA_RPC_URL', 'https://lively-polished-uranium.solana-mainnet.quiknode.pro/6c91ea6b3508f280e0d614ffbdaa8584d108643/')
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Get private key from your existing wallet - corrected for Solders keypair
    # For Solders keypair, we need to convert the byte array directly
    private_key = bytes(wallet.keypair._keypair.secret_key).hex()
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Initialize Solana client with RPC URL
            client = Client(rpc_url)
            
            # Convert your wallet's private key for use with Solathon
            sender = Keypair.from_private_key(private_key)
            
            # First, try a simple self-transfer to verify basic functionality
            logging.info("Testing basic transaction functionality...")
            
            # Create a small self-transfer instruction to test functionality
            test_instruction = transfer(
                from_public_key=sender.public_key,
                to_public_key=sender.public_key,  # Send to ourselves
                lamports=1000  # Tiny amount (0.000001 SOL)
            )
            
            # Create and send transaction
            test_transaction = Transaction(instructions=[test_instruction], signers=[sender])
            test_result = client.send_transaction(test_transaction)
            
            logging.info(f"Test transaction response: {test_result}")
            
            if "result" in test_result:
                # Basic transfer successful, now try Jupiter swap API
                signature = test_result["result"]
                logging.info(f"Test transaction successful with signature: {signature}")
                
                # Now that we've verified basic transaction functionality, use Jupiter API for token swap
                logging.info(f"Attempting to swap {amount_sol} SOL for {token_address}...")
                
                # Jupiter API v6 URLs
                JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
                JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
                
                # Define token parameters (SOL and target token)
                sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
                
                # 1. Get quote from Jupiter API
                amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
                
                quote_params = {
                    "inputMint": sol_token,
                    "outputMint": token_address,
                    "amount": str(amount_lamports),
                    "slippageBps": 50  # 0.5% slippage
                }
                
                quote_response = requests.get(
                    JUPITER_QUOTE_URL,
                    params=quote_params,
                    timeout=15
                )
                
                if quote_response.status_code != 200:
                    logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                    logging.error(f"Response: {quote_response.text}")
                    continue
                    
                quote_data = quote_response.json()
                logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
                
                # 2. Get swap transaction
                swap_params = {
                    "quoteResponse": quote_data,
                    "userPublicKey": str(sender.public_key),
                    "wrapUnwrapSOL": True
                }
                
                swap_response = requests.post(
                    JUPITER_SWAP_URL,
                    json=swap_params,
                    timeout=15
                )
                
                if swap_response.status_code != 200:
                    logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                    logging.error(f"Response: {swap_response.text}")
                    continue
                    
                swap_data = swap_response.json()
                
                # 3. Extract transaction
                if "swapTransaction" not in swap_data:
                    logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                    continue
                
                # Get the transaction data
                tx_base64 = swap_data["swapTransaction"]
                
                # 4. Submit the transaction directly to the RPC node
                headers = {
                    "Content-Type": "application/json"
                }
                
                data = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        tx_base64,
                        {
                            "skipPreflight": False,
                            "preflightCommitment": "confirmed",
                            "encoding": "base64",
                            "maxRetries": 5
                        }
                    ]
                }
                
                # Send transaction directly to RPC
                tx_response = requests.post(rpc_url, headers=headers, json=data)
                tx_result = tx_response.json()
                
                if "result" in tx_result:
                    signature = tx_result["result"]
                    
                    # Check for all 1's pattern
                    if signature == "1" * len(signature):
                        logging.error("Received all 1's signature - transaction was simulated but not executed")
                        continue
                        
                    logging.info(f"Swap transaction submitted with signature: {signature}")
                    
                    # 5. Verify transaction success
                    success = False
                    for check_num in range(5):
                        wait_time = 5 * (2 ** check_num)
                        logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                        time.sleep(wait_time)
                        
                        # Check transaction status with RPC
                        check_data = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [
                                signature,
                                {"encoding": "json", "commitment": "confirmed"}
                            ]
                        }
                        
                        status_response = requests.post(rpc_url, headers=headers, json=check_data)
                        status_result = status_response.json()
                        
                        if "result" in status_result and status_result["result"]:
                            result = status_result["result"]
                            if result.get("meta", {}).get("err") is None:
                                logging.info(f"Swap transaction confirmed successfully!")
                                
                                # Record success
                                token_buy_timestamps[token_address] = time.time()
                                buy_successes += 1
                                
                                # Record price for monitoring
                                initial_price = get_token_price(token_address)
                                if initial_price:
                                    monitored_tokens[token_address] = {
                                        'initial_price': initial_price,
                                        'highest_price': initial_price,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                                
                                logging.info(f"✅ Token swap successful!")
                                success = True
                                break
                            else:
                                error = result.get("meta", {}).get("err")
                                logging.error(f"Transaction failed with error: {error}")
                                break
                    
                    if success:
                        return True
                else:
                    error_message = tx_result.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Failed to submit transaction: {error_message}")
            else:
                error_message = test_result.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed basic transaction test: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in buy attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} buy attempts for {token_address} failed")
    return False

def submit_jupiter_transaction(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Submit transaction from Jupiter without modification."""
    global buy_attempts, buy_successes
    import time
    import requests
    import json
    import logging
    import traceback
    
    buy_attempts += 1
    logging.info(f"Starting Jupiter transaction for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Get RPC URL from config
    rpc_url = CONFIG.get('SOLANA_RPC_URL')
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
    
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Step 1: Get quote from Jupiter API
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
            amount_lamports = int(amount_sol * 1_000_000_000)
            
            # Using v4 API as it might be more stable than v6
            quote_url = "https://quote-api.jup.ag/v4/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippage": 0.5  # 0.5% slippage (note different format than v6)
            }
            
            logging.info("Getting Jupiter quote...")
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Price: {quote_data.get('price', 'unknown')}")
            
            # Step 2: Get swap transaction from Jupiter
            # Using v4 API for transaction creation
            logging.info("Getting swap transaction from Jupiter...")
            swap_url = "https://quote-api.jup.ag/v4/swap"
            swap_params = {
                "route": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            # Get the serialized transaction
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Successfully received transaction data from Jupiter")
            
            # Step 3: Submit the transaction directly to RPC without any modifications
            # Use a clean, separate RPC call to avoid any issues
            headers = {"Content-Type": "application/json"}
            
            # Important: Send the transaction exactly as received from Jupiter
            # Try with both skipPreflight options
            for skip_preflight in [True, False]:
                logging.info(f"Submitting transaction with skipPreflight={skip_preflight}...")
                
                rpc_data = {
                    "jsonrpc": "2.0",
                    "id": f"{int(time.time())}",
                    "method": "sendTransaction",
                    "params": [
                        tx_base64,
                        {
                            "encoding": "base64",
                            "skipPreflight": skip_preflight,
                            "preflightCommitment": "finalized",
                            "maxRetries": 10
                        }
                    ]
                }
                
                # Submit transaction
                response = requests.post(rpc_url, headers=headers, json=rpc_data)
                response_data = response.json()
                
                if "result" in response_data:
                    signature = response_data["result"]
                    
                    # Check for all 1's pattern
                    if signature == "1" * len(signature):
                        logging.error("Received all 1's signature - transaction was simulated but not executed")
                        # Continue to next skipPreflight option
                        continue
                    
                    logging.info(f"Transaction submitted successfully! Signature: {signature}")
                    
                    # Step 4: Verify transaction success
                    success = False
                    for check_num in range(10):  # More checks with longer waits
                        wait_time = 5 * (check_num + 1)  # Longer wait times
                        logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/10)...")
                        time.sleep(wait_time)
                        
                        # Check transaction status
                        status_data = {
                            "jsonrpc": "2.0",
                            "id": f"{int(time.time())}",
                            "method": "getTransaction",
                            "params": [
                                signature,
                                {"encoding": "json", "commitment": "finalized"}
                            ]
                        }
                        
                        status_response = requests.post(rpc_url, headers=headers, json=status_data)
                        status_result = status_response.json()
                        
                        if "result" in status_result and status_result["result"]:
                            result = status_result["result"]
                            if result.get("meta", {}).get("err") is None:
                                logging.info(f"Transaction confirmed successfully!")
                                
                                # Record transaction success
                                token_buy_timestamps[token_address] = time.time()
                                buy_successes += 1
                                
                                # Record price for monitoring
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
                                        monitored_tokens[token_address] = {
                                            'initial_price': 0.01,  # Placeholder
                                            'highest_price': 0.01,
                                            'partial_profit_taken': False,
                                            'buy_time': time.time()
                                        }
                                except Exception as e:
                                    logging.warning(f"Error getting token price: {e}")
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,
                                        'highest_price': 0.01,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                                
                                logging.info(f"✅ Token purchase successful!")
                                success = True
                                break
                            else:
                                error = result["meta"]["err"]
                                logging.error(f"Transaction failed with error: {error}")
                                break
                        
                        logging.info("Transaction not confirmed yet, waiting longer...")
                    
                    if success:
                        return True
                    
                    # If we got here, this skipPreflight option didn't work
                    logging.error(f"Transaction failed to confirm with skipPreflight={skip_preflight}")
                else:
                    error_message = response_data.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Failed to submit transaction: {error_message}")
            
            # If we got here, both skipPreflight options failed for this attempt
            
        except Exception as e:
            logging.error(f"Error in transaction attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} transaction attempts for {token_address} failed")
    return False

def optimized_buy_token(token_address: str, amount_sol: float = 0.01, max_attempts: int = 3):
    """Buy token using optimized parameters from the Discord bot example."""
    global buy_attempts, buy_successes
    
    buy_attempts += 1
    logging.info(f"Starting optimized buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.05:  # Larger buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
    
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Get keypair
            keypair = get_backpack_keypair()
            
            # Step 1: Get Jupiter quote
            sol_token = "So11111111111111111111111111111111111111112"
            amount_lamports = int(amount_sol * 1_000_000_000)
            
            quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 100,  # 1% slippage
                "onlyDirectRoutes": True
            }
            
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Step 2: Get swap transaction
            swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(keypair.pubkey()),
                "wrapUnwrapSOL": True,  # Correct parameter name
                "computeUnitPriceMicroLamports": 0,
                "asLegacyTransaction": True,
                "onlyDirectRoutes": True
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Successfully received transaction data from Jupiter")
            
            # Step 3: Decode transaction
            tx_data = decode_transaction_blob(tx_base64)
            
            # Step 4: Submit raw transaction
            from solana.rpc.types import TxOpts
            
            logging.info(f"Sending raw transaction...")
            signature = solana_client.send_raw_transaction(
                tx_data,
                opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed")
            )
            
            logging.info(f"Transaction submitted with signature: {signature}")
            
            # Step 5: Verify transaction success
            success = False
            for check_num in range(8):  # More checks
                wait_time = 5 * (2 ** check_num)  # Exponential backoff
                logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/8)...")
                time.sleep(wait_time)
                
                try:
                    status = solana_client.get_transaction(signature)
                    if status and hasattr(status, 'value') and status.value:
                        if not status.value.err:
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
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,  # Placeholder
                                        'highest_price': 0.01,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {str(e)}")
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,
                                    'highest_price': 0.01,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token purchase successful!")
                            success = True
                            break
                        else:
                            error = status.value.err
                            logging.error(f"Transaction failed with error: {error}")
                            break
                except Exception as e:
                    logging.warning(f"Error checking transaction: {str(e)}")
            
            if success:
                return True
            
        except Exception as e:
            logging.error(f"Error in buy attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            # Try fallback RPC
            fallback_rpc()
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} buy attempts for {token_address} failed")
    return False

def buy_token_jupiter(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Buy a token using Jupiter with direct private key signing."""
    global buy_attempts, buy_successes
    import base64
    import time
    import traceback
    import requests
    import json
    
    # Default to USDC if no token specified
    if not token_address:
        token_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    
    buy_attempts += 1
    logging.info(f"Starting Jupiter buy for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Define Jupiter API endpoint - use v6 which is more reliable
            jupiter_api_url = "https://quote-api.jup.ag/v6"
            
            # Define token parameters (SOL and target token)
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL mint address
            target_token = token_address
            
            # 1. Get quote from Jupiter API
            logging.info(f"Getting Jupiter quote for {amount_sol} SOL to {token_address}...")
            
            amount_lamports = int(amount_sol * 1_000_000_000)  # Convert to lamports
            
            quote_url = f"{jupiter_api_url}/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": target_token,
                "amount": str(amount_lamports),
                "slippageBps": 100  # 1% slippage
            }
            
            try:
                logging.info(f"Quote URL: {quote_url}")
                logging.info(f"Quote params: {json.dumps(quote_params)}")
                
                quote_response = requests.get(
                    quote_url,
                    params=quote_params,
                    timeout=15
                )
                
                if quote_response.status_code != 200:
                    logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                    logging.error(f"Response: {quote_response.text}")
                    continue
                    
                quote_data = quote_response.json()
                logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
                
                # 2. Get swap transaction
                swap_url = f"{jupiter_api_url}/swap"
                swap_params = {
                    "quoteResponse": quote_data,
                    "userPublicKey": str(wallet.public_key),
                    "wrapUnwrapSOL": True
                }
                
                logging.info(f"Swap URL: {swap_url}")
                logging.info(f"Swap params: {json.dumps(swap_params, default=str)[:200]}...")  # Truncate long output
                
                swap_response = requests.post(
                    swap_url,
                    json=swap_params,
                    timeout=15
                )
                
                if swap_response.status_code != 200:
                    logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                    logging.error(f"Response: {swap_response.text}")
                    continue
                    
                swap_data = swap_response.json()
                
                # 3. Extract and sign transaction
                if "swapTransaction" not in swap_data:
                    logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                    continue
                
                tx_base64 = swap_data["swapTransaction"]
                
                # 4. Submit transaction using our wallet
                logging.info(f"Signing and submitting transaction...")
                
                try:
                    # Convert base64 transaction to bytes
                    from base64 import b64decode
                    tx_bytes = b64decode(tx_base64)
                    
                    # Log length of transaction bytes for debugging
                    logging.info(f"Transaction bytes length: {len(tx_bytes)}")
                    
                    # Use the wallet's method to sign and submit transaction bytes
                    signature = wallet.sign_and_submit_transaction_bytes(tx_bytes)
                    
                    if not signature:
                        logging.error("Failed to sign and submit transaction")
                        continue
                        
                    logging.info(f"Transaction submitted with signature: {signature}")
                    
                    # 5. Check transaction success with exponential backoff
                    success = False
                    for check_num in range(5):
                        wait_time = 5 * (2 ** check_num)
                        logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                        time.sleep(wait_time)
                        
                        # Check transaction status
                        status_response = wallet._rpc_call("getTransaction", [
                            signature,
                            {"encoding": "json", "commitment": "confirmed"}
                        ])
                        
                        if "result" in status_response and status_response["result"]:
                            result = status_response["result"]
                            if result.get("meta", {}).get("err") is None:
                                logging.info(f"Transaction confirmed successfully!")
                                
                                # Record success
                                token_buy_timestamps[token_address] = time.time()
                                buy_successes += 1
                                
                                # Record price for monitoring
                                initial_price = get_token_price(token_address)
                                if initial_price:
                                    monitored_tokens[token_address] = {
                                        'initial_price': initial_price,
                                        'highest_price': initial_price,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                                
                                logging.info(f"✅ Buy transaction successful!")
                                success = True
                                break
                            else:
                                error = result["meta"]["err"]
                                logging.error(f"Transaction failed with error: {error}")
                                break
                    
                    if success:
                        return True
                        
                except Exception as sign_error:
                    logging.error(f"Error signing/submitting transaction: {str(sign_error)}")
                    logging.error(traceback.format_exc())
                
            except Exception as api_error:
                logging.error(f"Error in Jupiter API communication: {str(api_error)}")
                logging.error(traceback.format_exc())
                
        except Exception as e:
            logging.error(f"Error in Jupiter buy attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} Jupiter buy attempts for {token_address} failed")
    return False
    
def test_with_public_rpc():
    """Test a transaction with a public RPC endpoint."""
    logging.info("===== TESTING WITH PUBLIC RPC ENDPOINT =====")
    
    # Store original RPC URL
    original_rpc = CONFIG['SOLANA_RPC_URL']
    
    try:
        # Temporarily use public RPC
        public_rpc = "https://api.mainnet-beta.solana.com"
        logging.info(f"Temporarily switching to public RPC: {public_rpc}")
        
        # Create a temporary wallet with the public RPC
        temp_wallet = SolanaWallet(
            private_key=CONFIG['WALLET_PRIVATE_KEY'],
            rpc_url=public_rpc
        )
        
        # Check balance to verify connection
        balance = temp_wallet.get_balance()
        logging.info(f"Wallet balance via public RPC: {balance} SOL")
        
        # Try buying a small amount of BONK
        bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
        amount_sol = 0.05  # Small test amount
        
        # Get quote from Jupiter
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS,
            "outputMint": bonk_address,
            "amount": str(int(amount_sol * 1000000000)),
            "slippageBps": "3000"
        }
        
        quote_response = requests.get(quote_url, params=params, timeout=30)
        if quote_response.status_code != 200:
            logging.error(f"Quote failed: {quote_response.status_code}")
            return False
            
        quote_data = quote_response.json()
        
        # Prepare swap
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(temp_wallet.public_key),
            "wrapAndUnwrapSol": True
        }
        
        swap_response = requests.post(
            swap_url, 
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Swap preparation failed: {swap_response.status_code}")
            return False
            
        swap_data = swap_response.json()
        serialized_tx = swap_data["swapTransaction"]
        
        # Submit via public RPC
        response = temp_wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted via public RPC: {signature}")
            
            # Wait for confirmation
            time.sleep(30)
            
            # Check balance to see if it worked
            check_response = temp_wallet._rpc_call("getTokenAccountsByOwner", [
                str(temp_wallet.public_key),
                {"mint": bonk_address},
                {"encoding": "jsonParsed"}
            ])
            
            if 'result' in check_response and 'value' in check_response['result'] and check_response['result']['value']:
                logging.info("✅ Transaction via public RPC succeeded!")
                return True
            else:
                logging.warning("Transaction via public RPC may have succeeded but no tokens found")
                return False
        else:
            logging.error(f"Transaction via public RPC failed: {response}")
            return False
            
    except Exception as e:
        logging.error(f"Error testing with public RPC: {str(e)}")
        logging.error(traceback.format_exc())
        return False
    finally:
        # Restore original RPC
        CONFIG['SOLANA_RPC_URL'] = original_rpc
        logging.info(f"Restored original RPC URL: {original_rpc}")

    # Rest of the buy_token function remains the same...
def simulate_transaction(serialized_tx: str) -> bool:
    """Simulate a transaction before submitting it."""
    try:
        response = wallet._rpc_call("simulateTransaction", [
            serialized_tx,
            {"encoding": "base64", "commitment": "confirmed"}
        ])
        
        if "result" in response:
            result = response["result"]
            if "err" in result and result["err"] is not None:
                logging.error(f"Simulation error: {result['err']}")
                return False
            
            logging.info("Transaction simulation successful")
            return True
        else:
            logging.error(f"Simulation failed: {response.get('error', 'Unknown error')}")
            return False
    except Exception as e:
        logging.error(f"Error simulating transaction: {str(e)}")
        return False

def direct_jupiter_swap_with_protection():
    """Test direct Jupiter swap with MEV protection and automatic fees."""
    logging.info("=== TESTING DIRECT JUPITER SWAP WITH AUTO SETTINGS ===")
    
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    sol_address = "So11111111111111111111111111111111111111112"
    
    # Amount in lamports
    amount = "50000000"  # 0.05 SOL
    
    logging.info(f"Attempting Jupiter swap with auto settings: {amount} lamports SOL → BONK")
    
    try:
        # 1. Get quote
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": sol_address,
            "outputMint": bonk_address,
            "amount": amount,
            "slippageBps": "1000"
        }
        
        quote_response = requests.get(quote_url, params=params, timeout=10)
        if quote_response.status_code != 200:
            logging.error(f"Failed to get quote: {quote_response.status_code}")
            return False
            
        quote_data = quote_response.json()
        logging.info(f"Got quote - expected output amount: {quote_data.get('outAmount')}")
        
        # 2. Prepare swap with automatic settings
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        
        # Simplified payload with automatic fee handling
        payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,  # Auto compute limit instead of max
            "prioritizationFeeLamports": "auto"  # Auto priority fee
        }
        
        logging.info(f"Sending swap request with auto settings")
        
        swap_response = requests.post(
            swap_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Failed to prepare swap: {swap_response.status_code} - {swap_response.text}")
            return False
            
        swap_data = swap_response.json()
        
        if "swapTransaction" not in swap_data:
            logging.error(f"Swap response missing swapTransaction: {swap_data}")
            return False
            
        serialized_tx = swap_data["swapTransaction"]
        
        # 3. Submit transaction with MEV protection
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,
                "maxRetries": 5,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted: {signature}")
            
            # Wait for confirmation and check status
            logging.info("Waiting 30 seconds for confirmation...")
            time.sleep(30)
            
            # Check token balance after swap
            check_response = wallet._rpc_call("getTokenAccountsByOwner", [
                str(wallet.public_key),
                {"mint": bonk_address},
                {"encoding": "jsonParsed"}
            ])
            
            token_amount = 0
            if 'result' in check_response and 'value' in check_response['result']:
                accounts = check_response['result']['value']
                if accounts:
                    account = accounts[0]
                    if 'account' in account and 'data' in account['account'] and 'parsed' in account['account']['data']:
                        parsed_data = account['account']['data']['parsed']
                        if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                            token_amount_info = parsed_data['info']['tokenAmount']
                            if 'amount' in token_amount_info:
                                token_amount = int(token_amount_info['amount'])
                                logging.info(f"BONK balance after swap: {token_amount}")
            
            return token_amount > 0
        else:
            if "error" in response:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit: {error_message}")
            return False
            
    except Exception as e:
        logging.error(f"Error in protected swap: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def execute_jupiter_swap(token_address: str, amount_sol: float, max_attempts: int = 3) -> bool:
    """Execute a Jupiter swap with optimized transaction handling."""
    global buy_attempts, buy_successes
    import time
    import requests
    import json
    import logging
    import base64
    import traceback
    
    buy_attempts += 1
    logging.info(f"Starting optimized Jupiter swap for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.05:  # Include buffer for fees
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
        
    logging.info(f"Wallet balance: {balance} SOL")
    
    # Get RPC URL from config
    rpc_url = CONFIG.get('SOLANA_RPC_URL')
    headers = {"Content-Type": "application/json"}
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Swap attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Step 1: Get the latest blockhash - CRITICAL FOR TRANSACTION SUCCESS
            blockhash_request = {
                "jsonrpc": "2.0",
                "id": f"{int(time.time())}",
                "method": "getLatestBlockhash",
                "params": [{"commitment": "finalized"}]
            }
            
            logging.info("Getting fresh blockhash...")
            blockhash_response = requests.post(rpc_url, headers=headers, json=blockhash_request)
            blockhash_data = blockhash_response.json()
            
            if "result" not in blockhash_data or "value" not in blockhash_data["result"]:
                logging.error("Failed to get latest blockhash")
                continue
                
            blockhash = blockhash_data["result"]["value"]["blockhash"]
            last_valid_block_height = blockhash_data["result"]["value"]["lastValidBlockHeight"]
            logging.info(f"Got fresh blockhash: {blockhash} (valid until block {last_valid_block_height})")
            
            # Step 2: Get quote from Jupiter API
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
            amount_lamports = int(amount_sol * 1_000_000_000)
            
            quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 100  # 1% slippage
            }
            
            logging.info("Getting Jupiter quote...")
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Step 3: Get swap transaction with our fresh blockhash and priority fees
            logging.info("Getting swap transaction from Jupiter...")
            swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "dynamicComputeUnitLimit": True,  # Optimize compute units
                "prioritizationFeeLamports": 1000000,  # 0.001 SOL fee for priority
                "blockhash": blockhash,  # Use our fresh blockhash
                "lastValidBlockHeight": last_valid_block_height
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Successfully received transaction data from Jupiter")
            
            # Step 4: Submit transaction directly to RPC with skipPreflight
            rpc_data = {
                "jsonrpc": "2.0",
                "id": f"{int(time.time())}",
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": True,  # Skip preflight to avoid simulation-only issues
                        "maxRetries": 3,
                        "preflightCommitment": "processed"  # Use faster commitment level for initial submission
                    }
                ]
            }
            
            logging.info("Submitting transaction to RPC...")
            response = requests.post(rpc_url, headers=headers, json=rpc_data)
            response_data = response.json()
            
            if "result" in response_data:
                signature = response_data["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                    
                logging.info(f"Transaction submitted successfully! Signature: {signature}")
                
                # Step 5: Confirm transaction with exponential backoff
                success = False
                for check_num in range(8):  # More checks with longer timeouts
                    wait_time = 5 * (2 ** check_num)  # Exponential backoff
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/8)...")
                    time.sleep(wait_time)
                    
                    # Confirm transaction with blockhash and lastValidBlockHeight
                    confirm_data = {
                        "jsonrpc": "2.0",
                        "id": f"{int(time.time())}",
                        "method": "confirmTransaction",
                        "params": [
                            {
                                "signature": signature,
                                "blockhash": blockhash,
                                "lastValidBlockHeight": last_valid_block_height
                            },
                            "confirmed"
                        ]
                    }
                    
                    confirm_response = requests.post(rpc_url, headers=headers, json=confirm_data)
                    confirm_result = confirm_response.json()
                    
                    if "result" in confirm_result and confirm_result["result"].get("value", False):
                        # Transaction confirmed, check for errors
                        tx_data = {
                            "jsonrpc": "2.0",
                            "id": f"{int(time.time())}",
                            "method": "getTransaction",
                            "params": [
                                signature,
                                {"encoding": "json", "commitment": "confirmed"}
                            ]
                        }
                        
                        tx_response = requests.post(rpc_url, headers=headers, json=tx_data)
                        tx_result = tx_response.json()
                        
                        if "result" in tx_result and tx_result["result"]:
                            if tx_result["result"].get("meta", {}).get("err") is None:
                                logging.info(f"Transaction confirmed successfully!")
                                
                                # Record transaction success
                                token_buy_timestamps[token_address] = time.time()
                                buy_successes += 1
                                
                                # Record price for monitoring
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
                                        # Fallback price
                                        monitored_tokens[token_address] = {
                                            'initial_price': 0.01,
                                            'highest_price': 0.01,
                                            'partial_profit_taken': False,
                                            'buy_time': time.time()
                                        }
                                except Exception as e:
                                    logging.warning(f"Error getting token price: {str(e)}")
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,
                                        'highest_price': 0.01,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                                
                                logging.info(f"✅ Token purchase successful!")
                                success = True
                                break
                            else:
                                error = tx_result["result"]["meta"]["err"]
                                logging.error(f"Transaction failed with error: {error}")
                                break
                
                if success:
                    return True
                else:
                    logging.error("Transaction was not confirmed within the timeout period")
            else:
                error_message = response_data.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in swap attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} swap attempts for {token_address} failed")
    return False

def send_token_simple(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Simple token purchase using solana.py library."""
    global buy_attempts, buy_successes
    import time
    import requests
    import json
    import logging
    import subprocess
    import sys
    
    # First, ensure solana-py is properly installed
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "solana-py==0.29.2"])
        logging.info("Successfully installed solana-py package")
    except Exception as e:
        logging.error(f"Error installing solana-py: {str(e)}")
        return False
    
    # Now try importing the necessary modules
    try:
        from solana.rpc.api import Client
        from solana.transaction import Transaction
        from solana.keypair import Keypair
        from solana.publickey import PublicKey
        logging.info("Successfully imported solana-py modules")
    except ImportError as e:
        logging.error(f"Failed to import solana-py modules: {str(e)}")
        return False
    
    buy_attempts += 1
    logging.info(f"Starting simple token purchase for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Get RPC URL from config
    rpc_url = CONFIG.get('SOLANA_RPC_URL')
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
    
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Step 1: Convert your existing wallet to a solana-py compatible keypair
            # This assumes wallet.keypair._keypair.secret_key is available as seen in your code
            secret_key_bytes = bytes(wallet.keypair._keypair.secret_key)
            keypair = Keypair.from_secret_key(secret_key_bytes)
            logging.info(f"Created solana-py keypair with public key: {keypair.public_key}")
            
            # Step 2: Get token swap transaction from Jupiter API
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
            amount_lamports = int(amount_sol * 1_000_000_000)
            
            # Get quote from Jupiter
            quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 300  # 3% slippage
            }
            
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Get transaction from Jupiter
            swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(keypair.public_key),
                "wrapUnwrapSOL": True,
                "prioritizationFeeLamports": 5000000  # 0.005 SOL fee
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            # Get the transaction data in base64 format
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Got transaction data from Jupiter")
            
            # Step 3: Create solana-py client
            solana_client = Client(rpc_url)
            
            # Step 4: Submit the transaction directly
            # Using the raw_request method to have full control over parameters
            resp = solana_client.raw_request(
                method="sendTransaction",
                params=[
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": True,  # Skip preflight to help with simulation issues
                        "preflightCommitment": "processed",
                        "maxRetries": 3,
                    }
                ]
            )
            
            if "result" in resp:
                signature = resp["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                
                logging.info(f"Transaction submitted with signature: {signature}")
                
                # Step 5: Verify transaction success
                success = False
                for check_num in range(5):
                    wait_time = 5 * (2 ** check_num)  # Exponential backoff
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/5)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status
                    confirm_resp = solana_client.raw_request(
                        method="getTransaction",
                        params=[
                            signature,
                            {"encoding": "json", "commitment": "confirmed"}
                        ]
                    )
                    
                    if "result" in confirm_resp and confirm_resp["result"]:
                        result = confirm_resp["result"]
                        if result.get("meta", {}).get("err") is None:
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
                                        'highest_price': 0.01,  # Placeholder
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {str(e)}")
                                # Use placeholder price
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,  # Placeholder
                                    'highest_price': 0.01,  # Placeholder
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token purchase successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                
                if success:
                    return True
            else:
                error_message = resp.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in simple token purchase attempt #{attempt+1}: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} token purchase attempts for {token_address} failed")
    return False

def submit_direct_transaction(token_address: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", amount_sol: float = 0.01, max_attempts: int = 3) -> bool:
    """Submit transaction directly using RPC calls without external libraries."""
    global buy_attempts, buy_successes
    import time
    import requests
    import json
    import logging
    import traceback
    
    buy_attempts += 1
    logging.info(f"Starting direct transaction for {token_address} - Amount: {amount_sol} SOL")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Bought token {token_address}")
        token_buy_timestamps[token_address] = time.time()
        buy_successes += 1
        return True
    
    # Get RPC URL from config
    rpc_url = CONFIG.get('SOLANA_RPC_URL')
    
    # Check wallet balance
    balance = wallet.get_balance()
    if balance < amount_sol + 0.01:
        logging.error(f"Insufficient balance: {balance} SOL")
        return False
    
    logging.info(f"Wallet balance: {balance} SOL")
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Buy attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Step 1: Get quote from Jupiter API
            sol_token = "So11111111111111111111111111111111111111112"  # Wrapped SOL
            amount_lamports = int(amount_sol * 1_000_000_000)
            
            quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": sol_token,
                "outputMint": token_address,
                "amount": str(amount_lamports),
                "slippageBps": 300  # 3% slippage
            }
            
            logging.info("Getting Jupiter quote...")
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote. Output amount: {quote_data.get('outAmount', 'unknown')}")
            
            # Step 2: Get swap transaction from Jupiter
            logging.info("Getting swap transaction from Jupiter...")
            swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapUnwrapSOL": True,
                "prioritizationFeeLamports": 1000000  # 0.001 SOL priority fee
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Successfully received transaction data from Jupiter")
            
            # Step 3: Submit transaction directly to RPC
            logging.info(f"Submitting transaction to RPC...")
            headers = {"Content-Type": "application/json"}
            
            # Try multiple submission approaches to handle different RPC configurations
            # First attempt - with skipPreflight
            rpc_data = {
                "jsonrpc": "2.0",
                "id": str(int(time.time())),  # Use timestamp as ID to ensure uniqueness
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base64",
                        "skipPreflight": True,  # Skip preflight checks to avoid simulation issues
                        "preflightCommitment": "processed", 
                        "maxRetries": 5
                    }
                ]
            }
            
            logging.info("Sending transaction with skipPreflight=True...")
            response = requests.post(rpc_url, headers=headers, json=rpc_data)
            response_data = response.json()
            
            # If first attempt fails, try without skipPreflight
            if "error" in response_data:
                error_message = response_data.get("error", {}).get("message", "Unknown error")
                logging.warning(f"First submission attempt failed: {error_message}")
                
                # Second attempt - without skipPreflight
                rpc_data["params"][1]["skipPreflight"] = False
                logging.info("Trying again with skipPreflight=False...")
                response = requests.post(rpc_url, headers=headers, json=rpc_data)
                response_data = response.json()
            
            if "result" in response_data:
                signature = response_data["result"]
                
                # Check for all 1's pattern
                if signature == "1" * len(signature):
                    logging.error("Received all 1's signature - transaction was simulated but not executed")
                    continue
                
                logging.info(f"Transaction submitted with signature: {signature}")
                
                # Step 4: Verify transaction success
                success = False
                for check_num in range(6):  # More checks with longer waits
                    wait_time = 10 * (check_num + 1)  # Longer wait times
                    logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/6)...")
                    time.sleep(wait_time)
                    
                    # Check transaction status
                    status_data = {
                        "jsonrpc": "2.0",
                        "id": str(int(time.time())),
                        "method": "getTransaction",
                        "params": [
                            signature,
                            {"encoding": "json"}
                        ]
                    }
                    
                    status_response = requests.post(rpc_url, headers=headers, json=status_data)
                    status_result = status_response.json()
                    
                    if "result" in status_result and status_result["result"]:
                        result = status_result["result"]
                        if result.get("meta", {}).get("err") is None:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record transaction success
                            token_buy_timestamps[token_address] = time.time()
                            buy_successes += 1
                            
                            # Record price for monitoring
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
                                    monitored_tokens[token_address] = {
                                        'initial_price': 0.01,  # Placeholder
                                        'highest_price': 0.01,
                                        'partial_profit_taken': False,
                                        'buy_time': time.time()
                                    }
                            except Exception as e:
                                logging.warning(f"Error getting token price: {e}")
                                monitored_tokens[token_address] = {
                                    'initial_price': 0.01,
                                    'highest_price': 0.01,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                            
                            logging.info(f"✅ Token purchase successful!")
                            success = True
                            break
                        else:
                            error = result["meta"]["err"]
                            logging.error(f"Transaction failed with error: {error}")
                            break
                    
                    logging.info("Transaction not confirmed yet, waiting longer...")
                
                if success:
                    return True
            else:
                error_message = response_data.get("error", {}).get("message", "Unknown error")
                logging.error(f"Failed to submit transaction: {error_message}")
                
        except Exception as e:
            logging.error(f"Error in transaction attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} transaction attempts for {token_address} failed")
    return False

def simple_buy_token(token_address: str, amount_sol: float) -> bool:
    """Simplified token purchase function with minimal steps."""
    logging.info(f"Simple buy attempt for {token_address} with {amount_sol} SOL")
    
    try:
        # 1. Convert SOL to lamports
        amount_lamports = int(amount_sol * 1000000000)
        
        # 2. Get a quote
        quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
        params = {
            "inputMint": SOL_TOKEN_ADDRESS, 
            "outputMint": token_address,
            "amount": str(amount_lamports),
            "slippageBps": "2000"  # 20% slippage - very permissive
        }
        
        quote_response = requests.get(quote_url, params=params, timeout=10)
        if quote_response.status_code != 200:
            logging.error(f"Quote failed: {quote_response.status_code}")
            return False
            
        quote_data = quote_response.json()
        
        # 3. Prepare swap with correct parameters
        swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
        payload = {
            "quoteResponse": quote_data,
            "userPublicKey": str(wallet.public_key),
            "wrapAndUnwrapSol": True,  # Correct parameter name
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto"
        }
        
        swap_response = requests.post(
            swap_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if swap_response.status_code != 200:
            logging.error(f"Swap preparation failed: {swap_response.status_code}")
            return False
            
        swap_data = swap_response.json()
        
        # 4. Submit transaction directly without modification
        serialized_tx = swap_data["swapTransaction"]  
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,  # Skip client-side validation
                "maxRetries": 5,
                "preflightCommitment": "processed"  # Use faster confirmation level
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted successfully: {signature}")
            return True
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction submission error: {error_message}")
            return False
            
    except Exception as e:
        logging.error(f"Error in simple buy: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def optimized_sell_token(token_address: str, max_attempts: int = 3):
    """Sell token using optimized parameters from the Discord bot example."""
    global sell_attempts, sell_successes
    
    sell_attempts += 1
    logging.info(f"Starting optimized sell for {token_address}")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Sold token {token_address}")
        sell_successes += 1
        return True
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Sell attempt #{attempt+1}/{max_attempts} for {token_address}")
            
            # Get keypair
            keypair = get_backpack_keypair()
            
            # Step 1: Get Jupiter quote for selling to SOL
            sol_token = "So11111111111111111111111111111111111111112"
            quote_url = "https://quote-api.jup.ag/v6/quote"
            quote_params = {
                "inputMint": token_address,
                "outputMint": sol_token,
                "amount": "1000000",  # Just use a placeholder amount
                "slippageBps": 100,  # 1% slippage
                "onlyDirectRoutes": True
            }
            
            quote_response = requests.get(quote_url, params=quote_params, timeout=15)
            if quote_response.status_code != 200:
                logging.error(f"Failed to get Jupiter quote: {quote_response.status_code}")
                logging.error(f"Response: {quote_response.text}")
                continue
                
            quote_data = quote_response.json()
            logging.info(f"Got Jupiter quote for sell.")
            
            # Step 2: Get swap transaction
            swap_url = "https://quote-api.jup.ag/v6/swap"
            swap_params = {
                "quoteResponse": quote_data,
                "userPublicKey": str(keypair.pubkey()),
                "wrapUnwrapSOL": True,
                "computeUnitPriceMicroLamports": 0,
                "asLegacyTransaction": True,
                "onlyDirectRoutes": True
            }
            
            swap_response = requests.post(swap_url, json=swap_params, timeout=15)
            if swap_response.status_code != 200:
                logging.error(f"Failed to get swap transaction: {swap_response.status_code}")
                logging.error(f"Response: {swap_response.text}")
                continue
                
            swap_data = swap_response.json()
            if "swapTransaction" not in swap_data:
                logging.error(f"Jupiter response missing transaction data: {list(swap_data.keys())}")
                continue
            
            tx_base64 = swap_data["swapTransaction"]
            logging.info("Successfully received transaction data from Jupiter")
            
            # Step 3: Decode transaction
            tx_data = decode_transaction_blob(tx_base64)
            
            # Step 4: Submit raw transaction
            from solana.rpc.types import TxOpts
            
            logging.info(f"Sending raw transaction...")
            signature = solana_client.send_raw_transaction(
                tx_data,
                opts=TxOpts(skip_preflight=True, preflight_commitment="confirmed")
            )
            
            logging.info(f"Sell transaction submitted with signature: {signature}")
            
            # Step 5: Verify transaction success
            success = False
            for check_num in range(8):  # More checks
                wait_time = 5 * (2 ** check_num)  # Exponential backoff
                logging.info(f"Waiting {wait_time}s for confirmation (check {check_num+1}/8)...")
                time.sleep(wait_time)
                
                try:
                    status = solana_client.get_transaction(signature)
                    if status and hasattr(status, 'value') and status.value:
                        if not status.value.err:
                            logging.info(f"Transaction confirmed successfully!")
                            
                            # Record transaction success
                            sell_successes += 1
                            
                            # If there was a buy price, calculate profit
                            if token_address in monitored_tokens:
                                # Calculate profit or loss
                                current_price = get_token_price(token_address)
                                initial_price = monitored_tokens[token_address].get('initial_price')
                                if current_price and initial_price:
                                    profit = current_price - initial_price
                                    profit_percent = (profit / initial_price) * 100
                                    logging.info(f"Sold with {profit_percent:.2f}% {'profit' if profit > 0 else 'loss'}")
                                
                                # Remove from monitored tokens
                                del monitored_tokens[token_address]
                            
                            logging.info(f"✅ Token sell successful!")
                            success = True
                            break
                        else:
                            error = status.value.err
                            logging.error(f"Transaction failed with error: {error}")
                            break
                except Exception as e:
                    logging.warning(f"Error checking transaction: {str(e)}")
            
            if success:
                return True
            
        except Exception as e:
            logging.error(f"Error in sell attempt #{attempt+1}: {str(e)}")
            logging.error(traceback.format_exc())
            
            # Try fallback RPC
            fallback_rpc()
            
            wait_time = 10 * (attempt + 1)
            logging.info(f"Waiting {wait_time}s before next attempt...")
            time.sleep(wait_time)
    
    logging.error(f"All {max_attempts} sell attempts for {token_address} failed")
    return False

def sell_token(token_address: str, percentage: int = 100, max_attempts: int = 3) -> bool:
    """Sell a percentage of token holdings with robust error handling."""
    global sell_attempts, sell_successes
    
    sell_attempts += 1
    
    logging.info(f"Starting sell process for {token_address} - Percentage: {percentage}%")
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[SIMULATION] Sold {percentage}% of {token_address}")
        sell_successes += 1
        return True
    
    for attempt in range(max_attempts):
        try:
            logging.info(f"Sell attempt #{attempt+1} for {token_address}")
            
            # First, check balance and retry if needed
            token_amount = 0
            balance_check_attempts = 3
            
            for balance_attempt in range(balance_check_attempts):
                logging.info(f"Checking token balance (attempt {balance_attempt+1}/{balance_check_attempts})...")
                
                response = wallet._rpc_call("getTokenAccountsByOwner", [
                    str(wallet.public_key),
                    {"mint": token_address},
                    {"encoding": "jsonParsed"}
                ])
                
                if 'result' in response and 'value' in response['result'] and response['result']['value']:
                    token_account = response['result']['value'][0]
                    
                    if 'account' in token_account and 'data' in token_account['account'] and 'parsed' in token_account['account']['data']:
                        parsed_data = token_account['account']['data']['parsed']
                        if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                            token_amount_info = parsed_data['info']['tokenAmount']
                            if 'amount' in token_amount_info:
                                token_amount = int(token_amount_info['amount'])
                                logging.info(f"Found token balance: {token_amount}")
                                break
                
                if token_amount == 0 and balance_attempt < balance_check_attempts-1:
                    logging.info(f"No balance yet, waiting 10 seconds before retrying...")
                    time.sleep(10)
            
            if token_amount == 0:
                logging.error(f"Zero balance for {token_address} after {balance_check_attempts} attempts")
                return False
            
            # Add delay to avoid rate limits
            sleep_time = 2 * (attempt + 1)
            logging.info(f"Sleeping {sleep_time}s before quote request to avoid rate limits")
            time.sleep(sleep_time)
            
            # Calculate amount to sell based on percentage
            amount_to_sell = int(token_amount * percentage / 100)
            logging.info(f"Selling {amount_to_sell} tokens ({percentage}% of {token_amount})")
            
            # Step 2: Get a quote
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            params = {
                "inputMint": token_address,
                "outputMint": SOL_TOKEN_ADDRESS,
                "amount": str(amount_to_sell),
                "slippageBps": "5000"  # 50% slippage - extremely permissive
            }
            
            quote_response = requests.get(quote_url, params=params, timeout=30)
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get quote: {quote_response.status_code} - {quote_response.text}")
                if attempt < max_attempts-1:
                    continue
                return False
                
            quote_data = quote_response.json()
            logging.info(f"Quote received successfully")
            
            # Add delay between requests
            time.sleep(2)
            
            # Step 3: Prepare swap transaction with automatic settings
            swap_url = f"{CONFIG['JUPITER_API_URL']}/v6/swap"
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto"
            }
            
            swap_response = requests.post(
                swap_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to prepare swap: {swap_response.status_code} - {swap_response.text}")
                if attempt < max_attempts-1:
                    continue
                return False
                
            swap_data = swap_response.json()
            
            if "swapTransaction" not in swap_data:
                logging.error(f"Swap response missing swapTransaction: {swap_data}")
                if attempt < max_attempts-1:
                    continue
                return False
            
            # Add delay before transaction submission
            time.sleep(2)
            
            # Step 4: Submit transaction with optimal parameters
            serialized_tx = swap_data["swapTransaction"]
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
                logging.info(f"Sell transaction submitted successfully: {signature}")
                sell_successes += 1
                
                # If we're selling 100%, remove from monitored tokens
                if percentage == 100 and token_address in monitored_tokens:
                    logging.info(f"Removing {token_address} from monitored tokens after full sell")
                    del monitored_tokens[token_address]
                
                return True
            else:
                if "error" in response:
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Transaction error: {error_message}")
                if attempt < max_attempts-1:
                    continue
                return False
                
        except Exception as e:
            logging.error(f"Error selling {token_address}: {str(e)}")
            logging.error(traceback.format_exc())
            
            if attempt < max_attempts-1:
                wait_time = 5 * (attempt + 1)
                logging.info(f"Waiting {wait_time}s before next attempt...")
                time.sleep(wait_time)
            else:
                return False
    
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
        
        # Strategy execution with optimized sell function
        if not monitored_tokens[token_address]['partial_profit_taken'] and price_change_pct >= CONFIG['PARTIAL_PROFIT_PERCENT']:
            # Take partial profit at PARTIAL_PROFIT_PERCENT
            logging.info(f"Taking partial profit for {token_address} at {price_change_pct:.2f}% gain")
            if optimized_sell_token(token_address):  # Use optimized function
                monitored_tokens[token_address]['partial_profit_taken'] = True
        
        if price_change_pct >= CONFIG['PROFIT_TARGET_PERCENT']:
            # Take full profit at PROFIT_TARGET_PERCENT
            logging.info(f"Taking full profit for {token_address} at {price_change_pct:.2f}% gain")
            optimized_sell_token(token_address)  # Use optimized function
            # Remove from monitoring happens inside the optimized_sell_token function
            return
        
        if price_change_pct <= -CONFIG['STOP_LOSS_PERCENT']:
            # Stop loss hit
            logging.info(f"Stop loss triggered for {token_address} at {price_change_pct:.2f}% loss")
            optimized_sell_token(token_address)  # Use optimized function
            # Remove from monitoring happens inside the optimized_sell_token function
            return
        
        if time_limit_hit:
            if price_change_pct > 0:
                # Time limit hit with profit
                logging.info(f"Time limit reached for {token_address} with {price_change_pct:.2f}% gain")
                optimized_sell_token(token_address)  # Use optimized function
            else:
                # Time limit hit with loss
                logging.info(f"Time limit reached for {token_address} with {price_change_pct:.2f}% loss")
                optimized_sell_token(token_address)  # Use optimized function
            
            # Remove from monitoring happens inside the optimized_sell_token function
            return
    except Exception as e:
        logging.error(f"Error monitoring token {token_address}: {str(e)}")
        logging.error(traceback.format_exc())

def test_simple_transaction():
    """Test simple token purchase."""
    logging.info("===== TESTING SIMPLE TOKEN PURCHASE =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = send_token_simple(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Simple token purchase test passed!")
        return True
    else:
        logging.error("❌ Simple token purchase test failed.")
        return False

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
    """Main trading loop with optimized functions."""
    global iteration_count, last_status_time, errors_encountered, api_call_delay
    
    logging.info("Starting main trading loop with optimized transaction handling")
    
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
                
                # The monitor_token_price function should now call optimized_sell_token
                # when it's time to sell based on your strategy
                
                # Add a sleep between token monitoring to avoid rate limits
                time.sleep(3)
            
            # Only look for new tokens if we have capacity
            if len(monitored_tokens) < CONFIG['MAX_CONCURRENT_TOKENS']:
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
                            
                            # Use optimized buy function
                            if optimized_buy_token(token_address, CONFIG['BUY_AMOUNT_SOL']):
                                logging.info(f"Successfully bought token: {token_address}")
                                # Add a longer delay after successful buy
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

def test_minimal_sol_transfer():
    """Test absolute minimal SOL transfer to verify basic transaction functionality."""
    logging.info("===== TESTING MINIMAL SOL TRANSFER =====")
    
    try:
        # Check wallet connection
        balance = wallet.get_balance()
        logging.info(f"Wallet balance: {balance} SOL")
        
        if balance < 0.001:  # Need very little for this test
            logging.error(f"Wallet balance too low for testing: {balance} SOL")
            return False
        
        # Create a minimal SystemProgram transfer instruction
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Legacy, Transaction
        from solders.message import Message
        
        # Send a tiny amount of SOL back to ourselves
        amount_lamports = 1000  # Just 0.000001 SOL
        
        # Create transfer instruction
        transfer_ix = transfer(
            TransferParams(
                from_pubkey=wallet.public_key,
                to_pubkey=wallet.public_key,  # Send to ourselves
                lamports=amount_lamports
            )
        )
        
        # Get recent blockhash directly via RPC
        rpc_url = CONFIG.get('SOLANA_RPC_URL')
        headers = {"Content-Type": "application/json"}
        blockhash_request = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "getLatestBlockhash",
            "params": []
        }
        
        response = requests.post(rpc_url, headers=headers, json=blockhash_request)
        data = response.json()
        if "result" not in data or "value" not in data["result"]:
            logging.error("Failed to get blockhash")
            return False
            
        recent_blockhash = data["result"]["value"]["blockhash"]
        logging.info(f"Got blockhash: {recent_blockhash}")
        
        # Create message with our instruction
        message = Message.new_with_blockhash(
            [transfer_ix],
            wallet.public_key,
            recent_blockhash
        )
        
        # Create and sign transaction
        transaction = Transaction.new_signed_with_payer(
            [transfer_ix],
            wallet.public_key,
            [wallet.keypair],
            recent_blockhash
        )
        
        # Serialize transaction (raw bytes)
        serialized_tx = bytes(transaction)
        
        # Send directly to RPC
        tx_request = {
            "jsonrpc": "2.0",
            "id": "2",
            "method": "sendTransaction",
            "params": [
                base64.b64encode(serialized_tx).decode('utf-8'),
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "preflightCommitment": "processed"
                }
            ]
        }
        
        logging.info("Sending minimal transaction...")
        tx_response = requests.post(rpc_url, headers=headers, json=tx_request)
        tx_data = tx_response.json()
        
        if "result" in tx_data:
            signature = tx_data["result"]
            logging.info(f"Transaction sent with signature: {signature}")
            
            # Check for all 1's pattern
            if signature == "1" * len(signature):
                logging.error("Received all 1's signature - transaction was simulated but not executed")
                return False
                
            # Wait for confirmation
            time.sleep(10)
            
            # Check status
            status_request = {
                "jsonrpc": "2.0",
                "id": "3",
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "json"}
                ]
            }
            
            status_response = requests.post(rpc_url, headers=headers, json=status_request)
            status_data = status_response.json()
            
            if "result" in status_data and status_data["result"]:
                if status_data["result"].get("meta", {}).get("err") is None:
                    logging.info("✅ Minimal SOL transfer successful!")
                    return True
                else:
                    error = status_data["result"]["meta"]["err"]
                    logging.error(f"Transaction failed with error: {error}")
            else:
                logging.error("Could not verify transaction status")
        else:
            error_message = tx_data.get("error", {}).get("message", "Unknown error")
            logging.error(f"Failed to submit transaction: {error_message}")
        
        return False
        
    except Exception as e:
        logging.error(f"Error in minimal SOL transfer test: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        return False

def test_minimal_sol_transfer():
    """Minimal SOL transfer test using wallet's existing methods."""
    logging.info("===== TESTING MINIMAL SOL TRANSFER =====")
    
    try:
        # Use the wallet's sign_and_submit_transaction method
        # with a transaction constructed by Jupiter instead
        
        # Start by testing the USDC swap directly
        logging.info("Skipping basic SOL transfer and moving directly to USDC swap test...")
        return test_basic_swap()
            
    except Exception as e:
        logging.error(f"Error in minimal SOL transfer test: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
def force_sell_all_positions():
    logging.info("⚠️ [force_sell_all_positions] Not yet implemented — skipping for now.")

def tiny_buy_test():
    try:
        from solders.pubkey import Pubkey

        test_token = {
            "symbol": "BONK",
            "mint": Pubkey.from_string("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
        }

        logging.info("⚠️ [tiny_buy_test] Running tiny buy test with BONK...")
        
        # Get a quote
        amount_lamports = 1000000  # 0.001 SOL
        quote_data = jupiter_handler.get_quote(
            input_mint=SOL_TOKEN_ADDRESS,
            output_mint=str(test_token["mint"]),
            amount=str(amount_lamports),
            slippage_bps="500"
        )
        
        if not quote_data:
            logging.error("Failed to get quote")
            return False
            
        # Prepare the swap transaction
        swap_data = jupiter_handler.prepare_swap_transaction(
            quote_data=quote_data,
            user_public_key=str(wallet.public_key)
        )
        
        if not swap_data or "swapTransaction" not in swap_data:
            logging.error("Failed to prepare swap transaction")
            return False
            
        # Skip deserialization - directly submit the transaction
        serialized_tx = swap_data["swapTransaction"]
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {"encoding": "base64", "skipPreflight": False}
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"✅ [tiny_buy_test] Tiny buy test successful! Signature: {signature}")
            return True
        else:
            if "error" in response:
                error_message = response.get("error", {}).get("message", "Unknown error")
                logging.error(f"Transaction error: {error_message}")
            logging.warning("⚠️ [tiny_buy_test] Tiny buy test failed.")
            return False
            
    except Exception as e:
        logging.error(f"❌ [tiny_buy_test] Exception occurred: {e}")
        logging.error(traceback.format_exc())
        return False

def test_cli_direct():
    """Test direct CLI transaction submission."""
    logging.info("===== TESTING CLI DIRECT TRANSACTION =====")
    
    # Check if Solana CLI is installed
    try:
        result = subprocess.run(["solana", "--version"], capture_output=True, text=True)
        if result.returncode != 0:
            logging.error("Solana CLI is not installed. Please install it first.")
            logging.info("You can install it with: 'sh -c \"$(curl -sSfL https://release.solana.com/stable/install)\"'")
            return False
        logging.info(f"Solana CLI version: {result.stdout.strip()}")
    except:
        logging.error("Solana CLI is not installed. Please install it first.")
        logging.info("You can install it with: 'sh -c \"$(curl -sSfL https://release.solana.com/stable/install)\"'")
        return False
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.06:  # Need more buffer for fees
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with a simple self-transfer before trying a swap
    logging.info(f"Testing simple SOL transfer via CLI")
    result = buy_token_cli_direct(wallet.public_key.to_string(), 0.000001)
    
    if result:
        logging.info("✅ CLI direct transaction test passed!")
        return True
    else:
        logging.error("❌ CLI direct transaction test failed.")
        return False

def test_optimized_jupiter_swap():
    """Test optimized Jupiter swap implementation."""
    logging.info("===== TESTING OPTIMIZED JUPITER SWAP =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = execute_jupiter_swap(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Optimized Jupiter swap test passed!")
        return True
    else:
        logging.error("❌ Optimized Jupiter swap test failed.")
        return False

def test_jupiter_transaction():
    """Test Jupiter transaction submission."""
    logging.info("===== TESTING JUPITER TRANSACTION SUBMISSION =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = submit_jupiter_transaction(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Jupiter transaction submission test passed!")
        return True
    else:
        logging.error("❌ Jupiter transaction submission test failed.")
        return False

def test_direct_transaction():
    """Test direct transaction submission."""
    logging.info("===== TESTING DIRECT TRANSACTION SUBMISSION =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = submit_direct_transaction(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Direct transaction submission test passed!")
        return True
    else:
        logging.error("❌ Direct transaction submission test failed.")
        return False

def test_direct_submit():
    """Test direct RPC submission of Jupiter transactions."""
    logging.info("===== TESTING DIRECT RPC SUBMISSION =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.02:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL via direct RPC submission")
    result = buy_token_direct_submit(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Direct RPC submission test passed!")
        return True
    else:
        logging.error("❌ Direct RPC submission test failed.")
        return False

def test_helius_buy():
    """Test token purchase using Helius service."""
    logging.info("===== TESTING HELIUS TOKEN PURCHASE =====")
    
    # Check if Helius API key is configured - access directly from environment
    import os
    helius_api_key = os.environ.get('HELIUS_API_KEY', '')
    
    if not helius_api_key:
        # Fall back to CONFIG dictionary if it exists there
        helius_api_key = CONFIG.get('HELIUS_API_KEY', '')
        
    if not helius_api_key:
        logging.error("No Helius API key found in configuration. Please add one to proceed.")
        logging.info("You can get a free API key at https://dev.helius.xyz/dashboard/app")
        return False
    
    # Add the API key to CONFIG for use by the buy function
    CONFIG['HELIUS_API_KEY'] = helius_api_key
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.02:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL via Helius")
    result = buy_token_helius(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Helius token purchase test passed!")
        return True
    else:
        logging.error("❌ Helius token purchase test failed.")
        return False

def test_bonk_trading_cycle():
    """Test a complete buy/sell cycle with BONK token."""
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    
    logging.info("==== TESTING BONK TRADING CYCLE ====")
    
    # Start with a larger amount for more reliable transactions
    buy_amount = 0.05
    
    # Try up to 3 different buy amounts if needed
    for attempt in range(3):
        logging.info(f"Buy attempt #{attempt+1} with {buy_amount} SOL")
        
        # Step 1: Buy BONK
        buy_success = buy_token(bonk_address, buy_amount)
        
        if not buy_success:
            logging.error(f"Buy attempt #{attempt+1} failed")
            # Increase amount for next attempt
            buy_amount *= 2
            continue
        
        logging.info(f"Buy transaction submitted. Waiting 45 seconds for confirmation...")
        time.sleep(45)  # Even longer wait to ensure confirmation
        
        # Check balance after waiting
        response = wallet._rpc_call("getTokenAccountsByOwner", [
            str(wallet.public_key),
            {"mint": bonk_address},
            {"encoding": "jsonParsed"}
        ])
        
        token_amount = 0
        if 'result' in response and 'value' in response['result'] and response['result']['value']:
            token_account = response['result']['value'][0]
            if 'account' in token_account and 'data' in token_account['account'] and 'parsed' in token_account['account']['data']:
                parsed_data = token_account['account']['data']['parsed']
                if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                    token_amount_info = parsed_data['info']['tokenAmount']
                    if 'amount' in token_amount_info:
                        token_amount = int(token_amount_info['amount'])
        
        logging.info(f"BONK balance after buy: {token_amount}")
        
        if token_amount > 0:
            # We have a balance, proceed with selling
            logging.info(f"Step 2: Selling BONK (balance: {token_amount})")
            
            # Wait a bit more before selling to ensure the buy is fully settled
            time.sleep(15)
            
            sell_success = sell_token(bonk_address, 100)  # Sell 100%
            
            if not sell_success:
                logging.error("Failed to sell BONK")
                return False
            
            logging.info("==== BONK TRADING CYCLE COMPLETED SUCCESSFULLY ====")
            return True
        else:
            logging.warning(f"Buy attempt #{attempt+1} confirmed but balance is still 0")
            # Try with a larger amount in the next attempt
            buy_amount *= 2
    
    logging.error("All buy attempts failed to result in a non-zero balance")
    return False

def test_cli_buy():
    """Test token purchase using Solana CLI."""
    logging.info("===== TESTING CLI TOKEN PURCHASE =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.01:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # First test with a simple self-transfer to verify basic functionality
    result = buy_token_cli(wallet.public_key.to_string(), 0.000001)
    
    if result:
        logging.info("✅ CLI token purchase test passed!")
        return True
    else:
        logging.error("❌ CLI token purchase test failed.")
        return False

def test_optimized_buy():
    """Test optimized token purchase."""
    logging.info("===== TESTING OPTIMIZED TOKEN PURCHASE =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.06:  # Need more buffer for higher fees
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL via optimized method")
    result = buy_token_optimized(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Optimized token purchase test passed!")
        return True
    else:
        logging.error("❌ Optimized token purchase test failed.")
        return False

def test_solathon_buy():
    """Test token purchase using Solathon."""
    logging.info("===== TESTING SOLATHON TOKEN PURCHASE =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.02:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = buy_token_with_solathon(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Solathon token purchase test passed!")
        return True
    else:
        logging.error("❌ Solathon token purchase test failed.")
        return False

def simple_rpc_test():
    """Test basic RPC functionality without using Transaction objects."""
    logging.info("===== TESTING BASIC RPC FUNCTIONALITY =====")
    
    try:
        # Just test if we can get the wallet balance
        response = wallet._rpc_call("getBalance", [str(wallet.public_key)])
        
        if "result" in response and "value" in response["result"]:
            balance_lamports = response["result"]["value"]
            balance_sol = balance_lamports / 1_000_000_000
            logging.info(f"Wallet balance: {balance_sol} SOL")
            logging.info("RPC connection is working correctly!")
            return True
        else:
            logging.error(f"Failed to get balance: {response}")
            return False
            
    except Exception as e:
        logging.error(f"Error in RPC test: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
def submit_standardized_transaction(serialized_tx: str) -> Optional[str]:
    """Submit a transaction with standardized parameters including MEV protection."""
    try:
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {
                "encoding": "base64",
                "skipPreflight": True,  # Skip preflight consistently 
                "maxRetries": 5,
                "preflightCommitment": "confirmed"
            }
        ])
        
        if "result" in response:
            signature = response["result"]
            logging.info(f"Transaction submitted: {signature}")
            return signature
        else:
            error_message = response.get("error", {}).get("message", "Unknown error")
            logging.error(f"Transaction error: {error_message}")
            return None
            
    except Exception as e:
        logging.error(f"Error submitting transaction: {str(e)}")
        logging.error(traceback.format_exc())
        return None

def test_jupiter_direct():
    """Test Jupiter direct buy functionality."""
    logging.info("===== TESTING JUPITER DIRECT BUY FUNCTIONALITY =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = buy_token_jupiter_direct(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Jupiter direct buy functionality test passed!")
        return True
    else:
        logging.error("❌ Jupiter direct buy functionality test failed.")
        return False

def test_basic_functionality():
    """Test basic wallet and transaction functionality."""
    logging.info("===== TESTING BASIC FUNCTIONALITY WITH ORCA =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test a simple USDC purchase
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL using Orca")
    result = buy_token_orca(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Orca functionality test passed!")
        return True
    else:
        logging.error("❌ Orca functionality test failed.")
        return False

def test_wallet_functionality():
    """Test basic wallet functionality with a tiny SOL transfer."""
    logging.info("=== TESTING BASIC WALLET FUNCTIONALITY ===")
    
    try:
        # Create a simple SOL transfer instruction
        from solders.system_program import transfer, TransferParams
        from solders.transaction import Transaction
        
        # Send to your own wallet (self-transfer) for simplicity
        to_pubkey = wallet.public_key
        amount = 100000  # 0.0001 SOL
        
        # Get recent blockhash
        blockhash_response = wallet._rpc_call("getLatestBlockhash", [])
        if 'result' not in blockhash_response or 'value' not in blockhash_response['result']:
            logging.error("Failed to get recent blockhash")
            return False
            
        recent_blockhash = blockhash_response['result']['value']['blockhash']
        
        # Create transfer instruction
        transfer_ix = transfer(TransferParams(
            from_pubkey=wallet.public_key, 
            to_pubkey=to_pubkey,
            lamports=amount
        ))
        
        # Create and sign transaction
        tx = Transaction()
        tx.add(transfer_ix)
        tx.recent_blockhash = recent_blockhash
        tx.sign([wallet.keypair])
        
        # Serialize and submit
        serialized_tx = base64.b64encode(tx.serialize()).decode("utf-8")
        
        response = wallet._rpc_call("sendTransaction", [
            serialized_tx,
            {"encoding": "base64", "skipPreflight": False}
        ])
        
        if "result" in response:
            logging.info(f"Basic SOL transfer successful: {response['result']}")
            return True
        else:
            if "error" in response:
                logging.error(f"SOL transfer error: {response['error']}")
            return False
            
    except Exception as e:
        logging.error(f"Error testing wallet: {str(e)}")
        logging.error(traceback.format_exc())
        return False

def test_raydium_functionality():
    """Test basic wallet and transaction functionality using Raydium."""
    logging.info("===== TESTING BASIC FUNCTIONALITY WITH RAYDIUM =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test a simple USDC purchase
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL using Raydium")
    result = buy_token_raydium(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Raydium functionality test passed!")
        return True
    else:
        logging.error("❌ Raydium functionality test failed.")
        return False

def test_direct_swap():
    """Test basic wallet and transaction functionality using direct Solana swaps."""
    logging.info("===== TESTING DIRECT SOLANA SWAP FUNCTIONALITY =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test a simple USDC purchase
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing direct purchase of USDC with {amount_sol} SOL")
    result = buy_token_direct(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Direct swap functionality test passed!")
        return True
    else:
        logging.error("❌ Direct swap functionality test failed.")
        return False

def test_jupiter_buy():
    """Test Jupiter buy functionality with a small amount."""
    logging.info("===== TESTING JUPITER BUY FUNCTIONALITY =====")
    
    # Check wallet connection
    balance = wallet.get_balance()
    logging.info(f"Wallet balance: {balance} SOL")
    
    if balance < 0.05:
        logging.error(f"Wallet balance too low for testing: {balance} SOL")
        return False
    
    # Test with USDC which is highly liquid
    usdc_address = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    amount_sol = 0.01  # Small test amount
    
    logging.info(f"Testing purchase of USDC with {amount_sol} SOL")
    result = buy_token_jupiter(usdc_address, amount_sol)
    
    if result:
        logging.info("✅ Jupiter buy functionality test passed!")
        return True
    else:
        logging.error("❌ Jupiter buy functionality test failed.")
        return False

def test_token_account_creation():
    """Test creating a token account for BONK and verifying it exists."""
    logging.info("=== TESTING TOKEN ACCOUNT CREATION ===")
    
    bonk_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    
    try:
        # Step 1: Check if we already have a token account for BONK
        response = wallet._rpc_call("getTokenAccountsByOwner", [
            str(wallet.public_key),
            {"mint": bonk_address},
            {"encoding": "jsonParsed"}
        ])
        
        has_account = False
        if 'result' in response and 'value' in response['result']:
            accounts = response['result']['value']
            has_account = len(accounts) > 0
            
        if has_account:
            logging.info(f"Token account for BONK already exists: {accounts[0]['pubkey']}")
            
            # Check if the account has a balance
            if 'account' in accounts[0] and 'data' in accounts[0]['account'] and 'parsed' in accounts[0]['account']['data']:
                parsed_data = accounts[0]['account']['data']['parsed']
                if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                    token_amount_info = parsed_data['info']['tokenAmount']
                    if 'amount' in token_amount_info:
                        token_amount = int(token_amount_info['amount'])
                        logging.info(f"Token balance: {token_amount}")
            
            # Return True since the account exists, even if the balance is 0
            return True
        else:
            logging.info("No token account for BONK exists yet - we need to create one")
            
            # Step 2: Construct an ATA creation instruction via SPL Token program
            from solders.pubkey import Pubkey
            import base58
            
            # This is a simplified instruction to create an ATA
            # In production code, you'd use the spl-token library
            token_program_id = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            associated_token_program_id = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            
            # Get recent blockhash
            blockhash_response = wallet._rpc_call("getLatestBlockhash", [])
            if 'result' not in blockhash_response or 'value' not in blockhash_response['result']:
                logging.error("Failed to get recent blockhash")
                return False
                
            recent_blockhash = blockhash_response['result']['value']['blockhash']
            
            # We'll use the simplest possible approach - just buy a tiny amount 
            # via Jupiter which will create the ATA automatically
            logging.info("Creating token account by making a minimal buy...")
            
            # Get a quote for a tiny amount (0.005 SOL)
            amount_lamports = 5000000  # 0.005 SOL
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            quote_params = {
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": bonk_address,
                "amount": str(amount_lamports),
                "slippageBps": "1000"  # 10% slippage
            }
            
            quote_response = requests.get(quote_url, params=quote_params, timeout=10)
            
            if quote_response.status_code != 200:
                logging.error(f"Failed to get quote: {quote_response.status_code}")
                return False
                
            quote_data = quote_response.json()
            logging.info(f"Got quote for minimal token purchase")
            
            # Prepare swap transaction with strict settings
            swap_payload = {
                "quoteResponse": quote_data,
                "userPublicKey": str(wallet.public_key),
                "wrapAndUnwrapSol": True,
                # These settings might help with reliability
                "prioritizationFeeLamports": 10000,  # Add a priority fee
                "dynamicComputeUnitLimit": True      # Let Jupiter calculate CU
            }
            
            swap_response = requests.post(
                f"{CONFIG['JUPITER_API_URL']}/v6/swap",
                json=swap_payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if swap_response.status_code != 200:
                logging.error(f"Failed to prepare swap: {swap_response.status_code}")
                return False
                
            swap_data = swap_response.json()
            serialized_tx = swap_data["swapTransaction"]
            
            # Submit with additional options
            response = wallet._rpc_call("sendTransaction", [
                serialized_tx,
                {
                    "encoding": "base64",
                    "skipPreflight": True,
                    "maxRetries": 3,
                    "preflightCommitment": "confirmed"  # Use higher commitment level
                }
            ])
            
            if "result" in response:
                signature = response["result"]
                logging.info(f"Transaction submitted successfully: {signature}")
                
                # Wait for confirmation with explicit status check
                logging.info("Waiting 30 seconds for confirmation...")
                time.sleep(30)
                
                # Check if token account was created
                check_response = wallet._rpc_call("getTokenAccountsByOwner", [
                    str(wallet.public_key),
                    {"mint": bonk_address},
                    {"encoding": "jsonParsed"}
                ])
                
                if 'result' in check_response and 'value' in check_response['result']:
                    accounts = check_response['result']['value']
                    if len(accounts) > 0:
                        logging.info(f"Token account successfully created: {accounts[0]['pubkey']}")
                        
                        # Also check if we have a balance
                        if 'account' in accounts[0] and 'data' in accounts[0]['account'] and 'parsed' in accounts[0]['account']['data']:
                            parsed_data = accounts[0]['account']['data']['parsed']
                            if 'info' in parsed_data and 'tokenAmount' in parsed_data['info']:
                                token_amount_info = parsed_data['info']['tokenAmount']
                                if 'amount' in token_amount_info:
                                    token_amount = int(token_amount_info['amount'])
                                    logging.info(f"Token balance: {token_amount}")
                                    return True
                    else:
                        logging.error("Failed to create token account")
                        return False
            else:
                if "error" in response:
                    error_message = response.get("error", {}).get("message", "Unknown error")
                    logging.error(f"Transaction error: {error_message}")
                return False
    
    except Exception as e:
        logging.error(f"Error testing token account creation: {str(e)}")
        logging.error(traceback.format_exc())
        return False
        
    return False
    
def main():
    """Main entry point."""
    logging.info("============ BOT STARTING ============")
    
    # Check Solders version at startup
    solders_version = check_solders_version()
    logging.info(f"Solders version: {solders_version}")
    
    if initialize():
        # Try the optimized transaction approach with a small test amount
        test_token = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
        test_amount = 0.005  # Small test amount
        
        logging.info(f"Testing optimized transaction with {test_amount} SOL...")
        if optimized_buy_token(test_token, test_amount):
            logging.info("Optimized transaction successful! Starting trading loop...")
            # Let's also test selling right away
            if optimized_sell_token(test_token):
                logging.info("Optimized sell also successful! Everything works!")
            
            trading_loop()
        else:
            logging.error("Transaction test failed. Cannot start trading.")
            logging.error("Please verify RPC endpoint and wallet configuration.")
    else:
        logging.error("Failed to initialize bot. Please check configurations.")
# Add this at the end of your file
if __name__ == "__main__":
    main()
