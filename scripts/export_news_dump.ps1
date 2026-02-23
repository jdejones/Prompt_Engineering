param(
    [string]$Schema = "news",
    [string]$OutputDir = "$PSScriptRoot\..\backups",
    [string]$MySqlDumpPath = "mysqldump",
    [string]$DbHost = "127.0.0.1",
    [int]$DbPort = 3306,
    [string]$DbUser = "root",
    [string]$DbPassword = $env:DATABASE_PASSWORD,
    [string]$RemoteUser = "",
    [string]$RemoteHost = "",
    [string]$RemotePath = "/opt/mcp-news/incoming",
    [string]$SshKeyPath = "$HOME\.ssh\id_ed25519"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DbPassword)) {
    throw "DATABASE_PASSWORD is missing. Set it as env var or pass -DbPassword."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sqlFile = Join-Path $OutputDir "${Schema}_${timestamp}.sql"
$gzipFile = "${sqlFile}.gz"

Write-Host "Creating MySQL dump for schema '$Schema'..."
$env:MYSQL_PWD = $DbPassword
try {
    & $MySqlDumpPath `
        "--host=$DbHost" `
        "--port=$DbPort" `
        "--user=$DbUser" `
        "--single-transaction" `
        "--quick" `
        "--set-gtid-purged=OFF" `
        "--databases" $Schema `
        "--result-file=$sqlFile"
    if ($LASTEXITCODE -ne 0) {
        throw "mysqldump failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item Env:MYSQL_PWD -ErrorAction SilentlyContinue
}

Write-Host "Compressing dump file..."
$inputStream = [System.IO.File]::OpenRead($sqlFile)
$outputStream = [System.IO.File]::Create($gzipFile)
try {
    $gzipStream = New-Object System.IO.Compression.GZipStream(
        $outputStream,
        [System.IO.Compression.CompressionLevel]::Optimal
    )
    try {
        $inputStream.CopyTo($gzipStream)
    }
    finally {
        $gzipStream.Dispose()
    }
}
finally {
    $inputStream.Dispose()
    $outputStream.Dispose()
}

Remove-Item -Path $sqlFile -Force
Write-Host "Created archive: $gzipFile"

if (-not [string]::IsNullOrWhiteSpace($RemoteHost) -and -not [string]::IsNullOrWhiteSpace($RemoteUser)) {
    Write-Host "Uploading archive to $RemoteUser@$RemoteHost:$RemotePath ..."
    $scpTarget = "${RemoteUser}@${RemoteHost}:${RemotePath}/"
    & scp -i $SshKeyPath $gzipFile $scpTarget
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed with exit code $LASTEXITCODE"
    }
    Write-Host "Upload complete."
}

Write-Host "Done."
