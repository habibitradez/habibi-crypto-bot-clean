# === Robust Transaction Execution ===

# Constants for transaction reliability
MAX_TRANSACTION_ATTEMPTS = 3      # Max retries for transactions
TRANSACTION_RETRY_DELAY = 2       # Seconds between retries
RPC_ENDPOINTS = [
    f"https://rpc.shyft.to?api_key={SHYFT_RPC_KEY}",
    "https://api.mainnet-beta.solana.com",
    "https://solana-mainnet.g.alchemy.com/v2/demo",
    "https://solana.genesysgo.net",
    "https://ssc-dao.genesysgo.net"
]

def get_best_rpc():
    """
    Tests all RPC endpoints and returns the most responsive one
    """
    best_rpc = None
    best_time = float('inf')
    
    for endpoint in RPC_ENDPOINTS:
        try:
            start_time = time.time()
            test_client = Client(endpoint)
            test_key = get_phantom_keypair().pubkey()
            test_client.get_balance(test_key)
            end_time = time.time()
            
            response_time = end_time - start_time
            logging.info(f"RPC {endpoint} response time: {response_time:.3f}s")
            
            if response_time < best_time:
                best_time = response_time
                best_rpc = endpoint
        except Exception as e:
            logging.warning(f"RPC {endpoint} test failed: {str(e)[:100]}")
    
    if best_rpc:
        logging.info(f"Selected fastest RPC: {best_rpc} ({best_time:.3f}s)")
        return best_rpc
    else:
        # Fallback to first endpoint if all tests fail
        logging.warning(f"All RPC tests failed, using first endpoint")
        return RPC_ENDPOINTS[0]

# Enhanced buy function with retry mechanism
@retry(stop=stop_after_attempt(MAX_TRANSACTION_ATTEMPTS), 
       wait=wait_exponential(multiplier=1, max=10),
       retry=retry_if_exception_type(Exception))
def real_buy_token(to_addr: str, lamports: int):
    """
    Enhanced buy function with robust error handling and retry logic
    """
    try:
        # Get wallet keypair
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # Skip if we've reached daily profit target
        if daily_profit >= DAILY_PROFIT_TARGET:
            logging.info(f"🎯 Daily profit target reached. Skipping buy.")
            return None
        
        # Get initial quote
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=2&maxAccounts=10"
        logging.info(f"🔍 Getting buy quote for: {to_addr}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            
            # Retry with higher slippage
            fallback_quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={lamports}&slippage=3&maxAccounts=10"
            r = requests.get(fallback_quote_url, timeout=10)
            if r.status_code != 200:
                logging.error(f"❌ Jupiter quote retry failed with {r.status_code}")
                return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No swap route available for {to_addr}")
            return None
            
        # Check if the transaction is feasible
        out_amount = quote.get('outAmount', '0')
        if int(out_amount) <= 0:
            logging.warning(f"❌ Zero output amount for {to_addr}")
            return None

        # Execute the transaction
        swap_attempts = 0
        max_swap_attempts = 2  # Try twice with different settings
        
        while swap_attempts < max_swap_attempts:
            try:
                swap_payload = {
                    "userPublicKey": str(kp.pubkey()),
                    "wrapUnwrapSOL": True,
                    "quoteResponse": quote,
                    "computeUnitPriceMicroLamports": 1000,
                    "asLegacyTransaction": False,
                    "prioritizationFeeLamports": 0,
                    "dynamicComputeUnitLimit": True
                }
                
                # On second attempt, use simpler parameters
                if swap_attempts > 0:
                    swap_payload["prioritizationFeeLamports"] = 10000  # Add priority fee
                    swap_payload["computeUnitPriceMicroLamports"] = 2000  # Increase compute unit price
                
                swap = requests.post("https://quote-api.jup.ag/v6/swap", 
                                    json=swap_payload, 
                                    timeout=10).json()
                
                if "swapTransaction" not in swap:
                    swap_attempts += 1
                    logging.error(f"❌ No swap transaction returned (attempt {swap_attempts})")
                    continue

                tx_data = decode_transaction_blob(swap["swapTransaction"])
                logging.info(f"🚀 Sending BUY transaction for {to_addr}")
                
                # Send transaction with optimized parameters
                tx_opts = TxOpts(
                    skip_preflight=True,
                    preflight_commitment="confirmed",
                    max_retries=3
                )
                
                sig = solana_client.send_raw_transaction(tx_data, opts=tx_opts)
                
                # Now wait for confirmation with timeout
                try:
                    commitment = "confirmed"
                    timeout = 30  # seconds
                    resp = solana_client.confirm_transaction_with_spinner(
                        sig.value, commitment=commitment, timeout=timeout
                    )
                    if resp.value:
                        logging.info(f"✅ Buy transaction CONFIRMED for {to_addr}, sig: {sig.value}")
                        return sig.value
                    else:
                        logging.warning(f"⚠️ Buy transaction not confirmed within timeout")
                        # Still return the signature, as transaction might still succeed
                        return sig.value
                except Exception as confirm_error:
                    logging.warning(f"⚠️ Confirmation error: {str(confirm_error)[:100]}")
                    # Return signature anyway, as transaction might still succeed
                    return sig.value
                
            except Exception as swap_error:
                swap_attempts += 1
                logging.warning(f"⚠️ Swap attempt {swap_attempts} failed: {str(swap_error)[:100]}")
                
                # If first attempt failed, try with reduced amount
                if swap_attempts < max_swap_attempts:
                    reduced_lamports = int(lamports * 0.95)  # Reduce by 5%
                    quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={reduced_lamports}&slippage=3&maxAccounts=5&onlyDirectRoutes=true"
                    r = requests.get(quote_url, timeout=10)
                    
                    if r.status_code == 200:
                        quote = r.json()
                        logging.info(f"🔄 Retrying with simplified parameters and {reduced_lamports/1_000_000_000:.3f} SOL")
                    else:
                        break
                else:
                    break
        
        # If we've exhausted all attempts, try one last approach - even simpler transaction
        try:
            # Last resort - try with minimal transaction and higher slippage
            minimal_lamports = int(lamports * 0.9)  # Reduce by 10%
            minimal_quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={to_addr}&amount={minimal_lamports}&slippage=5&maxAccounts=3&onlyDirectRoutes=true"
            r = requests.get(minimal_quote_url, timeout=10)
            
            if r.status_code == 200:
                quote = r.json()
                if quote.get("routePlan"):
                    swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                        "userPublicKey": str(kp.pubkey()),
                        "wrapUnwrapSOL": True,
                        "quoteResponse": quote,
                        "computeUnitPriceMicroLamports": 2500,  # Higher compute price for priority
                        "asLegacyTransaction": True,  # Try legacy transaction format
                        "prioritizationFeeLamports": 15000,  # Higher priority fee
                        "dynamicComputeUnitLimit": True
                    }, timeout=10).json()
                    
                    if "swapTransaction" in swap:
                        tx_data = decode_transaction_blob(swap["swapTransaction"])
                        logging.info(f"🚀 Sending LAST RESORT BUY transaction for {to_addr}")
                        
                        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True))
                        logging.info(f"✅ Last resort buy transaction sent for {to_addr}, sig: {sig.value}")
                        return sig.value
        except Exception as last_attempt_error:
            logging.error(f"❌ Last resort buy failed: {str(last_attempt_error)[:100]}")
        
        return None
        
    except Exception as e:
        logging.error(f"❌ Buy completely failed for {to_addr}: {str(e)[:150]}")
        
        # Switch to fallback RPC if needed
        fallback_rpc()
        
        # Re-raise to trigger retry
        raise e

# Enhanced sell function with retry mechanism
@retry(stop=stop_after_attempt(MAX_TRANSACTION_ATTEMPTS), 
       wait=wait_exponential(multiplier=1, max=10),
       retry=retry_if_exception_type(Exception))
def real_sell_token(to_addr: str):
    """
    Enhanced sell function with robust error handling and retry logic
    """
    try:
        # Get wallet keypair
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # Get token balance to sell
        token_accounts = solana_client.get_token_accounts_by_owner(
            kp.pubkey(),
            {"mint": Pubkey.from_string(to_addr)}
        ).value
        
        if not token_accounts:
            logging.warning(f"❌ No token account found for {to_addr}")
            return None
            
        token_balance = int(token_accounts[0].account.data.parsed['info']['tokenAmount']['amount'])
        if token_balance <= 0:
            logging.warning(f"❌ Zero balance for {to_addr}")
            return None
            
        # Get initial quote
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=3&maxAccounts=10"
        logging.info(f"🔍 Getting sell quote for: {to_addr}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            
            # Try with higher slippage
            fallback_quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=5&maxAccounts=10"
            r = requests.get(fallback_quote_url, timeout=10)
            if r.status_code != 200:
                logging.error(f"❌ Jupiter sell quote retry failed with {r.status_code}")
                return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No sell route available for {to_addr}")
            
            # Try with a reduced amount as last resort
            reduced_balance = int(token_balance * 0.95)  # Try with 95% of balance
            reduced_quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={reduced_balance}&slippage=5&maxAccounts=5&onlyDirectRoutes=true"
            r = requests.get(reduced_quote_url, timeout=10)
            
            if r.status_code != 200 or "routePlan" not in r.json():
                logging.error(f"❌ No sell route available even with reduced amount")
                return None
                
            quote = r.json()

        # Execute the transaction
        swap_attempts = 0
        max_swap_attempts = 3  # Try three approaches for selling
        
        while swap_attempts < max_swap_attempts:
            try:
                swap_payload = {
                    "userPublicKey": str(kp.pubkey()),
                    "wrapUnwrapSOL": True,
                    "quoteResponse": quote,
                    "computeUnitPriceMicroLamports": 1000,
                    "asLegacyTransaction": False,
                    "prioritizationFeeLamports": 0,
                    "dynamicComputeUnitLimit": True
                }
                
                # Adjust parameters based on attempt number
                if swap_attempts == 1:
                    # Second attempt: Use higher priority
                    swap_payload["prioritizationFeeLamports"] = 10000
                    swap_payload["computeUnitPriceMicroLamports"] = 2000
                elif swap_attempts == 2:
                    # Third attempt: Use legacy transaction with maximum priority
                    swap_payload["asLegacyTransaction"] = True
                    swap_payload["prioritizationFeeLamports"] = 20000
                    swap_payload["computeUnitPriceMicroLamports"] = 3000
                
                swap = requests.post("https://quote-api.jup.ag/v6/swap", 
                                    json=swap_payload, 
                                    timeout=10).json()
                
                if "swapTransaction" not in swap:
                    swap_attempts += 1
                    logging.error(f"❌ No swap transaction returned for sell (attempt {swap_attempts})")
                    
                    # On failure, try with reduced amount for next attempt
                    if swap_attempts < max_swap_attempts:
                        reduced_amount = int(token_balance * (0.95 - (swap_attempts * 0.05)))  # Reduce more with each attempt
                        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={reduced_amount}&slippage={3 + swap_attempts}&maxAccounts={10 - swap_attempts * 2}&onlyDirectRoutes={swap_attempts > 0}"
                        r = requests.get(quote_url, timeout=10)
                        
                        if r.status_code == 200:
                            quote = r.json()
                            if "routePlan" in quote:
                                logging.info(f"🔄 Retrying sell with {reduced_amount/token_balance:.1%} of balance")
                            else:
                                logging.error(f"❌ No route for reduced amount")
                                # Continue anyway, will try different transaction parameters
                    
                    continue

                tx_data = decode_transaction_blob(swap["swapTransaction"])
                logging.info(f"🚀 Sending SELL transaction for {to_addr} (attempt {swap_attempts+1})")
                
                # Send transaction with optimized parameters
                tx_opts = TxOpts(
                    skip_preflight=True,
                    preflight_commitment="confirmed",
                    max_retries=3
                )
                
                sig = solana_client.send_raw_transaction(tx_data, opts=tx_opts)
                
                # Wait for confirmation with timeout
                try:
                    commitment = "confirmed"
                    timeout = 30  # seconds
                    resp = solana_client.confirm_transaction_with_spinner(
                        sig.value, commitment=commitment, timeout=timeout
                    )
                    if resp.value:
                        logging.info(f"✅ Sell transaction CONFIRMED for {to_addr}, sig: {sig.value}")
                        return sig.value
                    else:
                        logging.warning(f"⚠️ Sell transaction not confirmed within timeout")
                        # Still return the signature, as transaction might still succeed
                        return sig.value
                except Exception as confirm_error:
                    logging.warning(f"⚠️ Confirmation error: {str(confirm_error)[:100]}")
                    # Return signature anyway, as transaction might still succeed
                    return sig.value
                
            except Exception as swap_error:
                swap_attempts += 1
                logging.warning(f"⚠️ Sell swap attempt {swap_attempts} failed: {str(swap_error)[:100]}")
                
                # Try a different RPC endpoint for next attempt
                if swap_attempts < max_swap_attempts:
                    try:
                        # Cycle through RPC endpoints
                        endpoint_index = swap_attempts % len(RPC_ENDPOINTS)
                        new_endpoint = RPC_ENDPOINTS[endpoint_index]
                        solana_client = Client(new_endpoint)
                        logging.info(f"🔄 Switched to RPC endpoint: {new_endpoint}")
                    except Exception as rpc_error:
                        logging.warning(f"⚠️ RPC switch failed: {str(rpc_error)[:100]}")
        
        # If we've exhausted all attempts, try one absolute last approach - sell half
        try:
            # Absolute last resort - try selling half the balance with maximum slippage
            half_balance = int(token_balance * 0.5)  # Half of balance
            half_quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={half_balance}&slippage=10&maxAccounts=3&onlyDirectRoutes=true"
            r = requests.get(half_quote_url, timeout=10)
            
            if r.status_code == 200:
                quote = r.json()
                if quote.get("routePlan"):
                    swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
                        "userPublicKey": str(kp.pubkey()),
                        "wrapUnwrapSOL": True,
                        "quoteResponse": quote,
                        "computeUnitPriceMicroLamports": 5000,  # Maximum compute price
                        "asLegacyTransaction": True,  # Use legacy format
                        "prioritizationFeeLamports": 50000,  # Very high priority fee
                        "dynamicComputeUnitLimit": True
                    }, timeout=10).json()
                    
                    if "swapTransaction" in swap:
                        tx_data = decode_transaction_blob(swap["swapTransaction"])
                        logging.info(f"🚀 Sending ABSOLUTE LAST RESORT SELL transaction for {to_addr}")
                        
                        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True))
                        logging.info(f"✅ Last resort sell transaction sent for {to_addr}, sig: {sig.value}")
                        return sig.value
        except Exception as last_attempt_error:
            logging.error(f"❌ Last resort sell failed: {str(last_attempt_error)[:100]}")
        
        return None
        
    except Exception as e:
        logging.error(f"❌ Sell completely failed for {to_addr}: {str(e)[:150]}")
        
        # Switch to fallback RPC
        fallback_rpc()
        
        # Re-raise to trigger retry
        raise e

# === Check and sell function focused on 2x profit ===
async def check_and_sell_token(token, token_data):
    """
    Enhanced token profit checking focused on 2x target with robust selling
    """
    global daily_profit, successful_sells_today, successful_2x_sells
    
    try:
        # Get current price
        price_now = get_token_price(token)
        initial_price = token_data.get('initial_price', 0)
        
        # Fix type issues
        if isinstance(price_now, str):
            try:
                price_now = float(price_now)
            except:
                price_now = 0
                
        if isinstance(initial_price, str):
            try:
                initial_price = float(initial_price)
            except:
                initial_price = 0
        
        # Get buy amount
        buy_amount = token_data.get('buy_amount', BUY_AMOUNT_LAMPORTS)
        
        # Calculate time since purchase
        minutes_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 60
        
        # If we can't get price data yet
        if price_now <= 0:
            # Log status at intervals
            if int(minutes_since_buy) % PROFIT_CHECK_INTERVAL == 0:
                logging.info(f"⏳ Waiting for price data for {token} - held for {minutes_since_buy:.1f} minutes")
            
            # Force sell after max holding time if still no price
            if minutes_since_buy >= FORCE_SELL_MINUTES:
                logging.info(f"⚠️ FORCE SELLING {token} after {minutes_since_buy:.1f} minutes with no price data")
                
                # Try to get ANY price data as last resort
                try:
                    # Try Jupiter quote API
                    r = requests.get(f"https://quote-api.jup.ag/v6/quote?inputMint={token}&outputMint=So11111111111111111111111111111111111111112&amount=1000000&slippage=5", timeout=5)
                    if r.status_code == 200 and 'outAmount' in r.json():
                        out_amount = int(r.json().get('outAmount', 0))
                        if out_amount > 0:
                            estimated_price = out_amount / 1000000000 / 1000000
                            logging.info(f"✅ Last chance price estimate: ${estimated_price:.8f} for {token}")
                            price_now = estimated_price
                except Exception as e:
                    logging.error(f"❌ Last chance price estimate failed: {str(e)[:100]}")
                
                # Force sell
                sell_sig = real_sell_token(token)
                
                if sell_sig:
                    # Log the trade
                    profit = 0
                    if price_now > 0 and initial_price > 0:
                        profit = ((price_now - initial_price) / initial_price) * buy_amount / 1_000_000_000
                    
                    log_trade({
                        "type": "sell", 
                        "token": token,
                        "tx": sell_sig,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                        "price": price_now if price_now > 0 else (initial_price if initial_price > 0 else 0.00000001),
                        "profit": profit,
                        "reason": f"force sold after {minutes_since_buy:.1f} minutes with no price data"
                    })
                    
                    # Notify in Discord
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"⚠️ Force-sold {token} after {minutes_since_buy:.1f} minutes with no price data! https://solscan.io/tx/{sell_sig}")
                    
                    del bought_tokens[token]
                    
            return
            
        # Update initial price if we didn't have one
        if initial_price <= 0:
            token_data['initial_price'] = price_now
            logging.info(f"✅ Updated initial price for {token} to ${price_now:.8f}")
            return
            
        # Calculate current profit ratio
        price_ratio = price_now / initial_price
        
        # Log status at regular intervals
        if int(minutes_since_buy) % PROFIT_CHECK_INTERVAL == 0:
            approx_profit = ((price_now - initial_price) / initial_price) * buy_amount / 1_000_000_000
            logging.info(f"📈 Token {token} price ratio: {price_ratio:.2f}x (${approx_profit:.2f} profit) - held for {minutes_since_buy:.1f} minutes")
        
        # FOCUSED SELL CONDITIONS:
        # 1. Hit 2.0x target (primary goal)
        # 2. Stop loss at 0.85x
        # 3. Early take-profit if approaching daily target
        # 4. Force sell after max hold time
        should_sell = False
        sell_reason = ""
        
        # 1. Primary goal: 2x target
        if price_ratio >= SELL_PROFIT_TRIGGER:
            should_sell = True
            sell_reason = f"HIT 2X TARGET!!! ({price_ratio:.2f}x)"
            
        # 2. Stop loss
        elif price_ratio <= STOP_LOSS_TRIGGER:
            should_sell = True
            sell_reason = f"stop loss triggered ({price_ratio:.2f}x)"
            
        # 3. If close to daily target, take profits earlier
        elif daily_profit >= DAILY_PROFIT_TARGET * 0.8 and price_ratio >= 1.5:
            should_sell = True
            sell_reason = f"accelerating to daily target ({price_ratio:.2f}x)"
            
        # 4. Take profit based on holding time
        elif minutes_since_buy >= 60 and price_ratio >= 1.5:
            # If held over 1 hour with 1.5x, take profit
            should_sell = True
            sell_reason = f"time-based profit taking ({price_ratio:.2f}x after {minutes_since_buy:.1f}min)"
            
        # 5. Force sell after maximum hold time
        elif minutes_since_buy >= FORCE_SELL_MINUTES:
            should_sell = True
            sell_reason = f"maximum hold time reached ({price_ratio:.2f}x after {minutes_since_buy:.1f}min)"
                
        # Execute sell if conditions are met
        if should_sell:
            logging.info(f"🔄 Selling {token} - {sell_reason}")
            
            # Improved sell process with retry
            sell_sig = real_sell_token(token)
            
            if sell_sig:
                profit = ((price_now - initial_price) / initial_price) * buy_amount / 1_000_000_000
                
                log_trade({
                    "type": "sell", 
                    "token": token,
                    "tx": sell_sig,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                    "price": price_now,
                    "profit": profit,
                    "reason": sell_reason
                })
                
                # Increment success counters
                successful_sells_today += 1
                if price_ratio >= 2.0:
                    successful_2x_sells += 1
                
                # Notify in Discord
                if DISCORD_NEWS_CHANNEL_ID:
                    channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                    if channel:
                        await channel.send(f"💰 Auto-sold {token} at ${price_now:.6f} ({price_ratio:.2f}x, ${profit:.2f} profit) - {sell_reason}! https://solscan.io/tx/{sell_sig}")
                
                del bought_tokens[token]
                
                # Special notifications for 2x trades
                if price_ratio >= 2.0:
                    logging.info(f"💎 2X TARGET REACHED! {token} sold at {price_ratio:.2f}x return!")
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"💎 2X TARGET REACHED! {token} sold at {price_ratio:.2f}x return! Profit: ${profit:.2f}")
                            
                # Daily profit target notification
                daily_profit_amount = summarize_daily_profit()
                if daily_profit_amount >= DAILY_PROFIT_TARGET:
                    logging.info(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
                    if DISCORD_NEWS_CHANNEL_ID:
                        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                        if channel:
                            await channel.send(f"🎯 DAILY PROFIT TARGET REACHED! ${daily_profit_amount:.2f} / ${DAILY_PROFIT_TARGET:.2f}")
    
    except Exception as e:
        logging.error(f"❌ Error checking token {token}: {str(e)[:150]}")
