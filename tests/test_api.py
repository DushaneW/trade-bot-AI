"""The HTTP API through FastAPI's test client, with a throwaway data folder."""
import pytest
from fastapi.testclient import TestClient

from quorum.api import create_app

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def client(tmp_path, monkeypatch, password=""):
    monkeypatch.setenv("QUORUM_PASSWORD", password)
    return TestClient(create_app(tmp_path, tmp_path / "strategies"))


@pytest.fixture
def open_api(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch) as c:
        yield c


def test_state_and_catalog_describe_a_fresh_install(open_api):
    state = open_api.get("/api/state").json()
    assert state["running"] is False and state["bots"] == [] and state["auth"] is False
    assert state["setup"]["step"] == "market"
    catalog = open_api.get("/api/catalog").json()
    assert set(catalog["profiles"]) == {"careful", "balanced", "bold"}
    assert {s["key"] for s in catalog["strategies"]} >= {"trend_rider", "funding"}


def test_export_is_an_empty_csv_with_headers(open_api):
    r = open_api.get("/api/export/trades.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.strip() == "time_utc,trader,account,market,side,quantity,price,value,fee,result,reason"


def test_settings_include_the_backup_stop_switch(open_api):
    assert open_api.get("/api/settings").json()["exchange_stops"] is True
    assert open_api.post("/api/settings", json={"exchange_stops": False}).json()["exchange_stops"] is False


def test_unknown_bots_are_404(open_api):
    assert open_api.get("/api/bots/nope").status_code == 404


def test_password_protects_pages_api_and_websocket(tmp_path, monkeypatch):
    with client(tmp_path, monkeypatch, password="s3cret") as c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/", follow_redirects=False).headers["location"] == "/login"
        assert c.post("/login", content="password=nope", headers=FORM).status_code == 401
        r = c.post("/login", content="password=s3cret", headers=FORM, follow_redirects=False)
        assert r.status_code == 303 and "quorum_session" in r.cookies
        assert c.get("/api/state").json()["auth"] is True
        c.get("/logout")
        c.cookies.clear()
        assert c.get("/api/state").status_code == 401


def test_data_folder_can_be_chosen(tmp_path, monkeypatch):
    with client(tmp_path / "elsewhere", monkeypatch) as c:
        assert c.get("/api/state").status_code == 200
    assert (tmp_path / "elsewhere" / "quorum.db").exists()
    assert (tmp_path / "elsewhere" / "secret.key").exists()


def test_health_answers_without_a_password_and_tells_nothing_else(tmp_path, monkeypatch):
    from quorum import __version__

    with client(tmp_path, monkeypatch, password="s3cret") as c:
        assert c.get("/api/health").json() == {"ok": True, "version": __version__}
