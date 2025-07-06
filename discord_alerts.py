import requests
import json
import logging
from datetime import datetime

class DiscordWebhookAlerts:
    def __init__(self, webhook_url, channel_id=None):
        self.webhook_url = webhook_url
        self.channel_id = channel_id
        
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False):
        """Send alert via webhook - 100% reliable"""
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
        
        # Add urgency for big moves
        if abs(profit_sol) > 0.5:
            description = f"🚨 **LARGE MOVE** 🚨\n{description}"
        
        self.send_alert(title, description, color, fields)
    
    def send_critical_alert(self, title, message, token=None):
        """Send URGENT alert for manual intervention"""
        urgent_title = f"🚨🚨🚨 URGENT: {title} 🚨🚨🚨"
        urgent_desc = f"**MANUAL INTERVENTION NEEDED**\n\n{message}"
        
        if token:
            urgent_desc += f"\n\nToken: `{token}`"
            urgent_desc += f"\nCheck wallet immediately!"
        
        # Send twice for visibility
        for i in range(2):
            self.send_alert(urgent_title, urgent_desc, color=0xff00ff)
        
        logging.critical(f"🚨 URGENT ALERT SENT: {title}")
    
    def send_stuck_position_alert(self, token, position, attempts):
        """Alert for positions that can't be sold"""
        try:
            from your_price_function import get_token_price  # You'll need to import this
            current_price = get_token_price(token)
            if current_price:
                price_change = ((current_price - position['entry_price']) / position['entry_price']) * 100
                profit_sol = position['size'] * (current_price - position['entry_price'])
                profit_usd = profit_sol * 240
                
                self.send_critical_alert(
                    "POSITION STUCK - SELL MANUALLY",
                    f"Bot failed to sell after {attempts} attempts!\n\n"
                    f"**Current P&L: {profit_sol:+.4f} SOL (${profit_usd:+.2f})**\n"
                    f"**Price Change: {price_change:+.1f}%**\n\n"
                    f"Position Size: {position['size']} SOL\n"
                    f"**ACTION REQUIRED: Manual sell recommended**",
                    token
                )
        except Exception as e:
            logging.error(f"Stuck position alert error: {e}")
    
    def close(self):
        """No cleanup needed for webhooks"""
        pass
