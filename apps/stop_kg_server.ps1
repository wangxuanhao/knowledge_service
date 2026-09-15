# stop_kg_server.ps1 - stop the running knowledge-graph web server
$found = $false
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*16_kg_web_server.py*' } |
    ForEach-Object {
        $found = $true
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("Stopped PID " + $_.ProcessId)
    }
if (-not $found) {
    Write-Host "No running server found."
}
