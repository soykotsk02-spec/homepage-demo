param([switch]$StartNow,[switch]$Remove)
$ErrorActionPreference='Stop'
$workerTaskName='TechNewsAgent-WebWorker'
if($Remove){
 if(Get-ScheduledTask -TaskName $workerTaskName -ErrorAction SilentlyContinue){Unregister-ScheduledTask -TaskName $workerTaskName -Confirm:$false}
 '已移除网页 worker 登录启动任务。'
 exit 0
}
# web-worker-config.json 示例（workerToken 必须换成由管理员设置的真实随机值）：
# {"baseUrl":"https://homepage-demo1111.vercel.app/api/agent","workerId":"home-pc","workerToken":"REPLACE_WITH_RANDOM_WORKER_TOKEN","pollSeconds":30}
if(-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'web-worker-config.json') -PathType Leaf)){throw 'web-worker-config.json 不存在，未安装任务。'}
& (Join-Path $PSScriptRoot 'run-web-worker.ps1') -Check
if($LASTEXITCODE -ne 0){throw '网页 worker 本地配置检查失败，未安装任务。'}
$workerUser=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$workerAgentConfig=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$workerPythonw=Join-Path (Split-Path -Parent $workerAgentConfig.pythonExecutable) 'pythonw.exe'
if(Test-Path -LiteralPath $workerPythonw -PathType Leaf){
 # A GUI-subsystem interpreter has no hidden console that another host can close.
 $workerScript=Join-Path $PSScriptRoot 'web_worker.py'
 $workerAction=New-ScheduledTaskAction -Execute $workerPythonw -Argument ('-X utf8 "'+$workerScript+'"') -WorkingDirectory $PSScriptRoot
}else{
 $workerPwsh=(Get-Command pwsh -ErrorAction Stop).Source
 $workerRunner=Join-Path $PSScriptRoot 'run-web-worker.ps1'
 $workerAction=New-ScheduledTaskAction -Execute $workerPwsh -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$workerRunner+'"') -WorkingDirectory $PSScriptRoot
}
$workerTrigger=New-ScheduledTaskTrigger -AtLogOn -User $workerUser
$workerPrincipal=New-ScheduledTaskPrincipal -UserId $workerUser -LogonType Interactive -RunLevel Limited
$workerSettings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
$workerDefinition=New-ScheduledTask -Action $workerAction -Trigger $workerTrigger -Settings $workerSettings -Principal $workerPrincipal -Description '登录后隐藏运行，通过出站HTTPS领取网页任务并同步科技简报，不开放本机入站端口。'
Register-ScheduledTask -TaskName $workerTaskName -InputObject $workerDefinition -Force | Out-Null
if($StartNow){Start-ScheduledTask -TaskName $workerTaskName}
'已安装 TechNewsAgent-WebWorker 登录启动任务。'
