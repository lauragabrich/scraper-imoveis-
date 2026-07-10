"""
Lista de todas as cidades do Brasil via API do IBGE.
Baixa uma vez e cacheia localmente.
"""
import requests
import json
import os

CACHE_FILE = "cidades_ibge.json"

ESTADOS_IBGE = {
    "AC": 12, "AL": 27, "AP": 16, "AM": 13, "BA": 29, "CE": 23,
    "DF": 53, "ES": 32, "GO": 52, "MA": 21, "MT": 51, "MS": 50,
    "MG": 31, "PA": 15, "PB": 25, "PR": 41, "PE": 26, "PI": 22,
    "RJ": 33, "RN": 24, "RS": 43, "RO": 11, "RR": 14, "SC": 42,
    "SP": 35, "SE": 28, "TO": 17,
}


def get_all_cidades() -> dict[str, list[str]]:
    """Retorna dict {estado: [lista de cidades]} do IBGE."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    print("[IBGE] Baixando lista completa de municípios...", flush=True)
    resultado = {}

    for estado, codigo in ESTADOS_IBGE.items():
        try:
            url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{codigo}/municipios"
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                municipios = r.json()
                cidades = [m.get("nome") for m in municipios if m.get("nome")]
                resultado[estado] = sorted(cidades)
                print(f"  {estado}: {len(cidades)} cidades", flush=True)
        except Exception as e:
            print(f"  {estado}: erro - {e}", flush=True)
            resultado[estado] = []

    # Salva cache
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    total = sum(len(v) for v in resultado.values())
    print(f"[IBGE] Total: {total} municípios em {len(resultado)} estados", flush=True)

    return resultado


def get_cidades_estado(estado: str) -> list[str]:
    """Retorna lista de cidades de um estado."""
    todas = get_all_cidades()
    return todas.get(estado.upper(), [])
