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
$IndicadorCaixa33LDir = "C:\Users\Administrador\Documents\Indicador-Caixa33L"
$IndicadorCaixa42LDir = "C:\Users\Administrador\Documents\Indicador-Caixa42L"
$IndicadorCaixasGeralDir = "C:\Users\Administrador\Documents\Indicador-CaixasGeral"
$ReversaStageDir = "C:\Users\Administrador\Documents\NOVO INDICADOR DE REVERSA - VTC_STAGE"
$LogDir = Join-Path $ScriptDir "logs"
$IntervalSec = 600
$StepTimeoutSec = 300
$GitRemote = "origin"
$GitBranch = "main"
$ExpectedRemote = "gerenciamento-termico/Dashboards.git"
$GitHubRepoUrl = "https://github.com/gerenciamento-termico/Dashboards.git"
$GitHubPagesBase = "https://gerenciamento-termico.github.io/Dashboards"

function Import-DotEnvFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim().TrimStart([char]0xFEFF)
        $value = $line.Substring($eq + 1).Trim()
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        )) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

Import-DotEnvFile (Join-Path $ScriptDir "hub_share.env")
Import-DotEnvFile (Join-Path $ScriptDir ".env")

$HubSharePath = if ($env:AURA_HUB_SHARE) { $env:AURA_HUB_SHARE } else { "" }
$HubGitUser = if ($env:AURA_HUB_USER) { $env:AURA_HUB_USER } else { "gerenciamento-termico" }
$HubShareUser = $HubGitUser
$HubSharePassword = [string]$env:AURA_HUB_PASSWORD
if ($env:AURA_HUB_GIT_URL) { $GitHubRepoUrl = $env:AURA_HUB_GIT_URL }
if ($env:AURA_HUB_PAGES_BASE) { $GitHubPagesBase = $env:AURA_HUB_PAGES_BASE.TrimEnd("/") }

$PublishFiles = @(
    ".nojekyll",
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
    "relatorio_analitico_vtcbox.csv",
    "INDICADOR_CAIXA_VELHA_130L.html",
    "relatorio_analitico_caixa_velha_130l.xlsx",
    "relatorio_analitico_caixa_velha_130l.csv",
    "INDICADOR_CAIXA_33L.html",
    "relatorio_analitico_caixa_33l.xlsx",
    "relatorio_analitico_caixa_33l.csv",
    "INDICADOR_CAIXA_42L.html",
    "relatorio_analitico_caixa_42l.xlsx",
    "relatorio_analitico_caixa_42l.csv"
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
    "relatorio_analitico_vtcbox.csv",
    "INDICADOR_CAIXA_VELHA_130L.html",
    "relatorio_analitico_caixa_velha_130l.xlsx",
    "relatorio_analitico_caixa_velha_130l.csv",
    "INDICADOR_CAIXA_33L.html",
    "relatorio_analitico_caixa_33l.xlsx",
    "relatorio_analitico_caixa_33l.csv",
    "INDICADOR_CAIXA_42L.html",
    "relatorio_analitico_caixa_42l.xlsx",
    "relatorio_analitico_caixa_42l.csv"
)

$Urls = @(
    "$GitHubPagesBase/gerenciamento_termico.html",
    "$GitHubPagesBase/ESTOQUE_DATALOGGERS.html",
    "$GitHubPagesBase/CONTROLE_ENTREGAS_20D.html",
    "$GitHubPagesBase/REVERSA_DATALOGGERS.html",
    "$GitHubPagesBase/GESTAO_DISPOSITIVOS.html",
    "$GitHubPagesBase/RASTREIO_CAIXAS_SEM_DATALOGGER.html",
    "$GitHubPagesBase/INDICADOR_VTCBOX.html",
    "$GitHubPagesBase/relatorio_analitico_vtcbox.xlsx",
    "$GitHubPagesBase/relatorio_analitico_vtcbox.csv",
    "$GitHubPagesBase/INDICADOR_CAIXA_VELHA_130L.html",
    "$GitHubPagesBase/relatorio_analitico_caixa_velha_130l.xlsx",
    "$GitHubPagesBase/relatorio_analitico_caixa_velha_130l.csv",
    "$GitHubPagesBase/INDICADOR_CAIXA_33L.html",
    "$GitHubPagesBase/relatorio_analitico_caixa_33l.xlsx",
    "$GitHubPagesBase/relatorio_analitico_caixa_33l.csv",
    "$GitHubPagesBase/INDICADOR_CAIXA_42L.html",
    "$GitHubPagesBase/relatorio_analitico_caixa_42l.xlsx",
    "$GitHubPagesBase/relatorio_analitico_caixa_42l.csv"
)

$IndicadorCaixasGeralPublishFiles = @(
    "INDICADOR_CAIXAS_GERAL.html",
    "relatorio_analitico_caixas_geral.xlsx",
    "relatorio_analitico_caixas_geral.csv",
    "relatorio_analitico_caixas_vtcbox.xlsx",
    "relatorio_analitico_caixas_vtcbox.csv",
    "relatorio_analitico_caixas_130l.xlsx",
    "relatorio_analitico_caixas_130l.csv",
    "relatorio_analitico_caixas_33l.xlsx",
    "relatorio_analitico_caixas_33l.csv",
    "relatorio_analitico_caixas_42l.xlsx",
    "relatorio_analitico_caixas_42l.csv"
)
$PublishFiles += $IndicadorCaixasGeralPublishFiles
$DashboardFiles += $IndicadorCaixasGeralPublishFiles
$Urls += $IndicadorCaixasGeralPublishFiles | ForEach-Object { "$GitHubPagesBase/$_" }

$ExtraPagesFiles = @(
    "index.html"
)
$PublishFiles += $ExtraPagesFiles
$Urls += $ExtraPagesFiles | ForEach-Object { "$GitHubPagesBase/$_" }

$PendenciasSincronismoFiles = @(
    "PENDENCIAS_SINCRONISMO.html",
    "PENDENCIAS_SINCRONISMO.csv",
    "PENDENCIAS_SINCRONISMO.xlsx",
    "MANIFESTO_SNAPSHOT_PENDENCIAS_SINCRONISMO.json"
)
$PublishFiles += $PendenciasSincronismoFiles
$DashboardFiles += @("PENDENCIAS_SINCRONISMO.html", "PENDENCIAS_SINCRONISMO.csv")
$Urls += @("$GitHubPagesBase/PENDENCIAS_SINCRONISMO.html")
$PendenciasSeedCsv = Join-Path $PublishDir "sem_sync_ares_589_pendente.csv"
$PendenciasSeedCsvDownloads = "C:\Users\Administrador\Downloads\sem_sync_ares_589_pendente.csv"

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

    Write-Log ("RUN: {0} {1}" -f $FilePath, (Protect-SensitiveText $argLine))
    Write-Log ("CWD: {0}" -f $WorkingDirectory)

    $outFile = Join-Path $env:TEMP ("aura_step_{0}_{1}.out" -f $PID, ([guid]::NewGuid().ToString("N")))
    $errFile = Join-Path $env:TEMP ("aura_step_{0}_{1}.err" -f $PID, ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $argLine -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        throw ("Nao foi possivel iniciar {0}: {1}" -f $Name, $_.Exception.Message)
    }

    if (-not $process.WaitForExit($TimeoutSec * 1000)) {
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        } catch {}
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        throw ("{0} excedeu timeout de {1}s" -f $Name, $TimeoutSec)
    }

    $stdout = if (Test-Path $outFile) { Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue } else { "" }
    $stderr = if (Test-Path $errFile) { Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($stdout) { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log (Protect-SensitiveText $_) } }
    if ($stderr) { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log ("ERR: " + (Protect-SensitiveText $_)) } }
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue

    $process.Refresh()
    if ($null -eq $process.ExitCode) {
        # Start-Process as vezes nao preenche ExitCode em comandos muito rapidos.
        $exitCode = 0
    } else {
        $exitCode = [int]$process.ExitCode
    }

    $combined = "{0}`n{1}" -f [string]$stdout, [string]$stderr
    $isGit = ($FilePath -eq "git") -or ($Name -like "git *")
    $isPythonVersion = ($Name -like '*python*' -or ($Arguments -contains '--version')) -and ($combined -match 'Python \d+\.\d+')
    if ($isGit -and $combined -match 'fatal:|could not read Username|Authentication failed|error: failed to execute prompt') {
        $exitCode = 1
    }
    if (-not $isGit -and -not $isPythonVersion -and $combined -match 'Traceback \(most recent call last\)|RuntimeError:') {
        $exitCode = 1
    }
    if ($Name -like '*fresco*' -and $combined -notmatch 'STATUS VALIDADO_COM_FONTES_FRESCAS') {
        $exitCode = 1
    }
    if ($isPythonVersion) {
        $exitCode = 0
    }
    if ($exitCode -ne 0) {
        $detail = Protect-SensitiveText (($combined.Trim() -replace '\s+', ' '))
        if ($detail.Length -gt 400) { $detail = $detail.Substring(0, 400) }
        throw ("{0} falhou com codigo {1}: {2}" -f $Name, $exitCode, $detail)
    }

    if (-not $Quiet -and $stdout) {
        $stdout.TrimEnd() -split "`r?`n" | Select-Object -Last 3 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    }
}

function Select-Python {
    $candidates = @(
        "C:\Users\Administrador\AppData\Local\Programs\Python\Python311\python.exe",
        (Join-Path $PackageDir ".venv\Scripts\python.exe"),
        "python",
        "py"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -like '*\*' -and -not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        try {
            Invoke-LoggedProcess -FilePath $candidate -Arguments @("--version") -Name "python --version" -TimeoutSec 30 -Quiet
            Write-Log ("Python selecionado: {0}" -f $candidate)
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
    Ensure-HubRemote
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
    if ($RelativePath -ieq "CONTROLE_ENTREGAS_20D.csv") { return 128 }
    if ($RelativePath -ieq "index.html") { return 200 }
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
    Write-Log ("COPIADO: {0} -> {1}" -f $Source, $destination)
}

function Invoke-NetUse {
    param(
        [string[]]$Arguments
    )
    $outFile = Join-Path $env:TEMP ("aura_netuse_{0}_{1}.out" -f $PID, ([guid]::NewGuid().ToString("N")))
    $errFile = Join-Path $env:TEMP ("aura_netuse_{0}_{1}.err" -f $PID, ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process -FilePath "net.exe" -ArgumentList $Arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $stdout = if (Test-Path -LiteralPath $outFile) { [string](Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        $stderr = if (Test-Path -LiteralPath $errFile) { [string](Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = (Protect-SensitiveText (($stdout + "`n" + $stderr).Trim()))
        }
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Connect-HubShare {
    if (Test-Path -LiteralPath $HubSharePath) {
        Write-Log ("HUB: pasta acessivel: {0}" -f $HubSharePath)
        return
    }

    if (-not $HubSharePassword) {
        throw "Senha do hub nao encontrada. Preencha AURA_HUB_PASSWORD em hub_share.env"
    }

    $deleteResult = Invoke-NetUse -Arguments @("use", $HubSharePath, "/delete", "/y")
    Write-Log ("HUB: limpeza de conexao anterior codigo={0}" -f $deleteResult.ExitCode)

    $usersToTry = @($HubShareUser)
    if ($HubShareUser -notmatch '\\') {
        $usersToTry += ("gerenciamento-termico\{0}" -f $HubShareUser)
        $usersToTry += (".\{0}" -f $HubShareUser)
    }
    if ($usersToTry -notcontains "Gtm") {
        $usersToTry += "Gtm"
        $usersToTry += "gerenciamento-termico\Gtm"
    }

    $lastError = ""
    foreach ($user in $usersToTry) {
        Write-Log ("HUB: tentando conectar em {0} com usuario {1}" -f $HubSharePath, $user)
        $result = Invoke-NetUse -Arguments @("use", $HubSharePath, $HubSharePassword, "/user:$user", "/persistent:no")
        if ($result.ExitCode -eq 0 -or (Test-Path -LiteralPath $HubSharePath)) {
            Write-Log ("HUB: conectado em {0} com usuario {1}" -f $HubSharePath, $user)
            return
        }
        $lastError = $result.Output
        Write-Log ("HUB: falha usuario {0} codigo={1} {2}" -f $user, $result.ExitCode, $lastError)
    }

    throw ("Nao foi possivel conectar em {0}: {1}" -f $HubSharePath, $lastError)
}

function Publish-ToHubShare {
    Connect-HubShare
    if (-not (Test-Path -LiteralPath $HubSharePath)) {
        throw "Pasta do hub inacessivel: $HubSharePath"
    }

    $files = @($PublishFiles)
    foreach ($extra in @("GESTAO_DISPOSITIVOS_STAGE_DATA.js", "MANIFESTO_SNAPSHOT_REVERSA_DATALOGGERS.json")) {
        if ($files -notcontains $extra) { $files += $extra }
    }

    $copied = 0
    foreach ($file in $files) {
        $src = Join-Path $PublishDir $file
        if (-not (Test-Path -LiteralPath $src)) {
            Write-Log ("HUB: origem ausente, pulando {0}" -f $file)
            continue
        }
        $dest = Join-Path $HubSharePath $file
        $tmp = $dest + ".tmp"
        Copy-Item -LiteralPath $src -Destination $tmp -Force
        Move-Item -LiteralPath $tmp -Destination $dest -Force
        $copied++
        Write-Log ("HUB COPIADO: {0} -> {1}" -f $src, $dest)
    }

    if ($copied -eq 0) {
        throw "Nenhum arquivo foi copiado para $HubSharePath"
    }
    Write-Status "[HUB] Publicacao em gerenciamento-termico/Dashboards" ("OK - {0} arquivos" -f $copied) Green
}

function Protect-SensitiveText {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    $safe = $Text -replace '(https://)([^/\s:@]+):([^@/\s]+)@', '$1***:***@'
    $safe = $safe -replace '(https://)([^@/\s]+)@', '$1***@'
    $safe = $safe -replace 'Authorization:\s*Basic\s+\S+', 'Authorization: Basic ***'
    $safe = $safe -replace 'http\.extraHeader=Authorization: Basic \S+', 'http.extraHeader=Authorization: Basic ***'
    $safe = $safe -replace '(token|password|senha|secret|api[_-]?key)(["'']?\s*[:=]\s*["'']?)[^"''\s]+', '$1$2***'
    return $safe
}

function Get-GitAuthenticatedRemoteUrl {
    if (-not $HubSharePassword) {
        throw "Senha do GitHub ausente. Preencha AURA_HUB_PASSWORD em hub_share.env"
    }
    $gitUser = $HubGitUser
    if ($HubSharePassword -like "github_pat_*" -or $HubSharePassword -like "ghp_*") {
        $gitUser = "x-access-token"
    }
    $user = [uri]::EscapeDataString($gitUser)
    $pass = [uri]::EscapeDataString($HubSharePassword)
    $remote = ($GitHubRepoUrl -replace '^https://', '')
    return ("https://{0}:{1}@{2}" -f $user, $pass, $remote)
}

function Get-GitAuthPrefix {
    return @()
}

function Get-GitHubPushAdvice {
    param([string]$Detail)
    $text = [string]$Detail
    if ($text -match 'Password authentication is not supported|Invalid username or token|Authentication failed') {
        return @"
GitHub recusou o login. Senha de conta (mesmo a do Google) nao autentica git push.
No hub_share.env use AURA_HUB_USER=gerenciamento-termico e AURA_HUB_PASSWORD=Personal Access Token.
"@
    }
    if ($text -match 'Permission to .* denied|returned error: 403') {
        return @"
O token autenticou como gerenciamento-termico, mas nao tem permissao de GRAVAR em gerenciamento-termico/Dashboards.
Edite o token em https://github.com/settings/personal-access-tokens
  Repository access = Dashboards
  Contents = Read and write
  Pages = Write
Salve. O proximo ciclo publica em https://gerenciamento-termico.github.io/Dashboards/
"@
    }
    return (Protect-SensitiveText $text)
}

function Enable-GitHubPages {
    if (-not $HubSharePassword) { return }
    $headers = @{
        Authorization = ("Bearer {0}" -f $HubSharePassword)
        Accept = "application/vnd.github+json"
        "User-Agent" = "AuraDashboards"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $body = @{ source = @{ branch = "main"; path = "/" } } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Method Put -Uri "https://api.github.com/repos/gerenciamento-termico/Dashboards/pages" -Headers $headers -Body $body -ContentType "application/json" | Out-Null
        Write-Log "[GIT] GitHub Pages habilitado em main /"
        Write-Status "[GIT] GitHub Pages" "OK" Green
    } catch {
        Write-Log ("[GIT] GitHub Pages aviso: {0}" -f (Protect-SensitiveText $_.Exception.Message))
        Write-Host "[GIT] GitHub Pages nao foi habilitado pela API. Ligue em Settings > Pages > branch main, pasta /." -ForegroundColor Yellow
    }
}

function Ensure-HubRemote {
    $current = (& git -C $PublishDir remote get-url origin 2>$null)
    if ($current -eq $GitHubRepoUrl) { return }
    if ($current) {
        Write-Log ("[GIT] Atualizando origin de {0} para {1}" -f (Protect-SensitiveText $current), $GitHubRepoUrl)
        Invoke-LoggedProcess -FilePath "git" -Arguments @("remote", "set-url", "origin", $GitHubRepoUrl) -WorkingDirectory $PublishDir -Name "git remote set-url origin" -TimeoutSec 30 -Quiet
    } else {
        Invoke-LoggedProcess -FilePath "git" -Arguments @("remote", "add", "origin", $GitHubRepoUrl) -WorkingDirectory $PublishDir -Name "git remote add origin" -TimeoutSec 30 -Quiet
    }
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
        if ($null -eq $exitCode) { $exitCode = 1 }
        $stdout = if (Test-Path -LiteralPath $outFile) { [string](Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        $stderr = if (Test-Path -LiteralPath $errFile) { [string](Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue) } else { "" }
        if ($exitCode -eq 0 -and ("{0}`n{1}" -f $stdout, $stderr) -match 'fatal:|could not read Username|Authentication failed') {
            $exitCode = 1
        }
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
    Ensure-HubRemote

    $probe = Invoke-GitCapture -Arguments @("ls-remote", "--heads", (Get-GitAuthenticatedRemoteUrl), $GitBranch)
    $probeText = ($probe.Output -join "`n")
    if ($probe.ExitCode -ne 0) {
        Write-GitLines -Title "[GIT] ls-remote:" -Lines $probe.Output
        if ($probeText -match '403|401|Authentication failed|Permission to .* denied|Invalid username or token') {
            Write-Host "[GIT] Token sem permissao de leitura/escrita no remoto; pull ignorado. O push vai falhar ate o token ter Contents: Read and write." -ForegroundColor Yellow
            Write-Log "[GIT] ls-remote recusado por permissao do token; pull ignorado."
            return
        }
        Write-Host "[GIT] Remoto inacessivel; pull ignorado." -ForegroundColor Yellow
        Write-Log "[GIT] ls-remote falhou; pull ignorado."
        return
    }

    $hasRemoteBranch = $probeText -match [regex]::Escape($GitBranch)
    if (-not $hasRemoteBranch) {
        Write-Host "[GIT] Remoto sem branch main ainda; pull ignorado (primeiro envio para gerenciamento-termico/Dashboards)." -ForegroundColor Yellow
        Write-Log "[GIT] Remoto sem branch main; pull ignorado."
        return
    }

    $pullArgs = @("pull", "--rebase", "--autostash", (Get-GitAuthenticatedRemoteUrl), $GitBranch)
    $commandLine = Protect-SensitiveText ("git " + (($pullArgs | ForEach-Object { Quote-Arg $_ }) -join " "))
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

function Update-PendenciasSeedCsv {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $PendenciasSeedCsvDownloads) {
        $candidates.Add($PendenciasSeedCsvDownloads)
    }
    $downloads = "C:\Users\Administrador\Downloads"
    if (Test-Path -LiteralPath $downloads) {
        Get-ChildItem -LiteralPath $downloads -Filter "sem_sync_ares*.csv" -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.FullName) }
    }
    $desktop = "C:\Users\Administrador\Desktop\LISTA SEM SINCRONIZAÇÃO"
    if (Test-Path -LiteralPath $desktop) {
        Get-ChildItem -LiteralPath $desktop -Filter "sem_sync_ares*.csv" -ErrorAction SilentlyContinue | ForEach-Object { $candidates.Add($_.FullName) }
    }
    if ($candidates.Count -eq 0) {
        if (Test-Path -LiteralPath $PendenciasSeedCsv) {
            Write-Log ("PENDENCIAS SEED: reusando {0}" -f $PendenciasSeedCsv)
            return
        }
        throw "CSV organizado de pendencias ausente. Coloque sem_sync_ares_589_pendente.csv em Downloads."
    }
    $latest = $candidates | Get-Unique | ForEach-Object { Get-Item -LiteralPath $_ } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    Copy-Item -LiteralPath $latest.FullName -Destination $PendenciasSeedCsv -Force
    Write-Log ("PENDENCIAS SEED: {0} -> {1}" -f $latest.FullName, $PendenciasSeedCsv)
    Write-Status "[3b] Seed CSV organizado" $latest.Name Green
}

function Publish-Changes {
    $preStaged = @(& git -C $PublishDir diff --cached --name-only)
    if ($preStaged.Count -gt 0) {
        Write-Status "[GIT] Limpando stage anterior" "OK" Yellow
        Write-Log ("[GIT] Stage anterior sera ignorado e nao publicado: " + ($preStaged -join ", "))
        Invoke-LoggedProcess -FilePath "git" -Arguments @("reset", "--mixed", "HEAD") -WorkingDirectory $PublishDir -Name "git reset --mixed" -TimeoutSec 60 -Quiet
    }

    Write-Status "[GIT] Status antes do stage" "OK" Gray
    $statusLinesRaw = @(& git -C $PublishDir status --porcelain --untracked-files=all)
    $statusLines = @($statusLinesRaw | Where-Object {
        $_ -notmatch "ESPECIFICACAO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "LEVANTAMENTO_CONTA_CORRENTE_DISPOSITIVOS\.md" -and
        $_ -notmatch "backup_restore_" -and
        $_ -notmatch "test_db\d*\.py" -and
        $_ -notmatch "(^| )\.env" -and
        $_ -notmatch "hub_share\.env"
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
        "relatorio_analitico_vtcbox.csv",
        "relatorio_analitico_caixa_velha_130l.xlsx",
        "relatorio_analitico_caixa_velha_130l.csv",
        "relatorio_analitico_caixa_33l.xlsx",
        "relatorio_analitico_caixa_33l.csv",
        "relatorio_analitico_caixa_42l.xlsx",
        "relatorio_analitico_caixa_42l.csv"
    )
    $forcedFiles += $IndicadorCaixasGeralPublishFiles | Where-Object { $_ -like "*.xlsx" -or $_ -like "*.csv" }
    $forcedFiles += @("PENDENCIAS_SINCRONISMO.xlsx")
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

    $envStaged = @($staged | Where-Object {
        $name = Split-Path $_ -Leaf
        $name -ne ".env.example" -and (
            $name -eq ".env" -or
            $name -like ".env.*" -or
            $name -like "*.env" -or
            $name -eq "hub_share.env"
        )
    })
    if ($envStaged.Count -gt 0) {
        & git -C $PublishDir restore --staged -- $PublishFiles 2>$null
        throw "Arquivo .env nao pode ser publicado (fica so local): $($envStaged -join ', ')"
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
    try {
        Invoke-LoggedProcess -FilePath "git" -Arguments @("-c", "credential.helper=", "push", (Get-GitAuthenticatedRemoteUrl), "HEAD:$GitBranch") -WorkingDirectory $PublishDir -Name "git push" -TimeoutSec 180
    } catch {
        throw (Get-GitHubPushAdvice -Detail $_.Exception.Message)
    }
    Enable-GitHubPages
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
    Write-Log ("Hub destino Git: {0}" -f $GitHubRepoUrl)
    Write-Host ("Hub destino Git: {0}" -f $GitHubRepoUrl)

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
        if (-not (Invoke-Step "[1/9] Estoque" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_estoque.py")) -WorkingDirectory $PublishDir -Name "gerar_html_estoque.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Estoque" -Message "falha ao conectar/gerar; ESTOQUE_DATALOGGERS.html anterior sera preservado"
        }

        if (-not (Invoke-Step "[2/9] Controle Entregas" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_controle_entregas.py")) -WorkingDirectory $PublishDir -Name "gerar_html_controle_entregas.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Controle Entregas" -Message "falha ao gerar; arquivo anterior sera preservado se existir"
        }

        $reversaOk = $true
        try {
            Write-Status "[3/9] Reversa" "GERANDO - fontes STAGE/dtbPortal/dtbTransporte/MongoARES" Yellow
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $ReversaStageDir "gerar_hibrido_aderente_original_fresco.py")) -WorkingDirectory $ReversaStageDir -Name "gerar_hibrido_aderente_original_fresco.py" -TimeoutSec 480

            $srcManifest = Join-Path $ReversaStageDir "MANIFESTO_SNAPSHOT_HIBRIDO_ADERENTE.json"
            $srcHtml = Join-Path $ReversaStageDir "REVERSA_DATALOGGERS_STAGE.html"
            $manifest = Get-Content -LiteralPath $srcManifest -Raw -Encoding UTF8 | ConvertFrom-Json
            $linhas = $manifest.camada_operacional.linhas_operacionais_finais
            $status = $manifest.fail_closed.status
            $geradoEm = [datetime]$manifest.gerado_em
            if ($geradoEm -lt $cycleStart.AddMinutes(-2)) {
                throw ("Snapshot da reversa esta velho (gerado_em={0}); extracao fresca falhou e o HTML antigo nao sera copiado." -f $geradoEm.ToString("yyyy-MM-dd HH:mm:ss"))
            }
            if ($status -ne "VALIDADO_COM_FONTES_FRESCAS") {
                throw ("Snapshot da reversa nao validado: {0}" -f $status)
            }

            Copy-PublishedFile -Source $srcHtml -DestinationName "REVERSA_DATALOGGERS.html"
            Copy-PublishedFile -Source $srcManifest -DestinationName "MANIFESTO_SNAPSHOT_REVERSA_DATALOGGERS.json"

            $reversaHtml = Join-Path $PublishDir "REVERSA_DATALOGGERS.html"
            $reversaManifest = Join-Path $PublishDir "MANIFESTO_SNAPSHOT_REVERSA_DATALOGGERS.json"
            Test-FreshRequiredFiles -Label "HTML REVERSA" -Paths @($reversaHtml, $reversaManifest) -CycleStart $cycleStart -MinSizeBytes 1024
            $modTime = (Get-Item $reversaHtml).LastWriteTime.ToString("yyyy-MM-dd HH:mm")

            Write-Status "[3/9] Reversa" ("OK - {0} | linhas={1} | {2}" -f $modTime, $linhas, $status) Green
        } catch {
            $err = $_.Exception.Message
            Write-Status "[3/9] Reversa" "ERRO" Red
            Write-Log ("ERRO DETALHE: " + $err)
            Add-StepFailure -Failures $stepFailures -Name "Reversa" -Message "falha ao gerar com fontes STAGE; HTML anterior sera preservado"
            $reversaOk = $false
        }

        if (-not $reversaOk) {
            Write-Host "AVISO: Reversa nao atualizada neste ciclo. Os demais dashboards seguem." -ForegroundColor Yellow
            Write-Log "AVISO: Reversa nao atualizada neste ciclo. Os demais dashboards seguem."
        }

        if (-not (Invoke-Step "[3b] Pendencias de Sincronismo" {
            Update-PendenciasSeedCsv
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_pendencias_sincronismo.py")) -WorkingDirectory $PublishDir -Name "gerar_html_pendencias_sincronismo.py" -TimeoutSec 360
            Test-FreshRequiredFiles -Label "HTML PENDENCIAS SINCRONISMO" -Paths @(
                (Join-Path $PublishDir "PENDENCIAS_SINCRONISMO.html"),
                (Join-Path $PublishDir "PENDENCIAS_SINCRONISMO.csv")
            ) -CycleStart $cycleStart -MinSizeBytes 1024
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Pendencias de Sincronismo" -Message "falha ao gerar; HTML/CSV anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[4/9] Gestao Dispositivos" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $DevDir "exportar_vtc_stage_gestao.py")) -WorkingDirectory $DevDir -Name "exportar_vtc_stage_gestao.py"
            Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html") -DestinationName "GESTAO_DISPOSITIVOS.html"
            Copy-PublishedFile -Source (Join-Path $DevDir "GESTAO_DISPOSITIVOS_STAGE_DATA.js") -DestinationName "GESTAO_DISPOSITIVOS_STAGE_DATA.js"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Gestao Dispositivos" -Message "falha ao gerar/copiar Stage; HTML anterior sera preservado"
        }

        if (-not (Invoke-Step "[5/9] Rastreio Caixas Sem Logger" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py")) -WorkingDirectory $PublishDir -Name "gerar_html_rastreio_caixas_sem_datalogger.py"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Rastreio Caixas Sem Logger" -Message "falha ao gerar; ciclo sera interrompido para evitar publicar arquivo antigo"
            $ok = $false
        }

        if (-not (Invoke-Step "[6/9] Indicador VTCBOX" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorDir "gerar_indicador.py")) -WorkingDirectory $IndicadorDir -Name "Indicador VTCBOX gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "INDICADOR_VTCBOX.html") -DestinationName "INDICADOR_VTCBOX.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "relatorio_analitico_vtcbox.xlsx") -DestinationName "relatorio_analitico_vtcbox.xlsx"
            Copy-PublishedFile -Source (Join-Path $IndicadorDir "relatorio_analitico_vtcbox.csv") -DestinationName "relatorio_analitico_vtcbox.csv"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador VTCBOX" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[7/9] Indicador Caixa Velha 130L" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorCaixaVelhaDir "gerar_indicador.py")) -WorkingDirectory $IndicadorCaixaVelhaDir -Name "Indicador Caixa Velha 130L gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixaVelhaDir "INDICADOR_CAIXA_VELHA_130L.html") -DestinationName "INDICADOR_CAIXA_VELHA_130L.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixaVelhaDir "relatorio_analitico_caixa_velha_130l.xlsx") -DestinationName "relatorio_analitico_caixa_velha_130l.xlsx"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixaVelhaDir "relatorio_analitico_caixa_velha_130l.csv") -DestinationName "relatorio_analitico_caixa_velha_130l.csv"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador Caixa Velha 130L" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[8/9] Indicador Caixa 33L" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorCaixa33LDir "gerar_indicador.py")) -WorkingDirectory $IndicadorCaixa33LDir -Name "Indicador Caixa 33L gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa33LDir "INDICADOR_CAIXA_33L.html") -DestinationName "INDICADOR_CAIXA_33L.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa33LDir "relatorio_analitico_caixa_33l.xlsx") -DestinationName "relatorio_analitico_caixa_33l.xlsx"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa33LDir "relatorio_analitico_caixa_33l.csv") -DestinationName "relatorio_analitico_caixa_33l.csv"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador Caixa 33L" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[9/9] Indicador Caixa 42L" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorCaixa42LDir "gerar_indicador.py")) -WorkingDirectory $IndicadorCaixa42LDir -Name "Indicador Caixa 42L gerar_indicador.py"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa42LDir "INDICADOR_CAIXA_42L.html") -DestinationName "INDICADOR_CAIXA_42L.html"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa42LDir "relatorio_analitico_caixa_42l.xlsx") -DestinationName "relatorio_analitico_caixa_42l.xlsx"
            Copy-PublishedFile -Source (Join-Path $IndicadorCaixa42LDir "relatorio_analitico_caixa_42l.csv") -DestinationName "relatorio_analitico_caixa_42l.csv"
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador Caixa 42L" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
        }

        if (-not (Invoke-Step "[10/10] Indicador Geral de Caixas" {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $IndicadorCaixasGeralDir "gerar_indicador.py")) -WorkingDirectory $IndicadorCaixasGeralDir -Name "Indicador Geral de Caixas gerar_indicador.py"
            foreach ($file in $IndicadorCaixasGeralPublishFiles) {
                Copy-PublishedFile -Source (Join-Path $IndicadorCaixasGeralDir $file) -DestinationName $file
            }
        })) {
            Add-StepFailure -Failures $stepFailures -Name "Indicador Geral de Caixas" -Message "falha ao gerar/copiar; arquivos anteriores serao preservados se existirem"
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
        Write-Host "AVISO: Falha no Estoque ou Gestao de Dispositivos. Esses arquivos nao serao atualizados neste ciclo; os demais seguem." -ForegroundColor Yellow
        Write-Log "AVISO: Falha no Estoque ou Gestao de Dispositivos. Esses arquivos nao serao atualizados neste ciclo; os demais seguem."
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
        (Join-Path $ReversaStageDir "gerar_hibrido_aderente_original_fresco.py"),
        (Join-Path $ReversaStageDir "gerar_snapshot_reversa_vtc_stage.py"),
        (Join-Path $ReversaStageDir "gerar_html_reversa_vtc_stage_hibrido_aderente_original.py"),
        (Join-Path $ReversaStageDir "streamlit\gerar_snapshot_reversa.py"),
        (Join-Path $ReversaStageDir "streamlit\gerar_modelo_final_reversa.py"),
        (Join-Path $PublishDir "gerar_snapshot_reversa_vtc_stage.py"),
        (Join-Path $PublishDir "gerar_html_reversa_vtc_stage_hibrido_aderente_original.py"),
        (Join-Path $PublishDir "gerar_html_rastreio_caixas_sem_datalogger.py"),
        (Join-Path $PublishDir "gerar_html_pendencias_sincronismo.py"),
        (Join-Path $StreamlitDir "gerar_snapshot_reversa.py"),
        (Join-Path $StreamlitDir "gerar_modelo_final_reversa.py"),
        (Join-Path $DevDir "exportar_planilha_gestao_dispositivos.py"),
        (Join-Path $DevDir "exportar_vtc_stage_gestao.py"),
        (Join-Path $DevDir "GESTAO_DISPOSITIVOS.html"),
        (Join-Path $PublishDir "gerenciamento_termico.html"),
        (Join-Path $IndicadorDir "gerar_indicador.py"),
        (Join-Path $IndicadorCaixaVelhaDir "gerar_indicador.py"),
        (Join-Path $IndicadorCaixa33LDir "gerar_indicador.py"),
        (Join-Path $IndicadorCaixa42LDir "gerar_indicador.py"),
        (Join-Path $IndicadorCaixasGeralDir "gerar_indicador.py")
    )
    foreach ($path in $required) {
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Arquivo obrigatorio ausente: $path"
        }
    }

    $pyCacheDir = Join-Path $env:TEMP "aura_pycache_check"
    New-Item -ItemType Directory -Force -Path $pyCacheDir | Out-Null
    Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments (@("-X", "pycache_prefix=$pyCacheDir", "-m", "py_compile") + ($required | Where-Object { $_ -like "*.py" })) -WorkingDirectory $PublishDir -Name "py_compile scripts" -TimeoutSec 120 -Quiet

    Ensure-HubRemote
    $remote = (& git -C $PublishDir remote get-url origin 2>$null)
    Write-Host ("[OK] Git origin: {0}" -f $remote) -ForegroundColor Green
    Write-Log ("CHECK origin OK: {0}" -f $remote)
    if (-not $HubSharePassword) {
        Write-Host "[AVISO] AURA_HUB_PASSWORD ausente em hub_share.env; o push para gerenciamento-termico/Dashboards vai falhar." -ForegroundColor Yellow
    }

    Write-Host "[OK] CHECK concluido. Nenhum commit/push foi feito." -ForegroundColor Green
    Write-Log "CHECK concluido."
}

$mutex = New-Object System.Threading.Mutex($false, "Global\AuraDashboardsUpdate10Min")
if (-not $mutex.WaitOne(0)) {
    Write-Host "Ja existe uma instancia do atualizador unificado em execucao." -ForegroundColor Yellow
    Write-Host "Feche a janela antiga do ATUALIZAR_TUDO_10_MIN.bat e abra de novo." -ForegroundColor Yellow
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
