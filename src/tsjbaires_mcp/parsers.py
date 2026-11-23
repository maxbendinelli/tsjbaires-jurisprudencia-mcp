"""Parsers HTML y normalizadores para el portal de Jurisprudencia del TSJ CABA."""

from __future__ import annotations

import html as html_lib
import math
import re
import unicodedata
from typing import Any

from bs4 import BeautifulSoup, Tag

DOWNLOAD_EJE_URL = (
    "https://www.tsjbaires.gov.ar/components/com_expedientes/expedientes_download_EJE.php"
)
DOWNLOAD_LEGACY_URL = (
    "https://www.tsjbaires.gov.ar/components/com_expedientes/expedientes_download.php"
)

_DOWNLOAD_PDF_RE = re.compile(
    r"downloadPDF\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)
_TESAURO_ONCLICK_RE = re.compile(
    r"busqPorTerminoTesauro\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)
_TOTAL_RE = re.compile(
    r"Se encontraron\s*(?:<[^>]+>)*\s*([\d.,]+)\s*(?:</[^>]+>)*\s*resultados",
    re.IGNORECASE,
)
_IR_PAGINA_RE = re.compile(r"irPagina\(\s*(\d+)\s*\)")
_MCLK_RE = re.compile(r"mClk\(\s*this\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")
_SUGERENCIA_RE = re.compile(
    r"cargarTerminoLista\(\s*(\d+)\s*,\s*'([^']*)'\s*\)"
)


def normalize_text(text: str) -> str:
    """Normaliza texto removiendo tildes y pasando a mayúsculas para búsquedas en tesauro."""
    nfd = unicodedata.normalize("NFD", text.strip())
    stripped = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", stripped).upper()


def build_download_url(
    origen: str,
    expediente: str,
    anio: str,
    incidente: str,
    movimiento: str,
) -> str:
    """Construye la URL directa de descarga según el origen ('EJE' o 'SGEJ'/legado)."""
    base = DOWNLOAD_EJE_URL if origen.strip().upper() == "EJE" else DOWNLOAD_LEGACY_URL
    return (
        f"{base}?expediente={expediente}&anio={anio}"
        f"&incidente={incidente}&movimiento={movimiento}"
    )


def _clean_cell_text(el: Tag | None) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _parse_int_or_none(val: Any) -> int | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.upper() == "NULL":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_tesauro_div(div: Tag) -> list[dict[str, str | None]]:
    terms: list[dict[str, str | None]] = []
    for link in div.find_all("a"):
        onclick = link.get("onclick") or ""
        m = _TESAURO_ONCLICK_RE.search(onclick)
        if not m:
            continue
        term_id = m.group(1).strip()
        term_name = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip() or m.group(2).strip()

        # Verificar si inmediatamente después viene un calificador ej: <span class="textoMuyChico">(RECHAZO)</span>
        calificador: str | None = None
        sib = link.next_sibling
        while sib is not None and isinstance(sib, str) and not sib.strip():
            sib = sib.next_sibling
        if isinstance(sib, Tag) and sib.name == "span":
            cal_txt = _clean_cell_text(sib)
            if cal_txt.startswith("(") and cal_txt.endswith(")"):
                calificador = cal_txt[1:-1].strip() or None
            elif cal_txt:
                calificador = cal_txt

        terms.append(
            {
                "id": term_id,
                "termino": term_name,
                "calificador": calificador,
            }
        )
    return terms


def _parse_vinculadas_div(div: Tag) -> dict[str, Any] | None:
    id_clasif = _parse_int_or_none(div.get("data-id-clasif"))
    if id_clasif is None:
        return None
    tipo = (div.get("data-tipo-vinculo") or "").strip()
    if tipo.upper() in ("", "NULL", "NONE"):
        tipo_norm: str | None = None
    else:
        tipo_norm = tipo

    return {
        "id_clasif": id_clasif,
        "expediente_id": (div.get("data-expediente-id") or "").strip() or None,
        "clasificacion_vinculada": _parse_int_or_none(div.get("data-clasificacion-vinculada")),
        "tipo_vinculo": tipo_norm,
    }


def parse_search_results(html: str, page: int, page_size: int) -> dict[str, Any]:
    """Parsea el HTML de resultadoBusqueda.asp y devuelve una estructura JSON limpia."""
    soup = BeautifulSoup(html, "html.parser")

    sin_res = soup.select_one("span.sinResultados")
    if sin_res is not None:
        return {
            "total_resultados": 0,
            "pagina": page,
            "resultados_por_pagina": page_size,
            "total_paginas": 0,
            "tiene_pagina_anterior": False,
            "tiene_pagina_siguiente": False,
            "resultados": [],
            "mensaje": _clean_cell_text(sin_res),
        }

    total_resultados = 0
    m_total = _TOTAL_RE.search(html)
    if m_total:
        digits = re.sub(r"[^\d]", "", m_total.group(1))
        if digits:
            total_resultados = int(digits)

    page_targets = {int(p) for p in _IR_PAGINA_RE.findall(html)}
    tiene_anterior = (page - 1) in page_targets if page > 1 else False
    tiene_siguiente = (page + 1) in page_targets

    resultados: list[dict[str, Any]] = []
    index_cells = soup.select("td.resultadoFilaImparDest, td.resultadoFilaParDest")

    for idx_cell in index_cells:
        row = idx_cell.find_parent("tr")
        if row is None:
            continue

        tds = row.find_all("td", recursive=False)
        if len(tds) < 6:
            continue

        indice_txt = _clean_cell_text(tds[0]).rstrip(".")
        try:
            indice = int(indice_txt)
        except ValueError:
            indice = len(resultados) + 1

        nro_expediente = _clean_cell_text(tds[1])
        caratula = _clean_cell_text(tds[2])
        secretaria_td = tds[3]
        secretaria = _clean_cell_text(secretaria_td)
        secretaria_desc = (secretaria_td.get("title") or "").strip() or None
        fecha_sentencia = _clean_cell_text(tds[4])

        doc_info: dict[str, Any] | None = None
        for tag in tds[5].find_all(["a", "svg"]):
            onclick = tag.get("onclick") or ""
            m_dl = _DOWNLOAD_PDF_RE.search(onclick)
            if m_dl:
                origen, expediente, incidente, movimiento, anio = m_dl.groups()
                doc_info = {
                    "origen": origen,
                    "expediente": expediente,
                    "anio": anio,
                    "incidente": incidente,
                    "movimiento": movimiento,
                    "formato_esperado": "pdf" if origen.upper() == "EJE" else "doc",
                    "url_descarga": build_download_url(
                        origen=origen,
                        expediente=expediente,
                        anio=anio,
                        incidente=incidente,
                        movimiento=movimiento,
                    ),
                }
                break

        terminos_tesauro: list[dict[str, str | None]] = []
        vinculacion: dict[str, Any] | None = None

        # Recorrer filas hermanas pertenecientes al mismo fallo
        sib = row.find_next_sibling("tr")
        while sib is not None:
            if sib.select_one("td.resultadoFilaImparDest, td.resultadoFilaParDest"):
                break
            sib_tds = sib.find_all("td", recursive=False)
            if len(sib_tds) == 1 and _clean_cell_text(sib_tds[0]) == "":
                # Fila separadora vacía entre fallos
                break

            for div in sib.find_all("div"):
                div_id = div.get("id") or ""
                if div_id.startswith("term_") and not div_id.endswith("_link"):
                    terminos_tesauro.extend(_parse_tesauro_div(div))
                if div.get("data-type") == "sentencias-vinculadas":
                    parsed_vinc = _parse_vinculadas_div(div)
                    if parsed_vinc is not None:
                        vinculacion = parsed_vinc

            sib = sib.find_next_sibling("tr")

        resultados.append(
            {
                "indice": indice,
                "nro_expediente": nro_expediente,
                "caratula": caratula,
                "secretaria": secretaria,
                "secretaria_descripcion": secretaria_desc,
                "fecha_sentencia": fecha_sentencia,
                "documento": doc_info,
                "terminos_tesauro": terminos_tesauro,
                "vinculacion": vinculacion,
            }
        )

    if total_resultados == 0 and resultados:
        total_resultados = len(resultados)

    total_paginas = math.ceil(total_resultados / page_size) if total_resultados > 0 else 0
    if total_paginas > page and not tiene_siguiente:
        tiene_siguiente = (page + 1) in page_targets

    return {
        "total_resultados": total_resultados,
        "pagina": page,
        "resultados_por_pagina": page_size,
        "total_paginas": total_paginas,
        "tiene_pagina_anterior": tiene_anterior,
        "tiene_pagina_siguiente": tiene_siguiente,
        "resultados": resultados,
    }


def parse_tesauro_relaciones(html: str) -> list[dict[str, Any]]:
    """Extrae todos los términos del tesauro y sus ocurrencias desde tsTerminosRelaciones.asp."""
    # Usamos BeautifulSoup o regex directa sobre las filas <tr ... onClick="mClk(this, 'ID', 'TERM');">
    # Dado que la página tiene ~800KB y estructura fija, regex sobre cada <tr> es muy rápido y exacto.
    row_re = re.compile(
        r"<tr[^>]+onClick=\"mClk\(this,\s*'([^']+)',\s*'([^']*)'\s*\);?\"[^>]*>\s*"
        r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
        re.IGNORECASE | re.DOTALL,
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in row_re.finditer(html):
        term_id = m.group(1).strip()
        raw_td_term = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        term_name = raw_td_term or html_lib.unescape(m.group(2)).strip()
        occ_str = re.sub(r"[^\d]", "", m.group(4))
        ocurrencias = int(occ_str) if occ_str else 0
        if term_id and term_id not in seen:
            seen.add(term_id)
            items.append(
                {
                    "id": term_id,
                    "termino": re.sub(r"\s+", " ", term_name).strip(),
                    "ocurrencias": ocurrencias,
                }
            )
    return items


def parse_tesauro_sugerencias(html: str) -> list[dict[str, str]]:
    """Extrae términos sugeridos desde tsSugerenciaTesauro.asp."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _SUGERENCIA_RE.finditer(html):
        term_id = m.group(1).strip()
        term_name = html_lib.unescape(m.group(2)).strip()
        if term_id not in seen:
            seen.add(term_id)
            items.append({"id": term_id, "termino": term_name})
    return items

