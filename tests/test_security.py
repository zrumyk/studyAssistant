import json
import pytest
from study import config, cloud, db
from study.runtime import dependencies


@pytest.mark.parametrize(
    "path", ["../outside.txt", "C:/Windows/win.ini", "a/../../outside", "/etc/passwd"]
)
def test_path_escape(isolated, path):
    with pytest.raises(ValueError):
        config.safe_path(isolated, path)


def test_csrf_and_origin(client):
    assert client.post("/api/backup", headers={"x-study-token": ""}).status_code == 403
    assert (
        client.get(
            "/api/settings", headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/session", headers={"sec-fetch-site": "cross-site"}).status_code
        == 403
    )


def test_host_header(client):
    assert (
        client.get("/api/session", headers={"host": "evil.example"}).status_code == 400
    )


@pytest.mark.parametrize(
    "name",
    [
        "numpy;whoami",
        "--index-url=x",
        "https://evil/x.whl",
        "$(whoami)",
        "../x",
        "a\nb",
    ],
)
def test_dependency_injection(name):
    with pytest.raises(ValueError):
        dependencies("python", [name])


def test_cloud_budget_and_one_shot(client, project, monkeypatch):
    monkeypatch.setattr(cloud, "key", lambda *args: "test-key")
    monkeypatch.setattr(
        cloud.httpx,
        "Client",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    quote = cloud.quote(project["id"], "Короткий запит", "Тест")
    assert (
        db.one("SELECT state FROM cloud_requests WHERE id=?", (quote["id"],))["state"]
        == "quoted"
    )
    cloud.approve_and_enqueue(quote["id"])
    with pytest.raises(RuntimeError):
        cloud.send(quote["id"])
    assert (
        db.one("SELECT state FROM cloud_requests WHERE id=?", (quote["id"],))["state"]
        == "uncertain"
    )
    with pytest.raises(ValueError):
        cloud.send(quote["id"])
    config.save_settings({"cloud_budget": 0})
    with pytest.raises(ValueError):
        cloud.quote(project["id"], "test", "test")


def test_cloud_never_sends_during_quote(client, project, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("Network called before consent")

    monkeypatch.setattr(cloud.httpx, "Client", fail)
    cloud.quote(project["id"], "text", "purpose")


def test_private_download_url_rejected():
    from study.network import public_url

    with pytest.raises(ValueError):
        public_url("https://127.0.0.1/file")


def test_secret_not_returned_in_settings(client, monkeypatch):
    monkeypatch.setattr(cloud, "key", lambda: "very-secret-key")
    r = client.get("/api/settings")
    assert r.json()["has_cloud_key"] is True
    assert "very-secret-key" not in r.text
