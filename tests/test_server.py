import json
import threading
import urllib.request

from vh import server


def _req(port, path, body=None, origin=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}") if "json" in r.headers.get("Content-Type", "") else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_api_and_security(monkeypatch):
    monkeypatch.setattr(server.moments, "trending", lambda *a, **k: [])
    srv, port = server.make_server(18765)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        code, st = _req(port, "/api/state")
        assert code == 200 and st["moments"]["anime"] >= 30 and "cinema" in st["templates"]
        code, html = _req(port, "/")
        assert code == 200 and b"VideoHook" in html
        code, _ = _req(port, "/media/../../etc/passwd")
        assert code in (403, 404)
        code, res = _req(port, "/api/settings", {"brand_handle": "@test"}, origin="https://evil.example")
        assert code == 403
        code, res = _req(port, "/api/settings", {"brand_handle": "@test", "gemini_api_key": "AIzaSECRET1234"})
        assert code == 200 and res["brand_handle"] == "@test"
        assert "SECRET" not in json.dumps(res) and res["gemini_api_key_set"]
    finally:
        srv.shutdown()
