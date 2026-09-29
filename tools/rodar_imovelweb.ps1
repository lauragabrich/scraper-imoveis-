# Coleta do Imovelweb neste computador.
#
# O Cloudflare do Imovelweb bloqueia as maquinas do GitHub (IP de datacenter da
# Microsoft), mas deixa passar IP residencial. Por isso este portal roda aqui.
#
# Pode fechar a janela ou apertar Ctrl+C a qualquer momento: ao rodar de novo,
# continua de onde parou (o progresso fica no S3, em progress/imovelweb/).
# O script repete a coleta sozinho ate todos os estados estarem concluidos.
#
# Uso, no PowerShell, dentro da pasta scraper-imoveis:
#     .\tools\rodar_imovelweb.ps1
# Se o Windows bloquear o script, rode antes (so vale para esta janela):
#     Set-ExecutionPolicy -Scope Process Bypass

$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path ".env")) {
    Write-Host "Falta o arquivo .env com as credenciais da AWS nesta pasta: $raiz"
    exit 1
}

for ($rodada = 1; $rodada -le 50; $rodada++) {
    Write-Host ""
    Write-Host "=== Imovelweb - rodada $rodada - $(Get-Date -Format 'dd/MM HH:mm') ==="
    python main.py --portal imovelweb --all-estados --workers 12 --passadas 2
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Imovelweb concluido em todos os estados."
        break
    }
    Write-Host "Ainda ha estados pendentes; nova rodada em 5 minutos (Ctrl+C para parar)..."
    Start-Sleep -Seconds 300
}
