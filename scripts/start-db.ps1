# Start the project's local PostgreSQL 17 (port 5433, UTC).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$pgBin = Join-Path $root '.local/pgsql/bin'
$pgData = Join-Path $root '.local/pgdata'
& "$pgBin/pg_ctl.exe" -D $pgData status *> $null
if ($LASTEXITCODE -ne 0) {
    & "$pgBin/pg_ctl.exe" -D $pgData -l (Join-Path $root '.local/postgres.log') -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
}
