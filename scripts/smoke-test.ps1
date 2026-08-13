Param(
  [Parameter(Mandatory = $true)] [string] $ProxyBaseUrl,
  [string] $StaticWebAppUrl = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-JsonEndpoint {
  Param(
    [Parameter(Mandatory = $true)] [string] $Url,
    [Parameter(Mandatory = $true)] [string] $Name,
    [switch] $AllowAuthenticationChallenge
  )

  try {
    $response = Invoke-WebRequest -Uri $Url -Method GET -UseBasicParsing -TimeoutSec 45 -Headers @{ "User-Agent" = "credit-memo-smoke-test/1.0" }
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      throw "$Name returned status $($response.StatusCode)"
    }

    "[ok] $Name -> $($response.StatusCode)"
  }
  catch {
    $response = $_.Exception.Response
    if ($AllowAuthenticationChallenge -and $response -and [int]$response.StatusCode -eq 401) {
      "[ok] $Name -> 401 (authentication required)"
      return
    }
    throw "Smoke test failed for $Name at $Url. $($_.Exception.Message)"
  }
}

$proxy = $ProxyBaseUrl.TrimEnd('/')

"Running smoke tests against $proxy"
Test-JsonEndpoint -Url "$proxy/api/healthz" -Name "Proxy health" -AllowAuthenticationChallenge
Test-JsonEndpoint -Url "$proxy/api/memo/status?requestId=smoke-test" -Name "Memo status" -AllowAuthenticationChallenge
Test-JsonEndpoint -Url "$proxy/api/indexer-status?requestId=smoke-test" -Name "Indexer status" -AllowAuthenticationChallenge

if ($StaticWebAppUrl) {
  $ui = $StaticWebAppUrl.TrimEnd('/')
  Test-JsonEndpoint -Url $ui -Name "Static web app root"
}

"Smoke tests completed successfully."
