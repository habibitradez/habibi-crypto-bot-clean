import os
import time
import json
import random
import logging
import datetime
import requests
import base64
import base58
from typing import Dict, List, Tuple, Optional, Any
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor, as_completed

# Solana imports
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.rpc.api import Client
from solana.rpc.types import TxOpts
from solana.transaction import Transaction
from solana.rpc.commitment import Confirmed

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
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
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

class SolanaWallet:
    """Solana wallet implementation for the trading bot."""
    
    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None):
        """Initialize a Solana wallet.
        
        Args:
            private_key: Base58 encoded private key string
            rpc_url: URL for the Solana RPC endpoint
        """
        self.rpc_url = rpc_url or CONFIG['SOLANA_RPC_URL']
        self.client = Client(endpoint=self.rpc_url, commitment=Confirmed)
        
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
        
        self.public_key = self.keypair.public_key
        
    def _create_keypair_from_private_key(self, private_key: str) -> Keypair:
        """Create a Solana keypair from a base58 encoded private key string."""
        decoded_key = base58.b58decode(private_key)
        return Keypair.from_secret_key(decoded_key)
    
    def get_balance(self) -> float:
        """Get the SOL balance of the wallet in SOL units."""
        try:
            response = self.client.get_balance(self.public_key)
            if 'result' in response and 'value' in response['result']:
                # Convert lamports to SOL (1 SOL = 10^9 lamports)
                return response['result']['value'] / 1_000_000_000
            return 0.0
        except Exception as e:
            logging.error(f"Error getting wallet balance: {str(e)}")
            return 0.0
    
    def sign_and_submit_transaction(self, transaction: Transaction) -> Optional[str]:
        """Sign and submit a transaction to the Solana blockchain.
        
        Args:
            transaction: The transaction to sign and submit
            
        Returns:
            The transaction signature if successful, None otherwise
        """
        try:
            # Submit the transaction to the network
            response = self.client.send_transaction(
                transaction, 
                self.keypair,
                opts=TxOpts(skip_confirmation=False, preflight_commitment=Confirmed)
            )
            
            if 'result' in response:
                signature = response['result']
                logging.info(f"Transaction submitted successfully: {signature}")
                return signature
            else:
                logging.error(f"Failed to submit transaction: {response}")
                return None
        except Exception as e:
            logging.error(f"Error signing and submitting transaction: {str(e)}")
            return None
    
    def get_token_accounts(self, token_address: str) -> List[dict]:
        """Get token accounts owned by this wallet for a specific token.
        
        Args:
            token_address: The token mint address
            
        Returns:
            List of token accounts
        """
        try:
            token_pubkey = PublicKey(token_address)
            response = self.client.get_token_accounts_by_owner(
                self.public_key,
                {'mint': token_pubkey}
            )
            
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
    
    def get_quote(self, input_mint: str, output_mint: str, amount: str, slippage_bps: str = "100") -> Optional[Dict]:
        """Get a swap quote from Jupiter API.
        
        Args:
            input_mint: The mint address of the input token
            output_mint: The mint address of the output token
            amount: The amount to swap in lamports/smallest decimal unit
            slippage_bps: The slippage tolerance in basis points (1% = 100 bps)
            
        Returns:
            The quote response data if successful, None otherwise
        """
        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": amount,
                "slippageBps": slippage_bps,
                "onlyDirectRoutes": "false"
            }
            
            response = requests.get(f"{self.api_url}/quote", params=params)
            
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
        """Prepare a swap transaction using the quote data.
        
        Args:
            quote_data: The quote data from get_quote
            user_public_key: The public key of the user's wallet
            
        Returns:
            The swap transaction data if successful, None otherwise
        """
        try:
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": user_public_key,
                "wrapUnwrapSOL": True
            }
            
            response = requests.post(
                f"{self.api_url}/swap",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return response.json()
            
            logging.warning(f"Failed to prepare swap transaction: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logging.error(f"Error preparing swap transaction: {str(e)}")
            return None
    
    def deserialize_transaction(self, transaction_data: Dict) -> Optional[Transaction]:
        """Deserialize a transaction from Jupiter API.
        
        Args:
            transaction_data: The transaction data from prepare_swap_transaction
            
        Returns:
            A Solana transaction object if successful, None otherwise
        """
        try:
            # Extract the serialized transaction
            if "swapTransaction" in transaction_data:
                serialized_tx = transaction_data["swapTransaction"]
                
                # Decode the base64 transaction data
                tx_bytes = base64.b64decode(serialized_tx)
                
                # Create a transaction from the bytes
                transaction = Transaction.deserialize(tx_bytes)
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
            slippage_bps="100"
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
            return True
        else:
            logging.error(f"Failed to submit transaction for buying {token_address}")
            return False
        
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
        global wallet, jupiter_handler
        
        if wallet is None or jupiter_handler is None:
            logging.error("Wallet or Jupiter handler not initialized")
            return False
        
        # Step 1: Find token account
        token_accounts = wallet.get_token_accounts(token_address)
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
            slippage_bps="100"
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
            return True
        else:
            logging.error(f"Failed to submit transaction for selling {token_address}")
            return False
        
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
