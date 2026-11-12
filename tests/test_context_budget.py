from study import search


def test_context_preserves_complete_variant_table_chunks(monkeypatch):
    first = "Варіант | довжина | ширина\n2 | 5 | 8\n" + "Теорія " * 330
    second = "Варіант | температура\n3 | 20\n" + "Пояснення " * 250
    hits = [
        {
            "document_id": "one",
            "name": "task.pdf",
            "locator": "сторінка 1",
            "role": "task",
            "content": first,
        },
        {
            "document_id": "two",
            "name": "theory.pdf",
            "locator": "сторінка 2",
            "role": "theory",
            "content": second,
        },
    ]
    monkeypatch.setattr(search, "search", lambda *a, **k: hits)
    result = search.context("project", "варіант 2")
    assert first in result
    assert "[ДЖЕРЕЛО one" in result
    assert "[ДЖЕРЕЛО two" not in result
    assert second not in result
    assert "Це не повний текст матеріалів" in result
