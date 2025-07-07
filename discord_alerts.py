import requests
import json
import logging
import io
import base64
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import time

class LiveDiscordDashboard:
    def __init__(self, webhook_url, channel_id=None):
        self.webhook_url = webhook_url
        self.channel_id = channel_id
        self.last_live_update = 0
        self.message_id = None  # Store message ID for editing
        
        logging.info("✅ Live Discord Dashboard initialized")
        
        # Send startup message
        self.send_alert(
            "🚀 Trading Bot Online",
            "Live dashboard starting... Real-time updates every 2 minutes!",
            color=0x00ff00
        )
    
    def send_live_dashboard_update(self, trader):
        """Send live dashboard update every 5 minutes - TEXT ONLY (reliable)"""
        current_time = time.time()
        
        # Update every 5 minutes (300 seconds) 
        if current_time - self.last_live_update < 300:
            return
        
        self.last_live_update = current_time
        
        try:
            # Get real-time data from trader
            current_balance = trader.wallet.get_balance()
            session_pnl = trader.brain.daily_stats.get('pnl_sol', 0)
            session_pnl_usd = session_pnl * 240
            trades = trader.brain.daily_stats.get('trades', 0)
            wins = trader.brain.daily_stats.get('wins', 0)
            win_rate = (wins / trades) * 100 if trades > 0 else 0
            best_trade = trader.brain.daily_stats.get('best_trade', 0)
            worst_trade = trader.brain.daily_stats.get('worst_trade', 0)
            
            # Get positions with SAFE price handling
            position_details = []
            total_unrealized_pnl = 0
            
            if trader.positions:
                for token, pos in trader.positions.items():
                    try:
                        # SAFE: Get price from trader or skip if not available
                        current_price = None
                        if hasattr(trader, 'get_token_price'):
                            current_price = trader.get_token_price(token)
                        elif hasattr(trader, 'price_cache') and token in trader.price_cache:
                            current_price = trader.price_cache[token]
                        
                        if current_price and pos.get('entry_price'):
                            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                            pnl_sol = pos.get('size', 0) * (current_price - pos['entry_price'])
                            total_unrealized_pnl += pnl_sol
                            
                            emoji = "🟢" if pnl_sol > 0 else "🔴" if pnl_sol < 0 else "⚪"
                            strategy = pos.get('strategy', 'UNKNOWN')
                            hold_time = (current_time - pos.get('entry_time', current_time)) / 60  # minutes
                            
                            position_details.append(
                                f"{emoji} `{token[:8]}` • {strategy}\n"
                                f"   {pnl_pct:+.1f}% • {pnl_sol:+.4f} SOL • {hold_time:.0f}m"
                            )
                        else:
                            # FALLBACK: Show position without P&L if price unavailable
                            emoji = "⚪"
                            strategy = pos.get('strategy', 'UNKNOWN')
                            hold_time = (current_time - pos.get('entry_time', current_time)) / 60
                            
                            position_details.append(
                                f"{emoji} `{token[:8]}` • {strategy}\n"
                                f"   Price unavailable • {hold_time:.0f}m"
                            )
                    except:
                        pass  # Skip problematic positions
            
            # Send comprehensive text update
            self.send_live_text_dashboard(
                current_balance, session_pnl, session_pnl_usd, trades, 
                win_rate, best_trade, worst_trade, position_details, total_unrealized_pnl
            )
            
        except Exception as e:
            logging.error(f"Live dashboard update error: {e}")
            # FALLBACK: Send basic update if everything fails
            try:
                basic_balance = trader.wallet.get_balance()
                basic_pnl = trader.brain.daily_stats.get('pnl_sol', 0)
                self.send_alert(
                    "📱 Live Update", 
                    f"💰 Balance: {basic_balance:.3f} SOL\n🔴 Session: {basic_pnl:+.4f} SOL\n⏰ {datetime.now().strftime('%H:%M:%S')}", 
                    0x0099ff
                )
            except:
                # Last resort - just send a basic message
                self.send_alert(
                    "📱 Live Update", 
                    f"Bot is running - {datetime.now().strftime('%H:%M:%S')}", 
                    0x0099ff
                )
    
    def send_live_text_dashboard(self, balance, session_pnl, session_pnl_usd, trades, win_rate, best_trade, worst_trade, positions, unrealized_pnl):
        """Send comprehensive text-based live dashboard"""
        try:
            # Main stats
            pnl_emoji = "🟢" if session_pnl > 0 else "🔴" if session_pnl < 0 else "⚪"
            unrealized_emoji = "🟢" if unrealized_pnl > 0 else "🔴" if unrealized_pnl < 0 else "⚪"
        
            # Calculate wins from win_rate
            wins = int(trades * win_rate / 100) if trades > 0 else 0
        
            # Progress calculation
            daily_target = 200  # $200 target
            progress_pct = min(100, (session_pnl_usd / daily_target) * 100) if session_pnl_usd > 0 else 0
            progress_bar = self.create_text_progress_bar(progress_pct)
        
            # Win rate color
            if win_rate > 60:
                wr_emoji = "🟢"
            elif win_rate > 40:
                wr_emoji = "🟡"
            else:
                wr_emoji = "🔴"
        
            # Main description
            description = (
                f"📊 **LIVE TRADING DASHBOARD**\n\n"
                f"💰 **Wallet Balance:** {balance:.3f} SOL\n"
                f"{pnl_emoji} **Session P&L:** {session_pnl:+.4f} SOL (${session_pnl_usd:+.2f})\n"
                f"{unrealized_emoji} **Unrealized:** {unrealized_pnl:+.4f} SOL\n\n"
                f"📈 **Trading Stats:**\n"
                f"• Trades: {trades} ({wins} wins)\n"
                f"• {wr_emoji} Win Rate: {win_rate:.1f}%\n"
                f"• Best: +{best_trade:.4f} SOL\n"
                f"• Worst: {worst_trade:.4f} SOL\n\n"
                f"🎯 **Daily Progress:**\n"
                f"{progress_bar}\n"
                f"{progress_pct:.1f}% to ${daily_target} target\n"
            )
            
            # Add positions if any
            if positions:
                description += f"\n💼 **Active Positions ({len(positions)}):**\n"
                for pos in positions[:3]:  # Show max 3 positions
                    description += f"{pos}\n"
                if len(positions) > 3:
                    description += f"... and {len(positions)-3} more\n"
            else:
                description += f"\n💼 **No Active Positions**\n"
            
            # Add timestamp
            description += f"\n⏰ Updated: {datetime.now().strftime('%H:%M:%S')}"
            
            # Create fields for additional info
            fields = []
            
            # Market status field
            if trades > 0:
                avg_trade = session_pnl / trades if trades > 0 else 0
                fields.append({
                    "name": "📊 Performance",
                    "value": f"Avg per trade: {avg_trade:+.4f} SOL\nTotal positions: {len(positions)}",
                    "inline": True
                })
            
            # Next update field
            fields.append({
                "name": "🔄 Updates",
                "value": "Every 5 minutes\nAuto-refresh enabled",
                "inline": True
            })
            
            color = 0x00ff00 if session_pnl > 0 else 0xff0000 if session_pnl < 0 else 0x0099ff
            
            self.send_alert("📱 Live Dashboard", description, color, fields)
            
        except Exception as e:
            logging.error(f"Live text dashboard error: {e}")
            
    
    def create_text_progress_bar(self, percentage):
        """Create a text-based progress bar"""
        filled = int(percentage / 10)
        empty = 10 - filled
        bar = "🟩" * filled + "⬜" * empty
        return f"`{bar}` {percentage:.1f}%"
        
    
    def create_live_dashboard_chart(self, balance, session_pnl, session_pnl_usd, trades, win_rate, positions, unrealized_pnl):
        """Create live dashboard chart with all key metrics"""
        try:
            # Create figure with dark theme
            plt.style.use('dark_background')
            fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
            fig.patch.set_facecolor('#2b2d31')
            
            # 1. Balance & P&L Overview (Top Left)
            ax1.axis('off')
            ax1.text(0.5, 0.9, '💰 WALLET & SESSION P&L', ha='center', va='top', 
                    fontsize=16, fontweight='bold', color='white', transform=ax1.transAxes)
            
            # Balance
            ax1.text(0.1, 0.7, f'Balance:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax1.transAxes)
            ax1.text(0.9, 0.7, f'{balance:.3f} SOL', ha='right', va='center', fontsize=14, 
                    color='white', fontweight='bold', transform=ax1.transAxes)
            
            # Session P&L
            pnl_color = '#00ff00' if session_pnl > 0 else '#ff0000' if session_pnl < 0 else '#ffffff'
            ax1.text(0.1, 0.5, f'Session P&L:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax1.transAxes)
            ax1.text(0.9, 0.5, f'{session_pnl:+.4f} SOL', ha='right', va='center', fontsize=14, 
                    color=pnl_color, fontweight='bold', transform=ax1.transAxes)
            ax1.text(0.9, 0.4, f'(${session_pnl_usd:+.2f})', ha='right', va='center', fontsize=12, 
                    color=pnl_color, transform=ax1.transAxes)
            
            # Unrealized P&L
            unrealized_color = '#00ff00' if unrealized_pnl > 0 else '#ff0000' if unrealized_pnl < 0 else '#ffffff'
            ax1.text(0.1, 0.25, f'Unrealized:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax1.transAxes)
            ax1.text(0.9, 0.25, f'{unrealized_pnl:+.4f} SOL', ha='right', va='center', fontsize=14, 
                    color=unrealized_color, fontweight='bold', transform=ax1.transAxes)
            
            # 2. Trading Stats (Top Right)
            ax2.axis('off')
            ax2.text(0.5, 0.9, '📊 TRADING STATS', ha='center', va='top', 
                    fontsize=16, fontweight='bold', color='white', transform=ax2.transAxes)
            
            ax2.text(0.1, 0.7, f'Trades:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax2.transAxes)
            ax2.text(0.9, 0.7, f'{trades}', ha='right', va='center', fontsize=14, 
                    color='white', fontweight='bold', transform=ax2.transAxes)
            
            ax2.text(0.1, 0.5, f'Win Rate:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax2.transAxes)
            wr_color = '#00ff00' if win_rate > 60 else '#ffff00' if win_rate > 40 else '#ff0000'
            ax2.text(0.9, 0.5, f'{win_rate:.1f}%', ha='right', va='center', fontsize=14, 
                    color=wr_color, fontweight='bold', transform=ax2.transAxes)
            
            # Progress to daily target
            daily_target_usd = 480  # $480 target
            progress = min(100, (session_pnl_usd / daily_target_usd) * 100) if session_pnl_usd > 0 else 0
            ax2.text(0.1, 0.3, f'Daily Progress:', ha='left', va='center', fontsize=14, 
                    color='#87ceeb', transform=ax2.transAxes)
            ax2.text(0.9, 0.3, f'{progress:.1f}%', ha='right', va='center', fontsize=14, 
                    color='#00ff00' if progress > 50 else '#ffff00', fontweight='bold', transform=ax2.transAxes)
            
            # 3. Active Positions (Bottom Left)
            ax3.axis('off')
            ax3.text(0.5, 0.95, '💼 ACTIVE POSITIONS', ha='center', va='top', 
                    fontsize=16, fontweight='bold', color='white', transform=ax3.transAxes)
            
            if positions:
                y_pos = 0.85
                for i, pos in enumerate(positions[:5]):  # Show top 5 positions
                    # Token and strategy
                    ax3.text(0.05, y_pos, f"{pos['emoji']} {pos['token']}", ha='left', va='center', 
                            fontsize=12, color='white', fontweight='bold', transform=ax3.transAxes)
                    ax3.text(0.05, y_pos - 0.06, f"{pos['strategy']}", ha='left', va='center', 
                            fontsize=10, color='#87ceeb', transform=ax3.transAxes)
                    
                    # P&L
                    pnl_color = '#00ff00' if pos['pnl_sol'] > 0 else '#ff0000'
                    ax3.text(0.95, y_pos, f"{pos['pnl_pct']:+.1f}%", ha='right', va='center', 
                            fontsize=12, color=pnl_color, fontweight='bold', transform=ax3.transAxes)
                    ax3.text(0.95, y_pos - 0.06, f"{pos['pnl_sol']:+.4f} SOL", ha='right', va='center', 
                            fontsize=10, color=pnl_color, transform=ax3.transAxes)
                    
                    y_pos -= 0.15
            else:
                ax3.text(0.5, 0.5, 'No active positions', ha='center', va='center', 
                        fontsize=14, color='#87ceeb', transform=ax3.transAxes)
            
            # 4. Real-time P&L Chart (Bottom Right)
            # Create mock time series data (you'd replace with real historical data)
            times = [datetime.now() - timedelta(minutes=x) for x in range(60, 0, -5)]
            mock_pnl = [session_pnl * (0.5 + 0.5 * np.sin(i/3)) for i in range(len(times))]
            mock_pnl[-1] = session_pnl  # Current P&L
            
            ax4.plot(times, mock_pnl, color='#00ff00' if session_pnl > 0 else '#ff0000', linewidth=3)
            ax4.fill_between(times, mock_pnl, alpha=0.3, color='#00ff00' if session_pnl > 0 else '#ff0000')
            ax4.set_title('📈 SESSION P&L TREND', fontsize=14, fontweight='bold', color='white', pad=20)
            ax4.set_ylabel('P&L (SOL)', fontsize=12, color='#87ceeb')
            ax4.tick_params(colors='#87ceeb')
            ax4.grid(True, alpha=0.3)
            ax4.set_facecolor('#36393f')
            
            # Format x-axis
            ax4.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax4.xaxis.set_major_locator(mdates.MinuteLocator(interval=15))
            plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45)
            
            # Add timestamp
            fig.text(0.99, 0.01, f'Updated: {datetime.now().strftime("%H:%M:%S")}', 
                    ha='right', va='bottom', fontsize=10, color='#87ceeb')
            
            plt.tight_layout()
            
            # Save chart to bytes
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', facecolor='#2b2d31', dpi=150, bbox_inches='tight')
            img_buffer.seek(0)
            plt.close()
            
            # Send to Discord with summary
            self.send_live_dashboard_with_chart(
                img_buffer, balance, session_pnl, session_pnl_usd, 
                trades, win_rate, len(positions), unrealized_pnl
            )
            
        except Exception as e:
            logging.error(f"Chart creation error: {e}")
            # Send text update if chart fails
            self.send_text_dashboard_update(balance, session_pnl, session_pnl_usd, trades, win_rate)


    def send_simple_dashboard_with_chart(self, img_buffer, balance, session_pnl, session_pnl_usd, trades, win_rate):
        """Send simple dashboard with chart image"""
        try:
            # Convert image to base64
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
            
            # Create summary description
            pnl_emoji = "🟢" if session_pnl > 0 else "🔴" if session_pnl < 0 else "⚪"
            
            description = (
                f"📱 **LIVE DASHBOARD** 📱\n\n"
                f"💰 **Balance:** {balance:.3f} SOL\n"
                f"{pnl_emoji} **Session:** {session_pnl:+.4f} SOL (${session_pnl_usd:+.2f})\n"
                f"📊 **Stats:** {trades} trades | {win_rate:.1f}% WR\n"
                f"⏰ **Auto-updates every 5 minutes**"
            )
            
            # Create embed with image
            embed = {
                "title": "📊 Live Trading Dashboard",
                "description": description,
                "color": 0x00ff00 if session_pnl > 0 else 0xff0000 if session_pnl < 0 else 0x0099ff,
                "timestamp": datetime.utcnow().isoformat(),
                "image": {"url": f"data:image/png;base64,{img_base64}"},
                "footer": {"text": "Live Dashboard • Compact Format"}
            }
            
            data = {"embeds": [embed]}
            
            response = requests.post(self.webhook_url, json=data, timeout=15)
            if response.status_code == 204:
                logging.info("✅ Live dashboard sent to Discord")
            else:
                logging.error(f"Live dashboard failed: {response.status_code}")
                
        except Exception as e:
            logging.error(f"Live dashboard error: {e}")
            
    
    def create_simple_dashboard_chart(self, balance, session_pnl, session_pnl_usd, trades, win_rate, positions, unrealized_pnl):
        """Create simple dashboard chart matching the working size"""
        try:
            # Create smaller, simpler figure like the working one
            plt.style.use('dark_background')
            fig, ax = plt.subplots(1, 1, figsize=(8, 6))  # Much smaller size
            fig.patch.set_facecolor('#2b2d31')
            
            # Create simple progress chart
            ax.axis('off')
            
            # Title
            ax.text(0.5, 0.95, '📊 Live Trading Dashboard', ha='center', va='top', 
                    fontsize=16, fontweight='bold', color='white', transform=ax.transAxes)
            
            # Balance section
            ax.text(0.1, 0.85, f'💰 Balance:', ha='left', va='center', fontsize=12, 
                    color='#87ceeb', transform=ax.transAxes)
            ax.text(0.9, 0.85, f'{balance:.3f} SOL', ha='right', va='center', fontsize=12, 
                    color='white', fontweight='bold', transform=ax.transAxes)
            
            # Session P&L
            pnl_color = '#00ff00' if session_pnl > 0 else '#ff0000' if session_pnl < 0 else '#ffffff'
            ax.text(0.1, 0.75, f'📈 Session P&L:', ha='left', va='center', fontsize=12, 
                    color='#87ceeb', transform=ax.transAxes)
            ax.text(0.9, 0.75, f'{session_pnl:+.4f} SOL', ha='right', va='center', fontsize=12, 
                    color=pnl_color, fontweight='bold', transform=ax.transAxes)
            ax.text(0.9, 0.70, f'(${session_pnl_usd:+.2f})', ha='right', va='center', fontsize=10, 
                    color=pnl_color, transform=ax.transAxes)
            
            # Trading stats
            ax.text(0.1, 0.6, f'🎯 Trades:', ha='left', va='center', fontsize=12, 
                    color='#87ceeb', transform=ax.transAxes)
            ax.text(0.9, 0.6, f'{trades}', ha='right', va='center', fontsize=12, 
                    color='white', fontweight='bold', transform=ax.transAxes)
            
            ax.text(0.1, 0.5, f'📊 Win Rate:', ha='left', va='center', fontsize=12, 
                    color='#87ceeb', transform=ax.transAxes)
            wr_color = '#00ff00' if win_rate > 60 else '#ffff00' if win_rate > 40 else '#ff0000'
            ax.text(0.9, 0.5, f'{win_rate:.1f}%', ha='right', va='center', fontsize=12, 
                    color=wr_color, fontweight='bold', transform=ax.transAxes)
            
            # Active positions
            ax.text(0.1, 0.4, f'💼 Positions:', ha='left', va='center', fontsize=12, 
                    color='#87ceeb', transform=ax.transAxes)
            ax.text(0.9, 0.4, f'{len(positions) if positions else 0}', ha='right', va='center', fontsize=12, 
                    color='white', fontweight='bold', transform=ax.transAxes)
            
            # Unrealized P&L
            if unrealized_pnl != 0:
                unrealized_color = '#00ff00' if unrealized_pnl > 0 else '#ff0000'
                ax.text(0.1, 0.3, f'📍 Unrealized:', ha='left', va='center', fontsize=12, 
                        color='#87ceeb', transform=ax.transAxes)
                ax.text(0.9, 0.3, f'{unrealized_pnl:+.4f} SOL', ha='right', va='center', fontsize=12, 
                        color=unrealized_color, fontweight='bold', transform=ax.transAxes)
            
            # Progress bar for daily target
            daily_target_usd = 200  # Updated target
            progress = min(100, (session_pnl_usd / daily_target_usd) * 100) if session_pnl_usd > 0 else 0
            
            # Simple progress bar
            bar_width = 0.8
            bar_height = 0.05
            bar_x = 0.1
            bar_y = 0.15
            
            # Background bar
            ax.add_patch(plt.Rectangle((bar_x, bar_y), bar_width, bar_height, 
                                     facecolor='#444444', transform=ax.transAxes))
            
            # Progress bar
            if progress > 0:
                progress_width = bar_width * (progress / 100)
                bar_color = '#00ff00' if progress > 50 else '#ffff00' if progress > 25 else '#ff0000'
                ax.add_patch(plt.Rectangle((bar_x, bar_y), progress_width, bar_height, 
                                         facecolor=bar_color, transform=ax.transAxes))
            
            # Progress text
            ax.text(0.5, 0.08, f'Daily Progress: {progress:.1f}% (${session_pnl_usd:+.0f}/${daily_target_usd})', 
                    ha='center', va='center', fontsize=10, color='white', transform=ax.transAxes)
            
            # Timestamp
            ax.text(0.99, 0.01, f'Updated: {datetime.now().strftime("%H:%M:%S")}', 
                    ha='right', va='bottom', fontsize=8, color='#87ceeb', transform=ax.transAxes)
            
            plt.tight_layout()
            
            # Save with smaller size and lower DPI
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format='png', facecolor='#2b2d31', dpi=80, bbox_inches='tight')
            img_buffer.seek(0)
            plt.close()
            
            return img_buffer
            
        except Exception as e:
            logging.error(f"Simple chart creation error: {e}")
            return None
    
    
    def send_text_dashboard_update(self, balance, session_pnl, session_pnl_usd, trades, win_rate):
        """Send text-only dashboard if chart fails"""
        pnl_emoji = "🟢" if session_pnl > 0 else "🔴" if session_pnl < 0 else "⚪"
        
        description = (
            f"📊 **LIVE DASHBOARD UPDATE**\n\n"
            f"💰 Balance: {balance:.3f} SOL\n"
            f"{pnl_emoji} Session P&L: {session_pnl:+.4f} SOL (${session_pnl_usd:+.2f})\n"
            f"📈 Trades: {trades} | Win Rate: {win_rate:.1f}%\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        
        self.send_alert("📱 Live Update", description, 0x0099ff)
    
    def send_alert(self, title, description, color=0x0099ff, fields=None):
        """Send regular alert via webhook"""
        try:
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.utcnow().isoformat(),
                "footer": {"text": "Solana Trading Bot"}
            }
            
            if fields:
                embed["fields"] = fields
            
            data = {"embeds": [embed]}
            
            response = requests.post(self.webhook_url, json=data, timeout=10)
            if response.status_code == 204:
                logging.info(f"✅ Discord alert sent: {title}")
                return True
            else:
                logging.error(f"Discord webhook failed: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Discord webhook error: {e}")
            return False
    
    # Keep all your existing critical alert methods
    def send_critical_alert(self, title, message, token=None):
        """Send URGENT alert for manual intervention - HIGHEST PRIORITY"""
        urgent_title = f"🚨🚨🚨 CRITICAL: {title} 🚨🚨🚨"
        urgent_desc = f"**🔥 IMMEDIATE ACTION REQUIRED 🔥**\n\n{message}"
        
        if token:
            urgent_desc += f"\n\n**Token:** `{token}`"
            urgent_desc += f"\n**⏰ CHECK WALLET NOW!**"
        
        # Send multiple alerts for critical issues
        for i in range(3):
            self.send_alert(urgent_title, urgent_desc, color=0xff00ff)
        
        logging.critical(f"🚨 CRITICAL ALERT SENT: {title}")
    
    def send_stuck_position_alert(self, token, position, attempts):
        """Alert for positions that can't be sold - CRITICAL for manual intervention"""
        try:
            current_price = get_token_price(token)
            
            if current_price and position.get('entry_price'):
                price_change = ((current_price - position['entry_price']) / position['entry_price']) * 100
                profit_sol = position.get('size', 0) * (current_price - position['entry_price'])
                profit_usd = profit_sol * 240
                
                message = (
                    f"🔴 **SELL FAILED {attempts} TIMES** 🔴\n\n"
                    f"**Current P&L: {profit_sol:+.4f} SOL (${profit_usd:+.2f})**\n"
                    f"**Price Change: {price_change:+.1f}%**\n\n"
                    f"📊 **Position Details:**\n"
                    f"• Size: {position.get('size', 'Unknown')} SOL\n"
                    f"• Entry: ${position.get('entry_price', 0):.8f}\n"
                    f"• Current: ${current_price:.8f}\n\n"
                    f"⚠️ **Bot cannot sell - MANUAL SELL RECOMMENDED**\n"
                    f"💡 **Action:** Use Jupiter or Raydium to sell manually"
                )
            else:
                message = (
                    f"🔴 **SELL FAILED {attempts} TIMES** 🔴\n\n"
                    f"Position stuck and cannot be sold automatically.\n"
                    f"Size: {position.get('size', 'Unknown')} SOL\n\n"
                    f"⚠️ **MANUAL INTERVENTION REQUIRED**"
                )
            
            self.send_critical_alert("POSITION STUCK - SELL MANUALLY", message, token)
            
        except Exception as e:
            logging.error(f"Stuck position alert error: {e}")
            self.send_critical_alert(
                "POSITION STUCK - SELL MANUALLY", 
                f"Position {token[:8]}... failed to sell {attempts} times.\nManual intervention required!",
                token
            )
    
    def close(self):
        """Cleanup"""
        logging.info("Live Discord dashboard closed")
