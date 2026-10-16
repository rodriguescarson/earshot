<#
  Earshot budget kill switch (Azure Automation runbook, PowerShell 7.2).

  Fired by the action group on the monthly budget's 100% "actual" alert.
  Cuts off every Cognitive Services / Foundry account in the resource group by
  disabling public network access: model calls and speech both stop at once.
  Deployments, keys-off auth and settings are untouched, so restoring is one
  command: infra/killswitch/restore-ai.sh

  Runs as the Automation account's system-assigned identity, which holds only
  "Cognitive Services Contributor" on the resource group.
#>
param([object]$WebhookData)

$ErrorActionPreference = 'Stop'
$ResourceGroup = 'rg-earshot'
$ApiVersion    = '2024-10-01'

Connect-AzAccount -Identity | Out-Null
$SubscriptionId = (Get-AzContext).Subscription.Id

$listPath = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts?api-version=$ApiVersion"
$accounts = ((Invoke-AzRestMethod -Method GET -Path $listPath).Content | ConvertFrom-Json).value

if (-not $accounts) {
  Write-Output "No Cognitive Services accounts in $ResourceGroup; nothing to cut off."
  return
}

$body = @{ properties = @{ publicNetworkAccess = 'Disabled' } } | ConvertTo-Json -Depth 4
foreach ($a in $accounts) {
  $r = Invoke-AzRestMethod -Method PATCH -Path "$($a.id)?api-version=$ApiVersion" -Payload $body
  Write-Output "$($a.name): publicNetworkAccess=Disabled -> HTTP $($r.StatusCode)"
  if ($r.StatusCode -ge 300) { throw "Kill switch failed on $($a.name): $($r.Content)" }
}
