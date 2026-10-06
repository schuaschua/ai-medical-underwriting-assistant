# Low-Level Design

<!-- Org default outline (kit 1.5.0). A project replaces it by putting its own lld.md in
<design_docs_folder>/templates/. An LLD covers one component or the whole system (its "scope");
the HLD comes first and the LLD refers to it rather than repeating it. -->

## Introduction and Scope
The component(s) this LLD covers, their place in the HLD, and what this document leaves to others.

## Component Design
For each component in scope: responsibilities, internal structure (modules, layers), technology and versions, and the ADs behind them.

## Interfaces and APIs
Each endpoint or operation: method and path (or message and topic), request and response, errors, authentication and authorisation, versioning.

## Data Model
Entities or documents, their fields, types, keys, indexes, relationships, classification (PII/PHI) and retention.

## Key Flows
Each significant flow step by step, with its sequence diagram.
Diagram: sequence

## Error Handling and Resilience
Validation, error responses, retries, timeouts, idempotency, fallbacks and what happens when a dependency is down.

## Security Implementation
How authentication, authorisation, input validation, secrets and encryption are applied in the components in scope.

## Configuration and Environments
Settings, feature flags and per-environment values, and where each is stored.

## Deployment Details
Resources, SKUs and sizes, scaling rules, deployment steps and the pipeline.
Diagram: deployment

## Logging, Monitoring and Alerting
What is logged (and what never is), metrics, health checks, dashboards and alerts.

## Testing Approach
Unit, integration, end-to-end and non-functional tests for the components in scope. Source: test design, spec.
