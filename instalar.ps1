# ShortsFactory - instalacion con un solo comando (Windows)
# Uso, en PowerShell:
#   irm https://raw.githubusercontent.com/Sigma01-Gang/YoutubeLameBicho/refs/heads/claude/relaxed-meitner-ui2vo2/instalar.ps1 | iex
# Descarga INSTALAR.bat y lo ejecuta. No necesita permisos de administrador.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$url = "https://raw.githubusercontent.com/Sigma01-Gang/YoutubeLameBicho/refs/heads/claude/relaxed-meitner-ui2vo2/INSTALAR.bat"
$dst = Join-Path $env:TEMP "INSTALAR_ShortsFactory.bat"
Write-Host "Descargando el instalador de ShortsFactory..."
Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dst
Write-Host "Ejecutando el instalador (se abrira una ventana nueva)..."
Start-Process -FilePath $dst -Wait
