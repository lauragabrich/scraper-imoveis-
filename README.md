# Scraper VivaReal - Imóveis Brasil

Coleta **todos** os anúncios imobiliários de venda do VivaReal no Brasil inteiro — imóveis usados e lançamentos, todas as cidades (5.570 municípios IBGE), todos os bairros.

## Abordagem técnica

### API interna (glue-api)

Utilizamos engenharia reversa para identificar a API REST interna do VivaReal, consumida pelo próprio frontend do site:

```
https://glue-api.vivareal.com/v2/listings
```

Essa API não é documentada publicamente. Foi descoberta inspecionando as requisições HTTP do navegador (DevTools → Network). Retorna JSON estruturado sem necessidade de autenticação — apenas requer o header `x-domain: www.vivareal.com.br` e um User-Agent de navegador.

### Parâmetros da API

```
?addressState=São Paulo
&addressCity=Campinas
&addressNeighborhood=Centro
&businessType=SALE
&listingType=USED        (ou DEVELOPMENT para lançamentos)
&size=24
&from=0                  (paginação)
&categoryPage=RESULT
```

### Vantagens vs scraping HTML

| API interna | Scraping HTML |
|---|---|
| Dados em JSON estruturado | Precisa parsear HTML (frágil) |
| 24 anúncios por request | 1 anúncio por página |
| Datas exatas (createdAt, updatedAt) | Nem sempre disponíveis |
| Coordenadas, CEP, amenities | Dependem da estrutura do HTML |
| Rápido (~1s por request) | Lento (~3-5s por anúncio) |

## Fluxo de coleta

```
1. Lista IBGE → 5.570 municípios (todas as cidades do Brasil)
   ↓
2. Para cada cidade → descobre bairros via API de locations
   GET glue-api.vivareal.com/v2/locations?q=A&addressState=São Paulo
   ↓
3. Para cada bairro → pagina anúncios (USED + DEVELOPMENT)
   GET glue-api.vivareal.com/v2/listings?...&from=0
   GET glue-api.vivareal.com/v2/listings?...&from=24
   ... (até acabar)
   ↓
4. Fallback → busca sem bairro (pega anúncios sem bairro definido)
   ↓
5. JSON → extrai campos → salva no Turso
   ↓
6. Progresso salvo no banco a cada cidade concluída
```

## Dados coletados (todos os campos disponíveis)

| Categoria | Campos |
|-----------|--------|
| Preço | preço, condomínio, IPTU, preço/m², aluguel total |
| Características | área construída, área terreno, quartos, suítes, banheiros, vagas, tipo |
| Localização | rua, bairro, cidade, estado, CEP, latitude, longitude, zona |
| Temporalidade | data de publicação, data de última atualização, data de coleta |
| Qualitativo | descrição, URLs das fotos, image_count, amenities, complex_amenities |
| Anunciante | nome, telefone |
| Metadata | listing_id, stamps, contract_type, usage_types, property_sub_type |
| Estrutura | andar, total_andares, aceita_permuta, status |
| Controle | imovel_disponivel, imovel_atualizado (preenchidos depois) |
| Backup | raw_json (resposta completa da API) |

## Uso local

```bash
pip install -r requirements.txt

# Um estado
python main.py --estado SP

# Cidade específica
python main.py --estado SP --cidade "Campinas"

# Todos os estados (Brasil inteiro)
python main.py --all-estados

# Com limite
python main.py --estado SP --limit 100

# Resetar progresso
python main.py --all-estados --reset
```

## GitHub Actions (execução automática)

Roda a cada 6h automaticamente. Configuração:
1. Adicionar secrets: `TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`
2. Progresso salvo no banco Turso — não depende de cache do GitHub
3. Repositório público = minutos ilimitados

## Estrutura

```
├── config/settings.py         # Configurações
├── scrapers/
│   ├── base.py                # Classe base (retry, rate limit)
│   └── vivareal.py            # API VivaReal (cidades + bairros + paginação)
├── parsers/extractor.py       # Utilitários de extração
├── storage/database.py        # Turso HTTP API + tabela de progresso
├── utils/
│   ├── ibge_cidades.py        # Lista completa de municípios (API IBGE)
│   ├── proxy_manager.py       # Proxies (opcional)
│   └── rate_limiter.py        # Delays entre requests
├── main.py                    # Entry point
├── .github/workflows/         # GitHub Actions
└── requirements.txt
```

## Cobertura

- ✅ 27 estados
- ✅ 5.570 municípios (lista IBGE completa)
- ✅ Todos os bairros de cada cidade (descobertos via API)
- ✅ Fallback sem bairro (pega anúncios sem bairro definido)
- ✅ Imóveis usados + lançamentos
- ✅ Apenas venda (aluguel excluído intencionalmente)

## Limitações

- API não documentada — pode mudar sem aviso
- Cidades sem anúncios no VivaReal retornam 0 resultados (esperado)
- Delay de 1-3s entre requests (rate limiting)
- Limite de ~10k resultados por bairro (raro de atingir)
- Turso free: 5GB de armazenamento
