#!/usr/bin/env python3
"""Pruebas básicas de la aplicación Flask."""
import app

client = app.app.test_client()

def test_index():
    r = client.get("/")
    assert r.status_code == 200, r.status_code
    print("index OK")


def test_api_buscar():
    r = client.get("/api/buscar?q=maria&tipo=todos")
    assert r.status_code == 200, r.status_code
    data = r.get_json()
    print("api/buscar OK, total", data["total"])


def test_filter_pacientes():
    r = client.get("/?tipo=pacientes")
    assert r.status_code == 200
    print("pacientes filter OK")


def test_filter_faltantes():
    r = client.get("/?tipo=faltantes")
    assert r.status_code == 200
    print("faltantes filter OK")


def test_filter_localizados():
    r = client.get("/?tipo=localizados")
    assert r.status_code == 200
    print("localizados filter OK")


if __name__ == "__main__":
    test_index()
    test_api_buscar()
    test_filter_pacientes()
    test_filter_faltantes()
    test_filter_localizados()
    print("Todas las pruebas pasaron.")
