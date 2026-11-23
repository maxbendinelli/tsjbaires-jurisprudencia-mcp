"""Tests unitarios para extractores y limpieza de PDF/MTOM."""

import pytest

from tsjbaires_mcp.extractors import (
    clean_pdf_bytes,
    detect_document_format,
    extract_document_text,
)


def _build_minimal_pdf(text: str = "Tribunal Superior de Justicia CABA") -> bytes:
    """Construye un PDF mínimo válido de 1 página para pruebas unitarias."""
    stream_content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        (
            f"4 0 obj\n<< /Length {len(stream_content)} >>\nstream\n".encode("ascii")
            + stream_content
            + b"\nendstream\nendobj\n"
        ),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode(
            "ascii"
        )
    )
    return bytes(out)


def test_clean_pdf_bytes_strips_mtom_wrapper():
    pdf = _build_minimal_pdf("Sentencia de prueba")
    wrapped = (
        b"\nContent-Type: application/octet-stream\r\n"
        b"Content-Transfer-Encoding: binary\r\n"
        b"Content-ID: <abc@iurix>\r\n\r\n"
        + pdf
        + b"\r\n--uuid:12345--"
    )
    assert detect_document_format(wrapped) == "pdf"
    cleaned = clean_pdf_bytes(wrapped)
    assert cleaned.startswith(b"%PDF-")
    assert cleaned.endswith(b"%%EOF")
    assert cleaned == pdf


def test_extract_pdf_text_and_truncation():
    pdf = _build_minimal_pdf("Amparo colectivo en la Ciudad de Buenos Aires")
    wrapped = b"\r\nContent-Type: application/octet-stream\r\n\r\n" + pdf + b"\r\n--uuid:end--"
    res = extract_document_text(wrapped, max_caracteres=25)
    assert res["formato"] == "pdf"
    assert res["total_paginas"] == 1
    assert res["pagina_desde"] == 1
    assert res["pagina_hasta"] == 1
    assert res["truncado"] is True
    assert res["caracteres_totales"] > 25
    assert "Amparo" in res["texto"]


def test_detect_formats_and_unknown():
    assert detect_document_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest") == "doc"
    assert detect_document_format(b"PK\x03\x04rest") == "docx"
    assert detect_document_format(b"{\\rtf1 hello}") == "rtf"
    assert detect_document_format(b"<html>error</html>") == "unknown"

    with pytest.raises(ValueError, match="no soportado"):
        extract_document_text(b"<html>not a doc</html>")

