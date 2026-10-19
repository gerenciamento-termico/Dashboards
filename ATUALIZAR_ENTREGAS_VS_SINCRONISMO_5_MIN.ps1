param(
    [ValidateSet("LOOP", "RUN", "ONCE", "CHECK")]
    [string]$Mode = "LOOP"
)

$ErrorActionPreference = "Stop"
if ($Mode -eq "RUN") { $Mode = "LOOP" }
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUNBUFFERED = "1"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PublishDir = $ScriptDir
$PackageDir = Resolve-Path (Join-Path $ScriptDir "..")
$LogDir = Join-Path $ScriptDir "logs"
$IntervalSec = 300
$GenerateTimeoutSec = 720
$GitRemote = "origin"
$GitBranch = "main"
$ExpectedRemote = "gerenciamento-termico/Dashboards.git"
$GitHubRepoUrl = "https://github.com/gerenciamento-termico/Dashboards.git"
$GitHubPagesBase = "https://gerenciamento-termico.github.io/Dashboards"
$PageUrl = "$GitHubPagesBase/ENTREGAS_VS_SINCRONISMO.html"
$GeneratorName = "gerar_html_entregas_vs_sincronismo.py"
$PublishFiles = @(
    "ENTREGAS_VS_SINCRONISMO.html",
    "ENTREGAS_VS_SINCRONISMO.csv",
    "MANIFESTO_ENTREGAS_VS_SINCRONISMO.json",
    "SINCRONISMO_48H.html"
)
$Sync48Generator = "gerar_html_sync48.py"
$PublishFolder = "bases_sincronismo"

function Test-InPublishScope {
    param([string]$Path)
    if ($PublishFiles -contains $Path) { return $true }
    return $Path -like "$PublishFolder/*"
}

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

$HubGitUser = if ($env:AURA_HUB_USER) { $env:AURA_HUB_USER } else { "gerenciamento-termico" }
$HubSharePassword = [string]$env:AURA_HUB_PASSWORD
if ($env:AURA_HUB_GIT_URL) { $GitHubRepoUrl = $env:AURA_HUB_GIT_URL }
if ($env:AURA_HUB_PAGES_BASE) { $GitHubPagesBase = $env:AURA_HUB_PAGES_BASE.TrimEnd("/") }
$PageUrl = "$GitHubPagesBase/ENTREGAS_VS_SINCRONISMO.html"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("atualizar_entregas_vs_{0}.log" -f (Get-Date -Format "yyyyMMdd"))

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

function Protect-SensitiveText {
    param([string]$Text)
    if ($null -eq $Text) { return "" }
    $safe = $Text -replace '(https://)([^/\s:@]+):([^@/\s]+)@', '$1***:***@'
    $safe = $safe -replace '(https://)([^@/\s]+)@', '$1***@'
    $safe = $safe -replace '(token|password|senha|secret|api[_-]?key)(["'']?\s*[:=]\s*["'']?)[^"''\s]+', '$1$2***'
    return $safe
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
        [int]$TimeoutSec = 180,
        [switch]$Quiet
    )

    $argLine = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join " "
    Write-Log ("RUN: {0} {1}" -f $FilePath, (Protect-SensitiveText $argLine))
    Write-Log ("CWD: {0}" -f $WorkingDirectory)

    $outFile = Join-Path $env:TEMP ("aura_evs_{0}_{1}.out" -f $PID, ([guid]::NewGuid().ToString("N")))
    $errFile = Join-Path $env:TEMP ("aura_evs_{0}_{1}.err" -f $PID, ([guid]::NewGuid().ToString("N")))
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $argLine -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    } catch {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        throw ("Nao foi possivel iniciar {0}: {1}" -f $Name, $_.Exception.Message)
    }

    $startedAt = Get-Date
    $lastBeat = $startedAt
    while (-not $process.WaitForExit(15000)) {
        $elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
        if ($elapsed -ge $TimeoutSec) {
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
            Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
            throw ("{0} excedeu timeout de {1}s" -f $Name, $TimeoutSec)
        }
        if (-not $Quiet -and (((Get-Date) - $lastBeat).TotalSeconds -ge 30)) {
            Write-Host ("      ainda executando {0}... {1}s / {2}s" -f $Name, $elapsed, $TimeoutSec) -ForegroundColor DarkYellow
            Write-Log ("RUN ainda em andamento: {0} ({1}s / {2}s)" -f $Name, $elapsed, $TimeoutSec)
            $lastBeat = Get-Date
        }
    }

    $stdout = if (Test-Path $outFile) { Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue } else { "" }
    $stderr = if (Test-Path $errFile) { Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue } else { "" }
    if ($stdout) { $stdout.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log (Protect-SensitiveText $_) } }
    if ($stderr) { $stderr.TrimEnd() -split "`r?`n" | ForEach-Object { Write-Log ("ERR: " + (Protect-SensitiveText $_)) } }
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue

    $process.Refresh()
    $exitCode = if ($null -eq $process.ExitCode) { 0 } else { [int]$process.ExitCode }
    $combined = "{0}`n{1}" -f [string]$stdout, [string]$stderr
    $isGit = ($FilePath -eq "git") -or ($Name -like "git *")
    $isPythonVersion = ($Name -like '*python*' -or ($Arguments -contains '--version')) -and ($combined -match 'Python \d+\.\d+')
    if ($isGit -and $combined -match 'fatal:|could not read Username|Authentication failed|error: failed to execute prompt') {
        $exitCode = 1
    }
    if (-not $isGit -and -not $isPythonVersion -and $combined -match 'Traceback \(most recent call last\)|RuntimeError:') {
        $exitCode = 1
    }
    if ($isPythonVersion) { $exitCode = 0 }
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
        if ($candidate -like '*\*' -and -not (Test-Path -LiteralPath $candidate)) { continue }
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

function Protect-SensitiveTextForHost {
    param([string]$Text)
    return (Protect-SensitiveText $Text)
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

function Get-GitHubPushAdvice {
    param([string]$Detail)
    $text = [string]$Detail
    if ($text -match 'Password authentication is not supported|Invalid username or token|Authentication failed') {
        return "GitHub recusou o login. No hub_share.env use AURA_HUB_USER e AURA_HUB_PASSWORD=Personal Access Token."
    }
    if ($text -match 'Permission to .* denied|returned error: 403') {
        return "Token sem permissao de gravar em gerenciamento-termico/Dashboards."
    }
    return (Protect-SensitiveText $text)
}

function Test-EntregasGeneratorBusy {
    $hits = @()
    try {
        $hits = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$GeneratorName*" })
    } catch {
        return $false
    }
    return ($hits.Count -gt 0)
}

function Wait-GitIndexFree {
    param([int]$TimeoutSec = 60)
    $lockPath = Join-Path $PublishDir ".git\index.lock"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while (Test-Path -LiteralPath $lockPath) {
        if ((Get-Date) -ge $deadline) { return $false }
        Start-Sleep -Seconds 2
    }
    return $true
}

function Test-GitBusyOrBlocked {
    if (Test-Path -LiteralPath (Join-Path $PublishDir ".git\rebase-merge")) { return "rebase pendente" }
    if (Test-Path -LiteralPath (Join-Path $PublishDir ".git\rebase-apply")) { return "rebase pendente" }
    if (Test-Path -LiteralPath (Join-Path $PublishDir ".git\MERGE_HEAD")) { return "merge pendente" }
    if (-not (Wait-GitIndexFree -TimeoutSec 60)) { return ".git\\index.lock (outro git em execucao)" }
    return ""
}

function Test-Repo {
    if (-not (Test-Path (Join-Path $PublishDir ".git"))) {
        throw "Repositorio Git nao encontrado em $PublishDir"
    }
    Invoke-LoggedProcess -FilePath "git" -Arguments @("rev-parse", "--is-inside-work-tree") -WorkingDirectory $PublishDir -Name "git rev-parse" -TimeoutSec 30 -Quiet
    $current = (& git -C $PublishDir remote get-url origin 2>$null)
    if ($current -and $current -ne $GitHubRepoUrl) {
        Invoke-LoggedProcess -FilePath "git" -Arguments @("remote", "set-url", "origin", $GitHubRepoUrl) -WorkingDirectory $PublishDir -Name "git remote set-url origin" -TimeoutSec 30 -Quiet
    } elseif (-not $current) {
        Invoke-LoggedProcess -FilePath "git" -Arguments @("remote", "add", "origin", $GitHubRepoUrl) -WorkingDirectory $PublishDir -Name "git remote add origin" -TimeoutSec 30 -Quiet
    }
    $remote = (& git -C $PublishDir remote get-url origin 2>$null)
    if (-not $remote -or ($remote -notlike "*$ExpectedRemote*")) {
        throw "Remote origin inesperado: $remote"
    }
}

function Test-FreshRequiredFiles {
    param(
        [string[]]$Paths,
        [datetime]$CycleStart,
        [int]$MinSizeBytes = 1
    )
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw ("arquivo obrigatorio ausente: {0}" -f $path)
        }
        $item = Get-Item -LiteralPath $path
        if ($item.Length -lt $MinSizeBytes) {
            throw ("arquivo invalido: {0} ({1} bytes)" -f $path, $item.Length)
        }
        if ($item.LastWriteTime -lt $CycleStart.AddMinutes(-1)) {
            throw ("arquivo nao foi atualizado neste ciclo: {0}" -f $path)
        }
    }
}

function Publish-EntregasVs {
    $blocked = Test-GitBusyOrBlocked
    if ($blocked) {
        Write-Status "[GIT] Publicacao adiada" $blocked Yellow
        return
    }

    $preStaged = @(& git -C $PublishDir diff --cached --name-only)
    $foreignStaged = @($preStaged | Where-Object { -not (Test-InPublishScope $_) })
    if ($foreignStaged.Count -gt 0) {
        Write-Status "[GIT] Publicacao adiada" "stage de outro atualizador" Yellow
        Write-Log ("[GIT] Stage alheio preservado: " + ($foreignStaged -join ", "))
        return
    }

    $existing = @()
    foreach ($f in $PublishFiles) {
        if (Test-Path -LiteralPath (Join-Path $PublishDir $f)) { $existing += $f }
    }
    if (Test-Path -LiteralPath (Join-Path $PublishDir $PublishFolder)) { $existing += $PublishFolder }
    if ($existing.Count -eq 0) {
        throw "Nenhum arquivo do indicador existe para publicar."
    }

    Invoke-LoggedProcess -FilePath "git" -Arguments (@("add", "--") + $existing) -WorkingDirectory $PublishDir -Name "git add entregas vs" -TimeoutSec 60 -Quiet

    $staged = @(& git -C $PublishDir diff --cached --name-only)
    $bad = @($staged | Where-Object { -not (Test-InPublishScope $_) })
    if ($bad.Count -gt 0) {
        & git -C $PublishDir restore --staged -- @($PublishFiles + $PublishFolder) 2>$null
        throw "Stage contem arquivo fora do escopo: $($bad -join ', ')"
    }

    $envStaged = @($staged | Where-Object {
        $name = Split-Path $_ -Leaf
        $name -eq ".env" -or $name -like ".env.*" -or $name -like "*.env" -or $name -eq "hub_share.env"
    })
    if ($envStaged.Count -gt 0) {
        & git -C $PublishDir restore --staged -- @($PublishFiles + $PublishFolder) 2>$null
        throw "Arquivo .env nao pode ser publicado: $($envStaged -join ', ')"
    }

    if ($staged.Count -eq 0) {
        Write-Status "[GIT] Nenhuma alteracao para publicar" "OK" Yellow
        return
    }

    $blockedSecretPattern = '\.env|BEGIN PRIVATE KEY|AURA_DB_PASSWORD|AURA_POSTGRES_PASSWORD'
    $assignedSecretPattern = '(password|senha|token|api[_-]?key|secret)\s*["'']?\s*[:=]\s*["''][^"'']{8,}["'']'
    foreach ($file in $staged) {
        $blockedMatches = Select-String -LiteralPath (Join-Path $PublishDir $file) -Pattern $blockedSecretPattern -CaseSensitive:$false -ErrorAction SilentlyContinue
        $assignedMatches = Select-String -LiteralPath (Join-Path $PublishDir $file) -Pattern $assignedSecretPattern -CaseSensitive:$false -ErrorAction SilentlyContinue
        if ($blockedMatches -or $assignedMatches) {
            & git -C $PublishDir restore --staged -- @($PublishFiles + $PublishFolder) 2>$null
            throw "Possivel dado sensivel detectado em $file"
        }
    }

    $blocked = Test-GitBusyOrBlocked
    if ($blocked) {
        & git -C $PublishDir restore --staged -- @($PublishFiles + $PublishFolder) 2>$null
        Write-Status "[GIT] Publicacao adiada" $blocked Yellow
        return
    }

    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Invoke-LoggedProcess -FilePath "git" -Arguments @("commit", "-m", "Atualiza ENTREGAS_VS_SINCRONISMO - $stamp") -WorkingDirectory $PublishDir -Name "git commit" -TimeoutSec 120
    try {
        Invoke-LoggedProcess -FilePath "git" -Arguments @("-c", "credential.helper=", "push", (Get-GitAuthenticatedRemoteUrl), "HEAD:$GitBranch") -WorkingDirectory $PublishDir -Name "git push" -TimeoutSec 180
    } catch {
        throw (Get-GitHubPushAdvice -Detail $_.Exception.Message)
    }
    Write-Status "[GIT] Commit e push" "OK" Green
}

function Run-Check {
    Write-Host "CHECK isolado Entregas x Sincronismo (sem gerar/publicar)."
    $script:PythonExe = Select-Python
    Test-Repo
    $py = Join-Path $PublishDir $GeneratorName
    if (-not (Test-Path -LiteralPath $py)) { throw "Gerador ausente: $py" }
    $py48 = Join-Path $PublishDir $Sync48Generator
    if (-not (Test-Path -LiteralPath $py48)) { throw "Gerador ausente: $py48" }
    Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @("-m", "py_compile", $py, $py48) -WorkingDirectory $PublishDir -Name "py_compile entregas vs" -TimeoutSec 60 -Quiet
    Write-Host "[OK] CHECK concluido." -ForegroundColor Green
}

function Run-Cycle {
    $cycleStart = Get-Date
    Write-Host "============================================================"
    Write-Host " ENTREGAS VS SINCRONISMO - ATUALIZADOR ISOLADO 5 MIN"
    Write-Host "============================================================"
    Write-Host ("Inicio: {0}" -f $cycleStart.ToString("HH:mm:ss"))
    Write-Host ("URL:    {0}" -f $PageUrl)
    Write-Host ""
    Write-Log "============================================================"
    Write-Log "CICLO ISOLADO ENTREGAS VS INICIADO"

    try {
        if (-not $script:PythonExe) { $script:PythonExe = Select-Python }
        Test-Repo

        if (Test-EntregasGeneratorBusy) {
            Write-Status "[1/3] Gerar HTML" "ADIADO - gerador ja em execucao" Yellow
            Write-Log "Outro python ja roda gerar_html_entregas_vs_sincronismo.py (provavel passo [3d] do ciclo de 10 min)."
            return $true
        }

        $htmlPath = Join-Path $PublishDir "ENTREGAS_VS_SINCRONISMO.html"
        Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir $GeneratorName)) -WorkingDirectory $PublishDir -Name $GeneratorName -TimeoutSec $GenerateTimeoutSec
        Test-FreshRequiredFiles -Paths @($htmlPath) -CycleStart $cycleStart -MinSizeBytes 1024
        try {
            Test-FreshRequiredFiles -Paths @((Join-Path $PublishDir "ENTREGAS_VS_SINCRONISMO.csv")) -CycleStart $cycleStart -MinSizeBytes 256
        } catch {
            Write-Log ("AVISO: CSV nao atualizado ({0}); HTML do ciclo sera publicado." -f $_.Exception.Message)
        }
        Write-Status "[1/3] Gerar HTML" "OK" Green

        try {
            Invoke-LoggedProcess -FilePath $script:PythonExe -Arguments @((Join-Path $PublishDir $Sync48Generator)) -WorkingDirectory $PublishDir -Name $Sync48Generator -TimeoutSec 300
            Test-FreshRequiredFiles -Paths @((Join-Path $PublishDir "SINCRONISMO_48H.html")) -CycleStart $cycleStart -MinSizeBytes 8000
            Write-Status "[1b] Sincronismo 48h" "OK" Green
        } catch {
            Write-Status "[1b] Sincronismo 48h" "ERRO (anterior preservado)" Yellow
            Write-Log ("AVISO SYNC48: " + $_.Exception.Message)
        }

        Publish-EntregasVs
        Write-Status "[2/3] Publicar GitHub" "OK" Green
        Write-Status "[3/3] Pagina" $PageUrl Cyan
        Write-Log ("CICLO ISOLADO ENTREGAS VS OK em {0}s" -f [int]((Get-Date) - $cycleStart).TotalSeconds)
        return $true
    } catch {
        Write-Status "CICLO" "ERRO" Red
        Write-Log ("ERRO DETALHE: " + $_.Exception.Message)
        Write-Host (Protect-SensitiveTextForHost $_.Exception.Message) -ForegroundColor Red
        return $false
    }
}

$mutex = New-Object System.Threading.Mutex($false, "Global\AuraEntregasVsSincronismoUpdate5Min")
if (-not $mutex.WaitOne(0)) {
    Write-Host "Ja existe uma instancia do atualizador isolado de Entregas x Sincronismo." -ForegroundColor Yellow
    Write-Host "Feche a janela antiga do ATUALIZAR_ENTREGAS_VS_SINCRONISMO_5_MIN.bat." -ForegroundColor Yellow
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
        Write-Log ("Proxima atualizacao em {0}s ({1})" -f $sleepSeconds, $nextRunAt.ToString("HH:mm:ss"))
        if ($sleepSeconds -gt 0) {
            Start-Sleep -Seconds $sleepSeconds
        }
    } while ($true)
} finally {
    $mutex.ReleaseMutex() | Out-Null
    $mutex.Dispose()
}

