# Servidor MCP de Jurisprudencia del TSJ CABA

Servidor [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) en Python para buscar, explorar el tesauro, consultar sentencias vinculadas y extraer el texto completo de los fallos del **Tribunal Superior de Justicia de la Ciudad Autónoma de Buenos Aires** ([jurisprudencia.tsjbaires.gob.ar](https://jurisprudencia.tsjbaires.gob.ar/jurisprudencia/)).

## Requisitos

- Python 3.11 o superior
- [uv](https://docs.astral.sh/uv/)

## Instalación

```bash
uv sync
```

Para ejecutar los tests unitarios y de integración en vivo:

```bash
uv run pytest -v
```

## Herramientas disponibles

| Herramienta | Descripción |
|---|---|
| `buscar_jurisprudencia` | Busca sentencias por número de expediente, carátula, rango de fechas (`DD/MM/AAAA`), texto libre (todas las palabras, frase exacta, algunas palabras, sin palabras), términos del tesauro, secretaría de trámite, ordenamiento y paginación. |
| `obtener_texto_fallo` | Descarga el fallo (`.pdf` de EJE o `.doc` histórico de SGEJ) y extrae su texto completo en Python puro, con soporte de rango de páginas para PDF y límite de caracteres. |
| `descargar_fallo` | Descarga y guarda en disco el archivo original limpio (`.pdf` o `.doc`) en la ruta indicada. |
| `descargar_fallos_masivo` | Descarga múltiples fallos en paralelo (concurrencia configurable de 1 a 10) en una carpeta local. |
| `buscar_tesauro` | Busca voces del tesauro temático oficial (con cantidad de ocurrencias) de forma insensible a tildes, o lista las más utilizadas si `consulta` está vacía. |
| `obtener_sentencias_vinculadas` | Dado un `id_clasif`, consulta precedentes a los que remite la sentencia, fallos posteriores que remiten a ella y sentencias en igual sentido. |
| `listar_secretarias` | Lista los códigos y descripciones de las secretarías de trámite válidas (`SAOyRC`, `SACAyT`, `SAPPJCyF`, `SACyC`, `SAL`, `SAG`, `TSN`). |

## Notas de uso

- **Tildes en texto libre**: el motor del portal del TSJ distingue tildes en las búsquedas de texto libre (ej. `constitución` vs `constitucion`). En cambio, `buscar_tesauro` normaliza tildes automáticamente.
- **Descarga de fallos EJE**: el endpoint del TSJ envuelve algunos PDFs en cabeceras MIME/MTOM; este servidor las limpia automáticamente antes de extraer texto o guardar el archivo.
- **Fallos históricos SGEJ (`.doc`)**: los documentos anteriores a EJE en formato binario Word 97-2003 se extraen directamente en Python sin requerir LibreOffice ni Microsoft Word.

## Configuración en clientes MCP

### Antigravity / Claude Desktop / Cursor (`mcp_config.json`)

```json
{
  "mcpServers": {
    "tsjbaires-jurisprudencia": {
      "command": "uv",
      "args": [
        "--directory",
        "g:/Mi unidad/PycharmProjects/Jurisprudencia",
        "run",
        "tsjbaires-mcp"
      ]
    }
  }
}
```

