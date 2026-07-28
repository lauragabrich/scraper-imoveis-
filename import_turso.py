"""
Importa dados do SQLite (Turso export) para Azure SQL.
Uso: python import_turso.py
"""
import sqlite3
import pyodbc
from config.settings import settings

# Conexão SQLite (arquivo exportado do Turso)
SQLITE_FILE = r"C:\Users\laura\Downloads\vivareal (1).db"

# Conexão Azure SQL
conn_str = (
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={settings.AZURE_SQL_SERVER};"
    f"DATABASE={settings.AZURE_SQL_DATABASE};"
    f"UID={settings.AZURE_SQL_USER};"
    f"PWD={settings.AZURE_SQL_PASSWORD};"
    f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
)

def main():
    print("Conectando ao SQLite...", flush=True)
    sqlite_conn = sqlite3.connect(SQLITE_FILE)
    sqlite_conn.row_factory = sqlite3.Row
    cursor_sqlite = sqlite_conn.cursor()

    # Conta registros
    cursor_sqlite.execute("SELECT COUNT(*) FROM anuncios")
    total = cursor_sqlite.fetchone()[0]
    print(f"Total no SQLite: {total} anúncios", flush=True)

    print("Conectando ao Azure SQL...", flush=True)
    azure_conn = pyodbc.connect(conn_str)
    azure_conn.autocommit = True
    cursor_azure = azure_conn.cursor()

    # Pega colunas que existem no Azure
    cursor_azure.execute("SELECT TOP 0 * FROM anuncios")
    azure_columns = set(col[0] for col in cursor_azure.description)

    # Pega colunas do SQLite
    cursor_sqlite.execute("SELECT * FROM anuncios LIMIT 1")
    sqlite_columns = [desc[0] for desc in cursor_sqlite.description]

    # Colunas em comum (ignora 'id' pois é auto-increment)
    common_cols = [c for c in sqlite_columns if c in azure_columns and c != "id"]
    print(f"Colunas em comum: {len(common_cols)}", flush=True)

    # Importa em batches
    batch_size = 100
    imported = 0
    errors = 0

    cursor_sqlite.execute("SELECT * FROM anuncios")

    while True:
        rows = cursor_sqlite.fetchmany(batch_size)
        if not rows:
            break

        for row in rows:
            row_dict = dict(row)
            data = {c: row_dict.get(c) for c in common_cols}

            # Pula se não tem URL
            if not data.get("url"):
                continue

            try:
                col_names = ", ".join(data.keys())
                placeholders = ", ".join(["?" for _ in data])
                updates = ", ".join([f"{c}=?" for c in data.keys() if c != "url"])

                sql = f"""
                    MERGE anuncios AS target
                    USING (SELECT ? AS url) AS source
                    ON target.url = source.url
                    WHEN MATCHED THEN UPDATE SET {updates}
                    WHEN NOT MATCHED THEN INSERT ({col_names}) VALUES ({placeholders});
                """
                url_val = data["url"]
                update_vals = [data[c] for c in data.keys() if c != "url"]
                insert_vals = list(data.values())

                cursor_azure.execute(sql, [url_val] + update_vals + insert_vals)
                imported += 1
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Erro: {e}", flush=True)

        print(f"  Importados: {imported}/{total} ({errors} erros)", flush=True)

    sqlite_conn.close()
    azure_conn.close()
    print(f"\nConcluído: {imported} importados, {errors} erros", flush=True)


if __name__ == "__main__":
    main()
