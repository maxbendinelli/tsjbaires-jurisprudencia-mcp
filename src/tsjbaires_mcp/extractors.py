"""Extractores de texto para fallos en PDF (EJE), DOC binario (SGEJ), DOCX y RTF."""

from __future__ import annotations

import io
import re
import struct
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

import olefile
from pypdf import PdfReader

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"
_RTF_MAGIC = b"{\\rtf"


def clean_pdf_bytes(raw: bytes) -> bytes:
    """Elimina encabezados SOAP/MTOM previos a '%PDF-' y el boundary posterior a '%%EOF'."""
    start = raw.find(b"%PDF-")
    if start == -1:
        return raw
    end = raw.rfind(b"%%EOF")
    if end == -1 or end < start:
        return raw[start:]
    return raw[start : end + 5]


def detect_document_format(raw: bytes) -> str:
    """Detecta el formato del documento a partir de sus magic bytes."""
    if not raw:
        return "unknown"
    if b"%PDF-" in raw[:2048]:
        return "pdf"
    if raw.startswith(_OLE2_MAGIC):
        return "doc"
    if raw.startswith(_ZIP_MAGIC):
        return "docx"
    if raw.lstrip().startswith(_RTF_MAGIC):
        return "rtf"
    return "unknown"


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf_text(
    raw: bytes,
    pagina_desde: int | None = None,
    pagina_hasta: int | None = None,
) -> dict[str, Any]:
    """Extrae texto de un PDF (limpiando el envoltorio MTOM si existe)."""
    clean = clean_pdf_bytes(raw)
    reader = PdfReader(io.BytesIO(clean))
    total_paginas = len(reader.pages)

    desde = 1 if pagina_desde is None else pagina_desde
    hasta = total_paginas if pagina_hasta is None else pagina_hasta

    if total_paginas == 0:
        return {
            "formato": "pdf",
            "total_paginas": 0,
            "pagina_desde": 0,
            "pagina_hasta": 0,
            "texto": "",
        }

    if desde < 1 or desde > total_paginas:
        raise ValueError(
            f"pagina_desde={desde} fuera de rango (el documento tiene {total_paginas} páginas)."
        )
    if hasta < desde:
        raise ValueError(
            f"pagina_hasta={hasta} no puede ser menor que pagina_desde={desde}."
        )
    hasta = min(hasta, total_paginas)

    partes: list[str] = []
    for idx in range(desde - 1, hasta):
        page_text = reader.pages[idx].extract_text() or ""
        page_text = _normalize_whitespace(page_text)
        if page_text:
            partes.append(f"--- Página {idx + 1} ---\n{page_text}")
        else:
            partes.append(f"--- Página {idx + 1} ---\n[Sin texto extraíble en esta página]")

    return {
        "formato": "pdf",
        "total_paginas": total_paginas,
        "pagina_desde": desde,
        "pagina_hasta": hasta,
        "texto": "\n\n".join(partes),
    }


def _clean_word_text(raw_text: str) -> str:
    """Limpia códigos de campo de Word y caracteres de control del stream .doc."""
    # Resolver campos anidados simples/múltiples: \x13 instr \x14 result \x15 -> result
    out: list[str] = []
    field_stack: list[bool] = []  # True si ya vimos \x14 (en fase visible)
    for ch in raw_text:
        if ch == "\x13":
            field_stack.append(False)
        elif ch == "\x14":
            if field_stack:
                field_stack[-1] = True
        elif ch == "\x15":
            if field_stack:
                field_stack.pop()
        else:
            if not field_stack or field_stack[-1]:
                out.append(ch)

    cleaned = "".join(out)
    # Normalizar separadores Word
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n")
    cleaned = cleaned.replace("\x07", "\t").replace("\x0c", "\n\n")
    # Eliminar otros caracteres de control excepto \n y \t
    cleaned = re.sub(r"[\x00-\x08\x0e-\x1f\x7f]", "", cleaned)
    # Limpiar tabs sobrantes al final de línea
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    return _normalize_whitespace(cleaned)


def _extract_doc_fib_clx(word_stream: bytes, table_stream: bytes) -> str | None:
    """Extrae texto desde WordDocument + 0Table/1Table usando la tabla de piezas Clx/Pcdt."""
    if len(word_stream) < 0x01AA:
        return None

    w_ident = struct.unpack_from("<H", word_stream, 0x0000)[0]
    if w_ident != 0xA5EC:
        return None

    flags = struct.unpack_from("<H", word_stream, 0x000A)[0]
    if flags & 0x0100:  # fEncrypted
        raise ValueError("El archivo .doc está cifrado y no puede extraerse.")

    ccp_text = struct.unpack_from("<I", word_stream, 0x004C)[0]
    ccp_ftn = struct.unpack_from("<I", word_stream, 0x0050)[0]
    fc_clx = struct.unpack_from("<I", word_stream, 0x01A2)[0]
    lcb_clx = struct.unpack_from("<I", word_stream, 0x01A6)[0]

    if lcb_clx == 0 or fc_clx + lcb_clx > len(table_stream):
        return None

    clx = table_stream[fc_clx : fc_clx + lcb_clx]
    pos = 0
    pcdt_data: bytes | None = None
    while pos < len(clx):
        clxt = clx[pos]
        pos += 1
        if clxt == 0x01:  # Grpprl
            if pos + 2 > len(clx):
                break
            cb = struct.unpack_from("<H", clx, pos)[0]
            pos += 2 + cb
        elif clxt == 0x02:  # Pcdt
            if pos + 4 > len(clx):
                break
            lcb = struct.unpack_from("<I", clx, pos)[0]
            pos += 4
            pcdt_data = clx[pos : pos + lcb]
            break
        else:
            break

    if not pcdt_data or len(pcdt_data) < 16:
        return None

    # PlcPcd: (n + 1) CPs de 4 bytes seguidos de n PCDs de 8 bytes -> 4 + 12*n = len
    n = (len(pcdt_data) - 4) // 12
    if n <= 0:
        return None

    cps = struct.unpack_from(f"<{n + 1}I", pcdt_data, 0)
    pcd_offset = (n + 1) * 4

    chunks: list[str] = []
    for i in range(n):
        cp_start = cps[i]
        cp_end = cps[i + 1]
        char_count = cp_end - cp_start
        if char_count <= 0:
            continue

        # Cada PCD es 8 bytes: uint16 flags, uint32 fc, uint16 prm
        fc_raw = struct.unpack_from("<I", pcdt_data, pcd_offset + i * 8 + 2)[0]
        compressed = bool(fc_raw & 0x40000000)
        fc = fc_raw & 0x3FFFFFFF

        if compressed:
            byte_start = fc // 2
            byte_end = byte_start + char_count
            piece_bytes = word_stream[byte_start:byte_end]
            chunks.append(piece_bytes.decode("cp1252", errors="replace"))
        else:
            byte_start = fc
            byte_end = byte_start + char_count * 2
            piece_bytes = word_stream[byte_start:byte_end]
            chunks.append(piece_bytes.decode("utf-16-le", errors="replace"))

    full = "".join(chunks)
    if not full:
        return None

    main_text = full[:ccp_text] if ccp_text > 0 else full
    if ccp_ftn > 0 and len(full) > ccp_text:
        footnotes = full[ccp_text : ccp_text + ccp_ftn]
        cleaned_main = _clean_word_text(main_text)
        cleaned_ftn = _clean_word_text(footnotes)
        if cleaned_ftn:
            return f"{cleaned_main}\n\n--- Notas ---\n{cleaned_ftn}"
        return cleaned_main

    return _clean_word_text(main_text)


def _extract_doc_fallback(word_stream: bytes) -> str:
    """Respaldo por heurística si la tabla de piezas Clx no es estándar."""
    runs: list[str] = []
    for m in re.finditer(rb"[\x20-\x7e\xa0-\xff\r\n\t]{20,}", word_stream):
        text = m.group(0).decode("cp1252", errors="ignore")
        if sum(c.isalpha() for c in text) >= 10:
            runs.append(text)
    return _clean_word_text("\n".join(runs))


def extract_doc_text(raw: bytes) -> dict[str, Any]:
    """Extrae texto de un archivo Microsoft Word binario (.doc OLE2) en Python puro."""
    with olefile.OleFileIO(io.BytesIO(raw)) as ole:
        if not ole.exists("WordDocument"):
            raise ValueError("El archivo OLE2 no contiene un stream 'WordDocument'.")
        word_stream = ole.openstream("WordDocument").read()

        flags = struct.unpack_from("<H", word_stream, 0x000A)[0] if len(word_stream) >= 12 else 0
        table_name = "1Table" if (flags & 0x0200) else "0Table"
        table_stream = (
            ole.openstream(table_name).read() if ole.exists(table_name) else b""
        )

    texto = None
    if table_stream:
        texto = _extract_doc_fib_clx(word_stream, table_stream)
    if not texto:
        texto = _extract_doc_fallback(word_stream)

    return {
        "formato": "doc",
        "total_paginas": None,
        "pagina_desde": None,
        "pagina_hasta": None,
        "texto": texto,
    }


def extract_docx_text(raw: bytes) -> dict[str, Any]:
    """Extrae texto de un archivo .docx (OOXML)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("El archivo ZIP/DOCX no contiene 'word/document.xml'.")
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for p in root.iter(f"{ns}p"):
        texts = [t.text for t in p.iter(f"{ns}t") if t.text]
        if texts:
            paragraphs.append("".join(texts))

    return {
        "formato": "docx",
        "total_paginas": None,
        "pagina_desde": None,
        "pagina_hasta": None,
        "texto": _normalize_whitespace("\n".join(paragraphs)),
    }


def extract_rtf_text(raw: bytes) -> dict[str, Any]:
    """Extrae texto básico de un documento RTF."""
    text = raw.decode("cp1252", errors="replace")
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", errors="replace"), text)
    text = re.sub(r"\\par[d]?\b", "\n", text)
    text = re.sub(r"\\line\b", "\n", text)
    text = re.sub(r"\\tab\b", "\t", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("{", "").replace("}", "")
    return {
        "formato": "rtf",
        "total_paginas": None,
        "pagina_desde": None,
        "pagina_hasta": None,
        "texto": _normalize_whitespace(text),
    }


def extract_document_text(
    raw: bytes,
    pagina_desde: int | None = None,
    pagina_hasta: int | None = None,
    max_caracteres: int | None = 40000,
) -> dict[str, Any]:
    """Detecta el formato del documento y extrae su texto con soporte de truncado."""
    fmt = detect_document_format(raw)
    if fmt == "pdf":
        result = extract_pdf_text(raw, pagina_desde=pagina_desde, pagina_hasta=pagina_hasta)
    elif fmt == "doc":
        result = extract_doc_text(raw)
    elif fmt == "docx":
        result = extract_docx_text(raw)
    elif fmt == "rtf":
        result = extract_rtf_text(raw)
    else:
        raise ValueError(
            f"Formato de documento no soportado o respuesta inválida (primeros bytes: {raw[:32]!r})."
        )

    full_text = result["texto"]
    total_chars = len(full_text)
    truncated = False
    if max_caracteres is not None and max_caracteres > 0 and total_chars > max_caracteres:
        full_text = (
            full_text[:max_caracteres].rstrip()
            + f"\n\n[... texto truncado a {max_caracteres} de {total_chars} caracteres ...]"
        )
        truncated = True

    result["texto"] = full_text
    result["caracteres_totales"] = total_chars
    result["caracteres_devueltos"] = len(full_text)
    result["truncado"] = truncated
    return result

