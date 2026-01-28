#!/usr/bin/env python3
"""
Ring Doorbell Package Thief Detector

Monitors Ring doorbell for motion events, analyzes snapshots with Claude Vision,
and saves images of potential package thieves to Google Drive.
"""

import asyncio
import base64
import json
import logging
import os
from dotenv import load_dotenv
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import openai
from ring_doorbell import Auth, AuthenticationError, Ring, RingEventListener

# Optional imports for Google Drive
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_DRIVE_AVAILABLE = True
except ImportError:
    GOOGLE_DRIVE_AVAILABLE = False
    print("⚠️  Google Drive libraries not installed. Run: pip install google-api-python-client google-auth-oauthlib")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================
load_dotenv()
CONFIG = {
    # Ring authentication file (created during setup)
    "ring_auth_file": os.getenv("RING_AUTH_FILE", "ring_auth.json"),
    
    # OpenAI API key
    "openai_api_key": os.getenv("OPENAI_API_KEY"),
    
    # Google Drive folder ID to save flagged images
    "google_drive_folder_id": os.getenv("GOOGLE_DRIVE_FOLDER_ID"),
    
    # Local directory to save images (backup/fallback)
    "local_save_dir": os.environ.get("LOCAL_SAVE_DIR", "./flagged_images"),
    
    # Cooldown between motion events (seconds) to avoid spam
    "motion_cooldown": int(os.environ.get("MOTION_COOLDOWN", 30)),
    
    # Google OAuth credentials file (for Drive upload)
    "google_credentials_file": os.environ.get("GOOGLE_CREDENTIALS_FILE", "google_credentials.json"),
    
    # Specific doorbell name to monitor (None = all doorbells)
    "doorbell_name": os.environ.get("RING_DOORBELL_NAME"),
}

# =============================================================================
# OPENAI VISION ANALYSIS
# =============================================================================

ANALYSIS_PROMPT = """Analyze this image from a doorbell camera that was triggered by motion.

Your task is to determine if there's a potential package thief in this image.

Look for these suspicious behaviors:
1. Someone picking up a package that was left at the door
2. Someone looking around suspiciously while near packages
3. Someone quickly grabbing something and leaving
4. Someone who doesn't appear to be a delivery person taking a package
5. Multiple people where one acts as a lookout

Also consider these innocent scenarios:
- The homeowner retrieving their own package
- A delivery person dropping off a package
- A neighbor or expected visitor
- Just motion from animals, cars, or wind

Respond with a JSON object:
{
    "is_suspicious": true/false,
    "confidence": "high"/"medium"/"low",
    "description": "Brief description of what you see",
    "reason": "Why you flagged or didn't flag this as suspicious"
}

Only set is_suspicious to true if you have medium or high confidence that package theft 
is occurring or about to occur. When in doubt, err on the side of caution (false positive 
is better than missing a thief).
"""


async def analyze_image_with_openai(
    image_data: bytes,
    api_key: str
) -> dict:
    """
    Send image to OpenAI GPT Vision API for package thief analysis.

    Args:
        image_data: JPEG image bytes
        api_key: OpenAI API key

    Returns:
        Analysis result dict with is_suspicious, confidence, description, reason
    """
    client = openai.OpenAI(api_key=api_key)

    image_base64 = base64.standard_b64encode(image_data).decode("utf-8")

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}",
                            },
                        },
                        {
                            "type": "text",
                            "text": ANALYSIS_PROMPT,
                        },
                    ],
                }
            ],
        )

        response_text = response.choices[0].message.content

        # Extract JSON from the response
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text.strip()

        result = json.loads(json_str)
        logger.info(f"GPT analysis: {result}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse GPT response as JSON: {e}")
        logger.error(f"Raw response: {response_text}")
        return {
            "is_suspicious": False,
            "confidence": "low",
            "description": "Failed to analyze image",
            "reason": f"JSON parse error: {e}",
        }
    except openai.APIError as e:
        logger.error(f"OpenAI API error: {e}")
        raise


# =============================================================================
# GOOGLE DRIVE UPLOAD
# =============================================================================

def get_google_drive_service():
    """
    Initialize and return Google Drive service.
    Handles OAuth flow for first-time authentication.
    """
    if not GOOGLE_DRIVE_AVAILABLE:
        return None
        
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    creds = None
    token_file = 'google_token.json'
    
    # Load existing credentials
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    
    # If no valid credentials, do OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CONFIG["google_credentials_file"]):
                logger.warning(
                    f"Google credentials file not found: {CONFIG['google_credentials_file']}. "
                    "Download from Google Cloud Console and save as 'google_credentials.json'"
                )
                return None
                
            flow = InstalledAppFlow.from_client_secrets_file(
                CONFIG["google_credentials_file"], SCOPES
            )
            creds = flow.run_local_server(port=0)
        
        # Save credentials for next run
        with open(token_file, 'w') as token:
            token.write(creds.to_json())
    
    return build('drive', 'v3', credentials=creds)


async def upload_to_google_drive(
    image_data: bytes,
    filename: str,
    analysis: dict,
    folder_id: Optional[str] = None
) -> Optional[str]:
    """
    Upload image to Google Drive.
    
    Args:
        image_data: JPEG image bytes
        filename: Name for the file
        analysis: Analysis result from Claude
        folder_id: Google Drive folder ID
        
    Returns:
        File ID if successful, None otherwise
    """
    service = get_google_drive_service()
    if not service:
        logger.warning("Google Drive not available, skipping upload")
        return None
    
    # Save temporarily
    temp_path = Path("/tmp") / filename
    temp_path.write_bytes(image_data)
    
    # Prepare metadata with analysis in description
    file_metadata = {
        'name': filename,
        'description': json.dumps(analysis, indent=2),
    }
    
    if folder_id:
        file_metadata['parents'] = [folder_id]
    
    try:
        media = MediaFileUpload(str(temp_path), mimetype='image/jpeg')
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        logger.info(f"Uploaded to Google Drive: {file.get('webViewLink')}")
        return file.get('id')
        
    except Exception as e:
        logger.error(f"Google Drive upload failed: {e}")
        return None
    finally:
        # Clean up temp file
        temp_path.unlink(missing_ok=True)


# =============================================================================
# LOCAL FILE SAVING
# =============================================================================

def save_locally(
    image_data: bytes,
    filename: str,
    analysis: dict
) -> Path:
    """
    Save image and analysis locally as fallback.
    
    Args:
        image_data: JPEG image bytes
        filename: Name for the file
        analysis: Analysis result from Claude
        
    Returns:
        Path to saved image
    """
    save_dir = Path(CONFIG["local_save_dir"])
    save_dir.mkdir(parents=True, exist_ok=True)
    
    # Save image
    image_path = save_dir / filename
    image_path.write_bytes(image_data)
    
    # Save analysis alongside
    analysis_path = save_dir / f"{filename}.json"
    analysis_path.write_text(json.dumps(analysis, indent=2))
    
    logger.info(f"Saved locally: {image_path}")
    return image_path


# =============================================================================
# RING DOORBELL MONITORING
# =============================================================================

class PackageThiefDetector:
    """Main class for monitoring Ring doorbell and detecting package thieves."""

    def __init__(self):
        self.ring: Optional[Ring] = None
        self.auth: Optional[Auth] = None
        self.auth_file: Optional[Path] = None
        self.token_updated_callback = None
        self.last_motion_time: dict[str, datetime] = {}  # device_id -> last motion time

    async def authenticate(self) -> bool:
        """
        Authenticate with Ring API.

        Returns:
            True if successful, False otherwise
        """
        self.auth_file = Path(CONFIG["ring_auth_file"])

        if not self.auth_file.exists():
            logger.error(
                f"Ring auth file not found: {self.auth_file}. "
                "Run ring_auth_setup.py first to authenticate."
            )
            return False

        def token_updated(token: dict):
            """Callback to save updated tokens."""
            self.auth_file.write_text(json.dumps(token))
            logger.debug("Ring token updated")

        self.token_updated_callback = token_updated

        try:
            token = json.loads(self.auth_file.read_text())
            self.auth = Auth("PackageThiefDetector/1.0", token, token_updated)
            self.ring = Ring(self.auth)
            try:
                await self.ring.async_create_session()
            except AuthenticationError:
                logger.error(
                    "Ring auth token has expired. "
                    "Run ring_auth_setup.py again to re-authenticate."
                )
                return False
            await self.ring.async_update_data()
            logger.info("Successfully authenticated with Ring")
            return True

        except AuthenticationError:
            logger.error(
                "Ring authentication failed. "
                "Run ring_auth_setup.py again to re-authenticate."
            )
            return False
        except Exception as e:
            logger.error(f"Ring authentication failed: {e}")
            return False

    async def close(self):
        """Clean up Ring auth connection."""
        if self.auth:
            await self.auth.async_close()
    
    def get_doorbells(self):
        """Get list of Ring doorbells."""
        devices = self.ring.devices()
        doorbells = devices.doorbells
        
        if CONFIG["doorbell_name"]:
            doorbells = [d for d in doorbells if d.name == CONFIG["doorbell_name"]]
            
        return doorbells
    
    async def get_snapshot(self, doorbell) -> Optional[bytes]:
        """
        Capture a frame from the doorbell's live stream via WebRTC.

        Args:
            doorbell: Ring doorbell device

        Returns:
            JPEG image bytes or None
        """
        import io

        from aiortc import RTCPeerConnection, RTCSessionDescription

        logger.info(f"Connecting to live stream on {doorbell.name}...")

        pc = RTCPeerConnection()
        frame_future: asyncio.Future[bytes] = asyncio.get_event_loop().create_future()
        session_id = None

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                asyncio.ensure_future(_capture_frame(track))

        async def _capture_frame(track):
            try:
                # Skip a few frames to let the stream stabilise
                for _ in range(5):
                    frame = await track.recv()
                img = frame.to_image()
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                if not frame_future.done():
                    frame_future.set_result(buf.getvalue())
            except Exception as exc:
                if not frame_future.done():
                    frame_future.set_exception(exc)

        try:
            pc.addTransceiver("video", direction="recvonly")
            pc.addTransceiver("audio", direction="recvonly")

            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Wait for ICE gathering to finish so all candidates are in the offer
            while pc.iceGatheringState != "complete":
                await asyncio.sleep(0.1)

            local_sdp = pc.localDescription.sdp

            # Exchange SDP with Ring
            sdp_answer = await doorbell.generate_webrtc_stream(local_sdp)
            session_id = local_sdp.split("o=")[1].split()[1] if "o=" in local_sdp else None

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp_answer, type="answer")
            )

            frame_data = await asyncio.wait_for(frame_future, timeout=15)
            logger.info(f"Captured live frame: {len(frame_data)} bytes")
            return frame_data

        except asyncio.TimeoutError:
            logger.error("Timed out waiting for live stream frame")
            return None
        except Exception as e:
            logger.error(f"Live stream capture failed: {e}")
            return None
        finally:
            if session_id:
                try:
                    await doorbell.close_webrtc_stream(session_id)
                except Exception:
                    pass
            await pc.close()
    
    def should_process_motion(self, device_id: str) -> bool:
        """
        Check if we should process this motion event (cooldown check).
        
        Args:
            device_id: Ring device ID
            
        Returns:
            True if we should process, False if in cooldown
        """
        now = datetime.now()
        last_time = self.last_motion_time.get(device_id)
        
        if last_time:
            elapsed = (now - last_time).total_seconds()
            if elapsed < CONFIG["motion_cooldown"]:
                logger.debug(f"Motion cooldown active, {CONFIG['motion_cooldown'] - elapsed:.0f}s remaining")
                return False
        
        self.last_motion_time[device_id] = now
        return True
    
    async def handle_motion(self, doorbell):
        """
        Handle a motion event from a doorbell.
        
        Args:
            doorbell: Ring doorbell device that detected motion
        """
        device_id = str(doorbell.id)
        
        if not self.should_process_motion(device_id):
            return
        
        logger.info(f"🔔 Motion detected on {doorbell.name}")
        
        # Get snapshot
        snapshot = await self.get_snapshot(doorbell)
        if not snapshot:
            logger.warning("Could not get snapshot, skipping analysis")
            return
        
        # Analyze with GPT Vision
        if not CONFIG["openai_api_key"]:
            logger.error("OPENAI_API_KEY not set!")
            return

        logger.info("Analyzing image with GPT Vision...")
        analysis = await analyze_image_with_openai(
            snapshot,
            CONFIG["openai_api_key"]
        )
        
        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ring_{doorbell.name}_{timestamp}.jpg"
        filename = filename.replace(" ", "_")
        
        # If suspicious, save the image
        if analysis.get("is_suspicious"):
            logger.warning(f"⚠️  SUSPICIOUS ACTIVITY DETECTED: {analysis.get('description')}")
            
            # Try Google Drive first
            if CONFIG["google_drive_folder_id"]:
                await upload_to_google_drive(
                    snapshot,
                    filename,
                    analysis,
                    CONFIG["google_drive_folder_id"]
                )
            
            # Always save locally as backup
            save_locally(snapshot, filename, analysis)
            
            # TODO: Add notifications here (Pushover, email, SMS, etc.)
            
        else:
            logger.info(f"✅ No suspicious activity: {analysis.get('description')}")
    
    async def start_monitoring(self):
        """
        Start monitoring Ring doorbells for motion events.
        Uses polling since push notifications require additional setup.
        """
        if not self.ring:
            logger.error("Not authenticated with Ring")
            return
        
        doorbells = self.get_doorbells()
        if not doorbells:
            logger.error("No doorbells found!")
            return
        
        logger.info(f"Monitoring {len(doorbells)} doorbell(s): {[d.name for d in doorbells]}")
        
        # Track last known ding IDs to detect new events
        last_ding_ids: dict[str, set] = {str(d.id): set() for d in doorbells}
        
        logger.info("Starting motion monitoring loop (Ctrl+C to stop)...")
        
        try:
            while True:
                # Refresh data from Ring
                await self.ring.async_update_data()
                
                for doorbell in doorbells:
                    device_id = str(doorbell.id)
                    
                    # Check for active dings (motion events)
                    try:
                        # Get recent history
                        history = await doorbell.async_history(limit=15)
                        
                        for event in history:
                            event_id = event.get('id')
                            event_kind = event.get('kind')
                            
                            # Only process motion events we haven't seen
                            if event_kind == 'motion' and event_id not in last_ding_ids[device_id]:
                                # Check if this is a recent event (within last 2 minutes)
                                created_at = event.get('created_at')
                                if created_at:
                                    # Parse and check age
                                    # Ring returns ISO format timestamps
                                    pass  # For simplicity, process all new events
                                
                                last_ding_ids[device_id].add(event_id)
                                
                                # Keep set from growing forever
                                if len(last_ding_ids[device_id]) > 100:
                                    last_ding_ids[device_id] = set(list(last_ding_ids[device_id])[-50:])
                                
                                await self.handle_motion(doorbell)
                                
                    except Exception as e:
                        logger.error(f"Error checking doorbell {doorbell.name}: {e}")
                
                # Poll every 10 seconds
                await asyncio.sleep(10)
                
        except KeyboardInterrupt:
            logger.info("Stopping monitoring...")
        except Exception as e:
            logger.error(f"Monitoring error: {e}")
            raise


# =============================================================================
# PUSH NOTIFICATION LISTENER (ALTERNATIVE APPROACH)
# =============================================================================

async def start_with_push_notifications():
    """
    Alternative: Use Ring push notifications for faster event detection.
    Requires the ring_doorbell[listen] extra.
    """
    detector = PackageThiefDetector()
    if not await detector.authenticate():
        return

    doorbells = detector.get_doorbells()
    if not doorbells:
        logger.error("No doorbells found!")
        return

    doorbell_ids = {str(d.id) for d in doorbells}

    def on_event(event):
        """Callback for RingEvent notifications."""
        device_id = str(event.doorbot_id)
        event_kind = event.kind

        if event_kind != 'motion' or device_id not in doorbell_ids:
            return

        for doorbell in doorbells:
            if str(doorbell.id) == device_id:
                loop = asyncio.get_event_loop()
                loop.create_task(detector.handle_motion(doorbell))
                break

    # FCM credentials are separate from Ring auth tokens.
    # Pass None on first run; FCM will register and we save via callback.
    fcm_credentials_file = Path("fcm_credentials.json")
    fcm_credentials = None
    if fcm_credentials_file.is_file():
        fcm_credentials = json.loads(fcm_credentials_file.read_text())

    def fcm_credentials_updated(credentials):
        """Save FCM credentials when updated."""
        fcm_credentials_file.write_text(json.dumps(credentials))
        logger.debug("FCM credentials updated")

    listener = RingEventListener(
        detector.ring, fcm_credentials, fcm_credentials_updated
    )
    listener.add_notification_callback(on_event)

    logger.info("Starting push notification listener...")

    try:
        started = await listener.start()
        if not started:
            logger.error("Failed to start push notification listener")
            return
        logger.info("Push notification listener active, waiting for events...")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
    finally:
        if listener.started:
            await listener.stop()
        await detector.close()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Ring Package Thief Detector")
    logger.info("=" * 60)
    
    # Validate configuration
    if not CONFIG["openai_api_key"]:
        logger.error("OPENAI_API_KEY environment variable not set!")
        logger.error("Set it with: export OPENAI_API_KEY='your-key-here'")
        sys.exit(1)
    
    # Check if using push notifications or polling
    use_push = os.environ.get("USE_PUSH_NOTIFICATIONS", "").lower() == "true"
    
    if use_push:
        logger.info("Using push notifications for motion detection")
        await start_with_push_notifications()
    else:
        logger.info("Using polling for motion detection (set USE_PUSH_NOTIFICATIONS=true for push)")
        detector = PackageThiefDetector()
        try:
            if await detector.authenticate():
                await detector.start_monitoring()
        finally:
            await detector.close()


if __name__ == "__main__":
    asyncio.run(main())
