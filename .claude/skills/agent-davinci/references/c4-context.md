# C4 System Context

Shows the system as one box among the people who use it and the other systems it depends on or serves. A reviewer reads it to learn the system's edges: who touches it, and what it trusts beyond itself.

- **The system** (`system`): one box, cited to the AD or spec section that defines its purpose. Its internals belong on the containers diagram.
- **People** (`person`): user roles the spec or UX docs name (customer, agent, administrator), cited to where each role is stated. Roles only, never named individuals.
- **External systems** (`system_ext`): every third-party service, platform API or org system a source says the system calls or is called by, such as identity providers, payment gateways, model APIs and the org's CRM.
- **Relationships**: a person or system to the system, labelled with what flows or is asked for ("asks questions", "sends order events"), cited to where the interaction is stated. Leave out protocols; they belong on the containers diagram.
- **Boundary** (`boundary`), optional: the organisation or enterprise edge when a source draws one.

Gaps this diagram usually turns up: external systems named without saying which direction the calls go; user roles in the UX docs that the spine never mentions; an integration the spec requires and no AD owns.
