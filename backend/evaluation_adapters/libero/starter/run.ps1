param([string]$Scenes = "practice-scenes.json", [string]$Output = "", [switch]$Build)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$imageName = "wujie-libero-practice:20261018-final"
$imageIds = @(docker image ls --quiet $imageName)
if (!$Build -and $imageIds.Count -eq 0 -and (Test-Path -LiteralPath "wujie-libero-practice-20261018-final.tar.gz")) {
    docker load -i wujie-libero-practice-20261018-final.tar.gz
    if ($LASTEXITCODE -ne 0) { throw "Environment import failed" }
}
$imageIds = @(docker image ls --quiet $imageName)
if ($Build -or $imageIds.Count -eq 0) {
    docker build -f Dockerfile.environment -t $imageName .
    if ($LASTEXITCODE -ne 0) { throw "Environment build failed" }
}
docker run --rm --network none -v "${PSScriptRoot}:/workspace" --entrypoint python $imageName /workspace/pack.py
if ($LASTEXITCODE -ne 0) { throw "Submission packaging failed" }
if (!$Output) { $Output = "runs/" + (Get-Date -Format "yyyyMMdd-HHmmss") }
$networkName = if ($env:UESTC_DOCKER_NETWORK) { $env:UESTC_DOCKER_NETWORK } else { "bridge" }
docker run --rm --cpus 4 --memory 4g --network $networkName -v "${PSScriptRoot}:/workspace" -e UESTC_MODEL_API_KEY -e UESTC_MODEL_API_URL --entrypoint python $imageName /workspace/practice.py --package agent.zip --scenes $Scenes --output $Output
$runExit = $LASTEXITCODE
$reviewPath = Join-Path $PSScriptRoot "$Output/review.html"
if (Test-Path -LiteralPath $reviewPath) { Write-Output "Review: $reviewPath" }
exit $runExit
