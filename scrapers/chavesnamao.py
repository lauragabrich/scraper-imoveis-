"""
Scraper Chaves na Mão (chavesnamao.com.br).

O site (Next.js) carrega a listagem por uma API interna que devolve JSON:
    GET https://www.chavesnamao.com.br/api/realestate/listing/items/
        ?level1=imoveis-a-venda&level2=sp&filtro=pmin:100000,pmax:200000&pg=3
Cada anúncio da busca já traz quase tudo: preço, condomínio/IPTU (quando informados),
áreas, cômodos, endereço com número e CEP, coordenada, descrição, datas, anunciante
(nome, CRECI, telefones, endereço) e pontos próximos. A página do anúncio só
acrescenta a lista de características (do imóvel e do condomínio) e o condomínio
quando a busca não o traz.

Limites da API (medidos em out/2026):
  - 15 anúncios por página; a numeração da API começa em 0 (pg=0 é a 1ª página do
    site, pg=1 a 2ª...). O Cloudflare bloqueia (403) de pg=100 em diante, então cada
    consulta só entrega 1.500 anúncios (ver LIMITE_FAIXA);
  - depois dos resultados reais a API emenda "imóveis similares" (fora do filtro),
    sinalizados por um item {"recommendedCount": ...}: a coleta para nesse item e só
    aceita anúncios dentro da faixa pedida;
  - paginando de pg=0 até o fim, uma ordenação já cobre 100% da faixa (medido: 687 de
    687). Se mesmo assim faltar anúncio (o total muda durante a coleta), a faixa é
    paginada de novo com outra ordenação.
  - anúncios sem preço ("sob consulta", ~0,2%) não entram em nenhum filtro de preço e
    ficam no fim de qualquer ordenação, depois do limite de 1.500: não são coletados
    (também não serviriam para estimar preço).

Segmentação: estado × operação (venda/aluguel) × faixa de preço, dividida ao meio até
ter <= LIMITE_FAIXA anúncios. Preço único grande demais (há 36 mil anúncios a
exatamente R$ 450.000 em SP; 57% dos anúncios de venda de SP estão em preços assim) é
dividido pelo tipo de imóvel (páginas "apartamentos-a-venda", "casas-a-venda"..., que
a API navigationFilters lista com a contagem) e, se o tipo ainda passar do limite,
pela área útil, mais uma fatia "área 0". Nesse último caso ficam de fora os anúncios
sem área nenhuma (~3% desse tipo/preço): o filtro de área os exclui e qualquer
ordenação os põe no fim, depois do limite de 1.500. Cada faixa vira um Parquet e
entra no progresso quando termina, então uma execução interrompida continua de onde
parou.
"""
import json
import math
import random
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import requests

from config.settings import settings
from scrapers.base import BaseScraper


def _texto(v):
    """Valores de referência do Next.js ("$undefined", "$82:0:props...") contam como vazios."""
    if v is None or (isinstance(v, str) and (v.startswith("$") or not v.strip())):
        return None
    return v


def _coordenada(v) -> float | None:
    """'-19.931381' -> -19.931381 (sem a limpeza de milhar de _numero)."""
    try:
        return float(_texto(v)) if _texto(v) is not None else None
    except (TypeError, ValueError):
        return None


def _numero(v, cast=float):
    """'R$ 1.020.000', '1.400', 64, '62,85' -> número (None se vazio)."""
    v = _texto(v)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return cast(v)
    s = re.sub(r"[^\d,]", "", str(v)).replace(",", ".")
    try:
        return cast(float(s)) if s else None
    except ValueError:
        return None


class ChavesNaMaoScraper(BaseScraper):
    """Scraper para Chaves na Mão via API interna listing/items."""

    PORTAL_NAME = "chavesnamao"
    BASE_URL = "https://www.chavesnamao.com.br"
    API_URL = "https://www.chavesnamao.com.br/api/realestate/listing/items/"
    TIPOS_URL = "https://www.chavesnamao.com.br/api/realestate/aggregations/navigationFilters/"
    FOTO_URL = "https://www.chavesnamao.com.br/imn/0400X0262/N/60/imoveis/"
    POR_PAGINA = 15
    MAX_PAGINAS = 100     # pg=0..99; pg=100 em diante leva 403 do Cloudflare
    LIMITE_FAIXA = 1400   # folga abaixo de 100 x 15 = 1.500 (o total muda durante a coleta)

    OPERACOES = {"venda": "imoveis-a-venda", "aluguel": "imoveis-para-alugar"}
    # "" = ordenação padrão do site; or:1 e or:2 = reforço quando faltar anúncio
    ORDENACOES = ["", "or:1", "or:2"]
    # Sem a faixa "acima de R$ 10 bilhões" dos outros portais: aqui um pmin tão alto é
    # ignorado e a consulta devolve o estado inteiro
    FAIXAS_INICIAIS = [(0, 999_999), (1_000_000, 9_999_999_999)]
    # Faixas de área útil (m²), usadas só quando um preço único passa do limite. Começa em
    # 1: área 0/vazia fica na fatia SEM_AREA, lida com a ordenação por área (or:3), que
    # põe esses anúncios primeiro (~3,7% dos anúncios de um preço "redondo" em SP)
    FAIXAS_AREA = [(1, 49), (50, 79), (80, 119), (120, 199), (200, 99_999_999)]
    SEM_AREA = -1
    ORDEM_AREA = "or:3"

    ESTADOS = [
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
        "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
    ]

    # realtyType.name do portal -> categorias comuns aos portais
    # (apartamento, casa, cobertura, flat, terreno, comercial, rural)
    TIPOS = {
        "apartamento": "apartamento", "kitnet / studio": "apartamento", "kitnet / stúdio": "apartamento",
        "loft": "apartamento", "flat": "flat", "cobertura": "cobertura",
        "casa / sobrado": "casa", "casa em condominio": "casa", "casa em condomínio": "casa",
        "terreno / lote": "terreno", "terreno em condominio": "terreno", "terreno em condomínio": "terreno",
        "terreno comercial": "terreno",
        "chacara": "rural", "chácara": "rural", "sitio": "rural", "sítio": "rural", "fazenda": "rural",
        "haras": "rural", "rancho": "rural",
    }
    TIPOS_ANUNCIANTE = {"PJ": "imobiliaria", "PF": "particular"}

    def __init__(self, workers: int = 4, detalhes: bool = True, parte: int = 1, partes: int = 1):
        super().__init__()
        self.workers = workers
        # Página de cada anúncio: lista de características e condomínio quando a busca
        # não traz. É pesada (~500 KB), então dobra o tempo da coleta.
        self.detalhes = detalhes
        self.parte, self.partes = parte, partes
        self._local = threading.local()
        self._divididos = set()
        self._folhas = set()
        self._falhas = 0
        self._tipos_cache = {}  # chave do segmento -> tipos de imóvel (navigationFilters)

    def _chave_progresso(self, estado: str) -> str:
        return estado if self.partes == 1 else f"{estado}_parte{self.parte}de{self.partes}"

    def _e_meu(self, chave: str) -> bool:
        return self.partes == 1 or zlib.crc32(chave.encode("utf-8")) % self.partes == self.parte - 1

    # ------------------------------------------------------------------ HTTP

    def _sessao(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({
                "User-Agent": random.choice(settings.USER_AGENTS),
                "Accept-Language": "pt-BR,pt;q=0.9",
            })
            self._local.session = s
        return self._local.session

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None,
             tentativas: int = 5) -> requests.Response | None:
        """GET com retry. 403/429 do Cloudflare esperam mais a cada tentativa."""
        for tentativa in range(tentativas):
            time.sleep(random.uniform(settings.REQUEST_DELAY_MIN, settings.REQUEST_DELAY_MAX))
            try:
                r = self._sessao().get(url, params=params, headers=headers, timeout=60)
                if r.status_code in (200, 404):
                    return r
                espera = 60 * (tentativa + 1) if r.status_code in (403, 429) else 10 * (tentativa + 1)
                print(f"    [chavesnamao] HTTP {r.status_code}, aguardando {espera}s "
                      f"(tentativa {tentativa + 1}/{tentativas})", flush=True)
            except requests.RequestException as e:
                espera = 10 * (tentativa + 1)
                print(f"    [chavesnamao] {type(e).__name__}, aguardando {espera}s", flush=True)
            time.sleep(espera)
            if hasattr(self._local, "session"):
                del self._local.session  # sessão nova (outro User-Agent) na próxima tentativa
        return None

    def _consulta(self, estado: str, operacao: str, seg: tuple, pagina: int, ordem: str = "") -> dict | None:
        """Uma página (pagina 1 = primeira). seg = (preço mín, preço máx, área mín, área máx,
        tipo); None = sem teto/filtro; tipo = (página do tipo, id) ou None."""
        lo, hi, alo, ahi, tipo = seg
        filtro = [f"pmin:{lo}"]
        if hi is not None:
            filtro.append(f"pmax:{hi}")
        if alo == self.SEM_AREA:
            ordem = self.ORDEM_AREA
        elif alo is not None:
            filtro.append(f"amin:{alo}")
            if ahi is not None:
                filtro.append(f"amax:{ahi}")
        if ordem:
            filtro.append(ordem)
        params = {"level1": tipo[0] if tipo else self.OPERACOES[operacao], "level2": estado.lower(),
                  "filtro": ",".join(filtro), "pg": pagina - 1}  # a API conta a partir de 0
        r = self._get(self.API_URL, params=params, headers={"Accept": "application/json"})
        if r is None or r.status_code != 200:
            return None
        try:
            dados = r.json()
        except ValueError:
            return None
        return dados if isinstance(dados.get("metadata"), dict) else None

    @staticmethod
    def _total(dados: dict) -> int:
        """Resultados reais (sem os "similares" que a API emenda no fim)."""
        md = dados.get("metadata") or {}
        real = md.get("realResults")  # 0 é válido: aí totalListing conta só os similares
        return int(real if real is not None else md.get("totalListing") or 0)

    @staticmethod
    def _itens(dados: dict) -> list[dict]:
        """Anúncios reais da página: para no marcador de "imóveis similares"."""
        itens = []
        for x in dados.get("items") or []:
            if not isinstance(x, dict):
                continue
            if x.get("recommendedCount") is not None or x.get("relaxesFilters"):
                break
            if x.get("id"):
                itens.append(x)
        return itens

    @staticmethod
    def _area(item: dict) -> float | None:
        """Área como o filtro e a ordenação do site a enxergam: a útil quando informada
        (mesmo "0"), senão a total. 0 conta como sem área."""
        area = item.get("area") or {}
        util = _numero(area.get("useful"))
        valor = util if util is not None else _numero(area.get("total"))
        return valor or None

    def _pertence(self, item: dict, seg: tuple) -> bool:
        """O anúncio está mesmo na faixa? (descarta similares e evita duplicar entre faixas)"""
        lo, hi, alo, ahi, tipo = seg
        # A página de um tipo inclui subtipos (apartamentos-a-venda traz coberturas): só o id exato
        if tipo and (item.get("realtyType") or {}).get("id") != tipo[1]:
            return False
        preco = _numero((item.get("prices") or {}).get("rawPrice")) or 0
        if preco < lo or (hi is not None and preco > hi):
            return False
        area = self._area(item)
        if alo == self.SEM_AREA:
            return area is None
        if alo is not None and (area is None or area < alo or (ahi is not None and area > ahi)):
            return False
        return True

    # ---------------------------------------------------------- segmentação

    @staticmethod
    def _chave(operacao: str, seg: tuple) -> str:
        lo, hi, alo, ahi, tipo = seg
        chave = f"{operacao}:{lo}-{hi if hi is not None else 'max'}"
        if tipo:
            chave += f"|tipo:{tipo[1]}"
        if alo == ChavesNaMaoScraper.SEM_AREA:
            chave += "|area:sem"
        elif alo is not None:
            chave += f"|area:{alo}-{ahi if ahi is not None else 'max'}"
        return chave

    @staticmethod
    def _dividir(lo: int, hi: int) -> int:
        """Ponto de corte: geométrico em faixas muito largas, aritmético nas estreitas."""
        if lo > 0 and hi / lo > 4:
            return int(math.sqrt(lo * hi))
        return (lo + hi) // 2

    def _tipos(self, estado: str, operacao: str, seg: tuple) -> list[tuple] | None:
        """[(página do tipo, id do tipo, quantidade)] para o preço do segmento."""
        chave = self._chave(operacao, seg)
        if chave not in self._tipos_cache:
            lo, hi = seg[0], seg[1]
            params = {"level1": self.OPERACOES[operacao], "level2": estado.lower(),
                      "filtro": f"pmin:{lo},pmax:{hi}"}
            r = self._get(self.TIPOS_URL, params=params, headers={"Accept": "application/json"})
            try:
                itens = r.json()["data"]["items"] if r is not None and r.status_code == 200 else None
                tipos = [(i["url"].strip("/").split("/")[0], int(i["realtyID"]), int(i["total"]["value"]))
                         for i in itens if i.get("realtyID") is not None and i.get("url")]
            except (ValueError, KeyError, TypeError, AttributeError):
                tipos = None
            self._tipos_cache[chave] = tipos or None
        return self._tipos_cache[chave]

    def _subdividir(self, seg: tuple, estado: str, operacao: str) -> list[tuple] | None:
        lo, hi, alo, ahi, tipo = seg
        if hi is not None and hi > lo:
            meio = self._dividir(lo, hi)
            return [(lo, meio, alo, ahi, tipo), (meio + 1, hi, alo, ahi, tipo)]
        if tipo is None and alo is None:
            tipos = self._tipos(estado, operacao, seg)
            # Sem a lista de tipos não divide por área: numa execução seguinte a divisão
            # seria por tipo e os anúncios já gravados sairiam de novo (duplicados)
            return [(lo, hi, None, None, (pagina, id_tipo)) for pagina, id_tipo, _ in tipos] if tipos else None
        if alo is None:
            return [(lo, hi, a, b, tipo) for a, b in self.FAIXAS_AREA] + [(lo, hi, self.SEM_AREA, None, tipo)]
        if alo != self.SEM_AREA and ahi is not None and ahi > alo:
            meio = self._dividir(alo, ahi)
            return [(lo, hi, alo, meio, tipo), (lo, hi, meio + 1, ahi, tipo)]
        return None

    def _faixas(self, estado: str, operacao: str, feitos: set):
        """Gera (segmento, total, dados_pagina_1) para segmentos com <= LIMITE_FAIXA anúncios."""
        pendentes = [(lo, hi, None, None, None) for lo, hi in self.FAIXAS_INICIAIS]
        while pendentes:
            seg = pendentes.pop(0)
            chave = self._chave(operacao, seg)
            if chave in feitos:
                continue
            if chave in self._folhas and not self._e_meu(chave):
                continue
            if chave in self._divididos:
                filhos = self._subdividir(seg, estado, operacao)
                if filhos:
                    pendentes[0:0] = filhos
                else:
                    print(f"  [chavesnamao] Falha ao listar os tipos de {chave}, será refeito", flush=True)
                    self._falhas += 1
                continue
            if seg[2] == self.SEM_AREA:
                # O total da API aqui é o do preço inteiro: a fatia é lida até o 1º anúncio com área
                self._folhas.add(chave)
                yield seg, None, None
                continue
            dados = self._consulta(estado, operacao, seg, 1)
            if dados is None:
                print(f"  [chavesnamao] Falha ao consultar {chave}, pulando "
                      f"(será refeito na próxima execução)", flush=True)
                self._falhas += 1
                continue
            total = self._total(dados)
            if total > self.LIMITE_FAIXA:
                partes = self._subdividir(seg, estado, operacao)
                if partes and partes[0][4] != seg[4]:
                    soma = sum(n for *_, n in self._tipos(estado, operacao, seg) or [])
                    if soma < total:
                        print(f"  [chavesnamao] {chave}: tipos somam {soma} de {total} anúncios", flush=True)
                if partes:
                    self._divididos.add(chave)
                    pendentes[0:0] = partes
                    continue
                if seg[4] is None and seg[2] is None:
                    print(f"  [chavesnamao] Falha ao listar os tipos de {chave}, será refeito", flush=True)
                    self._falhas += 1
                    continue
                print(f"  [chavesnamao] {chave} tem {total} anúncios e não dá para dividir; "
                      f"coletando os primeiros {self.MAX_PAGINAS * self.POR_PAGINA} de cada ordenação", flush=True)
            self._folhas.add(chave)
            yield seg, total, dados

    # --------------------------------------------------------------- coleta

    def _coletar_faixa(self, estado: str, operacao: str, seg: tuple, total: int,
                       pagina1: dict) -> tuple[dict, bool]:
        """Todas as páginas do segmento; outras ordenações só se faltar anúncio.
        Retorna ({id: item}, ok); ok=False se alguma página da 1ª ordenação falhou."""
        if seg[2] == self.SEM_AREA:
            return self._coletar_sem_area(estado, operacao, seg)
        paginas = min(math.ceil(total / self.POR_PAGINA), self.MAX_PAGINAS)
        anuncios = {x["id"]: x for x in self._itens(pagina1) if self._pertence(x, seg)}
        ok = True

        for n, ordem in enumerate(self.ORDENACOES):
            if n > 0 and len(anuncios) >= total:
                break
            inicio = 2 if n == 0 else 1  # a página 1 da ordenação padrão já veio no _faixas

            def baixar(pagina, ordem=ordem):
                return self._consulta(estado, operacao, seg, pagina, ordem)

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for dados in pool.map(baixar, range(inicio, paginas + 1)):
                    if dados is None:
                        ok = ok and n > 0  # falha numa passada extra só reduz o reforço
                        continue
                    for x in self._itens(dados):
                        if self._pertence(x, seg):
                            anuncios[x["id"]] = x
        return anuncios, ok

    def _coletar_sem_area(self, estado: str, operacao: str, seg: tuple) -> tuple[dict, bool]:
        """Anúncios sem área de um preço único: na ordenação por área eles vêm primeiro;
        lê página a página até aparecer um anúncio com área (ou acabar o limite de páginas)."""
        anuncios = {}
        for pagina in range(1, self.MAX_PAGINAS + 1):
            dados = self._consulta(estado, operacao, seg, pagina)
            if dados is None:
                return anuncios, False
            itens = self._itens(dados)
            anuncios.update((x["id"], x) for x in itens if self._pertence(x, seg))
            if not itens or any(self._area(x) is not None for x in itens):
                return anuncios, True
        print(f"  [chavesnamao] {self._chave(operacao, seg)}: mais de "
              f"{self.MAX_PAGINAS * self.POR_PAGINA} anúncios sem área; coletados os primeiros", flush=True)
        return anuncios, True

    def _detalhe(self, item: dict) -> dict | None:
        """Página do anúncio (payload RSC do Next.js, ~500 KB): devolve o objeto do
        anúncio com privativeItems/commonItems e preços completos."""
        url = item.get("url") or ""
        if not url:
            return None
        r = self._get(self.BASE_URL + url, headers={"RSC": "1"}, tentativas=3)
        if r is None or r.status_code != 200:
            return None
        texto = r.text
        dec = json.JSONDecoder()
        for m in re.finditer(r'\{"id":%d,' % int(item["id"]), texto):
            try:
                obj, _ = dec.raw_decode(texto, m.start())
            except ValueError:
                continue
            if "privativeItems" in obj or "commonItems" in obj:
                return obj
        return {}

    def run(self, estado: str = "SP", cidade: str = "", limit: int = None, reset: bool = False):
        """Coleta um estado inteiro (venda + aluguel). Retorna (salvos, -1 se concluído).
        O parâmetro cidade é ignorado: a segmentação é por faixa de preço."""
        estado = estado.upper()
        if estado not in self.ESTADOS:
            print(f"[chavesnamao] Estado desconhecido: {estado}", flush=True)
            return 0, -1

        chave_prog = self._chave_progresso(estado)
        progress = {} if reset else self.storage.get_portal_progress(self.PORTAL_NAME, chave_prog)
        if progress.get("concluido"):
            print(f"[chavesnamao] {chave_prog} já concluído, pulando (use --reset para refazer)", flush=True)
            return 0, -1
        feitos = set(progress.get("segmentos_concluidos", []))
        self._divididos = set(progress.get("segmentos_divididos", []))
        self._folhas = set(progress.get("segmentos_folha", []))
        self._falhas = 0

        def salvar_progresso(concluido=False):
            dados = {"segmentos_concluidos": sorted(feitos), "segmentos_divididos": sorted(self._divididos),
                     "segmentos_folha": sorted(self._folhas)}
            if concluido:
                dados["concluido"] = True
            self.storage.save_portal_progress(self.PORTAL_NAME, chave_prog, dados)

        parte_txt = f" (parte {self.parte}/{self.partes})" if self.partes > 1 else ""
        print(f"\n{'='*60}\nScraping Chaves na Mão: {estado}{parte_txt}\n{'='*60}", flush=True)
        total_saved = 0

        for operacao in self.OPERACOES:
            for seg, total, pagina1 in self._faixas(estado, operacao, feitos):
                chave = self._chave(operacao, seg)
                if not self._e_meu(chave):
                    continue
                if total == 0:  # None = fatia sem área (total desconhecido até ler)
                    feitos.add(chave)
                    continue

                t0 = time.time()
                brutos, ok = self._coletar_faixa(estado, operacao, seg, total, pagina1)
                if not ok:
                    print(f"  [chavesnamao] {chave}: páginas falharam, segmento será refeito na próxima execução",
                          flush=True)
                    self._falhas += 1
                    continue

                detalhes = {}
                if self.detalhes and brutos:
                    itens = list(brutos.values())
                    with ThreadPoolExecutor(max_workers=self.workers) as pool:
                        detalhes = dict(zip((x["id"] for x in itens), pool.map(self._detalhe, itens)))
                    sem = sum(1 for d in detalhes.values() if d is None)
                    if sem:
                        print(f"  [chavesnamao] AVISO: {sem}/{len(itens)} anúncios sem detalhe em {chave}", flush=True)

                anuncios = [a for a in (self._parse_listing(x, operacao, detalhes.get(i))
                                        for i, x in brutos.items()) if a]
                if len(anuncios) < len(brutos):
                    print(f"  [chavesnamao] AVISO: {len(brutos) - len(anuncios)} anúncios descartados "
                          f"por erro de parse em {chave}", flush=True)

                if total:
                    print(f"  [{estado}] {chave}: {len(brutos)}/{total} anúncios "
                          f"({100 * len(brutos) / total:.1f}%) em {time.time() - t0:.0f}s", flush=True)
                else:
                    print(f"  [{estado}] {chave}: {len(brutos)} anúncios em {time.time() - t0:.0f}s", flush=True)

                nome = chave.replace(":", "_").replace("|", "_")
                if anuncios and not self.storage.save_anuncios(anuncios, estado, nome, self.PORTAL_NAME):
                    self._falhas += 1
                    continue  # falhou ao salvar: não marca como feita
                total_saved += len(anuncios)
                feitos.add(chave)
                salvar_progresso()

                if limit and total_saved >= limit:
                    print(f"[chavesnamao] Limite de {limit} atingido", flush=True)
                    return total_saved, 0

        if self._falhas:
            salvar_progresso()
            print(f"\n[chavesnamao] {chave_prog}: {total_saved} anúncios salvos; {self._falhas} segmentos "
                  f"falharam e serão refeitos na próxima execução", flush=True)
            return total_saved, 0
        salvar_progresso(concluido=True)
        print(f"\n[chavesnamao] {chave_prog}: {total_saved} anúncios salvos", flush=True)
        return total_saved, -1

    # --------------------------------------------------------------- parse

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Não usado (coleta por faixa de preço)."""
        return 0

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Não usado (coleta por faixa de preço)."""
        return []

    def _map_tipo(self, nome: str | None) -> str | None:
        n = (nome or "").strip().lower()
        if not n:
            return None
        if n in self.TIPOS:
            return self.TIPOS[n]
        if "comercial" in n or any(k in n for k in ("sala", "loja", "galpão", "galpao", "prédio", "predio",
                                                     "garagem", "ponto", "hotel", "pousada", "andar")):
            return "comercial"
        if any(k in n for k in ("sítio", "sitio", "chácara", "chacara", "fazenda", "rural", "haras")):
            return "rural"
        if "terreno" in n or "lote" in n:
            return "terreno"
        if "casa" in n or "sobrado" in n:
            return "casa"
        if "apartamento" in n:
            return "apartamento"
        return n

    @staticmethod
    def _contagem(v, cast=int):
        """{"count": 2, "max": null} (em lançamentos, count = mínimo da faixa) -> 2."""
        if isinstance(v, dict):
            return _numero(v.get("count"), cast)
        return _numero(v, cast)

    @staticmethod
    def _nomes(lista) -> str | None:
        if not isinstance(lista, list):
            return None
        nomes = [x.get("name") for x in lista if isinstance(x, dict) and _texto(x.get("name"))]
        return "|".join(dict.fromkeys(nomes)) or None

    @staticmethod
    def _proximidades(prox) -> tuple[str | None, str | None]:
        """proximities = {"busStops": [{"distance": 120, "name": ...}], "metros": [...], "hospitals",
        "parks", "malls", "schools", "gyms", "restaurants", "supermarkets", "pharmacies"}
        -> (transporte "Nome (120 m), ...", pontos de interesse "categoria:Nome (120 m)|...")."""
        if not isinstance(prox, dict):
            return None, None
        transporte, pontos = [], []
        for categoria, lista in prox.items():
            for x in lista or []:
                if not isinstance(x, dict) or not x.get("name"):
                    continue
                txt = f"{x['name']} ({x['distance']} m)" if x.get("distance") is not None else x["name"]
                if categoria in ("busStops", "metros"):
                    transporte.append(txt)
                pontos.append(f"{categoria}:{txt}")
        return ", ".join(transporte) or None, "|".join(pontos) or None

    @staticmethod
    def _endereco_anunciante(end) -> str | None:
        if not isinstance(end, dict):
            return None
        rua = end.get("street") or {}
        partes_rua = ", ".join(str(x) for x in (_texto(rua.get("name")), _texto(rua.get("addressNumber")),
                                                _texto(end.get("addressComp"))) if x)
        local = ", ".join(x for x in (_texto((end.get("neighborhood") or {}).get("name")),
                                      "/".join(x for x in (_texto((end.get("city") or {}).get("name")),
                                                           _texto((end.get("state") or {}).get("acronym"))) if x))
                          if x)
        return " - ".join(x for x in (partes_rua, local) if x) or None

    def _parse_listing(self, x: dict, operacao: str, det: dict | None = None) -> dict | None:
        """Converte um anúncio da busca (+ página do anúncio, se houver) para o schema comum."""
        det = det or {}
        try:
            precos = {**(x.get("prices") or {}),
                      **{k: v for k, v in (det.get("prices") or {}).items() if _texto(v) is not None}}
            preco = _numero(precos.get("rawPrice")) or None  # 0 = "sob consulta"
            area_info = x.get("area") or {}
            area_util = _numero(area_info.get("useful"))
            area_total = _numero(area_info.get("total"))
            area = area_util or area_total

            loc = x.get("location") or {}
            rua = loc.get("street") or {}
            geo = loc.get("geoposition") or {}
            lat, lon = _coordenada(geo.get("lat")), _coordenada(geo.get("lon"))

            pictures = x.get("pictures") or {}
            fotos = [self.FOTO_URL + p for p in pictures.get("list") or [] if isinstance(p, str)]

            publisher = x.get("publisher") or {}
            telefones = publisher.get("phones") or {}
            fones = [*(telefones.get("whatsapp") or [])]
            for tipo in ("cellphone", "landline", "commercial"):
                fones.append((telefones.get(tipo) or {}).get("number"))

            media = x.get("media") or {}
            transporte, pontos = self._proximidades(x.get("proximities") or det.get("proximities"))
            tipo_nome = _texto((x.get("realtyType") or {}).get("name"))
            empreendimento = _texto(x.get("newEnterprise"))

            stamps = []
            if x.get("highlighted"):
                stamps.append("DESTAQUE")
            if empreendimento:
                stamps.append("LANCAMENTO")

            return {
                "url": self.BASE_URL + x["url"] if x.get("url") else None,
                "titulo": _texto(x.get("title")),
                "descricao": _texto(x.get("descriptionRaw")) or _texto(x.get("description")),
                "tipo": self._map_tipo(tipo_nome),
                "finalidade": operacao,
                "preco": preco,
                "preco_condominio": _numero(precos.get("condominiumFee")),
                "iptu": _numero(precos.get("iptuValue")),
                "area_construida": area,
                "area_terreno": area_total,
                "quartos": self._contagem(x.get("bedrooms")),
                "suites": self._contagem(x.get("suites")),
                "banheiros": self._contagem(x.get("bathrooms")),
                "vagas": self._contagem(x.get("garages")),
                "rua": ", ".join(str(v) for v in (_texto(rua.get("name")), _texto(rua.get("addressNumber"))) if v)
                       or None,
                "bairro": _texto((loc.get("neighborhood") or {}).get("name")),
                "cidade": _texto((loc.get("city") or {}).get("name")),
                "estado": _texto((loc.get("state") or {}).get("acronym")),
                "cep": _texto(loc.get("zipCode")),
                "latitude": lat,
                "longitude": lon,
                "fotos_urls": "|".join(fotos) or None,
                "image_count": _numero(pictures.get("count"), int) or len(fotos),
                "data_publicacao": _texto(x.get("createdAt")),
                "data_ultima_atualizacao": _texto(x.get("updatedAt")),
                # Só vêm na página do anúncio
                "amenities": self._nomes(det.get("privativeItems")),
                "complex_amenities": self._nomes(det.get("commonItems")),
                "preco_por_m2": round(preco / area, 2) if preco and area else None,
                "usage_types": _texto(x.get("category")),
                "property_sub_type": tipo_nome,
                "andar": None,
                "total_andares": None,
                "aceita_permuta": str(x["acceptTrade"]) if isinstance(x.get("acceptTrade"), bool) else None,
                "status_anuncio": "ACTIVE" if x.get("active", True) else "INACTIVE",
                "anunciante_nome": _texto(publisher.get("name")),
                "anunciante_telefone": "|".join(dict.fromkeys(f for f in fones if _texto(f))) or None,
                "listing_id": x.get("id"),
                "stamps": "|".join(stamps) or None,
                "contract_type": "RENTAL" if operacao == "aluguel" else "SALE",
                "zona": None,
                "periodo_iptu": None,
                "garantias_aluguel": None,
                "aluguel_total": (_numero(precos.get("total")) or preco) if operacao == "aluguel" else None,
                "imovel_disponivel": True,
                "imovel_atualizado": None,
                "codigo_imovel_anunciante": _texto(x.get("reference")),
                "anunciante_id": publisher.get("id"),
                "tipo_anunciante": self.TIPOS_ANUNCIANTE.get(publisher.get("type"), _texto(publisher.get("type"))),
                "anunciante_creci": _texto(publisher.get("creci")),
                "idade_imovel": None,
                "lancamento": bool(empreendimento),
                "estagio_obra": None,
                "previsao_entrega": None,
                "nome_empreendimento": None,
                # publicAddress=False: o anunciante escondeu o endereço (coordenada aproximada)
                "localizacao_exata": bool(loc.get("publicAddress")) if lat is not None else None,
                "baixou_preco_pct": None,
                "tem_tour_virtual": bool(_texto(media.get("tour360"))),
                "tem_video": bool(_texto(media.get("videoUrl"))),
                "tem_planta": None,
                "unidades_por_andar": None,
                "elevadores": None,
                "transporte_proximo": transporte,
                "outros_portais": None,
                "anuncio_duplicado_id": None,
                "aceita_financiamento": None,
                "incorporadora": None,
                "qualidade_anuncio": None,
                "anunciante_nivel": None,
                "anunciante_verificado": publisher.get("verified") if isinstance(publisher.get("verified"), bool) else None,
                "h3_index": None,
                "localizacao_id": None,
                "descricao_ia": None,
                "salas": self._contagem(x.get("commercialRooms")),
                "tem_closet": None,
                "formas_pagamento": None,
                "anunciante_endereco": self._endereco_anunciante(publisher.get("address")),
                "anunciante_site": None,  # o portal não publica o site do anunciante
                "pontos_interesse": pontos,
                "olx_id": None,
            }
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            return None
