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
from ring_doorbell import Auth, AuthenticationError, Ring, RingEventListener
from aiortc import RTCPeerConnection, RTCSessionDescription
from pydub import AudioSegment
from audio_file_track import AudioFileTrack
from google_drive_class import GoogleDriveWriter
from llm_analysis import DeterminationEngine
from notifier import EmailNotifier
import numpy as np

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

    # Alert sound file path
    "alert_sound_file": os.environ.get("ALERT_SOUND_FILE", "./sound_effects/alert.mp3"),

    # Duration to play alert sound (seconds)
    "alert_sound_duration": int(os.environ.get("ALERT_SOUND_DURATION", 3)),

    # Email notification settings
    "sender_email": os.environ.get("SENDER_EMAIL", "roshan.bellary@gmail.com"),
    "email_app_password": os.environ.get("EMAIL_APP_PASSWORD"),
    "notification_recipients": [
        r.strip() for r in os.environ.get("NOTIFICATION_RECIPIENTS", "").split(",") if r.strip()
    ],
}



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
        self.google_drive_writer = GoogleDriveWriter(CONFIG["google_credentials_file"], CONFIG["google_drive_folder_id"], CONFIG["local_save_dir"], True)
        self.detection_engine = DeterminationEngine(CONFIG["openai_api_key"])
        self.notifier = None
        if CONFIG["email_app_password"] and CONFIG["notification_recipients"]:
            self.notifier = EmailNotifier(
                CONFIG["sender_email"],
                CONFIG["email_app_password"],
                CONFIG["notification_recipients"],
            )
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
    
    async def play_alert_through_doorbell(self, doorbell) -> bool:
        """
        Play alert audio through the doorbell's speaker via WebRTC.

        Args:
            doorbell: Ring doorbell device

        Returns:
            True if successful, False otherwise
        """



        sound_file = Path(CONFIG["alert_sound_file"])
        duration = CONFIG["alert_sound_duration"]

        if not sound_file.exists():
            logger.warning(f"Alert sound file not found: {sound_file}")
            return False

        logger.info(f"🔊 Playing alert through {doorbell.name} speaker for {duration}s...")

        # Load and prepare audio
        try:
            audio = AudioSegment.from_mp3(str(sound_file))
            # Convert to format suitable for WebRTC (48kHz, mono, 16-bit)
            audio = audio.set_frame_rate(48000).set_channels(1).set_sample_width(2)
            # Trim to duration
            audio = audio[:int(duration * 1000)]
            audio_samples = np.array(audio.get_array_of_samples(), dtype=np.int16)
        except Exception as e:
            logger.error(f"Failed to load audio file: {e}")
            return False

        

        pc = RTCPeerConnection()
        session_id = None
        audio_track = AudioFileTrack(audio_samples)

        try:
            # Add audio track for sending
            pc.addTrack(audio_track)
            # Add video transceiver as receive-only (required by Ring)
            pc.addTransceiver("video", direction="recvonly")

            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)

            # Wait for ICE gathering
            while pc.iceGatheringState != "complete":
                await asyncio.sleep(0.1)

            local_sdp = pc.localDescription.sdp

            # Exchange SDP with Ring
            sdp_answer = await doorbell.generate_webrtc_stream(local_sdp)
            session_id = local_sdp.split("o=")[1].split()[1] if "o=" in local_sdp else None

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp_answer, type="answer")
            )

            # Keep connection open for duration
            logger.info(f"Streaming audio for {duration} seconds...")
            await asyncio.sleep(duration + 0.5)
            logger.info("Alert audio finished")
            return True

        except Exception as e:
            logger.error(f"Failed to play alert through doorbell: {e}")
            return False
        finally:
            if session_id:
                try:
                    await doorbell.close_webrtc_stream(session_id)
                except Exception:
                    pass
            await pc.close()

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

        Args: doorbell: Ring doorbell device that detected motion
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
        analysis = await self.detection_engine.analyze_image_for_theft(snapshot)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ring_{doorbell.name}_{timestamp}.jpg".replace(" ", "_")
        description = analysis.get("description", "")

        if analysis.get("is_suspicious"):
            logger.warning(f"⚠️  SUSPICIOUS ACTIVITY DETECTED: {description}")

            await self.play_alert_through_doorbell(doorbell)

            if CONFIG["google_drive_folder_id"]:
                await self.google_drive_writer.upload_to_google_drive(
                    snapshot,
                    filename,
                    analysis,
                )

            self.google_drive_writer.save_locally(snapshot, filename, analysis)

            if self.notifier:
                self.notifier.notify_thief_detected(description)
        elif analysis.get("is_delivery"):
            logger.info(f"✅ PACKAGE DELIVERY DETECTED: {description}")

            if self.notifier:
                self.notifier.notify_package_delivered(description)
        else:
            logger.info(f"✅ No suspicious activity: {description}")
async def start_with_push_notifications():
    """
    Use Ring push notifications for faster event detection.
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

        if event_kind == 'motion':
            for doorbell in doorbells:
                if str(doorbell.id) == device_id:
                    loop = asyncio.get_event_loop()
                    loop.create_task(detector.handle_motion(doorbell))
                    break
        else:
            for doorbell in doorbells:
                if str(doorbell.id) == device_id:
                    loop = asyncio.get_event_loop()
                    # Someone rang doorbell play the sound!
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
        logger.warn("Push notifications not authorized. Ending Program")

if __name__ == "__main__":
    asyncio.run(main())
