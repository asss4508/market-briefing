param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'MarketBriefingScheduler'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$log = Join-Path $stateDir 'dispatch.log'
function Write-Log([string]$message) {
    $line = '{0} {1}' -f [DateTimeOffset]::Now.ToString('o'), $message
    Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    Write-Output $line
}
try {
    $gh = (Get-Command gh.exe -ErrorAction Stop).Source
    $repo = 'asss4508/market-briefing'
    $runsJson = & $gh run list -R $repo --workflow market_report.yml --limit 30 --json status,databaseId 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Could not read GitHub workflow runs' }
    $runs = ($runsJson -join "`n") | ConvertFrom-Json
    $active = @($runs | Where-Object { $_.status -ne 'completed' })
    if ($active.Count -gt 0) {
        Write-Log ('Report workflow already active: ' + ($active.databaseId -join ', '))
        exit 0
    }
    if ($CheckOnly) {
        Write-Log 'Check passed: GitHub login and workflow accessible; no dispatch requested.'
        exit 0
    }
    & $gh workflow run market_report.yml -R $repo --ref master
    if ($LASTEXITCODE -ne 0) { throw 'GitHub rejected workflow dispatch' }
    Write-Log 'Report workflow dispatch accepted.'
} catch {
    Write-Log ('ERROR: ' + $_.Exception.Message)
    exit 1
}
