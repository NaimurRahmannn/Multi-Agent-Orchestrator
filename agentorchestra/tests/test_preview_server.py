import os
import socket
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from scripts.feasibility._preview_server import preview_server


def test_preview_server_serves_temp_index(tmp_path):
    (tmp_path / "index.html").write_text("hello preview", encoding="utf-8")

    with preview_server(tmp_path) as base_url:
        with urlopen(f"{base_url}/index.html", timeout=5) as response:
            body = response.read().decode("utf-8")

    assert body == "hello preview"


def test_preview_server_uses_local_ephemeral_port(tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")

    with preview_server(tmp_path) as base_url:
        assert base_url.startswith("http://127.0.0.1:")
        port = int(base_url.rsplit(":", 1)[1])

    assert port > 0


def test_preview_server_rejects_invalid_roots(tmp_path):
    with pytest.raises(ValueError):
        with preview_server(tmp_path / "missing"):
            pass

    file_root = tmp_path / "file.txt"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError):
        with preview_server(file_root):
            pass


def test_preview_server_releases_after_exit(tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")

    with preview_server(tmp_path) as base_url:
        port = int(base_url.rsplit(":", 1)[1])

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        assert sock.connect_ex(("127.0.0.1", port)) != 0


def test_preview_server_does_not_change_cwd(tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    original_cwd = Path.cwd()

    with preview_server(tmp_path):
        assert Path.cwd() == original_cwd

    assert Path.cwd() == original_cwd
