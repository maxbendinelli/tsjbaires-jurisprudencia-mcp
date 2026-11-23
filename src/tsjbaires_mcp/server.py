"""Servidor MCP (FastMCP) para consultar Jurisprudencia del TSJ CABA."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from .client import TSJBairesClient

_client: TSJBairesClient | None = None


def get_client() -> TSJBairesClient:
    global _client
    if _client is None:
        _client = TSJBairesClient()
    return _client


@asynccontextmanager
async def _lifespan(_server: MCPServer):
    client = get_client()
    try:
        yield {"client": client}
    finally:
        await client.aclose()


mcp = MCPServer(
    "tsjbaires-jurisprudencia",
    instructions=(
        "Servidor MCP para consultar fallos y el tesauro temático del Tribunal Superior "
        "de Justicia de la Ciudad Autónoma de Buenos Aires (jurisprudencia.tsjbaires.gob.ar). "
        "Nota importante: las búsquedas de texto libre en el portal distinguen tildes "
        "(por ejemplo, 'constitución' vs 'constitucion'), mientras que `buscar_tesauro` es "
        "insensible a tildes y ayuda a encontrar voces exactas para filtrar fallos."
    ),
    log_level="WARNING",
    lifespan=_lifespan,
)


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, httpx.HTTPStatusError):
        return {
            "error": f"Error HTTP {exc.response.status_code} al consultar el portal del TSJ CABA.",
            "detalle": str(exc),
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "error": "Error de conexión o timeout al comunicarse con el portal del TSJ CABA.",
            "detalle": str(exc),
        }
    return {"error": str(exc)}


@mcp.tool()
async def buscar_jurisprudencia(
    nro_expediente: str | None = None,
    caratula: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    todas_las_palabras: str | None = None,
    frase_exacta: str | None = None,
    algunas_palabras: str | None = None,
    sin_palabras: str | None = None,
    terminos_tesauro: list[str] | None = None,
    secretarias: list[str] | None = None,
    ordenar_por: str | None = None,
    orden_direccion: str = "desc",
    pagina: int = 1,
    resultados_por_pagina: int = 20,
) -> dict[str, Any]:
    """Busca sentencias en el portal de Jurisprudencia del TSJ CABA.

    Debe especificarse al menos un criterio de búsqueda. Los campos de texto
    requieren mínimo 3 caracteres y en el servidor distinguen tildes (respetar acentos).

    Args:
        nro_expediente: Número de expediente (solo dígitos, puntos o guiones).
        caratula: Texto total o parcial de la carátula.
        fecha_desde: Fecha de sentencia desde en formato DD/MM/AAAA.
        fecha_hasta: Fecha de sentencia hasta en formato DD/MM/AAAA.
        todas_las_palabras: Palabras que deben aparecer todas en el fallo.
        frase_exacta: Frase exacta que debe aparecer en el fallo.
        algunas_palabras: Al menos una de estas palabras debe aparecer.
        sin_palabras: Palabras a excluir (requiere otro campo de texto activo).
        terminos_tesauro: Lista de nombres exactos o IDs numéricos del tesauro (usar `buscar_tesauro` para descubrirlos).
        secretarias: Lista de códigos de secretaría: SAOyRC, SACAyT, SAPPJCyF, SACyC, SAL, SAG, TSN.
        ordenar_por: Campo de ordenamiento: 'fecha', 'expediente', 'caratula' o 'secretaria'.
        orden_direccion: Dirección de ordenamiento: 'asc' o 'desc' (por defecto 'desc').
        pagina: Número de página (1-based).
        resultados_por_pagina: Cantidad por página: 10, 20, 50 o 100.
    """
    try:
        return await get_client().buscar_jurisprudencia(
            nro_expediente=nro_expediente,
            caratula=caratula,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            todas_las_palabras=todas_las_palabras,
            frase_exacta=frase_exacta,
            algunas_palabras=algunas_palabras,
            sin_palabras=sin_palabras,
            terminos_tesauro=terminos_tesauro,
            secretarias=secretarias,
            ordenar_por=ordenar_por,
            orden_direccion=orden_direccion,
            pagina=pagina,
            resultados_por_pagina=resultados_por_pagina,
        )
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def obtener_texto_fallo(
    origen: str,
    expediente: str,
    anio: str,
    incidente: str,
    movimiento: str,
    pagina_desde: int | None = None,
    pagina_hasta: int | None = None,
    max_caracteres: int = 40000,
) -> dict[str, Any]:
    """Descarga una sentencia (PDF de EJE o DOC histórico de SGEJ) y extrae su texto completo.

    Los 5 identificadores (`origen`, `expediente`, `anio`, `incidente`, `movimiento`)
    se obtienen directamente del campo `documento` de `buscar_jurisprudencia` o de
    `obtener_sentencias_vinculadas`.

    Args:
        origen: 'EJE' o 'SGEJ' (u origen legado).
        expediente: Número de expediente interno del documento.
        anio: Año del expediente (campo `documento.anio`, importante usar el devuelto en `documento`).
        incidente: Número de incidente (generalmente '0', '1', '2', etc.).
        movimiento: ID de movimiento de la resolución.
        pagina_desde: Página inicial (1-based, solo aplica a PDF).
        pagina_hasta: Página final (1-based, solo aplica a PDF).
        max_caracteres: Límite de caracteres devueltos (por defecto 40000; 0 o negativo desactiva el límite).
    """
    try:
        limit = max_caracteres if max_caracteres > 0 else None
        return await get_client().obtener_texto_fallo(
            origen=origen,
            expediente=expediente,
            anio=anio,
            incidente=incidente,
            movimiento=movimiento,
            pagina_desde=pagina_desde,
            pagina_hasta=pagina_hasta,
            max_caracteres=limit,
        )
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def descargar_fallo(
    origen: str,
    expediente: str,
    anio: str,
    incidente: str,
    movimiento: str,
    destino: str,
) -> dict[str, Any]:
    """Descarga y guarda en disco el archivo original limpio (.pdf o .doc) de un fallo.

    Args:
        origen: 'EJE' o 'SGEJ'.
        expediente: Número de expediente.
        anio: Año del expediente (`documento.anio`).
        incidente: Número de incidente.
        movimiento: ID de movimiento.
        destino: Ruta de archivo o carpeta donde guardar el fallo.
    """
    try:
        return await get_client().guardar_fallo(
            origen=origen,
            expediente=expediente,
            anio=anio,
            incidente=incidente,
            movimiento=movimiento,
            destino=destino,
        )
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def descargar_fallos_masivo(
    fallos: list[dict[str, Any]],
    carpeta_destino: str,
    concurrencia: int = 4,
) -> dict[str, Any]:
    """Descarga múltiples fallos en paralelo en una carpeta local.

    Cada elemento de `fallos` debe incluir las claves `origen`, `expediente`, `anio`,
    `incidente` y `movimiento` (del campo `documento` de `buscar_jurisprudencia`), y
    opcionalmente `nombre_archivo` (sin extensión; se asigna `.pdf` o `.doc` automáticamente).

    Args:
        fallos: Lista de diccionarios con los metadatos de cada documento a descargar.
        carpeta_destino: Ruta de la carpeta donde se guardarán los archivos.
        concurrencia: Cantidad de descargas simultáneas (entre 1 y 10, por defecto 4).
    """
    try:
        return await get_client().guardar_fallos_masivo(
            fallos=fallos,
            carpeta_destino=carpeta_destino,
            concurrencia=concurrencia,
        )
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def buscar_tesauro(
    consulta: str = "",
    limite: int = 30,
) -> dict[str, Any]:
    """Busca voces temáticas del tesauro oficial del TSJ CABA (insensible a tildes).

    Si `consulta` está vacía, devuelve las voces más utilizadas ordenadas por cantidad
    de fallos asociados. Los `id` o `termino` devueltos pueden pasarse en `terminos_tesauro`
    al llamar a `buscar_jurisprudencia`.

    Args:
        consulta: Palabra o frase a buscar en el tesauro (opcional).
        limite: Cantidad máxima de términos a devolver (1 a 200, por defecto 30).
    """
    try:
        terminos = await get_client().buscar_tesauro(consulta=consulta, limite=limite)
        return {
            "consulta": consulta,
            "total_devueltos": len(terminos),
            "terminos": terminos,
        }
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def obtener_sentencias_vinculadas(id_clasif: int) -> dict[str, Any]:
    """Obtiene las sentencias vinculadas a una resolución (precedente al que remite, sentencias que remiten a esta y sentencias en igual sentido).

    Args:
        id_clasif: ID de clasificación (`vinculacion.id_clasif` en los resultados de búsqueda).
    """
    try:
        return await get_client().obtener_sentencias_vinculadas(id_clasif=id_clasif)
    except Exception as exc:
        return _error_payload(exc)


@mcp.tool()
async def listar_secretarias() -> dict[str, Any]:
    """Lista las Secretarías de Trámite válidas para filtrar búsquedas en el TSJ CABA."""
    return {"secretarias": TSJBairesClient.listar_secretarias()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Servidor MCP para Jurisprudencia del TSJ CABA"
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transporte MCP a utilizar (por defecto: stdio)",
    )
    args = parser.parse_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

