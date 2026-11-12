import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
import httpx
import pytest
from study import cloud, config, db
from study.credentials import deepseek_key


def select_deepseek():
    config.save_settings(
        {
            "cloud_provider": "deepseek",
            "cloud_model": "deepseek-flash",
            "cloud_input_price": 0.3,
            "cloud_output_price": 1.2,
        }
    )


def mocked_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        cloud.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handler)),
    )


def test_env_is_read_on_demand_without_exporting_to_children(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=\n", "utf-8")
    assert deepseek_key() is None
    env.write_text('export DEEPSEEK_API_KEY="fake-test-secret" # local only\n', "utf-8")
    assert deepseek_key() == "fake-test-secret"
    env.write_text("DEEPSEEK_API_KEY='replacement-secret'\n", "utf-8")
    assert deepseek_key() == "replacement-secret"
    assert "DEEPSEEK_API_KEY" not in os.environ


@pytest.mark.parametrize(
    "text",
    [
        "DEEPSEEK_API_KEY=first\nDEEPSEEK_API_KEY=second",
        'DEEPSEEK_API_KEY="broken',
        "DEEPSEEK_API_KEY=bad value",
    ],
)
def test_malformed_env_rejected_without_echoing_values(tmp_path, monkeypatch, text):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(text, "utf-8")
    with pytest.raises(ValueError) as error:
        deepseek_key()
    assert "first" not in str(error.value) and "second" not in str(error.value)
    assert "bad value" not in str(error.value) and "broken" not in str(error.value)


def test_key_is_not_in_settings_quote_or_backup(client, project, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    secret = "fake-private-deepseek-secret"
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=" + secret, "utf-8")
    select_deepseek()
    settings = client.get("/api/settings")
    assert settings.json()["has_cloud_key"] is True
    assert secret not in settings.text
    q = cloud.quote(project["id"], "Test", "Purpose")
    row = db.one("SELECT * FROM cloud_requests WHERE id=?", (q["id"],))
    assert secret not in json.dumps(row)
    backup = client.post("/api/backup").json()
    with zipfile.ZipFile(backup["path"]) as archive:
        assert not any(name.endswith(".env") for name in archive.namelist())
        assert all(
            secret.encode() not in archive.read(name) for name in archive.namelist()
        )
    assert (
        client.put(
            "/api/settings",
            json={"cloud_provider": "deepseek", "cloud_api_key": secret},
        ).status_code
        == 400
    )


def test_quote_is_never_approval_even_when_key_exists(project, monkeypatch):
    select_deepseek()
    calls = []
    monkeypatch.setattr(cloud, "key", lambda *args: "test-secret")
    mocked_http(monkeypatch, lambda request: calls.append(request))
    q = cloud.quote(project["id"], "Test", "Purpose")
    assert q["provider"] == "deepseek"
    with pytest.raises(ValueError, match="окремий дозвіл"):
        cloud.send(q["id"])
    assert calls == []


def test_native_json_and_image_request_preserves_snapshot_and_budget(project,monkeypatch):
    from PIL import Image
    select_deepseek()
    path=config.DATA/'figure.png'
    Image.new('RGB',(30,40),'white').save(path)
    images=cloud.image_payloads([path])
    baseline=cloud.quote(project['id'],'Return json','structured')
    q=cloud.quote(project['id'],'Return json','figure',json_response=True,attachments=images)
    assert q['maximum_estimate_usd']-baseline['maximum_estimate_usd']==pytest.approx(1024*0.3/1e6)
    assert 'base64' not in json.dumps(q)
    cloud.approve_and_enqueue(q['id'])
    monkeypatch.setattr(cloud,'key',lambda *a:'test-secret')
    def handler(request):
        body=json.loads(request.content)
        assert body['response_format']=={'type':'json_object'}
        content=body['messages'][1]['content']
        assert content[0]['text']=='Return json'
        assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"description":"shape"}'},'finish_reason':'stop'}],
                                     'usage':{'prompt_tokens':100,'completion_tokens':20}})
    mocked_http(monkeypatch,handler)
    assert cloud.send(q['id'])['complete']


def test_cloud_image_must_be_valid_and_inside_data(isolated,tmp_path):
    outside=tmp_path.parent/'outside-private.txt'
    outside.write_text('not an image','utf-8')
    with pytest.raises(ValueError):
        cloud.image_payloads([outside])
    assert not db.query('SELECT id FROM cloud_requests')


def test_deepseek_uses_approved_provider_snapshot_and_one_request(project, monkeypatch):
    select_deepseek()
    q = cloud.quote(project["id"], "Approved exact prompt", "Repair code")
    cloud.approve_and_enqueue(q["id"])
    config.save_settings({"cloud_provider": "gemini", "cloud_model": "other-model"})
    providers = []
    monkeypatch.setattr(
        cloud, "key", lambda provider: providers.append(provider) or "test-secret"
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-flash"
        assert body["messages"][-1]["content"] == "Approved exact prompt"
        assert body["thinking"] == {"type": "disabled"}
        assert "api_key" not in body and "tools" not in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "corrected code"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 15},
                },
            },
        )

    mocked_http(monkeypatch, handler)
    result = cloud.send(q["id"])
    assert result["complete"] and result["provider"] == "deepseek"
    assert result["cost_usd"] == pytest.approx((100 * 0.3 + 20 * 1.2) / 1e6)
    assert providers == ["deepseek"]
    with pytest.raises(ValueError):
        cloud.send(q["id"])
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["timeout", "http500", "length"])
def test_failed_or_truncated_response_is_not_automatically_retried(
    project, monkeypatch, kind
):
    select_deepseek()
    monkeypatch.setattr(cloud, "key", lambda *args: "test-secret")
    q = cloud.quote(project["id"], "Test", "Purpose")
    cloud.approve_and_enqueue(q["id"])
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("secret must not leak", request=request)
        if kind == "http500":
            return httpx.Response(500, json={"error": "private server detail"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "partial code"}, "finish_reason": "length"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 50},
            },
        )

    mocked_http(monkeypatch, handler)
    if kind == "length":
        assert cloud.send(q["id"])["complete"] is False
    else:
        with pytest.raises(RuntimeError) as error:
            cloud.send(q["id"])
        assert "secret must not leak" not in str(error.value)
        row = db.one("SELECT state,actual FROM cloud_requests WHERE id=?", (q["id"],))
        assert row["state"] == "uncertain" and row["actual"] is None
    assert len(calls) == 1
    with pytest.raises(ValueError):
        cloud.send(q["id"])
    assert len(calls) == 1


def test_budget_checked_again_after_approval(project, monkeypatch):
    select_deepseek()
    monkeypatch.setattr(cloud, "key", lambda *args: "test-secret")
    q = cloud.quote(project["id"], "Test", "Purpose")
    cloud.approve_and_enqueue(q["id"])
    config.save_settings({"cloud_budget": 0})
    monkeypatch.setattr(
        cloud.httpx,
        "Client",
        lambda **kwargs: pytest.fail("Over-budget request reached network"),
    )
    with pytest.raises(ValueError, match="бюджет"):
        cloud.send(q["id"])


def test_old_gemini_quote_still_uses_gemini(project, monkeypatch):
    q = cloud.quote(project["id"], "Original", "Purpose")
    row = db.one("SELECT config FROM cloud_requests WHERE id=?", (q["id"],))
    old = json.loads(row["config"])
    old.pop("cloud_provider")
    db.execute(
        "UPDATE cloud_requests SET config=? WHERE id=?", (json.dumps(old), q["id"])
    )
    select_deepseek()
    cloud.approve_and_enqueue(q["id"])
    providers = []
    monkeypatch.setattr(
        cloud, "key", lambda provider: providers.append(provider) or "test-secret"
    )

    def handler(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.headers["x-goog-api-key"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "answer"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3},
            },
        )

    mocked_http(monkeypatch, handler)
    assert cloud.send(q["id"])["provider"] == "gemini"
    assert providers == ["gemini"]


def test_send_click_enqueues_one_approved_request(client, project):
    q = cloud.quote(project["id"], "Test", "Purpose")
    response = client.post("/api/cloud/" + q["id"] + "/send")
    assert response.status_code == 200
    assert (
        db.one("SELECT state FROM cloud_requests WHERE id=?", (q["id"],))["state"]
        == "approved"
    )
    assert client.post("/api/cloud/" + q["id"] + "/send").status_code == 400
    assert len(db.query("SELECT id FROM jobs WHERE kind='cloud'")) == 1


def test_gitignore_excludes_env_but_allows_empty_template(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        (config.ROOT / ".gitignore").read_text("utf-8"), "utf-8"
    )
    subprocess.run(
        ["git", "init", "--quiet", str(repo)], check=True, capture_output=True
    )
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "--no-index",
            "--",
            ".env",
            ".env.local",
            ".env.example",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert set(result.stdout.splitlines()) == {".env", ".env.local"}


def test_standing_authorization_requires_opt_in_and_request_cap(project):
    select_deepseek()
    q = cloud.quote(project["id"], "Test", "Purpose")
    with pytest.raises(ValueError, match="межами"):
        cloud.approve_and_enqueue(q["id"], standing=True)
    config.save_settings(
        {"cloud_consent_mode": "budgeted", "cloud_request_limit_usd": 0}
    )
    with pytest.raises(ValueError, match="межами"):
        cloud.approve_and_enqueue(q["id"], standing=True)
    assert (
        db.one("SELECT state FROM cloud_requests WHERE id=?", (q["id"],))["state"]
        == "quoted"
    )


def test_standing_authorization_can_be_revoked_before_send(project, monkeypatch):
    select_deepseek()
    config.save_settings(
        {"cloud_consent_mode": "budgeted", "cloud_request_limit_usd": 0.03}
    )
    q = cloud.quote(project["id"], "Test", "Purpose")
    cloud.approve_and_enqueue(q["id"], standing=True)
    config.save_settings({"cloud_consent_mode": "per_request"})
    monkeypatch.setattr(
        cloud.httpx,
        "Client",
        lambda **kw: pytest.fail("Revoked authorization reached network"),
    )
    with pytest.raises(ValueError, match="відкликано"):
        cloud.send(q["id"])


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_budget_or_price_rejected(value):
    with pytest.raises(ValueError):
        config.save_settings({"cloud_budget": value})
    with pytest.raises(ValueError):
        config.save_settings({"cloud_input_price": value})
