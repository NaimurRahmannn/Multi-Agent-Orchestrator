import os
import socket
from pathlib import Path
from urllib.request import urlopen

import pytest

from agentorchestra.services.preview_server import serve_site
from agentorchestra.services.workspace import SiteValidationError
from tests.test_workspace_service import write_site


def test_preview_server_serves_loopback_ephemeral_port_and_stops(tmp_path):
    site = tmp_path / "site"
    write_site(site)
    cwd = Path.cwd()

    with serve_site(site) as base_url:
        assert base_url.startswith("http://127.0.0.1:")
        host, port_text = base_url.removeprefix("http://").split(":")
        with urlopen(f"{base_url}/index.html", timeout=2) as response:  # noqa: S310
            assert b"<title>Home</title>" in response.read()
        assert Path.cwd() == cwd

    with socket.socket() as client:
        client.settimeout(1)
        assert client.connect_ex((host, int(port_text))) != 0
    assert Path.cwd() == cwd


def test_preview_server_rejects_invalid_site_without_changing_cwd(tmp_path):
    cwd = Path.cwd()
    with pytest.raises(SiteValidationError), serve_site(tmp_path / "missing"):
        pass
    assert Path.cwd() == cwd


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires privileges.")
def test_preview_server_rejects_symlink_site_root(tmp_path):
    site = tmp_path / "site"
    write_site(site)
    link = tmp_path / "link"
    link.symlink_to(site, target_is_directory=True)
    with pytest.raises(SiteValidationError), serve_site(link):
        pass
