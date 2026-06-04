"""Cifrado AEAD para secretos por empresa (regla no negociable #5).

Los secretos por empresa (URL de conexión del tenant, MATIAS, Cloudinary, token de bot)
se guardan cifrados en el control DB con SECRETS_MASTER_KEY. Aquí solo el primitivo;
las tablas viven en migrations/control.

Formato BYTEA en una sola columna: nonce(12) || ciphertext+tag. La tabla secretos_empresa
usa columna `nonce` aparte; tenant_databases.connection_url_cifrada empaqueta nonce+ct.
"""
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(master: str) -> bytes:
    """Deriva una clave AES-256 (32 bytes) determinística del master key."""
    return hashlib.sha256(master.encode("utf-8")).digest()


def encrypt(plaintext: str, master: str) -> bytes:
    """Cifra y empaqueta nonce(12) || ciphertext en un solo blob (para columnas BYTEA únicas)."""
    nonce = os.urandom(12)
    ct = AESGCM(_key(master)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct


def decrypt(blob: bytes, master: str) -> str:
    """Descifra un blob nonce(12) || ciphertext producido por encrypt()."""
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(_key(master)).decrypt(nonce, ct, None).decode("utf-8")


def encrypt_split(plaintext: str, master: str) -> tuple[bytes, bytes]:
    """Cifra devolviendo (valor_cifrado, nonce) por separado (tabla secretos_empresa)."""
    nonce = os.urandom(12)
    ct = AESGCM(_key(master)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ct, nonce


def decrypt_split(ciphertext: bytes, nonce: bytes, master: str) -> str:
    """Descifra desde columnas separadas (valor_cifrado, nonce)."""
    return AESGCM(_key(master)).decrypt(nonce, ciphertext, None).decode("utf-8")
