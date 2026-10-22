"""极简 WebSocket 客户端（仅用于本地 CDP 截图，避免额外依赖）。"""

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


class WS:
    def __init__(self, url: str):
        assert url.startswith("ws://")
        host_port, _, path = url[5:].partition("/")
        host, _, port = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += self.sock.recv(4096)
        assert b"101" in response.split(b"\r\n")[0], response[:200]
        self._id = 0

    def send(self, method: str, params: dict | None = None) -> int:
        self._id += 1
        payload = json.dumps({"id": self._id, "method": method, "params": params or {}}).encode()
        header = b"\x81"
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)
        return self._id

    def _read_frame(self) -> bytes:
        def recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("closed")
                buf += chunk
            return buf

        first = recv_exact(2)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", recv_exact(8))[0]
        return recv_exact(length)

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        message_id = self.send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = json.loads(self._read_frame().decode())
            if data.get("id") == message_id:
                return data
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def find_page(port: int = 9333) -> str | None:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
        targets = json.load(resp)
    for target in targets:
        if target.get("type") == "page":
            return target.get("webSocketDebuggerUrl")
    return None


def capture(port: int, out_path: Path) -> bool:
    url = find_page(port)
    if not url:
        return False
    ws = WS(url)
    try:
        ws.call("Page.enable")
        result = ws.call("Page.captureScreenshot", {"format": "png"})
        data = result.get("result", {}).get("data")
        if not data:
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(data))
        return True
    finally:
        ws.close()


if __name__ == "__main__":
    print(capture(int(sys.argv[1]) if len(sys.argv) > 1 else 9333,
                  Path(sys.argv[2]) if len(sys.argv) > 2 else Path("shot.png")))
