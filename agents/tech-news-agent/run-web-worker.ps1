param([switch]$Once,[switch]$Check)
$ErrorActionPreference='Stop'
$workerConfig=Join-Path $PSScriptRoot 'web-worker-config.json'
if(-not (Test-Path -LiteralPath $workerConfig -PathType Leaf)){throw '请先配置 web-worker-config.json；不要将 workerToken 放进网页或公开仓库。'}
$agentConfig=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$workerPython=$agentConfig.pythonExecutable
if(-not (Test-Path -LiteralPath $workerPython -PathType Leaf)){throw '找不到 Python，请检查 config.json。'}
$env:PYTHONUTF8='1'
$workerArguments=@((Join-Path $PSScriptRoot 'web_worker.py'))
if($Once){$workerArguments+='--once'}
if($Check){$workerArguments+='--check'}
& $workerPython @workerArguments
exit $LASTEXITCODE
