from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from agentorchestra.services.workspace import validate_site_structure


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def serve_site(site_root: Path) -> Iterator[str]:
    """Serve one validated site on loopback and an ephemeral port without changing cwd."""
    validate_site_structure(site_root)
    root = site_root.resolve(strict=True)
    handler = partial(_QuietStaticHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, name="agentorchestra-preview", daemon=True)
    try:
        thread.start()
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
