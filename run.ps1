param(
    [string]$Config = "",
    [switch]$Refresh,
    [switch]$ScreenOnly,
    [switch]$Test,
    [switch]$InstallDependencies,
    [switch]$LegacyTencent,
    [switch]$EastmoneyOnly,
    [switch]$FinalHFQ,
    [switch]$RebuildProcessed,
    [switch]$Audit,
    [switch]$WriteHashes
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$TempRoot = Join-Path $RuntimeRoot "temp"
$CacheRoot = Join-Path $RuntimeRoot "cache"
$MarketRoot = Join-Path $RuntimeRoot "market_py310"
$BaoRoot = Join-Path $RuntimeRoot "baostock_py"
$LocalConfigPath = Join-Path $ProjectRoot "config\local_paths.psd1"
$LocalConfig = if (Test-Path -LiteralPath $LocalConfigPath) { Import-PowerShellDataFile -LiteralPath $LocalConfigPath } else { @{} }
$Python = if ($env:FYP_PYTHON) { $env:FYP_PYTHON } elseif ($LocalConfig.Python) { $LocalConfig.Python } else { "D:\Python\Anaconda3\envs\clean\python.exe" }
$env:FYP_DATA_ROOT = if ($env:FYP_DATA_ROOT) { $env:FYP_DATA_ROOT } elseif ($LocalConfig.DataVault) { $LocalConfig.DataVault } else { "D:\FYP_DataVault\csi300_breakout_events" }

if (-not (Test-Path -LiteralPath $Python)) {
    throw "No D-drive Python was found at $Python. Set FYP_PYTHON or create config/local_paths.psd1 from the example."
}
if (-not ([System.IO.Path]::GetFullPath($Python).StartsWith("D:\", [System.StringComparison]::OrdinalIgnoreCase))) {
    throw "Python must be located on D drive under the project storage policy: $Python"
}
if (-not ([System.IO.Path]::GetFullPath($env:FYP_DATA_ROOT).StartsWith("D:\", [System.StringComparison]::OrdinalIgnoreCase))) {
    throw "FYP_DATA_ROOT must be located on D drive: $env:FYP_DATA_ROOT"
}
New-Item -ItemType Directory -Force -Path $TempRoot,$CacheRoot,$MarketRoot | Out-Null
$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = "$(Join-Path $ProjectRoot 'src');$MarketRoot;$BaoRoot"

Push-Location $ProjectRoot
try {
    if ($InstallDependencies) {
        & $Python -m pip install --upgrade --target $MarketRoot akshare yfinance pytest
        exit $LASTEXITCODE
    }

    if ($Test) {
        & $Python -m pytest -q
        exit $LASTEXITCODE
    }

    if ($Audit) {
        $AuditArguments = @("-m", "csi300_events.formal_audit")
        if ($WriteHashes) { $AuditArguments += "--write-missing-hashes" }
        & $Python @AuditArguments
        exit $LASTEXITCODE
    }

    if ($LegacyTencent) {
        throw "The Tencent workflow was retired and its large data layer was deleted. Use the formal Eastmoney hfq pipeline."
    }

    if ($ScreenOnly) {
        throw "-ScreenOnly belongs to the legacy Tencent workflow. Use -LegacyTencent -ScreenOnly, or run the new source audit from complete caches."
    }
    if ($EastmoneyOnly) {
        throw "-EastmoneyOnly belonged to the retired qfq/hfq comparison workflow. The formal pipeline now uses Eastmoney hfq only."
    }
    $FinalConfig = if ($Config) { $Config } else { "config/hfq_final_sample.json" }
    $FormalArguments = @(
        "-m", "csi300_events.formal_pipeline",
        "--price-config", "config/eastmoney_price_audit.json",
        "--final-config", $FinalConfig
    )
    if ($Refresh) { $FormalArguments += "--refresh" }
    if ($RebuildProcessed) { $FormalArguments += "--rebuild-processed" }
    & $Python @FormalArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
