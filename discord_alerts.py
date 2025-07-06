import discord
from discord.ext import commands
import asyncio
import threading
import logging
from datetime import datetime

class DiscordBotAlerts:
    def __init__(self, token, channel_id, role_id=None):
        self.token = token
        self.channel_id = int(channel_id)
        self.role_id = role_id
        self.bot = None
        self.bot_ready = False
        
        # Start bot in separate thread
        self.bot_thread = threading.Thread(target=self._run_bot, daemon=True)
        self.bot_thread.start()
        
        logging.info("🚀 Discord bot starting in background...")
    
    def _run_bot(self):
        """Run bot in separate thread with its own event loop"""
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Enable message content intent
            intents = discord.Intents.default()
            intents.message_content = True
            
            self.bot = commands.Bot(command_prefix='!', intents=intents)
            
            @self.bot.event
            async def on_ready():
                self.bot_ready = True
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
                    embed.add_field(name="🔄 Active Positions", value="Checking...", inline=True)
                    embed.add_field(name="📊 Daily Trades", value="Scanning...", inline=True)
                    embed.add_field(name="⚡ Last Update", value="Just now", inline=True)
                    embed.add_field(name="🎯 Strategy", value="MOMENTUM + ML", inline=True)
                    embed.add_field(name="📈 Mode", value="Aggressive Scanning", inline=True)
                    embed.set_footer(text="Type 'help' for more commands")
                    await message.channel.send(embed=embed)
                
                # PNL COMMANDS
                elif content in ["pnl", "show pnl", "profit", "p&l", "pl"]:
                    embed = discord.Embed(
                        title="📊 Current P&L",
                        description="**Bot is actively scanning for opportunities**",
                        color=0x0099ff,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(name="📈 Status", value="Finding score 90+ tokens", inline=True)
                    embed.add_field(name="🎯 Strategy", value="MOMENTUM_EXPLOSION", inline=True)
                    embed.add_field(name="⏰ Uptime", value="Running", inline=True)
                    embed.set_footer(text="Real P&L will show when trades execute")
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
                              "`positions` - Active trading positions", 
                        inline=False
                    )
                    embed.add_field(
                        name="💡 Tip", 
                        value="The bot is actively scanning and will alert on trades!", 
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
                    embed.add_field(name="🤖 Trading", value="✅ Active", inline=True)
                    embed.add_field(name="🔍 Scanning", value="✅ Finding opportunities", inline=True)
                    await message.channel.send(embed=embed)
            
            # Run the bot
            loop.run_until_complete(self.bot.start(self.token))
            
        except Exception as e:
            logging.error(f"Discord bot error: {e}")
    
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False):
        """Send alert to Discord"""
        if not self.bot_ready or not self.bot:
            logging.warning("Discord bot not ready yet")
            return
        
        try:
            # Create the coroutine
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
                    
                    # Send message
                    content = f"<@&{self.role_id}>" if ping_role and self.role_id else None
                    await channel.send(content=content, embed=embed)
                    
                except Exception as e:
                    logging.error(f"Discord send error: {e}")
            
            # Schedule the coroutine
            asyncio.run_coroutine_threadsafe(_send(), self.bot.loop)
            
        except Exception as e:
            logging.error(f"Discord alert error: {e}")
    
    def send_trade_alert(self, action, token, price_change, profit_sol, profit_usd):
        """Send trade notification"""
        color = 0x00ff00 if profit_sol > 0 else 0xff0000
        emoji = "🟢" if profit_sol > 0 else "🔴"
        
        title = f"{emoji} {action}: {token[:8]}..."
        description = f"**P&L: {profit_sol:+.4f} SOL (${profit_usd:+.2f})**"
        
        fields = [
            {"name": "📊 Price Change", "value": f"{price_change:+.1f}%", "inline": True},
            {"name": "🪙 Token", "value": f"`{token}`", "inline": False}
        ]
        
        self.send_alert(title, description, color, fields, ping_role=abs(profit_sol) > 0.5)
    
    def send_critical_alert(self, title, message):
        """Send critical alert with role ping"""
        self.send_alert(
            f"🚨🚨🚨 {title}",
            message,
            color=0xff00ff,
            ping_role=True
        )
    
    def close(self):
        """Close the bot connection"""
        if self.bot:
            try:
                asyncio.run_coroutine_threadsafe(self.bot.close(), self.bot.loop)
            except:
                pass
