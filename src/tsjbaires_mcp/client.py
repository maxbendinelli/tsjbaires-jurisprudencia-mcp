"""Cliente HTTP asíncrono para el sistema de Jurisprudencia del TSJ CABA."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from .extractors import detect_document_format, extract_document_text
from .parsers import (
    build_download_url,
    normalize_text,
    parse_search_results,
    parse_tesauro_relaciones,
    parse_tesauro_sugerencias,
)

BASE_URL = "https://jurisprudencia.tsjbaires.gob.ar/jurisprudencia/"

SECRETARIAS: dict[str, dict[str, str]] = {
    "SAOyRC": {
        "codigo": "SAOyRC",
        "campo_form": "chkSAO",
        "nombre": "Secretaría de Asuntos Originarios y Relaciones de Consumo",
    },
    "SACAyT": {
        "codigo": "SACAyT",
        "campo_form": "chkSACAYT",
        "nombre": "Secretaría de Asuntos Contencioso, Administrativos y Tributarios",
    },
    "SAPPJCyF": {
        "codigo": "SAPPJCyF",
        "campo_form": "chkSAC",
        "nombre": "Secretaría de Asuntos Penales, Penales Juveniles, Contravencionales y de Faltas",
    },
    "SACyC": {
        "codigo": "SACyC",
        "campo_form": "chkSACyC",
        "nombre": "Secretaría de Asuntos Civiles y Comerciales",
    },
    "SAL": {
        "codigo": "SAL",
        "campo_form": "chkSAL",
        "nombre": "Secretaría de Asuntos Laborales",
    },
    "SAG": {
        "codigo": "SAG",
        "campo_form": "chkSAG",
        "nombre": "Secretaría de Asuntos Generales",
    },
    "TSN": {
        "codigo": "TSN",
        "campo_form": "chkTSN",
        "nombre": "Tribunal de Superintendencia del Notariado",
    },
}

SECRETARIA_ALIASES: dict[str, str] = {
    "SAO": "SAOyRC",
    "SAOYRC": "SAOyRC",
    "CHKSAO": "SAOyRC",
    "SACAYT": "SACAyT",
    "CHKSACAYT": "SACAyT",
    "SAC": "SAPPJCyF",
    "SAPPJCYF": "SAPPJCyF",
    "CHKSAC": "SAPPJCyF",
    "SACYC": "SACyC",
    "CHKSACYC": "SACyC",
    "SAL": "SAL",
    "CHKSAL": "SAL",
    "SAG": "SAG",
    "CHKSAG": "SAG",
    "TSN": "TSN",
    "CHKTSN": "TSN",
}

SORT_FIELDS: dict[str, str] = {
    "fecha": "moviFecha",
    "movifecha": "moviFecha",
    "expediente": "nroExpe",
    "nroexpe": "nroExpe",
    "caratula": "titulo",
    "titulo": "titulo",
    "secretaria": "ubicacion",
    "ubicacion": "ubicacion",
}

VALID_PAGE_SIZES = (10, 20, 50, 100)
_TESAURO_TTL_SECONDS = 6 * 3600
_DATE_DDMMYYYY_RE = r"^\d{2}/\d{2}/\d{4}$"


def _encode_latin1_form(params: list[tuple[str, str]]) -> bytes:
    """Codifica parámetros de formulario en ISO-8859-1 como espera ASP clásico."""
    return urllib.parse.urlencode(params, encoding="latin-1", errors="replace").encode("ascii")


def _parse_ddmmyyyy(value: str, label: str) -> tuple[int, int, int]:
    import re
    from datetime import date

    v = value.strip()
    if not re.match(_DATE_DDMMYYYY_RE, v):
        raise ValueError(f"{label} debe tener formato DD/MM/AAAA (recibido: {value!r}).")
    dd, mm, yyyy = int(v[:2]), int(v[3:5]), int(v[6:10])
    try:
        date(yyyy, mm, dd)
    except ValueError as exc:
        raise ValueError(f"{label} no es una fecha válida: {value!r}.") from exc
    return yyyy, mm, dd


class TSJBairesClient:
    """Cliente asíncrono para buscar, navegar y descargar fallos del TSJ CABA."""

    def __init__(self, timeout: float = 35.0) -> None:
        self._timeout = timeout
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
        }
        self._shared_client: httpx.AsyncClient | None = None
        self._tesauro_cache: list[dict[str, Any]] | None = None
        self._tesauro_cached_at: float = 0.0
        self._tesauro_lock = asyncio.Lock()

    def _get_shared_client(self) -> httpx.AsyncClient:
        if self._shared_client is None or self._shared_client.is_closed:
            self._shared_client = httpx.AsyncClient(
                timeout=self._timeout,
                headers=self._headers,
                follow_redirects=True,
            )
        return self._shared_client

    async def aclose(self) -> None:
        if self._shared_client is not None and not self._shared_client.is_closed:
            await self._shared_client.aclose()
            self._shared_client = None

    def _new_session_client(self) -> httpx.AsyncClient:
        """Crea un cliente aislado con su propio jar de cookies ASPSESSIONID."""
        return httpx.AsyncClient(
            timeout=self._timeout,
            headers=self._headers,
            follow_redirects=True,
        )

    @staticmethod
    def listar_secretarias() -> list[dict[str, str]]:
        return [
            {"codigo": v["codigo"], "nombre": v["nombre"]}
            for v in SECRETARIAS.values()
        ]

    @staticmethod
    def _normalize_secretarias(secretarias: list[str] | None) -> list[str]:
        if not secretarias:
            return []
        resolved: list[str] = []
        for s in secretarias:
            key = s.strip().upper()
            canon = SECRETARIA_ALIASES.get(key)
            if canon is None:
                valid = ", ".join(SECRETARIAS.keys())
                raise ValueError(
                    f"Secretaría desconocida: {s!r}. Valores válidos: {valid}."
                )
            if canon not in resolved:
                resolved.append(canon)
        return resolved

    @staticmethod
    def validate_search_params(
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
        exp_nro = (nro_expediente or "").strip()
        exp_car = (caratula or "").strip()
        f_desde = (fecha_desde or "").strip()
        f_hasta = (fecha_hasta or "").strip()
        t_todas = (todas_las_palabras or "").strip()
        t_frase = (frase_exacta or "").strip()
        t_algunas = (algunas_palabras or "").strip()
        t_sin = (sin_palabras or "").strip()
        tesauro_list = [t.strip() for t in (terminos_tesauro or []) if t and t.strip()]

        if not any(
            [
                exp_nro,
                exp_car,
                f_desde,
                f_hasta,
                t_todas,
                t_frase,
                t_algunas,
                t_sin,
                tesauro_list,
            ]
        ):
            raise ValueError(
                "Debe especificar al menos un criterio de búsqueda "
                "(expediente, carátula, fechas, texto o términos del tesauro)."
            )

        if exp_nro and not all(c in "0123456789.-" for c in exp_nro):
            raise ValueError(
                f"nro_expediente debe ser numérico (recibido: {nro_expediente!r})."
            )

        for label, txt in [
            ("todas_las_palabras", t_todas),
            ("frase_exacta", t_frase),
            ("algunas_palabras", t_algunas),
            ("sin_palabras", t_sin),
        ]:
            if txt and len(txt) < 3:
                raise ValueError(
                    f"El campo {label} debe tener al menos 3 caracteres (recibido: {txt!r})."
                )

        if t_sin and not (t_todas or t_frase or t_algunas):
            raise ValueError(
                "sin_palabras no puede usarse sin al menos otro campo de texto "
                "(todas_las_palabras, frase_exacta o algunas_palabras)."
            )

        d_desde = _parse_ddmmyyyy(f_desde, "fecha_desde") if f_desde else None
        d_hasta = _parse_ddmmyyyy(f_hasta, "fecha_hasta") if f_hasta else None
        if d_desde and d_hasta and d_desde > d_hasta:
            raise ValueError(
                f"fecha_hasta ({f_hasta}) no puede ser anterior a fecha_desde ({f_desde})."
            )

        sec_codigos = TSJBairesClient._normalize_secretarias(secretarias)

        orden_campo = ""
        orden_dir = ""
        if ordenar_por:
            campo_norm = SORT_FIELDS.get(ordenar_por.strip().lower())
            if campo_norm is None:
                raise ValueError(
                    f"ordenar_por inválido: {ordenar_por!r}. "
                    "Opciones: fecha, expediente, caratula, secretaria."
                )
            dir_norm = orden_direccion.strip().lower()
            if dir_norm not in ("asc", "desc"):
                raise ValueError(
                    f"orden_direccion inválida: {orden_direccion!r}. Use 'asc' o 'desc'."
                )
            orden_campo = campo_norm
            orden_dir = dir_norm

        if pagina < 1:
            raise ValueError("pagina debe ser >= 1.")
        if resultados_por_pagina not in VALID_PAGE_SIZES:
            raise ValueError(
                f"resultados_por_pagina={resultados_por_pagina} inválido. "
                f"Opciones permitidas: {VALID_PAGE_SIZES}."
            )

        return {
            "exp_nro": exp_nro,
            "exp_car": exp_car,
            "f_desde": f_desde,
            "f_hasta": f_hasta,
            "t_todas": t_todas,
            "t_frase": t_frase,
            "t_algunas": t_algunas,
            "t_sin": t_sin,
            "tesauro_list": tesauro_list,
            "sec_codigos": sec_codigos,
            "orden_campo": orden_campo,
            "orden_dir": orden_dir,
            "pagina": pagina,
            "resultados_por_pagina": resultados_por_pagina,
        }

    async def obtener_tesauro_completo(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Obtiene y cachea el listado completo del tesauro desde tsTerminosRelaciones.asp."""
        now = time.monotonic()
        if (
            not force_refresh
            and self._tesauro_cache is not None
            and (now - self._tesauro_cached_at) < _TESAURO_TTL_SECONDS
        ):
            return self._tesauro_cache

        async with self._tesauro_lock:
            now = time.monotonic()
            if (
                not force_refresh
                and self._tesauro_cache is not None
                and (now - self._tesauro_cached_at) < _TESAURO_TTL_SECONDS
            ):
                return self._tesauro_cache

            client = self._get_shared_client()
            resp = await client.get(f"{BASE_URL}ts/tsTerminosRelaciones.asp")
            resp.raise_for_status()
            html = resp.content.decode("latin-1", errors="replace")
            parsed = parse_tesauro_relaciones(html)
            if parsed:
                self._tesauro_cache = parsed
                self._tesauro_cached_at = time.monotonic()
            return parsed

    async def sugerir_tesauro(self, palabra: str) -> list[dict[str, str]]:
        """Consulta el endpoint de sugerencias del servidor (tsSugerenciaTesauro.asp)."""
        q = urllib.parse.quote(palabra.strip(), encoding="latin-1", errors="replace")
        client = self._get_shared_client()
        resp = await client.get(f"{BASE_URL}ts/tsSugerenciaTesauro.asp?palabra={q}")
        resp.raise_for_status()
        html = resp.content.decode("latin-1", errors="replace")
        return parse_tesauro_sugerencias(html)

    async def buscar_tesauro(
        self,
        consulta: str = "",
        limite: int = 30,
    ) -> list[dict[str, Any]]:
        """Busca términos del tesauro (sin sensibilidad a tildes), ordenados por relevancia y frecuencia."""
        if limite < 1:
            limite = 1
        elif limite > 200:
            limite = 200

        items = await self.obtener_tesauro_completo()
        q_norm = normalize_text(consulta)
        if not q_norm:
            return items[:limite]

        q_tokens = q_norm.split()
        ranked: list[tuple[int, int, dict[str, Any]]] = []
        for item in items:
            t_norm = normalize_text(item["termino"])
            if t_norm == q_norm:
                rank = 0
            elif t_norm.startswith(q_norm):
                rank = 1
            elif q_norm in t_norm:
                rank = 2
            elif all(tok in t_norm for tok in q_tokens):
                rank = 3
            else:
                continue
            ranked.append((rank, -item["ocurrencias"], item))

        ranked.sort(key=lambda x: (x[0], x[1], x[2]["termino"]))
        return [x[2] for x in ranked[:limite]]

    async def _resolver_terminos_tesauro(
        self,
        terminos: list[str],
    ) -> list[tuple[str, str]]:
        """Resuelve cada ID o nombre de término a (id, termino) válido para el carrito ASP."""
        if not terminos:
            return []

        items = await self.obtener_tesauro_completo()
        by_id = {it["id"]: it["termino"] for it in items}
        by_norm = {normalize_text(it["termino"]): (it["id"], it["termino"]) for it in items}

        resolved: list[tuple[str, str]] = []
        for raw in terminos:
            val = raw.strip()
            if val.isdigit():
                if val in by_id:
                    resolved.append((val, by_id[val]))
                    continue
                raise ValueError(f"ID de término de tesauro inexistente: {val!r}.")

            norm = normalize_text(val)
            if norm in by_norm:
                resolved.append(by_norm[norm])
                continue

            # Fallback: consultar sugerencias del servidor
            sugs = await self.sugerir_tesauro(val)
            for s in sugs:
                if normalize_text(s["termino"]) == norm:
                    resolved.append((s["id"], s["termino"]))
                    break
            else:
                matches = await self.buscar_tesauro(val, limite=5)
                sug_txt = ", ".join(f"{m['termino']} (id={m['id']})" for m in matches)
                raise ValueError(
                    f"Término de tesauro no encontrado exactamente: {val!r}."
                    + (f" Sugerencias cercanas: {sug_txt}." if sug_txt else "")
                )

        return resolved

    async def buscar_jurisprudencia(
        self,
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
        """Ejecuta una búsqueda en resultadoBusqueda.asp usando una sesión ASP aislada."""
        v = self.validate_search_params(
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

        tesauro_pairs = await self._resolver_terminos_tesauro(v["tesauro_list"])

        async with self._new_session_client() as session:
            # 1. Inicializar cookie ASPSESSIONID
            r_home = await session.get(BASE_URL)
            r_home.raise_for_status()

            # 2. Cargar términos de tesauro en el carrito de la sesión ASP si corresponde
            for term_id, term_name in tesauro_pairs:
                body_ts = _encode_latin1_form(
                    [
                        ("sModoOpe", "AddTerminoCarrito"),
                        ("termino_elegido", term_name),
                        ("IDtermino_elegido", term_id),
                        ("hdnPaginaOrigen", "busqueda.asp"),
                    ]
                )
                r_ts = await session.post(
                    f"{BASE_URL}ts/tsActualizaSesionTesauro.asp",
                    content=body_ts,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                r_ts.raise_for_status()

            # 3. Enviar búsqueda a resultadoBusqueda.asp
            form_params: list[tuple[str, str]] = [
                ("hdnPaginaOrigen", "busqueda.asp"),
                ("expNro", v["exp_nro"]),
                ("expFechaResDesde", v["f_desde"]),
                ("expFechaResHasta", v["f_hasta"]),
                ("expCaratula", v["exp_car"]),
                ("textoLibreTodas", v["t_todas"]),
                ("textoLibreFrase", v["t_frase"]),
                ("textoLibreAlgunas", v["t_algunas"]),
                ("textoLibreExcluyendo", v["t_sin"]),
                ("ordenInicial", "1" if v["orden_campo"] else ""),
                ("ordenResCampo", v["orden_campo"]),
                ("ordenResTipo", v["orden_dir"]),
                ("pag", str(v["pagina"])),
                ("cantRegPagina", str(v["resultados_por_pagina"])),
            ]
            for sec_code in v["sec_codigos"]:
                form_params.append((SECRETARIAS[sec_code]["campo_form"], "on"))

            body_search = _encode_latin1_form(form_params)
            r_res = await session.post(
                f"{BASE_URL}resultadoBusqueda.asp",
                content=body_search,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r_res.raise_for_status()
            html = r_res.content.decode("latin-1", errors="replace")

        parsed = parse_search_results(
            html,
            page=v["pagina"],
            page_size=v["resultados_por_pagina"],
        )
        parsed["parametros_aplicados"] = {
            "nro_expediente": v["exp_nro"] or None,
            "caratula": v["exp_car"] or None,
            "fecha_desde": v["f_desde"] or None,
            "fecha_hasta": v["f_hasta"] or None,
            "todas_las_palabras": v["t_todas"] or None,
            "frase_exacta": v["t_frase"] or None,
            "algunas_palabras": v["t_algunas"] or None,
            "sin_palabras": v["t_sin"] or None,
            "terminos_tesauro": [
                {"id": tid, "termino": tname} for tid, tname in tesauro_pairs
            ],
            "secretarias": v["sec_codigos"],
            "ordenar_por": ordenar_por,
            "orden_direccion": v["orden_dir"] or None,
        }
        return parsed

    @staticmethod
    def _enrich_clasificacion(c: dict[str, Any]) -> dict[str, Any]:
        origen = str(c.get("origen") or "EJE")
        expediente = str(c.get("expeNro") or "")
        anio = str(c.get("anioExpediente") or "")
        incidente = str(c.get("expeInciNro") or "0")
        movimiento = str(c.get("moviNro") or "")
        return {
            "id_clasif": c.get("idClasif"),
            "expediente_id": c.get("expediente_ID"),
            "titulo": c.get("titulo"),
            "fecha": c.get("fecha"),
            "tipo_vinculo": (
                None
                if str(c.get("tipo_vinculo") or "").upper() in ("", "NULL")
                else c.get("tipo_vinculo")
            ),
            "clasificacion_vinculada": c.get("clasificacion_vinculada"),
            "documento": {
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
            },
        }

    async def _fetch_clasificaciones(self, **query: int) -> list[dict[str, Any]]:
        client = self._get_shared_client()
        qs = urllib.parse.urlencode(query)
        resp = await client.get(f"{BASE_URL}clasificaciones.asp?{qs}")
        resp.raise_for_status()
        try:
            text = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            text = resp.content.decode("latin-1", errors="replace")
        data = json.loads(text)
        return data.get("clasificaciones") or []

    async def obtener_sentencias_vinculadas(self, id_clasif: int) -> dict[str, Any]:
        """Obtiene la ficha de clasificación y todas las sentencias vinculadas (remisiones / igual sentido)."""
        actual_list, hijas = await asyncio.gather(
            self._fetch_clasificaciones(id=id_clasif),
            self._fetch_clasificaciones(idClasificacionVinculada=id_clasif),
        )
        if not actual_list:
            raise ValueError(f"No se encontró la clasificación con id_clasif={id_clasif}.")

        actual = actual_list[0]
        tipo_vinculo = str(actual.get("tipo_vinculo") or "").upper()
        madre_id = actual.get("clasificacion_vinculada")

        remite_a: dict[str, Any] | None = None
        en_igual_sentido: list[dict[str, Any]] = []

        if tipo_vinculo == "REMITE_A" and madre_id:
            madres = await self._fetch_clasificaciones(id=int(madre_id))
            if madres:
                remite_a = self._enrich_clasificacion(madres[0])
        elif tipo_vinculo == "IGUAL_SENTIDO_A" and madre_id:
            madres, hermanas = await asyncio.gather(
                self._fetch_clasificaciones(id=int(madre_id)),
                self._fetch_clasificaciones(idClasificacionVinculada=int(madre_id)),
            )
            for h in hermanas:
                if (
                    str(h.get("tipo_vinculo") or "").upper() == "IGUAL_SENTIDO_A"
                    and int(h.get("idClasif", -1)) != int(id_clasif)
                ):
                    en_igual_sentido.append(self._enrich_clasificacion(h))
            for m in madres:
                en_igual_sentido.append(self._enrich_clasificacion(m))

        remiten_a_esta: list[dict[str, Any]] = []
        seen_igual = {item["id_clasif"] for item in en_igual_sentido}
        for h in hijas:
            tv = str(h.get("tipo_vinculo") or "").upper()
            if tv == "REMITE_A":
                remiten_a_esta.append(self._enrich_clasificacion(h))
            elif tv == "IGUAL_SENTIDO_A" and h.get("idClasif") not in seen_igual:
                en_igual_sentido.append(self._enrich_clasificacion(h))
                seen_igual.add(h.get("idClasif"))

        return {
            "sentencia": self._enrich_clasificacion(actual),
            "remite_a": remite_a,
            "remiten_a_esta": remiten_a_esta,
            "en_igual_sentido": en_igual_sentido,
        }

    async def descargar_bytes_fallo(
        self,
        origen: str,
        expediente: str,
        anio: str,
        incidente: str,
        movimiento: str,
        reintentos: int = 3,
    ) -> tuple[bytes, str, str]:
        """Descarga el archivo del fallo, valida formato real y limpia wrappers MTOM si es PDF."""
        url = build_download_url(
            origen=origen,
            expediente=expediente,
            anio=anio,
            incidente=incidente,
            movimiento=movimiento,
        )
        client = self._get_shared_client()
        last_err: Exception | None = None

        for attempt in range(max(1, reintentos)):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                raw = resp.content
                fmt = detect_document_format(raw)
                if fmt != "unknown":
                    if fmt == "pdf":
                        from .extractors import clean_pdf_bytes

                        raw = clean_pdf_bytes(raw)
                    return raw, fmt, url
                last_err = ValueError(
                    f"Respuesta inválida al descargar fallo ({len(raw)} bytes, formato desconocido)."
                )
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_err = exc

            if attempt < reintentos - 1:
                await asyncio.sleep(0.4 * (attempt + 1))

        assert last_err is not None
        raise last_err

    async def obtener_texto_fallo(
        self,
        origen: str,
        expediente: str,
        anio: str,
        incidente: str,
        movimiento: str,
        pagina_desde: int | None = None,
        pagina_hasta: int | None = None,
        max_caracteres: int | None = 40000,
    ) -> dict[str, Any]:
        """Descarga el fallo y extrae su texto completo o por rango de páginas."""
        raw, fmt, url = await self.descargar_bytes_fallo(
            origen=origen,
            expediente=expediente,
            anio=anio,
            incidente=incidente,
            movimiento=movimiento,
        )
        extracted = extract_document_text(
            raw,
            pagina_desde=pagina_desde,
            pagina_hasta=pagina_hasta,
            max_caracteres=max_caracteres,
        )
        extracted["url_descarga"] = url
        extracted["tamano_bytes"] = len(raw)
        return extracted

    async def guardar_fallo(
        self,
        origen: str,
        expediente: str,
        anio: str,
        incidente: str,
        movimiento: str,
        destino: str,
    ) -> dict[str, Any]:
        """Descarga y guarda el archivo limpio del fallo en la ruta local indicada."""
        raw, fmt, url = await self.descargar_bytes_fallo(
            origen=origen,
            expediente=expediente,
            anio=anio,
            incidente=incidente,
            movimiento=movimiento,
        )
        ext = f".{fmt}" if fmt != "unknown" else (".pdf" if origen.upper() == "EJE" else ".doc")
        path = Path(destino).expanduser()
        if path.exists() and path.is_dir():
            filename = f"{expediente}_{anio}_{incidente}_{movimiento}{ext}"
            path = path / filename
        elif not path.suffix:
            path = path.with_suffix(ext)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return {
            "ruta": str(path.resolve()),
            "formato": fmt,
            "tamano_bytes": len(raw),
            "url_descarga": url,
        }

    async def guardar_fallos_masivo(
        self,
        fallos: list[dict[str, Any]],
        carpeta_destino: str,
        concurrencia: int = 4,
    ) -> dict[str, Any]:
        """Descarga múltiples fallos en paralelo usando un semáforo de concurrencia."""
        if not fallos:
            raise ValueError("Debe especificar al menos un fallo en 'fallos'.")

        conc = max(1, min(int(concurrencia), 10))
        dest_dir = Path(carpeta_destino).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(conc)
        t0 = time.monotonic()

        async def _worker(item: dict[str, Any]) -> dict[str, Any]:
            origen = str(item["origen"])
            expediente = str(item["expediente"])
            anio = str(item["anio"])
            incidente = str(item["incidente"])
            movimiento = str(item["movimiento"])
            nombre = item.get("nombre_archivo")
            if nombre:
                safe_name = Path(str(nombre)).name
                destino = str(dest_dir / safe_name)
            else:
                destino = str(dest_dir / f"{expediente}_{anio}_{incidente}_{movimiento}")

            async with sem:
                try:
                    res = await self.guardar_fallo(
                        origen=origen,
                        expediente=expediente,
                        anio=anio,
                        incidente=incidente,
                        movimiento=movimiento,
                        destino=destino,
                    )
                    return {
                        "ok": True,
                        "expediente": expediente,
                        "anio": anio,
                        "incidente": incidente,
                        "movimiento": movimiento,
                        **res,
                    }
                except Exception as exc:
                    return {
                        "ok": False,
                        "expediente": expediente,
                        "anio": anio,
                        "incidente": incidente,
                        "movimiento": movimiento,
                        "error": str(exc),
                    }

        resultados = await asyncio.gather(*(_worker(it) for it in fallos))
        duracion = round(time.monotonic() - t0, 2)
        descargados = sum(1 for r in resultados if r["ok"])
        return {
            "total": len(resultados),
            "descargados": descargados,
            "errores": len(resultados) - descargados,
            "concurrencia": conc,
            "duracion_segundos": duracion,
            "resultados": resultados,
        }

