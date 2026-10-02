# v0.38.7 ops fix: rebuild scheduled task DailyReview-OpenStrategy
# Why: 9/22-9/23 the 09:25 task failed to start (0x800710E0, interactive-session
#      + battery-mode denied). Fix: keep Mon-Fri 09:25 trigger, ADD logon trigger
#      (covers missed runs after boot/login; push_state dedupes), allow battery,
#      enable StartWhenAvailable. Idempotent: unregister then register.
$ErrorActionPreference = 'Stop'
$tn = 'DailyReview-开盘策略'
$user = "$env:USERDOMAIN\$env:USERNAME"
Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At '09:25'
$t2 = New-ScheduledTaskTrigger -AtLogOn -User $user
$action = New-ScheduledTaskAction -Execute 'cmd' -Argument '/c ""E:\workspace\AI应用工具\每日复盘\tools\scheduled_push.bat" open"'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 60)
$settings.DisallowStartIfOnBatteries = $false
$settings.StopIfGoingOnBatteries = $false
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $tn -Action $action -Trigger @($t1, $t2) -Settings $settings -Principal $principal
Write-Output ("OK rebuilt task " + $tn)