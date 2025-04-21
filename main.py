@tree.command(name="holdings", description="Check current token holdings")
async def holdings_slash(interaction: discord.Interaction):
    """Enhanced holdings command with detailed metrics"""
    if not bought_tokens:
        await interaction.response.send_message("No tokens currently held.")
        return
        
    holdings_text = "**Current Holdings:**\n"
    total_value = 0
    tokens_with_prices = 0
    tokens_without_prices = 0
    potential_profit = 0
    
    # Sort tokens by time held (oldest first)
    sorted_tokens = sorted(bought_tokens.items(), key=lambda x: x[1]['buy_time'])
    
    for token, data in sorted_tokens:
        current_price = get_token_price(token)
        initial_price = data['initial_price']
        buy_amount = data.get('buy_amount', BUY_AMOUNT_LAMPORTS)
        buy_amount_sol = buy_amount / 1_000_000_000
        
        # Handle string conversion
        if isinstance(current_price, str):
            try:
                current_price = float(current_price)
            except:
                current_price = 0
                
        if isinstance(initial_price, str):
            try:
                initial_price = float(initial_price)
            except:
                initial_price = 0
        
        minutes_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
        
        if current_price > 0 and initial_price > 0:
            price_ratio = current_price / initial_price
            profit_percent = (price_ratio - 1) * 100
            estimated_value = buy_amount_sol * price_ratio
            total_value += estimated_value
            tokens_with_prices += 1
            
            token_profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
            potential_profit += token_profit
            
            # Color coding based on performance
            if price_ratio >= 1.8:  # Almost 2x
                emoji = "🔥"  # Fire for near target
            elif price_ratio >= 1.2:  # Good profit
                emoji = "💰"  # Money bag for profit
            elif price_ratio >= 0.9:  # Near break-even
                emoji = "⚖️"  # Balance for near break-even
            else:  # Loss
                emoji = "📉"  # Chart down for loss
                
            holdings_text += f"{emoji} {token}: {price_ratio:.2f}x ({profit_percent:.1f}%) - ${token_profit:.2f} profit - Held {minutes_held:.1f}min\n"
        else:
            tokens_without_prices += 1
            if initial_price > 0:
                holdings_text += f"⏳ {token}: No current price (initial ${initial_price:.8f}) - Held {minutes_held:.1f}min\n"
            else:
                holdings_text += f"⏳ {token}: No price data yet - Held {minutes_held:.1f}min\n"
    
    # Summary stats
    holdings_text += f"\n**Summary:**\n"
    holdings_text += f"Total tokens: {len(bought_tokens)}\n"
    holdings_text += f"Tokens with prices: {tokens_with_prices}\n"
    holdings_text += f"Tokens awaiting prices: {tokens_without_prices}\n"
    holdings_text += f"Total estimated value: ${total_value:.2f}\n"
    holdings_text += f"Potential profit: ${potential_profit:.2f}\n"
    
    # Check if message is too long for Discord (limit is 2000 chars)
    if len(holdings_text) > 1950:
        # Trim message if too long
        holdings_text = holdings_text[:1900] + "\n... (message trimmed due to length)"
    
    await interaction.response.send_message(holdings_text)

@tree.command(name="buy", description="Buy a specific token")
@app_commands.describe(token_address="Token mint address to buy", amount_sol="Amount in SOL to buy with (default: 0.15)")
async def buy_slash(interaction: discord.Interaction, token_address: str, amount_sol: float = 0.15):
    """Manual buy command with amount option"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Convert SOL to lamports
        amount_lamports = int(amount_sol * 1_000_000_000)
        
        # Cap the amount at 2 SOL for safety
        if amount_lamports > 2_000_000_000:
            amount_lamports = 2_000_000_000
            await interaction.followup.send(f"⚠️ Amount capped at 2 SOL for safety.")
        
        # Validate token address format
        token_address = sanitize_token_address(token_address)
        
        # Try to buy the token
        sig = real_buy_token(token_address, amount_lamports)
        
        if sig:
            price = get_token_price(token_address)
            
            # Handle string price
            if isinstance(price, str):
                try:
                    price = float(price)
                except:
                    price = 0
                    
            bought_tokens[token_address] = {
                'buy_sig': sig,
                'buy_time': datetime.utcnow(),
                'token': token_address,
                'initial_price': price,
                'buy_amount': amount_lamports
            }
            
            log_trade({
                "type": "buy", 
                "token": token_address, 
                "tx": sig, 
                "timestamp": datetime.utcnow().strftime("%H:%M:%S"), 
                "price": price,
                "amount_lamports": amount_lamports,
                "manual": True
            })
            
            await interaction.followup.send(f"✅ Bought {token_address} with {amount_sol} SOL! Transaction: https://solscan.io/tx/{sig}")
        else:
            await interaction.followup.send(f"❌ Failed to buy {token_address}. Check logs for details.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@tree.command(name="sell", description="Sell a specific token")
@app_commands.describe(token_address="Token mint address to sell")
async def sell_slash(interaction: discord.Interaction, token_address: str):
    """Manual sell command"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Validate token address format
        token_address = sanitize_token_address(token_address)
        
        # Try to sell the token
        sig = real_sell_token(token_address)
        
        if sig:
            if token_address in bought_tokens:
                # Calculate profit if we have buy data
                initial_price = bought_tokens[token_address].get('initial_price', 0)
                buy_amount = bought_tokens[token_address].get('buy_amount', BUY_AMOUNT_LAMPORTS)
                current_price = get_token_price(token_address)
                
                # Handle string conversion
                if isinstance(current_price, str):
                    try:
                        current_price = float(current_price)
                    except:
                        current_price = 0
                        
                if isinstance(initial_price, str):
                    try:
                        initial_price = float(initial_price)
                    except:
                        initial_price = 0
                
                if current_price > 0 and initial_price > 0:
                    profit = ((current_price - initial_price) / initial_price) * buy_amount / 1_000_000_000
                    price_ratio = current_price / initial_price
                    
                    log_trade({
                        "type": "sell", 
                        "token": token_address,
                        "tx": sig,
                        "timestamp": datetime.utcnow().strftime("%H:%M:%S"),
                        "price": current_price,
                        "profit": profit,
                        "reason": "manual sell",
                        "manual": True
                    })
                    
                    await interaction.followup.send(f"✅ Sold {token_address} at ${current_price:.6f} ({price_ratio:.2f}x, ${profit:.2f} profit)! Transaction: https://solscan.io/tx/{sig}")
                else:
                    await interaction.followup.send(f"✅ Sold {token_address} (profit unknown)! Transaction: https://solscan.io/tx/{sig}")
                
                # Remove from bought_tokens
                if token_address in bought_tokens:
                    del bought_tokens[token_address]
            else:
                await interaction.followup.send(f"✅ Sold {token_address}! Transaction: https://solscan.io/tx/{sig}")
        else:
            await interaction.followup.send(f"❌ Failed to sell {token_address}. Check logs for details.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

@tree.command(name="chart", description="Generate a profit chart")
async def chart_slash(interaction: discord.Interaction):
    """Generate and display a profit chart"""
    await interaction.response.defer(thinking=True)
    
    try:
        # Extract profit data from trade log
        if not trade_log:
            await interaction.followup.send("No trade data available to create chart.")
            return
            
        # Extract sell entries with profit data
        profit_entries = [entry for entry in trade_log if entry.get("type") == "sell" and "profit" in entry]
        
        if not profit_entries:
            await interaction.followup.send("No profit data available to create chart.")
            return
            
        # Extract timestamps and profits
        timestamps = []
        profits = []
        cumulative_profit = 0
        
        for entry in profit_entries:
            # Convert timestamp string to datetime if needed
            if isinstance(entry.get("timestamp"), str):
                time_obj = datetime.strptime(entry.get("timestamp"), "%H:%M:%S")
                # Use today's date with the time from the log
                timestamp = datetime.now().replace(hour=time_obj.hour, minute=time_obj.minute, second=time_obj.second)
            else:
                timestamp = entry.get("timestamp", datetime.now())
                
            profit = entry.get("profit", 0)
            cumulative_profit += profit
            
            timestamps.append(timestamp)
            profits.append(cumulative_profit)
            
        # Create the chart
        plt.figure(figsize=(10, 6))
        plt.plot(timestamps, profits, marker='o', linestyle='-', color='green')
        plt.axhline(y=0, color='r', linestyle='-', alpha=0.3)
        plt.axhline(y=DAILY_PROFIT_TARGET, color='g', linestyle='--', alpha=0.5, label=f"${DAILY_PROFIT_TARGET} Target")
        
        plt.title('Cumulative Trading Profit')
        plt.xlabel('Time')
        plt.ylabel('Profit (USD)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        
        # Format y-axis as dollars
        plt.gca().yaxis.set_major_formatter('${x:.0f}')
        
        # Save chart to file
        chart_path = "profit_chart.png"
        plt.tight_layout()
        plt.savefig(chart_path)
        plt.close()
        
        # Send the chart as an attachment
        await interaction.followup.send(file=discord.File(chart_path))
        
    except Exception as e:
        logging.error(f"❌ Error generating chart: {e}")
        await interaction.followup.send(f"❌ Error generating chart: {str(e)}")

@tree.command(name="analyze", description="Get AI analysis of current market conditions")
async def analyze_slash(interaction: discord.Interaction):
    """Generate AI analysis of current market trends using GPT-4"""
    await interaction.response.defer(thinking=True)
    
    try:
        # First, gather some data from our trading history
        if not trade_log:
            await interaction.followup.send("Not enough trading data for analysis.")
            return
        
        # Count successful vs failed trades
        sells = [entry for entry in trade_log if entry.get("type") == "sell"]
        profitable_sells = [entry for entry in sells if entry.get("profit", 0) > 0]
        successful_ratio = len(profitable_sells) / len(sells) if sells else 0
        
        # Get holding time stats
        holding_times = []
        for token, data in bought_tokens.items():
            minutes_held = (datetime.utcnow() - data['buy_time']).total_seconds() / 60
            holding_times.append(minutes_held)
            
        avg_hold_time = sum(holding_times) / len(holding_times) if holding_times else 0
        
        # Calculate average profit per trade
        total_profit = sum(entry.get("profit", 0) for entry in sells)
        avg_profit = total_profit / len(sells) if sells else 0
        
        # Gather tokens with the best performance
        best_tokens = []
        for entry in profitable_sells[-10:]:  # Look at recent profitable sells
            if "token" in entry and "profit" in entry:
                best_tokens.append((entry["token"], entry["profit"]))
                
        # Sort by profit
        best_tokens.sort(key=lambda x: x[1], reverse=True)
        
        # Create prompt for GPT-4
        system_prompt = "You are an expert crypto trading assistant. Analyze the provided trading data and give insights."
        
        user_prompt = f"""
        Trading Data Summary:
        - Total trades: {len(trade_log)}
        - Successful trades ratio: {successful_ratio:.2f}
        - Average profit per trade: ${avg_profit:.2f}
        - Average holding time: {avg_hold_time:.1f} minutes
        - Current tokens held: {len(bought_tokens)}
        - Daily profit so far: ${daily_profit:.2f}
        
        Best performing tokens:
        {best_tokens[:5]}
        
        Based on this data, provide a brief analysis of:
        1. Current market conditions
        2. Recommended strategy adjustments
        3. Opportunities to watch for
        
        Keep the analysis under 400 words and focus on actionable insights.
        """
        
        # Call GPT-4 for analysis
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        analysis = response.choices[0].message.content
        
        # Send the analysis
        await interaction.followup.send(f"**📊 Market Analysis:**\n\n{analysis}")
        
    except Exception as e:
        logging.error(f"❌ Error generating analysis: {e}")
        await interaction.followup.send(f"❌ Error generating analysis: {str(e)}")

@tree.command(name="newrpc", description="Test and switch to the fastest RPC endpoint")
async def newrpc_slash(interaction: discord.Interaction):
    """Test all RPC endpoints and switch to the fastest one"""
    await interaction.response.defer(thinking=True)
    
    try:
        old_rpc = solana_client.endpoint
        best_rpc = get_best_rpc()
        
        if best_rpc and best_rpc != old_rpc:
            await interaction.followup.send(f"✅ Switched from {old_rpc} to faster RPC: {best_rpc}")
        elif best_rpc == old_rpc:
            await interaction.followup.send(f"✅ Current RPC endpoint ({old_rpc}) is already the fastest.")
        else:
            await interaction.followup.send("❌ Failed to find a faster RPC endpoint.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error testing RPC endpoints: {str(e)}")

# Add daily stats reset function
async def reset_daily_stats():
    """Reset daily stats at midnight UTC"""
    global daily_profit, total_buys_today, successful_sells_today, successful_2x_sells
    
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.utcnow()
        # Calculate time until next midnight UTC
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        seconds_until_midnight = (tomorrow - now).total_seconds()
        
        # Sleep until midnight
        await asyncio.sleep(seconds_until_midnight)
        
        # Reset daily stats
        old_profit = daily_profit
        daily_profit = 0
        old_buys = total_buys_today
        total_buys_today = 0
        old_sells = successful_sells_today
        successful_sells_today = 0
        old_2x = successful_2x_sells
        successful_2x_sells = 0
        
        # Log the reset
        logging.info(f"🔄 Daily stats reset! Previous: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")
        
        # Notify in Discord
        if DISCORD_NEWS_CHANNEL_ID:
            channel = bot.get_channel(int(DISCORD_NEWS_CHANNEL_ID))
            if channel:
                await channel.send(f"🔄 Daily stats reset! Previous day: ${old_profit:.2f} profit | {old_buys} buys | {old_sells} sells | {old_2x} 2x+ sells")

@bot.event
async def on_ready():
    logging.info(f"Bot logged in as {bot.user}")
    await tree.sync()
    
    # Load trade log if it exists
    global trade_log
    try:
        if os.path.exists("trade_log.json"):
            with open("trade_log.json", "r") as f:
                trade_log = json.load(f)
            logging.info(f"✅ Loaded {len(trade_log)} entries from trade log")
    except Exception as e:
        logging.error(f"❌ Error loading trade log: {e}")

    # Start the auto-snipe task
    bot.loop.create_task(auto_snipe())
    
    # Start the daily stats reset task
    bot.loop.create_task(reset_daily_stats())
    
    # Log initial wallet balance
    log_wallet_balance()

def run_bot():
    """Main function to run the bot"""
    try:
        if not DISCORD_TOKEN:
            logging.error("❌ DISCORD_TOKEN not set in .env file")
            return
            
        if not PHANTOM_SECRET_KEY:
            logging.error("❌ PHANTOM_SECRET_KEY not set in .env file")
            return
            
        # Test wallet connection
        try:
            kp = get_phantom_keypair()
            pubkey = kp.pubkey()
            logging.info(f"✅ Wallet loaded: {pubkey}")
        except Exception as e:
            logging.error(f"❌ Wallet setup failed: {e}")
            return
            
        # Run the bot
        logging.info("🚀 Starting bot...")
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logging.error(f"❌ Bot run failed: {e}")

if __name__ == "__main__":
    run_bot()
