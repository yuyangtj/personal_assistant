"""The web console and the files that make it installable as an app."""

from __future__ import annotations

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
    "drawer.js": "text/javascript",
    "items.js": "text/javascript",
    "phone.js": "text/javascript",
    "events.js": "text/javascript",
    "main.js": "text/javascript",
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


@router.get("/", include_in_schema=False)
def web_console_root(request: Request) -> RedirectResponse:
    # Keep the query: push notifications link to /?task=<id>.
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(url=f"/ui{query}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
