from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import logging
GOOGLE_DRIVE_AVAILABLE = True
from typing import Optional
from pathlib import Path
# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class GoogleDriveWriter():

    def __init__(self, google_credential_file_path, google_drive_folder_id, local_save_dir=None, google_drive_available=False):
        self.GOOGLE_DRIVE_AVAILABLE = google_drive_available
        self.scopes = None
        self.GOOGLE_CREDENTIAL_FILE_PATH = google_credential_file_path
        self.LOCAL_SAVE_DIR = local_save_dir
        self.GOOGLE_DRIVE_FOLDER_ID = google_drive_folder_id
         
    def get_google_drive_service(self):
        """
        initialize and return Google Drive service.
        handles OAuth flow for first-time authentication.
        """
        if not GOOGLE_DRIVE_AVAILABLE:
            return None
            
        self.SCOPES = ['https://www.googleapis.com/auth/drive.file']
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
                if not os.path.exists(self.GOOGLE_CREDENTIAL_FILE_PATH):
                    logger.warning(
                        f"Google credentials file not found: {self.GOOGLE_CREDENTIAL_FILE_PATH}. "
                        "Download from Google Cloud Console and save as 'google_credentials.json'"
                    )
                    return None
                    
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.GOOGLE_CREDENTIAL_FILE_PATH, self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            with open(token_file, 'w') as token:
                token.write(creds.to_json())
        
        return build('drive', 'v3', credentials=creds)

    async def upload_to_google_drive(
        self,
        image_data: bytes,
        filename: str,
        analysis: dict,
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
        service = self.get_google_drive_service()
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


        
        if self.GOOGLE_DRIVE_FOLDER_ID:
            file_metadata['parents'] = [self.GOOGLE_DRIVE_FOLDER_ID]
        
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
        if not self.LOCAL_SAVE_DIR:
            print("Local Save Parameter not given")
            return
        
        save_dir = Path(self.LOCAL_SAVE_DIR)
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # Save image
        image_path = save_dir / filename
        image_path.write_bytes(image_data)
        
        # Save analysis alongside
        analysis_path = save_dir / f"{filename}.json"
        analysis_path.write_text(json.dumps(analysis, indent=2))
        
        logger.info(f"Saved locally: {image_path}")
        return image_path