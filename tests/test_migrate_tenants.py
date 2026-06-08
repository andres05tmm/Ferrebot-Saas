"""Runner `tools.migrate_tenants` endurecido: códigos de salida (sin Postgres, con seams falseados).

Mockea `_empresas`/`decrypt`/`upgrade_tenant` para verificar que NO falla en silencio: 0 empresas
sale != 0 (salvo --allow-empty), una empresa fallida sale 1, y el camino feliz sale 0.
"""
import tools.migrate_tenants as mt


def _emp(slug: str) -> dict:
    return {"id": 1, "slug": slug, "estado": "activa", "connection_url_cifrada": slug.encode()}


def test_cero_empresas_sale_distinto_de_cero(monkeypatch):
    monkeypatch.setattr(mt, "_empresas", lambda _url: [])
    assert mt.main([]) == 2          # join/filtro roto NO debe pasar como deploy verde


def test_cero_empresas_con_allow_empty_sale_cero(monkeypatch):
    monkeypatch.setattr(mt, "_empresas", lambda _url: [])
    assert mt.main(["--allow-empty"]) == 0


def test_todas_ok_sale_cero(monkeypatch):
    monkeypatch.setattr(mt, "_empresas", lambda _url: [_emp("clinica-demo")])
    monkeypatch.setattr(mt, "decrypt", lambda cifrado, _k: cifrado.decode())
    monkeypatch.setattr(mt, "upgrade_tenant", lambda _url: None)
    assert mt.main([]) == 0


def test_una_falla_sale_uno(monkeypatch):
    monkeypatch.setattr(mt, "_empresas", lambda _url: [_emp("a"), _emp("b")])
    monkeypatch.setattr(mt, "decrypt", lambda cifrado, _k: cifrado.decode())

    def _upgrade(url: str) -> None:
        if url == "b":
            raise RuntimeError("boom")

    monkeypatch.setattr(mt, "upgrade_tenant", _upgrade)
    assert mt.main([]) == 1          # una empresa fallida → exit 1 (las demás igual se migran)
