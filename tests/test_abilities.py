from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.abilities import (
    Ability,
    AbilityRegistry,
    AbilityRegistryError,
    Band,
    abilities_prompt,
)
from app.config import Settings


@pytest.fixture
def registry() -> AbilityRegistry:
    return AbilityRegistry.from_directory("abilities")


def test_every_ability_file_is_valid_and_reads_as_a_full_sentence(registry) -> None:
    abilities = registry.list()
    assert len(abilities) >= 10
    for ability in abilities:
        # An unquoted "#tag" in YAML starts a comment and silently cuts the sentence short.
        assert ability.summary.rstrip()[-1] in ".!?", f"{ability.id}: {ability.summary!r}"
    assert {ability.band for ability in abilities} == set(Band)


def test_abilities_that_need_something_missing_are_not_offered(registry) -> None:
    bare = {ability.id for ability in registry.available(Settings())}
    full = {
        ability.id for ability in registry.available(Settings(github_token="t", tavily_api_key="k"))
    }

    assert "repositories" not in bare and "research" not in bare
    assert {"repositories", "research"} <= full
    assert "notes" in bare and "coding" in bare


def test_the_description_is_grouped_by_band_and_says_it_is_complete(registry) -> None:
    text = abilities_prompt(registry.available(Settings()))

    headings = [
        "Done without asking:",
        "Done, and the user reviews the result before it takes effect:",
        "Only after the user confirms:",
        "Never done by the assistant:",
    ]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert text.startswith("What the assistant can do. This is the complete list")
    assert "say the assistant can't do that yet" in text
    assert "- Notes: Save a note" in text and '(for example "note: call the dentist' in text
    assert text.index("- Coding:") > text.index("Done, and the user reviews")
    assert text.index("- Merging and deploying:") > text.index("Never done by the assistant")


def test_a_band_with_nothing_in_it_is_left_out() -> None:
    only = Ability(id="notes", name="Notes", summary="Save notes.", kind="quick", band="free")

    text = abilities_prompt([only])

    assert "Done without asking:" in text and "Never done" not in text


def test_bad_registries_are_refused(tmp_path) -> None:
    (tmp_path / "a.yaml").write_text("id: notes\nname: N\nsummary: S.\nkind: quick\nband: free\n")
    (tmp_path / "b.yaml").write_text("id: notes\nname: N\nsummary: S.\nkind: quick\nband: free\n")
    with pytest.raises(AbilityRegistryError, match="Duplicate"):
        AbilityRegistry.from_directory(tmp_path)

    (tmp_path / "b.yaml").write_text(
        "id: other\nname: N\nsummary: S.\nkind: quick\nband: sometimes\n"
    )
    with pytest.raises(AbilityRegistryError, match="Invalid ability"):
        AbilityRegistry.from_directory(tmp_path)
    with pytest.raises(AbilityRegistryError, match="does not exist"):
        AbilityRegistry.from_directory(tmp_path / "missing")


def test_the_abilities_endpoint(client: TestClient) -> None:
    response = client.get("/abilities")

    assert response.status_code == 200
    abilities = {ability["id"]: ability for ability in response.json()["abilities"]}
    assert abilities["coding"]["band"] == "review" and abilities["coding"]["kind"] == "job"
    assert abilities["notes"]["available"] is True
    assert abilities["repositories"]["available"] is False  # no GitHub token in tests
