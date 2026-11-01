"""
Cosmic ki baddie — Bot-Hosting.net / Pterodactyl Default Entrypoint Alias (`app.py`)
Many Bot-Hosting.net Python containers default their Startup "App py file" to `app.py` or `main.py`.
This file ensures that regardless of whether `app.py` or `main.py` is configured in the
Bot-Hosting Startup tab, Cosmic ki baddie installs any missing packages and launches cleanly.
"""

from __future__ import annotations

import asyncio
from main import ensure_bothosting_requirements, run_bot

if __name__ == "__main__":
    ensure_bothosting_requirements()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
