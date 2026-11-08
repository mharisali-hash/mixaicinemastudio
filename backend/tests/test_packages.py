"""Packages, quota and the admin panel (M17 step 2, contract v39)."""
import csv
import io
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import models_catalog as mc
from app import packages as pk
from app.db import SessionLocal
from app.models import (ACTING_USER, AdminAudit, Job, Membership, Package, Project, Subscription, UsageLedger,
                        User, utcnow)
from app.quota import QUOTA_USER, QuotaExceeded
from app.worker import claim_next
from tests.conftest import ADMIN
from tests.test_accounts import PW, make_user
from tests.test_api_tokens import _bearer, _mint, flags, login  # noqa: F401  (fixtures)
from tests.test_mcp import Mcp

KITE = {"prompt": "a red kite"}


def _ws(uid: str) -> str:
    with SessionLocal() as db:
        return db.scalars(select(Membership.workspace_id).where(Membership.user_id == uid)).first()


def _spend(uid: str, minutes: float, at=None) -> None:
    with SessionLocal() as db:
        db.add(UsageLedger(workspace_id=_ws(uid), user_id=uid, gpu_seconds=minutes * 60, kind="image",
                           created_at=at or utcnow()))
        db.commit()


def _pkg(slug: str) -> str:
    with SessionLocal() as db:
        return db.scalars(select(Package.id).where(Package.slug == slug)).one()


@pytest.fixture
def ben(login):  # noqa: F811
    uid = make_user()
    return uid, login({"email": "ben@example.com", "password": PW})


def _admin_id(client) -> str:
    return client.get("/api/auth/me").json()["id"]


# ---------------------------------------------------------------- what everyone starts with

def test_seeded_packages_and_new_accounts_start_on_free(client, ben):
    uid, b = ben
    pkgs = {p["slug"]: p for p in client.get("/api/admin/packages").json()}
    assert set(pkgs) == {"free", "demo", "pro"}
    assert (pkgs["free"]["gpu_min_day"], pkgs["free"]["max_queued"]) == (30, 2)
    assert (pkgs["demo"]["kind"], pkgs["demo"]["days"]) == ("demo", 7)
    assert pkgs["free"]["subscribers"] == 1

    u = b.get("/api/account/usage").json()
    assert u["package"]["name"] == "Free" and not u["unlimited"]
    assert (u["gpu_min_day"], u["max_queued"], u["used_min_today"], u["queued"]) == (30, 2, 0, 0)
    assert u["resets_at"].endswith("T00:00:00Z") and u["subscription"]["source"] == "signup_default"

    mine = client.get("/api/account/usage").json()
    assert mine["unlimited"] and mine["gpu_min_day"] is None
    row = next(r for r in client.get("/api/admin/users").json() if r["id"] == uid)
    assert row["package_name"] == "Free" and row["gpu_min_day_limit"] == 30


# ---------------------------------------------------------------- the 429

def test_over_the_daily_minutes_is_429_everywhere(anon, ben, flags):  # noqa: F811
    flags(public_api=True)
    uid, b = ben
    _spend(uid, 30)
    r = b.post("/api/images/generate", json=KITE)
    assert r.status_code == 429
    body = r.json()
    assert body["reason"] == "day" and body["used_min"] == 30 and body["limit_min"] == 30
    assert body["resets_at"].endswith("T00:00:00Z") and "GPU minutes" in body["detail"]

    # the token REST API and MCP count against the same package
    token = _mint(b, ["read", "generate"])["token"]
    r = anon.post("/api/images/generate", json=KITE, headers=_bearer(token))
    assert r.status_code == 429 and r.json()["limit_min"] == 30
    res = Mcp(anon, token).call("generate_image", prompt="a red kite")
    assert res["isError"] and "GPU minutes" in res["content"][0]["text"]
    with SessionLocal() as db:
        assert db.scalars(select(Job).where(Job.user_id == uid)).all() == []

    # yesterday's minutes don't count
    with SessionLocal() as db:
        for row in db.scalars(select(UsageLedger)):
            row.created_at = utcnow() - timedelta(days=1)
        db.commit()
    assert b.post("/api/images/generate", json=KITE).status_code == 202


def test_monthly_limit(ben, client):
    uid, b = ben
    client.patch(f"/api/admin/users/{uid}", json={"limit_gpu_min_month": 10, "limit_gpu_min_day": 100})
    _spend(uid, 11, at=utcnow().replace(day=1, hour=0, minute=0, second=1))
    r = b.post("/api/images/generate", json=KITE)
    assert r.status_code == 429 and r.json()["reason"] == "month"


def test_max_queued_counts_top_level_jobs(ben):
    uid, b = ben
    assert b.post("/api/images/generate", json=KITE).status_code == 202
    assert b.post("/api/images/generate", json=KITE).status_code == 202
    r = b.post("/api/images/generate", json=KITE)
    assert r.status_code == 429 and r.json()["reason"] == "queued" and r.json()["max_queued"] == 2
    # a child of one of them doesn't take a place
    with SessionLocal() as db:
        db.add(Job(workspace_id=_ws(uid), user_id=uid, type="generate", payload={"parent_job_id": "x"}))
        db.commit()
    assert b.get("/api/account/usage").json()["queued"] == 2


def test_child_jobs_and_the_worker_are_exempt(ben):
    uid, _ = ben
    _spend(uid, 60)
    ws = _ws(uid)
    with SessionLocal() as db:
        # the worker's sessions carry the user but aren't quota-checked
        db.info[ACTING_USER] = uid
        db.add(Job(workspace_id=ws, type="generate"))
        db.commit()
    with SessionLocal() as db:
        db.info[QUOTA_USER] = uid
        db.add(Job(workspace_id=ws, type="generate", payload={"parent_job_id": "p1"}))
        db.add(Job(workspace_id=ws, type="generate", payload={"inline_of": "p1"}))
        db.commit()
        db.add(Job(workspace_id=ws, type="generate"))
        with pytest.raises(QuotaExceeded):
            db.flush()


def test_admins_are_unlimited(client):
    _spend(_admin_id(client), 10_000)
    for _ in range(8):
        assert client.post("/api/images/generate", json=KITE).status_code == 202


# ---------------------------------------------------------------- grants, demos, expiry, custom, override

def test_grants_add_minutes_and_can_be_revoked(client, ben):
    uid, b = ben
    _spend(uid, 30)
    assert b.post("/api/images/generate", json=KITE).status_code == 429
    r = client.post(f"/api/admin/users/{uid}/grants", json={"gpu_min": 15, "reason": "Sorry about the outage"})
    assert r.status_code == 201 and r.json()["live"]
    # no expiry given: a top-up for the rest of today
    assert r.json()["expires_at"].endswith("T00:00:00Z")
    u = b.get("/api/account/usage").json()
    assert (u["gpu_min_day"], u["grant_min"]) == (45, 15)
    assert b.post("/api/images/generate", json=KITE).status_code == 202

    gid = r.json()["id"]
    assert not client.post(f"/api/admin/grants/{gid}/revoke").json()["live"]
    assert b.get("/api/account/usage").json()["gpu_min_day"] == 30
    assert client.post(f"/api/admin/users/{uid}/grants", json={"gpu_min": 0}).status_code == 422


def test_demo_once_unless_the_admin_says_so(client, ben):
    uid, b = ben
    r = client.post(f"/api/admin/users/{uid}/demo", json={})
    assert r.status_code == 201
    sub = r.json()
    assert sub["package_name"] == "Demo (7 days)" and sub["source"] == "demo"
    u = b.get("/api/account/usage").json()
    assert u["package"]["kind"] == "demo" and u["gpu_min_day"] == 120 and u["max_queued"] == 4
    assert u["subscription"]["ends_at"] is not None

    assert client.post(f"/api/admin/users/{uid}/demo", json={}).status_code == 409
    assert client.post(f"/api/admin/users/{uid}/demo", json={"allow_repeat": True}).status_code == 201
    with SessionLocal() as db:
        statuses = sorted(s.status for s in db.scalars(select(Subscription).where(Subscription.user_id == uid)))
    # one active at a time: the Free row and the first demo were cancelled
    assert statuses == ["active", "cancelled", "cancelled"]


def test_expired_subscription_falls_back_to_free(client, ben):
    uid, b = ben
    past = utcnow() - timedelta(days=40)
    r = client.post(f"/api/admin/users/{uid}/subscription",
                    json={"package_id": _pkg("pro"), "starts_at": past.isoformat(),
                          "ends_at": (past + timedelta(days=30)).isoformat()})
    assert r.status_code == 201 and r.json()["status"] == "expired"
    with SessionLocal() as db:
        # still "active" on the row until something sweeps; the worker does it every few minutes
        assert pk.expire_due(db) == 1
        db.commit()
    assert b.get("/api/account/usage").json()["package"]["name"] == "Free"
    assert [s["id"] for s in client.get("/api/admin/subscriptions?status=expired").json()] == [r.json()["id"]]

    # extending it brings it back
    r = client.post(f"/api/admin/subscriptions/{r.json()['id']}/extend", json={"days": 30})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert b.get("/api/account/usage").json()["package"]["name"] == "Pro"
    soon = client.get("/api/admin/subscriptions?status=expiring").json()
    assert soon == []
    assert client.post(f"/api/admin/subscriptions/{r.json()['id']}/cancel").json()["status"] == "cancelled"
    assert b.get("/api/account/usage").json()["package"]["name"] == "Free"


def test_assign_with_dates_and_expiring_soon(client, ben):
    uid, _ = ben
    end = utcnow() + timedelta(days=3)
    r = client.post(f"/api/admin/users/{uid}/subscription", json={"package_id": _pkg("pro"),
                                                                  "ends_at": end.isoformat() + "Z"})
    assert r.status_code == 201 and r.json()["ends_at"].startswith(end.strftime("%Y-%m-%dT%H:%M"))
    assert [s["user_id"] for s in client.get("/api/admin/subscriptions?status=expiring").json()] == [uid]
    assert [u["id"] for u in client.get(f"/api/admin/users?package_id={_pkg('pro')}").json()] == [uid]
    assert client.get(f"/api/admin/users?package_id={_pkg('free')}").json() == []
    bad = client.post(f"/api/admin/users/{uid}/subscription",
                      json={"package_id": _pkg("pro"), "ends_at": (utcnow() - timedelta(days=1)).isoformat()})
    assert bad.status_code == 422


def test_custom_package_for_one_user(client, ben):
    uid, b = ben
    r = client.post(f"/api/admin/users/{uid}/custom-package",
                    json={"name": "Ben's studio deal", "gpu_min_day": 5, "max_queued": 1, "price_pkr": 1500})
    assert r.status_code == 201
    u = b.get("/api/account/usage").json()
    assert (u["package"]["name"], u["package"]["kind"], u["gpu_min_day"], u["max_queued"]) == \
        ("Ben's studio deal", "custom", 5, 1)
    custom = next(p for p in client.get("/api/admin/packages").json() if p["kind"] == "custom")
    assert custom["custom_for_user_id"] == uid and not custom["public"] and custom["price_pkr"] == 1500
    other = make_user("cara@example.com", "Cara")
    r = client.post(f"/api/admin/users/{other}/subscription", json={"package_id": custom["id"]})
    assert r.status_code == 422


def test_per_user_override(client, ben):
    uid, b = ben
    r = client.patch(f"/api/admin/users/{uid}", json={"limit_gpu_min_day": 100, "limit_max_queued": 9})
    assert r.status_code == 200 and r.json()["gpu_min_day_limit"] == 100
    assert (b.get("/api/account/usage").json()["max_queued"]) == 9
    client.patch(f"/api/admin/users/{uid}", json={"limit_gpu_min_day": None})
    u = b.get("/api/account/usage").json()
    assert (u["gpu_min_day"], u["max_queued"]) == (30, 9)
    detail = client.get(f"/api/admin/users/{uid}").json()
    assert detail["limits_override"] == {"gpu_min_day": None, "gpu_min_month": None, "max_queued": 9}


def test_features_and_models_are_enforced(client, ben, anon, flags):  # noqa: F811
    uid, b = ben
    flags(public_api=True)
    pid = client.post("/api/admin/packages", json={"name": "Stills off", "features": {"image": False}}).json()["id"]
    client.post(f"/api/admin/users/{uid}/subscription", json={"package_id": pid})
    r = b.post("/api/images/generate", json=KITE)
    assert r.status_code == 403 and "Image studio" in r.json()["detail"]
    assert b.get("/api/account/usage").json()["features"]["image"] is False

    default = mc.default_for("image").id
    other = next(m.id for m in mc.MODELS.values() if m.type == "image" and m.id != default)
    pid = client.post("/api/admin/packages", json={"name": "One model", "models": [other]}).json()["id"]
    client.post(f"/api/admin/users/{uid}/subscription", json={"package_id": pid})
    r = b.post("/api/images/generate", json={**KITE, "model": default})
    assert r.status_code == 403 and default in r.json()["detail"]
    client.patch(f"/api/admin/packages/{pid}", json={"models": [other, default]})
    assert b.post("/api/images/generate", json={**KITE, "model": default}).status_code == 202

    pid = client.post("/api/admin/packages", json={"name": "App only", "features": {"api": False}}).json()["id"]
    client.post(f"/api/admin/users/{uid}/subscription", json={"package_id": pid})
    token = _mint(b, ["read", "generate"])["token"]
    r = anon.post("/api/images/generate", json=KITE, headers=_bearer(token))
    assert r.status_code == 403 and "API" in r.json()["detail"]
    assert b.post("/api/images/generate", json=KITE).status_code == 202
    assert client.post("/api/admin/packages", json={"name": "Bad", "models": ["nope"]}).status_code == 422
    assert client.post("/api/admin/packages", json={"name": "Bad", "features": {"nope": False}}).status_code == 422


# ---------------------------------------------------------------- the fair queue

def _job(ws, uid, at, *, priority=0, lane="image", type_="generate", project_id=None, status="queued"):
    return Job(workspace_id=ws, user_id=uid, type=type_, lane=lane, priority=priority, created_at=at,
               project_id=project_id, status=status)


def test_fair_claim_alternates_users_within_a_priority(client, db):
    a = _admin_id(client)
    b = make_user()
    ws_a, ws_b = _ws(a), _ws(b)
    t = utcnow() - timedelta(minutes=10)
    a_jobs = [_job(ws_a, a, t + timedelta(seconds=i)) for i in range(3)]
    b_job = _job(ws_b, b, t + timedelta(seconds=30))
    db.add_all([*a_jobs, b_job])
    db.commit()
    order = []
    for _ in range(4):
        j = claim_next(db)
        order.append(j.id)
        j.status = "done"
        db.commit()
    # A's batch was first in line, but B's one job goes second instead of last
    assert order == [a_jobs[0].id, b_job.id, a_jobs[1].id, a_jobs[2].id]


def test_fair_claim_prefers_fewest_running_and_keeps_priority(client, db):
    a = _admin_id(client)
    b = make_user()
    ws_a, ws_b = _ws(a), _ws(b)
    t = utcnow() - timedelta(minutes=10)
    running = _job(ws_a, a, t, status="running")
    running.started_at = t
    a_next = _job(ws_a, a, t + timedelta(seconds=1))
    b_next = _job(ws_b, b, t + timedelta(seconds=5))
    # a parent waiting on its children stays behind everyone's normal work
    b_wait = _job(ws_b, b, t - timedelta(minutes=5), priority=-20)
    a_zip = _job(ws_a, a, t + timedelta(seconds=9), priority=50, lane="image")
    db.add_all([running, a_next, b_next, b_wait, a_zip])
    db.commit()
    got = []
    for _ in range(4):
        j = claim_next(db)
        got.append(j.id)
    assert got == [a_zip.id, b_next.id, a_next.id, b_wait.id]


def test_fair_claim_keeps_the_serial_per_project_rule(client, db):
    a = _admin_id(client)
    b = make_user()
    ws_a = _ws(a)
    p = Project(workspace_id=ws_a, title="Reef", authoring_mode="scene_by_scene")
    db.add(p)
    db.flush()
    t = utcnow() - timedelta(minutes=10)
    db.add(_job(ws_a, a, t, type_="reel_assemble", lane="general", project_id=p.id, status="running"))
    waiting = _job(ws_a, a, t + timedelta(seconds=1), type_="reel_assemble", lane="general", project_id=p.id)
    other = _job(_ws(b), b, t + timedelta(seconds=2), lane="general", type_="ai_summarize")
    db.add_all([waiting, other])
    db.commit()
    assert claim_next(db).id == other.id
    assert claim_next(db) is None


# ---------------------------------------------------------------- packages CRUD, payments, usage, audit

def test_packages_crud(client):
    r = client.post("/api/admin/packages", json={"name": "Studio", "price_pkr": 4999, "period": "month",
                                                 "gpu_min_day": 600, "max_queued": 10, "public": True})
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.patch(f"/api/admin/packages/{pid}", json={"price_pkr": 3999, "gpu_min_day": None})
    assert r.json()["price_pkr"] == 3999 and r.json()["gpu_min_day"] is None
    # the edit form sends the whole package, name included
    r = client.patch(f"/api/admin/packages/{pid}", json={"name": "Studio Plus", "price_pkr": 3999})
    assert r.status_code == 200 and r.json()["name"] == "Studio Plus"
    dup = client.post(f"/api/admin/packages/{pid}/duplicate").json()
    assert dup["name"] == "Studio Plus (copy)" and dup["price_pkr"] == 3999 and not dup["public"]
    assert not client.post(f"/api/admin/packages/{dup['id']}/archive").json()["active"]
    assert dup["id"] not in {p["id"] for p in client.get("/api/admin/packages").json()}
    assert dup["id"] in {p["id"] for p in client.get("/api/admin/packages?archived=true").json()}
    assert client.post(f"/api/admin/packages/{_pkg('free')}/archive").status_code == 422
    assert client.patch(f"/api/admin/packages/{_pkg('free')}", json={"active": False}).status_code == 422
    assert client.post(f"/api/admin/packages/{dup['id']}/restore").json()["active"]


def test_payments_recorded_and_marked_by_hand(client, ben):
    uid, b = ben
    r = client.post("/api/admin/payments", json={"user_id": uid, "amount_pkr": 2500, "package_id": _pkg("pro"),
                                                 "gateway": "bank", "reference": "TXN-1"})
    assert r.status_code == 201
    pay = r.json()
    assert pay["status"] == "pending" and pay["package_name"] == "Pro" and pay["raw"] is None
    # waiting on the money: still on Free
    assert b.get("/api/account/usage").json()["package"]["name"] == "Free"
    assert [p["id"] for p in client.get("/api/admin/subscriptions?status=pending_payment").json()] == \
        [pay["subscription_id"]]

    r = client.post(f"/api/admin/payments/{pay['id']}/mark", json={"status": "paid", "notes": "Seen in the bank"})
    assert r.status_code == 200 and r.json()["status"] == "paid"
    assert b.get("/api/account/usage").json()["package"]["name"] == "Pro"
    assert client.post(f"/api/admin/payments/{pay['id']}/mark", json={"status": "paid"}).status_code == 409
    assert client.post(f"/api/admin/payments/{pay['id']}/mark", json={"status": "refunded"}).json()["status"] == \
        "refunded"

    assert [p["id"] for p in client.get("/api/admin/payments?status=refunded").json()] == [pay["id"]]
    assert client.get("/api/admin/payments?gateway=jazzcash").json() == []
    assert client.get(f"/api/admin/payments/{pay['id']}").json()["notes"] == "Seen in the bank"
    ov = client.get("/api/admin/overview").json()
    assert ov["users"]["total"] == 2 and ov["payments_month"]["count"] == 0
    assert {"queued", "running", "lanes"} <= set(ov["queue"]) and "live" in ov["workers"]


def test_usage_report_and_csv(client, ben):
    uid, _ = ben
    _spend(uid, 12)
    _spend(uid, 3, at=utcnow() - timedelta(days=1))
    _spend(_admin_id(client), 1)
    rep = client.get("/api/admin/usage?days=7").json()
    assert rep["total_min"] == 16 and rep["top"][0]["user_id"] == uid and rep["top"][0]["gpu_min"] == 15
    assert len([r for r in rep["rows"] if r["user_id"] == uid]) == 2
    only = client.get(f"/api/admin/usage?days=1&user_id={uid}").json()
    assert [r["gpu_min"] for r in only["rows"]] == [12]

    r = client.get("/api/admin/usage.csv?days=7")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["day", "user_id", "email", "name", "gpu_minutes", "jobs"]
    assert any(row[2] == "ben@example.com" and row[4] == "12.0" for row in rows[1:])


def test_user_detail(client, ben):
    uid, b = ben
    assert b.post("/api/images/generate", json=KITE).status_code == 202
    client.post(f"/api/admin/users/{uid}/grants", json={"gpu_min": 5})
    d = client.get(f"/api/admin/users/{uid}").json()
    assert d["user"]["email"] == "ben@example.com" and d["subscription"]["package_name"] == "Free"
    assert d["usage"]["queued"] >= 1 and len(d["grants"]) == 1 and d["payments"] == []
    assert d["recent_jobs"] and d["recent_jobs"][0]["type"] == "generate" and not d["had_demo"]
    assert client.get("/api/admin/users/nope").status_code == 404


def test_every_admin_change_is_audited(client, ben, monkeypatch):
    from app import mailer
    monkeypatch.setattr(mailer, "deliver", lambda _m: None)
    uid, _ = ben
    client.patch(f"/api/admin/users/{uid}", json={"limit_max_queued": 3})
    client.post(f"/api/admin/users/{uid}/grants", json={"gpu_min": 10, "reason": "launch"})
    client.post(f"/api/admin/users/{uid}/demo", json={})
    client.post(f"/api/admin/users/{uid}/send-reset")
    pid = client.post("/api/admin/packages", json={"name": "X"}).json()["id"]
    client.post(f"/api/admin/packages/{pid}/archive")
    rows = client.get("/api/admin/audit").json()
    actions = [r["action"] for r in rows]
    assert actions == ["package_archive", "package_create", "user_send_reset", "demo_start", "grant_add",
                       "user_update"]
    assert all(r["actor_email"] == ADMIN["email"] for r in rows)
    assert rows[-1]["detail"] == {"limit_max_queued": 3} and rows[-1]["target_id"] == uid
    assert [r["action"] for r in client.get("/api/admin/audit?action=package").json()] == \
        ["package_archive", "package_create"]
    assert len(client.get(f"/api/admin/audit?target_id={uid}").json()) == 4
    with SessionLocal() as db:
        assert db.query(AdminAudit).count() == 6


def test_panel_is_hidden_from_everyone_else(anon, ben):
    uid, b = ben
    for method, path in (("get", "/api/admin/overview"), ("get", f"/api/admin/users/{uid}"),
                         ("get", "/api/admin/packages"), ("post", "/api/admin/packages"),
                         ("get", "/api/admin/subscriptions"), ("get", "/api/admin/payments"),
                         ("post", "/api/admin/payments"), ("get", "/api/admin/usage"),
                         ("get", "/api/admin/usage.csv"), ("get", "/api/admin/audit"),
                         ("post", f"/api/admin/users/{uid}/grants"), ("post", f"/api/admin/users/{uid}/demo"),
                         ("post", f"/api/admin/users/{uid}/subscription")):
        r = getattr(b, method)(path, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 404, path
    assert anon.get("/api/admin/packages").status_code == 401
    assert anon.get("/api/account/usage").status_code == 401
    with SessionLocal() as db:
        assert db.get(User, uid).is_admin is False
