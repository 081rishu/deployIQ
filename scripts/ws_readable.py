"""Readable WS test: connects, starts, sends audio, prints JSON keys only."""

from __future__ import annotations

import asyncio
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Imports resolve from the editable src-layout installation.

import websockets

from llm.tts import synthesize


def _summary(m: dict) -> dict:
    out = {k: v for k, v in m.items() if k != "audio"}
    if "audio" in m:
        out["audio"] = f"<{len(m['audio'])} chars base64>"
    return out


async def main(url: str) -> None:
    async with websockets.connect(url, origin="http://localhost:8004") as ws:
        await ws.send(json.dumps({
            "action": "start",
            "sector": "customer_support",
            "problem": "we want to automate support tickets",
        }))
        ready = json.loads(await ws.recv())
        print("READY:", json.dumps(_summary(ready), indent=2))

        audio = synthesize("we handle about ten thousand tickets a month with fifteen agents")
        print(f"SEND audio bytes={len(audio)}")
        await ws.send(audio)

        turn = json.loads(await ws.recv())
        print("TURN:", json.dumps(_summary(turn), indent=2))


if __name__ == "__main__":
    asyncio.run(main("ws://localhost:8004/ws/interview/voice"))
