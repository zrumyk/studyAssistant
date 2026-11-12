import json
import httpx
import pytest
from study.providers import collect_response


def client_for(chunks):
    body = "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks)
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body.encode("utf-8"))
        )
    )


def test_only_complete_stream_returns_joined_unicode_and_usage():
    events = []
    with client_for(
        [
            {"message": {"content": "Привіт "}, "done": False},
            {"message": {"content": "світе"}, "done": False},
            {
                "message": {"content": "!"},
                "done": True,
                "done_reason": "stop",
                "eval_count": 5,
            },
        ]
    ) as client:
        result = collect_response(
            client, "http://localhost/api/chat", {}, lambda *args: events.append(args)
        )
    assert result["message"]["content"] == "Привіт світе!"
    assert result["eval_count"] == 5
    assert events[-1][0] == len("Привіт світе!")
    assert events[-1][-1] == "done"
    assert all("Привіт" not in str(event) for event in events)


@pytest.mark.parametrize(
    "chunks",
    [
        [{"message": {"content": "partial code"}, "done": False}],
        [{"error": "model unavailable"}],
    ],
)
def test_partial_stream_or_server_error_never_returns_executable_code(chunks):
    with client_for(chunks) as client:
        with pytest.raises(RuntimeError):
            collect_response(
                client, "http://localhost/api/chat", {}, lambda *args: None
            )
