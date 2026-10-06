# C4 Containers

Zooms into the system: the separately deployable or runnable parts (apps, APIs, functions, workers, databases, queues) and how they talk. A reviewer reads it to see where logic and data live, and how requests cross between them.

- **Containers** (`container`): each app, API, function app, worker or single-page app a source names as a part of this system, with its technology in `tech` when a source states it (".NET 8", "React"). A technology no source states stays blank; it isn't guessed from the rest of the stack.
- **Data stores** (`database`): each database, cache, blob store or search index a source names, with its product in `tech` when stated.
- **Queues and topics**: a `container` with the product in `tech` ("Azure Service Bus topic").
- **External systems** (`system_ext`, or `container_ext` for an external API the system calls directly) and **people** (`person`), carried over from the context diagram where they touch a container.
- **Boundary** (`boundary`): the system's boundary, around its own containers; external systems and people sit outside it.
- **Relationships**: container to container, labelled with the purpose ("reads conversation history") and the protocol in `tech` when stated ("HTTPS/JSON", "AMQP"). Only interactions a source states; `dashed` for asynchronous messaging when the source says it is asynchronous.

Gaps this diagram usually turns up: a store named without the container that owns it; synchronous or asynchronous not stated; containers the epics imply that the spine never names; the protocol between two parts.
