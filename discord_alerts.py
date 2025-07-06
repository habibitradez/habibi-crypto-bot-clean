import requests
import json
from datetime import datetime

class DiscordBotAlerts:
    def __init__(self, webhook_url, channel_id=None, role_id=None):
        self.webhook_url = webhook_url
        
        # Send test message
        if webhook_url:
            self.send_alert("🚀 Bot Started", "Trading bot is now online!", color=0x00ff00)
    
    def send_alert(self, title, description, color=0x0099ff, fields=None, ping_role=False):
        try:
            embed = {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            if fields:
                embed["fields"] = fields
            
            data = {"embeds": [embed]}
            
            response = requests.post(self.webhook_url, json=data)
            if response.status_code == 204:
                logging.info(f"✅ Discord alert sent: {title}")
            else:
                logging.error(f"Discord webhook failed: {response.status_code}")
                
        except Exception as e:
            logging.error(f"Discord webhook error: {e}")
