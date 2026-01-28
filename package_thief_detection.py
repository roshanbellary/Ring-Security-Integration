import asyncio
import base64
import json
import logging
import os
from dotenv import load_dotenv
import getpass
import asyncio
import json
from pathlib import Path

from ring_doorbell import Auth, AuthenticationError, Requires2FAError, Ring

load_dotenv()


CONFIG = {
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),

}

user_agent = "YourProjectName-1.0"  # Change this
cache_file = Path(user_agent + ".token.cache")


def token_updated(token):
    cache_file.write_text(json.dumps(token))


def otp_callback():
    auth_code = input("2FA code: ")
    return auth_code


async def do_auth():
    username = input("Username: ")
    password = getpass.getpass("Password: ")
    auth = Auth(user_agent, None, token_updated)
    try:
        await auth.async_fetch_token(username, password)
    except Requires2FAError:
        await auth.async_fetch_token(username, password, otp_callback())
    return auth


async def main():
    if cache_file.is_file():  # auth token is cached
        auth = Auth(user_agent, json.loads(cache_file.read_text()), token_updated)
        ring = Ring(auth)
        try:
            await ring.async_create_session()  # auth token still valid
        except AuthenticationError:  # auth token has expired
            auth = await do_auth()
    else:
        auth = await do_auth()  # Get new auth token
        ring = Ring(auth)

    await ring.async_update_data()

    devices = ring.devices()
    doorbell = devices.doorbells[0]
    
    print("Subscription:", doorbell.has_subscription)
    print("Live Streaming Json", doorbell.async_get_live_streaming_json())
    await auth.async_close()


if __name__ == "__main__":
    asyncio.run(main())