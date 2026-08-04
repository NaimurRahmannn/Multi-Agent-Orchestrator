from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def preview_server(document_root: Path) -> Iterator[str]:
    """Serve a directory on 127.0.0.1 using an ephemeral port."""
    root = document_root.resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Document root must be an existing directory: {document_root}")

    handler = lambda *args, **kwargs: QuietStaticHandler(*args, directory=str(root), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, name="agentorchestra-preview", daemon=True)
    try:
        thread.start()
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
