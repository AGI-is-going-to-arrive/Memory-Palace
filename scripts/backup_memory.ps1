param(
    [string]$EnvFile = '',
    [string]$OutputDir = ''
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $EnvFile = Join-Path $projectRoot '.env'
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $projectRoot 'backups'
}

if (-not (Test-Path $EnvFile)) {
    Write-Error "Environment file not found: $EnvFile"
    exit 1
}

function Normalize-EnvValue {
    param([string]$Value)

    $normalized = $Value.Trim()
    if ($normalized.Length -ge 2) {
        $startsWithSingle = $normalized.StartsWith("'")
        $endsWithSingle = $normalized.EndsWith("'")
        $startsWithDouble = $normalized.StartsWith('"')
        $endsWithDouble = $normalized.EndsWith('"')
        if (($startsWithSingle -and $endsWithSingle) -or ($startsWithDouble -and $endsWithDouble)) {
            $normalized = $normalized.Substring(1, $normalized.Length - 2)
        }
    }
    return $normalized
}

function Read-DatabaseUrlFromEnvFile {
    param([string]$Path)

    $databaseUrl = $null
    Get-Content $Path | ForEach-Object {
        if ($_ -match "^\s*([^#=]+)\s*=\s*(.*)\s*$") {
            $key = $matches[1].Trim()
            if ($key -ne 'DATABASE_URL') {
                return
            }
            $databaseUrl = Normalize-EnvValue -Value $matches[2]
        }
    }
    return $databaseUrl
}

function Resolve-SqlitePathFromDatabaseUrl {
    param([string]$DatabaseUrl)

    $prefix = 'sqlite+aiosqlite:///'
    if (-not $DatabaseUrl.StartsWith($prefix)) {
        throw "DATABASE_URL must start with '$prefix'"
    }

    $rawPath = $DatabaseUrl.Substring($prefix.Length)
    if ([string]::IsNullOrWhiteSpace($rawPath)) {
        throw "DATABASE_URL does not contain a valid sqlite file path"
    }

    if ([System.IO.Path]::IsPathRooted($rawPath)) {
        return $rawPath
    }

    return (Join-Path $projectRoot $rawPath)
}

$databaseUrl = Read-DatabaseUrlFromEnvFile -Path $EnvFile
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    Write-Error "DATABASE_URL is missing in $EnvFile"
    exit 1
}

try {
    $sqlitePath = Resolve-SqlitePathFromDatabaseUrl -DatabaseUrl $databaseUrl
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}

if (-not (Test-Path $sqlitePath)) {
    Write-Error "SQLite database file not found: $sqlitePath"
    exit 1
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$destFile = Join-Path $OutputDir ("memory_palace_backup_{0}.db" -f $timestamp)

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "Python is required for consistent SQLite backup but was not found in PATH."
    exit 1
}

$env:MEMORY_PALACE_BACKUP_SOURCE = $sqlitePath
$env:MEMORY_PALACE_BACKUP_TARGET = $destFile
$backupScript = @'
import os
import sqlite3

source = os.environ["MEMORY_PALACE_BACKUP_SOURCE"]
target = os.environ["MEMORY_PALACE_BACKUP_TARGET"]

with sqlite3.connect(source) as source_conn:
    with sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
'@

& $pythonCmd.Source -c $backupScript
if ($LASTEXITCODE -ne 0) {
    Write-Error "Backup failed for $sqlitePath: sqlite backup command returned non-zero exit code."
    exit 1
}

Write-Host "Backup completed." -ForegroundColor Green
Write-Host "Source: $sqlitePath"
Write-Host "Target: $destFile"
