"""Tests unitarios para parsers HTML y validación de parámetros."""

import pytest

from tsjbaires_mcp.client import TSJBairesClient
from tsjbaires_mcp.parsers import (
    build_download_url,
    normalize_text,
    parse_search_results,
    parse_tesauro_relaciones,
    parse_tesauro_sugerencias,
)

SAMPLE_RESULTS_HTML = """
<html><body>
<table>
  <tr>
    <td class="textoNegro"><b>Se encontraron <span style="color: #f00;">25</span> resultados para los siguientes par&aacute;metros de b&uacute;squeda:</b></td>
  </tr>
</table>
<table>
  <tr>
    <td colspan="6">
      <a href="#abajo"><b>Ir al pie</b></a>
      | <a href="#" onClick="irPagina(2)"><b>P&aacute;gina siguiente >></b></a>
    </td>
  </tr>
  <tr>
    <td class="resultadoFilaImparDest"><b>1.</b></td>
    <td class="resultadoFilaImpar"><b>129683</b></td>
    <td class="resultadoFilaImpar"><b>INSSJP s/ QUEJA</b></td>
    <td class="resultadoFilaImpar" title="Secr. Contencioso">SACAyT</td>
    <td class="resultadoFilaImpar">6/11/2024</td>
    <td class="resultadoFilaImpar">
      <a href="javascript:void(0)" onClick="downloadPDF('EJE', '129683', '2', '2453918', '2023')">pdf</a>
    </td>
    <td class="resultadoFilaImpar"><input type="checkbox" value="129683_2024_2_2453918_EJE"></td>
  </tr>
  <tr>
    <td colspan="7">
      <div id="term_1_link">Tesauro asociados</div>
      <div id="term_1">
        <a href="#" onClick="busqPorTerminoTesauro('65108', 'QUEJA POR DENEGACION DEL RECURSO DE INCONSTITUCIONALIDAD')">QUEJA POR DENEGACION DEL RECURSO DE INCONSTITUCIONALIDAD</a><span class="textoMuyChico">&nbsp;&nbsp;(RECHAZO)</span>
        - <a href="#" onClick="busqPorTerminoTesauro('842', 'ACCION DE AMPARO')">ACCION DE AMPARO</a>
      </div>
      <div class="sentencias-vinculadas" data-expediente-id="129683-2-2024"
           data-type="sentencias-vinculadas" data-id-clasif="1007887"
           data-clasificacion-vinculada="" data-tipo-vinculo="NULL"></div>
    </td>
  </tr>
  <tr><td colspan="7">&nbsp;</td></tr>
  <tr>
    <td class="resultadoFilaParDest"><b>2.</b></td>
    <td class="resultadoFilaPar"><b>12518</b></td>
    <td class="resultadoFilaPar"><b>CASO LEGADO</b></td>
    <td class="resultadoFilaPar" title="Secr. Originarios">SAOyRC</td>
    <td class="resultadoFilaPar">10/05/2015</td>
    <td class="resultadoFilaPar">
      <a href="javascript:void(0)" onClick="downloadPDF('SGEJ', '12518', '0', '81', '2015')">doc</a>
    </td>
    <td class="resultadoFilaPar"><input type="checkbox" value="12518_2016_0_81_SGEJ"></td>
  </tr>
  <tr>
    <td colspan="7">
      <div id="term_2">
        <a href="#" onClick="busqPorTerminoTesauro('4715', 'AMPARO POR MORA')">AMPARO POR MORA</a>
      </div>
      <div class="sentencias-vinculadas" data-expediente-id="12518-0-2015"
           data-type="sentencias-vinculadas" data-id-clasif="500100"
           data-clasificacion-vinculada="400100" data-tipo-vinculo="REMITE_A"></div>
    </td>
  </tr>
</table>
</body></html>
"""

SAMPLE_ZERO_HTML = """
<html><body>
<span class="sinResultados"><b>No se encontraron registros con los t&eacute;rminos seleccionados.</b></span>
</body></html>
"""


def test_parse_search_results_eje_and_sgej():
    res = parse_search_results(SAMPLE_RESULTS_HTML, page=1, page_size=10)
    assert res["total_resultados"] == 25
    assert res["total_paginas"] == 3
    assert res["tiene_pagina_anterior"] is False
    assert res["tiene_pagina_siguiente"] is True
    assert len(res["resultados"]) == 2

    r1 = res["resultados"][0]
    assert r1["indice"] == 1
    assert r1["nro_expediente"] == "129683"
    assert r1["secretaria"] == "SACAyT"
    assert r1["secretaria_descripcion"] == "Secr. Contencioso"
    assert r1["documento"]["origen"] == "EJE"
    assert r1["documento"]["anio"] == "2023"
    assert r1["documento"]["formato_esperado"] == "pdf"
    assert "expedientes_download_EJE.php" in r1["documento"]["url_descarga"]
    assert "anio=2023" in r1["documento"]["url_descarga"]
    assert r1["terminos_tesauro"] == [
        {
            "id": "65108",
            "termino": "QUEJA POR DENEGACION DEL RECURSO DE INCONSTITUCIONALIDAD",
            "calificador": "RECHAZO",
        },
        {
            "id": "842",
            "termino": "ACCION DE AMPARO",
            "calificador": None,
        },
    ]
    assert r1["vinculacion"] == {
        "id_clasif": 1007887,
        "expediente_id": "129683-2-2024",
        "clasificacion_vinculada": None,
        "tipo_vinculo": None,
    }

    r2 = res["resultados"][1]
    assert r2["documento"]["origen"] == "SGEJ"
    assert r2["documento"]["formato_esperado"] == "doc"
    assert "expedientes_download.php?" in r2["documento"]["url_descarga"]
    assert r2["vinculacion"]["tipo_vinculo"] == "REMITE_A"
    assert r2["vinculacion"]["clasificacion_vinculada"] == 400100


def test_parse_zero_results():
    res = parse_search_results(SAMPLE_ZERO_HTML, page=1, page_size=20)
    assert res["total_resultados"] == 0
    assert res["resultados"] == []
    assert "No se encontraron" in res["mensaje"]


def test_parse_tesauro_relaciones_and_sugerencias():
    html_rel = """
    <table>
      <tr class="resultadoFilaImpar" onClick="mClk(this, '842', 'ACCION DE AMPARO ');">
        <td>ACCION DE AMPARO</td><td>1234</td>
      </tr>
      <tr class="resultadoFilaPar" onClick="mClk(this, '36272', 'FALTA DE FUNDAMENTACION ');">
        <td>FALTA DE FUNDAMENTACION</td><td>5807</td>
      </tr>
    </table>
    """
    items = parse_tesauro_relaciones(html_rel)
    assert items == [
        {"id": "842", "termino": "ACCION DE AMPARO", "ocurrencias": 1234},
        {"id": "36272", "termino": "FALTA DE FUNDAMENTACION", "ocurrencias": 5807},
    ]

    html_sug = """<td onclick="cargarTerminoLista(842, 'ACCION DE AMPARO')">ACCION DE AMPARO</td>"""
    assert parse_tesauro_sugerencias(html_sug) == [
        {"id": "842", "termino": "ACCION DE AMPARO"}
    ]


def test_normalize_and_url():
    assert normalize_text("  Acción  de Constitución ") == "ACCION DE CONSTITUCION"
    assert "expedientes_download_EJE.php" in build_download_url("EJE", "1", "2024", "0", "9")
    assert "expedientes_download.php?" in build_download_url("SGEJ", "1", "2015", "0", "9")


def test_validate_search_params():
    with pytest.raises(ValueError, match="al menos un criterio"):
        TSJBairesClient.validate_search_params()

    with pytest.raises(ValueError, match="al menos 3 caracteres"):
        TSJBairesClient.validate_search_params(todas_las_palabras="ab")

    with pytest.raises(ValueError, match="sin al menos otro campo"):
        TSJBairesClient.validate_search_params(sin_palabras="amparo")

    with pytest.raises(ValueError, match="anterior"):
        TSJBairesClient.validate_search_params(
            fecha_desde="10/05/2024", fecha_hasta="01/05/2024"
        )

    with pytest.raises(ValueError, match="Secretaría desconocida"):
        TSJBairesClient.validate_search_params(
            todas_las_palabras="amparo", secretarias=["NOEXISTE"]
        )

    ok = TSJBairesClient.validate_search_params(
        todas_las_palabras="amparo",
        secretarias=["sacayt", "SAO"],
        ordenar_por="fecha",
        orden_direccion="asc",
        resultados_por_pagina=50,
    )
    assert ok["sec_codigos"] == ["SACAyT", "SAOyRC"]
    assert ok["orden_campo"] == "moviFecha"

