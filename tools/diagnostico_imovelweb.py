"""
Diagnóstico do bloqueio do Cloudflare no Imovelweb.

Testa, a partir da máquina onde roda (ex.: runner do GitHub Actions), várias
identidades de navegador do curl_cffi contra:
  1. a página inicial (pega o cookie __cf_bm)
  2. a API de listagem (POST /rplis-api/postings) — a que o scraper usa
  3. a página de um anúncio (detalhe)
Não grava nada e não usa credenciais.

Uso: python tools/diagnostico_imovelweb.py
"""
import re
import time

from curl_cffi import requests as cr

BASE = "https://www.imovelweb.com.br"
API = f"{BASE}/rplis-api/postings"
IDENTIDADES = [
    ("chrome", {}), ("chrome150", {}), ("chrome146", {}), ("chrome142", {}),
    ("chrome136", {}), ("chrome131_android", {}), ("edge101", {}),
    ("safari2601", {}), ("safari260_ios", {}), ("firefox147", {}), ("tor145", {}),
    ("chrome150", {"http_version": "v1"}),
]
CORPO = {
    "q": None, "tipoDeOperacion": "1", "province": "265", "pagina": 1, "sort": "low_price",
    "tipoAnunciante": "ALL", "superficieCubierta": 1, "idunidaddemedida": 1,
    "habitacionesminimo": 0, "habitacionesmaximo": 0, "ambientesminimo": 0, "ambientesmaximo": 0,
}
HEADERS_API = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest",
               "Origin": BASE, "Referer": f"{BASE}/imoveis-venda.html"}


def titulo(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html or "", re.S)
    return (m.group(1).strip() if m else "")[:40]


def main():
    try:
        info = cr.get("https://ipinfo.io/json", timeout=20).json()
        print(f"IP: {info.get('ip')} | {info.get('org')} | {info.get('city')}/{info.get('country')}\n")
    except Exception as e:
        print(f"IP: não consegui descobrir ({e})\n")

    url_detalhe = None
    print(f"{'identidade':24s} {'inicial':28s} {'API listagem':22s} {'detalhe':28s} cookies")
    for nome, extra in IDENTIDADES:
        rotulo = nome + (" http1" if extra else "")
        s = cr.Session(impersonate=nome, **extra)
        linha = [rotulo]
        try:
            r = s.get(f"{BASE}/imoveis-venda.html", timeout=40)
            linha.append(f"{r.status_code} {titulo(r.text)}")
        except Exception as e:
            linha.append(f"ERRO {type(e).__name__}")
        try:
            r = s.post(API, json=CORPO, headers=HEADERS_API, timeout=40)
            if r.status_code == 200:
                d = r.json()
                total = (d.get("paging") or {}).get("total")
                if not url_detalhe and d.get("listPostings"):
                    url_detalhe = BASE + d["listPostings"][0]["url"]
                linha.append(f"200 total={total}")
            else:
                linha.append(f"{r.status_code} {titulo(r.text)}")
        except Exception as e:
            linha.append(f"ERRO {type(e).__name__}")
        try:
            alvo = url_detalhe or f"{BASE}/propriedades/apartamento-a-venda-sao-luiz-belo-horizonte-3010128280.html"
            r = s.get(alvo, timeout=40)
            linha.append(f"{r.status_code} {'avisoInfo ok' if 'avisoInfo' in r.text else titulo(r.text)}")
        except Exception as e:
            linha.append(f"ERRO {type(e).__name__}")
        cookies = ",".join(sorted(k for k in s.cookies.keys() if k.startswith(("cf", "__cf"))))
        print(f"{linha[0]:24s} {linha[1]:28s} {linha[2]:22s} {linha[3]:28s} {cookies}", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
