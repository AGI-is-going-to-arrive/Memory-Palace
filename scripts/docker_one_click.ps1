param(
    [ValidateSet('a', 'b', 'c', 'd', 'A', 'B', 'C', 'D')]
    [string]$Profile = 'b',

    [int]$FrontendPort = 0,

    [int]$BackendPort = 0,

    [switch]$NoAutoPort,

    [switch]$NoBuild,

    [switch]$AllowRuntimeEnvInjection
)

$ErrorActionPreference = 'Stop'
$script:PortProbeFallbackWarned = $false
$script:FrontendPortLockDir = $null
$script:BackendPortLockDir = $null
$script:DeploymentLockDir = $null
$script:GeneratedDockerEnvFile = $null
$script:PreviousDockerEnvFile = $null

function Get-DefaultComposeProjectName {
    $projectSlug = (Split-Path -Leaf $projectRoot).ToLower() -replace '[^a-z0-9]+', '-'
    $projectSlug = $projectSlug.Trim('-')
    if ([string]::IsNullOrWhiteSpace($projectSlug)) {
        $projectSlug = 'memory-palace'
    }

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($projectRoot)
    $hashBytes = [System.Security.Cryptography.SHA256]::HashData($bytes)
    $hash = ([System.BitConverter]::ToString($hashBytes)).Replace('-', '').Substring(0, 8).ToLower()
    return "$projectSlug-$hash"
}

function Test-PortInUse {
    param([int]$Port)

    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "Invalid port: $Port"
    }

    $getNetTcpConnection = Get-Command -Name 'Get-NetTCPConnection' -ErrorAction SilentlyContinue
    if ($getNetTcpConnection) {
        try {
            $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
            return ($listeners.Count -gt 0)
        }
        catch {
            if (-not $script:PortProbeFallbackWarned) {
                Write-Warning "Port probe fallback engaged: Get-NetTCPConnection failed unexpectedly; trying ss-based probing before fail-closed fallback. detail=$($_.Exception.Message)"
                $script:PortProbeFallbackWarned = $true
            }
        }
    }

    $ssCommand = Get-Command -Name 'ss' -ErrorAction SilentlyContinue
    if ($ssCommand) {
        $ssOutput = & ss -ltnH "( sport = :$Port )" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (-not [string]::IsNullOrWhiteSpace(($ssOutput | Out-String).Trim()))
        }
        if (-not $script:PortProbeFallbackWarned) {
            Write-Warning "Port probe fallback engaged: ss probing returned exit code $LASTEXITCODE; fail-closed probing is enabled."
            $script:PortProbeFallbackWarned = $true
        }
        return $true
    }

    if (-not $script:PortProbeFallbackWarned) {
        Write-Warning "Port probe fallback engaged: neither Get-NetTCPConnection nor ss is available; fail-closed probing is enabled."
        $script:PortProbeFallbackWarned = $true
    }
    # Fail-closed to avoid selecting potentially occupied ports when probe is unavailable.
    return $true
}

function Try-AcquirePathLock {
    param([string]$TargetPath)

    $lockDir = "${TargetPath}.lockdir"
    $ownerFile = Join-Path $lockDir 'owner_pid'
    $parentDir = Split-Path -Parent $TargetPath
    if (-not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    try {
        New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
        Set-Content -Path $ownerFile -Value "$PID" -NoNewline
        return $lockDir
    }
    catch {
    }

    if (Test-Path $ownerFile) {
        $ownerPid = (Get-Content -Path $ownerFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        $ownerProcess = $null
        if ($ownerPid) {
            $ownerProcess = Get-Process -Id ([int]$ownerPid) -ErrorAction SilentlyContinue
        }
        if (-not $ownerProcess) {
            Remove-Item -Path $lockDir -Recurse -Force -ErrorAction SilentlyContinue
            try {
                New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
                Set-Content -Path $ownerFile -Value "$PID" -NoNewline
                return $lockDir
            }
            catch {
            }
        }
    }

    return $null
}

function Release-PathLock {
    param([string]$LockDir)

    if (-not [string]::IsNullOrWhiteSpace($LockDir)) {
        Remove-Item -Path $LockDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Resolve-FreePort {
    param(
        [int]$StartPort,
        [int]$MaxScan = 200,
        [Nullable[int]]$ExcludePort = $null
    )

    for ($i = 0; $i -le $MaxScan; $i++) {
        $candidate = $StartPort + $i
        if ($candidate -gt 65535) {
            break
        }
        if ($ExcludePort.HasValue -and $candidate -eq $ExcludePort.Value) {
            continue
        }
        if (-not (Test-PortInUse -Port $candidate)) {
            $lockDir = Try-AcquirePathLock -TargetPath (Join-Path ([System.IO.Path]::GetTempPath()) "memory-palace-port-locks/port-$candidate")
            if ($lockDir) {
                if (-not (Test-PortInUse -Port $candidate)) {
                    return @{
                        Port = $candidate
                        LockDir = $lockDir
                    }
                }
                Release-PathLock -LockDir $lockDir
            }
        }
    }

    throw "Failed to find free port near $StartPort"
}

function Assert-ValidPort {
    param(
        [int]$Port,
        [string]$Name
    )

    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "$Name must be in range [1, 65535], got $Port"
    }
}

function Resolve-DataVolume {
    if ($env:MEMORY_PALACE_DATA_VOLUME) {
        return $env:MEMORY_PALACE_DATA_VOLUME
    }
    if ($env:NOCTURNE_DATA_VOLUME) {
        return $env:NOCTURNE_DATA_VOLUME
    }

    $projectName = $composeProjectName
    if ([string]::IsNullOrWhiteSpace($projectName)) {
        $projectName = Get-DefaultComposeProjectName
    }

    $newVolume = "${projectName}_data"
    $legacyCandidates = @(
        'memory_palace_data',
        'nocturne_data',
        'nocturne_memory_data'
    )

    docker volume inspect $newVolume 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return $newVolume
    }

    foreach ($legacyVolume in $legacyCandidates) {
        docker volume inspect $legacyVolume 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        Write-Host "[compat] found legacy shared docker volume '$legacyVolume', but defaulting to isolated volume '$newVolume'. Set MEMORY_PALACE_DATA_VOLUME=$legacyVolume to reuse old data intentionally."
        break
    }

    return $newVolume
}

function Resolve-SnapshotsVolume {
    if ($env:MEMORY_PALACE_SNAPSHOTS_VOLUME) {
        return $env:MEMORY_PALACE_SNAPSHOTS_VOLUME
    }
    if ($env:NOCTURNE_SNAPSHOTS_VOLUME) {
        return $env:NOCTURNE_SNAPSHOTS_VOLUME
    }

    $projectName = $composeProjectName
    if ([string]::IsNullOrWhiteSpace($projectName)) {
        $projectName = Get-DefaultComposeProjectName
    }

    $newVolume = "${projectName}_snapshots"
    docker volume inspect $newVolume 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        return $newVolume
    }

    $legacyCandidates = @(
        'memory_palace_snapshots',
        'nocturne_snapshots'
    )
    foreach ($legacyVolume in $legacyCandidates) {
        docker volume inspect $legacyVolume 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            continue
        }
        Write-Host "[compat] found legacy shared docker volume '$legacyVolume', but defaulting to isolated volume '$newVolume'. Set MEMORY_PALACE_SNAPSHOTS_VOLUME=$legacyVolume to reuse old snapshots intentionally."
        break
    }

    return $newVolume
}

function Get-EnvValueFromFile {
    param(
        [string]$FilePath,
        [string]$Key
    )

    if (-not (Test-Path $FilePath)) {
        return ''
    }

    $escaped = [regex]::Escape($Key)
    $line = Get-Content -Path $FilePath | Where-Object { $_ -match "^${escaped}=" } | Select-Object -Last 1
    if (-not $line) {
        return ''
    }
    return ($line -replace "^${escaped}=", '')
}

function Set-EnvValueInFile {
    param(
        [string]$FilePath,
        [string]$Key,
        [string]$Value
    )

    $lines = @()
    if (Test-Path $FilePath) {
        $lines = Get-Content -Path $FilePath
    }

    $escaped = [regex]::Escape($Key)
    $updated = $false
    $newLines = foreach ($line in $lines) {
        if ($line -match "^${escaped}=") {
            if (-not $updated) {
                $updated = $true
                "$Key=$Value"
            }
        }
        else {
            $line
        }
    }

    if (-not $updated) {
        $newLines += "$Key=$Value"
    }

    Set-Content -Path $FilePath -Value $newLines
}

function Apply-ProfileRuntimeOverrides {
    param(
        [string]$EnvFile,
        [string]$SelectedProfile
    )

    $overrideKeys = @(
        'ROUTER_API_BASE',
        'ROUTER_API_KEY',
        'ROUTER_EMBEDDING_MODEL',
        'RETRIEVAL_EMBEDDING_BACKEND',
        'RETRIEVAL_EMBEDDING_API_BASE',
        'RETRIEVAL_EMBEDDING_API_KEY',
        'RETRIEVAL_EMBEDDING_MODEL',
        'RETRIEVAL_RERANKER_API_BASE',
        'RETRIEVAL_RERANKER_API_KEY',
        'RETRIEVAL_RERANKER_MODEL',
        'WRITE_GUARD_LLM_ENABLED',
        'WRITE_GUARD_LLM_API_BASE',
        'WRITE_GUARD_LLM_API_KEY',
        'WRITE_GUARD_LLM_MODEL',
        'COMPACT_GIST_LLM_ENABLED',
        'COMPACT_GIST_LLM_API_BASE',
        'COMPACT_GIST_LLM_API_KEY',
        'COMPACT_GIST_LLM_MODEL',
        'INTENT_LLM_ENABLED',
        'INTENT_LLM_API_BASE',
        'INTENT_LLM_API_KEY',
        'INTENT_LLM_MODEL',
        'MCP_API_KEY',
        'MCP_API_KEY_ALLOW_INSECURE_LOCAL'
    )

    foreach ($key in $overrideKeys) {
        $overrideValue = [System.Environment]::GetEnvironmentVariable($key)
        if (-not [string]::IsNullOrWhiteSpace($overrideValue)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key $key -Value $overrideValue
            Write-Host "[override] $key applied to $EnvFile"
        }
    }

    if ($SelectedProfile -in @('c', 'd')) {
        Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_EMBEDDING_BACKEND' -Value 'api'
        Write-Host "[override] RETRIEVAL_EMBEDDING_BACKEND=api forced for local profile $SelectedProfile runtime injection."

        $routerApiBase = [System.Environment]::GetEnvironmentVariable('ROUTER_API_BASE')
        $routerApiKey = [System.Environment]::GetEnvironmentVariable('ROUTER_API_KEY')
        $routerEmbeddingModel = [System.Environment]::GetEnvironmentVariable('ROUTER_EMBEDDING_MODEL')
        $embeddingApiBase = [System.Environment]::GetEnvironmentVariable('RETRIEVAL_EMBEDDING_API_BASE')
        $embeddingApiKey = [System.Environment]::GetEnvironmentVariable('RETRIEVAL_EMBEDDING_API_KEY')
        $embeddingModel = [System.Environment]::GetEnvironmentVariable('RETRIEVAL_EMBEDDING_MODEL')
        $rerankerApiBase = [System.Environment]::GetEnvironmentVariable('RETRIEVAL_RERANKER_API_BASE')
        $rerankerApiKey = [System.Environment]::GetEnvironmentVariable('RETRIEVAL_RERANKER_API_KEY')

        if ([string]::IsNullOrWhiteSpace($embeddingApiBase) -and -not [string]::IsNullOrWhiteSpace($routerApiBase)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_EMBEDDING_API_BASE' -Value $routerApiBase
            Write-Host "[override] RETRIEVAL_EMBEDDING_API_BASE copied from ROUTER_API_BASE for local profile $SelectedProfile runtime injection."
        }
        if ([string]::IsNullOrWhiteSpace($embeddingApiKey) -and -not [string]::IsNullOrWhiteSpace($routerApiKey)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_EMBEDDING_API_KEY' -Value $routerApiKey
            Write-Host "[override] RETRIEVAL_EMBEDDING_API_KEY copied from ROUTER_API_KEY for local profile $SelectedProfile runtime injection."
        }
        if ([string]::IsNullOrWhiteSpace($embeddingModel) -and -not [string]::IsNullOrWhiteSpace($routerEmbeddingModel)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_EMBEDDING_MODEL' -Value $routerEmbeddingModel
            Write-Host "[override] RETRIEVAL_EMBEDDING_MODEL copied from ROUTER_EMBEDDING_MODEL for local profile $SelectedProfile runtime injection."
        }
        if ([string]::IsNullOrWhiteSpace($rerankerApiBase) -and -not [string]::IsNullOrWhiteSpace($routerApiBase)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_RERANKER_API_BASE' -Value $routerApiBase
            Write-Host "[override] RETRIEVAL_RERANKER_API_BASE copied from ROUTER_API_BASE for local profile $SelectedProfile runtime injection."
        }
        if ([string]::IsNullOrWhiteSpace($rerankerApiKey) -and -not [string]::IsNullOrWhiteSpace($routerApiKey)) {
            Set-EnvValueInFile -FilePath $EnvFile -Key 'RETRIEVAL_RERANKER_API_KEY' -Value $routerApiKey
            Write-Host "[override] RETRIEVAL_RERANKER_API_KEY copied from ROUTER_API_KEY for local profile $SelectedProfile runtime injection."
        }
    }
}

function Test-TruthyValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }
    $normalized = $Value.Trim().ToLower()
    return @('1', 'true', 'yes', 'on', 'enabled') -contains $normalized
}

function Test-UnresolvedPlaceholder {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    return (
        $Value.Contains('replace-with-your-key') -or
        $Value.Contains('<your-router-host>') -or
        $Value.Contains('host.docker.internal:PORT') -or
        ($Value -match ':PORT($|/)')
    )
}

function Assert-ProfileExternalSettingsReady {
    param(
        [string]$EnvFile,
        [string]$SelectedProfile
    )

    if ($SelectedProfile -notin @('c', 'd')) {
        return
    }

    $embeddingBackend = (Get-EnvValueFromFile -FilePath $EnvFile -Key 'RETRIEVAL_EMBEDDING_BACKEND').ToLower()
    $rerankerEnabled = Get-EnvValueFromFile -FilePath $EnvFile -Key 'RETRIEVAL_RERANKER_ENABLED'
    $requiredKeys = New-Object System.Collections.Generic.List[string]

    switch ($embeddingBackend) {
        'router' {
            $requiredKeys.Add('ROUTER_API_BASE')
            $requiredKeys.Add('ROUTER_API_KEY')
        }
        'api' {
            $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_BASE')
            $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_KEY')
        }
        'openai' {
            $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_BASE')
            $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_KEY')
        }
        'hash' { }
        'none' { }
        default {
            if (-not [string]::IsNullOrWhiteSpace($embeddingBackend)) {
                $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_BASE')
                $requiredKeys.Add('RETRIEVAL_EMBEDDING_API_KEY')
            }
        }
    }

    if (Test-TruthyValue -Value $rerankerEnabled) {
        $requiredKeys.Add('RETRIEVAL_RERANKER_API_BASE')
        $requiredKeys.Add('RETRIEVAL_RERANKER_API_KEY')
    }

    $hasIssue = $false
    foreach ($key in $requiredKeys) {
        $value = Get-EnvValueFromFile -FilePath $EnvFile -Key $key
        if ([string]::IsNullOrWhiteSpace($value)) {
            Write-Error "[profile-check] Missing required value for $key ($SelectedProfile)"
            $hasIssue = $true
            continue
        }
        if (Test-UnresolvedPlaceholder -Value $value) {
            Write-Error "[profile-check] Unresolved placeholder for $key ($SelectedProfile): $value"
            $hasIssue = $true
        }
    }

    if ($hasIssue) {
        throw "Profile $SelectedProfile has unresolved external settings in $EnvFile"
    }
}

function Invoke-Compose {
    param(
        [string[]]$ComposeArgs,
        [string]$ComposeProjectName = '',
        [string]$EnvFile = ''
    )

    $composeOutput = @()
    $previousComposeProjectName = $env:COMPOSE_PROJECT_NAME
    $effectiveComposeArgs = @()
    try {
        if (-not [string]::IsNullOrWhiteSpace($ComposeProjectName)) {
            $env:COMPOSE_PROJECT_NAME = $ComposeProjectName
        }
        if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
            $effectiveComposeArgs += @('--env-file', $EnvFile)
        }
        $effectiveComposeArgs += $ComposeArgs

        if ($script:UseComposePlugin) {
            $composeOutput = & docker compose @effectiveComposeArgs 2>&1
        }
        else {
            $composeOutput = & docker-compose @effectiveComposeArgs 2>&1
        }
    }
    finally {
        if ([string]::IsNullOrWhiteSpace($previousComposeProjectName)) {
            Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
        }
        else {
            $env:COMPOSE_PROJECT_NAME = $previousComposeProjectName
        }
    }

    if ($composeOutput.Count -gt 0) {
        $composeOutput | ForEach-Object { Write-Output $_ }
    }

    if ($LASTEXITCODE -ne 0) {
        $detail = ($composeOutput | Out-String).Trim()
        throw "docker compose command failed: $($effectiveComposeArgs -join ' ')`n$detail"
    }
}

function Test-ComposeRetryableError {
    param([string]$Message)

    if ([string]::IsNullOrWhiteSpace($Message)) {
        return $false
    }

    $patterns = @(
        'No such container',
        'dependency failed to start',
        'toomanyrequests',
        'TLS handshake timeout',
        'connection reset by peer',
        'i/o timeout',
        'context canceled',
        'EOF'
    )

    foreach ($pattern in $patterns) {
        if ($Message -like "*$pattern*") {
            return $true
        }
    }

    return $false
}

function Invoke-ComposeWithRetry {
    param(
        [string[]]$ComposeArgs,
        [string]$ComposeProjectName = '',
        [int]$MaxAttempts = 3,
        [string]$EnvFile = ''
    )

    $attempt = 0
    while ($attempt -lt $MaxAttempts) {
        $attempt += 1
        try {
            Invoke-Compose -ComposeArgs $ComposeArgs -ComposeProjectName $ComposeProjectName -EnvFile $EnvFile
            return
        }
        catch {
            $detail = $_.Exception.Message
            $retryable = Test-ComposeRetryableError -Message $detail
            if ($attempt -ge $MaxAttempts -or -not $retryable) {
                throw
            }

            $sleepSeconds = 2 * $attempt
            Write-Warning "[compose-retry] transient compose up failure ($attempt/$MaxAttempts), retrying in ${sleepSeconds}s."
            Start-Sleep -Seconds $sleepSeconds
            try {
                Invoke-Compose -ComposeArgs @('-f', 'docker-compose.yml', 'down', '--remove-orphans') -ComposeProjectName $ComposeProjectName -EnvFile $EnvFile
            }
            catch {
                # Keep retry path best-effort; next attempt will surface a hard failure.
            }
        }
    }
}

function Get-HttpStatusCode {
    param([string]$Url)

    try {
        $code = & curl.exe -sS -o NUL -w '%{http_code}' $Url 2>$null
        if ([string]::IsNullOrWhiteSpace($code)) {
            return 0
        }
        return [int]$code
    }
    catch {
        return 0
    }
}

function Wait-DeploymentReady {
    param(
        [int]$FrontendPort,
        [int]$BackendPort,
        [int]$Attempts = 30,
        [int]$SleepSeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $backendCode = Get-HttpStatusCode -Url "http://127.0.0.1:$BackendPort/health"
        $frontendCode = Get-HttpStatusCode -Url "http://127.0.0.1:$FrontendPort/"
        $sseCode = Get-HttpStatusCode -Url "http://127.0.0.1:$FrontendPort/sse"
        if ($backendCode -eq 200 -and $frontendCode -eq 200 -and @('200', '401') -contains "$sseCode") {
            return $true
        }
        Start-Sleep -Seconds $SleepSeconds
    }

    return $false
}

function Test-ComposeProjectHasAnyContainer {
    param(
        [string]$ComposeProjectName,
        [string]$Service
    )

    $containers = docker ps -a `
        --filter "label=com.docker.compose.project=$ComposeProjectName" `
        --filter "label=com.docker.compose.service=$Service" `
        --format '{{.Names}}' 2>$null | Select-Object -First 1

    return (-not [string]::IsNullOrWhiteSpace(($containers | Out-String).Trim()))
}

function Get-ComposePublishedPort {
    param(
        [string]$ComposeProjectName,
        [string]$EnvFile,
        [string]$Service,
        [int]$TargetPort,
        [int]$FallbackPort,
        [int]$Attempts = 10
    )

    $previousComposeProjectName = $env:COMPOSE_PROJECT_NAME
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $portsOutput = docker ps `
                --filter "label=com.docker.compose.project=$ComposeProjectName" `
                --filter "label=com.docker.compose.service=$Service" `
                --format '{{.Ports}}' 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0 -and $portsOutput) {
                $portsLine = $portsOutput.ToString().Trim()
                if ($portsLine -match ":(\d+)->$TargetPort/tcp") {
                    return [int]$Matches[1]
                }
            }

            $containerName = docker ps `
                --filter "label=com.docker.compose.project=$ComposeProjectName" `
                --filter "label=com.docker.compose.service=$Service" `
                --format '{{.Names}}' 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0 -and $containerName) {
                $mappedPort = docker port $containerName $TargetPort 2>$null | Select-Object -First 1
                if ($LASTEXITCODE -eq 0 -and $mappedPort) {
                    $line = $mappedPort.ToString().Trim()
                    if ($line -match ':(\d+)\s*$') {
                        return [int]$Matches[1]
                    }
                }
            }

            if (-not [string]::IsNullOrWhiteSpace($ComposeProjectName)) {
                $env:COMPOSE_PROJECT_NAME = $ComposeProjectName
            }

            $portArgs = @()
            if (-not [string]::IsNullOrWhiteSpace($EnvFile)) {
                $portArgs += @('--env-file', $EnvFile)
            }
            $portArgs += @('-f', 'docker-compose.yml', 'port', $Service, "$TargetPort")

            if ($script:UseComposePlugin) {
                $output = & docker compose @portArgs 2>$null
            }
            else {
                $output = & docker-compose @portArgs 2>$null
            }

            if ($LASTEXITCODE -eq 0 -and $output) {
                $line = ($output | Select-Object -First 1).ToString().Trim()
                if ($line -match ':(\d+)\s*$') {
                    return [int]$Matches[1]
                }
            }
        }
        catch {
            # Fall back to the planned port when compose cannot report a binding.
        }
        finally {
            if ([string]::IsNullOrWhiteSpace($previousComposeProjectName)) {
                Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
            }
            else {
                $env:COMPOSE_PROJECT_NAME = $previousComposeProjectName
            }
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Seconds 1
        }
    }

    return $FallbackPort
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$profileLower = $Profile.ToLower()

if (-not $PSBoundParameters.ContainsKey('FrontendPort')) {
    if ($env:MEMORY_PALACE_FRONTEND_PORT) {
        $FrontendPort = [int]$env:MEMORY_PALACE_FRONTEND_PORT
    }
    elseif ($env:NOCTURNE_FRONTEND_PORT) {
        $FrontendPort = [int]$env:NOCTURNE_FRONTEND_PORT
    }
    else {
        $FrontendPort = 3000
    }
}

if (-not $PSBoundParameters.ContainsKey('BackendPort')) {
    if ($env:MEMORY_PALACE_BACKEND_PORT) {
        $BackendPort = [int]$env:MEMORY_PALACE_BACKEND_PORT
    }
    elseif ($env:NOCTURNE_BACKEND_PORT) {
        $BackendPort = [int]$env:NOCTURNE_BACKEND_PORT
    }
    else {
        $BackendPort = 18000
    }
}

Assert-ValidPort -Port $FrontendPort -Name 'FrontendPort'
Assert-ValidPort -Port $BackendPort -Name 'BackendPort'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker is not installed or not in PATH"
    exit 1
}

$script:UseComposePlugin = $false
try {
    docker compose version | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $script:UseComposePlugin = $true
    }
}
catch {
    $script:UseComposePlugin = $false
}

if (-not $script:UseComposePlugin -and -not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Write-Error "Neither 'docker compose' nor 'docker-compose' is available"
    exit 1
}

$script:DeploymentLockDir = Try-AcquirePathLock -TargetPath (Join-Path ([System.IO.Path]::GetTempPath()) "memory-palace-deploy-locks/$(Get-DefaultComposeProjectName)")
if (-not $script:DeploymentLockDir) {
    throw "[deploy-lock] another docker_one_click deployment is already running for this checkout; wait for it to finish before retrying."
}

$script:PreviousDockerEnvFile = [System.Environment]::GetEnvironmentVariable('MEMORY_PALACE_DOCKER_ENV_FILE')
$envFile = $script:PreviousDockerEnvFile
if ([string]::IsNullOrWhiteSpace($envFile)) {
    $envFile = Join-Path ([System.IO.Path]::GetTempPath()) ("memory-palace-docker-env-$profileLower-$([System.Guid]::NewGuid().ToString('N')).env")
    $script:GeneratedDockerEnvFile = $envFile
}
$env:MEMORY_PALACE_DOCKER_ENV_FILE = $envFile
Write-Host "[env-file] using $envFile"
& (Join-Path $scriptDir 'apply_profile.ps1') -Platform docker -Profile $profileLower -Target $envFile
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if ($AllowRuntimeEnvInjection.IsPresent) {
    Apply-ProfileRuntimeOverrides -EnvFile $envFile -SelectedProfile $profileLower
}
else {
    Write-Host "[override] runtime env injection disabled by default; pass -AllowRuntimeEnvInjection to opt in."
}
Assert-ProfileExternalSettingsReady -EnvFile $envFile -SelectedProfile $profileLower

Push-Location $projectRoot
try {
    $composeProjectName = [System.Environment]::GetEnvironmentVariable('COMPOSE_PROJECT_NAME')
    if ([string]::IsNullOrWhiteSpace($composeProjectName)) {
        $composeProjectName = Get-DefaultComposeProjectName
    }

    if (-not $NoAutoPort) {
        $frontendReservation = Resolve-FreePort -StartPort $FrontendPort
        $script:FrontendPortLockDir = $frontendReservation.LockDir
        $resolvedFrontendPort = [int]$frontendReservation.Port
        $backendReservation = Resolve-FreePort -StartPort $BackendPort -ExcludePort $resolvedFrontendPort
        $script:BackendPortLockDir = $backendReservation.LockDir
        $resolvedBackendPort = [int]$backendReservation.Port

        if ($resolvedFrontendPort -ne $FrontendPort) {
            Write-Host "[port-adjust] frontend $FrontendPort is occupied, switched to $resolvedFrontendPort"
        }
        if ($resolvedBackendPort -ne $BackendPort) {
            Write-Host "[port-adjust] backend $BackendPort is occupied, switched to $resolvedBackendPort"
        }

        $FrontendPort = $resolvedFrontendPort
        $BackendPort = $resolvedBackendPort
    }

    $dataVolume = Resolve-DataVolume
    $snapshotsVolume = Resolve-SnapshotsVolume
    Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_FRONTEND_PORT' -Value "$FrontendPort"
    Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_BACKEND_PORT' -Value "$BackendPort"
    Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_DATA_VOLUME' -Value "$dataVolume"
    Set-EnvValueInFile -FilePath $envFile -Key 'MEMORY_PALACE_SNAPSHOTS_VOLUME' -Value "$snapshotsVolume"
    Set-EnvValueInFile -FilePath $envFile -Key 'NOCTURNE_FRONTEND_PORT' -Value "$FrontendPort"
    Set-EnvValueInFile -FilePath $envFile -Key 'NOCTURNE_BACKEND_PORT' -Value "$BackendPort"
    Set-EnvValueInFile -FilePath $envFile -Key 'NOCTURNE_DATA_VOLUME' -Value "$dataVolume"
    Set-EnvValueInFile -FilePath $envFile -Key 'NOCTURNE_SNAPSHOTS_VOLUME' -Value "$snapshotsVolume"
    $plannedFrontendPort = Get-EnvValueFromFile -FilePath $envFile -Key 'MEMORY_PALACE_FRONTEND_PORT'
    $plannedBackendPort = Get-EnvValueFromFile -FilePath $envFile -Key 'MEMORY_PALACE_BACKEND_PORT'
    if ([string]::IsNullOrWhiteSpace($plannedFrontendPort)) {
        $plannedFrontendPort = "$FrontendPort"
    }
    if ([string]::IsNullOrWhiteSpace($plannedBackendPort)) {
        $plannedBackendPort = "$BackendPort"
    }
    $env:MEMORY_PALACE_FRONTEND_PORT = "$FrontendPort"
    $env:MEMORY_PALACE_BACKEND_PORT = "$BackendPort"
    $env:MEMORY_PALACE_DATA_VOLUME = "$dataVolume"
    $env:MEMORY_PALACE_SNAPSHOTS_VOLUME = "$snapshotsVolume"
    $env:NOCTURNE_FRONTEND_PORT = "$FrontendPort"
    $env:NOCTURNE_BACKEND_PORT = "$BackendPort"
    $env:NOCTURNE_DATA_VOLUME = "$dataVolume"
    $env:NOCTURNE_SNAPSHOTS_VOLUME = "$snapshotsVolume"

    try {
        Invoke-Compose -ComposeArgs @('-f', 'docker-compose.yml', 'down', '--remove-orphans') -ComposeProjectName $composeProjectName -EnvFile $envFile
    }
    catch {
        throw "[compose-down] pre-cleanup failed; aborting to match fail-closed deployment behavior. detail=$($_.Exception.Message)"
    }

    $composeUpArgs = @('-f', 'docker-compose.yml', 'up', '-d', '--wait', '--wait-timeout', '120', '--force-recreate', '--remove-orphans')
    if (-not $NoBuild) {
        $composeUpArgs = @('-f', 'docker-compose.yml', 'up', '-d', '--build', '--wait', '--wait-timeout', '120', '--force-recreate', '--remove-orphans')
    }
    try {
        Invoke-ComposeWithRetry -ComposeArgs $composeUpArgs -ComposeProjectName $composeProjectName -MaxAttempts 3 -EnvFile $envFile
    }
    catch {
        $hasBackendContainer = Test-ComposeProjectHasAnyContainer -ComposeProjectName $composeProjectName -Service 'backend'
        $hasSseContainer = Test-ComposeProjectHasAnyContainer -ComposeProjectName $composeProjectName -Service 'sse'
        $hasFrontendContainer = Test-ComposeProjectHasAnyContainer -ComposeProjectName $composeProjectName -Service 'frontend'
        if (-not ($hasBackendContainer -or $hasSseContainer -or $hasFrontendContainer)) {
            throw "[compose-up] docker compose failed before creating any service container; skipping readiness probe. detail=$($_.Exception.Message)"
        }
        Write-Warning "[compose-up] docker compose returned non-zero; probing backend/frontend/sse readiness..."
        $probeFrontendPort = Get-ComposePublishedPort -ComposeProjectName $composeProjectName -EnvFile $envFile -Service 'frontend' -TargetPort 8080 -FallbackPort ([int]$plannedFrontendPort)
        $probeBackendPort = Get-ComposePublishedPort -ComposeProjectName $composeProjectName -EnvFile $envFile -Service 'backend' -TargetPort 8000 -FallbackPort ([int]$plannedBackendPort)
        if (-not (Wait-DeploymentReady -FrontendPort $probeFrontendPort -BackendPort $probeBackendPort)) {
            throw
        }
        Write-Warning "[compose-up] services became ready after compose reported failure; continuing."
    }

    $reportedFrontendPort = Get-ComposePublishedPort -ComposeProjectName $composeProjectName -EnvFile $envFile -Service 'frontend' -TargetPort 8080 -FallbackPort ([int]$plannedFrontendPort)
    $reportedBackendPort = Get-ComposePublishedPort -ComposeProjectName $composeProjectName -EnvFile $envFile -Service 'backend' -TargetPort 8000 -FallbackPort ([int]$plannedBackendPort)
}
finally {
    Release-PathLock -LockDir $script:DeploymentLockDir
    Release-PathLock -LockDir $script:FrontendPortLockDir
    Release-PathLock -LockDir $script:BackendPortLockDir
    if ([string]::IsNullOrWhiteSpace($script:PreviousDockerEnvFile)) {
        Remove-Item Env:MEMORY_PALACE_DOCKER_ENV_FILE -ErrorAction SilentlyContinue
    }
    else {
        $env:MEMORY_PALACE_DOCKER_ENV_FILE = $script:PreviousDockerEnvFile
    }
    if (-not [string]::IsNullOrWhiteSpace($script:GeneratedDockerEnvFile) -and (Test-Path $script:GeneratedDockerEnvFile)) {
        Remove-Item $script:GeneratedDockerEnvFile -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}

Write-Host ""
Write-Host "Memory Palace is starting with docker profile $profileLower."
Write-Host "Frontend: http://localhost:$reportedFrontendPort"
Write-Host "Backend API: http://localhost:$reportedBackendPort"
Write-Host "SSE Endpoint: http://localhost:$reportedFrontendPort/sse"
Write-Host "Health: http://localhost:$reportedBackendPort/health"
Write-Host "Compose project: $composeProjectName"
