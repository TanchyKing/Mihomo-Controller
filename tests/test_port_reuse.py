"""Real Linux sockets: stopping a core must not leave false port conflicts."""
import errno
import socket
import pytest
from minimal_mihomo.network import preflight
from minimal_mihomo.storage import ControllerError


def config(port):
    with socket.socket() as spare:
        spare.bind(('127.0.0.1', 0))
        api = spare.getsockname()[1]
    return {'tun': {'enable': False}, 'mixed-port': port,
            'external-controller': f'127.0.0.1:{api}'}


def closed_server_port():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        server.listen()
        port = server.getsockname()[1]
        with socket.create_connection(('127.0.0.1', port)) as client:
            accepted, _ = server.accept()
            accepted.close()  # Server closes first, leaving server-side TIME_WAIT.
            assert client.recv(1) == b''
    return port


def test_time_wait_does_not_block_replacement_server():
    port = closed_server_port()
    # Demonstrate the old probe fails, then verify the replacement probe succeeds.
    with socket.socket() as old_probe:
        with pytest.raises(OSError) as error:
            old_probe.bind(('127.0.0.1', port))
        assert error.value.errno == errno.EADDRINUSE
    preflight(config(port))
    # The probe closes its sockets, so a following server can still start.
    with socket.socket() as replacement:
        replacement.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        replacement.bind(('127.0.0.1', port))
        replacement.listen()


def test_reuse_enabled_live_listener_still_blocks():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        server.listen()
        with pytest.raises(ControllerError, match='already used'):
            preflight(config(server.getsockname()[1]))


def test_udp_listener_still_blocks():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        with pytest.raises(ControllerError, match='UDP'):
            preflight(config(server.getsockname()[1]))
