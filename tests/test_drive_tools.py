from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.abilities import AbilityRegistry
from app.assistant.toolbox import Toolbox
from app.assistant.tools import drive_tools
from app.config import Settings
from app.tools.drive import (
    DRIVE_FOLDER_MIME_TYPE,
    GoogleDriveClient,
    GoogleDriveError,
    GoogleDrivePermissionError,
    GoogleDriveReader,
    extract_drive_id,
)

TOKEN_RESPONSE = {"access_token": "token-xyz", "expires_in": 3600}
DOC_ID = "1AbCdEfGhIjKlMnOpQrSt"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (DOC_ID, DOC_ID),
        (f"  {DOC_ID}  ", DOC_ID),
        (f"https://drive.google.com/drive/u/0/folders/{DOC_ID}", DOC_ID),
        (f"https://drive.google.com/drive/folders/{DOC_ID}?usp=sharing", DOC_ID),
        (f"https://drive.google.com/file/d/{DOC_ID}/view?usp=sharing", DOC_ID),
        (f"https://docs.google.com/document/d/{DOC_ID}/edit", DOC_ID),
        (f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit#gid=0", DOC_ID),
        (f"https://drive.google.com/open?id={DOC_ID}", DOC_ID),
    ],
)
def test_extract_drive_id_from_urls_and_plain_ids(value: str, expected: str) -> None:
    assert extract_drive_id(value) == expected


@pytest.mark.parametrize(
    "value", ["", "   ", "short", "has a space", "https://example.com/nothing-drive-shaped"]
)
def test_extract_drive_id_rejects_bad_input(value: str) -> None:
    with pytest.raises(ValueError):
        extract_drive_id(value)


def test_google_drive_client_requires_full_credentials() -> None:
    with pytest.raises(ValueError, match="client id, secret and refresh token"):
        GoogleDriveClient(client_id="i", client_secret="", refresh_token="r")


def test_fetches_an_access_token_with_the_refresh_token_and_sends_it_as_bearer() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            body = parse_qs(request.content.decode())
            assert body == {
                "client_id": ["client-id"],
                "client_secret": ["client-secret"],
                "refresh_token": ["refresh-token"],
                "grant_type": ["refresh_token"],
            }
            return httpx.Response(200, json=TOKEN_RESPONSE)
        assert request.headers["authorization"] == "Bearer token-xyz"
        return httpx.Response(200, json={"id": DOC_ID, "name": "Plan", "mimeType": "text/plain"})

    client = GoogleDriveClient(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        metadata = client.get_metadata(DOC_ID)
    finally:
        client.close()

    assert metadata == {"id": DOC_ID, "name": "Plan", "mimeType": "text/plain"}
    assert requests[0].url.host == "oauth2.googleapis.com"


def _client(handler) -> GoogleDriveClient:
    return GoogleDriveClient(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        transport=httpx.MockTransport(handler),
    )


def _token_or(other):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json=TOKEN_RESPONSE)
        return other(request)

    return handler


def test_listing_a_folder_paginates_and_returns_names_ids_and_mime_types() -> None:
    pages = {
        None: httpx.Response(
            200,
            json={
                "files": [{"id": "f1", "name": "plan.docx", "mimeType": "application/vnd.a"}],
                "nextPageToken": "p2",
            },
        ),
        "p2": httpx.Response(
            200,
            json={"files": [{"id": "f2", "name": "budget.xlsx", "mimeType": "application/vnd.b"}]},
        ),
    }

    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files/folder0001xx":
            return httpx.Response(
                200,
                json={"id": "folder0001xx", "name": "Project", "mimeType": DRIVE_FOLDER_MIME_TYPE},
            )
        assert request.url.path == "/drive/v3/files"
        assert request.url.params["q"] == "'folder0001xx' in parents and trashed = false"
        return pages[request.url.params.get("pageToken")]

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run("drive_read", {"url_or_id": "folder0001xx"})

    assert result == (
        "Project:\nplan.docx  (id: f1, application/vnd.a)\nbudget.xlsx  (id: f2, application/vnd.b)"
    )


def test_an_empty_folder_says_so() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files/folder0001xx":
            return httpx.Response(
                200,
                json={"id": "folder0001xx", "name": "Empty", "mimeType": DRIVE_FOLDER_MIME_TYPE},
            )
        return httpx.Response(200, json={"files": []})

    reader = GoogleDriveReader(_client(_token_or(api)))
    assert reader.run("drive_read", {"url_or_id": "folder0001xx"}) == "Empty: empty folder"


def test_a_google_doc_is_exported_as_text() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/drive/v3/files/{DOC_ID}":
            return httpx.Response(
                200,
                json={
                    "id": DOC_ID,
                    "name": "Project Plan",
                    "mimeType": "application/vnd.google-apps.document",
                },
            )
        assert request.url.path == f"/drive/v3/files/{DOC_ID}/export"
        assert request.url.params["mimeType"] == "text/plain"
        return httpx.Response(200, text="Milestones:\n1. Ship it\n")

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run(
        "drive_read", {"url_or_id": f"https://docs.google.com/document/d/{DOC_ID}/edit"}
    )

    assert result == (
        "Project Plan (application/vnd.google-apps.document):\nMilestones:\n1. Ship it\n"
    )


def test_a_google_sheet_is_exported_as_csv_text() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/drive/v3/files/{DOC_ID}":
            return httpx.Response(
                200,
                json={
                    "id": DOC_ID,
                    "name": "Budget",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                },
            )
        assert request.url.path == f"/drive/v3/files/{DOC_ID}/export"
        assert request.url.params["mimeType"] == "text/csv"
        return httpx.Response(200, text="item,cost\nrent,1000\n")

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run("drive_read", {"url_or_id": DOC_ID})

    assert result == "Budget (application/vnd.google-apps.spreadsheet):\nitem,cost\nrent,1000\n"


def test_a_regular_text_file_returns_its_content() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            assert request.url.path == f"/drive/v3/files/{DOC_ID}"
            return httpx.Response(200, text="print('hi')\n")
        return httpx.Response(
            200, json={"id": DOC_ID, "name": "script.py", "mimeType": "text/x-python"}
        )

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run("drive_read", {"url_or_id": DOC_ID})

    assert result == "script.py (text/x-python):\nprint('hi')\n"


def test_a_non_text_file_is_refused_without_downloading_it() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("alt") == "media":
            raise AssertionError("a binary file should never be downloaded as text")
        return httpx.Response(
            200, json={"id": DOC_ID, "name": "photo.png", "mimeType": "image/png"}
        )

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run("drive_read", {"url_or_id": DOC_ID})

    assert result == (
        "can't read photo.png: its type (image/png) isn't text; only plain text files and "
        "Google Docs/Sheets/Slides are supported"
    )


@pytest.mark.parametrize("status", [403, 404])
def test_restricted_or_missing_files_return_a_clear_message_instead_of_raising(status) -> None:
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    reader = GoogleDriveReader(_client(_token_or(api)))
    result = reader.run("drive_read", {"url_or_id": DOC_ID})

    assert result.startswith("error: ")
    assert "no access" in result


def test_the_permission_error_is_raised_by_the_client_too() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _client(_token_or(api))
    try:
        with pytest.raises(GoogleDrivePermissionError):
            client.get_metadata(DOC_ID)
    finally:
        client.close()


def test_an_unexpected_server_error_is_sanitized_and_does_not_expose_credentials() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="refresh-token leaked here by a bad server")

    client = GoogleDriveClient(
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        transport=httpx.MockTransport(_token_or(api)),
    )
    try:
        with pytest.raises(GoogleDriveError) as captured:
            client.get_metadata(DOC_ID)
    finally:
        client.close()

    assert str(captured.value) == "Google Drive returned HTTP 500"
    assert "refresh-token" not in str(captured.value)
    assert "client-secret" not in str(captured.value)


def test_a_bad_url_or_id_is_an_error_and_never_calls_the_api() -> None:
    def api(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not call the Drive API for an invalid id")

    reader = GoogleDriveReader(_client(_token_or(api)))

    assert reader.run("drive_read", {"url_or_id": "nope"}) == (
        "error: that doesn't look like a Drive URL or id"
    )
    assert reader.run("drive_read", {}) == ("error: say which Drive folder or file (a link or id)")


def test_an_unknown_tool_name_is_an_error() -> None:
    reader = GoogleDriveReader(_client(_token_or(lambda request: httpx.Response(200))))

    assert reader.run("drive_write", {"url_or_id": DOC_ID}) == (
        "error: there is no tool called 'drive_write'"
    )


class FakeReader:
    def run(self, name: str, arguments: dict) -> str:
        return f"ran {name} with {arguments}"


def test_drive_tools_registers_one_read_only_tool() -> None:
    tools = drive_tools(FakeReader())

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "drive_read"
    assert tool.ability == "google-drive"
    assert tool.changes is False

    text = Toolbox(tools).describe()
    assert "drive_read" in text and "url_or_id" in text


def test_google_drive_ability_requires_configured_credentials() -> None:
    registry = AbilityRegistry.from_directory("abilities")
    bare = {ability.id for ability in registry.available(Settings())}
    full = {
        ability.id
        for ability in registry.available(
            Settings(
                google_drive_client_id="i",
                google_drive_client_secret="s",
                google_drive_refresh_token="r",
            )
        )
    }

    assert "google-drive" not in bare
    assert "google-drive" in full
