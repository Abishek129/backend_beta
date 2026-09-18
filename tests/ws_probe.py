#!/usr/bin/env python
"""Print the frames a backend_beta socket sends — the manual smoke for a screen.

    python tests/ws_probe.py ws://127.0.0.1:8891/ws/mfm/1000001/overview/
    python tests/ws_probe.py ws://127.0.0.1:8891/ws/mfm/abc/overview/ --frames 1

Prints one line per frame: index, `type`, and the trimmed top-level keys, so a
snapshot/tick/error shape is readable without dumping a full payload. Exits 0 if
at least one frame arrived, 1 on timeout with none, 2 if the connect failed.
"""
import argparse
import asyncio
import json
import sys

import websockets

TRIM = 110


def _summary(value):
    """One short line describing a top-level value."""
    if isinstance(value, dict):
        return f'{{{len(value)} keys: ' + ', '.join(list(value)[:8]) + '}'
    if isinstance(value, list):
        return f'[{len(value)}]'
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= 40 else text[:37] + '...'


async def probe(url, frames, timeout):
    seen = 0
    try:
        conn = await asyncio.wait_for(websockets.connect(url), timeout)
    except Exception as exc:
        print(f'connect failed: {type(exc).__name__}: {exc}')
        return 2
    print(f'connected: {url}')
    async with conn as ws:
        while seen < frames:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except asyncio.TimeoutError:
                print(f'-- timeout after {timeout}s ({seen} frame(s))')
                break
            except websockets.ConnectionClosed as exc:
                print(f'-- closed: code={exc.code} reason={exc.reason!r}')
                break
            seen += 1
            try:
                msg = json.loads(raw)
            except ValueError:
                print(f'[{seen}] <non-json {len(raw)} bytes> {raw[:TRIM]}')
                continue
            if not isinstance(msg, dict):
                print(f'[{seen}] <{type(msg).__name__}> {str(msg)[:TRIM]}')
                continue
            head = f"[{seen}] type={msg.get('type')!r}"
            body = '  '.join(f'{k}={_summary(v)}' for k, v in msg.items() if k != 'type')
            print(f'{head}  {body}'[:400])
    return 0 if seen else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('url', help='ws://host:port/ws/mfm/<id>/<screen>/')
    ap.add_argument('--frames', type=int, default=3, help='stop after N frames (default 3)')
    ap.add_argument('--timeout', type=float, default=10.0, help='seconds per frame (default 10)')
    args = ap.parse_args()
    sys.exit(asyncio.run(probe(args.url, args.frames, args.timeout)))


if __name__ == '__main__':
    main()
