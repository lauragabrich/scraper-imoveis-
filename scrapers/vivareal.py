"""
Scraper VivaReal (glue-api.vivareal.com, a API interna que o próprio site usa).

Limites da API medidos em set/2026 (mudaram depois da coleta de ago/2026):
  - no máximo ~1.500 anúncios por consulta: a partir de from=1488 a API responde
    "From is above acceptable limit"; e no máximo 24 por página;
  - o tipo de negócio vai em `business` (SALE/RENTAL). O antigo `businessType` é
    ignorado e a API devolve venda;
  - priceMin/priceMax (preço do negócio pedido) e usableAreasMin/usableAreasMax
    funcionam, com limites inclusivos;
  - a ordem padrão dos resultados é estável entre requisições.

Por isso a coleta não usa mais cidades/bairros (a busca de bairros de A a Z deixava
bairros de fora e cada bairro era cortado no teto da API). Segmentação:
    estado × negócio (venda/aluguel) × tipo (usado/lançamento) × faixa de preço
dividida ao meio até ter <= LIMITE_FAIXA anúncios; um preço único acima disso é
dividido por área útil. Cada segmento vira um Parquet e entra no progresso
(progress/vivareal/<UF>.json) quando termina.

Título, descrição e outros campos só vêm no endpoint de detalhe de cada anúncio
(ver DETALHE_CAMPOS), buscado por padrão.
"""
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from config.settings import settings
from scrapers.base import BaseScraper


class VivaRealScraper(BaseScraper):
    """Scraper para VivaReal via API interna, segmentado por faixa de preço."""

    PORTAL_NAME = "vivareal"
    API_URL = "https://glue-api.vivareal.com/v2/listings"
    POR_PAGINA = 24
    MAX_ANUNCIOS_CONSULTA = 1488  # from + size; acima disso a API recusa
    LIMITE_FAIXA = 1400           # folga abaixo do teto (o total muda durante a coleta)

    ESTADOS = {
        "SP": "São Paulo", "RJ": "Rio de Janeiro", "MG": "Minas Gerais",
        "PR": "Paraná", "RS": "Rio Grande do Sul", "SC": "Santa Catarina",
        "BA": "Bahia", "PE": "Pernambuco", "CE": "Ceará", "DF": "Distrito Federal",
        "GO": "Goiás", "PA": "Pará", "AM": "Amazonas", "MA": "Maranhão",
        "ES": "Espírito Santo", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
        "PB": "Paraíba", "RN": "Rio Grande do Norte", "AL": "Alagoas",
        "PI": "Piauí", "SE": "Sergipe", "TO": "Tocantins", "RO": "Rondônia",
        "AC": "Acre", "AP": "Amapá", "RR": "Roraima",
    }

    # (business, listingType) coletados; lançamento para aluguel praticamente não existe
    NEGOCIOS = [("SALE", "USED"), ("SALE", "DEVELOPMENT"), ("RENTAL", "USED")]
    NOMES = {"SALE": "venda", "RENTAL": "aluguel", "USED": "usado", "DEVELOPMENT": "lancamento"}

    # Faixas iniciais de preço; cada uma é subdividida conforme a quantidade de anúncios.
    # hi=None = sem teto. Anúncios sem preço (~0,003%) não entram em nenhuma faixa.
    FAIXAS_INICIAIS = [(0, 999_999), (1_000_000, 9_999_999_999), (10_000_000_000, None)]

    @staticmethod
    def _bandas_finas() -> list[tuple]:
        """Com o estado dividido em partes, ~65 faixas fixas (crescimento de 30%, de R$ 500
        a R$ 10 bi) repartidas entre as partes: cada parte só consulta as suas, em vez de
        todas montarem a divisão do estado inteiro (~5 s por consulta)."""
        cortes, x = [0], 500.0
        while x < 10_000_000_000:
            cortes.append(int(round(x, -2)))
            x *= 1.3
        return [(a, b - 1) for a, b in zip(cortes, cortes[1:])] + [(cortes[-1], None)]
    # Faixas de área útil (m²), usadas só quando um preço único passa do limite
    # (ex.: ~6.500 anúncios a exatamente R$ 500.000 só na capital de SP)
    FAIXAS_AREA = [(0, 49), (50, 69), (70, 99), (100, 149), (150, 99_999_999)]

    ESTAGIOS_OBRA = {
        "PRE_LAUNCH": "Breve lançamento", "PLAN_ONLY": "Na planta",
        "UNDER_CONSTRUCTION": "Em obra", "BUILT": "Pronto",
    }
    STATUS_LANCAMENTO = {"PRE_LAUNCH", "PLAN_ONLY", "UNDER_CONSTRUCTION"}

    # Endpoint de um anúncio: desde ~ago/2026 é a única fonte de título e descrição (a
    # busca parou de enviá-los), além de updatedAt, ano de entrega, amenities em português,
    # transporte próximo e portais onde o anúncio também está. includeFields evita os
    # blocos de recomendações/outros anúncios (~490 KB -> poucos KB).
    DETALHE_URL = "https://glue-api.vivareal.com/v2/listing/{id}"
    DETALHE_CAMPOS = (
        "listing(title,description,updatedAt,deliveredAt,portals,portal,status,"
        "searchableAmenities,mergedAmenities,mergedSearchableAmenities,aiAmenities,"
        "aiSearchableAmenities,nearBy,videoTourLink,buildings,pricingInfos,"
        "displayAddressGeolocation,address,condominiumName,listingsCount,lqs,qualityScores,"
        "attributes,nonActivationReason)"
    )

    def __init__(self, detalhes: bool = True, workers: int = 4, parte: int = 1, partes: int = 1):
        super().__init__()
        self.detalhes = detalhes
        # Requisições em paralelo (listagem e detalhe). Cada uma espera REQUEST_DELAY_MIN..MAX:
        # com 4 workers e 1–3 s dá ~1,5 req/s. O Cloudflare do VivaReal bloqueou por >1h um
        # IP a ~10 req/s; aumente com cuidado.
        self.workers = workers
        # Divisão de um estado em N jobs (--parte 2/4): cada segmento de preço/área
        # pertence a uma parte (crc32 da chave); progresso separado por parte
        self.parte, self.partes = parte, partes
        self.cidade = ""          # --cidade restringe as consultas (addressCity)
        self._divididos = set()   # segmentos já subdivididos (vem do progresso)
        self._falhas = 0
        self.faixas_iniciais = self._bandas_finas() if partes > 1 else self.FAIXAS_INICIAIS

    def _chave_progresso(self, estado: str) -> str:
        return estado if self.partes == 1 else f"{estado}_parte{self.parte}de{self.partes}"

    def _e_meu(self, indice_faixa: int) -> bool:
        """Faixas iniciais repartidas em rodízio entre as partes; tudo que sai da
        subdivisão de uma faixa pertence à mesma parte."""
        return self.partes == 1 or indice_faixa % self.partes == self.parte - 1

    def _get_api_headers(self):
        return {
            "User-Agent": random.choice(settings.USER_AGENTS),
            "x-domain": "www.vivareal.com.br",
            "Accept": "application/json",
        }

    # ------------------------------------------------------------------ busca

    def _consulta(self, estado: str, seg: tuple, inicio: int = 0) -> tuple[int | None, list]:
        """Uma página de resultados. seg = (business, listingType, preço mín, preço máx,
        área mín, área máx); None = sem teto / sem filtro. Retorna (total, anúncios)."""
        business, listing_type, lo, hi, alo, ahi = seg
        params = {
            "addressState": self.ESTADOS.get(estado.upper(), estado),
            "business": business,
            "listingType": listing_type,
            "priceMin": str(lo),
            "size": str(self.POR_PAGINA),
            "from": str(inicio),
            "categoryPage": "RESULT",
        }
        if hi is not None:
            params["priceMax"] = str(hi)
        if alo is not None:
            params["usableAreasMin"] = str(alo)
            if ahi is not None:
                params["usableAreasMax"] = str(ahi)
        if self.cidade:
            params["addressCity"] = self.cidade

        for tentativa in range(5):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN, settings.REQUEST_DELAY_MAX))
            try:
                r = requests.get(self.API_URL, params=params, headers=self._get_api_headers(), timeout=30)
                if r.status_code == 200:
                    busca = r.json().get("search") or {}
                    return busca.get("totalCount"), (busca.get("result") or {}).get("listings") or []
                if r.status_code in (400, 404):
                    return None, []  # parâmetro recusado (ex.: from acima do teto): não adianta repetir
                espera = 60 * (tentativa + 1) if r.status_code in (403, 429) else 10 * (tentativa + 1)
                print(f"    [vivareal] HTTP {r.status_code}, aguardando {espera}s "
                      f"(tentativa {tentativa + 1}/5)", flush=True)
            except (requests.RequestException, ValueError) as e:
                espera = 10 * (tentativa + 1)
                print(f"    [vivareal] {type(e).__name__}, aguardando {espera}s", flush=True)
            time.sleep(espera)
        return None, []

    # ---------------------------------------------------------- segmentação

    def _chave(self, seg: tuple) -> str:
        business, listing_type, lo, hi, alo, ahi = seg
        chave = (f"{self.NOMES[business]}-{self.NOMES[listing_type]}:"
                 f"{lo}-{hi if hi is not None else 'max'}")
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
        preço único, pela área útil."""
        business, listing_type, lo, hi, alo, ahi = seg
        if hi is not None and hi > lo:
            meio = self._dividir(lo, hi)
            return [(business, listing_type, lo, meio, alo, ahi), (business, listing_type, meio + 1, hi, alo, ahi)]
        if alo is None:
            # Anúncios sem área informada ficam fora das faixas de área
            return [(business, listing_type, lo, hi, a, b) for a, b in self.FAIXAS_AREA]
        if ahi is not None and ahi > alo:
            meio = self._dividir(alo, ahi)
            return [(business, listing_type, lo, hi, alo, meio), (business, listing_type, lo, hi, meio + 1, ahi)]
        return None

    def _faixas(self, estado: str, feitos: set):
        """Gera (segmento, total, anúncios da 1ª página) para segmentos com <= LIMITE_FAIXA,
        só das faixas iniciais que pertencem a esta parte."""
        pendentes = [(b, t, lo, hi, None, None) for b, t in self.NEGOCIOS
                     for i, (lo, hi) in enumerate(self.faixas_iniciais) if self._e_meu(i)]
        while pendentes:
            seg = pendentes.pop(0)
            chave = self._chave(seg)
            if chave in feitos:
                continue
            if chave in self._divididos and self._subdividir(seg):
                pendentes[0:0] = self._subdividir(seg)  # já dividido antes: vai direto aos filhos
                continue
            total, pagina1 = self._consulta(estado, seg)
            if total is None:
                print(f"  [vivareal] Falha ao consultar {chave}, pulando (será refeito na próxima execução)", flush=True)
                self._falhas += 1
                continue
            if total > self.LIMITE_FAIXA:
                partes = self._subdividir(seg)
                if partes:
                    self._divididos.add(chave)
                    pendentes[0:0] = partes
                    continue
                print(f"  [vivareal] {chave} tem {total} anúncios e não dá para dividir; "
                      f"coletando os primeiros {self.MAX_ANUNCIOS_CONSULTA}", flush=True)
            yield seg, total, pagina1

    @staticmethod
    def _pertence(anuncio: dict, seg: tuple) -> bool:
        """Os filtros da API casam com QUALQUER preço/área do anúncio (ex.: áreas [61, 70]
        aparecem nas fatias 50-69 e 70-99), o que duplicaria anúncios entre fatias. Cada
        anúncio fica só na fatia do seu preço principal (e área principal, se a fatia é
        por área) — que sempre é uma das fatias em que a API o devolve."""
        _, _, lo, hi, alo, ahi = seg
        preco, area = anuncio.get("preco"), anuncio.get("area_construida")
        if preco is not None and not (lo <= preco < (hi + 1 if hi is not None else float("inf"))):
            return False
        if alo is not None and area is not None and not (alo <= area < (ahi + 1 if ahi is not None else float("inf"))):
            return False
        return True

    # --------------------------------------------------------------- coleta

    def _coletar_faixa(self, estado: str, seg: tuple, total: int, pagina1: list) -> tuple[dict, bool]:
        """Todas as páginas do segmento (em paralelo). Retorna ({id: item da API}, ok);
        ok=False se alguma página falhou mesmo após as tentativas."""
        itens = {x["listing"]["id"]: x for x in pagina1 if (x.get("listing") or {}).get("id")}
        fim = min(total, self.MAX_ANUNCIOS_CONSULTA)
        inicios = range(self.POR_PAGINA, fim, self.POR_PAGINA)
        ok = True
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for total_pagina, pagina in pool.map(lambda i: self._consulta(estado, seg, i), inicios):
                ok = ok and total_pagina is not None
                for x in pagina:
                    lid = (x.get("listing") or {}).get("id")
                    if lid:
                        itens[lid] = x
        return itens, ok

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Não usado (coleta por faixa de preço)."""
        return 0

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Não usado (coleta por faixa de preço)."""
        return []

    def run(self, estado: str = "SP", cidade: str = "", limit: int = None, reset: bool = False):
        """Coleta um estado inteiro (venda, lançamentos e aluguel). Retorna (salvos, -1 se concluído)."""
        estado = estado.upper()
        if estado not in self.ESTADOS:
            print(f"[vivareal] Estado desconhecido: {estado}", flush=True)
            return 0, -1
        self.cidade = cidade

        chave_prog = self._chave_progresso(estado) + (f"_{cidade}" if cidade else "")
        progress = {} if reset else self.storage.get_portal_progress(self.PORTAL_NAME, chave_prog)
        if progress.get("concluido"):
            print(f"[vivareal] {chave_prog} já concluído, pulando (use --reset para refazer)", flush=True)
            return 0, -1
        feitos = set(progress.get("segmentos_concluidos", []))
        self._divididos = set(progress.get("segmentos_divididos", []))
        self._falhas = 0  # consultas/gravações que falharam: impedem marcar como concluído

        def salvar_progresso(concluido=False):
            dados = {"segmentos_concluidos": sorted(feitos), "segmentos_divididos": sorted(self._divididos)}
            if concluido:
                dados["concluido"] = True
            self.storage.save_portal_progress(self.PORTAL_NAME, chave_prog, dados)

        parte_txt = f" (parte {self.parte}/{self.partes})" if self.partes > 1 else ""
        print(f"\n{'='*60}\nScraping VivaReal: {estado}{parte_txt}{' - ' + cidade if cidade else ''}\n{'='*60}",
              flush=True)
        total_saved = 0

        for seg, total, pagina1 in self._faixas(estado, feitos):
            chave = self._chave(seg)
            if not total:
                feitos.add(chave)
                continue

            t0 = time.time()
            itens, ok = self._coletar_faixa(estado, seg, total, pagina1)
            if not ok:
                print(f"  [vivareal] {chave}: páginas falharam, fatia será refeita na próxima execução", flush=True)
                self._falhas += 1
                continue
            parseados = [a for a in (self._parse_listing(x, seg[0]) for x in itens.values()) if a]
            if len(parseados) < len(itens):
                print(f"  [vivareal] AVISO: {len(itens) - len(parseados)} anúncios descartados "
                      f"por erro de parse em {chave}", flush=True)
            # Antes do detalhe, para não gastar requisição com anúncio que é de outra fatia
            anuncios = [a for a in parseados if self._pertence(a, seg)]
            if self.detalhes and anuncios:
                self._enriquecer(anuncios)

            cobertura = 100 * len(itens) / total if total else 100
            print(f"  [{estado}] {chave}: {len(itens)}/{total} anúncios ({cobertura:.1f}%), "
                  f"{len(anuncios)} desta fatia, em {time.time() - t0:.0f}s", flush=True)

            # arquivo = chave sem ":" e "|" (ex.: venda-usado_500000-500000_area_0-49)
            nome = chave.replace(":", "_").replace("|", "_")
            if cidade:
                nome = f"{cidade}_{nome}"
            if anuncios and self.storage.save_anuncios(anuncios, estado, nome, self.PORTAL_NAME):
                total_saved += len(anuncios)
            elif anuncios:
                self._falhas += 1
                continue  # falhou ao salvar: não marca como feito, tenta na próxima execução

            feitos.add(chave)
            salvar_progresso()

            if limit and total_saved >= limit:
                print(f"[vivareal] Limite de {limit} atingido", flush=True)
                return total_saved, 0

        if self._falhas:
            salvar_progresso()
            print(f"\n[vivareal] {chave_prog}: {total_saved} anúncios salvos; {self._falhas} segmentos "
                  f"falharam e serão refeitos na próxima execução", flush=True)
            return total_saved, 0
        salvar_progresso(concluido=True)
        print(f"\n[vivareal] {chave_prog}: {total_saved} anúncios salvos", flush=True)
        return total_saved, -1

    # -------------------------------------------------------------- detalhe

    def _detalhe(self, listing_id) -> dict | None:
        """Campos do endpoint de detalhe. O Cloudflare limita a taxa (429 acima de
        ~8 req/s), então cada worker respeita o delay e espera mais a cada 429."""
        for tentativa in range(4):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN, settings.REQUEST_DELAY_MAX))
            try:
                r = requests.get(self.DETALHE_URL.format(id=listing_id), params={"includeFields": self.DETALHE_CAMPOS},
                                 headers=self._get_api_headers(), timeout=20)
                if r.status_code == 200:
                    return r.json().get("listing") or {}
                if r.status_code == 404:
                    return None
                if r.status_code == 429:
                    time.sleep(60 * (tentativa + 1))
            except (requests.RequestException, ValueError):
                time.sleep(5)
        return None

    def _enriquecer(self, anuncios: list[dict]):
        """Preenche titulo, descricao, data_ultima_atualizacao e idade_imovel pelo detalhe."""
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            detalhes = list(pool.map(self._detalhe, [a.get("listing_id") for a in anuncios]))

        ano_atual = datetime.utcnow().year
        sem_detalhe = 0
        for anuncio, det in zip(anuncios, detalhes):
            if not det:
                sem_detalhe += 1
                continue
            anuncio["titulo"] = det.get("title") or anuncio.get("titulo")
            anuncio["descricao"] = det.get("description") or anuncio.get("descricao")
            anuncio["data_ultima_atualizacao"] = det.get("updatedAt")
            negocio = anuncio.get("contract_type") or "SALE"
            preco_det = next((p for p in det.get("pricingInfos") or [] if p.get("businessType") == negocio), {})
            if preco_det.get("iptuPeriod") and preco_det["iptuPeriod"] != "Period_NONE":
                anuncio["periodo_iptu"] = preco_det["iptuPeriod"]
            if det.get("mergedSearchableAmenities") or det.get("searchableAmenities"):
                # versão em português e mais completa (inclui o que a IA do portal extraiu do texto)
                anuncio["amenities"] = det.get("mergedSearchableAmenities") or det.get("searchableAmenities")
            anuncio["transporte_proximo"] = self._formatar_proximidades(det.get("nearBy"))
            anuncio["outros_portais"] = "|".join(det.get("portals") or []) or None
            anuncio["qualidade_anuncio"] = det.get("lqs") or (det.get("qualityScores") or {}).get("lqsBeta")
            endereco_det = det.get("address") or {}
            h3 = (endereco_det.get("h3") or [{}])[0]
            anuncio["h3_index"] = h3.get("index")
            # Pontos de interesse próximos, com prefixo de categoria do portal (ex.: "BS:" = ponto de ônibus)
            anuncio["pontos_interesse"] = "|".join(endereco_det.get("poisList") or []) or None
            # attributes.olx_id vem como texto "{sale=1516661524}" (ou com rent=...)
            olx = str((det.get("attributes") or {}).get("olx_id") or "").strip("{} ")
            anuncio["olx_id"] = olx or None
            try:
                ano = int(str(det.get("deliveredAt"))[:4])
                if 1800 < ano <= ano_atual:
                    anuncio["idade_imovel"] = ano_atual - ano
                elif ano > ano_atual:
                    anuncio["lancamento"] = True
                    anuncio["previsao_entrega"] = anuncio.get("previsao_entrega") or str(det["deliveredAt"])[:7]
            except (TypeError, ValueError):
                pass

        if sem_detalhe:
            print(f"    [vivareal] AVISO: {sem_detalhe}/{len(anuncios)} anúncios sem detalhe "
                  f"(título/descrição vazios)", flush=True)

    @staticmethod
    def _formatar_endereco(end) -> str | None:
        """account.addresses.billing -> "Rua Girassol, 1088 - Vila Madalena, São Paulo/SP"."""
        if not isinstance(end, dict):
            return None
        rua = ", ".join(str(x) for x in (end.get("street"), end.get("streetNumber"), end.get("complement")) if x)
        local = ", ".join(x for x in (end.get("neighborhood"),
                                       "/".join(x for x in (end.get("city"), end.get("state")) if x)) if x)
        return " - ".join(x for x in (rua, local) if x) or None

    @staticmethod
    def _formatar_proximidades(near_by) -> str | None:
        """nearBy = {"oneKm": {"TP": [...]}, "twoKm": ..., "threeKm": ...} (TP = transporte
        público). Vira "1km: Metrô Moema, Metrô AACD | 2km: ..." sem repetir estações."""
        if not isinstance(near_by, dict):
            return None
        partes, vistos = [], set()
        for chave, rotulo in (("oneKm", "1km"), ("twoKm", "2km"), ("threeKm", "3km")):
            itens = [x for lista in (near_by.get(chave) or {}).values() for x in (lista or [])
                     if x and x not in vistos]
            vistos.update(itens)
            if itens:
                partes.append(f"{rotulo}: {', '.join(itens)}")
        return " | ".join(partes) or None

    def _parse_listing(self, item: dict, business: str = "SALE") -> dict | None:
        """Parse de um anúncio da API - extrai TODOS os campos disponíveis."""

        try:
            listing = item.get("listing", {})
            address = listing.get("address", {})
            pricing = listing.get("pricingInfos", [{}]) or [{}]
            # Um anúncio de venda e aluguel traz os dois preços (ordem não garantida):
            # usa o do negócio que está sendo coletado
            price_info = next((p for p in pricing if p.get("businessType") == business), pricing[0])

            # Mídias: separa fotos de vídeos, tours e plantas (antes iam todos para fotos_urls)
            medias = item.get("medias", []) or []
            images = [m for m in medias if m.get("url") and m.get("type", "IMAGE") == "IMAGE"]
            tipos_midia = {m.get("type") for m in medias}
            fotos = "|".join(m["url"] for m in images) or None
            image_count = len(images)

            # Preço
            preco = price_info.get("price")
            if not preco:
                preco = price_info.get("rentalTotalPrice")

            # Áreas
            usable = listing.get("usableAreas", [])
            total = listing.get("totalAreas", [])

            # Datas
            created = listing.get("createdAt")
            updated = listing.get("updatedAt")

            # URL (o link fica no item, não dentro de listing)
            link = item.get("link") or listing.get("link") or {}
            url = f"https://www.vivareal.com.br{link.get('href', '')}" if link.get("href") else None
            if not url:
                lid = listing.get("id", "")
                url = f"https://www.vivareal.com.br/imovel/{lid}"

            # Coordenadas
            point = address.get("point", {})

            # Amenities
            amenities = listing.get("amenities", [])
            amenities_str = "|".join(amenities) if amenities else None

            # Complex amenities
            complex_raw = listing.get("complexAmenities") or listing.get("condominiumAmenities") or []
            complex_str = "|".join(complex_raw) if complex_raw else None

            # Preço por m²
            area_val = float(usable[0]) if usable and usable[0] else None
            preco_val = float(preco) if preco else None
            preco_por_m2 = round(preco_val / area_val, 2) if preco_val and area_val and area_val > 0 else None

            # Campos adicionais
            usage_types = listing.get("usageTypes", [])
            unit_types = listing.get("unitTypes", [])
            # unitFloor = andar da unidade (número); floors = andares do prédio (lista).
            # Antes: unitFloor era lido como lista, e todo anúncio com andar informado
            # dava TypeError e era descartado (~8%); além disso os dois estavam trocados.
            andar = self._primeiro_int(listing.get("unitFloor"))
            total_andares = self._primeiro_int(listing.get("floors"))

            # Obra: status atual e data prevista para BUILT no calendário
            status_obra = (listing.get("constructionStatus") or "").replace("ConstructionStatus_", "")
            estagio = self.ESTAGIOS_OBRA.get(status_obra, status_obra or None) if status_obra != "NONE" else None
            entrega = next((c.get("date") for c in listing.get("constructionStatusCalendar") or []
                            if c.get("constructionStatus") == "BUILT"), None)

            # Anunciante
            advertiser = item.get("account", {}) or item.get("advertiser", {})
            contact = listing.get("advertiserContact", {})

            # Pricing extra
            rental_info = price_info.get("rentalInfo", {})
            warranties = rental_info.get("warranties", [])
            aluguel_total = price_info.get("rentalTotalPrice") or rental_info.get("monthlyRentalTotalPrice")

            # Stamps
            stamps_raw = list(listing.get("stamps", []) or [])
            if listing.get("publicationType") and listing["publicationType"] != "STANDARD":
                stamps_raw.append(listing["publicationType"])  # PREMIUM / SUPER_PREMIUM = destaque pago
            stamps_str = "|".join(stamps_raw) if stamps_raw else None

            return {
                "url": url,
                # A busca não traz title/description (só o endpoint de detalhe);
                # link.name ("Apartamento com 3 Quartos à venda, 71m²") cobre ~90%
                "titulo": listing.get("title") or link.get("name"),
                "descricao": listing.get("description"),
                "tipo": self._map_tipo(unit_types[0]) if unit_types else None,
                "finalidade": price_info.get("businessType", "SALE").replace("SALE", "venda").replace("RENTAL", "aluguel"),
                "preco": preco_val,
                "preco_condominio": float(price_info.get("monthlyCondoFee")) if price_info.get("monthlyCondoFee") else None,
                "iptu": float(price_info.get("yearlyIptu")) if price_info.get("yearlyIptu") else None,
                "area_construida": area_val,
                "area_terreno": float(total[0]) if total and total[0] else None,
                "quartos": int(listing.get("bedrooms", [0])[0]) if listing.get("bedrooms") else None,
                "suites": int(listing.get("suites", [0])[0]) if listing.get("suites") else None,
                "banheiros": int(listing.get("bathrooms", [0])[0]) if listing.get("bathrooms") else None,
                "vagas": int(listing.get("parkingSpaces", [0])[0]) if listing.get("parkingSpaces") else None,
                "rua": ", ".join(str(x) for x in (address.get("street"), address.get("streetNumber")) if x) or None,
                "bairro": address.get("neighborhood"),
                "cidade": address.get("city"),
                "estado": address.get("stateAcronym"),
                "cep": address.get("zipCode"),
                # ~16% só têm a coordenada aproximada (localizacao_exata=False nesses)
                "latitude": point.get("lat") or point.get("approximateLat"),
                "longitude": point.get("lon") or point.get("approximateLon"),
                "fotos_urls": fotos,
                "image_count": image_count,
                "data_publicacao": created,
                "data_ultima_atualizacao": updated,
                "amenities": amenities_str,
                "complex_amenities": complex_str,
                "preco_por_m2": preco_por_m2,
                "usage_types": "|".join(usage_types) if usage_types else None,
                "property_sub_type": unit_types[0] if unit_types else None,
                "andar": andar,
                "total_andares": total_andares,
                "aceita_permuta": str(listing.get("acceptExchange")) if listing.get("acceptExchange") is not None else None,
                "status_anuncio": listing.get("status"),
                "anunciante_nome": advertiser.get("name") or contact.get("name"),
                "anunciante_telefone": "|".join(dict.fromkeys(
                    str(p) for p in [*(contact.get("phones") or []), listing.get("whatsappNumber")] if p)) or None,
                "listing_id": listing.get("id"),
                "stamps": stamps_str,
                "contract_type": price_info.get("businessType"),
                "zona": address.get("zone"),
                "periodo_iptu": price_info.get("iptuPeriod"),
                "garantias_aluguel": "|".join(warranties) if warranties else None,
                "aluguel_total": float(aluguel_total) if aluguel_total else None,
                "imovel_disponivel": True,
                "imovel_atualizado": None,
                # --- campos extras (mesmas colunas do Lugar Certo e Imovelweb) ---
                "codigo_imovel_anunciante": listing.get("externalId") or None,
                "anunciante_id": listing.get("advertiserId") or advertiser.get("id"),
                "tipo_anunciante": None,  # a API não diferencia imobiliária de particular
                "anunciante_creci": (advertiser.get("licenseNumber") or "").strip() or None,
                "idade_imovel": None,
                "lancamento": listing.get("listingType") == "DEVELOPMENT" or status_obra in self.STATUS_LANCAMENTO,
                "estagio_obra": estagio,
                "previsao_entrega": entrega,
                "nome_empreendimento": listing.get("condominiumName") or None,
                "localizacao_exata": address.get("precision") == "ROOFTOP" if address.get("precision") else None,
                "baixou_preco_pct": None,
                "tem_tour_virtual": "VIDEO_TOUR" in tipos_midia,
                "tem_video": "VIDEO" in tipos_midia,
                "tem_planta": "FLOOR_PLAN" in tipos_midia,
                "unidades_por_andar": listing.get("unitsOnTheFloor") or None,
                "elevadores": None,
                # transporte_proximo, outros_portais, qualidade_anuncio e h3_index só vêm
                # no detalhe (preenchidos em _enriquecer)
                "transporte_proximo": None,
                "outros_portais": None,
                "anuncio_duplicado_id": None,
                "aceita_financiamento": None,
                "incorporadora": "|".join(d["name"] for d in listing.get("propertyDevelopers") or []
                                          if d.get("name")) or None,
                "qualidade_anuncio": None,
                "anunciante_nivel": advertiser.get("tier") or None,
                "anunciante_verificado": (advertiser.get("config") or {}).get("verified"),
                "h3_index": None,
                "localizacao_id": address.get("locationId"),
                "descricao_ia": None,
                "salas": None,
                "tem_closet": None,
                "formas_pagamento": None,
                "anunciante_endereco": self._formatar_endereco((advertiser.get("addresses") or {}).get("billing")),
                "anunciante_site": advertiser.get("websiteUrl") or None,
                "pontos_interesse": None,  # só no detalhe (address.poisList)
                "olx_id": None,            # só no detalhe (attributes.olx_id)
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _primeiro_int(valor) -> int | None:
        """Aceita número ou lista (a API usa os dois formatos); 0 = não informado."""
        if isinstance(valor, list):
            valor = valor[0] if valor else None
        try:
            return int(valor) or None
        except (TypeError, ValueError):
            return None

    def _map_tipo(self, unit_type: str) -> str | None:
        if not unit_type:
            return None
        mapping = {
            "APARTMENT": "apartamento", "HOME": "casa",
            "CONDOMINIUM": "casa", "LAND": "terreno",
            "PENTHOUSE": "cobertura", "FLAT": "flat",
            "COMMERCIAL": "comercial", "FARM": "rural",
        }
        return mapping.get(unit_type.upper(), unit_type.lower())
