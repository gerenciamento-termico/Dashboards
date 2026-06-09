param(
    [ValidateSet("LOOP", "ONCE", "CHECK")]
    [string]$Mode = "LOOP"
)

$ErrorActionPreference = "Stop"
$env:PYTHONDONTWRITEBYTECODE = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PublishDir = $ScriptDir
$PackageDir = Resolve-Path (Join-Path $ScriptDir "..")
$StreamlitDir = Join-Path $PackageDir "streamlit"
$DevDir = Join-Path $PackageDir "EM DESENVOLVIMENTO"
$IndicadorDir = "C:\Users\Administrador\Documents\Indicador-VTCBOX"
$LogDir = Join-Path $ScriptDir "logs"
$IntervalSec = 600
$StepTimeoutSec = 300
$GitRemote = "origin"
$GitBranch = "main"
$ExpectedRemote = "banco-aura-dashboard.git"

$PublishFiles = @(
    "aura-hub.html",
    "ESTOQUE_DATALOGGERS.html",
    "CONTROLE_ENTREGAS_20D.html",
    "HTMLACOMPANHAMENTO.html",
    "REVERSA_DATALOGGERS.html",
    "GESTAO_DISPOSITIVOS.html",
    "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js",
    "GESTAO_DISPOSITIVOS_STAGE_DATA.js",
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "INDICADOR_VTCBOX.html",
    "relatorio_analitico_vtcbox.xlsx"
)

$DashboardFiles = @(
    "ESTOQUE_DATALOGGERS.html",
    "CONTROLE_ENTREGAS_20D.html",
    "HTMLACOMPANHAMENTO.html",
    "REVERSA_DATALOGGERS.html",
    "GESTAO_DISPOSITIVOS.html",
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "INDICADOR_VTCBOX.html",
    "relatorio_analitico_vtcbox.xlsx"
)

$Urls = @(
    "https://luan9753.github.io/banco-aura-dashboard/aura-hub.html",
    "https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html",
    "https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html",
    "https://luan9753.github.io/banco-aura-dashboard/HTMLACOMPANHAMENTO.html",
    "https://luan9753.github.io/banco-aura-dashboard/REVERSA_DATALOGGERS.html",
    "https://luan9753.github.io/banco-aura-dashboard/GESTAO_DISPOSITIVOS.html",
    "https://luan9753.github.io/banco-aura-dashboard/RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "https://luan9753.github.io/banco-aura-dashboard/INDICADOR_VTCBOX.html",
    "https://luan9753.github.io/banco-aura-dashboard/relatorio_analitico_vtcbox.xlsx"
)

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("atualizar_tudo_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log {
    param([string]$Message = "")
    $line = if ($Message) { "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message } else { "" }
    try {
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch {
        Write-Host ("[AVISO] Nao foi possivel gravar no log {0}: {1}" -f $LogFile, $_.Exception.Message) -ForegroundColor Yellow
    }
}

function Write-Status {
    param(
        [string]$Label,
        [string]$Status,
        [ConsoleColor]$Color = [ConsoleColor]::Gray
    )
    $text = "{0,-42} {1}" -f $Label, $Status
    Write-Host $text -ForegroundColor $Color
    Write-Log $text
}

function Quote-Arg {
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Invoke-LoggedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $ScriptDir,
        [string]$Name = $FilePath,
        [int]$TimeoutSec = $StepTimeoutSec,
        [switch]$Quiet
    )

    $argLine = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join " "

    Write-Log ("RUN: {0} {1}" -f $FilePath, $argLine)
    Write-Log ("CWD: {0}" -f $WorkingDirectory)

    $outFile = Join-Path $env:TEMP ("aura_step_{0}_{1}.out" -f $PID, ([guid]::NewGuid().ToString("N")))
    $errFile = Join-Path $env:TEMP ("aura_step_{0}_{1}.err" -f $PID, ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $argLine -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru -Wait -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        throw ("Nao foi possivel iniciar {0}: {1}" -f $Name, $_.Exception.Message)
    }

    $stdout = if (Test-Path $outFile) { Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue } else { "" }
    $stderr = if (Test-Path $errFile) { Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($stdout) { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log $_ } }
    if ($stderr) { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log ("ERR: " + $_) } }
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue

    $exitCode = $process.ExitCode
    if ($exitCode -ne 0) {
        throw ("{0} falhou com codigo {1}" -f $Name, $exitCode)
    }

    if (-not $Quiet -and $stdout) {
        $stdout.TrimEnd() -split "`r?`n" | Select-Object -Last 3 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    }
}

function Select-Python {
    $candidates = @(
        (Join-Path $PackageDir ".venv\Scripts\python.exe"),
        "C:\Users\Administrador\AppData\Local\Programs\Python\Python311\python.exe",
        "python",
        "py"
    )
    foreach ($candidate in $candidates) {
        try {
            Invoke-LoggedProcess -FilePath $candidate -Arguments @("--version") -Name "python --version" -TimeoutSec 30 -Quiet
            return $candidate
        } catch {
            Write-Log ("Python candidato falhou: {0} - {1}" -f $candidate, $_.Exception.Message)
        }
    }
    throw "Python nao encontrado."
}

function Test-Repo {
    if (-not (Test-Path (Join-Path $PublishDir ".git"))) {
        throw "Repositorio Git nao encontrado em $PublishDir"
    }
    Invoke-LoggedProcess -FilePath "git" -Arguments @("rev-parse", "--is-inside-work-tree") -WorkingDirectory $PublishDir -Name "git rev-parse" -TimeoutSec 30 -Quiet
    $remote = (& git -C $PublishDir remote get-url origin 2>$null)
    if (-not $remote -or ($remote -notlike "*$ExpectedRemote*")) {
        throw "Remote origin inesperado: $remote"
    }
}

function Invoke-Step {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    try {
        & $Action
        Write-Status $Label "OK" Green
        return $true
    } catch {
        Write-Status $Label "ERRO" Red
        Write-Log ("ERRO DETALHE: " + $_.Exception.Message)
        return $false
    }
}

function Add-StepFailure {
    param(
        [System.Collections.ArrayList]$Failures,
        [string]$Name,
        [string]$Message
    )
    [void]$Failures.Add(("{0}: {1}" -f $Name, $Message))
    Write-Log ("AVISO ETAPA: {0}: {1}" -f $Name, $Message)
}

function Invoke-OptionalProcess {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $ScriptDir,
        [string]$Name = $FilePath,
        [int]$TimeoutSec = $StepTimeoutSec
    )
    try {
        Invoke-LoggedProcess -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory -Name $Name -TimeoutSec $TimeoutSec
        return [pscustomobject]@{
            Ok = $true
            Message = ""
        }
    } catch {
        Write-Log ("AVISO: {0} falhou, mantendo arquivo publicado anterior quando existir. Detalhe: {1}" -f $Label, $_.Exception.Message)
        return [pscustomobject]@{
            Ok = $false
            Message = $_.Exception.Message
        }
    }
}

function Get-MinFileSize {
    param(
        [string]$RelativePath
    )
    if ($RelativePath -like "*.xlsx") { return 1024 }
    if ($RelativePath -like "*.js") { return 1024 }
    return 5120
}

function Write-ValidationLine {
    param(
        [string]$RelativePath,
        [string]$StatusText,
        [string]$Path,
        [string]$Detail = "",
        [ConsoleColor]$Color = [ConsoleColor]::Gray
    )
    $line = "{0,-58} {1}" -f ("[VALIDACAO] " + $RelativePath), $StatusText
    Write-Host $line -ForegroundColor $Color
    Write-Log $line
    if ($Path) {
        $pathLine = "             caminho: $Path"
        Write-Host $pathLine -ForegroundColor DarkGray
        Write-Log $pathLine
    }
    if ($Detail) {
        $detailLine = "             detalhe: $Detail"
        Write-Host $detailLine -ForegroundColor DarkGray
        Write-Log $detailLine
    }
}

function Test-PublishedFile {
    param(
        [string]$RelativePath,
        [datetime]$CycleStart
    )

    $path = Join-Path $PublishDir $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-ValidationLine -RelativePath $RelativePath -StatusText "ERRO - arquivo nao encontrado" -Path $path -Color Red
        return [pscustomobject]@{
            Ok = $false
            Reason = "arquivo nao encontrado"
            Path = $path
        }
    }

    $item = Get-Item -LiteralPath $path
    $minSize = Get-MinFileSize -RelativePath $RelativePath
    if ($item.Length -lt $minSize) {
        Write-ValidationLine -RelativePath $RelativePath -StatusText "ERRO - tamanho invalido" -Path $path -Detail ("tamanho={0} bytes; minimo={1} bytes" -f $item.Length, $minSize) -Color Red
        return [pscustomobject]@{
            Ok = $false
            Reason = "tamanho invalido ($($item.Length) bytes; minimo $minSize bytes)"
            Path = $path
        }
    }

    $freshDetail = "tamanho={0} bytes; modificado={1}" -f $item.Length, $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
    if ($item.LastWriteTime -lt $CycleStart.AddMinutes(-1)) {
        $freshDetail += "; aviso=arquivo nao foi regravado neste ciclo"
    }

    Write-ValidationLine -RelativePath $RelativePath -StatusText "OK" -Path $path -Detail $freshDetail -Color Green
    return [pscustomobject]@{
        Ok = $true
        Reason = ""
        Path = $path
    }
}

function Test-PublishedFiles {
    param(
        [datetime]$CycleStart,
        [string[]]$Files
    )

    $failures = @()
    foreach ($file in $Files) {
        $result = Test-PublishedFile -RelativePath $file -CycleStart $CycleStart
        if (-not $result.Ok) {
            $failures += ("{0}: {1} ({2})" -f $file, $result.Reason, $result.Path)
        }
    }

    if ($failures.Count -gt 0) {
        throw ("Falha na validacao dos arquivos publicados: " + ($failures -join "; "))
    }
}

function Copy-PublishedFile {
    param(
        [string]$Source,
        [string]$DestinationName
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Arquivo de origem nao encontrado: $Source"
    }
    $destination = Join-Path $PublishDir $DestinationName
    Copy-Item -LiteralPath $Source -Destination $destination -Force
    (Get-Item -LiteralPath $destination).LastWriteTime = Get-Date
    Write-Log ("COPIADO: {0} -> {1}" -f $Source, $destination)
}

function Sync-GitBeforeCycle {
    Invoke-LoggedProcess -FilePath "git" -Arguments @("pull", "--rebase", "--autostash", $GitRemote, $GitBranch) -WorkingDirectory $PublishDir -Name "git pull --rebase --autostash" -TimeoutSec 180
}

function Update-HubTimestamp {
    $hubPath = Join-Path $PublishDir "aura-hub.html"
    if (-not (Test-Path -LiteralPath $hubPath -PathType Leaf)) {
        throw "Hub Aura nao encontrado: $hubPath"
    }

    $agora = Get-Date -Format "dd/MM/yyyy HH:mm"
    $content = Get-Content -LiteralPath $hubPath -Raw -Encoding UTF8
    $pattern = '(Banco Aura Dashboard Hub[^<]*Atualizado em )\d{2}/\d{2}/\d{4}( \d{2}:\d{2})?'
    if ($content -notmatch $pattern) {
        throw "Rodape do Hub nao encontrado para atualizar timestamp."
    }

    $updated = [regex]::Replace($content, $pattern, { param($match) $match.Groups[1].Value + $agora }, 1)
    $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($hubPath, $updated, $utf8NoBom)
    (Get-Item -LiteralPath $hubPath).LastWriteTime = Get-Date
    Write-Log ("HUB TIMESTAMP: aura-hub.html atualizado para {0}" -f $agora)
}

function Publish-Changes {
    $preStaged = @(& git -C $PublishDir diff --cached --name-only)
    if ($preStaged.Count -gt 0) {
        throw "Ja existem arquivos staged antes do ciclo: $($preStaged -join ', ')"
    }

    Write-Status "[GIT] Status antes do stage" "OK" Gray
    $statusLines = @(& git -C $PublishDir status --porcelain --untracked-files=all)
    if ($statusLines.Count -eq 0) {
        Write-Host "      sem alteracoes no working tree" -ForegroundColor DarkGray
        Write-Log "      sem alteracoes no working tree"
    } else {
        $statusLines | ForEach-Object {
            Write-Host ("      " + $_) -ForegroundColor DarkGray
            Write-Log $_
        }
    }

    $normalFiles = $PublishFiles | Where-Object { $_ -ne "relatorio_analitico_vtcbox.xlsx" }
    Invoke-LoggedProcess -FilePath "git" -Arguments (@("add", "--") + $normalFiles) -WorkingDirectory $PublishDir -Name "git add arquivos publicados" -TimeoutSec 60 -Quiet
    Invoke-LoggedProcess -FilePath "git" -Arguments @("add", "-f", "--", "relatorio_analitico_vtcbox.xlsx") -WorkingDirectory $PublishDir -Name "git add -f relatorio xlsx" -TimeoutSec 60 -Quiet

    $staged = @(& git -C $PublishDir diff --cached --name-only)
    $bad = @($staged | Where-Object { $PublishFiles -notcontains $_ })
    if ($bad.Count -gt 0) {
        & git -C $PublishDir restore --staged -- $PublishFiles 2>$null
        throw "Stage contem arquivo fora do escopo: $($bad -join ', ')"
    }

    if ($staged.Count -eq 0) {
        Write-Status "[GIT] Nenhuma alteracao para publicar" "OK" Yellow
        return
    }

    $blockedSecretPattern = '\.env|BEGIN PRIVATE KEY|AURA_DB_PASSWORD|AURA_POSTGRES_PASSWORD'
    $assignedSecretPattern = '(password|senha|token|api[_-]?key|secret)\s*["'']?\s*[:=]\s*["''][^"'']{8,}["'']'
    $stagedTextFiles = @($staged | Where-Object { $_ -notlike "*.xlsx" })
    foreach ($file in $stagedTextFiles) {
        $blockedMatches = Select-String -LiteralPath (Join-Path $PublishDir $file) -Pattern $blockedSecretPattern -CaseSensitive:$false -ErrorAction SilentlyContinue
        $assignedMatches = Select-String -LiteralPath (Join-Path $PublishDir $file) -Pattern $assignedSecretPattern -CaseSensitive:$false -ErrorAction SilentlyContinue
        if ($blockedMatches -or $assignedMatches) {
            & git -C $PublishDir restore --staged -- $PublishFiles 2>$null
            throw "Possivel dado sensivel detectado em $file"
        }
    }

    Write-Status "[GIT] Alteracoes detectadas" "SIM" Yellow
    Write-Log ("STAGED: " + ($staged -join ", "))

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Invoke-LoggedProcess -FilePath "git" -Arguments @("commit", "-m", "Atualiza dashboards Aura - $stamp") -WorkingDirectory $PublishDir -Name "git commit" -TimeoutSec 120
    Invoke-LoggedProcess -FilePath "git" -Arguments @("push", $GitRemote, "HEAD:$GitBranch") -WorkingDirectory $PublishDir -Name "git push" -TimeoutSec 180
    Write-Status "[GIT] Commit e push" "OK" Green
}

function Run-Cycle {
    $cycleStart = Get-Date
    $ok = $true
    $stepFailures = New-Object System.Collections.ArrayList
    Clear-Host
    Write-Host "============================================================"
    Write-Host " AURA DASHBOARDS - ATUALIZACAO AUTOMATICA"
    Write-Host "============================================================"
    Write-Host ""
    Write-Host ("[{0}] Iniciando ciclo..." -f (Get-Date -Format "HH:mm:ss"))
    Write-Host ""

    Write-Log "============================================================"
    Write-Log "AURA DASHBOARDS - CICLO INICIADO"
    Write-Log ("Modo: {0}" -f $Mode)

    try {
        $script:PythonExe = Select-Python
        Test-Repo
        Sync-GitBeforeCycle
        Write-Status "[GIT] Pull/Rebase antes de gerar" "OK" Green
    } catch {
        Write-Status "[GIT] Pull/Rebase antes de gerar" "ERRO" Red
        Write-Log ("ERRO DETALHE: " + $_.Exception.Message)
        $ok = $false
    }

    if ($ok) {
        if (-not (Invoke-Step "[1/7] Estoque" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_estoque.py")) -WorkingDirectory $PublishDir -Name "gerar_html_estoque.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Estoque" -Message "falha ao conectar/gerar; ESTOQUE_DATALOGGERS.html anterior sera preservado"
        }

        if (-not (Invoke-Step "[2/7] Controle Entregas" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_controle_entregas.py")) -WorkingDirectory $PublishDir -Name "gerar_html_controle_entregas.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Controle Entregas" -Message "falha ao gerar; arquivo anterior sera preservado se existir"
        }

        if (-not (Invoke-Step "[3/7] Acompanhamento" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "HTMLACOMPANHAMENTO.py")) -WorkingDirectory $PublishDir -Name "HTMLACOMPANHAMENTO.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Acompanhamento" -Message "falha ao gerar; arquivo anterior sera preservado se existir"
        }

        if (-not (Invoke-Step "[4/7] Reversa" {
            $snapshot = Invoke-OptionalProcess -Label "gerar_snapshot_reversa.py" -FilePath $script:PythonExe -Arguments @((Join-Path $StreamlitDir "gerar_snapshot_reversa.py")) -WorkingDirectory $StreamlitDir -Name "gerar_snapshot_reversa.py"
            if (-not $snapshot.Ok) {
                Add-StepFailure -Failures $stepFailures -Name "Reversa snapshot" -Message $snapshot.Message
            }
            $modelo = Invoke-OptionalProcess -Label "gerar_modelo_final_reversa.py" -FilePath $script:PythonExe -Arguments @((Join-Path $StreamlitDir "gerar_modelo_final_reversa.py")) -WorkingDirectory $StreamlitDir -Name "gerar_modelo_final_reversa.py"
            if (-not $modelo.Ok) {
                Add-StepFailure -Failures $stepFailures -Name "Reversa modelo" -Message $modelo.Message
            }
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_reversa.py")) -WorkingDirectory $PublishDir -Name "gerar_html_reversa.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Reversa" -Message "falha ao gerar HTML; arquivo anterior sera preservado se existir"
        }

        if (-not (Invoke-Step "[5/7] Gestao Dispositivos" {
            $planilha = Invoke-OptionalProcess -Label "exportar_planilha_gestao_dispositivos.py" -FilePath $script:PythonExe -Arguments @((Join-Path $DevDir "exportar_planilha_gestao_dispositivos.py")) -WorkingDirectory $DevDir -Name "exportar_planilha_gestao_dispositivos.py"
            if (-not $planilha.Ok) {
                Add-StepFailure -Failures $stepFailures -Name "Gestao planilha" -Message "Conex*.xlsx ausente ou invalido; GESTAO_DISPOSITIVOS_PLANILHA_DATA.js anterior sera preservado"
            }
            $stage = Invoke-OptionalProcess -Label "exportar_vtc_stage_gestao.py" -FilePath $script:PythonExe -Arguments @((Join-Path $DevDir "exportar_vtc_stage_gestao.py")) -WorkingDirectory $DevDir -Name "exportar_vtc_stage_gestao.py"
            if (-not $stage.Ok) {
                Add-StepFailure -Failures $stepFailures -Name "Gestao VTC Stage" -Message $stage.Message
            }
            Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html") -DestinationName "GESTAO_DISPOSITIVOS.html"
            if (Test-Path -LiteralPath (Join-Path $DevDir "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js")) {
                Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js") -DestinationName "GESTAO_DISPOSITIVOS_PLANILHA_DATA.js"
            }
            if (Test-Path -LiteralPath (Join-Path $DevDir "GESTAO_DISPOSITIVOS_STAGE_DATA.js")) {
                Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS_STAGE_DATA.js") -DestinationName "GESTAO_DISPOSITIVOS_STAGE_DATA.js"
            }
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Gestao Dispositivos" -Message "falha ao publicar arquivos da gestao; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[6/7] Rastreio Caixas Sem Logger" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py")) -WorkingDirectory $PublishDir -Name "gerar_html_rastreio_caixas_sem_datalogger.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Rastreio Caixas Sem Logger" -Message "falha ao gerar; arquivo anterior sera preservado se existir"
        }

        if (-not (Invoke-Step "[7/7] Indicador VTCBOX" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorDir "gerar_indicador.py")) -WorkingDirectory $IndicadorDir -Name "Indicador VTCBOX gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "INDICADOR_VTCBOX.html") -DestinationName "INDICADOR_VTCBOX.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "relatorio_analitico_vtcbox.xlsx") -DestinationName "relatorio_analitico_vtcbox.xlsx"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador VTCBOX" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }
    }

    if ($ok) {
        $ok = (Invoke-Step "[VALIDACAO] Arquivos gerados" {
            Test-PublishedFiles -CycleStart $cycleStart -Files $DashboardFiles
            Test-PublishedFiles -CycleStart $cycleStart -Files @("GESTAO_DISPOSITIVOS_PLANILHA_DATA.js", "GESTAO_DISPOSITIVOS_STAGE_DATA.js")
        }) -and $ok
    }
    $blockEstoque = $stepFailures | Where-Object { $_ -match "^Estoque" }
    $blockGestao = $stepFailures | Where-Object { $_ -match "^Gestao" }

    if ($blockEstoque -or $blockGestao) {
        Write-Host "AVISO: Falha identificada no Estoque ou Gestão de Dispositivos. Esses arquivos não serão atualizados neste ciclo, mas os demais dashboards seguirão normalmente." -ForegroundColor Yellow
        Write-Log "AVISO: Falha identificada no Estoque ou Gestão de Dispositivos. Esses arquivos não serão atualizados neste ciclo, mas os demais dashboards seguirão normalmente."
    }

    if ($ok) {
        $ok = (Invoke-Step "[HUB] Timestamp do footer" {
            Update-HubTimestamp
        }) -and $ok
    }

    if ($ok) {
        try {
            Publish-Changes
        } catch {
            Write-Status "[GIT] Commit e push" "ERRO" Red
            Write-Log ("ERRO DETALHE: " + $_.Exception.Message)
            $ok = $false
        }
    }

    Write-Host ""
    if ($ok) {
        if ($stepFailures.Count -gt 0) {
            Write-Host "Ciclo publicado com avisos. Alguns dashboards preservaram a versao anterior." -ForegroundColor Yellow
            Write-Log "Ciclo publicado com avisos. Alguns dashboards preservaram a versao anterior."
            $stepFailures | ForEach-Object {
                Write-Host ("  - " + $_) -ForegroundColor Yellow
                Write-Log ("AVISO FINAL: " + $_)
            }
        } else {
            Write-Host "Ciclo concluido com sucesso." -ForegroundColor Green
            Write-Log "Ciclo concluido com sucesso."
        }
        Write-Log "URLs publicadas:"
        $Urls | ForEach-Object { Write-Log ("  " + $_) }
    } else {
        Write-Host "Ciclo finalizado com erro. Nada foi publicado." -ForegroundColor Red
        Write-Host ("Verifique o log em: {0}" -f $LogFile) -ForegroundColor Yellow
        Write-Log "Ciclo finalizado com erro. Nada foi publicado."
    }

    Write-Log ("Proximo ciclo previsto: {0}" -f ((Get-Date).AddSeconds($IntervalSec).ToString("yyyy-MM-dd HH:mm:ss")))
    return $ok
}

function Run-Check {
    Write-Host "============================================================"
    Write-Host " CHECK - AURA DASHBOARDS"
    Write-Host "============================================================"
    Write-Log "CHECK iniciado."
    $script:PythonExe = Select-Python
    Test-Repo

    $required = @(
        (Join-Path $PublishDir "gerar_html_estoque.py"),
        (Join-Path $PublishDir "gerar_html_controle_entregas.py"),
        (Join-Path $PublishDir "HTMLACOMPANHAMENTO.py"),
        (Join-Path $PublishDir "gerar_html_reversa.py"),
        (Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py"),
        (Join-Path $StreamlitDir "gerar_snapshot_reversa.py"),
        (Join-Path $StreamlitDir "gerar_modelo_final_reversa.py"),
        (Join-Path $DevDir "exportar_planilha_gestao_dispositivos.py"),
        (Join-Path $DevDir "exportar_vtc_stage_gestao.py"),
        (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html"),
        (Join-Path $IndicadorDir "gerar_indicador.py")
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Arquivo obrigatorio ausente: $path"
        }
    }

    $pyCacheDir = Join-Path $env:TEMP "aura_pycache_check"
    New-Item -ItemType Directory -Force -Path $pyCacheDir | Out-Null
    Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments (@("-X", "pycache_prefix=$pyCacheDir", "-m", "py_compile") + ($required | Where-Object { $_ -like "*.py" })) -WorkingDirectory $PublishDir -Name "py_compile scripts" -TimeoutSec 120 -Quiet
    Write-Host "[OK] CHECK concluido. Nenhum commit/push foi feito." -ForegroundColor Green
    Write-Log "CHECK concluido."
}

$mutex = New-Object System.Threading.Mutex($false, "Global\AuraDashboardsUpdate10Min")
if (-not $mutex.WaitOne(0)) {
    Write-Host "Ja existe uma instancia do atualizador unificado em execucao." -ForegroundColor Yellow
    exit 1
}

try {
    if ($Mode -eq "CHECK") {
        Run-Check
        exit 0
    }

    do {
        $cycleOk = Run-Cycle
        if ($Mode -eq "ONCE") {
            if ($cycleOk) { exit 0 } else { exit 1 }
        }
        Write-Host ""
        Write-Host "Proxima atualizacao em 10 minutos... Pressione Ctrl+C para parar."
        Start-Sleep -Seconds $IntervalSec
    } while ($true)
} finally {
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
