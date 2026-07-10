# Scraper VivaReal - Imóveis Brasil

Coleta **todos** os anúncios imobiliários de venda do VivaReal no Brasil inteiro via API interna — imóveis usados e lançamentos, todas as cidades, todos os bairros.

## Como funciona

1. Para cada **estado** (27), descobre todas as **cidades** via API de locations
2. Para cada **cidade**, descobre todos os **bairros**
3. Para cada **bairro**, busca anúncios de venda:
   - Imóveis usados (`USED`)
   - Lançamentos (`DEVELOPMENT`)
4. Salva no banco **Turso** (SQLite remoto)
5. Progresso salvo **no próprio banco** — continua de onde parou entre execuções
6. Roda automaticamente via **GitHub Actions** a cada 6h

## Dados coletados (todos os campos disponíveis na API)

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

## GitHub Actions

Roda automaticamente a cada 6h. Configuração:
1. Adicionar secrets: `TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`
2. Progresso salvo no banco — não depende de cache do GitHub
3. Repositório público = minutos ilimitados

## Estrutura

```
├── config/settings.py         # Configurações
├── scrapers/
│   ├── base.py                # Classe base (retry, rate limit)
│   └── vivareal.py            # API VivaReal (cidades + bairros + paginação)
├── parsers/extractor.py       # Utilitários de extração
├── storage/database.py        # Turso HTTP API + progresso
├── utils/
│   ├── proxy_manager.py       # Proxies (opcional)
│   └── rate_limiter.py        # Delays entre requests
├── main.py                    # Entry point
├── .github/workflows/         # GitHub Actions
└── requirements.txt
```

## Cobertura

- ✅ 27 estados
- ✅ Todas as cidades com anúncios (~50-100 por estado)
- ✅ Todos os bairros de cada cidade
- ✅ Imóveis usados + lançamentos
- ✅ Apenas venda (aluguel excluído intencionalmente)

## Limitações

- API não documentada — pode mudar sem aviso
- Cidades muito pequenas (sem anúncios no VivaReal) não aparecem na API de locations
- Delay de 1-3s entre requests
- Limite de ~10k resultados por bairro (raro de atingir)
