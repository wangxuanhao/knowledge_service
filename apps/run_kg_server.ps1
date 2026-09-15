# run_kg_server.ps1 - start the knowledge-graph web server (hidden window + logs)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path   # apps/
$repo = Split-Path -Parent $root
$web  = Join-Path $repo "data\kg_web"                     # data/kg_web
if (-not (Test-Path $web)) { New-Item -ItemType Directory -Force -Path $web | Out-Null }
Set-Location $root
$py = "D:\work\wangxuanhao\conda\envs\llm_model\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "python not found: $py"
    exit 1
}

# stop any running instance first
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*16_kg_web_server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

$out = Join-Path $web "_srv.log"
$err = Join-Path $web "_srv.err"
$p = Start-Process -FilePath $py -ArgumentList "-u", "16_kg_web_server.py" `
    -WorkingDirectory $root `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -WindowStyle Hidden -PassThru

Write-Host ("Server PID = " + $p.Id + "  waiting for readiness ...")

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-RestMethod "http://127.0.0.1:8000/api/projects" -TimeoutSec 2
        $ready = $true
        break
    } catch { }
}
if ($ready) {
    Write-Host "Ready: http://127.0.0.1:8000"
    Start-Process "http://127.0.0.1:8000"
} else {
    Write-Host "NOT ready in 60s. Check logs:"
    Write-Host "  $out"
    Write-Host "  $err"
}
