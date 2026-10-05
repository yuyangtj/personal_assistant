from __future__ import annotations

from fastapi.testclient import TestClient


def test_the_console_is_installable_as_an_app(client: TestClient) -> None:
    manifest = client.get("/manifest.webmanifest")
    page = client.get("/ui").text

    assert manifest.headers["content-type"].startswith("application/manifest+json")
    body = manifest.json()
    assert (body["start_url"], body["display"], body["scope"]) == ("/ui#/chats", "standalone", "/")
    sizes = {icon["sizes"] for icon in body["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(icon.get("purpose") == "maskable" for icon in body["icons"])
    for icon in body["icons"]:
        served = client.get(icon["src"])
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"
    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert 'navigator.serviceWorker.register("/sw.js")' in page


def test_the_service_worker_is_served_fresh_and_icons_are_whitelisted(
    client: TestClient,
) -> None:
    worker = client.get("/sw.js")

    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert client.get("/icons/../index.html").status_code == 404
    assert client.get("/icons/secret.png").status_code == 404
