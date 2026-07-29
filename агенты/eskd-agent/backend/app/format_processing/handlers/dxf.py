"""DXF: PNG схемы + текст сущностей."""

from __future__ import annotations

import io
from pathlib import Path

import ezdxf
from ezdxf.entities import Attrib, Insert, MText, Text

from app.format_processing.types import ProcessedArtifact


def _collect_text(doc: ezdxf.document.Drawing) -> str:
    lines: list[str] = []
    msp = doc.modelspace()
    for entity in msp:
        t = None
        if isinstance(entity, Text):
            t = entity.dxf.text
        elif isinstance(entity, MText):
            t = entity.plain_text()
        elif isinstance(entity, Insert):
            for sub in entity.attribs:
                if isinstance(sub, Attrib):
                    tag = str(sub.dxf.tag or "")
                    val = str(sub.dxf.text or "").strip()
                    if val:
                        lines.append(f"{tag}: {val}" if tag else val)
            continue
        if t:
            s = str(t).strip()
            if s:
                lines.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return "\n".join(out)


def _render_png(doc: ezdxf.document.Drawing, *, dpi: int = 150) -> bytes | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    except ImportError:
        return None

    fig = plt.figure(figsize=(11.69, 8.27), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    Frontend(ctx, backend).draw_layout(doc.modelspace())
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
    return buf.getvalue()


def process_dxf(data: bytes, *, source: str) -> list[ProcessedArtifact]:
    doc = ezdxf.readfile(io.BytesIO(data))
    stem = Path(source.replace("\\", "/")).stem
    artifacts: list[ProcessedArtifact] = []

    text = _collect_text(doc)
    if text:
        artifacts.append(
            ProcessedArtifact(
                source=source,
                name=f"{stem}.extracted.txt",
                kind="text",
                data=text.encode("utf-8"),
                mime="text/plain; charset=utf-8",
                format="dxf",
            )
        )

    png = _render_png(doc)
    if png:
        artifacts.append(
            ProcessedArtifact(
                source=source,
                name=f"{stem}_scheme.png",
                kind="image",
                data=png,
                mime="image/png",
                format="dxf",
            )
        )
    elif not text:
        raise ValueError("DXF: не удалось извлечь ни текст, ни PNG (нужен matplotlib)")

    return artifacts
