# Cadastra usuarios do SINCRONISMO_48H no Firebase (hash SHA-256 de "usuario:senha")
$ErrorActionPreference = "Stop"
$sha = [System.Security.Cryptography.SHA256]::Create()
function Get-HashHex([string]$s) {
    ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($s))) -replace '-', '').ToLower()
}
$senha = 'mudar@123'
# chave do no = usuario com "." trocado por "_" (Firebase nao aceita "." em chaves);
# o hash usa o login original com ponto, igual ao que a pagina calcula
$usuarios = [ordered]@{
    'caio_cedraz'     = @{ nome = 'Caio Cedraz';     h = (Get-HashHex "caio.cedraz:$senha") }
    'tiago_cicero'    = @{ nome = 'Tiago Cicero';    h = (Get-HashHex "tiago.cicero:$senha") }
    'marlon_henrique' = @{ nome = 'Marlon Henrique'; h = (Get-HashHex "marlon.henrique:$senha") }
    'marcus_galvao'   = @{ nome = 'Marcus Galvao';   h = (Get-HashHex "marcus.galvao:$senha") }
}
$json = $usuarios | ConvertTo-Json -Compress
$r = Invoke-RestMethod -Method Put -Uri 'https://gt-sincronismo-default-rtdb.firebaseio.com/sync48/_usuarios.json' -Body $json -ContentType 'application/json'
Write-Output ("gravado: " + ($r.PSObject.Properties.Name -join ', '))
