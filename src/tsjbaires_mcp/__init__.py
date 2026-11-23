"""MCP server y cliente para consultar jurisprudencia del TSJ CABA."""

from .client import TSJBairesClient

__version__ = "0.1.0"
__all__ = ["TSJBairesClient", "__version__"]

