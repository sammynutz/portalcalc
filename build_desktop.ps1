param(
    [string]$Python = "C:\Users\SamLawless\AppData\Local\Programs\Python\Python313\python.exe",
    [string]$Version = "",
    [ValidateSet("full", "quick")]
    [string]$SmokeMode = "full",
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
    param(
        [scriptblock]$Command,
        [string]$Label
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Test-PackagedExecutable {
    param(
        [string]$Path,
        [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label executable not found: $Path"
    }

    $internalPath = Join-Path (Split-Path -Parent $Path) "_internal"
    foreach ($incompatibleIcu in @("icudt78.dll", "icuuc.dll")) {
        $icuPath = Join-Path $internalPath $incompatibleIcu
        if (Test-Path -LiteralPath $icuPath) {
            throw "$Label contains incompatible Qt ICU runtime: $icuPath"
        }
    }

    $hadQpaPlatform = Test-Path Env:\QT_QPA_PLATFORM
    $previousQpaPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        $process = Start-Process `
            -FilePath $Path `
            -ArgumentList "--packaged-smoke-test" `
            -WorkingDirectory (Split-Path -Parent $Path) `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($process.ExitCode -ne 0) {
            throw "$Label packaged startup smoke failed with exit code $($process.ExitCode)"
        }
    }
    finally {
        if ($hadQpaPlatform) {
            $env:QT_QPA_PLATFORM = $previousQpaPlatform
        }
        else {
            Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
        }
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python not found: $Python"
}

if (-not $Version) {
    $Version = (& $Python -c "from portalcalc.app_metadata import APP_VERSION; print(APP_VERSION)")
    if ($LASTEXITCODE -ne 0) {
        throw "Reading APP_VERSION failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipSmoke -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot "tests\smoke_checks.py"))) {
    $env:QT_QPA_PLATFORM = "offscreen"
    $smokeArg = "--$SmokeMode"
    try {
        Invoke-Native { & $Python tests\smoke_checks.py $smokeArg } "Smoke checks"
    }
    finally {
        Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    }
}
elseif (-not $SkipSmoke) {
    Write-Warning "tests\smoke_checks.py not found. Continuing with packaged startup and report-export checks."
}

Invoke-Native { & $Python -m PyInstaller --noconfirm --clean PortalCalc.spec } "PortalCalc PyInstaller build"
Invoke-Native { & $Python -m PyInstaller --noconfirm --clean PortalCalcAdmin.spec } "PortalCalcAdmin PyInstaller build"

Test-PackagedExecutable -Path (Join-Path $PWD "dist\PortalCalc\PortalCalc.exe") -Label "PortalCalc"
Test-PackagedExecutable -Path (Join-Path $PWD "dist\PortalCalcAdmin\PortalCalcAdmin.exe") -Label "PortalCalcAdmin"

$reportSmokeRoot = Join-Path $PWD "build\packaged-report-smoke"
New-Item -ItemType Directory -Force -Path $reportSmokeRoot | Out-Null
$reportSmokePath = Join-Path $reportSmokeRoot "computations.pdf"
$reportProcess = Start-Process `
    -FilePath (Join-Path $PWD "dist\PortalCalc\PortalCalc.exe") `
    -ArgumentList @("--packaged-computation-smoke-test", "`"$reportSmokePath`"") `
    -WorkingDirectory (Join-Path $PWD "dist\PortalCalc") `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($reportProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $reportSmokePath)) {
    throw "PortalCalc packaged computation-report export failed; inspect $reportSmokeRoot"
}

# The frame + bracing optimiser starts worker processes by re-launching the EXE.
# Confirm real workers answer; otherwise the optimiser silently runs one at a time.
$workerSmokePath = Join-Path $reportSmokeRoot "workers.json"
$workerProcess = Start-Process `
    -FilePath (Join-Path $PWD "dist\PortalCalc\PortalCalc.exe") `
    -ArgumentList @("--packaged-worker-smoke-test", "`"$workerSmokePath`"") `
    -WorkingDirectory (Join-Path $PWD "dist\PortalCalc") `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($workerProcess.ExitCode -ne 0) {
    throw "PortalCalc packaged optimiser worker check failed; inspect $workerSmokePath"
}

New-Item -ItemType Directory -Force -Path "dist\PortalCalc\presets" | Out-Null
Copy-Item -LiteralPath "portalcalc\data\presets\factory_defaults.json" -Destination "dist\PortalCalc\presets\factory_defaults.json" -Force

$releaseRoot = Join-Path $PWD "release"
$packageRoot = Join-Path $releaseRoot "PortalCalc-$Version"
$zipPath = Join-Path $releaseRoot "PortalCalc-$Version-windows.zip"

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

Copy-Item -LiteralPath "dist\PortalCalc" -Destination $packageRoot -Recurse
Copy-Item -LiteralPath "dist\PortalCalcAdmin" -Destination $packageRoot -Recurse
Copy-Item -LiteralPath "portalcalc\data\EULA.txt" -Destination $packageRoot
Copy-Item -LiteralPath "docs\license_server_README.md" -Destination $packageRoot

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath

Write-Host "Built $zipPath"
