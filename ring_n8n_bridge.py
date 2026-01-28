#!/usr/bin/env python3
"""
Ring Motion to n8n Webhook Bridge

A lightweight script that monitors Ring for motion events and sends
them to an n8n webhook for processing.

This is useful if you want to use n8n for the LLM analysis and
Google Drive upload logic.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import aiohttp
from ring_doorbell import Auth, Ring

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "ring_auth_file": os.environ.get("RING_AUTH_FILE", "ring_auth.json"),
    "n8n_webhook_url": os.environ.get("N8N_WEBHOOK_URL"),  # Required
    "doorbell_name": os.environ.get("RING_DOORBELL_NAME"),  # Optional
    "motion_cooldown": int(os.environ.get("MOTION_COOLDOWN", 30)),
}


class RingToN8nBridge:
    """Bridges Ring motion events to n8n webhooks."""
    
    def __init__(self):
        self.ring: Optional[Ring] = None
        self.last_motion_time: dict[str, datetime] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None
        
    async def setup(self) -> bool:
        """Initialize Ring connection and HTTP session."""
        # Create HTTP session
        self.http_session = aiohttp.ClientSession()
        
        # Authenticate with Ring
        auth_file = Path(CONFIG["ring_auth_file"])
        
        if not auth_file.exists():
            logger.error(f"Ring auth file not found: {auth_file}")
            return False
        
        def token_updated(token: dict):
            auth_file.write_text(json.dumps(token))
        
        try:
            token = json.loads(auth_file.read_text())
            auth = Auth("RingN8nBridge/1.0", token, token_updated)
            self.ring = Ring(auth)
            await self.ring.async_update_data()
            logger.info("Connected to Ring")
            return True
            
        except Exception as e:
            logger.error(f"Ring authentication failed: {e}")
            return False
    
    async def cleanup(self):
        """Clean up resources."""
        if self.http_session:
            await self.http_session.close()
    
    def get_doorbells(self):
        """Get list of doorbells to monitor."""
        devices = self.ring.devices()
        doorbells = devices.get('doorbots', [])
        
        if CONFIG["doorbell_name"]:
            doorbells = [d for d in doorbells if d.name == CONFIG["doorbell_name"]]
            
        return doorbells
    
    def should_process(self, device_id: str) -> bool:
        """Check cooldown."""
        now = datetime.now()
        last = self.last_motion_time.get(device_id)
        
        if last and (now - last).total_seconds() < CONFIG["motion_cooldown"]:
            return False
        
        self.last_motion_time[device_id] = now
        return True
    
    async def send_to_n8n(self, doorbell, snapshot: bytes):
        """Send motion event and snapshot to n8n webhook."""
        if not CONFIG["n8n_webhook_url"]:
            logger.error("N8N_WEBHOOK_URL not configured!")
            return
        
        payload = {
            "doorbell_name": doorbell.name,
            "doorbell_id": str(doorbell.id),
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "image_base64": base64.standard_b64encode(snapshot).decode("utf-8"),
        }
        
        try:
            async with self.http_session.post(
                CONFIG["n8n_webhook_url"],
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    logger.info(f"✅ Sent to n8n webhook")
                else:
                    logger.error(f"n8n webhook returned {response.status}")
                    
        except Exception as e:
            logger.error(f"Failed to send to n8n: {e}")
    
    async def handle_motion(self, doorbell):
        """Handle motion event."""
        device_id = str(doorbell.id)
        
        if not self.should_process(device_id):
            return
        
        logger.info(f"🔔 Motion on {doorbell.name}")
        
        # Get snapshot
        try:
            snapshot = await doorbell.async_get_snapshot()
            logger.info(f"Got snapshot: {len(snapshot)} bytes")
            await self.send_to_n8n(doorbell, snapshot)
        except Exception as e:
            logger.error(f"Failed to get snapshot: {e}")
    
    async def run(self):
        """Main monitoring loop."""
        doorbells = self.get_doorbells()
        if not doorbells:
            logger.error("No doorbells found!")
            return
        
        logger.info(f"Monitoring: {[d.name for d in doorbells]}")
        logger.info(f"Webhook: {CONFIG['n8n_webhook_url']}")
        
        last_ding_ids: dict[str, set] = {str(d.id): set() for d in doorbells}
        
        try:
            while True:
                await self.ring.async_update_data()
                
                for doorbell in doorbells:
                    device_id = str(doorbell.id)
                    
                    try:
                        history = await doorbell.async_history(limit=5)
                        
                        for event in history:
                            event_id = event.get('id')
                            
                            if event.get('kind') == 'motion' and event_id not in last_ding_ids[device_id]:
                                last_ding_ids[device_id].add(event_id)
                                
                                # Keep set bounded
                                if len(last_ding_ids[device_id]) > 100:
                                    last_ding_ids[device_id] = set(list(last_ding_ids[device_id])[-50:])
                                
                                await self.handle_motion(doorbell)
                                
                    except Exception as e:
                        logger.error(f"Error checking {doorbell.name}: {e}")
                
                await asyncio.sleep(10)
                
        except KeyboardInterrupt:
            logger.info("Stopping...")


async def main():
    if not CONFIG["n8n_webhook_url"]:
        print("Error: N8N_WEBHOOK_URL environment variable not set!")
        print()
        print("Set it with:")
        print("  export N8N_WEBHOOK_URL='https://your-n8n-instance/webhook/ring-motion'")
        sys.exit(1)
    
    bridge = RingToN8nBridge()
    
    if not await bridge.setup():
        sys.exit(1)
    
    try:
        await bridge.run()
    finally:
        await bridge.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
