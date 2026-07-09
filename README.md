# Scraper VivaReal - Imóveis Brasil

Coleta dados de anúncios imobiliários do VivaReal (~1.6M anúncios ativos) via API interna, com busca por cidade e bairro para cobertura nacional completa.

## Como funciona

1. Para cada estado, descobre todas as **cidades** via API de locations
2. Para cada cidade, descobre todos os **bairros**
3. Para cada bairro, pagina todos os anúncios disponíveis (24 por página)
4. Salva no banco Turso (SQLite remoto, 5GB grátis)
5. Roda automaticamente na nuvem via GitHub Actions (a cada 6h)
6. Progresso salvo entre execuções — continua de onde parou

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

# Um estado com limite
python main.py --estado SP --limit 100

# Todos os estados sem limite
python main.py --all-estados

# Resetar progresso (reprocessa tudo)
python main.py --all-estados --reset
```

## GitHub Actions (execução automática)

O workflow roda a cada 6h automaticamente. Configuração:
1. Adicionar secrets no repo: `TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN`
2. O scraper continua de onde parou entre execuções
3. Repositório público = minutos ilimitados

## Estrutura

```
├── config/settings.py         # Configurações e constantes
├── scrapers/
│   ├── base.py                # Classe base (retry, rate limit, paginação)
│   └── vivareal.py            # API VivaReal + busca por bairro
├── parsers/extractor.py       # Utilitários de extração
├── storage/database.py        # Turso HTTP API
├── utils/
│   ├── proxy_manager.py       # Rotação de proxies (opcional)
│   └── rate_limiter.py        # Delays entre requests
├── main.py                    # Entry point com progresso
├── .github/workflows/         # GitHub Actions
└── requirements.txt
```

## Limitações

- API não documentada — pode mudar sem aviso
- Limite de ~10k resultados por busca (contornado pela busca por bairro)
- Cidades descobertas via API — pode não pegar 100% das cidades menores
- Delay de 1-3s entre requests (rate limiting)
- Turso free: 5GB (~500k-800k anúncios com raw_json)
