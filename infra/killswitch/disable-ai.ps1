<#
  Earshot budget kill switch (Azure Automation runbook, PowerShell 7.2).

  Run by action group ag-earshot-killswitch (webhook, common alert schema) when a usage alert
  (tokens or speech, hourly or daily) or the monthly budget's 100% "actual" alert fires.
  Cuts off every Cognitive Services / Foundry account in the resource group by
  disabling public network access: model calls and speech both stop at once.
  Deployments, keys-off auth and settings are untouched, so restoring is one
  command: infra/killswitch/restore-ai.sh

  Runs as the Automation account's system-assigned identity, which holds only
  "Cognitive Services Contributor" on the resource group and "Storage Blob Data Contributor" on
  the app storage account's `admin` container.

  After the cut-off it also switches the app to cached-only mode (eng ET10): it sets
  `cached_only` in admin/flags.json, which the worker polls every 30 s, so listeners hear the
  notice and recorded clips keep playing, with no restart. Live mode is restored by hand:
  `earshot flags --cached-only off` (after infra/killswitch/restore-ai.sh).

  Drill: start the runbook with DrillFlagOnly=true to exercise only the flag (the AI accounts are
  left alone), then restore the flag by hand.
#>
param([object]$WebhookData, [bool]$DrillFlagOnly = $false)

$ErrorActionPreference = 'Stop'

# The action group calls this webhook on both transitions of a metric alert: fired and resolved.
# Only a firing alert cuts the AI off. On 2026-10-17 the daily token alert's *resolve* at 14:23 UTC
# ran this and disabled AI again, seven hours after it had been restored. A budget alert carries
# no monitorCondition and still acts, and so does a payload that can't be read (fail safe).
if ($WebhookData -and -not $DrillFlagOnly) {
  try {
    $wd = if ($WebhookData -is [string]) { $WebhookData | ConvertFrom-Json } else { $WebhookData }
    $alert = if ($wd.RequestBody -is [string]) { $wd.RequestBody | ConvertFrom-Json } else { $wd.RequestBody }
    $essentials = $alert.data.essentials
    if ($essentials.monitorCondition -eq 'Resolved') {
      Write-Output "Alert $($essentials.alertRule) resolved: nothing to do (the kill switch acts when an alert fires)."
      return
    }
    Write-Output "Alert $($essentials.alertRule) $($essentials.monitorCondition): cutting the AI off."
  } catch {
    Write-Output "Alert payload unreadable ($($_.Exception.Message)): cutting the AI off."
  }
}

$ResourceGroup  = 'rg-earshot'
$ApiVersion     = '2024-10-01'
$StorageAccount = 'stearshotn6bxjyeo'   # the app's storage account (infra/main.bicep output STORAGE_ACCOUNT)

Connect-AzAccount -Identity | Out-Null
$SubscriptionId = (Get-AzContext).Subscription.Id

$listPath = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts?api-version=$ApiVersion"
$accounts = ((Invoke-AzRestMethod -Method GET -Path $listPath).Content | ConvertFrom-Json).value

$failure = $null
if ($DrillFlagOnly) {
  Write-Output "Drill: leaving the AI accounts alone, writing the cached-only flag only."
} elseif (-not $accounts) {
  Write-Output "No Cognitive Services accounts in $ResourceGroup; nothing to cut off."
} else {
  $body = @{ properties = @{ publicNetworkAccess = 'Disabled' } } | ConvertTo-Json -Depth 4
  foreach ($a in $accounts) {
    $r = Invoke-AzRestMethod -Method PATCH -Path "$($a.id)?api-version=$ApiVersion" -Payload $body
    Write-Output "$($a.name): publicNetworkAccess=Disabled -> HTTP $($r.StatusCode)"
    if ($r.StatusCode -ge 300) { $failure = "Kill switch failed on $($a.name): $($r.Content)" }
  }
}

# Cached-only mode: read-modify-write admin/flags.json so judge codes and spend counters survive.
# Never allowed to undo or block the cut-off above.
try {
  $tok = (Get-AzAccessToken -ResourceUrl 'https://storage.azure.com/').Token
  if ($tok -is [securestring]) { $tok = [System.Net.NetworkCredential]::new('', $tok).Password }
  $url = "https://$StorageAccount.blob.core.windows.net/admin/flags.json"
  $headers = @{ Authorization = "Bearer $tok"; 'x-ms-version' = '2023-11-03' }
  $flags = @{}
  try {
    $current = Invoke-RestMethod -Method GET -Uri $url -Headers $headers
    if ($current) { $current.PSObject.Properties | ForEach-Object { $flags[$_.Name] = $_.Value } }
  } catch {
    if ($_.Exception.Response.StatusCode.value__ -ne 404) { throw }
  }
  $flags['cached_only'] = $true
  $flags['reason'] = if ($DrillFlagOnly) { 'kill-switch drill (flag only)' } else { 'kill switch: usage or budget alert' }
  $flags['set_at'] = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
  $put = $headers + @{ 'x-ms-blob-type' = 'BlockBlob' }
  Invoke-RestMethod -Method PUT -Uri $url -Headers $put -ContentType 'application/json' -Body ($flags | ConvertTo-Json -Depth 6) | Out-Null
  Write-Output "cached_only=true written to admin/flags.json"
} catch {
  Write-Output "cached-only flag NOT written: $($_.Exception.Message)"
}

if ($failure) { throw $failure }
