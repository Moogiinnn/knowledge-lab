$root = Split-Path $PSScriptRoot -Parent
& (Join-Path $root '.local/pgsql/bin/pg_ctl.exe') -D (Join-Path $root '.local/pgdata') -m fast stop
