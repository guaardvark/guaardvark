import threading
import time

import pytest

try:
    import requests
    from werkzeug.serving import make_server

    from backend.app import app
except Exception:
    pytest.skip("Flask or requests not available", allow_module_level=True)


def test_health_endpoint_e2e():
    server = make_server("127.0.0.1", 5099, app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    time.sleep(1)
    try:
        resp = requests.get("http://127.0.0.1:5099/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
    finally:
        server.shutdown()
        thread.join()
