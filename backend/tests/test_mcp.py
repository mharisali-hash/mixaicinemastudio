"""MCP server (M12c Update 3): /api/mcp tools per token scope, auth, a queued job end to end, resources and the
signed download links."""
import json

import pytest
from sqlalchemy import select

from app import file_links, mcp_server
from app.db import SessionLocal
from app.models import Generation, Membership, User
from app.security import make_session_token
from app.worker import process_one
from tests.test_api_tokens import _mint, _user, flags, login  # noqa: F401  (fixtures)

ACCEPT = {"Accept": "application/json, text/event-stream"}
READ_TOOLS = {"list_projects", "get_job", "wait_for_job", "search_library", "get_media", "list_voices",
              "get_poetry_short", "list_characters", "list_looks", "get_presenter_video", "list_presenter_videos",
              # the test user is an admin, so the admin-only Print Studio reader is listed too (contract v41)
              "get_print_design"}


class Mcp:
    """JSON-RPC over the Streamable HTTP endpoint, the way Claude Code talks to it (stateless, JSON answers)."""

    def __init__(self, http, token: str | None):
        self.http = http
        self.headers = {**ACCEPT, **({"Authorization": f"Bearer {token}"} if token else {})}
        self.n = 0

    def raw(self, method: str, params: dict | None = None):
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method, **({"params": params} if params else {})}
        return self.http.post("/api/mcp", json=body, headers=self.headers)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        r = self.raw(method, params)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "error" not in data, data
        return data["result"]

    def tools(self) -> set[str]:
        return {t["name"] for t in self.rpc("tools/list")["tools"]}

    def call(self, tool: str, **args) -> dict:
        return self.rpc("tools/call", {"name": tool, "arguments": args})

    def call_json(self, tool: str, **args) -> dict:
        res = self.call(tool, **args)
        assert not res["isError"], res
        return json.loads(res["content"][0]["text"])


def _drain(fast_driver):
    while process_one(driver_factory=fast_driver):
        pass


# ---------------------------------------------------------------- auth and scopes

def test_handshake_and_no_standing_stream(client, anon):
    m = Mcp(anon, _mint(client, ["read"])["token"])
    init = m.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"}})
    assert init["serverInfo"]["name"] == "mixai" and "wait_for_job" in init["instructions"]
    r = anon.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=m.headers)
    assert r.status_code == 202
    assert anon.get("/api/mcp", headers=m.headers).status_code == 405


def test_tool_list_follows_the_token_scopes(client, anon):
    read = Mcp(anon, _mint(client, ["read"])["token"])
    assert read.tools() == READ_TOOLS

    both = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    assert both.tools() == set(mcp_server.TOOL_SCOPE)
    assert {"generate_image", "make_character_sheet", "create_project_from_idea", "transcribe"} <= both.tools()
    assert len(both.rpc("resources/templates/list")["resourceTemplates"]) == 3

    gen_only = Mcp(anon, _mint(client, ["generate"])["token"])
    assert gen_only.tools() == set(mcp_server.TOOL_SCOPE) - READ_TOOLS
    assert gen_only.rpc("resources/templates/list")["resourceTemplates"] == []


def test_every_tool_has_a_description_and_typed_inputs(client, anon):
    for t in Mcp(anon, _mint(client, ["read", "generate"])["token"]).rpc("tools/list")["tools"]:
        assert t["description"].strip(), t["name"]
        assert t["inputSchema"]["type"] == "object"
        assert "ctx" not in t["inputSchema"].get("properties", {})


def test_a_hidden_tool_is_refused_when_called_anyway(client, anon):
    read = Mcp(anon, _mint(client, ["read"])["token"])
    res = read.call("generate_image", prompt="a red kite")
    assert res["isError"] and "generate" in res["content"][0]["text"]


def test_no_token_bad_token_cookie_or_revoked_token_is_401(client, anon):
    assert Mcp(anon, None).raw("tools/list").status_code == 401
    r = Mcp(anon, "mxa_deadbeef_not-a-real-secret-at-all-000").raw("tools/list")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    # a signed-in browser session is not enough: MCP is tokens only
    anon.cookies.set("mixai_session", make_session_token(client.get("/api/auth/me").json()["id"]))
    assert Mcp(anon, None).raw("tools/list").status_code == 401
    anon.cookies.clear()

    made = _mint(client, ["read"])
    m = Mcp(anon, made["token"])
    assert m.tools() == READ_TOOLS
    assert client.delete(f"/api/tokens/{made['id']}").status_code == 204
    assert m.raw("tools/list").status_code == 401


def test_a_viewer_only_sees_read_tools(client, anon, login, flags):
    flags(public_api=True)
    ed = login(_user("ed@test.local", "editor"))
    token = _mint(ed, ["read", "generate"])["token"]
    m = Mcp(anon, token)
    assert "generate_image" in m.tools()
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.email == "ed@test.local")).one()
        db.scalars(select(Membership).where(Membership.user_id == u.id)).one().role = "viewer"
        db.commit()
    # not an admin: Print Studio and Presenter aren't released to packages yet (contract v42)
    assert m.tools() == READ_TOOLS - {"get_print_design", "get_presenter_video", "list_presenter_videos"}
    assert m.call("generate_image", prompt="x")["isError"]


# ---------------------------------------------------------------- tools end to end

def test_generate_image_then_get_job_and_download(client, anon, fast_driver):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    out = m.call_json("generate_image", prompt="a red kite over the dunes", aspect="16:9")
    assert out["model"] and len(out["items"]) == 1 and len(out["jobs"]) == 1
    job_id, media_id = out["jobs"][0]["job_id"], out["items"][0]["media_id"]
    assert m.call_json("get_job", job_id=job_id)["status"] == "queued"

    _drain(fast_driver)
    done = m.call_json("wait_for_job", job_id=job_id, timeout_s=5)
    assert done["status"] == "done" and done["waited_s"] == 0
    assert done["download_url"].startswith("http://testserver/api/files/")
    r = anon.get(done["download_url"].removeprefix("http://testserver"))
    assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.headers["cache-control"] == "private, no-store"

    res = m.call("get_media", id=media_id)
    meta = json.loads(res["content"][0]["text"])
    assert meta["media_id"] == media_id and meta["kind"] == "image" and meta["download_url"]
    assert res["content"][1]["type"] == "image" and res["content"][1]["mimeType"] == "image/png"
    assert len(m.call("get_media", id=media_id, preview=False)["content"]) == 1

    found = m.call_json("search_library", query="kite")
    assert [i["media_id"] for i in found["items"]] == [media_id]


def test_route_errors_come_back_as_tool_errors(client, anon):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    res = m.call("get_job", job_id="not-a-job")
    assert res["isError"] and "Job not found" in res["content"][0]["text"]
    res = m.call("generate_image", prompt="x", style="no-such-style")
    assert res["isError"] and "Unknown style" in res["content"][0]["text"]
    # bad arguments are caught by the input schema before the route runs
    assert m.call("generate_image", prompt="x", count=9)["isError"]


def test_wait_for_job_gives_up_and_reports_progress(client, anon):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    job_id = m.call_json("make_sfx", prompt="a door creaks")["jobs"][0]["job_id"]
    out = m.call_json("wait_for_job", job_id=job_id, timeout_s=1)
    assert out["status"] == "queued" and out["waited_s"] == 1 and "progress" in out


def test_character_tools_and_resources(client, anon, fast_driver):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    made = m.call_json("create_character", name="Maya", description="marine biologist, short grey hair",
                       profile={"age": "34"})
    cid = made["character_id"]
    assert made["portrait"]["status"] == "queued"
    # no portrait yet: the route's own message comes through
    res = m.call("make_character_sheet", character_id=cid)
    assert res["isError"] and "portrait" in res["content"][0]["text"]

    _drain(fast_driver)
    sheet = m.call_json("make_character_sheet", character_id=cid, preset="essential")
    assert sheet["queued"] >= 1 and sheet["panels"]

    voice = m.call_json("list_voices")
    gallery_id = voice["gallery"][0]["voice_id"]
    assert gallery_id.startswith("gallery:")
    assert m.call_json("set_character_voice", character_id=cid, voice_id=gallery_id)["voice_id"] == gallery_id

    res = m.rpc("resources/read", {"uri": f"mixai://characters/{cid}"})
    data = json.loads(res["contents"][0]["text"])
    assert data["name"] == "Maya" and data["voice_id"] == gallery_id and data["profile"]["age"] == "34"
    assert data["portrait_generation_id"] and data["sheet"]["preset"] == "essential"

    media = json.loads(m.rpc("resources/read", {"uri": f"mixai://media/{data['portrait_generation_id']}"})
                       ["contents"][0]["text"])
    assert media["kind"] == "portrait" and media["download_url"]


def test_project_tools_and_resource(client, anon, project, character):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    assert [p["project_id"] for p in m.call_json("list_projects")["projects"]] == [project["id"]]
    data = json.loads(m.rpc("resources/read", {"uri": f"mixai://projects/{project['id']}"})["contents"][0]["text"])
    assert data["title"] == "Reef" and data["cast"] == [{"character_id": character["id"], "name": "Diver"}]

    quick = m.call_json("create_project_from_idea", idea="A diver finds a lost city under the reef", duration_s=20)
    assert quick["project_id"] and quick["type"] == "autopilot" and quick["status"] == "queued"
    err = m.raw("resources/read", {"uri": "mixai://projects/not-a-project"}).json()["error"]
    assert err["message"] == "Project not found"


def test_storyboard_prompt(client, anon):
    m = Mcp(anon, _mint(client, ["read"])["token"])
    assert [p["name"] for p in m.rpc("prompts/list")["prompts"]] == ["storyboard_idea"]
    got = m.rpc("prompts/get", {"name": "storyboard_idea", "arguments": {"idea": "a lighthouse keeper's last night"}})
    text = got["messages"][0]["content"]["text"]
    assert "lighthouse keeper" in text and "create_project_from_idea" in text


# ---------------------------------------------------------------- signed links

@pytest.fixture
def finished(client, fast_driver) -> Generation:
    client.post("/api/images/generate", json={"prompt": "a lighthouse"})
    _drain(fast_driver)
    with SessionLocal() as db:
        return db.scalars(select(Generation).where(Generation.status == "ready")).one()


def test_signed_link_serves_the_file_without_auth(anon, finished):
    r = anon.get(f"/api/files/{file_links.sign(finished)}")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_signed_link_expires(anon, finished, monkeypatch):
    signed = file_links.sign(finished)
    monkeypatch.setattr(file_links, "TTL_S", -1)
    assert anon.get(f"/api/files/{signed}").status_code == 404


def test_tampered_or_foreign_links_are_refused(anon, finished):
    signed = file_links.sign(finished)
    # change a character in the middle of the signature: the last base64 character can carry padding bits,
    # so flipping it sometimes decodes to the same bytes and the test failed now and then
    sig_start = signed.rindex(".") + 1
    i = sig_start + (len(signed) - sig_start) // 2
    flipped = signed[:i] + ("A" if signed[i] != "A" else "B") + signed[i + 1:]
    assert anon.get(f"/api/files/{flipped}").status_code == 404
    # a session cookie is signed with the same secret but another salt
    assert anon.get(f"/api/files/{make_session_token(finished.id)}").status_code == 404
    other = file_links._serializer().dumps({"g": finished.id, "w": "someone-else"})
    assert anon.get(f"/api/files/{other}").status_code == 404
    assert file_links.read("garbage") is None


def test_poetry_short_tools(client, anon):
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    made = m.call_json("make_poetry_short", topic="the rain at dusk", language="ur", length_s=45, vocal="male")
    assert made["short_id"] and made["job_id"]
    got = m.call_json("get_poetry_short", short_id=made["short_id"])
    assert [s["key"] for s in got["stages"]][0] == "poem" and got["video"] is None
    # made over MCP, so it shows in the app's list
    assert made["short_id"] in [s["id"] for s in client.get("/api/poetry").json()]
    assert m.call("make_poetry_short", language="ur")["isError"]
    busy = m.call("continue_poetry_short", short_id=made["short_id"])  # still running: nothing to carry on
    assert busy["isError"] and "working" in busy["content"][0]["text"]


def test_separate_stems_can_write_out_the_lyrics(client, anon, fast_driver, tmp_path):
    from tests.test_audio_edit import tone, upload_audio

    a = upload_audio(client, tone(tmp_path / "song.wav", 3.0, 330), title="Lori")
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    job = m.call_json("separate_stems", media_id=a["id"], lyrics=True)
    _drain(fast_driver)
    done = m.call_json("get_job", job_id=job["job_id"])
    assert done["status"] == "done" and "mock transcript" in done["result"]["lyrics"]

    # off by default: a plain split has no lyrics in its result
    job = m.call_json("separate_stems", media_id=a["id"], mode="four")
    _drain(fast_driver)
    assert "lyrics" not in m.call_json("get_job", job_id=job["job_id"])["result"]


def test_make_presenter_video_returns_its_job_and_is_listed(client, anon, fast_driver):
    from tests.test_presenter import _presenter_character

    c = _presenter_character(client, fast_driver)
    m = Mcp(anon, _mint(client, ["read", "generate"])["token"])
    out = m.call_json("make_presenter_video", character_id=c["id"], script="Hello.\n\nHow are you?")
    assert out["scenes"] == 2 and out["job_id"]
    listed = m.call_json("list_presenter_videos")["result"]
    assert [p["presenter_id"] for p in listed] == [out["presenter_id"]]
    assert m.call_json("get_presenter_video", presenter_id=out["presenter_id"])["presenter_id"] == out["presenter_id"]
