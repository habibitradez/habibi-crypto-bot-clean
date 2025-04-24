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
from solders.transaction import Transaction
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
    'JUPITER_API_URL': 'https://quote-api.jup.ag/v6',  # Updated to v6 API
    'WALLET_ADDRESS': os.environ.get('WALLET_ADDRESS', ''),
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '100')),
    'PARTIAL_PROFIT_PERCENT': int(os.environ.get('PARTIAL_PROFIT_PERCENT', '40')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '15')),
    'TIME_LIMIT_MINUTES': int(os.environ.get('TIME_LIMIT_MINUTES', '30')),
    'BUY_COOLDOWN_MINUTES': int(os.environ.get('BUY_COOLDOWN_MINUTES', '1')),  # Reduced from 2 to 1
    'CHECK_INTERVAL_MS': int(os.environ.get('CHECK_INTERVAL_MS', '1000')),  # Reduced to 1 second
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '15')),
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.05')),  # Reduced from 0.1 to 0.05
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '500'))  # Increased from 200 to 500
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
        secret_bytes = base58.b58decode(private_key)
        if len(secret_bytes) == 64:
            return Keypair.from_bytes(secret_bytes)
        elif len(secret_bytes) == 32:
            return Keypair.from_seed(secret_bytes)
        else:
            raise ValueError("Secret key must be 32 or 64 bytes.")
    
    def get_balance(self) -> float:
        """Get the SOL balance of the wallet in SOL units."""
        try:
            response = self._rpc_call("getBalance", [str(self.public_key)])
            if 'result' in response and 'value' in response['result']:
                # Convert lamports to SOL (1 SOL = 10^9 lamports)
                return response['result']['value'] / 1_000_000_000
            return 0.0
        except Exception as e:
            logging.error(f"Error getting wallet balance: {str(e)}")
            return 0.0
    
    def _rpc_call(self, method: str, params: List) -> Dict:
        """Make an RPC call to the Solana network."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        headers = {"Content-Type": "application/json"}
        response = requests.post(self.rpc_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"RPC call failed with status {response.status_code}: {response.text}")
    
    def sign_and_submit_transaction(self, transaction: Transaction) -> Optional[str]:
        """Sign and submit a transaction to the Solana blockchain."""
        try:
            # Get recent blockhash
            blockhash_response = self._rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
            blockhash = blockhash_response["result"]["value"]["blockhash"]
            
            # Set blockhash and sign transaction
            transaction.recent_blockhash = blockhash
            transaction.sign([self.keypair])
            
            # Serialize and submit transaction
            serialized_tx = base64.b64encode(transaction.serialize()).decode("utf-8")
            response = self._rpc_call("sendTransaction", [
                serialized_tx, 
                {"encoding": "base64", "skipPreflight": False}
            ])
            
            if "result" in response:
                signature = response["result"]
                logging.info(f"Transaction submitted successfully: {signature}")
                return signature
            else:
                logging.error(f"Failed to submit transaction: {response}")
                return None
                
        except Exception as e:
            logging.error(f"Error signing and submitting transaction: {str(e)}")
            return None
    
    def get_token_accounts(self, token_address: str) -> List[dict]:
        """Get token accounts owned by this wallet for a specific token."""
        try:
            response = self._rpc_call("getTokenAccountsByOwner", [
                str(self.public_key),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ])
            
            if 'result' in response and 'value' in response['result']:
                return response['result']['value']
            return []
        except Exception as e:
            logging.error(f"Error getting token accounts: {str(e)}")
            return []

class JupiterSwapHandler:
    """Handler for Jupiter API swap transactions."""
    
    def __init__(self, jupiter_api_url: str):
        """Initialize the Jupiter swap handler.
        
        Args:
            jupiter_api_url: The URL for the Jupiter API
        """
        self.api_url = jupiter_api_url
    
    def get_quote(self, input_mint: str, output_mint: str, amount: str, slippage_bps: str = "500") -> Optional[Dict]:
        """Get a swap quote from Jupiter API."""
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": "false"
            }
            
            response = requests.get(f"{self.api_url}/quote", params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if "data" in data:
                    return data["data"]
            
            logging.warning(f"Failed to get quote: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logging.error(f"Error getting quote: {str(e)}")
            return None
    
    def prepare_swap_transaction(self, quote_data: Dict, user_public_key: str) -> Optional[Dict]:
        """Prepare a swap transaction using the quote data."""
        try:
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": user_public_key,
                "wrapUnwrapSOL": True
            }
            
            response = requests.post(
                f"{self.api_url}/swap",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
            
            logging.warning(f"Failed to prepare swap transaction: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logging.error(f"Error preparing swap transaction: {str(e)}")
            return None
    
    def deserialize_transaction(self, transaction_data: Dict) -> Optional[Transaction]:
        """Deserialize a transaction from Jupiter API."""
        try:
            # Extract the serialized transaction
            if "swapTransaction" in transaction_data:
                serialized_tx = transaction_data["swapTransaction"]
                
                # Decode the base64 transaction data
                tx_bytes = base64.b64decode(serialized_tx)
                
                # Create a transaction from the bytes
                # Note: This is different from before because we're using solders
                transaction = Transaction.from_bytes(tx_bytes)
                return transaction
            else:
                logging.warning("No swapTransaction found in transaction data")
                return None
        except Exception as e:
            logging.error(f"Error deserializing transaction: {str(e)}")
            return None

# Initialize global wallet and Jupiter swap handler
wallet = None
jupiter_handler = None

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
            return False
    
    # Initialize Jupiter handler
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
        rpc_response = requests.post(
            CONFIG['SOLANA_RPC_URL'],
            json={"jsonrpc": "2.0", "id": 1, "method": "getHealth"},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if rpc_response.status_code == 200:
            logging.info(f"Successfully connected to QuickNode RPC (status {rpc_response.status_code})")
            # No need to check getLatestBlockhash here since we'll use it during transactions
            # Just verify we got a valid response from getHealth
            if "result" in rpc_response.json():
                logging.info("RPC connection fully verified")
            else:
                logging.warning(f"RPC connection might have issues: {rpc_response.text}")
                # Still continue, as this might just be a format issue
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
            "slippageBps": "500"
        }
        jupiter_response = requests.get(jupiter_test_url, params=test_params, timeout=10)
        
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
            "slippageBps": "500"
        }
        
        response = requests.get(quote_url, params=params, timeout=10)
        
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
            "slippageBps": "500"
        }
        
        response = requests.get(quote_url, params=reverse_params, timeout=10)
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
            "amount": "5000000",  # Only 0.005 SOL in lamports - much smaller amount
            "slippageBps": "1000"  # 10% slippage - much more lenient
        }
        
        response = requests.get(quote_url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # If we got a valid quote, token has liquidity
            if "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Liquidity check PASSED for {token_address} - Found liquidity")
                return True
            else:
                logging.debug(f"Liquidity check FAILED for {token_address} - No valid quote data")
        
        # Try reverse direction (token to SOL) as a backup
        reverse_params = {
            "inputMint": token_address,
            "outputMint": SOL_TOKEN_ADDRESS,
            "amount": "1000000",  # Small amount of token
            "slippageBps": "1000"  # 10% slippage
        }
        
        response = requests.get(quote_url, params=reverse_params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "outAmount" in data["data"] and int(data["data"]["outAmount"]) > 0:
                logging.info(f"Reverse liquidity check PASSED for {token_address}")
                return True
        
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
            headers={"Content-Type": "application/json"},
            timeout=10
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
            headers={"Content-Type": "application/json"},
            timeout=10
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

def scan_for_new_tokens() -> List[str]:
    """Scan blockchain for new token addresses with enhanced detection for promising meme tokens."""
    global tokens_scanned
    
    logging.info(f"Scanning for new tokens (limit: {CONFIG['TOKEN_SCAN_LIMIT']})")
    potential_tokens = []
    promising_meme_tokens = []
    
    # Add known tokens to potential list to ensure we always have options
    for token in KNOWN_TOKENS:
        if token["address"] not in potential_tokens and token["address"] != SOL_TOKEN_ADDRESS:
            potential_tokens.append(token["address"])
            if is_meme_token(token["address"], token.get("symbol", "")):
                promising_meme_tokens.append(token["address"])
    
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
    
    # Log verification steps
    logging.info(f"Verifying token {token_address}")
        
    # Check if token has a price
    token_price = get_token_price(token_address)
    if token_price is None:
        logging.info(f"Token {token_address} verification failed: No price available")
        return False
    else:
        logging.info(f"Token {token_address} price: {token_price}")
        
    # Check if token has liquidity
    liquidity = check_token_liquidity(token_address)
    if not liquidity:
        logging.info(f"Token {token_address} verification failed: No liquidity")
        return False
    else:
        logging.info(f"Token {token_address} has liquidity")
        
    # Token passes verification
    logging.info(f"Token {token_address} PASSED verification")
    return True

def buy_token(token_address: str, amount_sol: float) -> bool:
    """Buy a token using Jupiter API."""
    global buy_attempts, buy_successes
    
    buy_attempts += 1
    
    if CONFIG['SIMULATION_MODE']:
        token_price = get_token_price(token_address)
        if token_price:
            estimated_tokens = amount_sol / token_price
            logging.info(f"[SIMULATION] Auto-bought {estimated_tokens:.2f} tokens of {token_address} for {amount_sol} SOL")
            # Record buy timestamp
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
        
        quote_data = jupiter_handler.get_quote(
            input_mint=SOL_TOKEN_ADDRESS,
            output_mint=token_address,
            amount=str(amount_lamports),
            slippage_bps="1000"  # 10% slippage to ensure transaction goes through
        )
        
        if quote_data is None:
            logging.error(f"Failed to get quote for buying {token_address}")
            return False
        
        # Step 2: Prepare swap transaction
        swap_data = jupiter_handler.prepare_swap_transaction(
            quote_data=quote_data,
            user_public_key=str(wallet.public_key)
        )
        
        if swap_data is None:
            logging.error(f"Failed to prepare swap transaction for {token_address}")
            return False
        
        # Step 3: Deserialize the transaction
        transaction = jupiter_handler.deserialize_transaction(swap_data)
        
        if transaction is None:
            logging.error(f"Failed to deserialize transaction for {token_address}")
            return False
        
        # Step 4: Sign and submit the transaction
        signature = wallet.sign_and_submit_transaction(transaction)
        
        if signature:
            logging.info(f"Successfully bought {token_address} for {amount_sol} SOL - Signature: {signature}")
            # Record buy timestamp
            token_buy_timestamps[token_address] = time.time()
            buy_successes += 1
            return True
        else:
            logging.error(f"Failed to submit transaction for buying {token_address}")
            return False
        
    except Exception as e:
        logging.error(f"Error buying {token_address}: {str(e)}")
        logging.error(traceback.format_exc())  # Print full stack trace
        return False

def sell_token(token_address: str, percentage: int = 100) -> bool:
    """Sell a percentage of token holdings using Jupiter API."""
    global sell_attempts, sell_successes
    
    sell_attempts += 1
    
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
        response = wallet._rpc_call("getTokenAccountsByOwner", [
            str(wallet.public_key),
            {"mint": token_address},
            {"encoding": "jsonParsed"}
        ])
        
        token_accounts = []
        if 'result' in response and 'value' in response['result']:
            token_accounts = response['result']['value']
        
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
        
        if token_amount is None:
            logging.error(f"Could not determine token amount for {token_address}")
            return False
        
        # Calculate amount to sell based on percentage
        amount_to_sell = int(int(token_amount) * percentage / 100)
        
        if amount_to_sell <= 0:
            logging.error(f"Invalid amount to sell for {token_address}: {amount_to_sell}")
            return False
        
        # Step 2: Get a quote
        quote_data = jupiter_handler.get_quote(
            input_mint=token_address,
            output_mint=SOL_TOKEN_ADDRESS,
            amount=str(amount_to_sell),
            slippage_bps="1000"  # 10% slippage to ensure transaction goes through
        )
        
        if quote_data is None:
            logging.error(f"Failed to get quote for selling {token_address}")
            return False
        
        # Step 3: Prepare swap transaction
        swap_data = jupiter_handler.prepare_swap_transaction(
            quote_data=quote_data,
            user_public_key=str(wallet.public_key)
        )
        
        if swap_data is None:
            logging.error(f"Failed to prepare swap transaction for selling {token_address}")
            return False
        
        # Step 4: Deserialize the transaction
        transaction = jupiter_handler.deserialize_transaction(swap_data)
        
        if transaction is None:
            logging.error(f"Failed to deserialize transaction for selling {token_address}")
            return False
        
        # Step 5: Sign and submit the transaction
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
        logging.error(traceback.format_exc())  # Print full stack trace
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
        return

def can_buy_token() -> bool:
    """Check if we can buy another token based on concurrent limits and cooldown."""
    # Always return true if we have zero monitored tokens
    if len(monitored_tokens) == 0:
        return True
        
    # Check concurrent token limit
    if len(monitored_tokens) >= CONFIG['MAX_CONCURRENT_TOKENS']:
        logging.info(f"Can't buy: at max concurrent tokens ({len(monitored_tokens)})")
        return False
    
    # Use much shorter cooldown
    cooldown_seconds = 30  # Just 30 seconds cooldown instead of minutes
    
    # Check buy cooldown
    current_time = time.time()
    for token_address, timestamp in token_buy_timestamps.items():
        time_since_last_buy = current_time - timestamp  # in seconds
        if time_since_last_buy < cooldown_seconds:
            logging.info(f"Can't buy: cooldown active ({cooldown_seconds - time_since_last_buy:.1f} seconds remaining)")
            return False
    
    return True

def print_status_report():
    """Print a comprehensive status report of the bot's activities."""
    logging.info("=" * 50)
    logging.info("BOT STATUS REPORT")
    logging.info("=" * 50)
    
    # Report wallet status
    if not CONFIG['SIMULATION_MODE'] and wallet:
        balance = wallet.get_balance()
        logging.info(f"Wallet address: {wallet.public_key}")
        logging.info(f"Wallet balance: {balance} SOL")
    else:
        logging.info("SIMULATION MODE: No real wallet in use")
    
    # Report monitored tokens
    logging.info(f"Currently monitoring {len(monitored_tokens)} tokens:")
    
    for token_address, data in monitored_tokens.items():
        initial_price = data['initial_price']
        highest_price = data['highest_price']
        current_price = get_token_price(token_address)
        
        if current_price:
            price_change_pct = ((current_price - initial_price) / initial_price) * 100
            high_change_pct = ((highest_price - initial_price) / initial_price) * 100
            time_elapsed = time.time() - data['buy_time']
            minutes = int(time_elapsed / 60)
            seconds = int(time_elapsed % 60)
            
            logging.info(f"  Token: {token_address}")
            logging.info(f"    Initial price: {initial_price:.10f} SOL")
            logging.info(f"    Current price: {current_price:.10f} SOL (Change: {price_change_pct:.2f}%)")
            logging.info(f"    Highest price: {highest_price:.10f} SOL (Max gain: {high_change_pct:.2f}%)")
            logging.info(f"    Holding time: {minutes} minutes, {seconds} seconds")
            logging.info(f"    Partial profit taken: {data['partial_profit_taken']}")
    
    # Report statistics
    logging.info("Bot Statistics:")
    logging.info(f"  Tokens scanned: {tokens_scanned}")
    logging.info(f"  Buy attempts: {buy_attempts}")
    logging.info(f"  Buy successes: {buy_successes}")
    logging.info(f"  Sell attempts: {sell_attempts}")
    logging.info(f"  Sell successes: {sell_successes}")
    logging.info(f"  Errors encountered: {errors_encountered}")
    
    # Report price cache stats
    logging.info(f"Price cache size: {len(price_cache)} tokens")
    
    # Report configuration
    logging.info("Current configuration:")
    logging.info(f"  PROFIT_TARGET_PERCENT: {CONFIG['PROFIT_TARGET_PERCENT']}%")
    logging.info(f"  PARTIAL_PROFIT_PERCENT: {CONFIG['PARTIAL_PROFIT_PERCENT']}%")
    logging.info(f"  STOP_LOSS_PERCENT: {CONFIG['STOP_LOSS_PERCENT']}%")
    logging.info(f"  TIME_LIMIT_MINUTES: {CONFIG['TIME_LIMIT_MINUTES']} minutes")
    logging.info(f"  BUY_AMOUNT_SOL: {CONFIG['BUY_AMOUNT_SOL']} SOL")
    logging.info(f"  MAX_CONCURRENT_TOKENS: {CONFIG['MAX_CONCURRENT_TOKENS']}")
    
    logging.info("=" * 50)

def trading_loop():
    """Main trading loop."""
    global iteration_count, last_status_time, errors_encountered
    
    logging.info("Trading loop started")
    
    iteration_count = 0
    last_status_time = time.time()
    force_buy_counter = 0
    
    while True:
        try:
            iteration_count += 1
            
            # Print status report every 5 minutes
            current_time = time.time()
            if current_time - last_status_time > 300:  # 5 minutes
                print_status_report()
                last_status_time = current_time
            
            # If we can buy more tokens
            if can_buy_token():
                logging.info(f"Iteration {iteration_count}: Looking for tokens to buy")
                # First, check for new tokens
                potential_tokens = scan_for_new_tokens()
                
                if potential_tokens:
                    logging.info(f"Found {len(potential_tokens)} potential tokens to evaluate")
                    
                    # Evaluate all tokens (up to 5 at a time)
                    valid_tokens = []
                    for token_address in potential_tokens[:5]:
                        if token_address not in monitored_tokens:
                            # Quick check if it's a meme token
                            is_meme = is_meme_token(token_address)
                            
                            # Very basic price check
                            has_price = False
                            price = get_token_price(token_address)
                            if price is not None:
                                has_price = True
                            
                            # Check liquidity
                            has_liquidity = check_token_liquidity(token_address)
                            
                            logging.info(f"Token {token_address}: Meme={is_meme}, HasPrice={has_price}, HasLiquidity={has_liquidity}")
                            
                            if has_price and has_liquidity:
                                valid_tokens.append({
                                    'address': token_address,
                                    'is_meme': is_meme,
                                    'priority': 1 if is_meme else 2
                                })
                    
                    # Force buy after 10 iterations if we haven't bought anything yet
                    force_buy = force_buy_counter >= 10 and len(monitored_tokens) == 0
                    
                    # Buy a token if we found any valid ones
                    if valid_tokens:
                        # Sort by priority (meme tokens first)
                        sorted_tokens = sorted(valid_tokens, key=lambda x: x['priority'])
                        token_to_buy = sorted_tokens[0]['address']
                        
                        logging.info(f"Attempting to buy token: {token_to_buy}")
                        # Try to buy it
                        buy_result = buy_token(token_to_buy, CONFIG['BUY_AMOUNT_SOL'])
                        logging.info(f"Buy attempt result: {buy_result}")
                        
                        if buy_result:
                            force_buy_counter = 0
                            
                            # Record the purchase in monitored_tokens
                            initial_price = get_token_price(token_to_buy)
                            if initial_price:
                                monitored_tokens[token_to_buy] = {
                                    'initial_price': initial_price,
                                    'highest_price': initial_price,
                                    'partial_profit_taken': False,
                                    'buy_time': time.time()
                                }
                                logging.info(f"Successfully bought and monitoring {token_to_buy} at {initial_price}")
                        else:
                            logging.warning(f"Failed to buy {token_to_buy}")
                    elif force_buy:
                        # Force buy a known token
                        logging.info("Force buying a known token after 10 failed iterations")
                        for token in KNOWN_TOKENS:
                            if token["address"] not in monitored_tokens and token["address"] != SOL_TOKEN_ADDRESS:
                                if verify_token(token["address"]):
                                    logging.info(f"Force buying known token: {token['symbol']}")
                                    if buy_token(token["address"], CONFIG['BUY_AMOUNT_SOL']):
                                        initial_price = get_token_price(token["address"])
                                        if initial_price:
                                            monitored_tokens[token["address"]] = {
                                                'initial_price': initial_price,
                                                'highest_price': initial_price,
                                                'partial_profit_taken': False,
                                                'buy_time': time.time()
                                            }
                                            force_buy_counter = 0
                                            break
                        
                        # Reset counter even if we didn't buy
                        force_buy_counter = 0
                    else:
                        force_buy_counter += 1
                else:
                    logging.info("No potential tokens found in this iteration")
                    force_buy_counter += 1
            else:
                logging.info(f"Iteration {iteration_count}: Cannot buy more tokens right now")
            
            # Monitor existing tokens
            tokens_to_monitor = list(monitored_tokens.keys())
            if tokens_to_monitor:
                logging.info(f"Monitoring {len(tokens_to_monitor)} tokens")
                for token_address in tokens_to_monitor:
                    monitor_token_price(token_address)
            
            # Sleep before next check - reduced to be more aggressive
            sleep_time = CONFIG.get('CHECK_INTERVAL_MS', 1000) / 1000
            # Make sure it's not too long
            sleep_time = min(sleep_time, 1.0)  # Maximum 1 second between checks
            time.sleep(sleep_time)
            
        except Exception as e:
            errors_encountered += 1
            logging.error(f"Error in trading loop: {str(e)}")
            logging.error(traceback.format_exc())  # Print full stack trace
            time.sleep(5)  # Sleep a bit longer on error

def test_buy_command(token_address: str = None):
    """Test command to manually buy a specific token."""
    global wallet, jupiter_handler
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[TEST] Cannot test buy in SIMULATION mode. Please set SIMULATION_MODE=false")
        return False
    
    if not token_address:
        # If no token provided, use BONK as a test
        token_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
    
    logging.info(f"[TEST] Manual buy command for token: {token_address}")
    
    # Initialize if needed
    if wallet is None or jupiter_handler is None:
        if not initialize():
            logging.error("[TEST] Failed to initialize for test buy")
            return False
    
    # Check wallet balance
    balance = wallet.get_balance()
    logging.info(f"[TEST] Current wallet balance: {balance} SOL")
    
    # Try to buy the token
    amount_sol = CONFIG['BUY_AMOUNT_SOL'] 
    logging.info(f"[TEST] Attempting to buy {token_address} for {amount_sol} SOL")
    
    result = buy_token(token_address, amount_sol)
    
    if result:
        logging.info(f"[TEST] Successfully bought {token_address}")
        return True
    else:
        logging.error(f"[TEST] Failed to buy {token_address}")
        return False

def test_sell_command(token_address: str = None):
    """Test command to manually sell a specific token."""
    global wallet, jupiter_handler
    
    if CONFIG['SIMULATION_MODE']:
        logging.info(f"[TEST] Cannot test sell in SIMULATION mode. Please set SIMULATION_MODE=false")
        return False
    
    if not token_address:
        # If no token provided, try to sell BONK as a test
        token_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK
    
    logging.info(f"[TEST] Manual sell command for token: {token_address}")
    
    # Initialize if needed
    if wallet is None or jupiter_handler is None:
        if not initialize():
            logging.error("[TEST] Failed to initialize for test sell")
            return False
    
    # Try to sell the token
    logging.info(f"[TEST] Attempting to sell {token_address}")
    
    result = sell_token(token_address)
    
    if result:
        logging.info(f"[TEST] Successfully sold {token_address}")
        return True
    else:
        logging.error(f"[TEST] Failed to sell {token_address}")
        return False

def main():
    """Main entry point."""
    import sys
    
    # Check for command-line arguments for testing
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "buy":
            token_address = sys.argv[2] if len(sys.argv) > 2 else None
            test_buy_command(token_address)
            return
        
        elif command == "sell":
            token_address = sys.argv[2] if len(sys.argv) > 2 else None
            test_sell_command(token_address)
            return
            
        elif command == "test":
            # Test both buying and selling
            if test_buy_command():
                logging.info("[TEST] Buy test successful. Waiting 10 seconds before sell test...")
                time.sleep(10)
                test_sell_command()
            return
    
    # Normal operation
    if initialize():
        trading_loop()
    else:
        logging.error("Failed to initialize bot. Please check configurations.")

if __name__ == "__main__":
    main()
