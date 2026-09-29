# Completa os estados do Imovelweb coletados antes da correcao do limite de ~5.000
# resultados ordenados por consulta (BA, GO, DF, ES e MT ficaram com 80-94%; MG tinha
# 1 fatia gravada antes da correcao).
#
# Percorre cada estado com a divisao correta e grava SO os anuncios que ainda nao
# estao no S3, em arquivos "complemento_..." separados. Nada e apagado nem
# sobrescrito. Ao terminar um estado, marca-o como concluido tambem para o
# rodar_imovelweb.ps1 (que entao nao coleta esse estado de novo).
#
# Pode interromper e rodar de novo: continua de onde parou.
# Rode com o rodar_imovelweb.ps1 PARADO (os dois ao mesmo tempo dobram o ritmo de
# acesso e podem levar a bloqueio).
#
# Uso, no PowerShell, dentro da pasta scraper-imoveis:
#     .\tools\completar_imovelweb.ps1

$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path ".env")) {
    Write-Host "Falta o arquivo .env com as credenciais da AWS nesta pasta: $raiz"
    exit 1
}

for ($rodada = 1; $rodada -le 20; $rodada++) {
    Write-Host ""
    Write-Host "=== Imovelweb (complemento) - rodada $rodada - $(Get-Date -Format 'dd/MM HH:mm') ==="
    python main.py --portal imovelweb --estado BA,GO,DF,ES,MT,MG --complementar --workers 12 --passadas 2
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Complemento concluido. Agora rode .\tools\rodar_imovelweb.ps1 para continuar os demais estados."
        break
    }
    Write-Host "Ainda ha estados pendentes; nova rodada em 5 minutos (Ctrl+C para parar)..."
    Start-Sleep -Seconds 300
}
