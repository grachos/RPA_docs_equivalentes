import io
import zipfile

import pytest

from app.zip_utils import extract_pdf_and_xml, safe_folder_name


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_nested_zip(tmp_path):
    inner = _zip_bytes({"ad0819.pdf": b"%PDF", "ad0819.xml": b"<x/>"})
    outer = tmp_path / "1234.tmp.zip"
    outer.write_bytes(_zip_bytes({"DE25_2264.zip": inner}))
    pdf, xml = extract_pdf_and_xml(outer, tmp_path / "out")
    assert pdf.read_bytes() == b"%PDF" and xml.read_bytes() == b"<x/>"


def test_zip_slip_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip_bytes({"../evil.pdf": b"x"}))
    with pytest.raises(ValueError):
        extract_pdf_and_xml(bad, tmp_path / "out")


def test_missing_xml(tmp_path):
    z = tmp_path / "z.zip"
    z.write_bytes(_zip_bytes({"a.pdf": b"x"}))
    with pytest.raises(ValueError):
        extract_pdf_and_xml(z, tmp_path / "out")


def test_safe_folder_name():
    assert safe_folder_name("11111111", "ANA MARIA PEREZ LOPEZ") == "11111111_ANA_MARIA_PEREZ_LOPEZ"
