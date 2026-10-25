"""The web console and the files that make it installable as an app."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

router = APIRouter()


WEB_INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


@router.get("/ui", include_in_schema=False)
def web_console() -> FileResponse:
    """A local console for exercising the chat and task flow from a browser."""
    if not WEB_INDEX.is_file():
        raise HTTPException(status_code=404, detail="Web console is not installed")
    # Revalidated on every load, so browsers and the app never keep an old console.
    return FileResponse(WEB_INDEX, media_type="text/html", headers={"Cache-Control": "no-cache"})


WEB_DIRECTORY = WEB_INDEX.parent
#: The console's stylesheet and scripts, loaded by the page in this order.
WEB_STATIC = {
    "console.css": "text/css",
    "state.js": "text/javascript",
    "transcript.js": "text/javascript",
    "charts.js": "text/javascript",
    "drawer.js": "text/javascript",
    "items.js": "text/javascript",
    "phone.js": "text/javascript",
    "passkeys.js": "text/javascript",
    "memories.js": "text/javascript",
    "notes.js": "text/javascript",
    "stats.js": "text/javascript",
    "events.js": "text/javascript",
    "main.js": "text/javascript",
    # Loaded only when the avatar is switched on (events.js).
    "avatar.js": "text/javascript",
}


@router.get("/ui/static/{name}", include_in_schema=False)
def web_static(name: str) -> FileResponse:
    """The console's code, behind the same login as the page and revalidated on every load."""
    if name not in WEB_STATIC:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        WEB_DIRECTORY / "static" / name,
        media_type=WEB_STATIC[name],
        headers={"Cache-Control": "no-cache"},
    )


#: Served without the console login: the installed app needs them before signing in, and
#: Android builds the installed app by fetching the manifest and icons from Google's side.
WEB_ICONS = {
    "icon-192.png",
    "icon-512.png",
    "maskable-512.png",
    "apple-touch-icon.png",
    "favicon-48.png",
}


WEB_MANIFEST = {
    "id": "/ui",
    "name": "Personal Assistant",
    "short_name": "Assistant",
    "description": "Talk to your assistant, follow its work, and approve what it does.",
    "start_url": "/ui#/chats",
    "scope": "/",
    "display": "standalone",
    "background_color": "#071116",
    "theme_color": "#0d8a7e",
    "icons": [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {
            "src": "/icons/maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
    "shortcuts": [
        {"name": "Chats", "url": "/ui#/chats"},
        {"name": "Work items", "url": "/ui#/items"},
    ],
}


@router.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> JSONResponse:
    return JSONResponse(WEB_MANIFEST, media_type="application/manifest+json")


@router.get("/sw.js", include_in_schema=False)
def web_service_worker() -> FileResponse:
    # Never cached by the browser, so a new service worker is picked up on the next visit.
    return FileResponse(
        WEB_DIRECTORY / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/icons/{name}", include_in_schema=False)
def web_icon(name: str) -> FileResponse:
    if name not in WEB_ICONS:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        WEB_DIRECTORY / "icons" / name,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/.well-known/assetlinks.json", include_in_schema=False)
def android_app_links() -> FileResponse:
    """Lets the Android app open this site's links (push notifications) directly.

    Android verifies it when the app is installed: the app with this package name and
    signing key may handle https links to this domain.
    """
    return FileResponse(WEB_DIRECTORY / "assetlinks.json", media_type="application/json")


@router.get("/", include_in_schema=False)
def web_console_root(request: Request) -> RedirectResponse:
    # Keep the query: push notifications link to /?task=<id>.
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/ui{query}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


def app_signing_fingerprints() -> list[str]:
    """The Android app's signing certificates, as published for App Links."""
    statements = json.loads((WEB_DIRECTORY / "assetlinks.json").read_text())
    return [
        fingerprint
        for statement in statements
        for fingerprint in statement["target"].get("sha256_cert_fingerprints", [])
    ]


#: What the Live2D avatar's files may be; anything else is refused.
AVATAR_TYPES = {
    ".js": "text/javascript",
    ".json": "application/json",
    ".png": "image/png",
    ".moc3": "application/octet-stream",
    ".frag": "text/plain",
    ".vert": "text/plain",
}


@router.get("/ui/avatar-assets/{path:path}", include_in_schema=False)
def avatar_assets(path: str, request: Request) -> FileResponse:
    """The Live2D avatar (model, textures, runtime), from a folder kept outside the repo.

    Behind the same login as the console. Unset or missing: 404, and the page shows its
    own drawn avatar instead.
    """
    root = request.app.state.settings.avatar_assets_dir
    suffix = Path(path).suffix.lower()
    if root is None or suffix not in AVATAR_TYPES or ".." in Path(path).parts:
        raise HTTPException(status_code=404, detail="Not found")
    base = Path(root).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    # The manifest says what to load, so it is checked on every load; the rest is big and
    # only changes when the model is rebuilt.
    cache = "no-cache" if path == "manifest.json" else "public, max-age=604800"
    return FileResponse(target, media_type=AVATAR_TYPES[suffix], headers={"Cache-Control": cache})
