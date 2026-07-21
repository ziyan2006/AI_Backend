from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import connect


def build_sample_snapshot(session_id: str) -> dict[str, Any]:
    return {
        "type": "circuit.snapshot",
        "schema_version": 1,
        "session_id": session_id,
        "topology_revision": 42,
        "slots": [
            {
                "slot": 0,
                "component": "and_gate",
                "ports": [
                    {"id": 8, "role": "input_a"},
                    {"id": 9, "role": "input_b"},
                    {"id": 10, "role": "output"},
                ],
            },
            {
                "slot": 1,
                "component": "led",
                "ports": [
                    {"id": 20, "role": "input"},
                    {"id": 21, "role": "ground"},
                ],
            },
        ],
        "valid_links": [{"from": 2, "to": 8}, {"from": 10, "to": 20}],
        "invalid_links": [],
        "scan": {"count": 135, "stable_count": 4},
    }


def generate_silence_pcm(duration_ms: int, sample_rate_hz: int = 16000) -> bytes:
    sample_count = sample_rate_hz * duration_ms // 1000
    return bytes(sample_count * 2)


async def send_json(websocket: Any, payload: dict[str, Any]) -> None:
    print(f"> {json.dumps(payload, ensure_ascii=False)}")
    await websocket.send(json.dumps(payload, ensure_ascii=False))


async def receive_json(websocket: Any) -> dict[str, Any]:
    raw = await websocket.recv()
    if not isinstance(raw, str):
        raise RuntimeError(f"expected JSON text frame, received {len(raw)} binary bytes")
    payload = json.loads(raw)
    print(f"< {json.dumps(payload, ensure_ascii=False)}")
    return payload


async def run_simulation(url: str, duration_ms: int) -> None:
    session_id = f"sim-{uuid4().hex[:12]}"
    async with connect(url, max_size=2**20) as websocket:
        await send_json(
            websocket,
            {
                "type": "device.hello",
                "protocol_version": 2,
                "device_id": "python-simulator",
                "firmware_version": "sim-0.1.0",
                "capabilities": {
                    "audio": "pcm_s16le_16000_mono",
                    "max_ports": 64,
                    "tools": ["highlight_ports"],
                },
            },
        )
        await receive_json(websocket)

        await send_json(
            websocket,
            {"type": "session.start", "session_id": session_id, "locale": "zh-CN"},
        )
        await receive_json(websocket)

        await send_json(websocket, build_sample_snapshot(session_id))
        await receive_json(websocket)

        await send_json(
            websocket,
            {
                "type": "input_audio.start",
                "session_id": session_id,
                "format": "pcm_s16le",
                "sample_rate_hz": 16000,
                "channels": 1,
            },
        )
        await receive_json(websocket)

        pcm = generate_silence_pcm(duration_ms)
        chunk_size = 3200
        for offset in range(0, len(pcm), chunk_size):
            chunk = pcm[offset : offset + chunk_size]
            await websocket.send(chunk)
            print(f"> <binary {len(chunk)} bytes>")
            await asyncio.sleep(len(chunk) / (16000 * 2))

        await send_json(
            websocket,
            {"type": "input_audio.commit", "session_id": session_id},
        )
        committed = await receive_json(websocket)
        expected_bytes = len(pcm)
        if committed.get("audio_bytes") != expected_bytes:
            raise RuntimeError(
                f"backend counted {committed.get('audio_bytes')} bytes, expected {expected_bytes}"
            )
        print("Simulation completed successfully.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate the TUCO ESP32 WebSocket protocol")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/device")
    parser.add_argument("--duration-ms", type=int, default=500)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.duration_ms <= 0:
        raise SystemExit("--duration-ms must be positive")
    asyncio.run(run_simulation(args.url, args.duration_ms))


if __name__ == "__main__":
    main()
