$ErrorActionPreference = "Stop"

$installerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Resolve-Path (Join-Path $installerRoot "..\..")
$runtime = Join-Path $installerRoot "python-runtime"
$python = Join-Path $runtime "python.exe"
$script = Join-Path $installerRoot "ALumenans.iss"

if (-not (Test-Path -LiteralPath (Join-Path $runtime "pythonw.exe"))) {
    throw "Bundled Python was not found: $runtime. Run: python packaging\windows\fetch-runtime.py"
}

# Inno Setup lives in different places depending on how it was installed:
# the per-user installer puts it under LOCALAPPDATA, choco (CI) under
# Program Files. $env:ISCC overrides everything.
$candidates = @(
    $env:ISCC,
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
)
$compiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup 6 was not found (set ISCC to the path of ISCC.exe)"
}
Write-Host "Inno Setup: $compiler"

# The packages ship inside the installer: the program itself no longer
# installs anything. There is one list, pyproject.toml, and it is installed
# by the pip of the very Python that goes to the user, so platform markers
# are evaluated correctly.
$requirements = Join-Path $installerRoot "output\requirements.txt"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $requirements) | Out-Null
& $python (Join-Path $repo "packaging\dependencies.py") | Set-Content -LiteralPath $requirements -Encoding ascii
if ($LASTEXITCODE -ne 0) {
    throw "Could not read dependencies from pyproject.toml"
}

& $python -m pip install --disable-pip-version-check --no-warn-script-location -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "pip could not install the bundled dependencies (exit code $LASTEXITCODE)"
}

# Check the build the same way the program does at launch, plus a real
# import: a missing DLL in onnxruntime or torch only shows up that way.
$env:PYTHONPATH = Join-Path $repo "src"
& $python -c @"
from alumenans.dependencies import missing_modules, required_modules
import importlib
missing = missing_modules()
if missing:
    raise SystemExit('Missing in the bundled runtime: ' + ', '.join(missing))
for module in required_modules():
    importlib.import_module(module)
print('Bundled runtime: all required modules import')
"@
$smoke = $LASTEXITCODE
Remove-Item Env:PYTHONPATH
if ($smoke -ne 0) {
    throw "The bundled runtime is incomplete (exit code $smoke)"
}

# The license texts of everything that ships, collected from the runtime
# itself, so the list matches the build exactly. The installer puts them
# into {app}\licenses next to LICENSE and THIRD-PARTY-NOTICES.md.
& $python (Join-Path $repo "packaging\third_party_licenses.py") (Join-Path $installerRoot "output\licenses") (Join-Path $runtime "Lib\site-packages")
if ($LASTEXITCODE -ne 0) {
    throw "Could not collect the third-party licenses (exit code $LASTEXITCODE)"
}

& $compiler $script
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup exited with code $LASTEXITCODE"
}

# The version is defined only in ALumenans.iss: two places inevitably drift apart.
$version = (Select-String -LiteralPath $script -Pattern '#define AppVersion "([^"]+)"').Matches[0].Groups[1].Value
$result = Join-Path $installerRoot "output\ALumenans-Setup-$version.exe"
if (-not (Test-Path -LiteralPath $result)) {
    throw "Installer was not created: $result"
}

Get-Item -LiteralPath $result
