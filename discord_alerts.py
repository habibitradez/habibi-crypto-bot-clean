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
        """Send live dashboard update every 2 minutes with current data"""
        current_time = time.time()
        
        # Update every 2 minutes (120 seconds) instead of hourly
        if current_time - self.last_live_update < 120:
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
            
            # Get active positions with real-time P&L
            active_positions = []
            total_unrealized_pnl = 0
            
            if trader.positions:
                for token, pos in trader.positions.items():
                    try:
                        current_price = get_token_price(token)
                        if current_price and pos.get('entry_price'):
                            pnl_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100
                            pnl_sol = pos.get('size', 0) * (current_price - pos['entry_price'])
                            total_unrealized_pnl += pnl_sol
                            
                            emoji = "🟢" if pnl_sol > 0 else "🔴" if pnl_sol < 0 else "⚪"
                            active_positions.append({
                                'token': token[:8],
                                'strategy': pos.get('strategy', 'UNKNOWN'),
                                'pnl_pct': pnl_pct,
                                'pnl_sol': pnl_sol,
                                'emoji': emoji
                            })
                    except:
                        pass
            
            # Create live dashboard
            self.create_live_dashboard_chart(
                current_balance,
                session_pnl,
                session_pnl_usd,
                trades,
                win_rate,
                active_positions,
                total_unrealized_pnl
            )
            
        except Exception as e:
            logging.error(f"Live dashboard update error: {e}")
    
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
    
    def send_live_dashboard_update(self, trader):
        """Send live dashboard update every 5 minutes with simple chart"""
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
        
            # Get positions
            total_unrealized_pnl = 0
            if trader.positions:
                for token, pos in trader.positions.items():
                    try:
                        current_price = get_token_price(token)
                        if current_price and pos.get('entry_price'):
                            pnl_sol = pos.get('size', 0) * (current_price - pos['entry_price'])
                            total_unrealized_pnl += pnl_sol
                    except:
                        pass
        
            # Create simple chart
            img_buffer = self.create_simple_dashboard_chart(
                current_balance, session_pnl, session_pnl_usd, 
                trades, win_rate, trader.positions, total_unrealized_pnl
            )
        
            if img_buffer:
                self.send_simple_dashboard_with_chart(img_buffer, current_balance, session_pnl, session_pnl_usd, trades, win_rate)
            else:
                # Fallback to text
                self.send_text_dashboard_update(current_balance, session_pnl, session_pnl_usd, trades, win_rate)
            
        except Exception as e:
            logging.error(f"Live dashboard update error: {e}")
    
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
