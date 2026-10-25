"""Ren (Live2D) as the avatar: loaded when the server has his files, driven by the same
events as the drawn one, and the drawing when it doesn't. Live2D's own files can't be in
CI, so a stand-in records what the model is told."""

from __future__ import annotations

import json
from dataclasses import replace

from playwright.sync_api import Page, expect

from tests.ui.helpers import eventually

STAND_IN = """
window.live2dCalls = [];
window.live2dAvatar = {
  mount(host, frame) {
    live2dCalls.push(["mount", frame]); host.dataset.mounted = "1"; return true;
  },
  unmount() { live2dCalls.push(["unmount"]); },
  setParams(p) { window.live2dParams = p; },
  setExpression(name) { live2dCalls.push(["expression", name]); },
  ready() { return true; },
};
"""


def _ren(live_app, tmp_path) -> None:
    root = tmp_path / "avatar-assets"
    (root / "Core").mkdir(parents=True)
    (root / "Core" / "live2dcubismcore.min.js").write_text("window.Live2DCubismCore = {};")
    (root / "live2d-avatar.js").write_text(STAND_IN)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "model": "ren",
                "script": "live2d-avatar.js",
                "core": "Core/live2dcubismcore.min.js",
                "frame": {"zoom": 4.2, "y": 0.7},
            }
        )
    )
    state = live_app.state
    state.settings = replace(state.settings, avatar_assets_dir=root)


def _params(page: Page) -> dict:
    return page.evaluate("() => window.live2dParams || {}")


def test_ren_is_used_when_the_server_has_him_and_follows_the_events(
    page: Page, live_server: str, live_app, tmp_path
) -> None:
    _ren(live_app, tmp_path)
    page.goto(f"{live_server}/ui")  # Assistant, where the avatar is on by default

    expect(page.locator("#avatar-stage .avatar-live2d")).to_have_attribute("data-mounted", "1")
    assert page.evaluate("() => live2dCalls[0]") == ["mount", {"zoom": 4.2, "y": 0.7}]
    expect(page.locator("#avatar-stage svg")).to_have_count(0)

    def event(type_: str, data: dict) -> None:
        page.evaluate("([t, d]) => avatarEvent(t, d)", [type_, data])

    event("transcript", {"waiting": False, "last": {"id": "m1", "emotion": "Concerned"}})
    eventually(lambda: ["expression", "exp_05"] in page.evaluate("() => live2dCalls"))
    event("transcript", {"waiting": False, "last": {"id": "m2", "emotion": "Excited"}})
    eventually(lambda: ["expression", "exp_02"] in page.evaluate("() => live2dCalls"))

    event("transcript", {"waiting": True, "last": None})  # thinking: eyes up and aside
    eventually(lambda: _params(page).get("ParamEyeBallY") == 0.6)

    event("transcript", {"waiting": False, "last": None})
    event("speech", {"state": "start"})
    event("word", {"word": "aaaaaaa", "ms": 600})
    eventually(lambda: _params(page)["ParamMouthOpenY"] > 0.5)
    event("speech", {"state": "done"})
    eventually(lambda: _params(page)["ParamMouthOpenY"] < 0.05)
    assert page.locator("#avatar-stage").get_attribute("data-state") == "idle"


def test_the_mapping_from_poses_to_rens_controls(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui")
    page.wait_for_function("() => window.avatar")
    mapped = page.evaluate(
        """() => {
      const target = (state, emotion, viseme) =>
        avatar.live2dMouthTarget(avatar.pose(state, emotion, viseme));
      return {
        round: target('speaking', 'Warm', 'U'), wide: target('speaking', 'Warm', 'E'),
        closed: target('speaking', 'Warm', 'M'), smiling: target('idle', 'Excited'),
        worried: target('idle', 'Concerned'),
        listening: avatar.live2dParams(avatar.pose('listening', 'Warm')),
      };
    }"""
    )
    assert mapped["round"]["form"] < 0 < mapped["wide"]["form"]
    assert mapped["closed"]["open"] == 0
    assert mapped["smiling"]["form"] > 0 > mapped["worried"]["form"]
    assert mapped["listening"]["+ParamAngleY"] == 6 and mapped["listening"]["ParamEyeBallX"] == 0


def test_without_rens_files_the_drawn_avatar_shows(page: Page, live_server: str) -> None:
    page.goto(f"{live_server}/ui")
    expect(page.locator("#avatar-stage svg")).to_be_visible()


def test_the_phone_reports_speech_and_words_to_ren(
    page: Page, live_server: str, live_app, tmp_path
) -> None:
    _ren(live_app, tmp_path)
    page.add_init_script(
        "window.AndroidAssistant = { postMessage(r) { const m = JSON.parse(r);"
        " if (m.type === 'hello') setTimeout(() => this.onmessage({ data: JSON.stringify("
        "{ type: 'capabilities', voice: true, speakQueue: true, speechEvents: true }) }), 30); },"
        " onmessage: null };"
    )
    page.goto(f"{live_server}/ui")
    expect(page.locator("#avatar-stage .avatar-live2d")).to_have_attribute("data-mounted", "1")

    page.evaluate("() => onPhoneMessage({ type: 'speech', state: 'start' })")
    expect(page.locator("#stop-speaking")).to_be_visible()
    page.evaluate("() => onPhoneMessage({ type: 'speech', state: 'word', word: 'aaaa' })")
    eventually(lambda: _params(page)["ParamMouthOpenY"] > 0.5)
    page.evaluate("() => onPhoneMessage({ type: 'speech', state: 'done' })")
    expect(page.locator("#stop-speaking")).to_be_hidden()
    eventually(lambda: _params(page)["ParamMouthOpenY"] < 0.05)
