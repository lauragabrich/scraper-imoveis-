# Scraper de Anúncios Imobiliários - VivaReal

Coleta dados de anúncios imobiliários do VivaReal (~1.6M anúncios ativos) via API interna, com busca por bairro para cobertura máxima.

## Como funciona

1. Descobre os bairros de cada capital via API de locations
2. Para cada bairro, pagina todos os anúncios disponíveis
3. Salva no banco Turso (SQLite remoto, 5GB grátis)
4. Roda automaticamente na nuvem via GitHub Actions (a cada 6h)
5. Progresso salvo entre execuções — continua de onde parou

## Dados coletados

| Categoria | Campos |
|-----------|--------|
| Preço | preço, condomínio, IPTU |
| Características | área construída, área terreno, quartos, suítes, banheiros, vagas, tipo |
| Localização | rua, bairro, cidade, estado, CEP, latitude, longitude |
| Temporalidade | data de publicação, data de última atualização |
| Qualitativo | descrição completa, URLs das fotos |

## Uso local

```bash
pip install -r requirements.txt

# Um estado com limite
python main.py --estado SP --limit 100

# Todos os estados sem limite
python main.py --all-estados

# Resetar progresso
python main.py --all-estados --reset
```

## Estrutura

```
├── config/settings.py        # Configurações e constantes
├── scrapers/
│   ├── base.py               # Classe base (retry, rate limit, paginação)
│   └── vivareal.py           # Scraper VivaReal (API + busca por bairro)
├── parsers/extractor.py      # Utilitários de extração de dados
├── storage/database.py       # Persistência no Turso (HTTP API)
├── utils/
│   ├── proxy_manager.py      # Rotação de proxies (opcional)
│   └── rate_limiter.py       # Controle de delays entre requests
├── main.py                   # Entry point
├── .github/workflows/        # GitHub Actions (execução automática)
├── requirements.txt
└── .env                      # Credenciais Turso (não commitado)
```

## Configuração

1. Criar banco no [Turso](https://app.turso.tech)
2. Copiar `.env.example` para `.env` e preencher URL + token
3. Para GitHub Actions: adicionar `TURSO_DATABASE_URL` e `TURSO_AUTH_TOKEN` nos secrets do repositório
