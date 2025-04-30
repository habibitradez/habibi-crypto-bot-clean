"""
Enhanced Solana Trading Bot with AI-Inspired Modules
"""

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

# Solana imports
from solders.keypair import Keypair
from solders.pubkey import Pubkey as PublicKey
from solders.transaction import Transaction, VersionedTransaction
from solders.system_program import transfer, TransferParams
import numpy as np  # For numerical calculations (e.g., volatility)

# --- Configuration ---
# (Keep your existing CONFIG, but I'll highlight key ones)
CONFIG = {
    'SOLANA_RPC_URL': os.environ.get('SOLANA_RPC_URL', ''),
    'JUPITER_API_URL': 'https://quote-api.jup.ag',
    'WALLET_PRIVATE_KEY': os.environ.get('WALLET_PRIVATE_KEY', ''),
    'SIMULATION_MODE': os.environ.get('SIMULATION_MODE', 'true').lower() == 'true',
    'PROFIT_TARGET_PERCENT': int(os.environ.get('PROFIT_TARGET_PERCENT', '20')),
    'STOP_LOSS_PERCENT': int(os.environ.get('STOP_LOSS_PERCENT', '10')),
    'BUY_AMOUNT_SOL': float(os.environ.get('BUY_AMOUNT_SOL', '0.1')),
    'MAX_CONCURRENT_TOKENS': int(os.environ.get('MAX_CONCURRENT_TOKENS', '3')),
    'TOKEN_SCAN_LIMIT': int(os.environ.get('TOKEN_SCAN_LIMIT', '50')),
    'TRADING_STRATEGY': os.environ.get('TRADING_STRATEGY', 'momentum'),  # momentum, mean_reversion, etc.
    'VOLATILITY_LOOKBACK': int(os.environ.get('VOLATILITY_LOOKBACK', '20')),  # For volatility calculations
    'TRAILING_STOP_LOSS_PERCENT': float(os.environ.get('TRAILING_STOP_LOSS_PERCENT', '0.05')),  # 5% trailing stop
    'RISK_PER_TRADE_PERCENT': float(os.environ.get('RISK_PER_TRADE_PERCENT', '0.02')),  # 2% risk per trade
}

# --- Logging Setup ---
def setup_logging():
    current_time = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(module)s - %(message)s',  # Include module name
        handlers=[
            logging.FileHandler(f"bot_log_{current_time}.log"),
            logging.StreamHandler()
        ],
        datefmt='%Y-%m-%d %H:%M:%S'
    )

# --- Solana Wallet Module ---
class SolanaWallet:
    def __init__(self, private_key: Optional[str] = None, rpc_url: Optional[str] = None):
        # (Same as your existing SolanaWallet, but ensure proper error handling)
        self.rpc_url = rpc_url or CONFIG['SOLANA_RPC_URL']
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
        try:
            secret_bytes = base58.b58decode(private_key)
            if len(secret_bytes) == 64:
                return Keypair.from_bytes(secret_bytes)
            elif len(secret_bytes) == 32:
                return Keypair.from_seed(secret_bytes)
            else:
                raise ValueError(f"Invalid private key length: {len(secret_bytes)}")
        except Exception as e:
            logging.error(f"Error creating keypair: {e}")
            raise

    def get_balance(self) -> float:
        try:
            response = self._rpc_call("getBalance", [str(self.public_key)])
            return response['result']['value'] / 1_000_000_000 if 'result' in response and 'value' in response['result'] else 0.0
        except Exception as e:
            logging.error(f"Error getting balance: {e}")
            return 0.0

    def _rpc_call(self, method: str, params: List) -> Dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(self.rpc_url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"RPC error: {e}")
            raise

    def sign_and_submit_transaction(self, transaction: Transaction | VersionedTransaction) -> Optional[str]:
        try:
            if isinstance(transaction, VersionedTransaction):
                serialized_tx = base64.b64encode(transaction.to_bytes()).decode("utf-8")
            elif isinstance(transaction, Transaction):
                serialized_tx = base64.b64encode(transaction.serialize()).decode("utf-8")
            else:
                raise ValueError(f"Unexpected transaction type: {type(transaction).__name__}")

            response = self._rpc_call("sendTransaction", [
                serialized_tx,
                {"encoding": "base64", "skipPreflight": False}
            ])
            return response.get("result")
        except Exception as e:
            logging.error(f"Error signing/submitting: {e}")
            return None

    def get_token_accounts(self, token_address: str) -> List[dict]:
        try:
            response = self._rpc_call("getTokenAccountsByOwner", [
                str(self.public_key),
                {"mint": token_address},
                {"encoding": "jsonParsed"}
            ])
            return response['result']['value'] if 'result' in response and 'value' in response['result'] else []
        except Exception as e:
            logging.error(f"Error getting token accounts: {e}")
            return []

# --- Jupiter Swap Module ---
class JupiterSwapHandler:
    def __init__(self, jupiter_api_url: str):
        self.api_url = jupiter_api_url
        logging.info(f"Jupiter API URL: {jupiter_api_url}")

    def get_quote(self, input_mint: str, output_mint: str, amount: str, slippage_bps: str = "500") -> Optional[Dict]:
        try:
            params = {"inputMint": input_mint, "outputMint": output_mint, "amount": amount, "slippageBps": slippage_bps}
            response = requests.get(f"{self.api_url}/v6/quote", params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data if "outAmount" in data else data.get("data")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting quote: {e}")
            return None
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON response: {response.text}")
            return None

    def prepare_swap_transaction(self, quote_data: Dict, user_public_key: str) -> Optional[Dict]:
        try:
            payload = {
                "quoteResponse": quote_data,
                "userPublicKey": user_public_key,
                "wrapUnwrapSOL": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": "auto"
            }
            response = requests.post(f"{self.api_url}/v6/swap", json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error preparing swap: {e}")
            return None
        except json.JSONDecodeError:
            logging.error(f"Invalid JSON response: {response.text}")
            return None

    def deserialize_transaction(self, transaction_data: Dict) -> Optional[Transaction | VersionedTransaction]:
        try:
            serialized_tx = transaction_data["swapTransaction"]
            tx_bytes = base64.b64decode(serialized_tx)
            try:
                return Transaction.from_bytes(tx_bytes)
            except ValueError:
                return VersionedTransaction.from_bytes(tx_bytes)
        except Exception as e:
            logging.error(f"Error deserializing: {e}")
            return None

# --- Token Analysis Module ---
class TokenAnalyzer:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    def is_meme_token(self, token_address: str, token_name: str = "", token_symbol: str = "") -> bool:
        # (Your enhanced meme token logic here)
        MEME_TOKEN_PATTERNS = [
            "pump", "moon", "pepe", "doge", "shib", "inu", "cat", "elon", "musk",
            "trump", "biden", "wojak", "chad", "frog", "dog", "puppy", "kitty",
            "meme", "coin", "stonk", "ape", "rocket", "mars", "lambo", "diamond",
            "hand", "hodl", "rich", "poor", "trader", "crypto", "token",
            "bonk", "wif", "dogwifhat", "popcat", "pnut", "peanut", "slerf",
            "myro", "giga", "gigachad", "moodeng", "pengu", "pudgy", "would",
            "bull", "bear", "hippo", "squirrel", "cat", "doge", "shiba",
            "monkey", "ape", "panda", "fox", "bird", "eagle", "penguin",
            "viral", "trend", "hype", "fomo", "mochi", "michi", "ai", "gpt",
            "official", "og", "based", "alpha", "shill", "gem", "baby", "daddy",
            "mini", "mega", "super", "hyper", "ultra", "king", "queen", "lord",
            "sol", "solana", "solaxy", "solama", "moonlana", "soldoge", "fronk",
            "smog", "sunny", "saga", "spx", "degods", "wepe", "bab"
        ]
        token_address_lower = token_address.lower()
        for pattern in MEME_TOKEN_PATTERNS:
            if pattern.lower() in token_address_lower:
                return True
        if token_name or token_symbol:
            token_info = (token_name + token_symbol).lower()
            for pattern in MEME_TOKEN_PATTERNS:
                if pattern.lower() in token_info:
                    return True
        high_potential_indicators = ["pump", "moon", "pepe", "doge", "wif", "bonk", "cat", "inu"]
        for indicator in high_potential_indicators:
            if indicator in token_address_lower:
                logging.info(f"High potential meme token detected: {token_address} (contains '{indicator}')")
                return True
        if "420" in token_address_lower or "69" in token_address_lower or "1337" in token_address_lower:
            logging.info(f"Meme number pattern detected in token: {token_address}")
            return True
        return False

    def check_token_liquidity(self, token_address: str) -> bool:
        # (Your liquidity check logic here)
        SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"
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
            {"symbol": "JUP", "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "tradable": True},
            {"symbol": "ORCA", "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", "tradable": True},
            {"symbol": "SAMO", "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "tradable": True},
            {"symbol": "RAY", "address": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "tradable": True},
            {"symbol": "STEP", "address": "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT", "tradable": True},
            {"symbol": "RENDER", "address": "RNDRxx6LYgjvGdgkTKYbJ3y4KMqZyWawN7GpfSZJT3z", "tradable": True}
        ]
        for token in KNOWN_TOKENS:
            if token["address"] == token_address:
                logging.info(f"Known token {token_address} ({token.get('symbol', '')}) - Assuming it has liquidity")
                return True
        try:
            logging.info(f"Checking liquidity for {token_address}...")
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            params = {
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": token_address,
                "amount": "1000000",
                "slippageBps": "2000"
            }
            logging.info(f"Liquidity check 1: 0.001 SOL → {token_address} with 20% slippage")
            response = requests.get(quote_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
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
            reverse_params = {
                "inputMint": token_address,
                "outputMint": SOL_TOKEN_ADDRESS,
                "amount": "1000000",
                "slippageBps": "2000"
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
            ultra_small_params = {
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": token_address,
                "amount": "500000",
                "slippageBps": "3000"
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

    def get_token_price(self, token_address: str, price_cache: dict, price_cache_time: dict) -> Optional[float]:
        # (Your price retrieval logic, with caching)
        SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"
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
            {"symbol": "JUP", "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", "tradable": True},
            {"symbol": "ORCA", "address": "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE", "tradable": True},
            {"symbol": "SAMO", "address": "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU", "tradable": True},
            {"symbol": "RAY", "address": "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", "tradable": True},
            {"symbol": "STEP", "address": "StepAscQoEioFxxWGnh2sLBDFp9d8rvKz2Yp39iDpyT", "tradable": True},
            {"symbol": "RENDER", "address": "RNDRxx6LYgjvGdgkTKYbJ3y4KMqZyWawN7GpfSZJT3z", "tradable": True}
        ]
        if token_address in price_cache and token_address in price_cache_time:
            if time.time() - price_cache_time[token_address] < 30:
                return price_cache[token_address]
        if token_address == SOL_TOKEN_ADDRESS:
            return 1.0
        for token in KNOWN_TOKENS:
            if token["address"] == token_address and token.get("tradable") is False:
                logging.info(f"Skipping price check for known non-tradable token: {token_address} ({token.get('symbol', '')})")
                return None
        try:
            quote_url = f"{CONFIG['JUPITER_API_URL']}/v6/quote"
            params = {
                "inputMint": SOL_TOKEN_ADDRESS,
                "outputMint": token_address,
                "amount": "1000000000",
                "slippageBps": "500"
            }
            logging.info(f"Getting price for {token_address} using Jupiter API...")
            global last_api_call_time, api_call_delay
            time_since_last_call = time.time() - last_api_call_time
            if time_since_last_call < api_call_delay:
                sleep_time = api_call_delay - time_since_last_call
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
                time.sleep(sleep_time)
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=params, timeout=10)
            if response.status_code == 429:
                logging.warning(f"Rate limited by Jupiter API (429). Waiting and retrying...")
                time.sleep(2)
                last_api_call_time = time.time()
                response = requests.get(quote_url, params=params, timeout=10)
                if response.status_code == 429:
                    api_call_delay += 0.5
                    logging.warning(f"Still rate limited. Increased delay to {api_call_delay}s")
            if response.status_code == 200:
                data = response.json()
                if "outAmount" in data:
                    out_amount = int(data["outAmount"])
                    token_price = 1.0 / (out_amount / 1000000000)
                    logging.info(f"Got price for {token_address}: {token_price} SOL (1 SOL = {out_amount} tokens)")
                    price_cache[token_address] = token_price
                    price_cache_time[token_address] = time.time()
                    for token in KNOWN_TOKENS:
                        if token["address"] == token_address:
                            token["tradable"] = True
                            break
                    return token_price
                elif "data" in data and "outAmount" in data["data"]:
                    out_amount = int(data["data"]["outAmount"])
                    token_price = 1.0 / (out_amount / 1000000000)
                    logging.info(f"Got price for {token_address}: {token_price} SOL (1 SOL = {out_amount} tokens)")
                    price_cache[token_address] = token_price
                    price_cache_time[token_address] = time.time()
                    for token in KNOWN_TOKENS:
                        if token["address"] == token_address:
                            token["tradable"] = True
                            break
                    return token_price
                else:
                    logging.warning(f"Invalid quote response for {token_address}")
            logging.info(f"Trying reverse direction for {token_address} price...")
            reverse_params = {
                "inputMint": token_address,
                "outputMint": SOL_TOKEN_ADDRESS,
                "amount": "1000000000",
                "slippageBps": "500"
            }
            time_since_last_call = time.time() - last_api_call_time
            if time_since_last_call < api_call_delay:
                sleep_time = api_call_delay - time_since_last_call
                logging.info(f"Rate limiting: Sleeping for {sleep_time:.2f}s before Jupiter API call")
                time.sleep(sleep_time)
            last_api_call_time = time.time()
            response = requests.get(quote_url, params=reverse_params, timeout=10)
            if response.status_code == 429:
                logging.warning(f"Rate limited by Jupiter API (429). Waiting and retrying...")
                time.sleep(2)
                last_api_call_time = time.time()
                response = requests.get(quote_url, params=reverse_params, timeout=10)
                if response.status_code == 429:
                    api_call_delay += 0.5
                    logging.warning(f"Still rate limited. Increased delay to {api_call_delay}s")
            if response.status_code == 200:
                data = response.json()
                if "outAmount" in data:
                    out_amount = int(data["outAmount"])
                    token_price = out_amount / 1000000000
                    logging.info(f"Got reverse price for {token_address}: {token_price} SOL (1 token = {out_amount} lamports)")
                    price_cache[token_address] = token_price
                    price_cache_time[token_address] = time.time()
                    for token in KNOWN_TOKENS:
                        if token["address"] == token_address:
                            token["tradable"] = True
                            break
                    return token_price
                elif "data" in data and "outAmount" in data["data"]:
                    out_amount = int(data["data"]["outAmount"])
                    token_price = out_amount / 1000000000
                    logging.info(f"Got reverse price for {token_address}: {token_price} SOL (1 token = {out_amount} lamports)")
                    price_cache[token_address] = token_price
                    price_cache_time[token_address] = time.time()
                    for token in KNOWN_TOKENS:
                        if token["address"] == token_address:
                            token["tradable"] = True
                            break
                    return token_price
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

    def get_recent_transactions(self, limit: int = 100) -> List[Dict]:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    {"limit": limit}
                ]
            }
            response = requests.post(self.rpc_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data["result"] if "result" in data else []
        except requests.exceptions.RequestException as e:
            logging.error(f"Error getting recent transactions: {e}")
            return []

    def analyze_transaction(self, signature: str) -> List[str]:
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
            response = requests.post(self.rpc_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            response.raise_for_status()
            data = response.json()
            result = data.get("result")
            if not result:
                return []
            found_tokens = []
            if "meta" in result and "innerInstructions" in result
