"""Empresa resuelta para un request: identidad + URLs de su base ya descifradas."""
from dataclasses import dataclass

from core.db.urls import to_async, to_sync


@dataclass(frozen=True, slots=True)
class ResolvedTenant:
    id: int
    slug: str
    estado: str
    db_name: str
    connection_url: str  # base postgresql://... (descifrada en memoria, por request)

    @property
    def async_url(self) -> str:
        return to_async(self.connection_url)

    @property
    def sync_url(self) -> str:
        return to_sync(self.connection_url)
