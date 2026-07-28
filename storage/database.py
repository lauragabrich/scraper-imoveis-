"""Conexão com Azure SQL Database via pyodbc."""
import pyodbc
from datetime import datetime
from config.settings import settings


class Database:
    """Gerencia conexão com Azure SQL."""

    def __init__(self):
        conn_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={settings.AZURE_SQL_SERVER};"
            f"DATABASE={settings.AZURE_SQL_DATABASE};"
            f"UID={settings.AZURE_SQL_USER};"
            f"PWD={settings.AZURE_SQL_PASSWORD};"
            f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=120;"
        )
        # Retry para lidar com auto-pause do Azure
        for attempt in range(3):
            try:
                self.conn = pyodbc.connect(conn_str)
                self.conn.autocommit = True
                break
            except Exception as e:
                if attempt < 2:
                    import time
                    print(f"[DB] Conexão falhou, tentando novamente em 30s... ({e})", flush=True)
                    time.sleep(30)
                else:
                    raise e
        self._create_tables()

    def _create_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='anuncios' AND xtype='U')
            CREATE TABLE anuncios (
                id INT IDENTITY(1,1) PRIMARY KEY,
                portal NVARCHAR(50) NOT NULL,
                url NVARCHAR(900) NOT NULL UNIQUE,
                preco FLOAT, area_construida FLOAT, area_terreno FLOAT,
                quartos INT, banheiros INT, vagas INT, suites INT,
                tipo NVARCHAR(100), rua NVARCHAR(500), bairro NVARCHAR(200),
                cidade NVARCHAR(200), estado NVARCHAR(5), cep NVARCHAR(20),
                latitude FLOAT, longitude FLOAT,
                titulo NVARCHAR(500), descricao NVARCHAR(MAX),
                fotos_urls NVARCHAR(MAX), image_count INT,
                preco_condominio FLOAT, iptu FLOAT, preco_por_m2 FLOAT,
                finalidade NVARCHAR(50), amenities NVARCHAR(MAX),
                complex_amenities NVARCHAR(MAX),
                data_publicacao NVARCHAR(100), data_ultima_atualizacao NVARCHAR(100),
                data_coleta NVARCHAR(100),
                anunciante_nome NVARCHAR(300), anunciante_telefone NVARCHAR(100),
                listing_id NVARCHAR(100), stamps NVARCHAR(500),
                contract_type NVARCHAR(50), zona NVARCHAR(200),
                usage_types NVARCHAR(200), property_sub_type NVARCHAR(100),
                andar INT, total_andares INT, aceita_permuta NVARCHAR(20),
                status_anuncio NVARCHAR(50), raw_json NVARCHAR(MAX),
                imovel_disponivel NVARCHAR(50), imovel_atualizado NVARCHAR(50),
                periodo_iptu NVARCHAR(50), garantias_aluguel NVARCHAR(500),
                aluguel_total FLOAT
            )
        """)
        cursor.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='scraper_progress' AND xtype='U')
            CREATE TABLE scraper_progress (
                estado NVARCHAR(5) PRIMARY KEY,
                last_page INT DEFAULT 1,
                updated_at NVARCHAR(100)
            )
        """)
        # Índices
        try:
            cursor.execute("CREATE INDEX idx_cidade ON anuncios(cidade)")
        except: pass
        try:
            cursor.execute("CREATE INDEX idx_estado ON anuncios(estado)")
        except: pass
        try:
            cursor.execute("CREATE INDEX idx_bairro ON anuncios(bairro)")
        except: pass
        try:
            cursor.execute("CREATE INDEX idx_tipo ON anuncios(tipo)")
        except: pass
        # Adiciona colunas que podem faltar
        for col in [
            "periodo_iptu NVARCHAR(50)",
            "garantias_aluguel NVARCHAR(500)",
            "aluguel_total FLOAT",
        ]:
            try:
                cursor.execute(f"ALTER TABLE anuncios ADD {col}")
            except: pass
        cursor.close()
        print("[DB] Tabela pronta no Azure SQL", flush=True)

    def save_anuncio(self, data: dict) -> bool:
        """Salva ou atualiza anúncio (MERGE/upsert por URL)."""
        try:
            if "data_coleta" not in data:
                data["data_coleta"] = datetime.utcnow().isoformat()

            # Converte datetimes
            for k, v in data.items():
                if isinstance(v, datetime):
                    data[k] = v.isoformat()

            columns = list(data.keys())
            placeholders = ", ".join(["?" for _ in columns])
            col_names = ", ".join(columns)
            updates = ", ".join([f"{c}=?" for c in columns if c != "url"])

            # MERGE (upsert)
            sql = f"""
                MERGE anuncios AS target
                USING (SELECT ? AS url) AS source
                ON target.url = source.url
                WHEN MATCHED THEN
                    UPDATE SET {updates}
                WHEN NOT MATCHED THEN
                    INSERT ({col_names}) VALUES ({placeholders});
            """

            # Valores: [url_for_match] + [update_values] + [insert_values]
            url_val = data.get("url")
            update_vals = [data[c] for c in columns if c != "url"]
            insert_vals = [data[c] for c in columns]

            cursor = self.conn.cursor()
            cursor.execute(sql, [url_val] + update_vals + insert_vals)
            cursor.close()
            return True
        except Exception as e:
            print(f"Erro ao salvar: {e}", flush=True)
            return False

    def get_progress(self, estado: str) -> int:
        """Retorna última página processada."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT last_page FROM scraper_progress WHERE estado = ?", estado)
            row = cursor.fetchone()
            cursor.close()
            if row:
                return row[0]
        except Exception:
            pass
        return 1

    def save_progress(self, estado: str, last_page: int):
        """Salva progresso."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                MERGE scraper_progress AS target
                USING (SELECT ? AS estado) AS source
                ON target.estado = source.estado
                WHEN MATCHED THEN
                    UPDATE SET last_page = ?, updated_at = ?
                WHEN NOT MATCHED THEN
                    INSERT (estado, last_page, updated_at) VALUES (?, ?, ?);
            """, estado, last_page, datetime.utcnow().isoformat(),
                 estado, last_page, datetime.utcnow().isoformat())
            cursor.close()
        except Exception as e:
            print(f"Erro progresso: {e}", flush=True)
