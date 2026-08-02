Param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$checks = @(
  @{ Name = "Azure CLI"; Command = "az"; VersionArgs = @("version") },
  @{ Name = "Azure Functions Core Tools"; Command = "func"; VersionArgs = @("--version") },
  @{ Name = "Python"; Command = "python"; VersionArgs = @("--version") },
  @{ Name = "Node.js"; Command = "node"; VersionArgs = @("--version") },
  @{ Name = "pnpm"; Command = "pnpm"; VersionArgs = @("--version") },
  @{ Name = "npx"; Command = "npx"; VersionArgs = @("--version") }
)

$missing = @()

foreach ($check in $checks) {
  $cmd = Get-Command -Name $check.Command -ErrorAction SilentlyContinue
  if (-not $cmd) {
    $missing += $check.Name
    continue
  }

  try {
    $version = & $check.Command @($check.VersionArgs) 2>$null
    if ($LASTEXITCODE -eq 0 -and $version) {
      "[ok] $($check.Name): $($version | Select-Object -First 1)"
    } else {
      "[ok] $($check.Name)"
    }
  }
  catch {
    "[ok] $($check.Name)"
  }
}

if ($missing.Count -gt 0) {
  ""
  "Missing required tooling:"
  foreach ($name in $missing) {
    "- $name"
  }
  exit 1
}

""
"All prerequisites are installed."
