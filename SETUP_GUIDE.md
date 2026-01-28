# Detailed Setup Guide

This guide walks you through setting up the Ring Package Thief Detector step by step.

## Prerequisites

- Python 3.9 or higher
- A Ring doorbell (works with free plan!)
- An Anthropic API key
- (Optional) Google Cloud project for Drive upload

## Step 1: Clone and Install

```bash
# Create project directory
mkdir ring-package-thief-detector
cd ring-package-thief-detector

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install ring_doorbell[listen] anthropic google-api-python-client google-auth-oauthlib aiohttp
```

## Step 2: Authenticate with Ring

Ring uses OAuth2 with 2FA support. Run the setup script:

```bash
python ring_auth_setup.py
```

You'll be prompted for:
1. Your Ring email address
2. Your Ring password
3. A 2FA code (if you have 2FA enabled)

This creates a `ring_auth.json` file with your refresh token.

**Important**: Keep this file secure! It provides access to your Ring account.

## Step 3: Get Your Anthropic API Key

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account or sign in
3. Navigate to API Keys
4. Create a new key
5. Copy the key (starts with `sk-ant-`)

Set it as an environment variable:
```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

## Step 4: (Optional) Set Up Google Drive

If you want flagged images saved to Google Drive:

### Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project
3. Enable the Google Drive API:
   - Go to APIs & Services > Library
   - Search for "Google Drive API"
   - Click Enable

### Create OAuth Credentials

1. Go to APIs & Services > Credentials
2. Click "Create Credentials" > "OAuth client ID"
3. Choose "Desktop app"
4. Download the JSON file
5. Rename it to `google_credentials.json` and place in project directory

### Get Your Folder ID

1. Open Google Drive
2. Create or navigate to the folder where you want images saved
3. Copy the folder ID from the URL:
   - URL: `https://drive.google.com/drive/folders/1ABC123xyz`
   - Folder ID: `1ABC123xyz`

Set the environment variable:
```bash
export GOOGLE_DRIVE_FOLDER_ID="1ABC123xyz"
```

### First Run OAuth

The first time you run the detector, it will:
1. Open a browser for Google OAuth
2. Ask you to sign in and authorize the app
3. Save a `google_token.json` file for future use

## Step 5: Run the Detector

### Basic Usage

```bash
python package_thief_detector.py
```

### With All Options

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GOOGLE_DRIVE_FOLDER_ID="1ABC123xyz"
export RING_DOORBELL_NAME="Front Door"  # Optional: specific doorbell
export MOTION_COOLDOWN=30  # Seconds between events

python package_thief_detector.py
```

### Using Docker

```bash
# Build
docker build -t ring-package-thief .

# Run
docker run -d \
  --name ring-detector \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e GOOGLE_DRIVE_FOLDER_ID="1ABC123xyz" \
  -v $(pwd)/ring_auth.json:/app/ring_auth.json \
  -v $(pwd)/flagged_images:/app/flagged_images \
  ring-package-thief
```

### Using Docker Compose

```bash
# Copy environment file
cp .env.example .env

# Edit .env with your values
nano .env

# Start
docker-compose up -d

# View logs
docker-compose logs -f
```

## Step 6: (Alternative) n8n Hybrid Approach

If you prefer using n8n for the LLM and Google Drive parts:

### Start n8n

```bash
docker run -d \
  --name n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  n8nio/n8n
```

Open http://localhost:5678 and:
1. Import the `n8n_workflow.json` file
2. Set up credentials for Anthropic API and Google Drive
3. Activate the workflow
4. Copy the webhook URL

### Run the Bridge Script

```bash
export N8N_WEBHOOK_URL="http://localhost:5678/webhook/ring-motion"
python ring_n8n_bridge.py
```

## Troubleshooting

### "Ring authentication failed"

- Double-check email and password
- Try logging into ring.com in a browser first
- If you have 2FA, make sure you're entering the code quickly
- Ring may temporarily block logins after too many attempts

### "Could not get snapshot"

- Battery-powered doorbells can only snapshot when not recording
- There's a brief delay after motion before snapshot is available
- Check your doorbell's battery level

### "Claude API error"

- Verify your API key is correct
- Check your Anthropic account has credits
- Ensure the image isn't too large (should be fine for Ring snapshots)

### "Google Drive upload failed"

- Re-run OAuth flow by deleting `google_token.json`
- Verify the folder ID is correct
- Check your Google Cloud project has Drive API enabled

## Running as a Service (Linux)

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/ring-detector.service
```

```ini
[Unit]
Description=Ring Package Thief Detector
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/path/to/ring-package-thief-detector
Environment=ANTHROPIC_API_KEY=sk-ant-...
Environment=GOOGLE_DRIVE_FOLDER_ID=1ABC123xyz
ExecStart=/path/to/venv/bin/python package_thief_detector.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ring-detector
sudo systemctl start ring-detector

# Check status
sudo systemctl status ring-detector

# View logs
journalctl -u ring-detector -f
```

## Adding Notifications

The detector saves images but doesn't send notifications by default. Here are some options:

### Pushover (Mobile Push Notifications)

Add to `package_thief_detector.py`:

```python
import httpx

async def send_pushover(message: str, image_path: str = None):
    async with httpx.AsyncClient() as client:
        data = {
            "token": os.environ["PUSHOVER_APP_TOKEN"],
            "user": os.environ["PUSHOVER_USER_KEY"],
            "message": message,
        }
        files = {}
        if image_path:
            files["attachment"] = open(image_path, "rb")
        
        await client.post(
            "https://api.pushover.net/1/messages.json",
            data=data,
            files=files
        )
```

### Email via SMTP

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

def send_email_alert(subject: str, body: str, image_data: bytes = None):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_FROM"]
    msg["To"] = os.environ["SMTP_TO"]
    
    msg.attach(MIMEText(body))
    
    if image_data:
        img = MIMEImage(image_data)
        img.add_header("Content-Disposition", "attachment", filename="alert.jpg")
        msg.attach(img)
    
    with smtplib.SMTP(os.environ["SMTP_HOST"], 587) as server:
        server.starttls()
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)
```

## Security Considerations

1. **Protect your auth files**: `ring_auth.json` and `google_token.json` provide access to your accounts
2. **Use environment variables**: Don't hardcode API keys in scripts
3. **Restrict network access**: If self-hosting, use a firewall
4. **Review flagged images**: LLMs can make mistakes; review before taking action
5. **Consider privacy**: This system captures images of people at your door
