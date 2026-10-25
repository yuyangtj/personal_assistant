"""The Live2D avatar's files: served from a folder outside the repo, only safe ones."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient


def _assets(client: TestClient, tmp_path: Path) -> Path:
    root = tmp_path / "avatar-assets"
    (root / "Resources" / "ren").mkdir(parents=True)
    (root / "manifest.json").write_text('{"model": "ren"}')
    (root / "Resources" / "ren" / "ren.moc3").write_bytes(b"MOC3")
    (root / "notes.txt").write_text("not served")
    (tmp_path / "secret.json").write_text("{}")
    state = client.app.state
    state.settings = replace(state.settings, avatar_assets_dir=root)
    return root


def test_without_a_folder_there_is_no_live2d_avatar(client: TestClient) -> None:
    assert client.get("/ui/avatar-assets/manifest.json").status_code == 404


def test_files_come_from_the_folder_with_the_right_caching(
    client: TestClient, tmp_path: Path
) -> None:
    _assets(client, tmp_path)

    manifest = client.get("/ui/avatar-assets/manifest.json")
    model = client.get("/ui/avatar-assets/Resources/ren/ren.moc3")

    assert manifest.json() == {"model": "ren"} and manifest.headers["cache-control"] == "no-cache"
    assert model.content == b"MOC3" and "max-age" in model.headers["cache-control"]


def test_only_safe_paths_and_known_types_are_served(client: TestClient, tmp_path: Path) -> None:
    _assets(client, tmp_path)

    for path in ("notes.txt", "../secret.json", "Resources/../../secret.json", "missing.json"):
        assert client.get(f"/ui/avatar-assets/{path}").status_code == 404, path
