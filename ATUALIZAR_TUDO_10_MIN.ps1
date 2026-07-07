param(
    [ValidateSet("LOOP", "RUN", "ONCE", "CHECK", "DRY_RUN")]
    [string]$Mode = "LOOP"
)

$ErrorActionPreference = "Stop"
if ($Mode -eq "RUN") { $Mode = "LOOP" }
if ($Mode -eq "DRY_RUN") { $Mode = "CHECK" }
$env:PYTHONDONTWRITEBYTECODE = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PublishDir = $ScriptDir
$PackageDir = Resolve-Path (Join-Path $ScriptDir "..")
$StreamlitDir = Join-Path $PackageDir "streamlit"
$DevDir = Join-Path $PackageDir "EM DESENVOLVIMENTO"
$IndicadorDir = "C:\Users\Administrador\Documents\Indicador-VTCBOX"
$IndicadorCaixaVelhaDir = "C:\Users\Administrador\Documents\Indicador-CaixaVelha130L"
$LogDir = Join-Path $ScriptDir "logs"
$IntervalSec = 600
$StepTimeoutSec = 300
$GitRemote = "origin"
$GitBranch = "main"
$ExpectedRemote = "banco-aura-dashboard.git"

$PublishFiles = @(
    "gerenciamento_termico.html",
    "ESTOQUE_DATALOGGERS.html",
    "CONTROLE_ENTREGAS_20D.html",
    "CONTROLE_ENTREGAS_20D.csv",
    "CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv",
    "REVERSA_DATALOGGERS.html",
    "GESTAO_DISPOSITIVOS.html",
    "GESTAO_DISPOSITIVOS_STAGE_DATA.js",
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "INDICADOR_VTCBOX.html",
    "relatorio_analitico_vtcbox.xlsx",
    "INDICADOR_CAIXA_VELHA_130L.html",
    "relatorio_analitico_caixa_velha_130l.xlsx"
)

$DashboardFiles = @(
    "gerenciamento_termico.html",
    "ESTOQUE_DATALOGGERS.html",
    "CONTROLE_ENTREGAS_20D.html",
    "CONTROLE_ENTREGAS_20D.csv",
    "CONTROLE_ENTREGAS_20D_SLA_PENDENTES.csv",
    "REVERSA_DATALOGGERS.html",
    "GESTAO_DISPOSITIVOS.html",
    "RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "INDICADOR_VTCBOX.html",
    "relatorio_analitico_vtcbox.xlsx",
    "INDICADOR_CAIXA_VELHA_130L.html",
    "relatorio_analitico_caixa_velha_130l.xlsx"
)

$Urls = @(
    "https://luan9753.github.io/banco-aura-dashboard/gerenciamento_termico.html",
    "https://luan9753.github.io/banco-aura-dashboard/ESTOQUE_DATALOGGERS.html",
    "https://luan9753.github.io/banco-aura-dashboard/CONTROLE_ENTREGAS_20D.html",
    "https://luan9753.github.io/banco-aura-dashboard/REVERSA_DATALOGGERS.html",
    "https://luan9753.github.io/banco-aura-dashboard/GESTAO_DISPOSITIVOS.html",
    "https://luan9753.github.io/banco-aura-dashboard/RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "https://luan9753.github.io/banco-aura-dashboard/INDICADOR_VTCBOX.html",
    "https://luan9753.github.io/banco-aura-dashboard/relatorio_analitico_vtcbox.xlsx",
    "https://luan9753.github.io/banco-aura-dashboard/INDICADOR_CAIXA_VELHA_130L.html",
    "https://luan9753.github.io/banco-aura-dashboard/relatorio_analitico_caixa_velha_130l.xlsx"
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

function Test-FreshRequiredFiles {
    param(
        [string]$Label,
        [string[]]$Paths,
        [datetime]$CycleStart,
        [int]$MinSizeBytes = 1
    )

    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw ("{0}: arquivo obrigatorio ausente: {1}" -f $Label, $path)
        }

        $item = Get-Item -LiteralPath $path
        if ($item.Length -lt $MinSizeBytes) {
            throw ("{0}: arquivo invalido: {1} ({2} bytes)" -f $Label, $path, $item.Length)
        }

        if ($item.LastWriteTime -lt $CycleStart.AddMinutes(-1)) {
            throw ("{0}: arquivo nao foi atualizado neste ciclo: {1} (modificado {2})" -f $Label, $path, $item.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"))
        }
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

function Protect-SensitiveText {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    $safe = $Text -replace '(https://)([^/\s:@]+):([^@/\s]+)@', '$1***:***@'
    $safe = $safe -replace '(https://)([^@/\s]+)@', '$1***@'
    $safe = $safe -replace '(token|password|senha|secret|api[_-]?key)(["'']?\s*[:=]\s*["'']?)[^"''\s]+', '$1$2***'
    return $safe
}

function Invoke-GitCapture {
    param([string[]]$Arguments)

    $lines = New-Object System.Collections.Generic.List[string]
    $argLine = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join " "
    $outFile = Join-Path $env:TEMP ("aura_git_{0}_{1}.out" -f $PID, ([guid]::NewGuid().ToString("N")))
    $errFile = Join-Path $env:TEMP ("aura_git_{0}_{1}.err" -f $PID, ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process -FilePath "git" -ArgumentList $argLine -WorkingDirectory $PublishDir -NoNewWindow -PassThru -Wait -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $exitCode = $process.ExitCode
        $stdout = if (Test-Path -LiteralPath $outFile) { [string](Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        $stderr = if (Test-Path -LiteralPath $errFile) { [string](Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        if ($stdout) {
            foreach ($line in @($stdout.TrimEnd() -split "`r?`n")) {
                if ($line) { [void]$lines.Add((Protect-SensitiveText ([string]$line))) }
            }
        }
        if ($stderr) {
            foreach ($line in @($stderr.TrimEnd() -split "`r?`n")) {
                if ($line) { [void]$lines.Add((Protect-SensitiveText ([string]$line))) }
            }
        }
    } catch {
        $exitCode = 1
        [void]$lines.Add((Protect-SensitiveText $_.Exception.Message))
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($lines)
    }
}

function Write-GitLines {
    param(
        [string]$Title,
        [string[]]$Lines,
        [ConsoleColor]$Color = [ConsoleColor]::DarkGray
    )
    Write-Host $Title -ForegroundColor $Color
    Write-Log $Title
    if (-not $Lines -or $Lines.Count -eq 0) {
        Write-Host "      <sem saida>" -ForegroundColor DarkGray
        Write-Log "      <sem saida>"
        return
    }
    foreach ($line in $Lines) {
        Write-Host ("      " + $line) -ForegroundColor $Color
        Write-Log ("      " + $line)
    }
}

function Get-GitProbableReason {
    param(
        [int]$ExitCode,
        [string]$OutputText
    )
    $text = $OutputText.ToLowerInvariant()
    if ($text -match 'unmerged files|unresolved conflict|resolve your current index') { return "existem arquivos com conflito nao resolvido no indice Git" }
    if ($text -match 'would be overwritten by merge|local changes would be overwritten') { return "existem alteracoes locais que seriam sobrescritas pelo pull" }
    if ($text -match 'rebase-merge|rebase-apply|rebase in progress') { return "existe rebase pendente" }
    if ($text -match 'merge_head|merge in progress') { return "existe merge pendente" }
    if ($text -match 'index\.lock|unable to create.*lock') { return "existe lock de Git impedindo escrita no indice" }
    if ($text -match 'authentication failed|could not read username|permission denied|repository not found|403|401') { return "falha de autenticacao/permissao no GitHub" }
    if ($text -match 'ssl|tls|certificate') { return "falha de SSL/certificado ao acessar o GitHub" }
    if ($text -match 'could not resolve host|failed to connect|timed out|network') { return "falha de rede ao acessar o GitHub" }
    if ($ExitCode -ne 0) { return "Git retornou codigo $ExitCode; ver saida acima" }
    return "sem erro identificado"
}

function Write-GitSnapshot {
    Write-Log "[GIT] Diagnostico antes do pull"
    Write-Host "[GIT] Diagnostico antes do pull" -ForegroundColor Gray
    Write-Host ("      Pasta atual: {0}" -f $PublishDir) -ForegroundColor DarkGray
    Write-Log ("[GIT] Pasta atual: {0}" -f $PublishDir)

    $branch = Invoke-GitCapture -Arguments @("branch", "--show-current")
    $branchText = if ($branch.Output.Count -gt 0 -and $branch.Output[0]) { $branch.Output[0] } else { "<desconhecido>" }
    Write-Host ("      Branch atual: {0}" -f $branchText) -ForegroundColor DarkGray
    Write-Log ("[GIT] Branch atual: {0}" -f $branchText)
    if ($branchText -ne "<desconhecido>" -and $branchText -ne $GitBranch) {
        Write-Log ("[GIT] AVISO: branch local diferente do alvo remoto ({0}); pull/push continuam usando {1}/{0}." -f $GitBranch, $GitRemote)
    }

    $remote = Invoke-GitCapture -Arguments @("remote", "get-url", $GitRemote)
    $remoteText = if ($remote.Output.Count -gt 0 -and $remote.Output[0]) { $remote.Output[0] } else { "<desconhecido>" }
    Write-Host ("      Remote origin: {0}" -f $remoteText) -ForegroundColor DarkGray
    Write-Log ("[GIT] Remote origin: {0}" -f $remoteText)
}

function Test-GitBlockingState {
    $status = Invoke-GitCapture -Arguments @("status", "--porcelain=v1", "--branch")
    $displayStatus = @($status.Output | Where-Object {
        $_ -notmatch "ESPECIFICACAO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "LEVANTAMENTO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "backup_restore_" -and
        $_ -notmatch "test_db\d*\.py"
    })
    Write-GitLines -Title "[GIT] Status antes do pull:" -Lines $displayStatus

    $unmerged = @($status.Output | Where-Object { $_ -match '^(DD|AU|UD|UA|DU|AA|UU)\s+' })
    if ($unmerged.Count -gt 0) {
        $reason = "existem arquivos com conflito nao resolvido: " + (($unmerged | ForEach-Object { $_.Substring(3) }) -join ", ")
        Write-Host "[GIT] Resultado: ERRO" -ForegroundColor Red
        Write-Host ("[GIT] Motivo provavel: {0}" -f $reason) -ForegroundColor Red
        Write-Host "[GIT] Orientacao: resolva o conflito, use git add no arquivo resolvido e rode o atualizador novamente." -ForegroundColor Yellow
        Write-Log "[GIT] Resultado: ERRO"
        Write-Log ("[GIT] Motivo provavel: {0}" -f $reason)
        Write-Log "[GIT] Orientacao: resolva o conflito, use git add no arquivo resolvido e rode o atualizador novamente."
        throw $reason
    }

    $rebaseMerge = Test-Path -LiteralPath (Join-Path $PublishDir ".git\rebase-merge")
    $rebaseApply = Test-Path -LiteralPath (Join-Path $PublishDir ".git\rebase-apply")
    $mergeHead = Test-Path -LiteralPath (Join-Path $PublishDir ".git\MERGE_HEAD")
    $indexLock = Test-Path -LiteralPath (Join-Path $PublishDir ".git\index.lock")
    if ($rebaseMerge -or $rebaseApply) { throw "existe rebase pendente em .git; finalize ou use git rebase --abort apos avaliar o estado" }
    if ($mergeHead) { throw "existe merge pendente em .git\MERGE_HEAD; finalize ou aborte o merge apos avaliar o estado" }
    if ($indexLock) { throw "existe .git\index.lock; verifique se outro Git esta em execucao antes de remover" }
}

function Sync-GitBeforeCycle {
    Write-GitSnapshot
    Test-GitBlockingState

    $pullArgs = @("pull", "--rebase", "--autostash", $GitRemote, $GitBranch)
    $commandLine = "git " + (($pullArgs | ForEach-Object { Quote-Arg $_ }) -join " ")
    Write-Host ("[GIT] Executando: {0}" -f $commandLine) -ForegroundColor Gray
    Write-Log ("[GIT] Executando: {0}" -f $commandLine)

    $result = Invoke-GitCapture -Arguments $pullArgs
    Write-GitLines -Title "[GIT] Saida:" -Lines $result.Output

    if ($result.ExitCode -ne 0) {
        $outputText = ($result.Output -join "`n")
        $reason = Get-GitProbableReason -ExitCode $result.ExitCode -OutputText $outputText
        Write-Host "[GIT] Resultado: ERRO" -ForegroundColor Red
        Write-Host ("[GIT] Motivo provavel: {0}" -f $reason) -ForegroundColor Red
        Write-Host "[GIT] Orientacao: corrija o estado acima e rode CHECK/ONCE novamente." -ForegroundColor Yellow
        Write-Log "[GIT] Resultado: ERRO"
        Write-Log ("[GIT] Motivo provavel: {0}" -f $reason)
        Write-Log "[GIT] Orientacao: corrija o estado acima e rode CHECK/ONCE novamente."
        throw ("git pull --rebase --autostash falhou com codigo {0}: {1}" -f $result.ExitCode, $reason)
    }

    Write-Log "[GIT] Resultado: OK"
}

function Publish-Changes {
    $preStaged = @(& git -C $PublishDir diff --cached --name-only)
    if ($preStaged.Count -gt 0) {
        throw "Ja existem arquivos staged antes do ciclo: $($preStaged -join ', ')"
    }

    Write-Status "[GIT] Status antes do stage" "OK" Gray
    $statusLinesRaw = @(& git -C $PublishDir status --porcelain --untracked-files=all)
    $statusLines = @($statusLinesRaw | Where-Object {
        $_ -notmatch "ESPECIFICACAO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "LEVANTAMENTO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "backup_restore_" -and
        $_ -notmatch "test_db\d*\.py"
    })
    if ($statusLines.Count -eq 0) {
        Write-Host "      sem alteracoes no working tree" -ForegroundColor DarkGray
        Write-Log "      sem alteracoes no working tree"
    } else {
        $statusLines | ForEach-Object {
            Write-Host ("      " + $_) -ForegroundColor DarkGray
            Write-Log $_
        }
    }

    $forcedFiles = @(
        "relatorio_analitico_vtcbox.xlsx",
        "relatorio_analitico_caixa_velha_130l.xlsx"
    )
    $normalFiles = $PublishFiles | Where-Object { $forcedFiles -notcontains $_ }
    # Somente inclua no git add os arquivos que existem no disco para evitar falha quando
    # arquivos opcionais (ex: aura-hub.html) estiverem ausentes.
    $existingNormalFiles = @()
    foreach ($f in $normalFiles) {
        if (Test-Path -LiteralPath (Join-Path $PublishDir $f)) { $existingNormalFiles += $f }
    }
    if ($existingNormalFiles.Count -gt 0) {
        Invoke-LoggedProcess -FilePath "git" -Arguments (@("add", "--") + $existingNormalFiles) -WorkingDirectory $PublishDir -Name "git add arquivos publicados" -TimeoutSec 60 -Quiet
    } else {
        Write-Log "Nenhum dos arquivos normais existem no disco; pulando git add para esses arquivos."
    }

    # Relatorios XLSX devem ser forcados apenas se existirem.
    foreach ($forcedFile in $forcedFiles) {
        if (Test-Path -LiteralPath (Join-Path $PublishDir $forcedFile)) {
            Invoke-LoggedProcess -FilePath "git" -Arguments @("add", "-f", "--", $forcedFile) -WorkingDirectory $PublishDir -Name ("git add -f {0}" -f $forcedFile) -TimeoutSec 60 -Quiet
        } else {
            Write-Log ("{0} ausente; pulando git add -f." -f $forcedFile)
        }
    }

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

        $reversaOk = $true
        try {
            $snapshotDir = Join-Path (Split-Path $StreamlitDir) "snapshot_reversa"
            $reqSnaps = @("base_loggers.pkl", "base_agentes.pkl", "recebimento_resumo.pkl", "base_destinatarios.pkl")
            $reqSnapPaths = @($reqSnaps | ForEach-Object { Join-Path $snapshotDir $_ })

            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $StreamlitDir "gerar_snapshot_reversa.py")) -WorkingDirectory $StreamlitDir -Name "gerar_snapshot_reversa.py"
            Test-FreshRequiredFiles -Label "SNAPSHOT REVERSA" -Paths $reqSnapPaths -CycleStart $cycleStart -MinSizeBytes 1024
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $StreamlitDir "gerar_modelo_final_reversa.py")) -WorkingDirectory $StreamlitDir -Name "gerar_modelo_final_reversa.py"
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_reversa.py")) -WorkingDirectory $PublishDir -Name "gerar_html_reversa.py"

            $modeloFinal = Join-Path $snapshotDir "modelo_final.pkl"
            Test-FreshRequiredFiles -Label "MODELO REVERSA" -Paths @($modeloFinal) -CycleStart $cycleStart -MinSizeBytes 1024

            $linhas = & $script:PythonExe -c "import pandas as pd; print(len(pd.read_pickle(r'$modeloFinal')))"
            $modTime = (Get-Item $modeloFinal).LastWriteTime.ToString("yyyy-MM-dd HH:mm")

            Write-Status "[3/6] Reversa" ("OK - SNAPSHOT {0} | linhas={1}" -f $modTime, $linhas) Green
        } catch {
            $err = $_.Exception.Message
            if ($err -match "SNAPSHOT REVERSA AUSENTE/INVALIDO" -or $err -match "FileNotFoundError") {
                Write-Status "[3/6] Reversa" "ERRO - SNAPSHOT REVERSA AUSENTE/INVALIDO" Red
            } else {
                Write-Status "[3/6] Reversa" "ERRO" Red
            }
            Write-Log ("ERRO DETALHE: " + $err)
            Add-StepFailure -Failures $stepFailures -Name "Reversa" -Message "falha ao atualizar snapshots/modelo/HTML; ciclo sera interrompido para evitar publicar dado antigo"
            $reversaOk = $false
        }

        if (-not $reversaOk) {
            $ok = $false
        }

        if (-not (Invoke-Step "[4/6] Gestao Dispositivos" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $DevDir "exportar_vtc_stage_gestao.py")) -WorkingDirectory $DevDir -Name "exportar_vtc_stage_gestao.py"
            Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html") -DestinationName "GESTAO_DISPOSITIVOS.html"
            Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS_STAGE_DATA.js") -DestinationName "GESTAO_DISPOSITIVOS_STAGE_DATA.js"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Gestao Dispositivos" -Message "falha ao gerar/copiar Stage; ciclo sera interrompido para evitar publicar dado antigo"
            $ok = $false
        }

        if (-not (Invoke-Step "[5/6] Rastreio Caixas Sem Logger" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py")) -WorkingDirectory $PublishDir -Name "gerar_html_rastreio_caixas_sem_datalogger.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Rastreio Caixas Sem Logger" -Message "falha ao gerar; ciclo sera interrompido para evitar publicar arquivo antigo"
            $ok = $false
        }

        if (-not (Invoke-Step "[6/6] Indicador VTCBOX" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorDir "gerar_indicador.py")) -WorkingDirectory $IndicadorDir -Name "Indicador VTCBOX gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "INDICADOR_VTCBOX.html") -DestinationName "INDICADOR_VTCBOX.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "relatorio_analitico_vtcbox.xlsx") -DestinationName "relatorio_analitico_vtcbox.xlsx"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador VTCBOX" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[7/7] Indicador Caixa Velha 130L" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorCaixaVelhaDir "gerar_indicador.py")) -WorkingDirectory $IndicadorCaixaVelhaDir -Name "Indicador Caixa Velha 130L gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixaVelhaDir "INDICADOR_CAIXA_VELHA_130L.html") -DestinationName "INDICADOR_CAIXA_VELHA_130L.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixaVelhaDir "relatorio_analitico_caixa_velha_130l.xlsx") -DestinationName "relatorio_analitico_caixa_velha_130l.xlsx"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador Caixa Velha 130L" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }
    }

    if ($ok) {
        $ok = (Invoke-Step "[VALIDACAO] Arquivos gerados" {
            Test-PublishedFiles -CycleStart $cycleStart -Files $DashboardFiles
            Test-PublishedFiles -CycleStart $cycleStart -Files @("GESTAO_DISPOSITIVOS_STAGE_DATA.js")
            Write-Log "PAGINA ESTATICA: gerenciamento_termico.html nao possui script gerador proprio; mantida no ciclo para commit/push e validacao do deploy."
        }) -and $ok
    }
    $blockEstoque = $stepFailures | Where-Object { $_ -match "^Estoque" }
    $blockGestao = $stepFailures | Where-Object { $_ -match "^Gestao" }

    if ($blockEstoque -or $blockGestao) {
        Write-Host "AVISO: Falha identificada no Estoque ou Gestão de Dispositivos. Esses arquivos não serão atualizados neste ciclo, mas os demais dashboards seguirão normalmente." -ForegroundColor Yellow
        Write-Log "AVISO: Falha identificada no Estoque ou Gestão de Dispositivos. Esses arquivos não serão atualizados neste ciclo, mas os demais dashboards seguirão normalmente."
    }

    if ($ok) {
        # aura-hub.html não faz parte do ciclo de atualização a cada 10 minutos.
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
        Write-Log "Paginas estaticas sem gerador proprio: gerenciamento_termico.html"
    } else {
        Write-Host "Ciclo finalizado com erro. Nada foi publicado." -ForegroundColor Red
        Write-Host ("Verifique o log em: {0}" -f $LogFile) -ForegroundColor Yellow
        Write-Log "Ciclo finalizado com erro. Nada foi publicado."
    }

    $nextCycleAt = $cycleStart.AddSeconds($IntervalSec)
    Write-Log ("Proximo ciclo previsto: {0}" -f ($nextCycleAt.ToString("yyyy-MM-dd HH:mm:ss")))
    return $ok
}

function Run-Check {
    Write-Host "============================================================"
    Write-Host " CHECK - AURA DASHBOARDS"
    Write-Host "============================================================"
    Write-Log "CHECK iniciado."
    $script:PythonExe = Select-Python
    Test-Repo
    Write-GitSnapshot
    Test-GitBlockingState

    $required = @(
        (Join-Path $PublishDir "gerar_html_estoque.py"),
        (Join-Path $PublishDir "gerar_html_controle_entregas.py"),
        (Join-Path $PublishDir "gerar_html_reversa.py"),
        (Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py"),
        (Join-Path $StreamlitDir "gerar_snapshot_reversa.py"),
        (Join-Path $StreamlitDir "gerar_modelo_final_reversa.py"),
        (Join-Path $DevDir "exportar_planilha_gestao_dispositivos.py"),
        (Join-Path $DevDir "exportar_vtc_stage_gestao.py"),
        (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html"),
        (Join-Path $PublishDir "gerenciamento_termico.html"),
        (Join-Path $IndicadorDir "gerar_indicador.py"),
        (Join-Path $IndicadorCaixaVelhaDir "gerar_indicador.py")
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
        $loopStartedAt = Get-Date
        $cycleOk = Run-Cycle
        if ($Mode -eq "ONCE") {
            if ($cycleOk) { exit 0 } else { exit 1 }
        }
        $nextRunAt = $loopStartedAt.AddSeconds($IntervalSec)
        $sleepSeconds = [int][Math]::Max(0, [Math]::Ceiling(($nextRunAt - (Get-Date)).TotalSeconds))
        Write-Host ""
        Write-Host ("Proxima atualizacao em {0} segundos ({1}). Pressione Ctrl+C para parar." -f $sleepSeconds, $nextRunAt.ToString("HH:mm:ss"))
        if ($sleepSeconds -gt 0) {
            Start-Sleep -Seconds $sleepSeconds
        }
    } while ($true)
} finally {
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}
