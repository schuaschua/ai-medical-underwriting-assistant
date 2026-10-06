# Data Flow (governance)

Shows how data moves between people, processes and stores, where it crosses a trust boundary, and where personal (PII) or health (PHI) data travels or rests. It is drawn for governance, so the markings matter more than the layout: an unmarked flow reads as "no personal data here", which is a claim.

- **External entities** (`external`, or `person` for a user role): where data enters or leaves the system.
- **Processes** (`process`): the parts that transform or act on data, usually the containers.
- **Stores** (`store`): databases, blobs, caches, logs and analytics sinks where data rests. Logs and telemetry count when a source says what they record.
- **Flows** (edges): labelled with what data moves ("chat message", "order with delivery address"), cited to where the source says it moves.
- **Trust boundaries** (`trust_boundary`): each boundary a source defines or implies by naming a network, tenant or organisation edge (public internet, the org's Azure tenant, a third-party processor). Cite the source for each.
- **PII / PHI** (`"data": ["PII"]`, `["PHI"]`, or both on a flow or store): only where a source says the data is personal or health data, or names fields that plainly are (name, email, address, medical history). Where a flow carries user content and no source classifies it, leave it unmarked and add a gap asking whether it can contain personal data.

Gaps this diagram usually turns up: whether free-text user input can carry PII; what logs and telemetry retain; retention and deletion; whether a third-party model or API receives personal data; where a trust boundary runs when the spine names two networks but not what separates them.
