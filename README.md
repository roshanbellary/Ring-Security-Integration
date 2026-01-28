# Ring Doorbell Package Thief Detector

An automation that captures snapshots from your Ring doorbell on motion, analyzes them with an LLM (Claude) to detect potential package thieves, and saves flagged images to Google Drive.

## Important: Free Plan Limitations

On Ring's **free plan** (no subscription):
- ✅ **Live View** works
- ✅ **Motion alerts** work  
- ✅ **On-demand snapshots** work (via API)
- ❌ No cloud video recording
- ❌ No Snapshot Capture feature (scheduled snapshots)

The good news: The unofficial Ring APIs can still request snapshots, which is all we need for this project!

## Architecture Options

### Option 1: Standalone Python Service (Recommended)
A continuously-running Python service that:
1. Listens for Ring motion events via push notifications
2. Captures a snapshot when motion is detected
3. Sends the image to Claude API for analysis
4. Uploads flagged images to Google Drive
5. Sends notifications (optional)

### Option 2: n8n + Python Bridge
- Python service handles Ring connection and motion events
- Sends webhook to n8n when motion detected (with image)
- n8n handles LLM analysis and Google Drive upload

### Option 3: Node.js Service
Similar to Option 1, using the `ring-client-api` npm package.

---

## Quick Start (Python - Recommended)

### 1. Install Dependencies

```bash
pip install ring_doorbell[listen] anthropic google-api-python-client google-auth-oauthlib
```

### 2. Set Up Ring Authentication

```bash
# First-time setup - will prompt for username/password/2FA
python -c "from ring_doorbell import Auth; Auth('ring_auth.json')"
```

### 3. Configure Environment Variables

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export GOOGLE_DRIVE_FOLDER_ID="your-google-drive-folder-id"
```

### 4. Run the Service

```bash
python package_thief_detector.py
```

---

## Files in This Project

- `package_thief_detector.py` - Main Python service
- `ring_auth_setup.py` - Helper script for Ring authentication
- `google_drive_helper.py` - Google Drive upload utilities
- `n8n_workflow.json` - n8n workflow (if using hybrid approach)
- `config.example.yaml` - Configuration template

---

## How It Works

```
┌─────────────────┐
│  Ring Doorbell  │
│  (Motion Event) │
└────────┬────────┘
         │ Push Notification
         ▼
┌─────────────────┐
│  Python Service │
│  (Listener)     │
└────────┬────────┘
         │ Request Snapshot
         ▼
┌─────────────────┐
│  Ring API       │
│  (Get Snapshot) │
└────────┬────────┘
         │ Image (JPEG)
         ▼
┌─────────────────┐
│  Claude Vision  │
│  API Analysis   │
└────────┬────────┘
         │ Is Package Thief?
         ▼
    ┌────┴────┐
    │ YES     │ NO
    ▼         ▼
┌───────┐  (discard)
│Google │
│Drive  │
└───────┘
```

## Notes on Ring API

- Ring doesn't have an official public API
- This uses the `python-ring-doorbell` library (reverse-engineered)
- Refresh tokens expire quickly after use - the library handles token refresh
- For battery-powered doorbells, snapshots may have a ~5-10 second delay
