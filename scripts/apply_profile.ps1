param(
    [ValidateSet('macos', 'windows', 'docker')]
    [string]$Platform = 'windows',

    [ValidateSet('a', 'b', 'c', 'd', 'A', 'B', 'C', 'D')]
    [string]$Profile = 'b',

    [string]$Target = ''
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$profileLower = $Profile.ToLower()

if ([string]::IsNullOrWhiteSpace($Target)) {
    $Target = Join-Path $projectRoot '.env'
}

$baseEnv = Join-Path $projectRoot '.env.example'
$overrideEnv = Join-Path $projectRoot ("deploy/profiles/{0}/profile-{1}.env" -f $Platform, $profileLower)

if (-not (Test-Path $baseEnv)) {
    Write-Error "Missing base env template: $baseEnv"
    exit 1
}

if (-not (Test-Path $overrideEnv)) {
    Write-Error "Missing profile template: $overrideEnv"
    exit 1
}

Copy-Item -Path $baseEnv -Destination $Target -Force
Add-Content -Path $Target -Value ""
Add-Content -Path $Target -Value "# -----------------------------------------------------------------------------"
Add-Content -Path $Target -Value "# Appended profile overrides ($Platform/profile-$profileLower)"
Add-Content -Path $Target -Value "# -----------------------------------------------------------------------------"
Get-Content -Path $overrideEnv | Add-Content -Path $Target

if ($Platform -eq 'macos') {
    $placeholder = 'DATABASE_URL=sqlite+aiosqlite:////Users/<your-user>/memory_palace/agent_memory.db'
    if (Select-String -Path $Target -Pattern [regex]::Escape($placeholder) -Quiet) {
        $dbPath = (Join-Path $projectRoot 'demo.db') -replace '\\', '/'
        $dbUrl = 'DATABASE_URL=sqlite+aiosqlite:////' + $dbPath.TrimStart('/')
        Add-Content -Path $Target -Value $dbUrl
        Write-Host "[auto-fill] DATABASE_URL set to $dbPath"
    }
}

if ($Platform -eq 'windows') {
    $placeholder = 'DATABASE_URL=sqlite+aiosqlite:///C:/memory_palace/agent_memory.db'
    if (Select-String -Path $Target -Pattern [regex]::Escape($placeholder) -Quiet) {
        $dbPath = (Join-Path $projectRoot 'demo.db') -replace '\\', '/'
        $dbUrl = 'DATABASE_URL=sqlite+aiosqlite:///' + $dbPath
        Add-Content -Path $Target -Value $dbUrl
        Write-Host "[auto-fill] DATABASE_URL set to $dbPath"
    }
}

Write-Host "Generated $Target from $overrideEnv"
