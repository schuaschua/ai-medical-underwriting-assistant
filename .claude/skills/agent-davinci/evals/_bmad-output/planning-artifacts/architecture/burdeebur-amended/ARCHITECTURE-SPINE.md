# burdeebur architecture spine (amended)

Sample spine for testing arch-diagrams, written from the owner's description only.

## Decisions

### AD-1 Hosting on AKS
The tech stack is hosted on Azure Kubernetes Service (AKS). The AKS cluster runs the front-end (React) and the back-end service (.NET). Cosmos DB is separate from the AKS cluster.

### AD-2 Autoscaling
Where possible, the front-end, the back-end and Cosmos DB all have autoscaling.

### AD-3 Multi-region
This is a multi-region architecture.

### AD-4 Global distribution
Azure Front Door handles the distribution of traffic.

### AD-5 Domain
Azure DNS handles the domain, burdeebur.com.

### AD-6 Access
Customers and administrators access the app: customers through the usual URL (https://burdeebur.com), administrators through /admin (https://burdeebur.com/admin). Analysts, a third set of users, connect to Power BI to visualise the data.

### AD-7 Network separation
In each region, the AKS cluster and Cosmos DB are in separate subnets of the region's virtual network.

### AD-8 On-premises ETL
An on-premises system connects to the data layer (Cosmos DB). It uses Azure Data Factory to extract, transform and load the data into an on-premises data warehouse.

### AD-9 Analytics
Power BI is used on top of the on-premises data warehouse for analytics.
