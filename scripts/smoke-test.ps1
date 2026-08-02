Param(
  [Parameter(Mandatory = $true)] [string] $ProxyBaseUrl,
  [string] $StaticWebAppUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-JsonEndpoint {
  Param(
    [Parameter(Mandatory = $true)] [string] $Url,
    [Parameter(Mandatory = $true)] [string] $Name
  )

  try {
    $response = Invoke-WebRequest -Uri $Url -Method GET -UseBasicParsing -TimeoutSec 45
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      throw "$Name returned status $($response.StatusCode)"
    }

    "[ok] $Name -> $($response.StatusCode)"
  }
  catch {
    throw "Smoke test failed for $Name at $Url. $($_.Exception.Message)"
  }
}

$proxy = $ProxyBaseUrl.TrimEnd('/')

"Running smoke tests against $proxy"
Test-JsonEndpoint -Url "$proxy/api/healthz" -Name "Proxy health"
Test-JsonEndpoint -Url "$proxy/api/memo/status?requestId=smoke-test" -Name "Memo status"
Test-JsonEndpoint -Url "$proxy/api/indexer-status?requestId=smoke-test" -Name "Indexer status"

if ($StaticWebAppUrl) {
  $ui = $StaticWebAppUrl.TrimEnd('/')
  Test-JsonEndpoint -Url $ui -Name "Static web app root"
}

"Smoke tests completed successfully."
