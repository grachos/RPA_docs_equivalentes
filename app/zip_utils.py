"""Extract the PDF and XML from the (possibly nested) AttachedDocument ZIP."""
from __future__ import annotations

import re
import zipfile
from pathlib import Path


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> list[Path]:
    """Extract members, rejecting paths that escape `dest` (zip-slip)."""
    dest = dest.resolve()
    out: list[Path] = []
    for member in zf.infolist():
        if member.is_dir():
            continue
        target = (dest / member.filename).resolve()
        if dest not in target.parents:
            raise ValueError(f"Ruta insegura en ZIP: {member.filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(target, "wb") as dst:
            dst.write(src.read())
        out.append(target)
    return out


def extract_pdf_and_xml(zip_path: Path, dest: Path, max_depth: int = 3) -> tuple[Path, Path]:
    """Recursively unzip until a .pdf and a .xml are found. Returns (pdf, xml)."""
    dest.mkdir(parents=True, exist_ok=True)
    pending: list[tuple[Path, int]] = [(Path(zip_path), 0)]
    pdfs: list[Path] = []
    xmls: list[Path] = []
    while pending:
        current, depth = pending.pop(0)
        if depth > max_depth:
            raise ValueError("ZIP anidado demasiado profundo")
        if not zipfile.is_zipfile(current):
            raise ValueError(f"No es un ZIP válido: {current.name}")
        with zipfile.ZipFile(current) as zf:
            sub = dest / f"_unz{depth}_{current.stem}"
            for f in _safe_extract(zf, sub):
                suffix = f.suffix.lower()
                if suffix == ".zip":
                    pending.append((f, depth + 1))
                elif suffix == ".pdf":
                    pdfs.append(f)
                elif suffix == ".xml":
                    xmls.append(f)
    if len(pdfs) != 1 or len(xmls) != 1:
        raise ValueError(f"Se esperaba 1 PDF y 1 XML; hallados {len(pdfs)} PDF y {len(xmls)} XML")
    return pdfs[0], xmls[0]


def safe_folder_name(provider_id: str, provider_name: str) -> str:
    """'11111111', 'ANA MARIA PEREZ LOPEZ' -> '11111111_ANA_MARIA_PEREZ_LOPEZ'."""
    name = re.sub(r"[^\w]+", "_", provider_name.strip(), flags=re.UNICODE).strip("_")
    return f"{provider_id}_{name}"[:120]
