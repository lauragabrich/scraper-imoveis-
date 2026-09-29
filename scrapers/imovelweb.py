"""
Scraper Imovelweb (imovelweb.com.br - grupo Navent/QuintoAndar).

O site fica atrás do Cloudflare:
  - requests/curl comuns levam 403 ("Just a moment..."): usamos curl_cffi, que imita
    o handshake TLS do Chrome;
  - as páginas HTML de listagem só abrem até a página 4 sem resolver o desafio JS.

Por isso a coleta usa a API interna que o próprio site chama no navegador:
    POST https://www.imovelweb.com.br/rplis-api/postings
Ela aceita páginas profundas, mas tem limites:
  - 30 anúncios por página (fixo) e no máximo 1000 páginas por consulta (30.000);
  - a ordenação desempata de forma aleatória a cada requisição, então paginar uma
    consulta perde ~3% dos anúncios (aparecem duplicados no lugar). Uma segunda
    passada com outra ordenação (--passadas 2) recupera a maior parte.

Segmentação: estado × operação (venda/aluguel) × faixa de preço. A faixa é dividida
ao meio até ter <= LIMITE_FAIXA anúncios. Cada faixa vira um arquivo Parquet e entra
no progresso quando termina, então uma execução interrompida continua de onde parou.
Anúncios sem preço (poucos, ~0,005%) não entram em nenhuma faixa.
"""
import json
import math
import random
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

from config.settings import settings
from scrapers.base import BaseScraper

try:
    from curl_cffi import requests as cffi_requests
except ImportError:  # pragma: no cover - dependência obrigatória só para este portal
    cffi_requests = None


class ImovelwebScraper(BaseScraper):
    """Scraper para Imovelweb via API interna rplis-api/postings."""

    PORTAL_NAME = "imovelweb"
    BASE_URL = "https://www.imovelweb.com.br"
    API_URL = "https://www.imovelweb.com.br/rplis-api/postings"
    POR_PAGINA = 30
    MAX_PAGINAS = 1000
    LIMITE_FAIXA = 29000  # folga abaixo de 30.000 (o total muda durante a coleta)

    # IDs de "province" do Imovelweb
    PROVINCIAS = {
        "AC": "241", "AL": "242", "AP": "243", "AM": "244", "BA": "245", "CE": "246",
        "DF": "247", "ES": "248", "GO": "249", "MA": "250", "MT": "251", "MS": "252",
        "MG": "253", "PA": "254", "PB": "255", "PR": "256", "PE": "257", "PI": "258",
        "RJ": "259", "RN": "260", "RS": "261", "RO": "262", "RR": "263", "SC": "264",
        "SP": "265", "SE": "266", "TO": "267",
    }
    ESTADOS = list(PROVINCIAS.keys())

    OPERACOES = {"venda": "1", "aluguel": "2"}

    # Faixas iniciais de preço; cada uma é subdividida conforme a quantidade de anúncios.
    # hi=None = sem teto (preços absurdos/digitados errado, poucos anúncios).
    FAIXAS_INICIAIS = [(0, 999_999), (1_000_000, 9_999_999_999), (10_000_000_000, None)]
    # Faixas de área útil (m²), usadas só quando um preço único passa do limite
    FAIXAS_AREA = [(0, 49), (50, 79), (80, 119), (120, 199), (200, 99_999_999)]

    ORDENACOES = ["low_price", "more_recent", "high_price"]

    CORPO_BASE = {
        "q": None, "direccion": None, "moneda": None, "preciomin": None, "preciomax": None,
        "services": "", "general": "", "searchbykeyword": "", "amenidades": "",
        "caracteristicasprop": None, "comodidades": "", "disposicion": None, "roomType": "",
        "outside": "", "areaPrivativa": "", "areaComun": "", "multipleRets": "",
        "tipoDePropiedad": None, "subtipoDePropiedad": None, "tipoDeOperacion": "1",
        "garages": None, "antiguedad": None, "expensasminimo": None, "expensasmaximo": None,
        "habitacionesminimo": 0, "habitacionesmaximo": 0, "ambientesminimo": 0,
        "ambientesmaximo": 0, "banos": None, "superficieCubierta": 1, "idunidaddemedida": 1,
        "metroscuadradomin": None, "metroscuadradomax": None, "tipoAnunciante": "ALL",
        "grupoTipoDeMultimedia": "", "publicacion": None, "sort": "low_price",
        "etapaDeDesarrollo": "", "auctions": None, "polygonApplied": None,
        "idInmobiliaria": None, "excludePostingContacted": "", "banks": "", "places": "",
        "condominio": "", "pagina": 1, "city": None, "province": None, "zone": None,
        "valueZone": None, "subZone": None, "coordenates": None,
    }

    # publisher.publisherTypeId (conferido pelos nomes: Tecnisa/Cyrela = 3, construtoras = 6)
    TIPOS_ANUNCIANTE = {
        "1": "particular", "2": "imobiliaria", "4": "imobiliaria",
        "3": "incorporadora", "6": "construtora",
    }
    # Estágios de obra (feature CFT200) que indicam imóvel ainda não entregue
    ESTAGIOS_LANCAMENTO = {"breve lançamento", "na planta", "em obra"}

    TIPOS = {
        "apartamentos": "apartamento", "casas": "casa", "terrenos": "terreno",
        "comerciais": "comercial", "rurais": "rural", "coberturas": "cobertura",
        "flats": "flat", "imoveis novos verticais": "apartamento",
        "imoveis novos horizontais": "casa",
    }

    def __init__(self, workers: int = 4, passadas: int = 1, detalhes: bool = True,
                 parte: int = 1, partes: int = 1):
        if cffi_requests is None:
            raise RuntimeError("Imovelweb requer curl_cffi: pip install curl_cffi")
        super().__init__()
        self.workers = workers
        self.passadas = max(1, min(passadas, len(self.ORDENACOES)))
        # Página de cada anúncio: única fonte da lista completa de características
        # (imóvel e condomínio) e da data de publicação. ~7 páginas/s com 12 workers.
        self.detalhes = detalhes
        # Divisão de um estado em N jobs (--parte 2/4): cada segmento de preço/área
        # pertence a uma parte (crc32 da chave); progresso separado por parte
        self.parte, self.partes = parte, partes
        self._local = threading.local()
        self._divididos = set()  # chaves de segmentos já subdivididos (vem do progresso)
        self._folhas = set()     # chaves de segmentos finais já conhecidos (vem do progresso)
        self._falhas = 0

    def _chave_progresso(self, estado: str) -> str:
        return estado if self.partes == 1 else f"{estado}_parte{self.parte}de{self.partes}"

    def _e_meu(self, chave: str) -> bool:
        return self.partes == 1 or zlib.crc32(chave.encode("utf-8")) % self.partes == self.parte - 1

    # ------------------------------------------------------------------ HTTP

    def _nova_sessao(self):
        """Sessão com fingerprint TLS do Chrome + cookie __cf_bm obtido numa página comum."""
        s = cffi_requests.Session(impersonate="chrome")
        try:
            s.get(f"{self.BASE_URL}/imoveis-venda.html", timeout=40)
        except Exception:
            pass
        return s

    def _sessao(self, renovar: bool = False):
        if renovar or not hasattr(self._local, "session"):
            self._local.session = self._nova_sessao()
        return self._local.session

    def _post(self, corpo: dict) -> dict | None:
        """POST na API com retry. 403/429 renovam a sessão e esperam cada vez mais."""
        headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/imoveis-venda.html",
        }
        for tentativa in range(5):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN / 2, settings.REQUEST_DELAY_MAX / 2))
            try:
                r = self._sessao().post(self.API_URL, json=corpo, headers=headers, timeout=60)
                if r.status_code == 200:
                    return r.json()
                espera = 60 * (tentativa + 1) if r.status_code in (403, 429) else 10 * (tentativa + 1)
                print(f"    [imovelweb] HTTP {r.status_code}, aguardando {espera}s "
                      f"(tentativa {tentativa + 1}/5)", flush=True)
            except Exception as e:
                espera = 10 * (tentativa + 1)
                print(f"    [imovelweb] {type(e).__name__}, aguardando {espera}s", flush=True)
            time.sleep(espera)
            self._sessao(renovar=True)
        return None

    def _detalhe(self, posting: dict) -> dict | None:
        """Baixa a página do anúncio e extrai do objeto JS `avisoInfo`:
        características (por categoria), data de publicação e vídeos."""
        url = posting.get("url") or ""
        if url.startswith("/"):
            url = self.BASE_URL + url
        if not url:
            return None
        for tentativa in range(3):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN / 4, settings.REQUEST_DELAY_MAX / 4))
            try:
                r = self._sessao().get(url, timeout=60)
                if r.status_code == 404:
                    return None  # anúncio saiu do ar entre a listagem e o detalhe
                if r.status_code == 200:
                    return self._extrair_detalhe(r.text)
                espera = 60 * (tentativa + 1) if r.status_code in (403, 429) else 10 * (tentativa + 1)
            except Exception:
                espera = 10 * (tentativa + 1)
            time.sleep(espera)
            self._sessao(renovar=True)
        return None

    @staticmethod
    def _extrair_detalhe(html: str) -> dict | None:
        """avisoInfo é JS (chaves com aspas simples), mas os valores que interessam são JSON."""
        i = html.find("const avisoInfo")
        if i < 0:
            return None
        bloco = html[i:i + 80000]
        det = {}

        def valor_json(chave):
            m = re.search(rf"'{chave}'\s*:\s*", bloco)
            if not m:
                return None
            try:
                return json.JSONDecoder().raw_decode(bloco, m.end())[0]
            except ValueError:
                return None

        # {"Áreas comuns": {"10140": {"label": "Piscina", ...}}, "Áreas privativas": {...}, ...}
        caracteristicas = valor_json("generalFeatures") or {}
        det["caracteristicas"] = {
            cat: [f.get("label") for f in (itens or {}).values() if isinstance(f, dict) and f.get("label")]
            for cat, itens in caracteristicas.items() if isinstance(itens, dict)
        }
        m = re.search(r"'publicationDateFormatted'\s*:\s*'([^']*)'", bloco)
        det["data_publicacao"] = m.group(1) if m and m.group(1) else None
        det["videos"] = valor_json("videos") or []
        return det

    def _consulta(self, estado: str, operacao: str, seg: tuple, pagina: int, sort: str):
        """seg = (preço mín, preço máx, área mín, área máx); None = sem teto / sem filtro."""
        lo, hi, alo, ahi = seg
        corpo = dict(self.CORPO_BASE)
        corpo.update({
            "province": self.PROVINCIAS[estado],
            "tipoDeOperacion": self.OPERACOES[operacao],
            "preciomin": str(lo),
            "preciomax": str(hi) if hi is not None else None,
            "moneda": 3,  # R$
            "pagina": pagina,
            "sort": sort,
        })
        if alo is not None:  # área útil (superficieCubierta=1), limites inclusivos
            corpo["metroscuadradomin"] = str(alo)
            corpo["metroscuadradomax"] = str(ahi) if ahi is not None else None
        return self._post(corpo)

    # ---------------------------------------------------------- segmentação

    @staticmethod
    def _chave(operacao: str, seg: tuple) -> str:
        lo, hi, alo, ahi = seg
        chave = f"{operacao}:{lo}-{hi if hi is not None else 'max'}"
        if alo is not None:
            chave += f"|area:{alo}-{ahi if ahi is not None else 'max'}"
        return chave

    @staticmethod
    def _dividir(lo: int, hi: int) -> int:
        """Ponto de corte: geométrico em faixas muito largas, aritmético nas estreitas."""
        if lo > 0 and hi / lo > 4:
            return int(math.sqrt(lo * hi))
        return (lo + hi) // 2

    def _subdividir(self, seg: tuple) -> list[tuple] | None:
        """Divide um segmento grande demais: primeiro pelo preço; quando a faixa já é um
        preço único (há ~35 mil anúncios a exatamente R$ 350.000 em SP), pela área útil."""
        lo, hi, alo, ahi = seg
        if hi is not None and hi > lo:
            meio = self._dividir(lo, hi)
            return [(lo, meio, alo, ahi), (meio + 1, hi, alo, ahi)]
        if alo is None:
            # Anúncios sem área informada ficam fora das faixas de área (~0,03%)
            return [(lo, hi, a, b) for a, b in self.FAIXAS_AREA]
        if ahi is not None and ahi > alo:
            meio = self._dividir(alo, ahi)
            return [(lo, hi, alo, meio), (lo, hi, meio + 1, ahi)]
        return None

    def _faixas(self, estado: str, operacao: str, feitos: set):
        """Gera (segmento, total, dados_pagina_1) para segmentos com <= LIMITE_FAIXA anúncios."""
        pendentes = [(lo, hi, None, None) for lo, hi in self.FAIXAS_INICIAIS]
        while pendentes:
            seg = pendentes.pop(0)
            chave = self._chave(operacao, seg)
            if chave in feitos:
                continue
            # Segmento final de outra parte, já conhecido: nem consulta
            if chave in self._folhas and not self._e_meu(chave):
                continue
            # Já dividido numa execução anterior: vai direto aos filhos sem consultar de novo
            if chave in self._divididos and self._subdividir(seg):
                pendentes[0:0] = self._subdividir(seg)
                continue
            dados = self._consulta(estado, operacao, seg, 1, self.ORDENACOES[0])
            if dados is None:
                print(f"  [imovelweb] Falha ao consultar {self._chave(operacao, seg)}, pulando "
                      f"(será refeito na próxima execução)", flush=True)
                self._falhas += 1
                continue
            total = int((dados.get("paging") or {}).get("total") or 0)
            if total > self.LIMITE_FAIXA:
                partes = self._subdividir(seg)
                if partes:
                    self._divididos.add(chave)
                    pendentes[0:0] = partes
                    continue
                print(f"  [imovelweb] {self._chave(operacao, seg)} tem {total} anúncios e não dá para "
                      f"dividir; coletando os primeiros {self.MAX_PAGINAS * self.POR_PAGINA}", flush=True)
            self._folhas.add(chave)
            yield seg, total, dados

    # --------------------------------------------------------------- coleta

    def _coletar_faixa(self, estado: str, operacao: str, seg: tuple, total: int,
                       pagina1: dict) -> tuple[dict, bool]:
        """Todas as páginas do segmento (em paralelo). Retorna ({postingId: posting}, ok);
        ok=False se alguma página da 1ª passada falhou mesmo após as tentativas."""
        paginas = min(math.ceil(total / self.POR_PAGINA), self.MAX_PAGINAS)
        anuncios = {p["postingId"]: p for p in pagina1.get("listPostings") or [] if p.get("postingId")}
        ok = True

        for n, sort in enumerate(self.ORDENACOES[:self.passadas]):
            inicio = 2 if n == 0 else 1  # a página 1 da primeira ordenação já veio no _faixas
            if n > 0 and len(anuncios) >= total:
                break

            def baixar(pagina, sort=sort):
                return self._consulta(estado, operacao, seg, pagina, sort)

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for dados in pool.map(baixar, range(inicio, paginas + 1)):
                    if dados is None:
                        ok = ok and n > 0  # falha na 2ª passada só reduz o reforço de cobertura
                        continue
                    for p in dados.get("listPostings") or []:
                        if p.get("postingId"):
                            anuncios[p["postingId"]] = p

        return anuncios, ok

    def run(self, estado: str = "SP", cidade: str = "", limit: int = None, reset: bool = False):
        """Coleta um estado inteiro (venda + aluguel). Retorna (salvos, -1 se concluído).
        O parâmetro cidade é ignorado: a segmentação é por faixa de preço."""
        estado = estado.upper()
        if estado not in self.PROVINCIAS:
            print(f"[imovelweb] Estado desconhecido: {estado}", flush=True)
            return 0, -1

        chave_prog = self._chave_progresso(estado)
        progress = {} if reset else self.storage.get_portal_progress(self.PORTAL_NAME, chave_prog)
        if progress.get("concluido"):
            print(f"[imovelweb] {chave_prog} já concluído, pulando (use --reset para refazer)", flush=True)
            return 0, -1
        feitos = set(progress.get("segmentos_concluidos", []))
        self._divididos = set(progress.get("segmentos_divididos", []))
        self._folhas = set(progress.get("segmentos_folha", []))
        self._falhas = 0  # consultas/gravações que falharam: impedem marcar como concluído

        def salvar_progresso(concluido=False):
            dados = {"segmentos_concluidos": sorted(feitos), "segmentos_divididos": sorted(self._divididos),
                     "segmentos_folha": sorted(self._folhas)}
            if concluido:
                dados["concluido"] = True
            self.storage.save_portal_progress(self.PORTAL_NAME, chave_prog, dados)

        parte_txt = f" (parte {self.parte}/{self.partes})" if self.partes > 1 else ""
        print(f"\n{'='*60}\nScraping Imovelweb: {estado}{parte_txt}\n{'='*60}", flush=True)
        total_saved = 0

        for operacao in self.OPERACOES:
            for seg, total, pagina1 in self._faixas(estado, operacao, feitos):
                chave = self._chave(operacao, seg)
                if not self._e_meu(chave):
                    continue  # segmento de outra parte
                if total == 0:
                    feitos.add(chave)
                    continue

                t0 = time.time()
                brutos, ok = self._coletar_faixa(estado, operacao, seg, total, pagina1)
                if not ok:
                    print(f"  [imovelweb] {chave}: páginas falharam, segmento será refeito na próxima execução",
                          flush=True)
                    self._falhas += 1
                    continue

                detalhes = {}
                if self.detalhes and brutos:
                    postings = list(brutos.values())
                    with ThreadPoolExecutor(max_workers=self.workers) as pool:
                        detalhes = dict(zip((p["postingId"] for p in postings), pool.map(self._detalhe, postings)))
                    sem = sum(1 for d in detalhes.values() if d is None)
                    if sem:
                        print(f"  [imovelweb] AVISO: {sem}/{len(postings)} anúncios sem detalhe em {chave}", flush=True)

                anuncios = [a for a in (self._parse_listing(p, operacao, detalhes.get(pid))
                                        for pid, p in brutos.items()) if a]
                if len(anuncios) < len(brutos):
                    print(f"  [imovelweb] AVISO: {len(brutos) - len(anuncios)} anúncios descartados "
                          f"por erro de parse em {chave}", flush=True)

                cobertura = 100 * len(brutos) / total if total else 100
                print(f"  [{estado}] {chave}: {len(brutos)}/{total} anúncios "
                      f"({cobertura:.1f}%) em {time.time() - t0:.0f}s", flush=True)

                # arquivo = chave sem ":" e "|" (ex.: venda_350000-350000_area_0-49)
                nome = chave.replace(":", "_").replace("|", "_")
                if anuncios and self.storage.save_anuncios(anuncios, estado, nome, self.PORTAL_NAME):
                    total_saved += len(anuncios)
                elif anuncios:
                    self._falhas += 1
                    continue  # falhou ao salvar: não marca como feita, tenta na próxima execução

                feitos.add(chave)
                salvar_progresso()

                if limit and total_saved >= limit:
                    print(f"[imovelweb] Limite de {limit} atingido", flush=True)
                    return total_saved, 0

        if self._falhas:
            salvar_progresso()
            print(f"\n[imovelweb] {chave_prog}: {total_saved} anúncios salvos; {self._falhas} segmentos "
                  f"falharam e serão refeitos na próxima execução", flush=True)
            return total_saved, 0
        salvar_progresso(concluido=True)
        print(f"\n[imovelweb] {chave_prog}: {total_saved} anúncios salvos", flush=True)
        return total_saved, -1

    # --------------------------------------------------------------- parse

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Não usado (coleta por faixa de preço)."""
        return 0

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Não usado (coleta por faixa de preço)."""
        return []

    @staticmethod
    def _feature(features: dict, feature_id: str, cast=float):
        """Valor de uma mainFeature (ex.: CFT2 = quartos). Em lançamentos usa o mínimo."""
        f = (features or {}).get(feature_id) or {}
        valor = f.get("value") or f.get("minValue")
        try:
            return cast(float(str(valor).replace(",", ".")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _percentual(valor) -> float | None:
        """lowPricePercentage vem como texto ("9%")."""
        try:
            return float(str(valor).replace("%", "").replace(",", ".").strip()) if valor else None
        except ValueError:
            return None

    def _parse_listing(self, p: dict, operacao: str, det: dict | None = None) -> dict | None:
        """Converte um posting da API (+ detalhe da página, se houver) para o schema comum."""
        det = det or {}
        # Características da página: "Áreas comuns" = condomínio; demais categorias
        # (Áreas privativas, Comodidades, Ambientes...) = imóvel
        comuns, privativas = [], []
        for categoria, itens in (det.get("caracteristicas") or {}).items():
            (comuns if "comun" in categoria.lower() else privativas).extend(itens)
        try:
            # Preço da operação consultada (um anúncio pode ter venda e aluguel)
            preco = baixou_preco = None
            for pot in p.get("priceOperationTypes") or []:
                if (pot.get("operationType") or {}).get("operationTypeId") == self.OPERACOES[operacao]:
                    precos = [x.get("amount") for x in pot.get("prices") or [] if x.get("amount")]
                    preco = float(precos[0]) if precos else None
                    baixou_preco = pot.get("lowPricePercentage")

            # Estágio da obra (CFT200) e previsão de entrega (CFT201): vêm em flagsFeatures
            # nos imóveis e em developmentFeatures nos empreendimentos
            obra = {f["featureId"]: f.get("label") for f in p.get("flagsFeatures") or [] if f and f.get("featureId")}
            for grupo_dev in (p.get("developmentFeatures") or {}).values():
                for fid, f in (grupo_dev or {}).items():
                    if f and f.get("label"):
                        obra.setdefault(fid, f["label"])
            estagio = obra.get("CFT200")
            empreendimento = p.get("postingType") == "DEVELOPMENT"

            features = p.get("mainFeatures") or {}
            area_util = self._feature(features, "CFT101")
            area_total = self._feature(features, "CFT100")
            area = area_util or area_total

            # Localização: cadeia ZONA (bairro) -> CIUDAD -> PROVINCIA
            loc = (p.get("postingLocation") or {})
            niveis = {}
            no = loc.get("location")
            while no:
                niveis[no.get("label")] = no
                no = no.get("parent")
            geo = ((loc.get("postingGeolocation") or {}).get("geolocation")) or {}

            pictures = (p.get("visiblePictures") or {}).get("pictures") or []
            fotos = [x.get("url730x532") or x.get("url360x266") for x in pictures]
            fotos = [f for f in fotos if f]

            publisher = p.get("publisher") or {}
            publication = p.get("publication") or {}
            tipo_nome = ((p.get("realEstateType") or {}).get("name") or "")
            tipo_norm = tipo_nome.lower().replace("ó", "o").replace("í", "i")

            expenses = (p.get("expenses") or {}).get("amount")
            iptu = p.get("iptu")
            if isinstance(iptu, dict):
                iptu = iptu.get("amount")

            stamps = []
            if p.get("premier"):
                stamps.append("PREMIER")
            for flag in p.get("flagsFeatures") or []:
                if flag and flag.get("label"):
                    stamps.append(flag["label"].upper())

            # O campo license às vezes vem com lixo (".", "a", "000"); só vale com dígito não-zero
            creci = (publisher.get("license") or "").strip()
            if not any(c in "123456789" for c in creci):
                creci = None
            visibilidade = (loc.get("address") or {}).get("visibility")

            url = p.get("url")
            return {
                "url": f"{self.BASE_URL}{url}" if url and url.startswith("/") else url,
                "titulo": p.get("title") or p.get("generatedTitle"),
                "descricao": p.get("descriptionNormalized"),
                "tipo": self.TIPOS.get(tipo_norm, tipo_norm or None),
                "finalidade": operacao,
                "preco": preco,
                "preco_condominio": float(expenses) if expenses else None,
                "iptu": float(iptu) if iptu else None,
                "area_construida": area,
                "area_terreno": area_total,
                "quartos": self._feature(features, "CFT2", int),
                "suites": self._feature(features, "CFT4", int),
                "banheiros": self._feature(features, "CFT3", int),
                "vagas": self._feature(features, "CFT7", int),
                "rua": ((loc.get("address") or {}).get("name") or "").strip() or None,
                "bairro": (niveis.get("ZONA") or niveis.get("SUBZONA") or {}).get("name"),
                "cidade": (niveis.get("CIUDAD") or {}).get("name"),
                "estado": (niveis.get("PROVINCIA") or {}).get("acronym"),
                "cep": None,
                "latitude": geo.get("latitude"),
                "longitude": geo.get("longitude"),
                "fotos_urls": "|".join(fotos) or None,
                "image_count": len(fotos),
                "data_publicacao": (det.get("data_publicacao") or publication.get("firstDateOnline")
                                    or publication.get("beginDate")),
                "data_ultima_atualizacao": p.get("modified_date"),
                # Lista completa vem do detalhe; sem ele, só os destaques da listagem
                "amenities": "|".join(dict.fromkeys(
                    x for x in [*privativas, *(p.get("highlightedFeatures") or []), p.get("triggerPill")] if x)) or None,
                "complex_amenities": "|".join(dict.fromkeys(comuns)) or None,
                "preco_por_m2": round(preco / area, 2) if preco and area else None,
                "usage_types": None,
                "property_sub_type": tipo_nome or None,
                "andar": None,
                "total_andares": None,
                "aceita_permuta": None,
                "status_anuncio": p.get("status"),
                "anunciante_nome": publisher.get("name"),
                # mainPhone vem em ~2%; o WhatsApp do anúncio em ~96%
                "anunciante_telefone": "|".join(dict.fromkeys(
                    str(t).strip() for t in (publisher.get("mainPhone"), p.get("whatsApp")) if t)) or None,
                "listing_id": p.get("postingId"),
                "stamps": "|".join(stamps) or None,
                "contract_type": "RENTAL" if operacao == "aluguel" else "SALE",
                "zona": None,
                "periodo_iptu": None,
                "garantias_aluguel": None,
                "aluguel_total": preco if operacao == "aluguel" else None,
                "imovel_disponivel": True,
                "imovel_atualizado": None,
                # --- campos extras (não existem nos arquivos do VivaReal) ---
                "codigo_imovel_anunciante": p.get("postingCode") or None,
                "anunciante_id": publisher.get("publisherId"),
                "tipo_anunciante": self.TIPOS_ANUNCIANTE.get(str(publisher.get("publisherTypeId")),
                                                             publisher.get("publisherTypeId")),
                "anunciante_creci": creci,
                "idade_imovel": self._feature(features, "CFT5", int),
                "lancamento": empreendimento or (estagio or "").lower() in self.ESTAGIOS_LANCAMENTO,
                "estagio_obra": estagio,
                "previsao_entrega": obra.get("CFT201"),
                "nome_empreendimento": p.get("title") if empreendimento else None,
                "localizacao_exata": visibilidade == "EXACT" if visibilidade else None,
                "baixou_preco_pct": self._percentual(baixou_preco),
                "tem_tour_virtual": bool(p.get("hasTour")),
                "tem_video": bool(p.get("hasVideos") or det.get("videos")),
                "tem_planta": bool(p.get("hasPlans")),
                "unidades_por_andar": None,
                "elevadores": None,
                "transporte_proximo": None,
                "outros_portais": None,
                # o próprio Imovelweb aponta quando o anúncio é duplicata de outro
                "anuncio_duplicado_id": (p.get("duplicated") or {}).get("id"),
                "aceita_financiamento": None,
                "incorporadora": None,
                "qualidade_anuncio": None,
                "anunciante_nivel": None,
                "anunciante_verificado": None,
                "h3_index": None,
                "localizacao_id": (loc.get("location") or {}).get("locationId"),
                "descricao_ia": p.get("iadescription") or None,
                "salas": None,
                "tem_closet": None,
                "formas_pagamento": None,
                "anunciante_endereco": None,  # o Imovelweb não publica
                "anunciante_site": None,
                "pontos_interesse": None,
                "olx_id": None,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return None
