# Sequence for a Named Flow

Shows one flow the user names, such as "one chat turn" or "checkout", as the ordered messages between its participants. A reviewer reads it to follow a single request end to end, so the order and the returns must match what the sources say.

- **The flow must be described in a source.** Find where the spine, spec or UX docs walk through it (a flow section, a UX journey, the ADs covering each step) and name that in the diagram's title or first cite. If no source describes the flow, say so, list what's missing as gaps, and don't assemble it from fragments.
- **Participants**: the people, containers, services and external systems the flow touches, as kinds `person`, `system`, `system_ext`, `container`, `container_ext`, `service` (an Azure resource) or `external` (a third party). The build places them left to right in the order they first take part.
- **Messages**, in order: each call or event, labelled with what is asked or sent ("POST /messages", "retrieve context"), cited to the step's source. A reply is its own message with `"return": true`, drawn only when the source states what comes back. Mark `data` (`PII`, `PHI`) only where a source says the data is personal or health data, or names fields that plainly are (name, email, address, medical history); where a message carries user content no source classifies, leave it unmarked and add a gap asking whether it can contain personal data, because an unmarked message reads as a claim that none flows.
- **Branches and loops**: draw the main path the source describes. Alternatives the source mentions go in the gaps as "not drawn: <alternative>", so the reader knows they exist.

Gaps this diagram usually turns up: timeouts and retries; what the user sees on failure; whether a step is synchronous; steps the UX describes that no AD assigns to a container.
