"""
Tests must not reach the network

Six catalogue tests passed for the wrong reason in this fork. `AppleDBProducts.products`
is a cached property that HEADs every installer link, and the tests read it after
the mock had come down — so they did real HTTP requests to `swcdn.apple.com` and
passed because the machine happened to be online. They would have failed on a
build machine, and worse, they were proving something other than what they said.

So: importing this module puts the network out of reach for the test process.

`socket.create_connection` and `socket.socket.connect` refuse any address that is
not loopback, which is where every Python HTTP stack ends up — `http.client`,
`urllib3`, and therefore `requests`. Loopback stays open, because a local server
is not the internet and because the app has one. Nothing in the suite needs an
escape hatch today; `allow_network()` exists so that a future test can say why
out loud rather than silently depending on connectivity.

Not covered: a child process started by a test has its own sockets; the patch is
process-local. Nothing in the suite shells out to something that fetches.
"""

import socket
import unittest

from contextlib import contextmanager


BLOCKED_REASON = (
    "opened a network connection. A test that reaches the real network passes or fails with "
    "the machine rather than with the code: stub the call instead — the catalogue tests patch "
    "NetworkUtilities.get and NetworkUtilities.validate_link — or state the intent with "
    "allow_network()."
)

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_create_connection = socket.create_connection

LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback(address: object) -> bool:
    if isinstance(address, str):
        return address.startswith("/") or address in LOOPBACK_HOSTS
    if isinstance(address, (tuple, list)) and address:
        host = str(address[0])
        return host in LOOPBACK_HOSTS or host.startswith("127.")
    return False


def _refuse(address: object) -> None:
    raise AssertionError(f"{address!r} {BLOCKED_REASON}")


def _blocked_connect(self: socket.socket, address: object) -> None:
    if _is_loopback(address):
        return _original_connect(self, address)
    _refuse(address)


def _blocked_connect_ex(self: socket.socket, address: object) -> int:
    if _is_loopback(address):
        return _original_connect_ex(self, address)
    _refuse(address)


def _blocked_create_connection(address: object, *args: object, **kwargs: object) -> socket.socket:
    if _is_loopback(address):
        return _original_create_connection(address, *args, **kwargs)
    _refuse(address)


@contextmanager
def allow_network():
    """Let one block of a test use the real network, having said so out loud"""
    socket.socket.connect = _original_connect
    socket.socket.connect_ex = _original_connect_ex
    socket.create_connection = _original_create_connection
    try:
        yield
    finally:
        socket.socket.connect = _blocked_connect
        socket.socket.connect_ex = _blocked_connect_ex
        socket.create_connection = _blocked_create_connection


socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect_ex
socket.create_connection = _blocked_create_connection


def _chain_text(error: BaseException) -> str:
    """Every message in an exception's cause chain, for stacks that wrap the failure"""
    seen: list[str] = []
    while error is not None:
        seen.append(str(error))
        error = error.__cause__ or error.__context__
    return " | ".join(seen)


class TheNetworkIsOutOfReach(unittest.TestCase):
    """The guard itself, so it cannot be quietly removed"""

    def test_a_connection_handler_refuses_an_address(self) -> None:
        with self.assertRaises(AssertionError) as raised:
            socket.create_connection(("203.0.113.1", 80), timeout=1)
        self.assertIn("opened a network connection", str(raised.exception))

    def test_a_socket_refuses_to_connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(AssertionError):
                sock.connect(("203.0.113.1", 80))
        finally:
            sock.close()

    def test_requests_cannot_reach_the_network(self) -> None:
        """The stack the patcher's own HTTP calls go through"""

        import requests
        import warnings

        with warnings.catch_warnings():
            # urllib3 has already made the socket object when the guard refuses the
            # connect, and the exception leaves it unclosed behind us.
            warnings.simplefilter("ignore", ResourceWarning)
            with self.assertRaises(Exception) as raised:
                requests.Session().get("http://203.0.113.1/", timeout=1)
        self.assertIn("opened a network connection", _chain_text(raised.exception))

    def test_loopback_is_not_treated_as_the_internet(self) -> None:
        """A local server is allowed; port 1 has none listening"""

        with self.assertRaises(OSError) as raised:
            socket.create_connection(("127.0.0.1", 1), timeout=1)
        self.assertNotIsInstance(raised.exception, AssertionError)

    def test_the_escape_hatch_reopens_the_network(self) -> None:
        with allow_network():
            self.assertIs(socket.socket.connect, _original_connect)
        self.assertIs(socket.socket.connect, _blocked_connect)


if __name__ == "__main__":
    unittest.main()
