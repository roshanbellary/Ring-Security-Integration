#!/usr/bin/env python3
"""
Ring Authentication Setup

Run this script first to authenticate with Ring and save your credentials.
You'll need your Ring email, password, and will be prompted for 2FA if enabled.
"""

import asyncio
import json
import getpass
from pathlib import Path

from ring_doorbell import Auth, AuthenticationError, Requires2FAError


AUTH_FILE = "ring_auth.json"


async def setup_auth():
    """Interactive Ring authentication setup."""
    print("=" * 60)
    print("Ring Doorbell Authentication Setup")
    print("=" * 60)
    print()
    
    # Check for existing auth
    auth_path = Path(AUTH_FILE)
    if auth_path.exists():
        print(f"⚠️  Existing auth file found: {AUTH_FILE}")
        overwrite = input("Overwrite? (y/N): ").strip().lower()
        if overwrite != 'y':
            print("Keeping existing auth file.")
            return
    
    # Get credentials
    print("\nEnter your Ring account credentials:")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    
    if not email or not password:
        print("❌ Email and password are required!")
        return
    
    def token_updated(token: dict):
        """Save token to file when updated."""
        auth_path.write_text(json.dumps(token))
        print(f"✅ Token saved to {AUTH_FILE}")
    
    try:
        # First attempt - may require 2FA
        auth = Auth("PackageThiefDetector/1.0", None, token_updated)
        
        try:
            await auth.async_fetch_token(email, password)
        except Requires2FAError:
            # 2FA required
            print("\n📱 Two-factor authentication required!")
            otp = input("Enter the 2FA code from your authenticator app or SMS: ").strip()
            await auth.async_fetch_token(email, password, otp)
        
        print("\n✅ Authentication successful!")
        print(f"✅ Credentials saved to: {AUTH_FILE}")
        print("\nYou can now run the package thief detector:")
        print("  python package_thief_detector.py")
        
    except AuthenticationError as e:
        print(f"\n❌ Authentication failed: {e}")
        print("\nPossible causes:")
        print("  - Incorrect email or password")
        print("  - Account locked due to too many attempts")
        print("  - Ring has changed their authentication method")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise


def main():
    asyncio.run(setup_auth())


if __name__ == "__main__":
    main()
