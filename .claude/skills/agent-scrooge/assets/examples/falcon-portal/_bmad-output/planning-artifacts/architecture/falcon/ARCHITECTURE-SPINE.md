# Falcon Portal architecture spine (synthetic sample)

## Decisions

### AD-1 Hosting
The portal (React front end, .NET 9 API) runs on AKS in uaenorth: 2 nodes in Dev and Test, 3 in Prod.

### AD-2 Data
Customer applications are stored in Azure SQL Database (General Purpose, 2 vCores; 4 vCores in Prod).

### AD-3 Documents
Uploaded documents go to Azure Blob Storage (Hot, LRS); about 500 GB in Prod.

### AD-4 Edge
Azure Front Door Standard fronts the portal in Test and Prod.

### AD-5 Secrets and monitoring
Secrets in Azure Key Vault; logs and metrics in Log Analytics.
