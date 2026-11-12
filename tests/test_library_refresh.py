from study import db
from study.documents import import_folder, refresh_library


def test_folder_refresh_discovers_changes(isolated):
    folder = isolated / "library"
    folder.mkdir()
    file = folder / "lecture.txt"
    file.write_text("Перший текст", "utf-8")
    import_folder(folder, "theory", "Новий предмет")
    old = db.one("SELECT id FROM documents")["id"]
    file.write_text("Другий текст", "utf-8")
    (folder / "new.txt").write_text("Нова лекція", "utf-8")
    refresh_library()
    assert (
        db.one("SELECT status FROM documents WHERE id=?", (old,))["status"]
        == "superseded"
    )
    assert len(db.query("SELECT id FROM documents WHERE status!='superseded'")) == 2
