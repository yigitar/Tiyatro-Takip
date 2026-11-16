<#
Creates (or refreshes) the Desktop shortcut "Tiyatro" that starts the PC app without a console
window: venv pythonw.exe + pc\tiyatro_takip.pyw, working directory = project folder, icon
pc\assets\tiyatro.ico. All paths are computed from this script's own location, so re-run it
after moving the project folder. Safe to run repeatedly (overwrites the same .lnk).

  powershell -ExecutionPolicy Bypass -File scripts\make_shortcut.ps1 [-Folder <dir>] [-Name Tiyatro]

-Folder defaults to the real Desktop (OneDrive-redirected or not).
#>
param(
    [string]$Folder = [Environment]::GetFolderPath('Desktop'),
    [string]$Name = 'Tiyatro'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\pythonw.exe'
$launcher = Join-Path $repo 'pc\tiyatro_takip.pyw'
$icon = Join-Path $repo 'pc\assets\tiyatro.ico'
$localIcon = Join-Path $repo 'pc\assets\local\tiyatro.ico'   # git-ignored; preferred when present
if (Test-Path -LiteralPath $localIcon) { $icon = $localIcon }
foreach ($needed in @($python, $launcher, $icon)) {
    if (-not (Test-Path -LiteralPath $needed)) { throw "Missing: $needed" }
}
if (-not (Test-Path -LiteralPath $Folder)) { throw "Folder not found: $Folder" }
$path = Join-Path $Folder ($Name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($path)
$link.TargetPath = $python
$link.Arguments = '"' + $launcher + '"'
$link.WorkingDirectory = $repo
$link.IconLocation = $icon + ',0'
$link.Description = 'Tiyatro Takip'
$link.WindowStyle = 1
$link.Save()
Write-Output "Shortcut written: $path"
