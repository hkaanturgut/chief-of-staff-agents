// Chief of Staff demo — Azure AI Foundry account, project, and model deployments.
// Built on Azure Verified Modules. Subscription-scoped: creates the resource group too.
targetScope = 'subscription'

@description('Location for all resources. canadaeast has quota for both model tiers.')
param location string = 'canadaeast'

@description('Resource group to create.')
param resourceGroupName string = 'rg-chief-of-staff-demo'

@minLength(3)
@maxLength(12)
@description('Base name for the Foundry account and project. Must be globally unique-ish; a hash suffix is appended by the module.')
param baseName string = 'chiefofstaff'

@description('Object id of the human running the demo. Gets data-plane access to the project.')
param principalId string

@description('Cheap tier: the three ingest agents. Extraction does not need the big model.')
param ingestModel object = {
  name: 'gpt-5.4-mini'
  version: '2026-03-17'
  capacity: 100
}

@description('Monthly spend cap to alert on. The VS Enterprise subscription has a credit cap; getting throttled the week of the talk is an avoidable failure.')
param budgetAmount int = 50

@description('Where budget alerts go.')
param budgetContactEmails array = ['kaanturgutbusiness@gmail.com']

@description('Strong tier: consolidator and drafter.')
param strongModel object = {
  name: 'gpt-5.5'
  version: '2026-04-24'
  capacity: 100
}

var tags = {
  project: 'chief-of-staff-agents'
  purpose: 'conference-demo'
  event: 'cloud-summit-toronto-2026'
}

// Azure AI User — data-plane access to projects, agents, and deployments.
// Local auth is off, so Entra RBAC is the only way in, for us and for CI.
var azureAiUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

module rg 'br/public:avm/res/resources/resource-group:0.4.4' = {
  name: 'rg-deploy'
  params: {
    name: resourceGroupName
    location: location
    tags: tags
  }
}

module foundry 'br/public:avm/ptn/ai-ml/ai-foundry:0.7.0' = {
  name: 'foundry-deploy'
  scope: resourceGroup(resourceGroupName)
  params: {
    baseName: baseName
    location: location
    tags: tags
    // No Key Vault / Search / Storage / Cosmos. The pipeline holds no state in
    // Azure — the repo is the state — and the subscription has a credit cap.
    includeAssociatedResources: false
    aiFoundryConfiguration: {
      accountName: 'aif-${baseName}'
      allowProjectManagement: true
      disableLocalAuth: true
      sku: 'S0'
      project: {
        name: 'chief-of-staff'
        displayName: 'Chief of Staff'
        desc: 'Multi-agent Chief of Staff demo. Cloud Summit Toronto 2026.'
      }
      roleAssignments: [
        {
          principalId: principalId
          roleDefinitionIdOrName: azureAiUserRoleId
          principalType: 'User'
        }
      ]
    }
    aiModelDeployments: [
      {
        name: ingestModel.name
        model: {
          format: 'OpenAI'
          name: ingestModel.name
          version: ingestModel.version
        }
        sku: {
          name: 'GlobalStandard'
          capacity: ingestModel.capacity
        }
        versionUpgradeOption: 'NoAutoUpgrade'
      }
      {
        name: strongModel.name
        model: {
          format: 'OpenAI'
          name: strongModel.name
          version: strongModel.version
        }
        sku: {
          name: 'GlobalStandard'
          capacity: strongModel.capacity
        }
        versionUpgradeOption: 'NoAutoUpgrade'
      }
    ]
  }
  dependsOn: [
    rg
  ]
}

// Spend guard. Alerts only — Azure budgets do not stop spend — but 50% of a $50
// cap firing on the Wednesday before the talk is the warning that matters.
module budget 'br/public:avm/res/consumption/budget:0.3.8' = {
  name: 'budget-deploy'
  params: {
    name: 'budget-chief-of-staff-demo'
    amount: budgetAmount
    resetPeriod: 'Monthly'
    thresholds: [50, 80, 100]
    contactEmails: budgetContactEmails
    resourceGroupFilter: [
      resourceGroupName
    ]
  }
  dependsOn: [
    rg
  ]
}

output resourceGroupName string = resourceGroupName
output location string = location
output foundryAccountName string = foundry.outputs.aiServicesName
output projectName string = foundry.outputs.aiProjectName
output projectEndpoint string = 'https://${foundry.outputs.aiServicesName}.services.ai.azure.com/api/projects/${foundry.outputs.aiProjectName}'
output ingestDeployment string = ingestModel.name
output strongDeployment string = strongModel.name
