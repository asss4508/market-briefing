$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'MarketBriefingScheduler'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$script = Join-Path $stateDir 'dispatch_report.ps1'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'dispatch_report.ps1') -Destination $script -Force
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script
$action = New-ScheduledTaskAction -Execute (Join-Path $PSHOME 'powershell.exe') -Argument $arguments
$kstNow = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([datetime]::UtcNow, 'Korea Standard Time')
$triggers = foreach ($time in @('05:45', '06:05', '06:25', '17:45', '18:05', '18:25')) {
    $kst = [datetime]::SpecifyKind([datetime]::Parse($kstNow.ToString('yyyy-MM-dd') + ' ' + $time), [DateTimeKind]::Unspecified)
    $local = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId($kst, 'Korea Standard Time', [TimeZoneInfo]::Local.Id)
    New-ScheduledTaskTrigger -Daily -At $local
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'MarketBriefing-ReportBackup' -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'Backup GitHub dispatch for 06:00 and 18:00 KST market reports; requires logged-in user and gh authentication.' -Force
