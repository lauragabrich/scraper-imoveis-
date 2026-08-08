# Scraper VivaReal — Imóveis Brasil

Scraper que coleta **todos os anúncios de imóveis** do VivaReal em todo o Brasil, salvando os dados em formato Parquet no Amazon S3.

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

## Como funciona o scraping

### Fluxo de coleta

1. **Percorre todos os 27 estados** do Brasil
2. Para cada estado, obtém **todas as cidades** via API do IBGE (ex: SP = 645 cidades)
3. Para cada cidade, **descobre bairros** via API de locations do VivaReal (busca A-Z + termos comuns)
4. Para cada bairro, busca **imóveis usados** + **lançamentos** (paginando até acabar)
5. **Fallback sem bairro**: busca geral da cidade para capturar anúncios não associados a bairros conhecidos
6. **Descoberta de bairros extras**: extrai bairros novos dos resultados do fallback e busca cada um individualmente
7. **Remove duplicatas** por URL
8. **Salva em Parquet** no S3 (1 arquivo por cidade)

### Salvamento parcial (proteção contra perda de dados)

Para cidades com muitos bairros (ex: São Paulo), o scraper salva parcialmente a cada 30 bairros processados. Se o workflow cair no meio da execução (timeout de 6h), no máximo perde os dados dos últimos 30 bairros em processamento — tudo que já foi salvo permanece no S3.

### Controle de progresso

A cada cidade processada, o scraper salva um arquivo de progresso no S3 (`progress/SP.json`). Na próxima execução, lê esse arquivo e continua de onde parou — não recomeça do zero.

---

## Estrutura dos dados no S3

```
s3://scraper-imoveis-data/
├── vivareal/
│   ├── estado=SP/
│   │   ├── adamantina.parquet
│   │   ├── sao-paulo.parquet
│   │   ├── sao-paulo_part500.parquet    (salvamento parcial)
│   │   └── campinas.parquet
│   ├── estado=RJ/
│   │   ├── rio-de-janeiro.parquet
│   │   └── niteroi.parquet
│   └── ...
└── progress/
    ├── SP.json
    ├── RJ.json
    └── ...
```

Particionado por estado → cidade. O Athena consegue escanear apenas o estado desejado, economizando custo.

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
python main.py --estado SP              # Um estado
python main.py --all-estados            # Todos os estados
python main.py --estado SP --limit 100  # Com limite
python main.py --all-estados --reset    # Resetar progresso
```

Requer variáveis de ambiente (ver `.env.example`):
```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=scraper-imoveis-data
AWS_REGION=us-east-2
```

### Via GitHub Actions (automático)
- Roda automaticamente a cada 6 horas
- Pode ser disparado manualmente em Actions → "Run workflow"
- Suporta parâmetros: estado específico ou ALL, limite de anúncios

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
