import requests
from datetime import datetime
from config.settings import settings


class Database:
    """Gerencia conexão com Turso via HTTP API (sem compilação nativa)."""

    def __init__(self):
        # Converte libsql:// para https:// para usar a HTTP API
        url = settings.TURSO_DATABASE_URL
        url = url.replace("libsql://", "https://")
        self.base_url = url
        self.token = settings.TURSO_AUTH_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self._create_table()

    def _execute(self, sql: str, args: list = None) -> dict:
        """Executa uma query SQL via Turso HTTP API."""
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": sql,
                    }
                },
                {"type": "close"}
            ]
        }

        if args:
            payload["requests"][0]["stmt"]["args"] = [
                self._format_arg(a) for a in args
            ]

        response = requests.post(
            f"{self.base_url}/v2/pipeline",
            json=payload,
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _format_arg(self, value):
        """Formata argumento para a API do Turso."""
        if value is None:
            return {"type": "null", "value": None}
        elif isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        elif isinstance(value, float):
            return {"type": "float", "value": value}
        elif isinstance(value, str):
            return {"type": "text", "value": value}
        elif isinstance(value, datetime):
            return {"type": "text", "value": value.isoformat()}
        else:
            return {"type": "text", "value": str(value)}

    def _create_table(self):
        """Cria a tabela de anúncios se não existir."""
        sql = """
            CREATE TABLE IF NOT EXISTS anuncios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                portal TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                preco REAL,
                area_construida REAL,
                area_terreno REAL,
                quartos INTEGER,
                banheiros INTEGER,
                vagas INTEGER,
                tipo TEXT,
                rua TEXT,
                bairro TEXT,
                cidade TEXT,
                estado TEXT,
                data_publicacao TEXT,
                data_ultima_atualizacao TEXT,
                descricao TEXT,
                fotos_urls TEXT,
                preco_condominio REAL,
                iptu REAL,
                latitude REAL,
                longitude REAL,
                finalidade TEXT,
                titulo TEXT,
                suites INTEGER,
                cep TEXT,
                data_coleta TEXT,
                data_atualizacao_scraper TEXT,
                amenities TEXT,
                complex_amenities TEXT,
                preco_por_m2 REAL,
                image_count INTEGER,
                raw_json TEXT,
                usage_types TEXT,
                property_sub_type TEXT,
                andar INTEGER,
                total_andares INTEGER,
                aceita_permuta TEXT,
                status_anuncio TEXT,
                anunciante_nome TEXT,
                anunciante_telefone TEXT,
                listing_id TEXT,
                stamps TEXT,
                contract_type TEXT,
                zona TEXT,
                periodo_iptu TEXT,
                garantias_aluguel TEXT,
                aluguel_total REAL,
                imovel_disponivel TEXT,
                imovel_atualizado TEXT
            )
        """
        self._execute(sql)
        # Adiciona colunas novas se tabela já existir
        new_columns = [
            "amenities TEXT", "complex_amenities TEXT", "preco_por_m2 REAL",
            "image_count INTEGER", "raw_json TEXT", "usage_types TEXT",
            "property_sub_type TEXT", "andar INTEGER", "total_andares INTEGER",
            "aceita_permuta TEXT", "status_anuncio TEXT", "anunciante_nome TEXT",
            "anunciante_telefone TEXT", "listing_id TEXT", "stamps TEXT",
            "contract_type TEXT", "zona TEXT", "periodo_iptu TEXT",
            "garantias_aluguel TEXT", "aluguel_total REAL",
            "imovel_disponivel TEXT", "imovel_atualizado TEXT",
        ]
        for col in new_columns:
            try:
                self._execute(f"ALTER TABLE anuncios ADD COLUMN {col}")
            except Exception:
                pass  # Coluna já existe
        self._execute("CREATE INDEX IF NOT EXISTS idx_portal ON anuncios(portal)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_cidade ON anuncios(cidade)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_bairro ON anuncios(bairro)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_estado ON anuncios(estado)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_tipo ON anuncios(tipo)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_finalidade ON anuncios(finalidade)")
        print("[DB] Tabela 'anuncios' pronta no Turso")

    def save_anuncio(self, data: dict) -> bool:
        """Salva ou atualiza um anúncio (upsert por URL)."""
        try:
            data = self._prepare_data(data)
            columns = list(data.keys())
            placeholders = ", ".join(["?" for _ in columns])
            col_names = ", ".join(columns)

            self._execute(
                f"INSERT OR REPLACE INTO anuncios ({col_names}) VALUES ({placeholders})",
                list(data.values()),
            )
            return True
        except Exception as e:
            print(f"Erro ao salvar anúncio: {e}")
            return False

    def save_batch(self, anuncios: list[dict]) -> int:
        """Salva múltiplos anúncios."""
        saved = 0
        for data in anuncios:
            if self.save_anuncio(data):
                saved += 1
        return saved

    def get_count(self, portal: str = None) -> int:
        """Retorna a contagem de anúncios."""
        try:
            if portal:
                result = self._execute(
                    "SELECT COUNT(*) FROM anuncios WHERE portal = ?", [portal]
                )
            else:
                result = self._execute("SELECT COUNT(*) FROM anuncios")

            # Parse da resposta da API
            rows = result.get("results", [{}])[0].get("response", {}).get("result", {}).get("rows", [])
            if rows:
                return int(rows[0][0].get("value", 0))
            return 0
        except Exception:
            return 0

    def _prepare_data(self, data: dict) -> dict:
        """Prepara dados para inserção."""
        prepared = {}
        for key, value in data.items():
            if isinstance(value, datetime):
                prepared[key] = value.isoformat()
            else:
                prepared[key] = value

        if "data_coleta" not in prepared:
            prepared["data_coleta"] = datetime.utcnow().isoformat()

        return prepared
