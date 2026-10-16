// Earshot on Azure (design "Azure", eng D2/E-1a/E-4, SEC2). Resource-group scope: rg-earshot, Sweden Central.
//
// Keyless throughout: Storage has shared keys off, Web PubSub and the AI account have local auth
// off, ACR has no admin user, and every app reaches every service with a user-assigned managed
// identity holding only the roles it needs. Logs go to Log Analytics through diagnostic settings,
// so no workspace key is handled either.
//
// Already in the group and referenced, not created here: the AI Services account (earshot-ai),
// the kill switch (automation account, action group, alerts, budget) and the Static Web App that
// hosts the listening page and pre-rendered clips (earshot-web).

targetScope = 'resourceGroup'

@description('Region for everything created here.')
param location string = resourceGroup().location

@description('Short environment name used in resource names (azd sets it).')
param environmentName string = 'earshot'

@description('Web PubSub tier: Free_F1 until the load test and judging need more than 20 connections (eng E-1a).')
@allowed(['Free_F1', 'Standard_S1'])
param webPubSubSku string = 'Free_F1'

@description('Worker image; empty on the first provision, before anything is built.')
param workerImage string = ''

@description('MCP app image (the same image, a different command).')
param mcpImage string = ''

@description('The existing AI Services account (Foundry models and Speech).')
param aiAccountName string = 'earshot-ai'

@description('Origins allowed to call the worker API: the Static Web App host.')
param allowedOrigins string = ''

var placeholder = 'mcr.microsoft.com/k8se/quickstart:latest'
var suffix = uniqueString(resourceGroup().id)
var tags = { project: 'earshot', 'azd-env-name': environmentName }

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  webPubSubOwner: '12cf5a90-567b-43ae-8102-96cf46c7d9b4'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  speechUser: 'f2dc8367-1007-4938-bd23-fe263f013447'
}

// ----- monitoring -------------------------------------------------------------------------------

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${environmentName}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    DisableLocalAuth: true // Entra-only ingestion (SEC2): the connection string alone cannot write telemetry
    RetentionInDays: 30
  }
}

// ----- storage: flags, snapshots, audio cache, clips and the hidden truth logs ------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'st${environmentName}${take(suffix, 8)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    accessTier: 'Hot'
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

// admin: flags.json (cached-only, judge-code hashes, spend counters); snapshots: E-4 match state
// for the MCP app; audio: the TTS cache; clips: pre-rendered clip packages for the static site;
// hidden: truth logs, readable by no app identity (design F4).
resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in ['admin', 'snapshots', 'audio', 'clips', 'hidden']: {
  parent: blobs
  name: c
  properties: { publicAccess: 'None' }
}]

// ----- registry and identities ------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${environmentName}${take(suffix, 8)}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
  }
}

resource workerId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${environmentName}-worker'
  location: location
  tags: tags
}

resource mcpId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${environmentName}-mcp'
  location: location
  tags: tags
}

resource ai 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiAccountName
}

// ----- Web PubSub -------------------------------------------------------------------------------

resource wps 'Microsoft.SignalRService/webPubSub@2024-03-01' = {
  name: 'wps-${environmentName}-${take(suffix, 6)}'
  location: location
  tags: tags
  sku: { name: webPubSubSku, capacity: 1 }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource hub 'Microsoft.SignalRService/webPubSub/hubs@2024-03-01' = {
  parent: wps
  name: 'earshot'
  properties: {
    anonymousConnectPolicy: 'deny'
  }
}

// ----- role assignments (least privilege) -------------------------------------------------------

resource workerAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, workerId.id, roles.acrPull)
  properties: { principalId: workerId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull) }
}

resource mcpAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, mcpId.id, roles.acrPull)
  properties: { principalId: mcpId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull) }
}

// The worker writes flags, snapshots and audio; it has no access to clips or hidden.
resource workerBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [0, 1, 2]: {
  scope: containers[i]
  name: guid(containers[i].id, workerId.id, roles.blobContributor)
  properties: { principalId: workerId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobContributor) }
}]

// The MCP app reads flags (judge-code hashes) and snapshots, nothing else.
resource mcpBlobs 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for i in [0, 1]: {
  scope: containers[i]
  name: guid(containers[i].id, mcpId.id, roles.blobReader)
  properties: { principalId: mcpId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobReader) }
}]

resource workerWps 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: wps
  name: guid(wps.id, workerId.id, roles.webPubSubOwner)
  properties: { principalId: workerId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.webPubSubOwner) }
}

resource workerOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ai
  name: guid(ai.id, workerId.id, roles.openAiUser)
  properties: { principalId: workerId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser) }
}

resource workerSpeech 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: ai
  name: guid(ai.id, workerId.id, roles.speechUser)
  properties: { principalId: workerId.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.speechUser) }
}

// ----- Container Apps ---------------------------------------------------------------------------

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${environmentName}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: { destination: 'azure-monitor' }
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
  }
}

resource envLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: env
  name: 'to-log-analytics'
  properties: {
    workspaceId: logs.id
    logs: [
      { category: 'ContainerAppConsoleLogs', enabled: true }
      { category: 'ContainerAppSystemLogs', enabled: true }
    ]
  }
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${environmentName}-worker'
  location: location
  tags: union(tags, { 'azd-service-name': 'worker' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${workerId.id}': {} } }
  dependsOn: [workerAcr]
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: empty(workerImage) ? 80 : 8080, transport: 'auto', allowInsecure: false }
      registries: empty(workerImage) ? [] : [ { server: acr.properties.loginServer, identity: workerId.id } ]
      secrets: [ { name: 'appinsights', value: appInsights.properties.ConnectionString } ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: empty(workerImage) ? placeholder : workerImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: workerId.properties.clientId }
            { name: 'EARSHOT_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'EARSHOT_WEBPUBSUB_ENDPOINT', value: 'https://${wps.properties.hostName}' }
            { name: 'EARSHOT_WEBPUBSUB_HUB', value: hub.name }
            { name: 'EARSHOT_AI_ENDPOINT', value: ai.properties.endpoint }
            { name: 'EARSHOT_ALLOWED_ORIGINS', value: allowedOrigins }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights' }
          ]
          probes: empty(workerImage) ? [] : [
            { type: 'Liveness', httpGet: { path: '/healthz', port: 8080 }, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8080 }, periodSeconds: 10 }
          ]
        }
      ]
      // One replica: live Director state is per replica (CEO Section 9: single-revision mode).
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource mcp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${environmentName}-mcp'
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp' })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mcpId.id}': {} } }
  dependsOn: [mcpAcr]
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: true, targetPort: empty(mcpImage) ? 80 : 8080, transport: 'auto', allowInsecure: false }
      registries: empty(mcpImage) ? [] : [ { server: acr.properties.loginServer, identity: mcpId.id } ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: empty(mcpImage) ? placeholder : mcpImage
          command: empty(mcpImage) ? [] : ['uvicorn', 'earshot.api.mcp:app', '--host', '0.0.0.0', '--port', '8080', '--proxy-headers', '--forwarded-allow-ips', '*']
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: mcpId.properties.clientId }
            { name: 'EARSHOT_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
          ]
        }
      ]
      // Scales to zero between judges: costs nothing idle.
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output WORKER_URL string = 'https://${worker.properties.configuration.ingress.fqdn}'
output MCP_URL string = 'https://${mcp.properties.configuration.ingress.fqdn}/mcp'
output STORAGE_ACCOUNT string = storage.name
output STORAGE_URL string = storage.properties.primaryEndpoints.blob
output WEBPUBSUB_HOST string = wps.properties.hostName
output WORKER_PRINCIPAL_ID string = workerId.properties.principalId
