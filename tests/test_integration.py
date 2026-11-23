"""Tests de integración en vivo contra https://jurisprudencia.tsjbaires.gob.ar/jurisprudencia/."""

import pytest

from tsjbaires_mcp.client import TSJBairesClient
from tsjbaires_mcp.server import mcp


@pytest.mark.asyncio
async def test_mcp_tools_registered():
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "buscar_jurisprudencia",
        "obtener_texto_fallo",
        "descargar_fallo",
        "descargar_fallos_masivo",
        "buscar_tesauro",
        "obtener_sentencias_vinculadas",
        "listar_secretarias",
    }


@pytest.mark.asyncio
async def test_live_buscar_tesauro():
    client = TSJBairesClient()
    try:
        items = await client.buscar_tesauro("amparo", limite=15)
        assert len(items) > 0
        ids = {it["id"]: it["termino"] for it in items}
        assert "842" in ids
        assert "AMPARO" in ids["842"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_buscar_jurisprudencia_variants():
    client = TSJBairesClient()
    try:
        # 1. Texto + rango de fechas + secretaría
        res_text = await client.buscar_jurisprudencia(
            todas_las_palabras="amparo",
            fecha_desde="01/01/2024",
            fecha_hasta="31/12/2024",
            secretarias=["SACAyT"],
            resultados_por_pagina=10,
        )
        assert res_text["total_resultados"] >= 1
        assert len(res_text["resultados"]) >= 1
        assert res_text["resultados"][0]["documento"] is not None

        # 2. Múltiples términos de tesauro por ID
        res_tesauro = await client.buscar_jurisprudencia(
            terminos_tesauro=["842", "59738"],
            resultados_por_pagina=10,
        )
        assert res_tesauro["total_resultados"] >= 1

        # 3. Cero resultados
        res_zero = await client.buscar_jurisprudencia(nro_expediente="999999999")
        assert res_zero["total_resultados"] == 0
        assert res_zero["resultados"] == []
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_sentencias_vinculadas():
    client = TSJBairesClient()
    try:
        vinc_hija = await client.obtener_sentencias_vinculadas(1007559)
        assert vinc_hija["remite_a"] is not None
        assert vinc_hija["remite_a"]["id_clasif"] == 1007193

        vinc_madre = await client.obtener_sentencias_vinculadas(1007193)
        remiten_ids = {item["id_clasif"] for item in vinc_madre["remiten_a_esta"]}
        assert 1007559 in remiten_ids
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_live_obtener_texto_fallo_eje_pdf_and_sgej_doc():
    client = TSJBairesClient()
    try:
        # PDF EJE (envuelto en MIME/MTOM en origen)
        pdf_res = await client.obtener_texto_fallo(
            origen="EJE",
            expediente="129683",
            anio="2023",
            incidente="2",
            movimiento="2453918",
            max_caracteres=5000,
        )
        assert pdf_res["formato"] == "pdf"
        assert pdf_res["total_paginas"] >= 1
        assert len(pdf_res["texto"]) > 500

        # DOC histórico SGEJ (OLE2 binario)
        doc_res = await client.obtener_texto_fallo(
            origen="SGEJ",
            expediente="12518",
            anio="2015",
            incidente="0",
            movimiento="81",
            max_caracteres=5000,
        )
        assert doc_res["formato"] == "doc"
        assert len(doc_res["texto"]) > 500
        # Verificar que extrajo texto legible en español y no basura binaria
        texto_lower = doc_res["texto"].lower()
        assert any(w in texto_lower for w in ("tribunal", "buenos aires", "autos", "visto", "considerando"))
    finally:
        await client.aclose()

