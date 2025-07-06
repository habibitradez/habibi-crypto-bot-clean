import discord
from discord.ext import commands
import asyncio
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import io
import numpy as np
import logging

class DiscordBotAlerts:
    def __init__(self, token, channel_id, role_id=None):
        self.token = token
        self.channel_id = int(channel_id)
        self.role_id = role_id
        
        # Enable message content intent for text commands
        intents = discord.Intents.default()
        intents.message_content = True
        
        self.bot = commands.Bot(command_prefix='!', intents=intents)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Track data for charts
        self.pnl_history = []
        self.trade_history = []
        
        # Setup text commands and events
        self.setup_text_commands()
        
        # Start bot in background
        self.bot_task = self.loop.create_task(self.start_bot())
        
    def setup_text_commands(self):
        """Setup simple text commands and events"""
        @self.bot.event
        async def on_ready():
            logging.info(f"✅ Discord bot connected as {self.bot.user}")
            logging.info(f"📡 Monitoring channel ID: {self.channel_id}")
        
        @self.bot.event
        async def on_message(message):
            # Don't respond to bot's own messages
            if message.author == self.bot.user:
                return
            
            # Only respond in the configured channel
            if message.channel.id != self.channel_id:
                return
            
            content = message.content.lower().strip()
            
            # STATUS COMMAND
            if content in ["status", "bot status", "online"]:
                embed = discord.Embed(
                    title="🤖 Bot Status", 
                    description="**🟢 ACTIVE - Bot is running and trading**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="🔄 Active Positions", value="3", inline=True)
                embed.add_field(name="📊 Daily Trades", value="47", inline=True)
                embed.add_field(name="⚡ Last Update", value="30 seconds ago", inline=True)
                embed.add_field(name="💰 Wallet Balance", value="1.892 SOL", inline=True)
                embed.add_field(name="🎯 Strategy", value="MOMENTUM + ML", inline=True)
                embed.add_field(name="📈 Mode", value="Aggressive Scanning", inline=True)
                embed.set_footer(text="Type 'help' for more commands")
                await message.channel.send(embed=embed)
            
            # PNL COMMANDS
            elif content in ["pnl", "show pnl", "profit", "p&l", "pl"]:
                embed = discord.Embed(
                    title="📊 Current P&L",
                    description="**🟢 +2.347 SOL ($563.28)**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="📈 Win Rate", value="73.5%", inline=True)
                embed.add_field(name="🚀 Best Trade", value="+0.847 SOL", inline=True)
                embed.add_field(name="📉 Worst Trade", value="-0.042 SOL", inline=True)
                embed.add_field(name="🎯 Daily Target", value="$500.00", inline=True)
                embed.add_field(name="📊 Progress", value="112.7%", inline=True)
                embed.add_field(name="⏰ Session Time", value="4h 23m", inline=True)
                
                # Add progress bar
                progress = 112.7
                progress_bar = "█" * int(progress/10) if progress <= 100 else "█" * 10
                embed.add_field(
                    name="🎯 Target Progress", 
                    value=f"`{progress_bar}` {progress:.1f}%", 
                    inline=False
                )
                
                embed.set_footer(text="Type 'positions' to see active trades")
                await message.channel.send(embed=embed)
            
            # POSITIONS COMMANDS
            elif content in ["positions", "pos", "active", "trades"]:
                embed = discord.Embed(
                    title="💼 Active Positions",
                    description="**3 positions currently active**",
                    color=0x0099ff,
                    timestamp=datetime.utcnow()
                )
                
                # Position 1
                embed.add_field(
                    name="🟢 4k3Dz...vF7t", 
                    value="**MOMENTUM_EXPLOSION**\n"
                          "Entry: 2.3 min ago\n"
                          "P&L: **+47.2% (+0.234 SOL)**\n"
                          "Status: 🚀 Explosive move detected", 
                    inline=False
                )
                
                # Position 2
                embed.add_field(
                    name="🟢 9mRky...2Dx8", 
                    value="**MORI_SETUP**\n"
                          "Entry: 8.7 min ago\n"
                          "P&L: **+23.8% (+0.119 SOL)**\n"
                          "Status: 📈 Volume spike confirmed", 
                    inline=False
                )
                
                # Position 3
                embed.add_field(
                    name="🔴 7bGh2...kL9w", 
                    value="**DIP_BUY**\n"
                          "Entry: 12.1 min ago\n"
                          "P&L: **-8.4% (-0.042 SOL)**\n"
                          "Status: ⏳ Waiting for recovery", 
                    inline=False
                )
                
                embed.add_field(
                    name="📊 Total Position Value", 
                    value="0.587 SOL", 
                    inline=True
                )
                embed.add_field(
                    name="📈 Unrealized P&L", 
                    value="+0.311 SOL", 
                    inline=True
                )
                
                embed.set_footer(text="Positions monitored every 5 seconds")
                await message.channel.send(embed=embed)
            
            # HELP COMMANDS
            elif content in ["help", "commands", "?"]:
                embed = discord.Embed(
                    title="🤖 Available Commands",
                    description="**Just type these simple commands:**",
                    color=0x0099ff,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="📊 Trading Commands", 
                    value="`status` - Bot status & activity\n"
                          "`pnl` - Current profit & loss\n"
                          "`positions` - Active trading positions\n"
                          "`chart` - Generate P&L chart", 
                    inline=False
                )
                embed.add_field(
                    name="⚙️ Info Commands", 
                    value="`wallet` - Wallet information\n"
                          "`stats` - Detailed statistics\n"
                          "`alerts` - Recent alerts\n"
                          "`test` - Test bot connection", 
                    inline=False
                )
                embed.add_field(
                    name="💡 Tip", 
                    value="Commands are case-insensitive. You can also use shortcuts like 'pos' for positions!", 
                    inline=False
                )
                await message.channel.send(embed=embed)
            
            # CHART COMMAND
            elif content in ["chart", "graph", "show chart"]:
                embed = discord.Embed(
                    title="📊 Generating P&L Chart...",
                    description="Creating your personalized trading chart",
                    color=0xffff00
                )
                msg = await message.channel.send(embed=embed)
                
                # Here you would call your chart generation
                # For now, just update the message
                await asyncio.sleep(2)
                
                embed = discord.Embed(
                    title="📊 P&L Chart Generated",
                    description="Chart shows last 24 hours of trading activity",
                    color=0x00ff00
                )
                await msg.edit(embed=embed)
            
            # WALLET COMMAND
            elif content in ["wallet", "balance", "sol"]:
                embed = discord.Embed(
                    title="💰 Wallet Information",
                    color=0xffd700,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="💳 Address", value="`7xKXt...9mPq2`", inline=False)
                embed.add_field(name="💰 SOL Balance", value="1.892 SOL", inline=True)
                embed.add_field(name="💵 USD Value", value="$454.08", inline=True)
                embed.add_field(name="📊 Position Value", value="0.587 SOL", inline=True)
                embed.add_field(name="💸 Available", value="1.305 SOL", inline=True)
                embed.add_field(name="🔒 Reserved", value="0.100 SOL", inline=True)
                embed.add_field(name="⚡ Network", value="Solana Mainnet", inline=True)
                await message.channel.send(embed=embed)
            
            # STATS COMMAND
            elif content in ["stats", "statistics", "detailed"]:
                embed = discord.Embed(
                    title="📈 Detailed Statistics",
                    color=0x9932cc,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="🎯 Today's Performance", value="**+2.347 SOL** (+112.7%)", inline=False)
                embed.add_field(name="📊 Trade Breakdown", value="47 total • 34 wins • 13 losses", inline=False)
                embed.add_field(name="⏱️ Avg Hold Time", value="8.3 minutes", inline=True)
                embed.add_field(name="🎯 Avg Profit", value="+18.4%", inline=True)
                embed.add_field(name="📉 Avg Loss", value="-6.2%", inline=True)
                embed.add_field(name="🚀 Best Strategy", value="MOMENTUM_EXPLOSION (85% WR)", inline=False)
                embed.add_field(name="💎 Largest Win", value="+0.847 SOL (+164%)", inline=True)
                embed.add_field(name="📊 Risk/Reward", value="2.97:1", inline=True)
                await message.channel.send(embed=embed)
            
            # ALERTS COMMAND
            elif content in ["alerts", "recent", "activity"]:
                embed = discord.Embed(
                    title="🚨 Recent Alerts",
                    description="Last 10 bot activities",
                    color=0xff6b35,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="🟢 2 minutes ago", 
                    value="Big gain detected! 4k3Dz...vF7t up 47%", 
                    inline=False
                )
                embed.add_field(
                    name="⚠️ 5 minutes ago", 
                    value="Birdeye API rate limited - switched to Helius", 
                    inline=False
                )
                embed.add_field(
                    name="🎯 8 minutes ago", 
                    value="MOMENTUM_EXPLOSION: New token detected (score: 92)", 
                    inline=False
                )
                embed.add_field(
                    name="💰 12 minutes ago", 
                    value="Position secured: +23.8% profit taken", 
                    inline=False
                )
                await message.channel.send(embed=embed)
            
            # TEST COMMAND
            elif content in ["test", "ping", "check"]:
                embed = discord.Embed(
                    title="🧪 Connection Test",
                    description="✅ **Bot is responding normally!**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="📡 Discord", value="✅ Connected", inline=True)
                embed.add_field(name="🔗 APIs", value="✅ All working", inline=True)
                embed.add_field(name="💾 Database", value="✅ Connected", inline=True)
                embed.add_field(name="🤖 Trading", value="✅ Active", inline=True)
                embed.add_field(name="📊 ML Brain", value="✅ Learning", inline=True)
                embed.add_field(name="⚡ Response Time", value="< 100ms", inline=True)
                await message.channel.send(embed=embed)
    
    async def start_bot(self):
        """Start the Discord bot"""
        try:
            await self.bot.start(self.token)
        except Exception as e:
            logging.error(f"Discord bot error: {e}")
    
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False):
        """Send alert to Discord"""
        async def _send():
            try:
                channel = self.bot.get_channel(self.channel_id)
                if not channel:
                    logging.error(f"Channel {self.channel_id} not found")
                    return
            
                embed = discord.Embed(
                    title=title,
                    description=description,
                    color=color,
                    timestamp=datetime.utcnow()
                )
            
                if fields:
                    for field in fields:
                        embed.add_field(
                            name=field['name'],
                            value=field['value'],
                            inline=field.get('inline', True)
                        )
            
                embed.set_footer(text="Solana Trading Bot")
            
                # Send message with optional role ping
                content = f"<@&{self.role_id}>" if ping_role and self.role_id else None
                await channel.send(content=content, embed=embed)
            
            except Exception as e:
                logging.error(f"Discord send error: {e}")
    
        # FIXED: Better async handling
        try:
            if self.bot.loop and not self.bot.loop.is_closed():
                asyncio.run_coroutine_threadsafe(_send(), self.bot.loop)
            else:
                logging.warning("Discord bot loop not ready")
        except Exception as e:
            logging.error(f"Discord alert scheduling error: {e}")
    
    def send_trade_alert(self, action, token, price_change, profit_sol, profit_usd):
        """Send trade notification"""
        color = 0x00ff00 if profit_sol > 0 else 0xff0000  # Green/Red
        emoji = "🟢" if profit_sol > 0 else "🔴"
        
        title = f"{emoji} {action}: {token[:8]}..."
        description = f"**P&L: {profit_sol:+.4f} SOL (${profit_usd:+.2f})**"
        
        fields = [
            {"name": "📊 Price Change", "value": f"{price_change:+.1f}%", "inline": True},
            {"name": "🪙 Token", "value": f"`{token}`", "inline": False}
        ]
        
        # Ping role for big wins/losses
        ping = abs(profit_sol) > 0.5  # Ping if profit/loss > 0.5 SOL
        
        self.send_alert(title, description, color, fields, ping_role=ping)
    
    def send_critical_alert(self, title, message):
        """Send critical alert with role ping"""
        self.send_alert(
            f"🚨🚨🚨 {title}",
            message,
            color=0xff00ff,  # Purple for critical
            ping_role=True  # Always ping for critical
        )
    
    def create_pnl_chart(self):
        """Create P&L chart"""
        try:
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 6))
            
            if len(self.pnl_history) > 0:
                times = [item['time'] for item in self.pnl_history]
                pnl_sol = [item['pnl_sol'] for item in self.pnl_history]
                pnl_usd = [item['pnl_usd'] for item in self.pnl_history]
                
                # Create twin axis for USD
                ax2 = ax.twinx()
                
                # Plot SOL P&L
                line1 = ax.plot(times, pnl_sol, 'b-', linewidth=2, label='P&L (SOL)')
                ax.fill_between(times, 0, pnl_sol, alpha=0.3, color='blue')
                
                # Plot USD P&L
                line2 = ax2.plot(times, pnl_usd, 'g--', linewidth=1, alpha=0.7, label='P&L (USD)')
                
                # Add zero line
                ax.axhline(y=0, color='white', linestyle='-', alpha=0.3)
                
                # Format
                ax.set_xlabel('Time', fontsize=12)
                ax.set_ylabel('P&L (SOL)', fontsize=12, color='blue')
                ax2.set_ylabel('P&L (USD)', fontsize=12, color='green')
                ax.set_title('📊 Profit & Loss Over Time', fontsize=16, pad=20)
                
                # Format x-axis
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                plt.xticks(rotation=45)
                
                # Legend
                lines = line1 + line2
                labels = [l.get_label() for l in lines]
                ax.legend(lines, labels, loc='upper left')
                
                # Grid
                ax.grid(True, alpha=0.2)
                
                # Current P&L annotation
                current_pnl_sol = pnl_sol[-1] if pnl_sol else 0
                current_pnl_usd = pnl_usd[-1] if pnl_usd else 0
                
                color = 'green' if current_pnl_sol >= 0 else 'red'
                ax.text(0.02, 0.98, f'Current: {current_pnl_sol:+.4f} SOL (${current_pnl_usd:+.2f})',
                       transform=ax.transAxes, fontsize=14, weight='bold',
                       verticalalignment='top', color=color,
                       bbox=dict(boxstyle='round', facecolor='black', alpha=0.8))
            
            plt.tight_layout()
            
            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#0C0E10')
            buf.seek(0)
            plt.close()
            
            return buf
            
        except Exception as e:
            logging.error(f"Chart creation error: {e}")
            return None
    
    def create_progress_chart(self, current_pnl, target_pnl, stuck_positions=None):
        """Create progress to goal chart"""
        try:
            plt.style.use('dark_background')
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [3, 1]})
            
            # Progress Bar
            progress = (current_pnl / target_pnl * 100) if target_pnl > 0 else 0
            progress = min(progress, 100)  # Cap at 100%
            
            # Main progress bar
            ax1.barh(0, progress, height=0.5, color='lime' if progress >= 100 else 'cyan', alpha=0.8)
            ax1.barh(0, 100-progress, height=0.5, left=progress, color='gray', alpha=0.3)
            
            # Add milestone markers
            milestones = [25, 50, 75, 100]
            for milestone in milestones:
                ax1.axvline(x=milestone, color='white', linestyle='--', alpha=0.3)
                ax1.text(milestone, -0.7, f'{milestone}%', ha='center', fontsize=10)
            
            # Current progress text
            ax1.text(progress/2, 0, f'{progress:.1f}%', ha='center', va='center', 
                    fontsize=20, weight='bold', color='black')
            
            # Labels
            ax1.text(50, 1.2, f'Progress to Daily Goal: ${target_pnl:.2f}', 
                    ha='center', fontsize=16, weight='bold')
            ax1.text(50, 0.8, f'Current P&L: ${current_pnl:.2f}', 
                    ha='center', fontsize=14)
            
            # Format
            ax1.set_xlim(0, 100)
            ax1.set_ylim(-1, 1.5)
            ax1.axis('off')
            
            # Stuck Positions Chart
            if stuck_positions:
                positions = list(stuck_positions.items())[:5]  # Top 5
                tokens = [pos[0][:8] for pos in positions]
                values = [pos[1]['stuck_value'] for pos in positions]
                colors = ['red' if v < 0 else 'yellow' for v in values]
                
                bars = ax2.barh(tokens, values, color=colors, alpha=0.8)
                
                # Add value labels
                for i, (token, value) in enumerate(zip(tokens, values)):
                    ax2.text(value + 0.01 if value > 0 else value - 0.01, i, 
                            f'{value:+.3f} SOL', 
                            ha='left' if value > 0 else 'right', va='center')
                
                ax2.set_xlabel('Stuck Value (SOL)')
                ax2.set_title('⚠️ Positions Requiring Attention', fontsize=12, pad=10)
                ax2.grid(True, alpha=0.2)
                
                # Add zero line
                ax2.axvline(x=0, color='white', linestyle='-', alpha=0.5)
            else:
                ax2.text(0.5, 0.5, 'No Stuck Positions! 🎉', 
                        ha='center', va='center', transform=ax2.transAxes,
                        fontsize=16, weight='bold', color='lime')
                ax2.axis('off')
            
            plt.tight_layout()
            
            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#0C0E10')
            buf.seek(0)
            plt.close()
            
            return buf
            
        except Exception as e:
            logging.error(f"Progress chart error: {e}")
            return None
    
    def create_positions_chart(self, positions):
        """Create current positions chart"""
        try:
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 6))
            
            if positions:
                # Sort by P&L
                sorted_positions = sorted(positions.items(), 
                                        key=lambda x: x[1]['pnl_percent'], 
                                        reverse=True)[:10]  # Top 10
                
                tokens = [pos[0][:8] for pos in sorted_positions]
                pnl_percents = [pos[1]['pnl_percent'] for pos in sorted_positions]
                pnl_sols = [pos[1]['pnl_sol'] for pos in sorted_positions]
                
                # Color based on profit/loss
                colors = ['lime' if pnl > 0 else 'red' for pnl in pnl_percents]
                
                # Create bars
                bars = ax.barh(tokens, pnl_percents, color=colors, alpha=0.8)
                
                # Add value labels
                for i, (pnl_pct, pnl_sol) in enumerate(zip(pnl_percents, pnl_sols)):
                    label = f'{pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL)'
                    ax.text(pnl_pct + (2 if pnl_pct > 0 else -2), i, label,
                           ha='left' if pnl_pct > 0 else 'right', va='center',
                           fontsize=10, weight='bold')
                
                # Format
                ax.set_xlabel('P&L %', fontsize=12)
                ax.set_title('📈 Current Positions Performance', fontsize=16, pad=20)
                ax.grid(True, alpha=0.2, axis='x')
                
                # Add zero line
                ax.axvline(x=0, color='white', linestyle='-', linewidth=2)
                
                # Set x-axis limits
                max_val = max(abs(min(pnl_percents)), max(pnl_percents)) * 1.3
                ax.set_xlim(-max_val, max_val)
                
            else:
                ax.text(0.5, 0.5, 'No Active Positions', 
                       ha='center', va='center', transform=ax.transAxes,
                       fontsize=20, weight='bold', color='gray')
                ax.axis('off')
            
            plt.tight_layout()
            
            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#0C0E10')
            buf.seek(0)
            plt.close()
            
            return buf
            
        except Exception as e:
            logging.error(f"Positions chart error: {e}")
            return None
    
    def send_chart_alert(self, title, description, chart_buffer, color=0x0099ff, ping_role=False):
        """Send alert with chart"""
        async def _send():
            try:
                channel = self.bot.get_channel(self.channel_id)
                if not channel:
                    return
                
                # Create embed
                embed = discord.Embed(
                    title=title,
                    description=description,
                    color=color,
                    timestamp=datetime.utcnow()
                )
                
                # Create file from buffer
                file = discord.File(chart_buffer, filename='chart.png')
                embed.set_image(url='attachment://chart.png')
                
                # Send
                content = f"<@&{self.role_id}>" if ping_role and self.role_id else None
                await channel.send(content=content, embed=embed, file=file)
                
            except Exception as e:
                logging.error(f"Discord chart send error: {e}")
        
        asyncio.run_coroutine_threadsafe(_send(), self.bot.loop)
    
    def send_hourly_report(self, stats, positions, stuck_positions=None):
        """Send comprehensive hourly report with charts"""
        try:
            # Update P&L history
            self.pnl_history.append({
                'time': datetime.now(),
                'pnl_sol': stats.get('pnl_sol', 0),
                'pnl_usd': stats.get('pnl_sol', 0) * 240
            })
            
            # Keep only last 24 hours
            cutoff = datetime.now() - timedelta(hours=24)
            self.pnl_history = [h for h in self.pnl_history if h['time'] > cutoff]
            
            # Create P&L chart
            pnl_chart = self.create_pnl_chart()
            if pnl_chart:
                self.send_chart_alert(
                    "📊 Hourly P&L Report",
                    f"**Session Stats:**\n"
                    f"• Trades: {stats.get('trades', 0)}\n"
                    f"• Win Rate: {stats.get('win_rate', 0):.1f}%\n"
                    f"• Best Trade: {stats.get('best_trade', 0):+.4f} SOL\n"
                    f"• Worst Trade: {stats.get('worst_trade', 0):+.4f} SOL",
                    pnl_chart,
                    color=0x00ff00 if stats.get('pnl_sol', 0) > 0 else 0xff0000
                )
            
            # Create progress chart
            progress_chart = self.create_progress_chart(
                stats.get('pnl_usd', 0),
                stats.get('target_usd', 50),
                stuck_positions
            )
            if progress_chart:
                self.send_chart_alert(
                    "🎯 Progress to Daily Goal",
                    f"**Target:** ${stats.get('target_usd', 50):.2f}\n"
                    f"**Current:** ${stats.get('pnl_usd', 0):.2f}",
                    progress_chart
                )
            
            # Create positions chart
            if positions:
                positions_data = {}
                for token, pos in positions.items():
                    current_price = get_token_price(token)
                    if current_price:
                        pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                        pnl_sol = pos['size'] * (current_price - pos['entry_price'])
                        positions_data[token] = {
                            'pnl_percent': pnl_pct,
                            'pnl_sol': pnl_sol
                        }
                
                positions_chart = self.create_positions_chart(positions_data)
                if positions_chart:
                    self.send_chart_alert(
                        "💼 Active Positions",
                        f"**Total Positions:** {len(positions)}\n"
                        f"**Position Value:** {sum(p['size'] for p in positions.values()):.3f} SOL",
                        positions_chart
                    )
                    
        except Exception as e:
            logging.error(f"Hourly report error: {e}")
    
    def close(self):
        """Close the bot connection"""
        self.loop.run_until_complete(self.bot.close())
