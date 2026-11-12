import json
from study import db, config
from study.documents import import_file, chunk_page, UniversalParser
from study.search import search


def test_pdf_with_native_text_still_reads_image_table(isolated, monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from study import documents

    page = SimpleNamespace(
        extract_text=lambda: "Теоретичні відомості " * 30,
        images=[SimpleNamespace(image=Image.new("RGB", (800, 400)))],
    )
    monkeypatch.setattr("pypdf.PdfReader", lambda path: SimpleNamespace(pages=[page]))
    monkeypatch.setattr(documents, "render_pdf", lambda path, number, out: out)
    monkeypatch.setattr(
        documents,
        "ocr",
        lambda image: ("Варіант | довжина\n7 | 12", "OCR потребує перевірки"),
    )
    source = isolated / "mixed.pdf"
    source.write_bytes(b"%PDF-1.4")
    pages, warnings, _ = UniversalParser().parse(source)
    assert "Теоретичні відомості" in pages[0][1]
    assert "7 | 12" in pages[0][1]
    assert any("містить зображення" in warning for warning in warnings)


def test_import_deduplicates_and_preserves_locator(isolated, client, project):
    p = isolated / "input.txt"
    p.write_text(
        "Завдання\nВаріант 7: ширина 12 та висота 15\nОбчислити площу", "utf-8"
    )
    did = import_file(p, "task", "Приклад", project["id"])
    assert import_file(p, "task", "Приклад", project["id"]) == did
    hits = search(project["id"], "варіант 7 ширина", semantic=False)
    assert hits[0]["document_id"] == did
    assert hits[0]["locator"] == "Текст"
    assert "12" in hits[0]["content"]


def test_reference_is_separate_role(isolated, client, project):
    p = isolated / "reference.txt"
    p.write_text("Титульний аркуш\nІван\nТочність 99%", "utf-8")
    did = import_file(p, "reference", "Приклад", project["id"])
    assert (
        db.one("SELECT role FROM documents WHERE id=?", (did,))["role"] == "reference"
    )


def test_other_subject_not_retrieved(isolated, client, project):
    p = isolated / "other.txt"
    p.write_text("Секретні вихідні дані іншого предмета", "utf-8")
    import_file(p, "data", "Інший")
    assert search(project["id"], "дані", semantic=False) == []


def test_large_page_keeps_header():
    chunks = list(
        chunk_page("№ | Параметр | Значення\n" + ("7 | довжина | 12\n" * 500))
    )
    assert len(chunks) > 1
    assert all("Параметр" in c for c in chunks)


def test_file_update_keeps_original_snapshot(isolated, client, project):
    p = isolated / "theory.txt"
    p.write_text("Початковий текст", "utf-8")
    a = import_file(p, "theory", "Приклад", project["id"])
    p.write_text("Оновлений текст", "utf-8")
    b = import_file(p, "theory", "Приклад", project["id"])
    assert a != b
    assert (
        "Початковий"
        in db.one("SELECT content FROM chunks WHERE document_id=?", (a,))["content"]
    )


def test_result_upload_does_not_revoke_approval(isolated, client, project, plan):
    from study.engine import save_plan, approve

    pid = project["id"]
    save_plan(pid, plan)
    approve(pid)
    p = isolated / "result.txt"
    p.write_text("Мій результат", "utf-8")
    import_file(p, "result", "Приклад", pid)
    assert db.project(pid)["approved_hash"]


def test_binary_result_saved_without_false_text(isolated, client, project):
    p = isolated / "drawing.dwg"
    p.write_bytes(b"AC1032\x00test")
    did = import_file(p, "result", "Приклад", project["id"])
    doc = db.one("SELECT * FROM documents WHERE id=?", (did,))
    assert doc["status"] == "needs_review"
    assert not db.query("SELECT id FROM chunks WHERE document_id=?", (did,))
    assert (
        config.project_dir(project["id"]) / "workspace/inputs" / did / "drawing.dwg"
    ).read_bytes() == p.read_bytes()


def test_report_context_excludes_other_students_content(isolated, client, project):
    from study.search import context

    ref = isolated / "other.txt"
    ref.write_text("Результати: чужа точність 99%", "utf-8")
    theory = isolated / "ours.txt"
    theory.write_text("Результати потрібно перевіряти", "utf-8")
    import_file(ref, "reference", "Приклад", project["id"])
    import_file(theory, "theory", "Приклад", project["id"])
    text = context(project["id"], "Результати", 2, exclude_reference=True)
    assert "99%" not in text
    assert "перевіряти" in text
