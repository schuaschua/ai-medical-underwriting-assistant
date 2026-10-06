# burdeebur architecture spine

Sample spine for testing arch-diagrams, written from the owner's description only.

## Decisions

### AD-1 Hosting on AKS
The tech stack is hosted on Azure Kubernetes Service (AKS). AKS has a front-end (React), a back-end service (.NET) and a Cosmos DB.

### AD-2 Autoscaling
Where possible, the front-end, the back-end and Cosmos DB all have autoscaling.

### AD-3 Multi-region
This is a multi-region architecture.

### AD-4 Global distribution
Azure Front Door handles the distribution of traffic.

### AD-5 Domain
Azure DNS handles the domain, burdeebur.com.

### AD-6 Access
Customers and administrators access the app: customers through the usual URL (https://burdeebur.com), administrators through /admin (https://burdeebur.com/admin).
