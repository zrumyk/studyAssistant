import httpx
import pytest
from study.providers import OllamaProvider
from study import config


def test_remote_alias_is_blocked_before_private_payload():
    calls = []

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "remote_host": "https://ollama.com",
                "remote_model": "large",
                "model_info": {},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(RuntimeError):
            OllamaProvider().ensure_local(client, "local-looking-alias")
    assert calls == ["/api/show"]


def test_cloud_name_not_allowed_in_settings():
    with pytest.raises(ValueError):
        config.save_settings({"model": "qwen3.5:cloud"})


def test_actual_local_metadata_accepted():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"model_info": {"general.architecture": "qwen3"}}
            )
        )
    ) as client:
        assert OllamaProvider().ensure_local(client, "qwen3:4b")["model_info"]
