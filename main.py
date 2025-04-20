if not quote.get("routePlan"):
            logging.warning(f"❌ No swap route available for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None

        # Check price impact
        price_impact = quote.get('priceImpactPct', 0) * 100
        if price_impact > 10 and not skip_validation:  # If price impact is greater than 10%
            logging.warning(f"❌ Price impact too high ({price_impact:.2f}%) for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None

        # Add random delay to avoid front-running
        if not skip_validation:  # Skip delay for high-priority tokens
            delay = random.uniform(0.1, 2.0)
            await asyncio.sleep(delay)

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 1000,  # Pay for priority
            "asLegacyTransaction": True
        }, timeout=10).json()

        if "swapTransaction" not in swap:
            logging.error(f"❌ No swap transaction returned for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending BUY transaction for {to_addr}: {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="processed"))
        
        # Wait for confirmation
        confirmation_timeout = 20 if skip_validation else 30  # Faster timeout for high-priority tokens
        for i in range(confirmation_timeout):
            try:
                conf = solana_client.confirm_transaction(sig.value)
                if conf.value:
                    logging.info(f"✅ Buy transaction confirmed for {to_addr}")
                    return sig.value
                await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(1)
                continue
                
        return sig.value
    except Exception as e:
        logging.error(f"❌ Buy failed for {to_addr}: {e}")
        fallback_rpc()
        return None
    finally:
        async with operation_lock:
            pending_operations -= 1

async def real_sell_token(to_addr: str, sell_percentage=100):
    """Sell a percentage of token holdings."""
    global pending_operations
    
    async with operation_lock:
        if pending_operations >= MAX_CONCURRENT_OPERATIONS:
            logging.warning(f"⚠️ Too many pending operations ({pending_operations}), delaying sell")
            return None
        pending_operations += 1
    
    try:
        kp = get_phantom_keypair()
        to_addr = sanitize_token_address(to_addr)
        
        # Get token balance to sell
        token_accounts = solana_client.get_token_accounts_by_owner(
            kp.pubkey(),
            {"mint": Pubkey.from_string(to_addr)}
        ).value
        
        if not token_accounts:
            logging.warning(f"❌ No token account found for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None
            
        total_balance = int(token_accounts[0].account.data.parsed['info']['tokenAmount']['amount'])
        if total_balance <= 0:
            logging.warning(f"❌ Zero balance for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None
            
        # Calculate amount to sell based on percentage
        token_balance = int(total_balance * sell_percentage / 100)
        
        quote_url = f"https://quote-api.jup.ag/v6/quote?inputMint={to_addr}&outputMint=So11111111111111111111111111111111111111112&amount={token_balance}&slippage=1"
        logging.info(f"🔍 Getting sell quote from: {quote_url}")
        
        r = requests.get(quote_url, timeout=10)
        if r.status_code != 200:
            logging.warning(f"❌ Jupiter quote API returned {r.status_code}")
            async with operation_lock:
                pending_operations -= 1
            return None
            
        quote = r.json()
        
        if not quote.get("routePlan"):
            logging.warning(f"❌ No sell route available for {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None

        swap = requests.post("https://quote-api.jup.ag/v6/swap", json={
            "userPublicKey": str(kp.pubkey()),
            "wrapUnwrapSOL": True,
            "quoteResponse": quote,
            "computeUnitPriceMicroLamports": 1000,  # Pay for priority
            "asLegacyTransaction": True
        }, timeout=10).json()

        if "swapTransaction" not in swap:
            logging.error(f"❌ No swap transaction returned for selling {to_addr}")
            async with operation_lock:
                pending_operations -= 1
            return None

        tx_data = decode_transaction_blob(swap["swapTransaction"])
        logging.info(f"🚀 Sending SELL transaction for {to_addr} ({sell_percentage}%): {tx_data.hex()[:80]}...")
        sig = solana_client.send_raw_transaction(tx_data, opts=TxOpts(skip_preflight=True, preflight_commitment="processed"))
        
        # Wait for confirmation
        for i in range(30):  # Try for 30 seconds
            try:
                conf = solana_client.confirm_transaction(sig.value)
                if conf.value:
                    logging.info(f"✅ Sell transaction confirmed for {to_addr} ({sell_percentage}%)")
                    return sig.value
                await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(1)
                continue
                
        return sig.value
    except Exception as e:
        logging.error(f"❌ Sell failed for {to_addr}: {e}")
        fallback_rpc()
        return None
    finally:
        async with operation_lock:
            pending_operations -= 1

async def handle_token_buy(token_address, amount, source="auto", skip_validation=False):
    """Handle the entire buy process for a token."""
    # Skip if we're at max tokens
    if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
        logging.info(f"🛑 Already holding maximum of {MAX_TOKENS_TO_HOLD} tokens. Skipping buy.")
        return None
        
    # Check if we're already holding this token
    if token_address in bought_tokens:
        logging.info(f"⚠️ Already holding {token_address}, skipping duplicate buy")
        return None
        
    # Check if we should be trading (daily loss limit)
    if await check_daily_loss_limit():
        logging.warning(f"⚠️ Daily loss limit reached, skipping buy")
        return None
        
    # Execute the buy
    logging.info(f"💰 Attempting to buy token: {token_address} from source: {source}")
    sig = await real_buy_token(token_address, amount, skip_validation=skip_validation)
    
    if sig:
        # Get token price and info
        price = get_token_price(token_address)
        
        # Add to our holdings
        bought_tokens[token_address] = {
            'buy_sig': sig,
            'buy_time': datetime.utcnow(),
            'token': token_address,
            'initial_price': price,
            'source': source,
            'buy_amount_lamports': amount
        }
        
        # Log the trade
        log_trade({
            "type": "buy", 
            "token": token_address, 
            "tx": sig, 
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
            "price": price,
            "source": source
        })
        
        # Notify in Discord
        if DISCORD_NEWS_CHANNEL_ID:
            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
            if channel:
                await channel.send(f"🚀 Auto-bought {token_address} at ${price:.6f} from {source}! https://solscan.io/tx/{sig}")
                
        return sig
    
    return None

async def handle_staged_selling(token, token_data):
    """Implement a staged selling strategy based on profit targets."""
    price_now = get_token_price(token)
    initial_price = token_data['initial_price']
    
    if price_now <= 0 or initial_price <= 0:
        return False
        
    price_ratio = price_now / initial_price
    hours_since_buy = (datetime.utcnow() - token_data['buy_time']).total_seconds() / 3600
    
    # Check if we've already partially sold this token
    sell_stages_completed = token_data.get('sell_stages_completed', 0)
    
    # Sell conditions
    should_sell = False
    sell_percentage = 0
    sell_reason = ""
    
    # Take profit levels - sell in stages
    if sell_stages_completed == 0 and price_ratio >= TAKE_PROFIT_LEVELS[0]:
        should_sell = True
        sell_percentage = 33  # Sell 33% at first target
        sell_reason = f"first profit target reached ({price_ratio:.2f}x)"
        token_data['sell_stages_completed'] = 1
        
    elif sell_stages_completed == 1 and price_ratio >= TAKE_PROFIT_LEVELS[1]:
        should_sell = True
        sell_percentage = 50  # Sell 50% of remaining (33% of original)
        sell_reason = f"second profit target reached ({price_ratio:.2f}x)"
        token_data['sell_stages_completed'] = 2
        
    elif sell_stages_completed == 2 and price_ratio >= TAKE_PROFIT_LEVELS[2]:
        should_sell = True
        sell_percentage = 100  # Sell remaining
        sell_reason = f"final profit target reached ({price_ratio:.2f}x)"
        
    # Stop loss - full sell
    elif price_ratio <= STOP_LOSS_TRIGGER:
        should_sell = True
        sell_percentage = 100
        sell_reason = f"stop loss triggered ({price_ratio:.2f}x)"
        
    # Time-based exit
    elif hours_since_buy >= 24:
        should_sell = True
        sell_percentage = 100
        sell_reason = f"time-based exit ({hours_since_buy:.1f} hours)"
        
    # Execute sell if needed
    if should_sell:
        logging.info(f"🔄 Selling {token} ({sell_percentage}%) - {sell_reason}")
        sell_sig = await real_sell_token(token, sell_percentage)
        
        if sell_sig:
            profit_percentage = ((price_now - initial_price) / initial_price) * 100
            profit_amount = ((price_now - initial_price) / initial_price) * token_data['buy_amount_lamports'] / 1_000_000_000 * (sell_percentage / 100)
            
            # Log the trade
            log_trade({
                "type": "sell", 
                "token": token,
                "tx": sell_sig,
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                "price": price_now,
                "profit": profit_amount,
                "profit_percent": profit_percentage,
                "reason": sell_reason,
                "sell_percentage": sell_percentage
            })
            
            # Notify in Discord
            if DISCORD_NEWS_CHANNEL_ID:
                channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
                if channel:
                    await channel.send(f"💰 Auto-sold {token} ({sell_percentage}%) at ${price_now:.6f} ({price_ratio:.2f}x, ${profit_amount:.2f} profit) - {sell_reason}! https://solscan.io/tx/{sell_sig}")
            
            # If we sold everything, remove from our holdings
            if sell_percentage == 100:
                del bought_tokens[token]
                return True
                
            return False  # Partial sell, keep tracking
    
    return False  # No sell executed

async def auto_snipe():
    """Main function to automatically find and trade tokens."""
    await bot.wait_until_ready()
    logging.info("🔍 Auto-snipe task started")
    
    # Start the specialized monitoring tasks
    if QUICKNODE_WSS:
        bot.loop.create_task(start_mempool_monitoring())
    bot.loop.create_task(monitor_pump_fun())
    
    while not bot.is_closed():
        try:
            # Reset daily stats at midnight
            now = datetime.utcnow()
            if now.hour == 0 and now.minute < 5:
                logging.info("🔄 Resetting daily statistics")
                daily_profit = 0
                daily_starting_balance = log_wallet_balance()
            
            # Skip if we're at max tokens
            if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                logging.info(f"🛑 Already holding maximum of {MAX_TOKENS_TO_HOLD} tokens. Checking existing holdings.")
                
                # Just check existing holdings
                for token, token_data in list(bought_tokens.items()):
                    await handle_staged_selling(token, token_data)
                
                await asyncio.sleep(15)
                continue
            
            # Check if we've hit daily loss limit
            if await check_daily_loss_limit():
                # Just monitor existing positions but don't open new ones
                for token, token_data in list(bought_tokens.items()):
                    await handle_staged_selling(token, token_data)
                
                await asyncio.sleep(60)
                continue
            
            # Get tokens from all sources
            tokens = await aggregate_token_sources()
            
            # Prioritize tokens that need immediate action
            high_priority_tokens = tokens[:5]  # Top 5 tokens
            normal_priority_tokens = tokens[5:15]  # Next 10 tokens
            
            # First process high priority tokens with less validation
            for token in high_priority_tokens:
                if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                    break
                
                # Use larger buy amount for high priority tokens
                await handle_token_buy(token, BUY_AMOUNT_LAMPORTS * 2, source="high-priority", skip_validation=True)
            
            # Then process normal priority tokens with full validation
            for token in normal_priority_tokens:
                if len(bought_tokens) >= MAX_TOKENS_TO_HOLD:
                    break
                
                await handle_token_buy(token, BUY_AMOUNT_LAMPORTS, source="normal-priority", skip_validation=False)
            
            # Check existing positions for selling
            for token, token_data in list(bought_tokens.items()):
                await handle_staged_selling(token, token_data)
            
            # Summarize current status
            total_profit = summarize_daily_profit()
            total_balance = log_wallet_balance()
            
            logging.info(f"📊 STATUS: Holding {len(bought_tokens)}/{MAX_TOKENS_TO_HOLD} tokens, Daily profit: ${total_profit:.2f}, Balance: {total_balance:.4f} SOL")
            
        except Exception as e:
            logging.error(f"❌ Error in auto_snipe: {e}")
            
        await asyncio.sleep(30)

# === Discord Commands ===
@tree.command(name="buy", description="Buy a token using SOL")
async def buy_slash(interaction: discord.Interaction, token: str, amount: float = 0.01):
    """Command to manually buy a token."""
    await interaction.response.send_message(f"Buying {token} with {amount} SOL...")
    
    lamports = int(amount * 1_000_000_000)
    sig = await handle_token_buy(token, lamports, source="manual")
    
    if sig:
        price = get_token_price(token)
        await interaction.followup.send(f"✅ Bought {token} at ${price:.6f}! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Buy failed for {token}. Check logs.")

@tree.command(name="sell", description="Sell a token for SOL")
async def sell_slash(interaction: discord.Interaction, token: str, percentage: float = 100):
    """Command to manually sell a token."""
    await interaction.response.send_message(f"Selling {percentage}% of {token}...")
    
    initial_price = 0
    buy_amount = BUY_AMOUNT_LAMPORTS
    
    if token in bought_tokens:
        initial_price = bought_tokens[token]['initial_price']
        buy_amount = bought_tokens[token].get('buy_amount_lamports', BUY_AMOUNT_LAMPORTS)
    
    sig = await real_sell_token(token, percentage)
    
    if sig:
        current_price = get_token_price(token)
        profit = 0
        
        if initial_price > 0 and current_price > 0:
            profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000 * (percentage / 100)
        
        log_trade({
            "type": "sell",
            "token": token,
            "tx": sig,
            "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
            "price": current_price,
            "profit": profit,
            "sell_percentage": percentage,
            "reason": "manual"
        })
        
        if token in bought_tokens and percentage >= 99:
            del bought_tokens[token]
            
        await interaction.followup.send(f"✅ Sold {percentage}% of {token} at ${current_price:.6f} (Profit: ${profit:.2f})! https://solscan.io/tx/{sig}")
    else:
        await interaction.followup.send(f"❌ Sell failed for {token}. Check logs.")

@tree.command(name="profit", description="Check today's trading profit")
async def profit_slash(interaction: discord.Interaction):
    """Command to check daily profit."""
    total_profit = summarize_daily_profit()
    await interaction.response.send_message(f"📊 Today's profit so far: ${total_profit:.2f}")

@tree.command(name="balance", description="Check wallet balance")
async def balance_slash(interaction: discord.Interaction):
    """Command to check wallet balance."""
    balance = log_wallet_balance()
    await interaction.response.send_message(f"💰 Current wallet balance: {balance:.4f} SOL")

@tree.command(name="holdings", description="Check current token holdings")
async def holdings_slash(interaction: discord.Interaction):
    """Command to view current token holdings."""
    if not bought_tokens:
        await interaction.response.send_message("No tokens currently held.")
        return
        
    holdings_text = "Current Holdings:\n```"
    total_profit = 0
    
    for token, data in bought_tokens.items():
        current_price = get_token_price(token)
        initial_price = data['initial_price']
        profit_percent = ((current_price - initial_price) / initial_price * 100) if initial_price > 0 else 0
        buy_time = data['buy_time'].strftime("%H:%M:%S")
        source = data.get('source', 'unknown')
        hours_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 3600
        buy_amount = data.get('buy_amount_lamports', BUY_AMOUNT_LAMPORTS) / 1_000_000_000
        
        # Calculate unrealized profit
        unrealized_profit = ((current_price - initial_price) / initial_price) * buy_amount if initial_price > 0 else 0
        total_profit += unrealized_profit
        
        holdings_text += f"{token[:8]}... | Buy: ${initial_price:.6f} | Now: ${current_price:.6f} | {profit_percent:.2f}% | ${unrealized_profit:.2f} | {hours_held:.1f}h | {source}\n"
    
    holdings_text += f"\nTotal Unrealized Profit: ${total_profit:.2f}\n```"
    
    await interaction.response.send_message(holdings_text)

@tree.command(name="debug", description="Debug token fetching")
async def debug_slash(interaction: discord.Interaction):
    """Command to debug token fetching."""
    await interaction.response.send_message("Running token fetch debug...")
    
    try:
        # Test token sources
        tokens = await aggregate_token_sources()
        
        debug_info = f"""Debug Results:
        
Found {len(tokens)} potential tokens from all sources

Sample tokens:
{', '.join([t[:8]+'...' for t in tokens[:5]]) if tokens else 'None'}

Active Operations: {pending_operations}
Tokens Held: {len(bought_tokens)}/{MAX_TOKENS_TO_HOLD}
Mempool Monitoring: {'Active' if mempool_monitoring_active else 'Inactive'}
Pump.fun Monitoring: {'Active' if pump_fun_monitoring_active else 'Inactive'}
"""
        await interaction.followup.send(debug_info)
    except Exception as e:
        await interaction.followup.send(f"Debug failed: {e}")

@tree.command(name="chart", description="Generate a profit chart")
async def chart_slash(interaction: discord.Interaction):
    """Command to generate a profit chart."""
    await interaction.response.send_message("Generating profit chart...")
    
    try:
        # Extract profit data from trade log
        timestamps = []
        profits = []
        cumulative_profit = 0
        
        for trade in trade_log:
            if trade.get("type") == "sell" and "profit" in trade:
                trade_time = datetime.strptime(trade.get("timestamp"), "%H:%M:%S")
                # Adjust time to today's date for proper display
                today = datetime.utcnow().date()
                trade_time = datetime.combine(today, trade_time.time())
                
                timestamps.append(trade_time)
                cumulative_profit += trade.get("profit", 0)
                profits.append(cumulative_profit)
        
        if not timestamps:
            await interaction.followup.send("No profit data available yet.")
            return
            
        # Create the chart
        plt.figure(figsize=(10, 6))
        plt.plot(timestamps, profits, marker='o', linestyle='-', color='green')
        plt.title('Cumulative Trading Profit')
        plt.xlabel('Time')
        plt.ylabel('Profit (USD)')
        plt.grid(True)
        plt.savefig('profit_chart.png')
        
        await interaction.followup.send(file=discord.File('profit_chart.png'))
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to generate chart: {e}")

@tree.command(name="config", description="Update bot configuration")
async def config_slash(interaction: discord.Interaction, 
                      buy_amount: float = None, 
                      profit_target: float = None,
                      stop_loss: float = None,
                      max_tokens: int = None):
    """Command to update bot configuration."""
    global BUY_AMOUNT_LAMPORTS, SELL_PROFIT_TRIGGER, STOP_LOSS_TRIGGER, MAX_TOKENS_TO_HOLD
    
    changes = []
    
    if buy_amount is not None:
        BUY_AMOUNT_LAMPORTS = int(buy_amount * 1_000_000_000)
        changes.append(f"Buy amount updated to {buy_amount} SOL")
        
    if profit_target is not None:
        SELL_PROFIT_TRIGGER = profit_target
        changes.append(f"Profit target updated to {profit_target}x")
        
    if stop_loss is not None:
        STOP_LOSS_TRIGGER = stop_loss
        changes.append(f"Stop loss updated to {stop_loss}x")
        
    if max_tokens is not None:
        MAX_TOKENS_TO_HOLD = max_tokens
        changes.append(f"Max tokens updated to {max_tokens}")
        
    if changes:
        await interaction.response.send_message("✅ Configuration updated:\n- " + "\n- ".join(changes))
    else:
        await interaction.response.send_message(f"""Current configuration:
- Buy amount: {BUY_AMOUNT_LAMPORTS / 1_000_000_000} SOL
- Profit target: {SELL_PROFIT_TRIGGER}x
- Stop loss: {STOP_LOSS_TRIGGER}x
- Max tokens: {MAX_TOKENS_TO_HOLD}
""")

@tree.command(name="clear", description="Clear all holdings (emergency)")
async def clear_slash(interaction: discord.Interaction):
    """Emergency command to sell all holdings."""
    await interaction.response.send_message("⚠️ EMERGENCY: Selling all holdings...")
    
    if not bought_tokens:
        await interaction.followup.send("No tokens currently held.")
        return
        
    results = []
    
    for token, data in list(bought_tokens.items()):
        try:
            sig = await real_sell_token(token, 100)
            
            if sig:
                current_price = get_token_price(token)
                initial_price = data['initial_price'] if 'initial_price' in data else 0
                profit = 0
                
                if initial_price > 0 and current_price > 0:
                    profit = ((current_price - initial_price) / initial_price) * data.get('buy_amount_lamports', BUY_AMOUNT_LAMPORTS) / 1_000_000_000
                
                log_trade({
                    "type": "sell",
                    "token": token,
                    "tx": sig,
                    "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                    "price": current_price,
                    "profit": profit,
                    "reason": "emergency"
                })
                
                del bought_tokens[token]
                results.append(f"✅ Sold {token}: ${profit:.2f} profit")
            else:
                results.append(f"❌ Failed to sell {token}")
        except Exception as e:
            results.append(f"❌ Error selling {token}: {str(e)}")
    
    await interaction.followup.send("Emergency sell complete:\n" + "\n".join(results))

@bot.event
async def on_ready():
    """Event handler when bot is ready."""
    await tree.sync()
    logging.info(f"✅ Logged in as {bot.user}")
    
    # Initialize daily starting balance
    global daily_starting_balance
    daily_starting_balance = log_wallet_balance()
    
    # Start auto-sniping task
    bot.loop.create_task(auto_snipe())
    
    logging.info("🚀 Bot fully ready: Commands, Auto-sniping, Mempool Monitoring Active")
    
    # Notify in Discord
    if DISCORD_NEWS_CHANNEL_ID:
        channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
        if channel:
            await channel.send(f"🚀 Advanced Solana Meme Coin Sniper Bot started! Monitoring for opportunities...")

# Load existing trade log if available
try:
    with open("trade_log.json", "r") as f:
        trade_log = json.load(f)
    logging.info(f"✅ Loaded {len(trade_log)} previous trades")
except FileNotFoundError:
    logging.info("No previous trade log found. Starting fresh.")

# Start the bot
if __name__ == "__main__":
    logging.info("🚀 Starting Advanced Solana Meme Coin Sniper Bot...")
    bot.run(DISCORD_TOKEN)
