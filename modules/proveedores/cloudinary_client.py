"""Cliente Cloudinary por empresa (subida de fotos de soporte). PEREZOSO: importar este módulo y
construir `CloudinaryClient` NO toca la red ni importa el SDK (patrón CR-1 de `MatiasClient`).

El SDK `cloudinary` solo se importa dentro de `_subir_real`, que corre en un hilo (`asyncio.to_thread`)
porque su API es bloqueante. En tests se inyecta un `uploader` falso → nunca red, nunca import real.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from modules.proveedores.cloudinary_config import CloudinaryCredenciales

# Un uploader es síncrono (corre en hilo): (data, filename) -> secure_url.
Uploader = Callable[[bytes, str | None], str]


class CloudinaryClient:
    """Sube bytes (imagen o PDF) a Cloudinary y devuelve el `secure_url`. Cliente de UNA empresa."""

    def __init__(self, cred: CloudinaryCredenciales, *, uploader: Uploader | None = None) -> None:
        """Guarda credenciales y un uploader inyectable (fake en tests); NO importa el SDK ni abre red."""
        self._cred = cred
        self._uploader = uploader

    async def subir(self, data: bytes, *, filename: str | None = None) -> str:
        """Sube los bytes en un hilo (la API del SDK es bloqueante) y devuelve la URL segura."""
        uploader = self._uploader or self._subir_real
        return await asyncio.to_thread(uploader, data, filename)

    def _subir_real(self, data: bytes, filename: str | None) -> str:
        """Subida real vía el SDK Cloudinary (import perezoso). `resource_type='auto'` → imagen o PDF."""
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=self._cred.cloud_name, api_key=self._cred.api_key,
            api_secret=self._cred.api_secret, secure=True,
        )
        resultado = cloudinary.uploader.upload(data, resource_type="auto", filename=filename)
        return resultado["secure_url"]
