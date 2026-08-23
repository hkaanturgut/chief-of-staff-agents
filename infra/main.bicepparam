using 'main.bicep'

param location = 'canadaeast'
param resourceGroupName = 'rg-chief-of-staff-demo'
param baseName = 'chiefofstaff'
// Object id of the demo operator. Not a secret — it is a directory object id, not a
// credential — but it is environment-specific, so it is overridable at deploy time.
param principalId = readEnvironmentVariable('AZURE_PRINCIPAL_ID', '7871a41e-3b43-4037-86f5-4ceb34208a34')
