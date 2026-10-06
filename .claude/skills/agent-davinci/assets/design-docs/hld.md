# High-Level Design

<!-- Org default outline (kit 1.5.0). A project replaces it by putting its own hld.md in
<design_docs_folder>/templates/. Each "## " heading is a section of the document, "### " a subsection;
the text under a heading tells Da Vinci what belongs there and where it comes from, and is not copied
into the document. "Diagram:" names the diagram the section shows (drawn first if it doesn't exist). -->

## Introduction
Purpose of the document, the solution in two or three sentences, scope (in and out), and the audience. Source: spec.

## Business Context
The problem, the users and their roles, the business capabilities the solution provides, and the success measures. Source: spec, UX.

## Architecture Overview
The architecture principles and the key decisions, one line each with its AD. The system in its context: users and external systems.
Diagram: c4-context

## Solution Components
Each container or component: its responsibility, technology and the AD that decides it; how they interact.
Diagram: c4-containers

## Integrations and Interfaces
Each external system: direction, protocol, data exchanged, authentication, and who owns it.

## Data Architecture
Data stores and what each holds, data classification (PII/PHI), residency, retention and backup, and the trust boundaries data crosses.
Diagram: data-flow

## Security Architecture
Identity and access, authorisation model, secrets management, network protection, encryption in transit and at rest, and admin access.

## Infrastructure and Deployment
Hosting platform, regions, environments, networking, scaling and the deployment pipeline.
Diagram: deployment

## Non-Functional Requirements
Availability, performance, scalability, disaster recovery (RTO and RPO), accessibility and compliance, each with its target and the AD or section that sets it.

## Operations
Monitoring, logging, alerting, support model and runbooks.

## Risks, Assumptions and Constraints
Architecture risks with their mitigation, assumptions the design relies on, and constraints it must respect.
