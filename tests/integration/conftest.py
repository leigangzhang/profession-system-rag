from __future__ import annotations

import socket

import pytest


def _can_bind_localhost() -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.close()
        return True
    except (PermissionError, OSError):
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("requires_milvus_local") and not _can_bind_localhost():
        pytest.skip(
            "Local Milvus Lite cannot bind 127.0.0.1 in this sandbox. "
            "Run outside the sandbox or provide a remote Milvus URI."
        )
