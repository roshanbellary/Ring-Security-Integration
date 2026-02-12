#!/usr/bin/env python3
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from package_thief_detector import main

if __name__ == "__main__":
    asyncio.run(main())
