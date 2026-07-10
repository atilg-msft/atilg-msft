@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short prefix used to name resources (lowercase, alphanumeric).')
@minLength(3)
@maxLength(11)
param namePrefix string = 'polybot'

@description('Full container image reference to deploy. Defaults to a public placeholder so the first deploy succeeds before anything has been pushed to ACR; point this at the ACR image (or let deploy.sh/the workflow do it via `az containerapp update`) once it exists.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('paper = simulated fills only. live = real orders with real funds.')
@allowed(['paper', 'live'])
param polybotMode string = 'paper'

@description('Signature type for live trading; irrelevant in paper mode.')
@allowed(['proxy', 'eoa', 'gnosis-safe'])
param signatureType string = 'proxy'

@description('App Configuration label used to select settings (leave empty for the default/no label).')
param appConfigLabel string = ''

@description('SKU for the App Configuration store.')
@allowed(['free', 'standard'])
param appConfigSku string = 'standard'

var resourceToken = uniqueString(resourceGroup().id, namePrefix)
var acrName = toLower('${namePrefix}acr${resourceToken}')
var kvName = toLower('${namePrefix}-kv-${take(resourceToken, 8)}')
var appConfigName = toLower('${namePrefix}-appconfig-${take(resourceToken, 8)}')
var storageAccountName = toLower('${namePrefix}st${take(resourceToken, 8)}')
var logAnalyticsName = '${namePrefix}-logs'
var containerAppEnvName = '${namePrefix}-env'
var containerAppName = '${namePrefix}-app'
var fileShareName = 'polybot-data'

// ---------------------------------------------------------------------------
// Observability + Container Apps environment
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerAppEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Persistent storage for portfolio state / trade log / control state
// (the bot is a stateful singleton -- see maxReplicas: 1 on the container app)
// ---------------------------------------------------------------------------

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-01-01' = {
  name: '${storageAccountName}/default/${fileShareName}'
  dependsOn: [storageAccount]
  properties: {
    shareQuota: 5
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2023-05-01' = {
  parent: containerAppEnv
  name: 'polybot-data-storage'
  properties: {
    azureFile: {
      accountName: storageAccountName
      accountKey: storageAccount.listKeys().keys[0].value
      shareName: fileShareName
      accessMode: 'ReadWrite'
    }
  }
  dependsOn: [fileShare]
}

// ---------------------------------------------------------------------------
// Container registry (admin credentials used for image pull -- simplest path
// for a first deployment; switch to identity-based AcrPull once the app is
// up if you want to drop the shared admin password).
// ---------------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
  }
}

// ---------------------------------------------------------------------------
// Key Vault (secrets: polybot-private-key, polybot-funder-address -- set
// these yourself after deployment, they are never passed through Bicep)
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// ---------------------------------------------------------------------------
// App Configuration (dynamic strategy/risk/scan parameters)
// ---------------------------------------------------------------------------

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2023-03-01' = {
  name: appConfigName
  location: location
  sku: { name: appConfigSku }
  properties: {
    disableLocalAuth: true
  }
}

// Strategy/risk/scan key-values are seeded post-deploy (deploy.sh / the
// GitHub Actions workflow) via `az appconfig kv set --auth-mode login`, not
// as native Bicep child resources here: ARM's own key-value write path uses
// the store's local-auth access keys, which fails once disableLocalAuth is
// true above ("Conflict: ... local authentication is disabled"). The
// az CLI's --auth-mode login instead authenticates as the deploying
// principal over AAD/RBAC, which works either way.

// ---------------------------------------------------------------------------
// Container App (the bot + its FastAPI control panel, single replica)
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: containerAppName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acr.properties.loginServer
          username: acr.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: acr.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'polybot'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'POLYBOT_MODE', value: polybotMode }
            { name: 'POLYBOT_SIGNATURE_TYPE', value: signatureType }
            { name: 'POLYBOT_DATA_DIR', value: '/app/data' }
            { name: 'PORT', value: '8000' }
            { name: 'AZURE_KEY_VAULT_URL', value: keyVault.properties.vaultUri }
            { name: 'AZURE_APPCONFIG_ENDPOINT', value: appConfig.properties.endpoint }
            { name: 'AZURE_APPCONFIG_LABEL', value: appConfigLabel }
          ]
          volumeMounts: [
            { volumeName: 'polybot-data', mountPath: '/app/data' }
          ]
        }
      ]
      volumes: [
        {
          name: 'polybot-data'
          storageType: 'AzureFile'
          storageName: envStorage.name
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

// ---------------------------------------------------------------------------
// RBAC: let the container app's managed identity read secrets/config
// ---------------------------------------------------------------------------

var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var appConfigDataReaderRoleId = '516239f1-63e1-4d78-a4de-a74fb236a071'

resource kvRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, containerApp.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource appConfigRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(appConfig.id, containerApp.id, appConfigDataReaderRoleId)
  scope: appConfig
  properties: {
    principalId: containerApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', appConfigDataReaderRoleId)
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output appConfigName string = appConfig.name
output appConfigEndpoint string = appConfig.properties.endpoint
output storageAccountName string = storageAccount.name
