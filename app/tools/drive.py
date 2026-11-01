"""Read-only Google Drive tool for the chat agent: list a folder, read a file.

Works only with the server's configured Google OAuth credentials (the user's own Drive
access) and never changes anything - no writes, uploads, deletes or shares. A Google Doc
or Sheet is exported as text; other files are returned as their raw text. What it returns
is the file's own text: the agent is told to treat it as data, not instructions.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

#: Google Drive ids: URL-safe, no spaces or quotes, so they are also safe inside a query.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_URL_ID_PATTERNS = [
    re.compile(r"/folders/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/file/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/document/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"/presentation/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})"),
]

DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
#: Google Docs/Sheets have no raw bytes; exporting them as this mime type gives their text.
EXPORTABLE_MIME_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
#: A regular file is only read as text when its own mime type looks like text.
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {"application/json", "application/xml"}

MAX_LISTED = 200
MAX_READ_CHARACTERS = 12_000
MAX_LIST_PAGES = 10

#: How the tool is described to the model.
TOOLS = {
    "drive_read": (
        '{"url_or_id": "<a Drive folder or file URL, or its id>"}',
        "a Drive folder's files and subfolders (name, id, mime type), or a file's text "
        "(a Google Doc or Sheet is exported as text)",
    ),
}


class GoogleDriveError(RuntimeError):
    """A sanitized Drive API failure that never includes credentials or raw response bodies."""


class GoogleDrivePermissionError(GoogleDriveError):
    """The connected Google account has no access to the requested file or folder."""


class ToolError(ValueError):
    """A tool call that can't be carried out; its message goes back to the model."""


def extract_drive_id(value: str) -> str:
    """A Drive file or folder id, from a Drive/Docs/Sheets URL or a plain id."""
    text = (value or "").strip()
    if not text:
        raise ValueError("say which Drive folder or file (a link or id)")
    if text.startswith("http://") or text.startswith("https://"):
        for pattern in _URL_ID_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
        raise ValueError("couldn't find a Drive file or folder id in that URL")
    if not _ID_PATTERN.fullmatch(text):
        raise ValueError("that doesn't look like a Drive URL or id")
    return text


class GoogleDriveClient:
    """Minimal Drive v3 client, authenticated as the user through a refresh token."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        base_url: str = "https://www.googleapis.com",
        token_url: str = "https://oauth2.googleapis.com/token",
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not (client_id and client_secret and refresh_token):
            raise ValueError("A Google Drive client id, secret and refresh token are required")
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._token_url = token_url
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def close(self) -> None:
        self._client.close()

    def get_metadata(self, file_id: str) -> dict[str, Any]:
        response = self._get(
            f"/drive/v3/files/{quote(file_id)}", params={"fields": "id,name,mimeType"}
        )
        body = _json_object(response, "Google Drive returned an unexpected file response")
        if not isinstance(body.get("name"), str) or not isinstance(body.get("mimeType"), str):
            raise GoogleDriveError("Google Drive returned an unexpected file response")
        return body

    def list_folder(self, folder_id: str) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        page_token: str | None = None
        for _page in range(MAX_LIST_PAGES):
            params = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": "nextPageToken, files(id, name, mimeType)",
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._get("/drive/v3/files", params=params)
            body = _json_object(response, "Google Drive returned an unexpected folder listing")
            files = body.get("files")
            if not isinstance(files, list):
                raise GoogleDriveError("Google Drive returned an unexpected folder listing")
            for entry in files:
                if (
                    isinstance(entry, dict)
                    and isinstance(entry.get("id"), str)
                    and isinstance(entry.get("name"), str)
                    and isinstance(entry.get("mimeType"), str)
                ):
                    entries.append(entry)
            page_token = body.get("nextPageToken")
            if not isinstance(page_token, str) or not page_token:
                break
        return entries

    def export_text(self, file_id: str, export_mime_type: str) -> str:
        response = self._get(
            f"/drive/v3/files/{quote(file_id)}/export", params={"mimeType": export_mime_type}
        )
        return response.text

    def download_text(self, file_id: str) -> str:
        response = self._get(f"/drive/v3/files/{quote(file_id)}", params={"alt": "media"})
        return response.text

    def _access_token(self) -> str:
        try:
            response = self._client.post(
                self._token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as error:
            raise GoogleDriveError(
                f"Google token request failed: {type(error).__name__}"
            ) from error
        if response.status_code not in range(200, 300):
            raise GoogleDriveError("Google rejected the Drive credentials")
        body = _json_object(response, "Google returned an unexpected token response")
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise GoogleDriveError("Google returned an unexpected token response")
        return token

    def _get(self, path: str, *, params: dict[str, Any]) -> httpx.Response:
        token = self._access_token()
        try:
            response = self._client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as error:
            raise GoogleDriveError(
                f"Google Drive request failed: {type(error).__name__}"
            ) from error
        if response.status_code in (403, 404):
            raise GoogleDrivePermissionError(
                "the connected Google account has no access to this file or folder (it may "
                "be private, restricted, or shared with a different account)"
            )
        if response.status_code not in range(200, 300):
            raise GoogleDriveError(f"Google Drive returned HTTP {response.status_code}")
        return response


def _json_object(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise GoogleDriveError(message) from error
    if not isinstance(body, dict):
        raise GoogleDriveError(message)
    return body


class GoogleDriveReader:
    def __init__(self, client: GoogleDriveClient):
        self.client = client

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        """The tool's result as text, or what went wrong (never raises)."""
        try:
            if name not in TOOLS:
                raise ToolError(f"there is no tool called {name!r}")
            value = arguments.get("url_or_id")
            if not isinstance(value, str) or not value.strip():
                raise ToolError("say which Drive folder or file (a link or id)")
            try:
                file_id = extract_drive_id(value)
            except ValueError as error:
                raise ToolError(str(error)) from error
            return self._read(file_id)
        except ToolError as error:
            return f"error: {error}"
        except GoogleDriveError as error:
            logger.warning("Drive tool failed: %s", error)
            return f"error: {error}"

    def _read(self, file_id: str) -> str:
        metadata = self.client.get_metadata(file_id)
        name, mime_type = metadata["name"], metadata["mimeType"]
        if mime_type == DRIVE_FOLDER_MIME_TYPE:
            return self._list(file_id, name)
        export_mime_type = EXPORTABLE_MIME_TYPES.get(mime_type)
        if export_mime_type:
            text = self.client.export_text(file_id, export_mime_type)
            return f"{name} ({mime_type}):\n{text[:MAX_READ_CHARACTERS]}"
        if not mime_type.startswith(TEXT_MIME_PREFIXES) and mime_type not in TEXT_MIME_TYPES:
            return (
                f"can't read {name}: its type ({mime_type}) isn't text; only plain text "
                "files and Google Docs/Sheets/Slides are supported"
            )
        text = self.client.download_text(file_id)
        return f"{name} ({mime_type}):\n{text[:MAX_READ_CHARACTERS]}"

    def _list(self, folder_id: str, name: str) -> str:
        entries = self.client.list_folder(folder_id)
        if not entries:
            return f"{name}: empty folder"
        shown = entries[:MAX_LISTED]
        lines = [f"{name}:"]
        lines += [f"{e['name']}  (id: {e['id']}, {e['mimeType']})" for e in shown]
        more = len(entries) - len(shown)
        if more > 0:
            lines.append(f"... and {more} more")
        return "\n".join(lines)
