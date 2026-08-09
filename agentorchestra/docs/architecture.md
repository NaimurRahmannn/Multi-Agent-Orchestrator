# Architecture

The governing rule is: **Manager decides what should run. Flow decides what actually runs.** Models propose plans or patches; trusted application services own execution and promotion.

## Components

```mermaid
flowchart TB
    UI[Streamlit UI] --> FLOW[CrewAI AgentOrchestraFlow]
    CLI[CLI entry points] --> FLOW
    FLOW --> MAN[Manager: routing only]
    MAN --> FLOW
    FLOW --> HTML[HTML specialist]
    FLOW --> CSS[CSS specialist]
    FLOW --> SEO[SEO specialist]
    HTML --> TOOLS[Workspace-bound read / exact patch]
    CSS --> TOOLS
    SEO -->|edit| TOOLS
    SEO -->|diagnostic| READ[Bound read only]
    TOOLS --> STAGE[sites/staging/run_id]
    READ --> STAGE
    FLOW --> SHOT[Playwright screenshots]
    FLOW --> LH[Lighthouse SEO only]
    FLOW --> QA[Tool-free QA]
    FLOW --> PROMO[Transactional promotion / reset]
    PROMO --> WORK[sites/working]
    FIX[sites/fixture] -->|reset source| PROMO
```

## Edit lifecycle

```mermaid
sequenceDiagram
    actor User
    participant Flow
    participant Manager
    participant Stage
    participant Specialist
    participant Lighthouse
    participant Screenshots
    participant QA
    participant Working

    User->>Flow: validated request
    Flow->>Manager: route only
    Manager-->>Flow: plan, assignments, criteria
    Flow->>Stage: copy working
    Flow->>Screenshots: capture Before
    Flow->>Specialist: run selected roles sequentially
    Specialist->>Stage: bound exact patches
    opt SEO selected
        Flow->>Lighthouse: SEO-only staged audit
    end
    Flow->>Flow: validate diff, evidence, content digest
    Flow->>Screenshots: capture proposed-after
    Note over Screenshots,QA: Screenshots are not QA evidence
    Flow->>QA: deterministic evidence bundle
    QA-->>Flow: criterion results and verdict
    alt accepted
        Flow->>Working: transactional promotion
    else rejected or failed
        Flow->>Stage: discard
    end
    Flow-->>User: structured report, timeline, metrics
```

SEO diagnostic mode branches after Lighthouse: it returns findings, skips proposed screenshot/QA/promotion, cleans staging, and leaves working unchanged.

## Agent ownership and tools

```mermaid
flowchart LR
    M[Manager<br/>routing only] -->|no tools| NONE1[ ]
    H[HTML<br/>structure and attributes] --> RP[read_file + propose_patch<br/>target HTML]
    C[CSS<br/>visual presentation] --> RC[read HTML/CSS + patch style.css]
    S[SEO edit<br/>metadata] --> RS[read + patch target HTML]
    SD[SEO diagnostic<br/>source findings] --> RO[read_file only]
    Q[QA<br/>evidence evaluation] -->|no tools| NONE2[ ]
```

All agents use `allow_delegation=False`. Manager never reads files or invokes specialists. QA is never a selectable specialist.

## File lifecycle

```mermaid
flowchart TD
    FIX[fixture] -->|transactional reset source| WORK[working]
    WORK -->|copy| STAGE[staging/run_id]
    STAGE --> EDIT[specialist edits]
    EDIT --> REVIEW[validated diff + QA]
    REVIEW -->|accept| CAND[candidate]
    WORK -->|rename| BACK[backup]
    CAND -->|verified install| WORK2[working]
    WORK2 -->|digest verified| CLEAN[clean staging/candidate/backup]
    REVIEW -->|reject / block / fail| DISCARD[discard staging]
    BACK -->|commit failure: restore + verify| WORK
    BACK -->|unverified restore| CRIT[critical recovery; preserve paths]
```

Candidate and backup paths are server-generated and constrained to the application root. Digest equality complements exact diff equality.

## Evidence model

```mermaid
flowchart LR
    CRIT[Manager acceptance criteria] --> BUNDLE[QA evidence bundle]
    PATCH[Applied/rejected patch evidence] --> BUNDLE
    DIFF[Deterministic unified diff] --> BUNDLE
    LH[Optional normalized Lighthouse SEO] --> BUNDLE
    DIGEST[Site + evidence digests] --> BUNDLE
    BUNDLE --> QA[QA criterion evaluation]
    QA --> RESULT[accept or reject]
    SHOT[Screenshots] -. presentation only .-> UI[UI]
    UI -. excluded .-> BUNDLE
```

QA receives normalized evidence, not raw provider responses or raw Lighthouse JSON. An SEO score cannot prove unrelated HTML/CSS criteria or ranking improvement.

## Actual Flow transitions

```mermaid
flowchart TD
    START[plan_request @start] --> ROUTE{route_manager_plan}
    ROUTE -->|clarification| FC[finalize_clarification]
    ROUTE -->|out_of_scope| FO[finalize_out_of_scope]
    ROUTE -->|executable| WS[create_workspace]
    ROUTE -->|failed| FF[finalize_failed]
    WS --> RWS{route_workspace_result}
    RWS -->|workspace_ready| BEFORE[capture_before_screenshot]
    RWS -->|failed| FF
    BEFORE --> RBS{route_before_screenshot}
    RBS -->|specialists_ready| SPEC[execute_specialists]
    RBS -->|failed| FF
    SPEC --> RS{route_specialist_result}
    RS -->|blocked| FB[finalize_blocked]
    RS -->|failed| FF
    RS -->|verification_ready| LH[run_seo_verification]
    LH --> RLH{route_seo_verification}
    RLH -->|diagnostic_ready| FD[finalize_seo_diagnostic]
    RLH -->|evidence_ready| EV[validate_and_build_qa_evidence]
    RLH -->|failed| FF
    EV --> REV{route_evidence_result}
    REV -->|screenshot_ready| PROP[capture_proposed_screenshot]
    REV -->|failed| FF
    PROP --> RPS{route_proposed_screenshot}
    RPS -->|qa_ready| QA[execute_qa]
    RPS -->|failed| FF
    QA --> RQA{route_qa_verdict}
    RQA -->|accepted| PROMO[promote_and_finalize]
    RQA -->|rejected| FR[finalize_rejected]
    RQA -->|failed| FF
```

Screenshot failures normally become warnings and the transition continues; safety failures can stop execution.

## Security boundaries

- Trusted code creates workspace handles, hides specialist identity, and binds allowed files.
- A deterministic patch policy rejects active scripting, cross-owned HTML/SEO/presentation
  changes, and CSS imports or active/external URL references before staged bytes are replaced.
- LLM-visible tool arguments contain only validated relative file names, bounded ranges, and exact replacement text.
- Absolute paths, traversal, unsupported extensions, structure drift, assets changes, and symlinks are rejected.
- Manager, specialist, and QA keys/models are resolved independently with no role fallback.
- Preview and screenshot networking is loopback-only; agents receive no shell or browser tool.
- Promotion revalidates the reviewed diff, evidence digest, and content digest immediately before replacement.

## Failure behavior

- Zero-match, multiple-match, no-op, unauthorized, or oversized patches are rejected with deterministic evidence.
- A blocked/failed specialist stops later specialists and discards staging.
- Lighthouse failure stops SEO verification without crashing the UI.
- A normal screenshot failure is a warning; screenshots never decide QA.
- QA rejection discards staging and preserves working.
- Promotion failure restores and verifies the original working tree.
- An unverified rollback raises critical recovery and preserves named recovery material.
- A successful commit with cleanup failure remains accepted with explicit cleanup warnings.
