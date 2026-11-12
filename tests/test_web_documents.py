from study import documents, engine, db, search
from study.schemas import Action
import hashlib
from pathlib import Path
import pytest


def test_large_local_data_import_streams_hash_and_keeps_document_limit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(documents, "DOCUMENT_MAX_BYTES", 10)
    monkeypatch.setattr(documents, "DATA_MAX_BYTES", 100)
    payload = b"0123456789" * 4
    data = tmp_path / "measurements.npy"
    data.write_bytes(payload)

    def no_whole_file_read(*args, **kwargs):
        pytest.fail("Large data must not be loaded into RAM to calculate its digest")

    monkeypatch.setattr(Path, "read_bytes", no_whole_file_read)
    did = documents.import_file(data, "data")
    assert (
        db.one("SELECT hash FROM documents WHERE id=?", (did,))["hash"]
        == hashlib.sha256(payload).hexdigest()
    )
    with pytest.raises(ValueError, match="100 МБ"):
        documents.import_file(data, "theory")
    data.write_bytes(payload * 3)
    with pytest.raises(ValueError, match="3 ГБ"):
        documents.import_file(data, "result")


def test_html_import_reads_table_without_executing_scripts(tmp_path):
    page = tmp_path / "reference.html"
    page.write_text(
        "<h1>Варіанти</h1><style>invisible</style><script>send_private_files()</script>"
        "<table><tr><th>Варіант</th><th>a</th></tr><tr><td>2</td><td>8</td></tr></table>",
        "utf-8",
    )
    pages, warnings, profile = documents.UniversalParser().parse(page)
    text = pages[0][1]
    assert "Варіанти" in text and "2\t8" in text
    assert "send_private_files" not in text and "invisible" not in text


def test_approved_download_is_searchable_and_pinned(project, plan, monkeypatch):
    url = "https://docs.python.org/3/library/statistics.html"
    plan["steps"][0].update(kind="fetch", urls=[url], expected="Сторінка документації")
    engine.save_plan(project["id"], plan)
    engine.approve(project["id"])
    monkeypatch.setattr(
        engine,
        "prepare_action",
        lambda *args: Action(
            url=url,
            expected_files=["reference.html"],
            explanation="Завантажити погоджену документацію",
        ),
    )

    def download(address, destination, *, max_bytes=50_000_000):
        assert address == url
        assert max_bytes == 50_000_000
        destination.write_text(
            "<h1>Середнє</h1><p>Функція mean обчислює середнє значення.</p>", "utf-8"
        )
        return {
            "url": address,
            "bytes": destination.stat().st_size,
            "content_type": "text/html",
        }

    monkeypatch.setattr("study.network.fetch", download)
    engine.execute_project(project["id"])
    saved = db.project(project["id"])
    assert saved["status"] == "completed"
    did = saved["steps"][0]["result"]["document_id"]
    assert db.one(
        "SELECT document_id FROM source_pins WHERE project_id=? AND document_id=?",
        (project["id"], did),
    )
    hits = search.search(project["id"], "середнє", semantic=False)
    assert any(hit["document_id"] == did for hit in hits)
