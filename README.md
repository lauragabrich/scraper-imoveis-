# Scraper VivaReal — Imóveis Brasil

Scraper que coleta **todos os anúncios de imóveis** do VivaReal em todo o Brasil, salvando os dados em formato Parquet no Amazon S3.

Também coleta **Lugar Certo**, **Imovelweb** (ver [Outros portais](#outros-portais-lugar-certo-e-imovelweb)) e **Chaves na Mão** (ver [Chaves na Mão](#chaves-na-mão-out2026)), gravando no mesmo bucket e com as mesmas colunas.

---

## Recoleta do VivaReal (set/2026)

**A API mudou depois da coleta de ago/2026** (medido em set/2026):

| Limite atual da API | Efeito na coleta antiga (por bairro) |
|---|---|
| Máximo de ~1.500 anúncios por consulta (`from` acima de 1.464 é recusado) e 24 por página | bairro com mais de ~1.500 anúncios era cortado sem aviso |
| Tipo de negócio vai em `business=SALE/RENTAL`; o antigo `businessType` é ignorado | aluguel nunca era coletado |
| A busca não traz mais `title`/`description` | a coleta antiga tem os dois (vieram antes da mudança) |

Volume real: **3,7 mi anúncios de venda + 0,87 mi de aluguel** no Brasil (SP: 2,2 mi + 0,57 mi). A coleta antiga tem ~2,3 mi anúncios únicos, só de venda.

**Nova coleta, sem cidades nem bairros:** estado × negócio (venda/aluguel) × tipo (usado/lançamento) × faixa de preço (`priceMin`/`priceMax`), dividida ao meio até ≤ 1.400 anúncios; um preço único acima disso (ex.: ~6.500 a R$ 500.000 só na capital de SP) é dividido por área útil (`usableAreasMin/Max`). A ordem padrão da API é estável, então paginar dentro de uma fatia é seguro. Os filtros casam com *qualquer* preço/área do anúncio; para não duplicar, cada anúncio fica só na fatia do seu preço (e área) principal. Anúncios sem preço (~0,003%) ficam de fora.

Outras correções em relação à coleta antiga:

| Problema na coleta antiga | Efeito | Correção |
|---|---|---|
| `unitFloor` lido como lista | ~8% dos anúncios descartados sem aviso (todos com andar informado) | lido como número; aviso no log se algum anúncio for descartado |
| `andar` e `total_andares` trocados | valores invertidos | corrigido |
| Duplicatas entre lotes da mesma cidade | ~26% de linhas repetidas | cada anúncio pertence a uma única fatia |
| Vídeos e plantas dentro de `fotos_urls` | contagem de fotos inflada | separados (`tem_video`, `tem_tour_virtual`, `tem_planta`) |
| Telefone gravado como `"['3199...']"` | formato inutilizável | `3199...|3198...` |

**Detalhe de cada anúncio (padrão; `--sem-detalhes` desliga):** `glue-api.vivareal.com/v2/listing/{id}` traz título, descrição, data de atualização, ano de entrega (`idade_imovel`), período do IPTU, amenities em português, transporte e pontos de interesse próximos, ID na OLX e em quais portais o anúncio também está. É 1 requisição por anúncio (~4,6 mi) e o Cloudflare do VivaReal bloqueia o IP por mais de 1h a ~10 req/s, então cada job usa `--workers 4` (~1,5 req/s).

**Jobs:** SP em 6 partes (`--parte N/6`, ~460 mil anúncios cada) e os demais estados em 6 grupos de ~300 mil, cada job numa máquina (IP) diferente. Com o estado dividido, ~65 faixas fixas de preço são repartidas entre as partes e cada parte só consulta as suas. Progresso em `progress/vivareal/<UF>[_parteNdeM].json` (o `progress/<UF>.json` da coleta antiga não é mais lido nem alterado). Estimativa: ~3–4 dias.

---

## Outros portais: Lugar Certo e Imovelweb

```bash
python main.py --portal lugarcerto --all-estados              # ~75 mil anúncios
python main.py --portal lugarcerto --estado MG --sem-detalhes # só listagem (rápido)
python main.py --portal imovelweb --estado SP --workers 12 --passadas 2   # no seu computador (ver abaixo)
```

| | Lugar Certo | Imovelweb |
|---|---|---|
| **Anúncios (set/2026)** | ~75 mil (55 mil em MG) | ~6 milhões (3,9 mi em SP) |
| **Fonte** | Endpoint JSON da busca `/busca/dasearch` | API interna `POST /rplis-api/postings` |
| **Proteção** | Nenhuma | Cloudflare: exige `curl_cffi` (fingerprint TLS do Chrome) |
| **Limite por consulta** | 10.000 por chamada | 30 por página, máx. 1000 páginas (30 mil) |
| **Segmentação** | estado → cidade → bairro (cidades > 10 mil) | estado × venda/aluguel × faixa de preço, dividida ao meio até ≤ 4.800 (a API só mantém a ordenação nos primeiros ~5.000 resultados de cada consulta; além disso embaralha); se um preço único passa disso (ex.: ~35 mil anúncios a R$ 350.000 em SP), divide também por área útil |
| **Cobertura medida** | 100% (DF), 99,97% (BH) | ~97% com 1 passada, ~100% com `--passadas 2` |
| **Tempo estimado** | minutos (listagem) + ~15 h (detalhes, 4 workers) | SP: ~2 dias em 4 jobs (listagem + página de cada anúncio, 12 workers, 2 passadas); demais estados em paralelo, <1 dia cada |
| **Onde roda** | GitHub Actions (`scraper-lugarcerto.yml`) | **No seu computador** (`tools/rodar_imovelweb.ps1`): o Cloudflare bloqueia os IPs do GitHub |

**Detalhes técnicos que importam:**
- **Lugar Certo:** a listagem não traz lat/lng, CEP, condomínio, IPTU, fotos, suítes nem telefone. Esses campos vêm da página de cada anúncio (`window.detalheanuncio`), o que é o passo demorado; `--sem-detalhes` pula esse passo. Não use `sort=menorpreco`: ele adiciona um filtro escondido que exclui anúncios sem preço. Em estados sem anúncios o filtro de estado é ignorado e a API devolve o Brasil inteiro, por isso os registros são validados por UF e cidade.
- **Imovelweb:** as páginas HTML só abrem até a página 4 sem resolver o desafio do Cloudflare; a API JSON não tem essa restrição. A ordenação desempata de forma aleatória a cada requisição, e por isso uma passada paginada perde ~3%. A segunda passada (`--passadas 2`, ordenada por data) recupera quase tudo. Anúncios sem preço (~0,005%) não entram em nenhuma faixa. Um imóvel anunciado para venda e para aluguel aparece em duas linhas, uma por `finalidade`. A lista completa de características (imóvel → `amenities`, "Áreas comuns" → `complex_amenities`) e a data de publicação só existem na página de cada anúncio (objeto JS `avisoInfo`, sem API mais leve), baixada por padrão (`--sem-detalhes` desliga); num teste curto (112 páginas em ~13 s) o site respondeu 8,9 páginas/s com 16 workers sem bloqueio, mas isso não prova que aguente 16 por dias; a coleta real usa 12 workers (~5 anúncios/s, sem bloqueio em 7 dias seguidos). SP é dividido em 4 jobs (`--parte N/4`): cada segmento de preço/área pertence a uma parte, com progresso próprio (`progress/imovelweb/SP_parte2de4.json`).
- **Colunas extras** (só nesses dois portais; nos arquivos do VivaReal ficam nulas no Athena):

  | Coluna | Lugar Certo | Imovelweb | Uso |
  |---|---|---|---|
  | `codigo_imovel_anunciante` | 93% | 100% | Código interno da imobiliária: junto com `anunciante_id`, identifica o mesmo imóvel em portais diferentes |
  | `anunciante_id` | 100% | 100% | ID do anunciante no portal |
  | `tipo_anunciante` | 97% | 100% | imobiliaria / particular / incorporadora / construtora |
  | `anunciante_creci` | — | ~35-60% | CRECI do anunciante |
  | `idade_imovel` | — | ~25% | Anos desde a construção |
  | `lancamento`, `estagio_obra`, `previsao_entrega` | só `lancamento` | 100% / ~10% / ~6% | Imóvel na planta / em obra e data de entrega |
  | `nome_empreendimento` | ~15% | só empreendimentos | Nome do edifício/condomínio |
  | `localizacao_exata` | 100% | ~90% | Se lat/lng é do endereço exato |
  | `baixou_preco_pct` | — | ~2% | % de redução de preço |
  | `tem_tour_virtual`, `tem_video`, `tem_planta` | só planta | 100% | Mídia disponível |
  | `unidades_por_andar`, `elevadores` | ~27% | — | Dados do prédio |
  | `transporte_proximo` | — | — | Metrô/trem a 1, 2 e 3 km (só VivaReal, pelo detalhe, ~29%) |
  | `outros_portais` | — | — | Outros portais do grupo onde o anúncio está, ex. `OLX\|VIVAREAL\|ZAP` (só VivaReal) |
  | `anuncio_duplicado_id` | — | ~8% | ID do anúncio que o próprio Imovelweb considera o original |
  | `aceita_financiamento`, `formas_pagamento` | ~17% | — | À vista / financiamento / sinal + ágio |
  | `incorporadora` | — | — | Incorporadora do lançamento (só VivaReal, ~12%) |
  | `qualidade_anuncio`, `anunciante_nivel`, `anunciante_verificado` | — | — | Nota de qualidade, plano e selo do anunciante (só VivaReal) |
  | `h3_index`, `localizacao_id` | — / — | — / 100% | Índice geográfico H3 (VivaReal) e ID interno de localização (VivaReal e Imovelweb) |
  | `descricao_ia` | — | ~23% | Resumo gerado por IA pelo próprio portal |
  | `salas`, `tem_closet` | ~40% / ~3% | — | Detalhes do imóvel |
  | `anunciante_endereco`, `anunciante_site` | 100% / ~15% | — | Endereço e site da imobiliária (VivaReal: ~48% / ~49%) |
  | `pontos_interesse` | — | — | Pontos de interesse próximos com prefixo de categoria do portal, ex. `BS:` = ponto de ônibus (só VivaReal, pelo detalhe) |
  | `olx_id` | — | — | ID do mesmo anúncio na OLX, ex. `sale=1516661524` (só VivaReal, pelo detalhe) |

- **Progresso:** fica em `progress/{portal}/{UF}.json`, com a lista de segmentos concluídos, separado do progresso do VivaReal. Cada segmento (cidade, bairro ou faixa de preço) vira um Parquet em `imoveis/portal={portal}/coleta={data}/estado={UF}/`.

## Chaves na Mão (out/2026)

```bash
python main.py --portal chavesnamao --estado RJ --workers 4
python main.py --portal chavesnamao --estado SP --parte 2/6 --sem-detalhes   # só a busca (rápido)
```

- **Anúncios:** ~4,64 milhões (4,02 mi venda + 0,62 mi aluguel), dos quais 2,95 mi em SP.
- **Fonte:** API interna do site (Next.js) `GET /api/realestate/listing/items/?level1=imoveis-a-venda&level2=sp&filtro=pmin:X,pmax:Y&pg=N`. Ela devolve JSON e não exige `curl_cffi`.
- **Limites:** 15 anúncios por página. A numeração começa em `pg=0`, que é a 1ª página do site. O Cloudflare bloqueia (403) de `pg=100` em diante, então cada consulta entrega no máximo 1.500 anúncios. A segmentação é estado × venda/aluguel × preço, dividido até ≤ 1.400. Um preço único grande demais (57% dos anúncios de venda de SP estão em preços "redondos" com mais de 1.400 anúncios, ex.: 36 mil a R$ 450.000) é dividido pelo tipo de imóvel (`navigationFilters` dá a página e a contagem de cada tipo) e, se ainda passar, pela área útil.
- **"Imóveis similares":** depois dos resultados reais, a API emenda anúncios fora do filtro, sinalizados por um item `{"recommendedCount": ...}`. O coletor para nesse item e só aceita anúncios dentro da faixa.
- **Cobertura medida:** 100% de cada faixa com uma ordenação (687/687); ES inteiro 13.788 de 13.806 (99,9%); R$ 450.000 em SP 98,8%. Ficam fora os anúncios sem preço (~0,2%, que não entram em nenhum filtro de preço) e, nos preços muito repetidos de apartamento/casa, os anúncios sem área nenhuma (o filtro de área os exclui e toda ordenação os põe depois do limite de 1.500).
- **Ritmo:** ~1,6 anúncio/s por job com a página do anúncio (4 workers). SP (2,95 mi em 6 jobs) leva ~3,5 dias; os demais jobs terminam antes.
- **Busca × página do anúncio:** a busca já traz preço, condomínio e IPTU (quando informados), áreas, cômodos, endereço com número, CEP, coordenada, descrição, datas, anunciante (nome, CRECI, telefones, endereço) e pontos próximos (`pontos_interesse`, `transporte_proximo`). A página do anúncio (payload RSC, ~500 KB) só acrescenta a lista de características (`amenities` e `complex_amenities`, presentes em ~40% dos anúncios) e o condomínio quando a busca não traz. `--sem-detalhes` pula essa página.
- **Onde roda:** GitHub Actions (`scraper-chavesnamao.yml`), em 12 jobs: SP em 6 partes, mais RS, SC, RJ, PR, MG e um job com os demais estados.

---

## Arquitetura

```
GitHub Actions (compute gratuito) → API VivaReal → Amazon S3 (Parquet) → Amazon Athena (consultas SQL)
```

### Componentes

| Componente | Função | Custo |
|-----------|--------|-------|
| **GitHub Actions** | Executa o scraper automaticamente a cada 6h | Gratuito |
| **API VivaReal** | Fonte dos dados (API interna de listagem) | Gratuito |
| **Amazon S3** | Armazena os dados coletados em Parquet | ~$0.92/mês (32 GB) |
| **Amazon Athena** | Consultas SQL sobre os dados no S3 | ~$0.005 por TB escaneado |

**Custo total estimado: < $1/mês**

---

## Como funciona o scraping (coleta antiga do VivaReal, ago/2026)

> **Histórico.** Esta seção descreve a coleta por cidade/bairro que gerou os dados em `vivareal/estado=...`. A recoleta usa segmentação por faixa de preço — ver [Recoleta do VivaReal](#recoleta-do-vivareal-set2026).

### Fluxo de coleta

1. **Percorre todos os 27 estados** do Brasil
2. Para cada estado, obtém **todas as cidades** via API do IBGE (ex: SP = 645 cidades)
3. Para cada cidade, **descobre bairros** via API de locations do VivaReal (busca A-Z + termos comuns)
4. Para cada bairro, busca **imóveis usados** + **lançamentos** (paginando até acabar)
5. **Fallback sem bairro**: busca geral da cidade para capturar anúncios não associados a bairros conhecidos
6. **Descoberta de bairros extras**: extrai bairros novos dos resultados do fallback e busca cada um individualmente
7. **Remove duplicatas** por URL
8. **Salva em Parquet** no S3 (1 arquivo por cidade)

### API utilizada

O scraper usa a **API interna** do VivaReal — a mesma que o site `vivareal.com.br` utiliza para carregar anúncios no navegador. Não é uma API pública documentada.

**Endpoints:**

| Endpoint | Função |
|----------|--------|
| `https://glue-api.vivareal.com/v2/listings` | Listagem de anúncios (com filtros de estado, cidade, bairro, tipo, paginação) |
| `https://glue-api.vivareal.com/v2/locations` | Descoberta de bairros/localizações por busca textual |

**Headers obrigatórios:**
- `x-domain: www.vivareal.com.br`
- `User-Agent` rotativo (simula navegador)
- `Accept: application/json`

### Proteções contra bloqueio

- **Rate limiting**: espera 1-3 segundos entre cada request
- **Rotação de User-Agent**: alterna entre 5 user-agents diferentes
- **Paginação respeitosa**: para após 2 páginas vazias consecutivas
- **Tratamento de erro 429** (rate limit): para e pula para o próximo bairro

### Salvamento parcial (proteção contra perda de dados)

Para cidades com muitos bairros (ex: São Paulo com 159+ bairros), o scraper salva parcialmente a cada 30 bairros processados — tanto na lista principal quanto no fallback. Se o workflow cair no meio da execução (timeout de 6h), no máximo perde os dados dos últimos 30 bairros em processamento — tudo que já foi salvo permanece no S3.

### Controle de progresso

O progresso é salvo no S3 (`progress/SP.json`) com a **lista de nomes de bairros já processados**. Na próxima execução:
- Lê o progresso
- Descobre os bairros da cidade
- Pula os que já estão na lista
- Continua apenas com os que faltam

Isso garante que não importa a ordem em que a API retorna os bairros — o scraper nunca reprocessa o que já fez.

```json
{
  "last_page": 572001,
  "cidade_nome": "São Paulo",
  "bairros_processados": ["Aclimação", "Alto da Lapa", "Bela Vista", ...],
  "updated_at": "2026-08-14T..."
}
```

### Limitações conhecidas

| Limitação | Impacto | Mitigação |
|-----------|---------|-----------|
| API não oficial (pode mudar) | Scraper pode parar de funcionar | Monitorar execuções no GitHub Actions |
| Só coleta imóveis à VENDA | Não pega aluguel | Pode ser adicionado com `businessType: RENTAL` |
| Limite de ~10.000 resultados por busca | Bairros muito grandes podem perder anúncios | Busca por bairro individual reduz o problema |
| Rate limiting da API (erro 429) | Pula bairro quando bloqueado | Espera 1-3s entre requests |
| Timeout de 6h do GitHub Actions | Cidades grandes levam múltiplas execuções | Progresso por bairro + salvamento parcial |
| Duplicatas possíveis entre execuções | Anúncios podem aparecer mais de uma vez | Corrigido na recoleta (deduplica por `listing_id` na cidade); na coleta antiga, filtrar com `SELECT DISTINCT url` |

---

## Estrutura dos dados no S3

A partir da recoleta, **cada coleta completa grava numa pasta própria**, então recoletar não sobrescreve a anterior (e dá para comparar coletas para saber o que saiu do ar e o que mudou de preço):

```
s3://scraper-imoveis-data/
├── imoveis/
│   ├── portal=vivareal/coleta=2026-10-01/estado=SP/sao-paulo.parquet
│   ├── portal=lugarcerto/coleta=2026-10-01/estado=MG/belo-horizonte_lourdes.parquet
│   └── portal=imovelweb/coleta=2026-10-01/estado=SP/venda_500000-620000.parquet
├── vivareal/estado=SP/...        ← coleta antiga (layout anterior, mantida como arquivo)
└── progress/
    ├── SP.json                   ← progresso do VivaReal
    ├── vivareal/_coleta.json     ← data da coleta em andamento de cada portal
    ├── lugarcerto/MG.json
    └── imovelweb/SP.json
```

- **Nova coleta:** rode com `--reset` (ou marque "reset" ao disparar o workflow). Isso zera o progresso dos estados pedidos e abre a pasta `coleta=<data de hoje>`. Todos os `--reset` dentro de 24h caem na mesma coleta (jobs paralelos, estados em sequência).
- **Continuar uma coleta:** rode sem `--reset`; ela continua gravando na mesma pasta até terminar.

### Tabela no Athena (os 3 portais juntos)

```sql
CREATE EXTERNAL TABLE imoveis.anuncios (
  `url` string,
  `titulo` string,
  `descricao` string,
  `tipo` string,
  `finalidade` string,
  `preco` double,
  `preco_condominio` double,
  `iptu` double,
  `area_construida` double,
  `area_terreno` double,
  `quartos` bigint,
  `suites` bigint,
  `banheiros` bigint,
  `vagas` bigint,
  `rua` string,
  `bairro` string,
  `cidade` string,
  `cep` string,
  `latitude` double,
  `longitude` double,
  `fotos_urls` string,
  `image_count` bigint,
  `data_publicacao` string,
  `data_ultima_atualizacao` string,
  `amenities` string,
  `complex_amenities` string,
  `preco_por_m2` double,
  `usage_types` string,
  `property_sub_type` string,
  `andar` bigint,
  `total_andares` bigint,
  `aceita_permuta` string,
  `status_anuncio` string,
  `anunciante_nome` string,
  `anunciante_telefone` string,
  `listing_id` string,
  `stamps` string,
  `contract_type` string,
  `zona` string,
  `periodo_iptu` string,
  `garantias_aluguel` string,
  `aluguel_total` double,
  `imovel_disponivel` boolean,
  `imovel_atualizado` string,
  `codigo_imovel_anunciante` string,
  `anunciante_id` string,
  `tipo_anunciante` string,
  `anunciante_creci` string,
  `idade_imovel` bigint,
  `lancamento` boolean,
  `estagio_obra` string,
  `previsao_entrega` string,
  `nome_empreendimento` string,
  `localizacao_exata` boolean,
  `baixou_preco_pct` double,
  `tem_tour_virtual` boolean,
  `tem_video` boolean,
  `tem_planta` boolean,
  `unidades_por_andar` bigint,
  `elevadores` bigint,
  `transporte_proximo` string,
  `outros_portais` string,
  `anuncio_duplicado_id` string,
  `aceita_financiamento` boolean,
  `incorporadora` string,
  `qualidade_anuncio` double,
  `anunciante_nivel` string,
  `anunciante_verificado` boolean,
  `h3_index` string,
  `localizacao_id` string,
  `descricao_ia` string,
  `salas` bigint,
  `tem_closet` boolean,
  `formas_pagamento` string,
  `anunciante_endereco` string,
  `anunciante_site` string,
  `pontos_interesse` string,
  `olx_id` string,
  `data_coleta` string
)
PARTITIONED BY (portal string, coleta string, estado string)
STORED AS PARQUET
LOCATION 's3://scraper-imoveis-data/imoveis/';

-- depois de cada coleta (registra as pastas novas):
MSCK REPAIR TABLE imoveis.anuncios;
```

Exemplo: `SELECT portal, COUNT(*) FROM imoveis.anuncios WHERE coleta = '2026-10-01' GROUP BY portal`.
A tabela antiga `vivareal` (usada pelo `athena_client.py`) continua apontando para os dados antigos.

---

## Colunas coletadas (40+ campos)

Cada anúncio tem as seguintes informações:

### Dados do imóvel
| Coluna | Descrição |
|--------|-----------|
| `tipo` | apartamento, casa, terreno, cobertura, flat, comercial, rural |
| `area_construida` | Área útil em m² |
| `area_terreno` | Área total do terreno em m² |
| `quartos` | Número de quartos |
| `suites` | Número de suítes |
| `banheiros` | Número de banheiros |
| `vagas` | Vagas de garagem |
| `andar` | Andar do imóvel |
| `total_andares` | Total de andares do prédio |
| `amenities` | Comodidades do imóvel (piscina, churrasqueira, etc.) |
| `complex_amenities` | Comodidades do condomínio |

### Financeiro
| Coluna | Descrição |
|--------|-----------|
| `preco` | Preço do imóvel |
| `preco_condominio` | Valor mensal do condomínio |
| `iptu` | Valor anual do IPTU |
| `periodo_iptu` | Período do IPTU |
| `preco_por_m2` | Preço por metro quadrado (calculado) |
| `aluguel_total` | Valor total do aluguel |
| `garantias_aluguel` | Garantias aceitas para aluguel |
| `finalidade` | Venda ou aluguel |
| `contract_type` | SALE ou RENTAL |

### Localização
| Coluna | Descrição |
|--------|-----------|
| `rua` | Logradouro |
| `bairro` | Bairro |
| `cidade` | Cidade |
| `estado` | Estado (sigla) |
| `cep` | CEP |
| `zona` | Zona da cidade |
| `latitude` | Coordenada geográfica |
| `longitude` | Coordenada geográfica |

### Descrição e mídia
| Coluna | Descrição |
|--------|-----------|
| `url` | Link do anúncio |
| `titulo` | Título |
| `descricao` | Descrição completa |
| `fotos_urls` | URLs de todas as fotos (separadas por \|) |
| `image_count` | Quantidade de fotos |

### Anunciante
| Coluna | Descrição |
|--------|-----------|
| `anunciante_nome` | Nome da imobiliária/anunciante |
| `anunciante_telefone` | Telefone de contato |

### Metadata
| Coluna | Descrição |
|--------|-----------|
| `listing_id` | ID interno do VivaReal |
| `stamps` | Selos (destaque, super destaque, etc.) |
| `data_publicacao` | Data de publicação do anúncio |
| `data_ultima_atualizacao` | Última atualização |
| `data_coleta` | Data/hora em que o scraper coletou |
| `portal` | "vivareal" |
| `status_anuncio` | Status (ativo, etc.) |
| `usage_types` | Residencial, comercial, etc. |
| `property_sub_type` | APARTMENT, HOME, LAND, etc. |
| `aceita_permuta` | Se aceita permuta |
| `imovel_disponivel` | Se o imóvel está disponível (True) |
| `imovel_atualizado` | Se foi atualizado (null inicialmente) |

---

## Serviços AWS utilizados

### Amazon S3 (Simple Storage Service)

**O que é:** Armazenamento de objetos na nuvem. Funciona como um "disco infinito" onde guardamos os arquivos Parquet.

**Por que usar:** 
- Sem limite de storage (paga por GB armazenado)
- Sem limite de processamento (não pausa por uso excessivo como o Azure SQL)
- Durabilidade de 99.999999999% (dados praticamente impossíveis de perder)
- Custo previsível e baixo

**Preço:**
- Storage: $0.023 por GB/mês (32 GB = $0.74/mês)
- PUT requests: $0.005 por 1.000 requests (~$0.05/mês)
- GET requests: $0.0004 por 1.000 requests (~$0.01/mês)

### Amazon Athena (consultas SQL)

**O que é:** Motor de consultas SQL serverless. Permite fazer SELECT, WHERE, GROUP BY diretamente nos arquivos Parquet do S3, sem precisar de banco de dados ligado 24h.

**Por que usar:**
- Não precisa de servidor rodando
- Paga apenas quando consulta (por volume de dados escaneados)
- Suporta SQL padrão
- Lê Parquet nativamente (formato colunar = escaneia menos dados = mais barato)

**Preço:**
- $5 por TB de dados escaneados
- Com Parquet particionado por estado, uma consulta típica escaneia ~100 MB = $0.0005

**Exemplo de consulta:**
```sql
SELECT cidade, COUNT(*) as total, AVG(preco) as preco_medio
FROM vivareal
WHERE estado = 'SP' AND quartos >= 3
GROUP BY cidade
ORDER BY total DESC
```

### GitHub Actions (compute)

**O que é:** Serviço de CI/CD do GitHub que executa código automaticamente. Funciona como um "computador na nuvem" que roda o Python do scraper.

**Por que usar:**
- Gratuito para repositórios públicos (2.000 min/mês para privados)
- Executa automaticamente via cron (a cada 6h)
- 7 GB de RAM, 14 GB de disco
- Timeout de 6h por job

**Preço:** $0.00 (gratuito)

---

## Formato Parquet

**O que é:** Formato de arquivo colunar e comprimido, otimizado para análise de dados.

**Vantagens sobre JSON/CSV:**
- **10-20x menor** que JSON (compressão colunar)
- **Consultas mais rápidas** (lê só as colunas necessárias)
- **Tipagem forte** (números são números, não strings)
- **Suporte nativo** no Athena, Pandas, Spark, etc.

**Exemplo:** 5 GB de dados em JSON → ~300-500 MB em Parquet

---

## Como executar

### Localmente
```bash
pip install -r requirements.txt
python main.py --estado SP                          # VivaReal, um estado
python main.py --estado SP --parte 2/6              # VivaReal, 2ª de 6 partes do estado
python main.py --all-estados --reset                # nova coleta do zero (pasta coleta=<data> nova)
python main.py --estado MG --cidade "Belo Horizonte" --sem-detalhes   # teste rápido
python main.py --portal lugarcerto --all-estados
python main.py --portal imovelweb --estado RJ --workers 12 --passadas 2
```

Requer variáveis de ambiente (ver `.env.example`):
```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=scraper-imoveis-data
AWS_REGION=us-east-2
```

### Imovelweb (no seu computador)

No PowerShell, dentro da pasta `scraper-imoveis` (precisa do `.env` com as credenciais da AWS):
```powershell
Set-ExecutionPolicy -Scope Process Bypass   # só se o Windows bloquear o script
.	ools
odar_imovelweb.ps1
```
O script coleta todos os estados e repete sozinho até terminar. Pode fechar a janela ou reiniciar o computador: ao rodar de novo, continua de onde parou (progresso em `progress/imovelweb/` no S3). Deixe o Windows sem suspender enquanto roda (Configurações → Sistema → Energia → "Suspender: Nunca"). Ele troca de identidade de navegador sozinho quando o Cloudflare responde 403.

### Via GitHub Actions (automático)
- Três workflows (`scraper.yml` = VivaReal, `scraper-lugarcerto.yml` e `scraper-chavesnamao.yml`), agendados de hora em hora e continuando de onde pararam; estados concluídos são pulados
- O **Imovelweb não roda no GitHub**: o Cloudflare dele bloqueia os IPs de datacenter (testado com 12 identidades de navegador pelo workflow manual `diagnostico-imovelweb.yml`, todas com 403). Ele roda no seu computador — ver abaixo
- Para uma **nova coleta**, dispare manualmente em Actions → "Run workflow" com **reset** marcado
- Juntos somam ~26 jobs; o plano gratuito roda 20 ao mesmo tempo e o resto espera na fila

---

## Secrets necessários no GitHub

| Secret | Valor |
|--------|-------|
| `AWS_ACCESS_KEY_ID` | Access key do IAM user |
| `AWS_SECRET_ACCESS_KEY` | Secret key do IAM user |
| `AWS_S3_BUCKET` | `scraper-imoveis-data` |
| `AWS_REGION` | `us-east-2` |

---

## Estimativa de custos mensais

| Serviço | Uso | Custo |
|---------|-----|-------|
| S3 (storage) | 32 GB | $0.74/mês |
| S3 (PUT requests) | ~6.000 PUTs | $0.03/mês |
| S3 (GET requests) | ~100 GETs | $0.00/mês |
| Athena | 600 consultas/mês (50 MB cada) | $0.14/mês |
| GitHub Actions | Cron 4x/dia | $0.00 |
| **Total mensal** | — | **~$0.91/mês (~R$ 5,00)** |
| **Custo inicial (upfront)** | PUTs para subir dados | **$0.16 (uma vez)** |

### Créditos AWS disponíveis

A conta AWS possui **$100 em créditos gratuitos** (AWS Free Tier), com $0.00 usados até o momento. Os créditos são válidos até **05 de agosto de 2027** (1 ano).

Serviços cobertos pelos créditos que utilizamos:
- ✓ Amazon Simple Storage Service (S3)
- ✓ Amazon Athena
- ✓ AWS Lambda (se necessário no futuro)

Com o custo estimado de ~$0.91/mês, os $100 de crédito cobrem o projeto por **todo o período de validade** sem nenhum custo real.

### Comparação de custo: Athena vs S3 Select (600 consultas/mês)

| | **Athena** | **S3 Select** |
|---|---|---|
| **Preço** | $5 por TB escaneado | $0.002/GB escaneado + $0.0007/GB retornado |
| **Cenário: 1 GB/consulta** | 600 × 1 GB × $0.005 = **$3.00/mês** | ❌ Não disponível para Parquet |
| **Cenário: 0.2 GB/consulta** | 600 × 0.2 GB × $0.005 = **$0.60/mês** | ❌ Não disponível para Parquet |
| **Capacidade** | SQL completo (GROUP BY, AVG, JOIN) | ❌ Descontinuado para Parquet |
| **Velocidade** | 3-5 segundos | — |

**Nota importante:** O S3 Select foi **descontinuado pela AWS para arquivos Parquet** (tanto via console quanto via API). A única forma de consultar dados Parquet no S3 é via **Amazon Athena** ou baixando o arquivo e lendo localmente com Python/Pandas.

**Custo real do Athena para nosso caso (1 cidade por vez):**
- Cada arquivo de cidade tem ~2-20 MB em Parquet
- Consulta filtrando por estado escaneia apenas os arquivos daquele estado
- 600 consultas/mês × ~5 MB por consulta = 3 GB escaneado
- 3 GB × $5/TB = **$0.015/mês** (menos de 2 centavos)

### Formas de consultar os dados

| Método | Custo | Velocidade | Quando usar |
|--------|-------|------------|-------------|
| **Amazon Athena** | ~$0.015/mês | 3-5 segundos | Consultas SQL via aplicação ou console |
| **Download + Pandas** | $0.00 | Imediato (local) | Análise exploratória, desenvolvimento |
| **S3 Select** | — | — | ❌ Não funciona com Parquet (descontinuado) |

```
S3 Standard storage:
  32 GB × $0.023/GB = $0.74/mês

PUT, COPY, POST, LIST requests (upload de arquivos):
  6.000 PUTs × $0.000005/request = $0.03/mês
  (1 arquivo Parquet por cidade + arquivos de progresso)

GET, SELECT requests (leitura):
  100 GETs × $0.0000004/request = ~$0.00/mês

Athena (consultas SQL nos dados Parquet):
  Preço: $5 por TB escaneado
  600 consultas/mês × 50 MB por consulta = 30 GB escaneado
  30 GB × $5/TB = $0.14/mês (confirmado pela AWS Pricing Calculator)

Custo inicial (upfront): $0.16 (PUTs para upload inicial dos dados)
Total mensal estimado: ~$0.91/mês (~R$ 5,00)
Total anual estimado: ~$10.92 (~R$ 60,00)
```

---

## Tecnologias

- **Python 3.12**
- **boto3** — SDK AWS para upload no S3
- **pandas** + **pyarrow** — Conversão para Parquet
- **requests** — Chamadas à API do VivaReal
- **GitHub Actions** — Automação e agendamento
