import requests
import json
import logging
import io
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

class DiscordWebhookAlerts:
    def __init__(self, webhook_url, channel_id=None):
        self.webhook_url = webhook_url
        self.channel_id = channel_id
        self.pnl_history = []
        self.trade_history = []
        
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False, image_data=None):
        """Send alert via webhook with optional image"""
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
            files = {}
            
            # Add image if provided
            if image_data:
                files = {"file": ("chart.png", image_data, "image/png")}
                embed["image"] = {"url": "attachment://chart.png"}
            
            if files:
                # Send with file attachment
                response = requests.post(self.webhook_url, data={"payload_json": json.dumps(data)}, files=files, timeout=10)
            else:
                # Send without file
                response = requests.post(self.webhook_url, json=data, timeout=5)
            
            if response.status_code == 204:
                logging.info(f"✅ Discord alert sent: {title}")
                return True
            else:
                logging.error(f"Discord webhook failed: {response.status_code}")
                return False
                
        except Exception as e:
            logging.error(f"Discord webhook error: {e}")
            return False
    
    def create_pnl_chart(self, current_pnl=0, target_pnl=50):
        """Create P&L progress chart"""
        try:
            plt.style.use('dark_background')
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), gridspec_kw={'height_ratios': [2, 1]})
            
            # Progress Bar
            progress = (current_pnl / target_pnl * 100) if target_pnl > 0 else 0
            progress = min(progress, 100)
            
            # Main progress bar
            ax1.barh(0, progress, height=0.6, color='lime' if progress >= 100 else 'cyan', alpha=0.8)
            ax1.barh(0, 100-progress, height=0.6, left=progress, color='gray', alpha=0.3)
            
            # Add milestone markers
            milestones = [25, 50, 75, 100]
            for milestone in milestones:
                ax1.axvline(x=milestone, color='white', linestyle='--', alpha=0.4)
                ax1.text(milestone, -0.8, f'{milestone}%', ha='center', fontsize=10, color='white')
            
            # Progress text
            ax1.text(progress/2 if progress > 10 else 10, 0, f'{progress:.1f}%', 
                    ha='center', va='center', fontsize=18, weight='bold', 
                    color='black' if progress > 10 else 'white')
            
            # Labels
            ax1.text(50, 1.2, f'Daily Target: ${target_pnl:.0f}', 
                    ha='center', fontsize=14, weight='bold', color='white')
            ax1.text(50, 0.8, f'Current P&L: ${current_pnl:.2f}', 
                    ha='center', fontsize=12, color='white')
            
            ax1.set_xlim(0, 100)
            ax1.set_ylim(-1, 1.5)
            ax1.axis('off')
            
            # P&L History Chart
            if len(self.pnl_history) > 1:
                times = [item['time'] for item in self.pnl_history[-20:]]  # Last 20 points
                pnl_values = [item['pnl_usd'] for item in self.pnl_history[-20:]]
                
                ax2.plot(times, pnl_values, 'cyan', linewidth=2, marker='o', markersize=4)
                ax2.fill_between(times, 0, pnl_values, alpha=0.3, color='cyan')
                ax2.axhline(y=0, color='white', linestyle='-', alpha=0.3)
                
                ax2.set_ylabel('P&L ($)', fontsize=10, color='white')
                ax2.tick_params(colors='white')
                ax2.grid(True, alpha=0.2)
                
                # Format x-axis
                if len(times) > 1:
                    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
                    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
            else:
                ax2.text(0.5, 0.5, 'Building P&L history...', 
                        ha='center', va='center', transform=ax2.transAxes,
                        fontsize=12, color='white')
                ax2.axis('off')
            
            plt.tight_layout()
            
            # Save to bytes
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', 
                       facecolor='#2C2F33', edgecolor='none')
            buf.seek(0)
            plt.close()
            
            return buf.getvalue()
            
        except Exception as e:
            logging.error(f"Chart creation error: {e}")
            return None
    
    def create_positions_chart(self, positions_data):
        """Create current positions performance chart"""
        try:
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(12, 6))
            
            if positions_data:
                # Sort by P&L
                sorted_positions = sorted(positions_data.items(), 
                                        key=lambda x: x[1]['pnl_percent'], 
                                        reverse=True)[:8]  # Top 8
                
                tokens = [pos[0][:8] + '...' for pos in sorted_positions]
                pnl_percents = [pos[1]['pnl_percent'] for pos in sorted_positions]
                pnl_sols = [pos[1]['pnl_sol'] for pos in sorted_positions]
                
                # Color based on profit/loss
                colors = ['#00FF88' if pnl > 0 else '#FF4757' for pnl in pnl_percents]
                
                # Create bars
                bars = ax.barh(tokens, pnl_percents, color=colors, alpha=0.8, height=0.6)
                
                # Add value labels
                for i, (pnl_pct, pnl_sol) in enumerate(zip(pnl_percents, pnl_sols)):
                    label = f'{pnl_pct:+.1f}% ({pnl_sol:+.3f} SOL)'
                    x_pos = pnl_pct + (5 if pnl_pct > 0 else -5)
                    ax.text(x_pos, i, label, ha='left' if pnl_pct > 0 else 'right', 
                           va='center', fontsize=10, weight='bold', color='white')
                
                # Format
                ax.set_xlabel('P&L %', fontsize=12, color='white')
                ax.set_title('📈 Active Positions Performance', fontsize=16, pad=20, color='white')
                ax.grid(True, alpha=0.2, axis='x')
                ax.tick_params(colors='white')
                
                # Add zero line
                ax.axvline(x=0, color='white', linestyle='-', linewidth=2, alpha=0.7)
                
                # Set limits
                if pnl_percents:
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
            plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', 
                       facecolor='#2C2F33', edgecolor='none')
            buf.seek(0)
            plt.close()
            
            return buf.getvalue()
            
        except Exception as e:
            logging.error(f"Positions chart error: {e}")
            return None
    
    def send_trade_alert(self, action, token, price_change, profit_sol, profit_usd):
        """Enhanced trade notification with rich formatting"""
        color = 0x00FF88 if profit_sol > 0 else 0xFF4757
        emoji = "🟢" if profit_sol > 0 else "🔴"
        
        # Determine urgency
        if abs(price_change) > 50:
            urgency = "🚀 MASSIVE MOVE"
        elif abs(price_change) > 25:
            urgency = "🔥 BIG MOVE"
        elif abs(price_change) > 10:
            urgency = "📈 GOOD MOVE"
        else:
            urgency = "📊 SMALL MOVE"
        
        title = f"{emoji} {action}: {token[:8]}..."
        description = f"**{urgency}**\n**P&L: {profit_sol:+.4f} SOL (${profit_usd:+.2f})**"
        
        fields = [
            {"name": "📊 Price Change", "value": f"**{price_change:+.1f}%**", "inline": True},
            {"name": "💰 Profit (SOL)", "value": f"**{profit_sol:+.4f}**", "inline": True},
            {"name": "💵 Profit (USD)", "value": f"**${profit_usd:+.2f}**", "inline": True},
            {"name": "🪙 Token Address", "value": f"`{token}`", "inline": False}
        ]
        
        # Add timestamp
        fields.append({"name": "⏰ Time", "value": datetime.now().strftime("%H:%M:%S"), "inline": True})
        
        self.send_alert(title, description, color, fields)
    
    def send_hourly_report(self, stats, positions, stuck_positions=None):
        """FIXED: Send comprehensive hourly report - handles your current format"""
        try:
            # Update P&L history
            current_pnl_usd = stats.get('pnl_usd', 0)
            self.pnl_history.append({
                'time': datetime.now(),
                'pnl_usd': current_pnl_usd,
                'pnl_sol': stats.get('pnl_sol', 0)
            })
            
            # Keep only last 24 hours
            cutoff = datetime.now() - timedelta(hours=24)
            self.pnl_history = [h for h in self.pnl_history if h['time'] > cutoff]
            
            # Convert positions to chart format
            positions_data = {}
            if positions:
                # Handle both old format (position objects) and new format (positions_data)
                for token, pos in positions.items():
                    try:
                        # Check if it's already in the right format
                        if isinstance(pos, dict) and 'pnl_percent' in pos:
                            # Already in chart format
                            positions_data[token] = pos
                        else:
                            # Convert from position object format
                            # Import get_token_price at the top of your main file
                            # For now, we'll skip real-time price and just send basic info
                            entry_price = pos.get('entry_price', 0)
                            size = pos.get('size', 0)
                            # Use a basic estimate - you can enhance this
                            positions_data[token] = {
                                'pnl_percent': 0,  # Will be updated when price data available
                                'pnl_sol': 0,
                                'size': size,
                                'entry_price': entry_price
                            }
                    except Exception as e:
                        logging.debug(f"Position conversion error for {token}: {e}")
                        continue
            
            # Create progress chart
            target_usd = stats.get('target_usd', 50)
            progress_chart = self.create_pnl_chart(current_pnl_usd, target_usd)
            
            # Send progress report
            if progress_chart:
                win_rate = stats.get('win_rate', 0)
                trades = stats.get('trades', 0)
                
                title = "📊 Hourly Trading Report"
                description = f"**Session Performance Update**"
                
                fields = [
                    {"name": "💰 Current P&L", "value": f"**${current_pnl_usd:.2f}**", "inline": True},
                    {"name": "🎯 Daily Target", "value": f"${target_usd:.0f}", "inline": True},
                    {"name": "📈 Progress", "value": f"**{(current_pnl_usd/target_usd*100):.1f}%**", "inline": True},
                    {"name": "📊 Total Trades", "value": f"**{trades}**", "inline": True},
                    {"name": "🏆 Win Rate", "value": f"**{win_rate:.1f}%**", "inline": True},
                    {"name": "🚀 Best Trade", "value": f"**{stats.get('best_trade', 0):+.3f} SOL**", "inline": True}
                ]
                
                self.send_alert(title, description, 0x00AAFF, fields, image_data=progress_chart)
            
            # Send positions chart if we have active positions
            if positions_data:
                positions_chart = self.create_positions_chart(positions_data)
                if positions_chart:
                    self.send_alert(
                        "💼 Active Positions",
                        f"**{len(positions_data)} positions currently active**",
                        0x9932CC,
                        image_data=positions_chart
                    )
            
            # Send stuck positions alert if any
            if stuck_positions:
                stuck_count = len(stuck_positions)
                self.send_critical_alert(
                    f"STUCK POSITIONS: {stuck_count}",
                    f"**{stuck_count} positions require manual intervention**\n\nCheck your wallet for failed sells!",
                    None
                )
                    
        except Exception as e:
            logging.error(f"Hourly report error: {e}")
            # Send simple text report as fallback
            try:
                self.send_alert(
                    "📊 Hourly Report (Simplified)", 
                    f"**Stats:** {stats.get('trades', 0)} trades, {stats.get('win_rate', 0):.1f}% win rate\n"
                    f"**P&L:** {stats.get('pnl_sol', 0):+.3f} SOL",
                    0x0099ff
                )
            except Exception as e2:
                logging.error(f"Fallback report also failed: {e2}")
    
    def send_critical_alert(self, title, message, token=None):
        """Send URGENT alert for manual intervention"""
        urgent_title = f"🚨🚨🚨 URGENT: {title}"
        urgent_desc = f"**⚠️ MANUAL INTERVENTION NEEDED ⚠️**\n\n{message}"
        
        if token:
            urgent_desc += f"\n\n**Token:** `{token}`"
            urgent_desc += f"\n**Action:** Check wallet immediately!"
        
        fields = [
            {"name": "🚨 Priority", "value": "**CRITICAL**", "inline": True},
            {"name": "⏰ Time", "value": datetime.now().strftime("%H:%M:%S"), "inline": True},
            {"name": "📱 Action Required", "value": "**Manual intervention needed**", "inline": False}
        ]
        
        # Send twice for visibility
        for i in range(2):
            self.send_alert(urgent_title, urgent_desc, 0xFF00FF, fields)
        
        logging.critical(f"🚨 URGENT ALERT SENT: {title}")
    
    def send_stuck_position_alert(self, token, position, attempts):
        """Enhanced alert for positions that can't be sold"""
        try:
            entry_price = position.get('entry_price', 0)
            size = position.get('size', 0)
            
            # Estimate current value (conservative estimate)
            estimated_value = size * entry_price  # Conservative estimate
            
            self.send_critical_alert(
                "POSITION STUCK - MANUAL SELL NEEDED",
                f"🤖 Bot failed to sell after **{attempts} attempts**!\n\n"
                f"📊 **Position Details:**\n"
                f"• Size: **{size:.4f} SOL**\n"
                f"• Entry Price: **${entry_price:.8f}**\n"
                f"• Est. Value: **${estimated_value:.2f}**\n\n"
                f"🔥 **Immediate action recommended**\n"
                f"💡 Try manual sell with higher slippage",
                token
            )
        except Exception as e:
            logging.error(f"Stuck position alert error: {e}")
    
    def send_big_gain_alert(self, token, position, price_change, current_price=None):
        """Alert for significant unrealized gains"""
        if price_change > 40:
            size = position.get('size', 0)
            entry_price = position.get('entry_price', 0)
            
            if current_price:
                unrealized_profit = size * (current_price - entry_price)
                unrealized_usd = unrealized_profit * 240
            else:
                unrealized_profit = size * entry_price * (price_change / 100)
                unrealized_usd = unrealized_profit * 240
            
            title = f"🚀 BIG GAINS ALERT: {price_change:+.1f}%"
            description = f"**Position showing major unrealized gains!**\n\n"
            description += f"💰 **Unrealized Profit: {unrealized_profit:+.4f} SOL (${unrealized_usd:+.2f})**"
            
            fields = [
                {"name": "📈 Price Change", "value": f"**{price_change:+.1f}%**", "inline": True},
                {"name": "💎 Position Size", "value": f"**{size:.4f} SOL**", "inline": True},
                {"name": "🎯 Entry Price", "value": f"**${entry_price:.8f}**", "inline": True},
                {"name": "💡 Suggestion", "value": "Consider taking profits manually if bot doesn't respond", "inline": False}
            ]
            
            self.send_alert(title, description, 0x00FF88, fields)
    
    def close(self):
        """No cleanup needed for webhooks"""
        pass
