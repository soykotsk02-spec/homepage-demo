param([ValidateSet('Daily','Demo','Remove')][string]$Mode='Daily',[int]$DelayMinutes=3)
$ErrorActionPreference='Stop'
$taskConfig=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$taskNames=@('TechNewsAgent-Daily','TechNewsAgent-Demo')
if($Mode -eq 'Remove'){
 foreach($taskName in $taskNames){
  if(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false}
 }
 '本程序的两个 Windows 定时任务已移除。'
 exit 0
}
if((Get-TimeZone).BaseUtcOffset -ne [TimeSpan]::FromHours(8)){throw '当前 Windows 时区不是 UTC+8，请先核对北京时间设置。'}
if($DelayMinutes -lt 2 -or $DelayMinutes -gt 60){throw '演示延迟必须为2到60分钟。'}
& $taskConfig.pythonExecutable (Join-Path $PSScriptRoot 'agent.py') check
if($LASTEXITCODE -ne 0){throw '模型或邮箱配置未齐全，尚未注册定时任务。'}
$taskPwsh=(Get-Command pwsh -ErrorAction Stop).Source
$taskRunner=Join-Path $PSScriptRoot 'run-agent.ps1'
if($Mode -eq 'Daily'){
 $taskName=$taskNames[0]
 $taskTrigger=New-ScheduledTaskTrigger -Daily -At '09:00'
 $taskArguments='-NoProfile -ExecutionPolicy Bypass -File "'+$taskRunner+'" -Mode daily -Scheduled'
 $taskDescription='每天北京时间09:00运行独立科技新闻agent，使用本地资料地图，真实采集、模型分析、排版及发信。'
}else{
 $taskName=$taskNames[1]
 $taskExisting=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
 if($taskExisting -and $taskExisting.State -eq 'Running'){throw '录屏演示正在运行，请等待完成。'}
 $taskTime=(Get-Date).AddMinutes($DelayMinutes)
 $taskDemoId='demo-'+$taskTime.ToString('yyyyMMdd-HHmmss')
 $taskTrigger=New-ScheduledTaskTrigger -Once -At $taskTime
 $taskArguments='-NoProfile -ExecutionPolicy Bypass -File "'+$taskRunner+'" -Mode demo -DemoId '+$taskDemoId+' -Scheduled'
 $taskDescription='仅运行一次的真实录屏演示。由Windows定时启动独立agent；演示编号 '+$taskDemoId
}
$taskAction=New-ScheduledTaskAction -Execute $taskPwsh -Argument $taskArguments -WorkingDirectory $PSScriptRoot
$taskPrincipal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$taskSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$taskDefinition=New-ScheduledTask -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Description $taskDescription
Register-ScheduledTask -TaskName $taskName -InputObject $taskDefinition -Force | Out-Null
$taskInfo=Get-ScheduledTaskInfo -TaskName $taskName
$taskRecord=[ordered]@{taskName=$taskName;mode=$Mode;registeredAtUtc=[DateTime]::UtcNow.ToString('o');nextRunTime=$taskInfo.NextRunTime.ToString('o');triggerObserved=$false;requiresWindowsUserLoggedIn=$true;requiresCodexDesktopOpen=$false}
if($Mode -eq 'Demo'){$taskRecord.demoId=$taskDemoId}
$taskRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot ('schedule-'+$Mode.ToLower()+'.json')) -Encoding utf8
Write-Output ('已注册：'+$taskName+'；下次运行：'+$taskInfo.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss'))
