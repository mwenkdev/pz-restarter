"""Minimal Source RCON client, as spoken by the Project Zomboid dedicated server."""

from __future__ import annotations

import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_EXECCOMMAND = 2
SERVERDATA_RESPONSE_VALUE = 0

_AUTH_ID = 1
_COMMAND_ID = 2
# Packets after the first one of a split response arrive quickly; stop waiting after this.
_CONTINUATION_TIMEOUT = 0.3
_MAX_PACKET = 1024 * 1024


class RconError(Exception):
    """RCON connection, protocol or authentication failure."""


def encode_packet(request_id: int, packet_type: int, body: str) -> bytes:
    payload = struct.pack("<ii", request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(payload)) + payload


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise RconError("connection closed by server")
        data.extend(chunk)
    return bytes(data)


def read_packet(sock: socket.socket) -> tuple[int, int, str]:
    (size,) = struct.unpack("<i", _recv_exact(sock, 4))
    if size < 10 or size > _MAX_PACKET:
        raise RconError(f"invalid packet size {size}")
    payload = _recv_exact(sock, size)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self._password = password
        self.timeout = timeout

    def command(self, command: str) -> str:
        """Open a connection, authenticate, run one command and return its output."""
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                self._authenticate(sock)
                sock.sendall(encode_packet(_COMMAND_ID, SERVERDATA_EXECCOMMAND, command))
                return self._read_response(sock)
        except OSError as exc:
            raise RconError(f"{self.host}:{self.port}: {exc}") from exc

    def _authenticate(self, sock: socket.socket) -> None:
        sock.sendall(encode_packet(_AUTH_ID, SERVERDATA_AUTH, self._password))
        # The server may send an empty RESPONSE_VALUE before the AUTH_RESPONSE.
        for _ in range(3):
            request_id, packet_type, _ = read_packet(sock)
            if packet_type == SERVERDATA_AUTH_RESPONSE:
                if request_id == -1:
                    raise RconError("authentication failed")
                return
        raise RconError("no authentication response")

    def _read_response(self, sock: socket.socket) -> str:
        request_id, _, body = read_packet(sock)
        if request_id != _COMMAND_ID:
            raise RconError(f"unexpected response id {request_id}")
        parts = [body]
        sock.settimeout(_CONTINUATION_TIMEOUT)
        try:
            while True:
                request_id, _, body = read_packet(sock)
                if request_id != _COMMAND_ID:
                    break
                parts.append(body)
        except (TimeoutError, RconError):
            pass
        return "".join(parts)
