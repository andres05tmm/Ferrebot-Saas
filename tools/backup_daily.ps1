# tools/backup_daily.ps1 — wrapper Windows del backup SEMANAL de producción (Task Scheduler).
#
# Committeable y SIN secretos: las credenciales viven en .env.prod (lo carga tools/_prodenv.py).
# El gate BACKUP_ENABLED lo resuelve Python (settings.backup_enabled); este wrapper SIEMPRE corre y
# delega — si el backup está apagado, Python imprime el aviso y termina con éxito (exit 0).
#
# Alta en Task Scheduler: ver docs/runbook.md ("Backup automático (Windows, semanal/opcional)").
$ErrorActionPreference = "Stop"

# Raíz del repo = carpeta padre de tools/ (donde vive este script). cd ahí para rutas relativas.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# Carpeta y archivo de log (timestamp local). Se crea la carpeta si no existe.
$logDir = Join-Path $repo "backups\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir ("backup_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

function Write-Log($msg) {
    ("{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg) | Tee-Object -FilePath $log -Append
}

Write-Log "=== Backup prod (semanal) iniciando en $repo ==="

# Docker debe estar corriendo: el cliente pg_dump/pg_restore es la imagen postgres:18 (servidor PG 18.x).
# Streams a $null (no se fusionan al pipeline) para no disparar NativeCommandError en PS 5.1.
& docker info 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: Docker no responde (¿Docker Desktop apagado?). Abortando sin backup."
    exit 1
}

# Fija el cliente Docker v18 (servidor PG 18.x). El dump viaja por stdout/stdin (no monta volúmenes).
$env:PG_DUMP    = "docker run --rm postgres:18 pg_dump"
$env:PG_RESTORE = "docker run --rm -i postgres:18 pg_restore"

Write-Log "Ejecutando tools.backup_db --podar 8 ..."
# ErrorActionPreference=Continue alrededor del .exe: en PS 5.1, fusionar stderr de un nativo con Stop
# puede lanzar NativeCommandError espurio aunque el proceso retorne 0.
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& ".\.venv\Scripts\python.exe" -m tools.backup_db --podar 8 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE
$ErrorActionPreference = $prev

Write-Log "=== Backup terminó con código $code ==="
exit $code
