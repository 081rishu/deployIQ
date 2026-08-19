"""WebSocket test client for the voice interviewer.

Connects to /ws/interview/voice, sends a `start` message, then sends a
TTS-generated audio clip as the simulated user's spoken answer, and prints
each JSON reply from the server.

Usage:
    .venv/Scripts/python scripts/ws_test_client.py
    .venv/Scripts/python scripts/ws_test_client.py --url ws://localhost:8001
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Imports resolve from the editable src-layout installation.

import websockets

from llm.tts import synthesize


async def main(url: str) -> None:
    print(f"connecting to {url}")
    # Use the legacy websockets.connect API with an explicit origin — the
    # asyncio.client variant rejects the handshake with HTTP 403 here.
    async with websockets.connect(url, origin="http://localhost:8003") as ws:
        # 1. Start the session.
        start_msg = {
            "action": "start",
            "sector": "customer_support",
            "problem": "we want to automate support tickets",
        }
        await ws.send(json.dumps(start_msg))
        print("> sent start")

        # 2. Read the first reply (ready).
        reply = json.loads(await ws.recv())
        print("<", json.dumps(reply, indent=2))

        # 3. Simulate a spoken answer via TTS, send it as a binary frame.
        spoken = "we handle about ten thousand tickets a month with fifteen agents"
        audio = synthesize(spoken)
        print(f"> sending audio ({len(audio)} bytes)")
        await ws.send(audio)

        # 4. Read the turn reply.
        reply = json.loads(await ws.recv())
        print("<", json.dumps(reply, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8003/ws/interview/voice", help="WebSocket URL (default points at the voice endpoint)")
    args = parser.parse_args()
    import asyncio

    asyncio.run(main(args.url))
