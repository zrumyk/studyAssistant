import json
import httpx
import pytest
from study import error_docs,network


def test_reads_and_caches_official_error_section_without_executing_html(monkeypatch):
    calls=[]
    def fetch(url,destination,**kwargs):
        calls.append(url)
        assert 'tensorflow.org' in kwargs['allowed_hosts']
        destination.write_text('<html><head>IGNORED_HEAD</head><body><p>Intro</p><h2 id="creating_tfvariables">Creating variables</h2><p>ACTUAL_API_REFERENCE</p><script>NEVER_EXECUTE_SCRIPT</script></body></html>','utf-8')
    monkeypatch.setattr(error_docs,'fetch',fetch)
    failure=json.dumps({'log':'See https://www.tensorflow.org/guide/function#creating_tfvariables for details.'})
    text=error_docs.runtime_documentation(failure)
    assert 'ACTUAL_API_REFERENCE' in text and 'NEVER_EXECUTE_SCRIPT' not in text and 'IGNORED_HEAD' not in text
    assert error_docs.runtime_documentation(failure)==text and len(calls)==1


def test_unknown_hosts_queries_credentials_and_malformed_ports_are_not_fetched(monkeypatch):
    monkeypatch.setattr(error_docs,'fetch',lambda *a,**k:pytest.fail('Must not fetch unapproved URLs'))
    for url in ('https://outside.example/page','https://www.tensorflow.org/guide/function?token=private','https://user:secret@www.tensorflow.org/guide/function','https://www.tensorflow.org:bad/guide/function'):
        assert error_docs.runtime_documentation(json.dumps({'log':url}))==''


def test_docs_redirect_cannot_leave_allowed_hosts(isolated,monkeypatch):
    calls=[]
    monkeypatch.setattr(network.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('93.184.216.34',443))])
    def handler(request):
        calls.append(request)
        return httpx.Response(302,headers={'Location':'https://outside.example/steal'})
    original=httpx.Client
    monkeypatch.setattr(network.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError,match='межі дозволених'):network.fetch('https://docs.python.org/3/library/json.html',isolated/'doc.html',allowed_hosts={'docs.python.org'})
    assert len(calls)==1
