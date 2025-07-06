import discord
import asyncio
import logging
from datetime import datetime

class DiscordBotAlerts:
    def __init__(self, token, channel_id, role_id=None):
        self.token = token
        self.channel_id = int(channel_id)
        self.role_id = role_id
        
        logging.info(f"🔧 Discord: Starting bot...")
        logging.info(f"🔧 Discord: Token exists: {bool(token and len(token) > 10)}")
        logging.info(f"🔧 Discord: Channel ID: {channel_id}")
        
        # Create bot
        intents = discord.Intents.default()
        intents.message_content = True
        
        self.bot = discord.Client(intents=intents)
        
        @self.bot.event
        async def on_ready():
            logging.info(f"✅ DISCORD BOT ONLINE: {self.bot.user}")
            logging.info(f"📡 Connected to {len(self.bot.guilds)} servers")
            
            # Find the channel
            channel = self.bot.get_channel(self.channel_id)
            if channel:
                logging.info(f"✅ Found channel: {channel.name}")
                try:
                    await channel.send("🤖 **Bot is now ONLINE!** Type `test` to verify.")
                    logging.info("✅ Startup message sent!")
                except Exception as e:
                    logging.error(f"❌ Could not send message: {e}")
            else:
                logging.error(f"❌ Channel {self.channel_id} not found")
                # List all available channels
                for guild in self.bot.guilds:
                    logging.info(f"Server: {guild.name}")
                    for ch in guild.text_channels:
                        logging.info(f"  Channel: {ch.name} (ID: {ch.id})")
        
        @self.bot.event
        async def on_message(message):
            if message.author == self.bot.user:
                return
                
            if message.channel.id != self.channel_id:
                return
            
            content = message.content.lower().strip()
            logging.info(f"📨 Received command: '{content}'")
            
            if content == "test":
                await message.channel.send("✅ **Discord bot is working perfectly!**")
            elif content == "status":
                embed = discord.Embed(
                    title="🤖 Bot Status",
                    description="✅ **ONLINE and ready for trading alerts!**",
                    color=0x00ff00
                )
                await message.channel.send(embed=embed)
        
        # Start bot
        self._start_bot()
    
    def _start_bot(self):
        """Start bot in background"""
        try:
            # Run in a separate thread
            import threading
            def run_bot():
                try:
                    asyncio.run(self.bot.start(self.token))
                except Exception as e:
                    logging.error(f"❌ Discord bot failed: {e}")
            
            bot_thread = threading.Thread(target=run_bot, daemon=True)
            bot_thread.start()
            logging.info("🚀 Discord bot thread started")
            
        except Exception as e:
            logging.error(f"❌ Discord startup error: {e}")
    
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False):
        logging.info(f"📱 Discord alert: {title}")
    
    def send_trade_alert(self, action, token, price_change, profit_sol, profit_usd):
        logging.info(f"📱 Trade alert: {action} {token}")
    
    def send_critical_alert(self, title, message):
        logging.info(f"📱 Critical alert: {title}")
    
    def close(self):
        pass
