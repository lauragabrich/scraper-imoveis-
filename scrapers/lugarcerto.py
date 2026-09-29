"""
Scraper Lugar Certo (lugarcerto.com.br - Diários Associados).

A busca do site é servida por um endpoint JSON (Solr) em /busca/dasearch:
  - method=getcidade  -> cidades de um estado com contagem de anúncios
  - method=getbairro  -> bairros de uma cidade com contagem de anúncios
  - qualquer outro method + filtros (estado, cidade, bairro, limit, offset)
    -> resultados da busca em JSON ({"cnt": total, "rec": [anúncios]})

Uma única chamada aceita até limit=10000. Paginar com offset NÃO é confiável
(a ordenação tem empates instáveis e ~7% dos anúncios se perdem), então a coleta
é segmentada até cada fatia caber numa só chamada:
    estado -> cidade -> (se a cidade tiver > 10000) bairro

A listagem não traz lat/lng, CEP, condomínio, IPTU, fotos, suítes nem telefone.
Esses campos vêm da página de cada anúncio (window.detalheanuncio), buscada em
paralelo quando detalhes=True.
"""
import json
import random
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import requests

from config.settings import settings
from scrapers.base import BaseScraper


def _norm(text: str) -> str:
    """Normaliza para comparação: sem acento, minúsculo, sem espaços extras."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip().lower()


class LugarCertoScraper(BaseScraper):
    """Scraper para Lugar Certo via endpoint JSON da busca."""

    PORTAL_NAME = "lugarcerto"
    SEARCH_URL = "https://www.lugarcerto.com.br/busca/dasearch"
    MAX_POR_BUSCA = 10000  # maior 'limit' aceito numa única chamada

    ESTADOS = [
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
        "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    ]

    TIPOS = {
        "apartamento": "apartamento", "area privativa": "apartamento", "quitinete": "apartamento",
        "apart hotel": "flat", "casa": "casa", "casa em condominio": "casa",
        "cobertura": "cobertura", "lote": "terreno", "lote em condominio": "terreno",
    }
    TIPOS_POR_GRUPO = {"comerciais": "comercial", "rurais": "rural", "lotes": "terreno"}
    # sc_donotipo: "Revenda" = imobiliária/corretor; demais valores seguem em minúsculo
    TIPOS_ANUNCIANTE = {"revenda": "imobiliaria", "particular": "particular"}

    def __init__(self, detalhes: bool = True, workers: int = 4):
        super().__init__()
        self.detalhes = detalhes
        self.workers = workers
        self._local = threading.local()
        self._falhas = 0

    # ------------------------------------------------------------------ HTTP

    def _headers(self):
        return {"User-Agent": random.choice(settings.USER_AGENTS), "Accept": "application/json"}

    def _get_json(self, params: dict, timeout: int = 120):
        """GET no endpoint de busca com retry (429/5xx/timeout: espera crescente)."""
        for tentativa in range(4):
            self.rate_limiter.wait()
            try:
                r = requests.get(self.SEARCH_URL, params=params, headers=self._headers(), timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                print(f"    [lugarcerto] HTTP {r.status_code} em {params}", flush=True)
            except (requests.RequestException, ValueError) as e:
                print(f"    [lugarcerto] Erro {type(e).__name__} em {params}", flush=True)
            time.sleep(30 * (tentativa + 1))
        return None

    # ------------------------------------------------------------ descoberta

    def listar_cidades(self, estado: str) -> list[tuple[str, int]]:
        """Cidades do estado com anúncios, maiores primeiro: [(nome, total)]."""
        data = self._get_json({"method": "getcidade", "estado": estado.lower()})
        if data is None:
            self._falhas += 1  # sem a lista não dá para saber quais cidades faltam
            data = []
        cidades = [(c["n"], int(c["c"])) for c in data if c.get("n") and int(c.get("c") or 0) > 0]
        return sorted(cidades, key=lambda c: -c[1])

    def listar_bairros(self, estado: str, cidade: str) -> list[tuple[str, int]]:
        """Bairros da cidade com anúncios: [(nome, total)]."""
        data = self._get_json({
            "method": "getbairro", "estado": _norm(estado), "cidade": _norm(cidade),
        })
        if data is None:
            self._falhas += 1
            data = []
        return [(b["n"], int(b["c"])) for b in data if b.get("n") and int(b.get("c") or 0) > 0]

    # --------------------------------------------------------------- busca

    def _buscar(self, estado: str, cidade: str, bairro: str = "", offset: int = 0) -> tuple[int, list[dict]]:
        """Uma chamada de busca. Retorna (total, registros do estado/cidade pedidos).

        O servidor ignora filtros que não reconhece (ex.: estado sem anúncios devolve
        o Brasil inteiro), então os registros são validados por estado e cidade.
        Não usar sort=menorpreco: ele adiciona um filtro oculto que exclui anúncios sem preço."""
        params = {
            "method": "busca", "estado": estado.lower(), "cidade": cidade,
            "limit": str(self.MAX_POR_BUSCA), "offset": str(offset),
        }
        if bairro:
            params["bairro"] = bairro
        data = self._get_json(params)
        if data is None:
            self._falhas += 1  # consulta falhou: o segmento não pode ser marcado como feito
            return 0, []
        cidade_norm = _norm(cidade)
        recs = [
            r for r in data.get("rec", [])
            if (r.get("sigladoestado") or "").upper() == estado.upper()
            and _norm(r.get("cidade")) == cidade_norm
        ]
        return int(data.get("cnt") or 0), recs

    def _coletar(self, estado: str, cidade: str, bairro: str = "") -> list[dict]:
        """Todos os registros de um segmento. Pagina só se passar de MAX_POR_BUSCA."""
        total, recs = self._buscar(estado, cidade, bairro)
        offset = self.MAX_POR_BUSCA
        while offset < total:
            recs.extend(self._buscar(estado, cidade, bairro, offset)[1])
            offset += self.MAX_POR_BUSCA
        return recs

    # ------------------------------------------------------------- detalhes

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
        return self._local.session

    def _detalhe(self, rec: dict) -> dict | None:
        """Baixa a página do anúncio e extrai o JSON window.detalheanuncio."""
        url = rec.get("urldestino") or ""
        if url.startswith("//"):
            url = "https:" + url
        if not url:
            return None
        for tentativa in range(3):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN, settings.REQUEST_DELAY_MAX))
            try:
                r = self._session().get(url, headers=self._headers(), timeout=30)
                if r.status_code == 404:
                    return None
                if r.status_code == 200:
                    html = r.content.decode("utf-8", "replace")
                    m = re.search(r"window\.detalheanuncio\s*=\s*", html)
                    return json.JSONDecoder().raw_decode(html[m.end():])[0] if m else None
            except (requests.RequestException, ValueError):
                pass
            time.sleep(10 * (tentativa + 1))
        return None

    def _parse_todos(self, recs: list[dict]) -> list[dict]:
        """Remove duplicatas, busca detalhes (se habilitado) e converte para o schema comum."""
        unicos = list({r["idanuncio"]: r for r in recs if r.get("idanuncio")}.values())
        if self.detalhes and unicos:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                detalhes = list(pool.map(self._detalhe, unicos))
        else:
            detalhes = [None] * len(unicos)
        anuncios = [a for a in (self._parse_listing(r, d) for r, d in zip(unicos, detalhes)) if a]
        if len(anuncios) < len(unicos):
            print(f"  [lugarcerto] AVISO: {len(unicos) - len(anuncios)} anúncios descartados por erro de parse",
                  flush=True)
        return anuncios

    # ----------------------------------------------------------------- run

    def _salvar(self, anuncios: list[dict], estado: str, nome_arquivo: str) -> int:
        if not anuncios:
            return 0
        ok = self.storage.save_anuncios(anuncios, estado, nome_arquivo, self.PORTAL_NAME)
        if not ok:
            self._falhas += 1
        return len(anuncios) if ok else 0

    def run(self, estado: str = "MG", cidade: str = "", limit: int = None, reset: bool = False):
        """Coleta um estado inteiro (ou uma cidade). Retorna (salvos, -1 se concluído)."""
        estado = estado.upper()
        progress = {} if reset else self.storage.get_portal_progress(self.PORTAL_NAME, estado)
        if progress.get("concluido") and not cidade:
            print(f"[lugarcerto] {estado} já concluído, pulando (use --reset para refazer)", flush=True)
            return 0, -1
        feitos = set(progress.get("segmentos_concluidos", []))
        # Consultas/gravações que falharam: o segmento em que ocorreram não é marcado
        # como feito e o estado não é marcado como concluído (a próxima execução refaz)
        self._falhas = 0

        def marcar(segmento: str, falhas_antes: int):
            if self._falhas > falhas_antes:
                print(f"  [lugarcerto] {segmento}: houve falha, será refeito na próxima execução", flush=True)
                return
            feitos.add(segmento)
            self.storage.save_portal_progress(self.PORTAL_NAME, estado, {"segmentos_concluidos": sorted(feitos)})

        print(f"\n{'='*60}\nScraping Lugar Certo: {estado}\n{'='*60}", flush=True)

        cidades = self.listar_cidades(estado)
        if cidade:
            cidades = [c for c in cidades if _norm(c[0]) == _norm(cidade)] or [(cidade, 0)]
        print(f"[lugarcerto] {len(cidades)} cidades com anúncios em {estado} "
              f"({sum(c[1] for c in cidades)} anúncios)", flush=True)

        total_saved = 0
        for i, (nome, qtd) in enumerate(cidades, 1):
            if nome in feitos:
                continue
            print(f"\n[{estado}] Cidade {i}/{len(cidades)}: {nome} ({qtd} anúncios)", flush=True)

            falhas_antes = self._falhas
            if qtd <= self.MAX_POR_BUSCA:
                total_saved += self._salvar(self._parse_todos(self._coletar(estado, nome)), estado, nome)
            else:
                total_saved += self._run_cidade_grande(estado, nome, feitos, marcar)

            marcar(nome, falhas_antes)
            print(f"  → {total_saved} anúncios salvos no total", flush=True)
            if limit and total_saved >= limit:
                print(f"[lugarcerto] Limite de {limit} atingido", flush=True)
                return total_saved, 0

        if self._falhas:
            print(f"\n[lugarcerto] {estado}: {total_saved} anúncios salvos; {self._falhas} falhas, "
                  f"o que faltou será refeito na próxima execução", flush=True)
            return total_saved, 0
        if not cidade:
            self.storage.save_portal_progress(self.PORTAL_NAME, estado, {
                "segmentos_concluidos": sorted(feitos), "concluido": True,
            })
        print(f"\n[lugarcerto] {estado}: {total_saved} anúncios salvos", flush=True)
        return total_saved, -1

    def _run_cidade_grande(self, estado: str, cidade: str, feitos: set, marcar) -> int:
        """Cidade com mais de 10000 anúncios: um arquivo por bairro + um com o resto."""
        falhas_antes = self._falhas
        bairros = self.listar_bairros(estado, cidade)
        if self._falhas > falhas_antes:
            return 0  # sem a lista de bairros não dá para separar o "resto"; refaz na próxima
        print(f"  [lugarcerto] {len(bairros)} bairros em {cidade}", flush=True)
        saved = 0

        for bairro, qtd in bairros:
            segmento = f"{cidade}|{bairro}"
            if segmento in feitos:
                continue
            falhas_antes = self._falhas
            # A busca por bairro casa nomes parciais ("Santo Antônio" traz "Santo Antônio
            # do Pinheiro"); mantém só o bairro exato para não duplicar entre arquivos
            recs = [r for r in self._coletar(estado, cidade, bairro) if _norm(r.get("bairro")) == _norm(bairro)]
            saved += self._salvar(self._parse_todos(recs), estado, f"{cidade}_{bairro}")
            marcar(segmento, falhas_antes)

        # Anúncios sem bairro (ou com bairro fora da lista) só aparecem na busca da cidade
        segmento = f"{cidade}|__outros__"
        if segmento not in feitos:
            falhas_antes = self._falhas
            conhecidos = {_norm(b) for b, _ in bairros}
            resto = [r for r in self._coletar(estado, cidade) if _norm(r.get("bairro")) not in conhecidos]
            print(f"  [lugarcerto] {len(resto)} anúncios fora dos bairros listados", flush=True)
            saved += self._salvar(self._parse_todos(resto), estado, f"{cidade}_outros")
            marcar(segmento, falhas_antes)

        return saved

    # --------------------------------------------------------------- parse

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Não usado (coleta por segmento)."""
        return 0

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Não usado (coleta por segmento)."""
        return []

    def _map_tipo(self, imovel: str, grupo: str) -> str | None:
        tipo = self.TIPOS.get(_norm(imovel))
        if tipo:
            return tipo
        return self.TIPOS_POR_GRUPO.get(_norm(grupo)) or (_norm(imovel) or None)

    @staticmethod
    def _num(value, cast=float):
        """Converte para número; 0 e vazio viram None (o portal usa 0 para 'não informado')."""
        try:
            n = cast(float(value))
            return n if n else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _idade(valor) -> int | None:
        """sc_idadeimovel é o ANO de construção (ex.: 2019), não a idade."""
        try:
            ano = int(float(valor))
        except (TypeError, ValueError):
            return None
        atual = time.gmtime().tm_year
        return atual - ano if 1800 < ano <= atual else None

    def _parse_listing(self, rec: dict, det: dict | None) -> dict | None:
        """Converte registro da busca (+ detalhe, se houver) para o schema comum dos portais."""
        try:
            det = det or {}
            url = det.get("urldestino") or rec.get("urldestino") or ""
            if url.startswith("//"):
                url = "https:" + url

            secao = det.get("sc_secao") or rec.get("secao") or ""
            secao_norm = _norm(secao)
            if secao_norm in ("aluguel", "temporada"):
                finalidade, contract = secao_norm, "RENTAL"
            else:
                finalidade, contract = "venda", "SALE"

            imovel = det.get("sc_tipoimovel") or rec.get("imovel") or ""
            grupo = det.get("sc_grupo") or rec.get("grupo") or ""

            preco = self._num(det.get("dc_preco") or rec.get("preco"))
            area = self._num(det.get("ia_areaconstruida") or rec.get("area"))

            # Endereço: detalhe traz rua e número separados; a busca só o texto "Rua X, Bairro, Cidade, UF"
            rua = det.get("sc_endereco")
            if rua and det.get("sc_numero"):
                rua = f"{rua}, {det['sc_numero']}"
            if not rua:
                partes = [p.strip() for p in (rec.get("descricao_local") or "").split(",")]
                rua = partes[0] if len(partes) >= 4 else None

            fotos = [
                img.get("extragrande") or img.get("grande") or img.get("media")
                for img in det.get("imagens") or []
            ]
            fotos = [f for f in fotos if f] or ([rec["urlthumbgrande"]] if rec.get("urlthumbgrande") else [])

            def juntar(lista):
                itens = list(dict.fromkeys(x for x in (lista or []) if x))
                return "|".join(itens) if itens else None

            stamps = []
            if det.get("ia_destaque") == 1:
                stamps.append("DESTAQUE")
            if secao_norm == "lancamento":
                stamps.append("LANCAMENTO")

            return {
                "url": url or None,
                "titulo": det.get("sc_titulodescricao") or rec.get("titulo_anuncio"),
                "descricao": det.get("sc_descricao") or rec.get("descricao"),
                "tipo": self._map_tipo(imovel, grupo),
                "finalidade": finalidade,
                "preco": preco,
                "preco_condominio": self._num(det.get("dc_precocondominio")),
                "iptu": self._num(det.get("dc_iptu") or rec.get("iptu")),
                "area_construida": area,
                # ia_tamanho = área do terreno; em lotes o portal só preenche a área principal
                "area_terreno": self._num(det.get("ia_tamanho"))
                or (area if self._map_tipo(imovel, grupo) == "terreno" else None),
                "quartos": self._num(rec.get("numquartos") or det.get("ia_quarto"), int),
                "suites": self._num(det.get("ia_suite"), int),
                "banheiros": self._num(rec.get("numbanheiros") or det.get("ia_banheiro"), int),
                "vagas": self._num(rec.get("numvagas") or det.get("ia_vagagaragem"), int),
                "rua": rua,
                "bairro": det.get("sc_bairro") or rec.get("bairro"),
                "cidade": det.get("sc_cidade") or rec.get("cidade"),
                "estado": (det.get("sc_estado") or rec.get("sigladoestado") or "").upper() or None,
                "cep": det.get("sc_cep"),
                "latitude": det.get("dc_lat"),
                "longitude": det.get("dc_lng"),
                "fotos_urls": "|".join(fotos) or None,
                "image_count": det.get("ia_numimgs") or len(fotos),
                "data_publicacao": det.get("dt_insercao"),
                "data_ultima_atualizacao": det.get("dt_atualizacao"),
                "amenities": juntar(det.get("caracteristicas_imovel")),
                "complex_amenities": juntar([*(det.get("caracteristicas_condominio") or []),
                                             *(det.get("caracteristicas_lazer") or [])]),
                "preco_por_m2": round(preco / area, 2) if preco and area else None,
                "usage_types": grupo or None,
                "property_sub_type": imovel or None,
                "andar": self._num(det.get("ia_andar"), int),
                "total_andares": None,
                "aceita_permuta": None,
                "status_anuncio": "ACTIVE" if det.get("ia_status", 1) == 1 else str(det.get("ia_status")),
                "anunciante_nome": det.get("sc_dononome") or det.get("sc_contato"),
                "anunciante_telefone": juntar(det.get("telefones_fmt")),
                "listing_id": rec.get("idanuncio"),
                "stamps": "|".join(stamps) or None,
                "contract_type": contract,
                "zona": None,
                "periodo_iptu": None,
                "garantias_aluguel": None,
                "aluguel_total": preco if contract == "RENTAL" else None,
                "imovel_disponivel": True,
                "imovel_atualizado": None,
                # --- campos extras (não existem nos arquivos do VivaReal) ---
                "codigo_imovel_anunciante": det.get("sc_codigoimovel") or None,
                "anunciante_id": rec.get("codigoanunciante") or det.get("codigoanunciante"),
                "tipo_anunciante": self.TIPOS_ANUNCIANTE.get(_norm(det.get("sc_donotipo")),
                                                             _norm(det.get("sc_donotipo")) or None),
                "anunciante_creci": str(det["sc_creci"]).strip() if det.get("sc_creci") else None,
                "idade_imovel": self._idade(det.get("sc_idadeimovel")),
                "lancamento": secao_norm == "lancamento",
                "estagio_obra": None,
                "previsao_entrega": rec.get("prazoentrega") or None,
                "nome_empreendimento": det.get("sc_nomeempreendimento") or None,
                "localizacao_exata": (det.get("ia_exibeendereco") == 1 and det.get("dc_lat") is not None) if det else None,
                "baixou_preco_pct": None,
                "tem_tour_virtual": None,
                "tem_video": bool(det.get("vids")) if det else None,
                "tem_planta": bool(det.get("plantas")) if det else None,
                "unidades_por_andar": self._num(det.get("ia_unidadeandar"), int),
                "elevadores": self._num(det.get("ia_elevador"), int),
                "transporte_proximo": None,
                "outros_portais": None,
                "anuncio_duplicado_id": None,
                "aceita_financiamento": True if det.get("ia_financio") == 1 else None,
                "incorporadora": None,
                "qualidade_anuncio": None,
                "anunciante_nivel": None,
                "anunciante_verificado": None,
                "h3_index": None,
                "localizacao_id": None,
                "descricao_ia": None,
                "salas": self._num(det.get("ia_sala"), int),
                "tem_closet": True if det.get("ia_closet") == 1 else None,
                "formas_pagamento": juntar([nome for campo, nome in (("ia_avista", "à vista"),
                                            ("ia_financio", "financiamento"), ("ia_sinalagio", "sinal + ágio"))
                                            if det.get(campo) == 1]),
                "anunciante_endereco": " - ".join(x for x in (
                    det.get("sc_donoendereco"),
                    "/".join(x for x in ((det.get("sc_donocidade") or "").title(), det.get("sc_donoestado")) if x),
                ) if x) or None,
                "anunciante_site": det.get("sc_donosite") or None,
                "pontos_interesse": None,
                "olx_id": None,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return None
