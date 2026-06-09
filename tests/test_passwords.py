"""Hashing de contraseñas (core/auth/passwords, ADR 0009 §D3). PURO: sin IO ni DB.

Cubre: roundtrip (hash → verify ok), rechazo de clave mala, y que `verify_password` NUNCA lanza
ante hash None/vacío/corrupto (devuelve False) — base del "sin enumeración de usuarios".
"""
from core.auth.passwords import hash_password, verify_password


def test_hash_roundtrip_verifica():
    h = hash_password("S3creta-larga!")
    assert h != "S3creta-larga!"           # no guarda la clave en claro
    assert h.startswith("$argon2id$")      # argon2id, parámetros embebidos
    assert verify_password("S3creta-larga!", h) is True


def test_hash_no_es_determinista_pero_ambos_verifican():
    # Sal aleatoria: dos hashes de la misma clave difieren, y ambos verifican.
    h1, h2 = hash_password("misma-clave"), hash_password("misma-clave")
    assert h1 != h2
    assert verify_password("misma-clave", h1) and verify_password("misma-clave", h2)


def test_verify_rechaza_clave_mala():
    h = hash_password("correcta")
    assert verify_password("incorrecta", h) is False


def test_verify_no_lanza_con_hash_none_vacio_o_corrupto():
    assert verify_password("x", None) is False        # sin contraseña aún (set-password pendiente)
    assert verify_password("x", "") is False
    assert verify_password("x", "no-es-un-hash-argon2") is False
    assert verify_password("x", "$argon2id$corrupto") is False
