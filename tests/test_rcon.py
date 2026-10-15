import socket
import struct
import threading

import pytest

from pz_restarter.game import parse_players, sanitize_message
from pz_restarter.rcon import RconClient, RconError, encode_packet, read_packet


def test_encode_packet():
    packet = encode_packet(7, 2, "players")
    assert packet == struct.pack("<iii", 4 + 4 + 7 + 2, 7, 2) + b"players\x00\x00"


def _serve(responses_for_command, password="secret", split=False):
    """Fake PZ RCON server on an ephemeral port; returns (port, received_commands)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received = []

    def run():
        conn, _ = server.accept()
        with conn:
            rid, typ, body = read_packet(conn)
            assert typ == 3
            # PZ sends an empty RESPONSE_VALUE before the AUTH_RESPONSE.
            conn.sendall(encode_packet(rid, 0, ""))
            conn.sendall(encode_packet(rid if body == password else -1, 2, ""))
            if body != password:
                return
            rid, typ, body = read_packet(conn)
            received.append(body)
            reply = responses_for_command(body)
            if split:
                for part in (reply[:5], reply[5:]):
                    conn.sendall(encode_packet(rid, 0, part))
            else:
                conn.sendall(encode_packet(rid, 0, reply))
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return server.getsockname()[1], received


@pytest.mark.parametrize("split", [False, True])
def test_command_roundtrip(split):
    port, received = _serve(lambda _: "Players connected (0): \n", split=split)
    out = RconClient("127.0.0.1", port, "secret", timeout=2).command("players")
    assert out == "Players connected (0): \n"
    assert received == ["players"]


def test_bad_password():
    port, _ = _serve(lambda _: "", password="secret")
    with pytest.raises(RconError, match="authentication failed"):
        RconClient("127.0.0.1", port, "wrong", timeout=2).command("players")


def test_connection_refused():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(RconError):
        RconClient("127.0.0.1", port, "x", timeout=1).command("players")


def test_parse_players_empty():
    # Exact reply captured from the live B42 server.
    assert parse_players("Players connected (0): \n") == []


def test_parse_players_some():
    assert parse_players("Players connected (2): \n-alice\n-bob the builder\n") == [
        "alice",
        "bob the builder",
    ]


def test_parse_players_count_wins():
    assert len(parse_players("Players connected (2): \n-alice\n")) == 2


def test_parse_players_garbage_is_error():
    with pytest.raises(RconError):
        parse_players("Unknown command")


def test_sanitize_message():
    assert sanitize_message('a "b"\\\nc') == "a bc"
