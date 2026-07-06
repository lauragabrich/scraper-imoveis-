# Scraper de Anúncios Imobiliários - Brasil

Scraper multi-portal para coleta de anúncios imobiliários em escala nacional.

## Portais Suportados

| Portal | Dificuldade | Proteção |
|--------|-------------|----------|
| ImovelWeb | Baixa | Leve |
| LugarCerto | Fácil | Nenhuma |
| VivaReal | Média | Moderada |
| ZapImóveis | Média | Moderada |
| OLX | Alta | Cloudflare forte |

## Estrutura

```
scraper-imoveis/
├── config/
│   └── settings.py          # Configurações gerais
├── scrapers/
│   ├── base.py              # Classe base do scraper
│   ├── imovelweb.py         # Scraper ImovelWeb
│   ├── lugarcerto.py        # Scraper LugarCerto
│   ├── vivareal.py          # Scraper VivaReal
│   ├── zapimoveis.py        # Scraper ZapImóveis
│   └── olx.py               # Scraper OLX
├── parsers/
│   └── extractor.py         # Extração de dados do HTML/JSON
├── storage/
│   └── database.py          # Persistência (PostgreSQL)
├── utils/
│   ├── proxy_manager.py     # Gerenciamento de proxies rotativos
│   ├── rate_limiter.py      # Controle de rate limiting
│   └── sitemap_parser.py    # Parser de sitemaps XML
├── main.py                  # Entry point
├── requirements.txt
└── README.md
```

## Instalação

```bash
pip install -r requirements.txt
```

## Configuração

1. Copie `.env.example` para `.env`
2. Configure a conexão do banco PostgreSQL
3. (Opcional) Configure proxies rotativos

## Uso

```bash
# Rodar todos os portais
python main.py --all

# Rodar portal específico
python main.py --portal vivareal

# Rodar por estado
python main.py --portal vivareal --estado SP

# Apenas coletar URLs do sitemap
python main.py --portal imovelweb --sitemap-only
```

## Dados Extraídos

- Preço (venda/aluguel)
- Área (m²)
- Quartos / Suítes
- Banheiros
- Vagas de garagem
- Endereço (rua, bairro, cidade, estado)
- Coordenadas (lat/lng)
- Tipo (apartamento, casa, terreno, etc.)
- URL do anúncio
- Data de coleta
