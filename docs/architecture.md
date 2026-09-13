# Architecture

## Previous foundation

```mermaid
flowchart LR
    client[HTTP client] --> api[FastAPI]
    api --> service[Task service]
    service --> db[(PostgreSQL)]
    worker[Task worker] -->|claim queued task| db
    worker --> fake[Hard-coded fake executor]
    fake --> validation[Output validation]
    validation --> service
    service -->|ordered events| db

    classDef boundary fill:#edf2f7,stroke:#64748b,color:#0f172a
    classDef runtime fill:#dbeafe,stroke:#2563eb,color:#172554
    classDef persistence fill:#dcfce7,stroke:#16a34a,color:#14532d
    class client boundary
    class api,service,worker,fake,validation runtime
    class db persistence
```

The task lifecycle is reliable, but executor selection is embedded directly in
the worker. Adding Kimi, a model, or a tool would require editing worker logic.

## Current architecture

```mermaid
flowchart LR
    client[HTTP client] --> api[FastAPI]
    api --> service[Task service]
    service --> db[(PostgreSQL task and event store)]

    manifests[YAML capability manifests] --> registry[Capability registry]
    registry --> capapi[Capabilities API]
    registry --> manager[Deterministic manager]

    worker[Task worker] -->|claim task| db
    worker --> manager
    manager -->|typed delegate decision| worker
    worker --> adapters{Adapter resolver}
    adapters --> fake[Fake executor enabled]
    adapters -. later .-> kimi[Kimi Code disabled]
    adapters -. later .-> models[Model adapters]
    adapters -. later .-> tools[Tool adapters]

    fake --> validation[Output validation]
    validation --> service
    service -->|ordered events| db

    classDef boundary fill:#edf2f7,stroke:#64748b,color:#0f172a
    classDef current fill:#dbeafe,stroke:#2563eb,color:#172554
    classDef new fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef persistence fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef later fill:#f3e8ff,stroke:#9333ea,color:#3b0764,stroke-dasharray:5 5
    class client boundary
    class api,service,worker,fake,validation current
    class manifests,registry,capapi,manager,adapters new
    class db persistence
    class kimi,models,tools later
```

This capability-driven layer is now implemented. The manager returns only
schema-validated actions. Application code still owns state transitions,
adapter availability, cancellation, and validation. Kimi is registered as a
disabled future capability and cannot be selected until its adapter is built
and the manifest is explicitly enabled.

## Manager-model boundary

```mermaid
flowchart LR
    task[Task without explicit requirements] --> assisted[Model-assisted manager]
    assisted --> adapter[Validated manager-model adapter]
    adapter --> prompt[Prompt plus capability catalog and JSON schema]
    prompt --> client{Provider client}
    client --> scripted[Scripted test client]
    client -. later .-> provider[Real model provider]
    scripted --> raw[Raw JSON response]
    provider -.-> raw
    raw --> guard[Schema and capability allowlist validation]
    guard --> analysis[TaskAnalysis]
    analysis --> policy[Deterministic routing policy]
    policy --> registry[Capability registry]
    registry --> decision[Typed manager outcome]

    explicit[Task with explicit requirements] --> policy

    classDef implemented fill:#dbeafe,stroke:#2563eb,color:#172554
    classDef later fill:#f3e8ff,stroke:#9333ea,color:#3b0764,stroke-dasharray:5 5
    class task,assisted,adapter,prompt,client,scripted,raw,guard,analysis,policy,registry,decision,explicit implemented
    class provider later
```

The model only infers `TaskAnalysis`; it never chooses an adapter or command.
Explicit requirements bypass inference. Malformed or invented outputs receive
one schema-repair attempt and then become a safe, audited failure. The real
provider connection remains a separate future adapter.
