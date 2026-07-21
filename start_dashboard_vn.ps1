$ErrorActionPreference = "SilentlyContinue"

$port = 8501
$projectDir = "E:\Trade"
$appFile = Join-Path $projectDir "dashboard_vn.py"
$pythonExe = "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
$stdoutLog = Join-Path $projectDir "dashboard_vn_streamlit.out.log"
$stderrLog = Join-Path $projectDir "dashboard_vn_streamlit.err.log"

$existing = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($existing) {
    exit 0
}

Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("-m", "streamlit", "run", $appFile, "--server.port", "$port", "--server.headless", "true") `
    -WorkingDirectory $projectDir `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden
