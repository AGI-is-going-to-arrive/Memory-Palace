param(
    [ValidateSet('a', 'b', 'c', 'd', 'A', 'B', 'C', 'D')]
    [string]$Profile = 'b',

    [int]$FrontendPort = 0,

    [int]$BackendPort = 0,

    [switch]$NoAutoPort,

    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'

function Test-PortInUse {
    param([int]$Port)

    if ($Port -lt 1 -or $Port -gt 65535) {
        throw "Invalid port: $Port"
    }

    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop
        return ($listeners.Count -gt 0)
    }
    catch {
        return $false
    }
}

function Resolve-FreePort {
    param(
        [int]$StartPort,
        [int]$MaxScan = 200
    )

    for ($i = 0; $i -le $MaxScan; $i++) {
        $candidate = $StartPort + $i
        if ($candidate -gt 65535) {
            break
        }
        if (-not (Test-PortInUse -Port $candidate)) {
            return $candidate
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

    $newVolume = 'memory_palace_data'
    $projectSlug = (Split-Path -Leaf $projectRoot).ToLower() -replace '[^a-z0-9]', '_'
    $legacyCandidates = @(
        "${projectSlug}_nocturne_data",
        "${projectSlug}_nocturne_memory_data",
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

        if ($legacyVolume.StartsWith("${projectSlug}_")) {
            Write-Host "[compat] detected project-scoped legacy docker volume '$legacyVolume'; reusing it for data continuity."
            return $legacyVolume
        }

        $ownerLabel = docker volume inspect $legacyVolume --format '{{ index .Labels "com.docker.compose.project" }}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $ownerLabel -eq $projectSlug) {
            Write-Host "[compat] detected legacy docker volume '$legacyVolume' owned by compose project '$ownerLabel'; reusing it for data continuity."
            return $legacyVolume
        }

        Write-Host "[compat] found legacy-like volume '$legacyVolume' but skipped auto-reuse (owner label mismatch). Set MEMORY_PALACE_DATA_VOLUME explicitly if this is the expected volume."
    }

    return $newVolume
}

function Invoke-Compose {
    param([string[]]$Args)

    if ($script:UseComposePlugin) {
        & docker compose @Args
    }
    else {
        & docker-compose @Args
    }

    if ($LASTEXITCODE -ne 0) {
        throw "docker compose command failed: $($Args -join ' ')"
    }
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

& (Join-Path $scriptDir 'apply_profile.ps1') -Platform docker -Profile $profileLower -Target (Join-Path $projectRoot '.env.docker')
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Push-Location $projectRoot
try {
    Invoke-Compose @('-f', 'docker-compose.yml', 'down', '--remove-orphans')

    if (-not $NoAutoPort) {
        $resolvedFrontendPort = Resolve-FreePort -StartPort $FrontendPort
        $resolvedBackendPort = Resolve-FreePort -StartPort $BackendPort

        if ($resolvedFrontendPort -ne $FrontendPort) {
            Write-Host "[port-adjust] frontend $FrontendPort is occupied, switched to $resolvedFrontendPort"
        }
        if ($resolvedBackendPort -ne $BackendPort) {
            Write-Host "[port-adjust] backend $BackendPort is occupied, switched to $resolvedBackendPort"
        }
        if ($resolvedFrontendPort -eq $resolvedBackendPort) {
            $resolvedBackendPort = Resolve-FreePort -StartPort ($resolvedBackendPort + 1)
            Write-Host "[port-adjust] backend reassigned to avoid collision with frontend: $resolvedBackendPort"
        }

        $FrontendPort = $resolvedFrontendPort
        $BackendPort = $resolvedBackendPort
    }

    $dataVolume = Resolve-DataVolume
    $env:MEMORY_PALACE_FRONTEND_PORT = "$FrontendPort"
    $env:MEMORY_PALACE_BACKEND_PORT = "$BackendPort"
    $env:MEMORY_PALACE_DATA_VOLUME = "$dataVolume"
    $env:NOCTURNE_FRONTEND_PORT = "$FrontendPort"
    $env:NOCTURNE_BACKEND_PORT = "$BackendPort"
    $env:NOCTURNE_DATA_VOLUME = "$dataVolume"

    if ($NoBuild) {
        Invoke-Compose @('-f', 'docker-compose.yml', 'up', '-d', '--force-recreate', '--remove-orphans')
    }
    else {
        Invoke-Compose @('-f', 'docker-compose.yml', 'up', '-d', '--build', '--force-recreate', '--remove-orphans')
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Memory Palace is starting with docker profile $profileLower."
Write-Host "Frontend: http://localhost:$FrontendPort"
Write-Host "Backend API: http://localhost:$BackendPort"
Write-Host "Health: http://localhost:$BackendPort/health"
