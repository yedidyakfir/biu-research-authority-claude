param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$skillsRoot = Join-Path $root '.agents\skills'
if (-not $CheckOnly) { New-Item -ItemType Directory -Force $skillsRoot | Out-Null }
foreach ($name in @('monitor-grant-calls', 'set-up-grant-watch')) {
    $source = (Resolve-Path (Join-Path $root "plugins\grant-watch\skills\$name")).Path
    $link = Join-Path $skillsRoot $name
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link
        if ($item.LinkType -ne 'Junction' -or $item.Target -ne $source) {
            throw "Refusing to replace existing nonmatching skill at $link"
        }
    } elseif ($CheckOnly) {
        throw "Missing development link: $link"
    } else {
        New-Item -ItemType Junction -Path $link -Target $source | Out-Null
    }
    $sourceHash = (Get-FileHash (Join-Path $source 'SKILL.md')).Hash
    $linkHash = (Get-FileHash (Join-Path $link 'SKILL.md')).Hash
    if ($sourceHash -ne $linkHash) { throw "Linked skill mismatch: $name" }
    Write-Output "Verified repository-linked skill: $name"
}
