import html
from . import config, db


def workflow(pid, sid):
    from .engine import artifact

    p = db.project(pid)
    titles = [s["title"] for s in p["plan"]["steps"]]
    root = config.project_dir(pid) / "workspace"
    root.mkdir(exist_ok=True)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="{len(titles) * 100 + 40}" viewBox="0 0 800 {len(titles) * 100 + 40}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#164e63"/></marker></defs>',
    ]
    mermaid = ["flowchart TD"]
    dot = ["digraph workflow {", "rankdir=TB;", "node [shape=box];"]
    import json

    for i, title in enumerate(titles):
        y = 20 + i * 100
        parts.append(
            f'<rect x="60" y="{y}" width="680" height="65" rx="12" fill="#ecfeff" stroke="#164e63"/>'
        )
        # Wrap labels instead of clipping long text.
        import textwrap

        lines = textwrap.wrap(f"{i + 1}. {title}", width=66)[:3]
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="400" y="{y + 23 + j * 18}" text-anchor="middle" font-family="Arial" font-size="16">{html.escape(line)}</text>'
            )
        if i < len(titles) - 1:
            parts.append(
                f'<path d="M400,{y + 65} L400,{y + 94}" stroke="#164e63" marker-end="url(#arrow)"/>'
            )
        mermaid.append(f' n{i}["{html.escape(title, quote=True)}"]')
        dot.append(f" n{i} [label={json.dumps(title, ensure_ascii=False)}];")
        if i:
            mermaid.append(f" n{i - 1} --> n{i}")
            dot.append(f" n{i - 1} -> n{i};")
    parts.append("</svg>")
    dot.append("}")
    outputs = {
        "workflow.svg": "\n".join(parts),
        "workflow.mmd": "\n".join(mermaid),
        "workflow.dot": "\n".join(dot),
    }
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path

    image = Image.new("RGB", (1000, len(titles) * 125 + 50), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = (
        ImageFont.truetype(str(font_path), 20)
        if font_path.exists()
        else ImageFont.load_default(size=20)
    )
    import textwrap

    for i, title in enumerate(titles):
        y = 25 + i * 125
        draw.rounded_rectangle(
            (60, y, 940, y + 85), radius=15, fill="#ecfeff", outline="#164e63", width=2
        )
        for j, line in enumerate(textwrap.wrap(f"{i + 1}. {title}", width=70)[:3]):
            draw.text(
                (500, y + 14 + j * 24), line, font=font, anchor="mt", fill="#164e63"
            )
        if i < len(titles) - 1:
            draw.line((500, y + 85, 500, y + 116), fill="#164e63", width=2)
            draw.polygon(
                [(495, y + 110), (505, y + 110), (500, y + 119)], fill="#164e63"
            )
    image.save(root / "workflow.png")
    artifact(
        pid,
        sid,
        "workflow.png",
        "created",
        {"type": "diagram", "basis": "approved_plan"},
    )
    for name, content in outputs.items():
        (root / name).write_text(content, "utf-8")
        artifact(
            pid, sid, name, "created", {"type": "diagram", "basis": "approved_plan"}
        )
    return {
        "files": list(outputs) + ["workflow.png"],
        "explanation": "Блок-схема погодженого плану. Діаграми алгоритмів також можна створювати кодом у кроках code.",
    }
