# Valida arquivos publicaveis contra o MESMO regex do guarda da rotina
$p1 = '\.env|BEGIN PRIVATE KEY|AURA_DB_PASSWORD|AURA_POSTGRES_PASSWORD'
$p2 = '(password|senha|token|api[_-]?key|secret)\s*["'']?\s*[:=]\s*["''][^"'']{8,}["'']'
foreach ($f in @('SINCRONISMO_48H.html', 'gerenciamento_termico.html')) {
    $m1 = Select-String -LiteralPath $f -Pattern $p1 -CaseSensitive:$false -ErrorAction SilentlyContinue
    $m2 = Select-String -LiteralPath $f -Pattern $p2 -CaseSensitive:$false -ErrorAction SilentlyContinue
    if ($m1 -or $m2) {
        Write-Output ("$f : AINDA DISPARA")
        @($m1) + @($m2) | Select-Object -First 3 | ForEach-Object {
            $l = $_.Line.Trim()
            if ($l.Length -gt 100) { $l = $l.Substring(0, 100) }
            Write-Output ("   -> " + $l)
        }
    } else {
        Write-Output ("$f : limpo")
    }
}
