param([ValidateSet('preview','daily','demo','check')][string]$Mode='preview',[string]$DemoId='',[switch]$Scheduled)
$ErrorActionPreference='Stop'
$taskConfig=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$taskPython=$taskConfig.pythonExecutable
if(-not (Test-Path -LiteralPath $taskPython)){throw '找不到 Python，请更新 config.json 中的 pythonExecutable。'}
$env:PYTHONUTF8='1'
$taskArgs=@((Join-Path $PSScriptRoot 'agent.py'))
switch($Mode){
 'check' {$taskArgs+='check'}
 default {
  $taskArgs+='run'
  if($Mode -in @('daily','demo')){$taskArgs+='--send'}
  if($Mode -eq 'demo'){
   if(-not $DemoId){throw '录屏演示必须有固定 DemoId，避免重试时重复发信。'}
   $taskArgs+=@('--demo-id',$DemoId)
  }
  if($Scheduled){$taskArgs+=@('--trigger','windows_task')}
 }
}
& $taskPython @taskArgs
exit $LASTEXITCODE
