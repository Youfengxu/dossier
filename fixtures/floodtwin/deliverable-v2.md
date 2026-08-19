# Flood Response Digital Twin — System Design Description

*Generated from deliverable-v1.md by make-v2.py — do not edit directly.*


**Tidewater Systems Pte Ltd** · Prepared for Meridian Metropolitan Water Authority
Document version 2.0 · Response to RFO MMWA-2029-DT-114

*Fictional test fixture. Defects are planted deliberately; see ground-truth.yaml.*

---

## 1. Document control

Document version 2.0. Supersedes 1.4.

"This deliverable is a logical architecture baseline and engineering contract
inventory. It defines system boundaries, ownership, data and control flow,
proposed interfaces, validation structure, and the decisions needed to proceed.
It is not an implementation-ready technical design or a deployment
specification."

Status legend for open items: **Open** (owner assigned, evidence defined),
**TBD** (no owner yet), **Hold** (blocked on an external dependency). Full
status definitions are in Section 14 and Appendix F.

## 2. Introduction

The twin models the drainage and coastal defence network of the metropolitan
area, supporting two operating scenarios: coastal overtopping of sea defences,
and sustained precipitation rate in excess of drainage capacity. Both scenarios
drive the same downstream decision surface.

The architecture is component-oriented. Six components are described: the
Runtime Orchestrator, the Sensor Fabric, the Hydrology Model, the Alerting
Service, the Calibration Pipeline, and the Platform Adapter.

## 3. Stakeholders and concerns

| Stakeholder | Concern |
|---|---|
| Authority operations | Time to alert under load |
| Authority engineering | Model fidelity against gauge records |
| Third-party sensor operators | Read-only access to their telemetry |
| Emergency services | Downstream consumption of twin outputs |

Emergency services are treated as an external consumer. Interfaces to their
systems are out of scope for this revision.

## 4. Component overview

| ID | Component | Responsibility |
|---|---|---|
| RO | Runtime Orchestrator | Execution scheduling, tick advancement, state commit |
| SF | Sensor Fabric | Telemetry ingest, quality flags, gap filling |
| HM | Hydrology Model | Hydraulic state estimation and forward projection |
| AS | Alerting Service | Threshold evaluation and alert emission |
| CP | Calibration Pipeline | Parameter estimation against historical events |
| PA | Platform Adapter | Integration with third-party telemetry platforms |

## 5. Runtime Orchestrator

### 5.1 Responsibility

The Runtime Orchestrator advances simulation state one tick at a time. Each tick
proceeds through ingest, estimation, evaluation, and commit phases.

### 5.2 Scheduling authority

**The Runtime Orchestrator is authoritative for execution scheduling.** It
constructs the per-tick execution order, freezes it before the ingest phase
begins, and commits the resulting state at end of tick. No component may modify
a frozen schedule.

### 5.3 Time base

Simulation advances in ticks. Wall-clock time must not govern simulation
advancement; a tick completes when all components have reported, not when a
wall-clock interval elapses. This decouples the twin from host performance
variation and makes replay deterministic.

### 5.4 State vector

Each modelled node carries a state vector s(i,t), defined below.

s(i,t) comprises: water level, flow rate, gate position, sensor confidence,
saturation index, upstream inflow, downstream head, and defence status.

### 5.5 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| RO-G01 | Tick budget under peak sensor load unproven | RO lead | Load test report |
| RO-G02 | Commit ordering under partial component failure | RO lead | Failure-mode analysis |
| RO-G03 | Replay determinism across host classes | RO lead | Replay diff report |
| RO-G04 | Checkpoint and branch semantics | RO lead | Design note |
| RO-G05 | Backpressure policy when SF lags | RO lead | Policy statement |
| RO-G06 | Authorisation model for schedule override | RO lead | Security review |

## 6. Sensor Fabric

### 6.1 Responsibility

The Sensor Fabric ingests telemetry from fixed gauges, tide boards, and
third-party platforms via the Platform Adapter. It applies quality flags and
fills short gaps by interpolation.

### 6.2 Sensor lifecycle

Sensors transition between commissioned, active, degraded, dormant, and
decommissioned states. Reactivation of a dormant sensor is modelled as an
exogenous event: the fabric does not decide reactivation, it observes it.
Reactivation restores the prior calibration offset unless the dormancy exceeded
the revalidation window. Reactivation events are logged and are replayable.

### 6.3 Disagreement between sensors

Where two sensors covering the same reach report incompatible levels, the fabric
flags the disagreement and defers resolution to the Hydrology Model, which holds
the physical constraint set needed to adjudicate.

### 6.4 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-SF-001 | Gauge telemetry ingest | in |
| IF-SF-002 | Quality-flagged observation stream | out |
| IF-SF-003 | Sensor lifecycle events | out |
| IF-SF-004 | Gap-fill provenance | out |

### 6.5 Implementation constraints and dependencies

Constraints for this component are tracked in the delivery plan.

## 7. Hydrology Model

### 7.1 Responsibility

The Hydrology Model estimates hydraulic state from flagged observations and
projects forward over the alerting horizon.

### 7.2 Disagreement handling

Where the Sensor Fabric reports a disagreement, the model incorporates both
readings with reduced confidence and refers the disagreement to the Alerting
Service, which holds the operational policy governing which reading to prefer.

### 7.3 Estimation

State estimation minimises residual against the physical constraint set.
Candidate solvers are catalogued but not selected; selection remains open.

### 7.4 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-HM-001 | Flagged observation intake | in |
| IF-HM-002 | Estimated state publication | out |
| IF-HM-003 | Projection series publication | out |

### 7.5 Implementation constraints and dependencies

Constraints for this component are tracked in the delivery plan.

## 8. Alerting Service

### 8.1 Responsibility

The Alerting Service evaluates estimated and projected state against thresholds
and emits alerts to Authority operations.

### 8.2 Thresholds

Alerting uses fixed thresholds held in service configuration. The surge
threshold θ_surge governs emission of the coastal overtopping alert.

### 8.3 Disagreement handling

Where the Hydrology Model refers a sensor disagreement, the service applies no
independent resolution. Raw sensor disagreement is a data-quality matter and the
Sensor Fabric is authoritative for it.

### 8.4 Evaluation order

**The Alerting Service determines the evaluation order for pending alerts**,
reordering as necessary so that higher-severity candidates are evaluated first
within the tick.

### 8.5 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-AS-001 | Estimated and projected state intake | in |
| IF-AS-002 | Alert emission | out |

### 8.6 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| AS-G01 | Threshold values not set | AS lead | Authority decision |
| AS-G02 | Alert suppression policy | AS lead | Policy statement |
| AS-G03 | Escalation path on repeated emission | AS lead | Runbook |
| AS-G04 | Downstream consumer contract | AS lead | Interface agreement |

## 9. Deployment topology

The twin is deployed as six services on the Authority's private cloud tenancy,
with the Platform Adapter in a separate network zone. Two environments are
provided: an operational environment and a replay environment sharing the same
image set. Horizontal scaling applies to the Sensor Fabric only.

Component maturity is assessed in Section 9.

## 10. Calibration Pipeline

### 10.1 Responsibility

The Calibration Pipeline estimates model parameters against historical event
records. It produces a four-dimensional calibration vector per reach, comprising
roughness, storage coefficient, lag, and defence permeability.

### 10.2 Method

Parameters are fitted by least squares against gauge records for the eleven
recorded events in the historical archive. Fitted vectors are versioned and
stored with the event set that produced them.

### 10.3 Refresh

Calibration is re-run when a new event is added to the archive, or annually,
whichever is sooner.

### 10.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| CAL-G-01 | Historical archive completeness | Authority | Archive audit |
| CAL-G-02 | Fitting method not benchmarked | CP lead | Benchmark report |
| CAL-G-03 | Vector versioning scheme | CP lead | Design note |
| CAL-G-04 | Treatment of ungauged reaches | CP lead | Method note |
| CAL-G-05 | Acceptance criteria for a fitted vector | CP lead | Criteria statement |

## 11. Platform Adapter

### 11.1 Responsibility

The adapter integrates third-party telemetry platforms. Access is read-only in
all cases; no write path to an operator platform is proposed.

### 11.2 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-PA-001 | Operator telemetry pull | in |
| IF-PA-002 | Operator availability signal | in |
| IF-PA-003 | Adapter health | out |

Retry and backoff behaviour for operator endpoints is specified under IF-PA-014.

### 11.3 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| PA-G01 | Operator agreements not executed | Authority | Signed agreements |
| PA-G02 | Credential rotation approach | PA lead | Security note |
| PA-G03 | Behaviour on operator outage | PA lead | Design note |

## 12. Algorithms

Per-tick sequence:

1. RO freezes the execution order for the tick.
2. SF ingests telemetry and applies quality flags.
3. AS reorders the frozen execution order by candidate severity and evaluates.
4. HM estimates state and publishes projections, damped by the stability
   factor k.
5. RO commits state and advances the tick.

Rationale and assumptions for each step are recorded per component above.
Solver and gap-fill candidates are catalogued; selections remain open under
CAL-G-07.

## 13. Validation structure

Validation is performed by an independent Validation Authority nominated by the
Authority. The Validation Authority reviews estimation output against withheld
gauge records and issues a validation statement per release.

## 15. Traceability summary

Requirements R-001 through R-042 are addressed across Sections 2 to 13. A
detailed mapping is provided in Appendix A.

## 16. Ingest Gateway

### 16.1 Responsibility

The Ingest Gateway terminates inbound telemetry sessions, applies rate limiting,
and forwards accepted payloads to the Sensor Fabric. It is the only component
with a network path to third-party operators.

### 16.2 Session handling

Sessions are long-lived. A session that stalls beyond the stall window is torn
down and re-established. The stall window is operator-specific.

### 16.3 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-IG-001 | Operator session intake | in |
| IF-IG-002 | Accepted payload forwarding | out |
| IF-IG-003 | Session lifecycle events | out |

### 16.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| IG-G01 | Stall window per operator not agreed | IG lead | Operator schedule |
| IG-G02 | Rate limit values not set | IG lead | Load analysis |
| IG-G03 | Behaviour on partial payload | IG lead | Design note |

## 17. Scenario Library

### 17.1 Responsibility

The Scenario Library holds scenario definitions used for exercises and for
regression runs against the twin. Definitions are versioned; a run records the
scenario version it used.

### 17.2 Scenario content

A scenario names a starting state, a sequence of forcing events, and a duration.
Expected outputs are not held in the library; they are produced by the run and
compared by hand.

### 17.3 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-SL-001 | Scenario definition retrieval | out |
| IF-SL-002 | Scenario version pinning | out |

### 17.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| SL-G01 | Authoring workflow not designed | SL lead | Design note |
| SL-G02 | Storage format not selected | SL lead | Format decision |

## 18. Replay Service

### 18.1 Responsibility

The Replay Service reconstructs any past tick range from the committed state log
and re-executes it deterministically. Replay uses the same image set as the
operational environment.

### 18.2 Determinism

Replay determinism depends on the Runtime Orchestrator freezing execution order
before ingest. Any component that reorders within a tick breaks replay.

### 18.3 Validation of replay

Replay output is compared with the original run. The comparison method is
described in the delivery plan.

### 18.4 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-RS-001 | State log read | in |
| IF-RS-002 | Replay execution control | in |
| IF-RS-003 | Replay output publication | out |

### 18.5 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| RS-G01 | State log retention insufficient for long replays | Authority | Retention decision |
| RS-G02 | Divergence tolerance not defined | RS lead | Tolerance statement |

## 19. Reporting

### 19.1 Responsibility

Reporting produces post-run summaries for Authority operations: alerts emitted,
thresholds crossed, and reach-level peak levels.

### 19.2 Alert reconstruction

A report can restate which alerts were emitted and when. The inputs that caused
a given alert are not carried into the report; an analyst wanting them consults
the state log directly.

### 19.3 Interfaces

| ID | Interface | Direction |
|---|---|---|
| IF-RP-001 | Run summary generation | out |
| IF-RP-002 | Report distribution | out |

### 19.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| RP-G01 | Report template not agreed | Authority | Template sign-off |
| RP-G02 | Distribution list not defined | Authority | Distribution policy |

## 20. Identity and Access

### 20.1 Access control model

Three roles are defined: Operator, Analyst, and Administrator. Operators read
alerts and acknowledge them. Analysts read alerts, reports and the state log.
Administrators additionally manage scenario definitions.

### 20.2 Service-to-service authentication

Components authenticate to one another using short-lived tokens issued by the
platform identity provider. Tokens are scoped to the calling component.

### 20.3 Credential rotation

Credential and key rotation responsibilities remain open under IA-G02.

### 20.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| IA-G01 | Identity provider not selected | IA lead | Product decision |
| IA-G02 | Rotation responsibilities unassigned | IA lead | RACI |

## 21. Data stores and retention

### 21.1 Stores

| Store | Contents | Owner |
|---|---|---|
| Telemetry archive | Raw gauge and operator readings | SF lead |
| State log | Committed per-tick state | RO lead |
| Calibration store | Fitted vectors and their event sets | CP lead |
| Scenario store | Scenario definitions | SL lead |
| Report store | Generated run summaries | RP lead |

### 21.2 Retention

The telemetry archive retention period remains open under SF-G05. Other stores
retain indefinitely.

### 21.3 Deletion

Deletion behaviour at end of retention is described in the delivery plan.

## 22. Non-functional characteristics

### 22.1 Availability

The operational environment targets 99.5% monthly availability. The replay
environment has no availability target.

### 22.2 Load

The Sensor Fabric is sized for the current gauge population plus fifty percent.
Other components are not separately sized.

### 22.3 Degradation

Where the Platform Adapter is unavailable, the Sensor Fabric continues on fixed
gauges alone and marks affected reaches as degraded.

### 22.4 Implementation constraints and dependencies

| ID | Gap | Owner | Closure evidence |
|---|---|---|---|
| NF-G01 | Recovery objectives not set | Authority | Continuity requirement |
| NF-G02 | Per-component load targets absent | Architecture lead | Sizing note |

## 23. Trust boundaries

Operator platforms are outside the Authority trust boundary. The Ingest Gateway
is the only component that crosses it, and it accepts inbound sessions only.

## 24. Precipitation handling

Sustained precipitation rate above drainage capacity is modelled as a forcing
input to the Hydrology Model. Intensity is expressed per reach per tick.

## 25. Run report handoff

### 25.1 Reporting responsibility

**Reporting produces the RunSummary** at end of run, drawing on the state log and
the alert ledger.

### 25.2 Operations responsibility

**Authority operations evaluates the RunSummary** against the exercise
objectives and decides whether a re-run is required.


---

## Appendix A — Requirement mapping

| Requirement | Addressed in |
|---|---|
| R-010 | Section 4 |
| R-011 | Sections 6.4, 7.4, 8.5, 11.2 |
| R-012 | Sections 5 to 11 |
| R-013 | Sections 5.2, 8.4 |
| R-014 | Section 5.3 |
| R-015 | Section 8.2 |
| R-020 | Sections 5.5, 6.5, 7.5, 8.6, 10.4, 11.3 |
| R-030 | Section 10 |
| R-031 | Section 8 |
| R-040 | This document |
| R-041 | Appendix A |
| R-050 | Section 21 |
| R-051 | Section 21 |
| R-070 | Section 18 |
| R-080 | Section 17 |

## Appendix C — Consolidated interface catalogue

IF-SF-001, IF-SF-002, IF-SF-003, IF-SF-004, IF-HM-001, IF-HM-002, IF-HM-003,
IF-AS-001, IF-AS-002, IF-RO-001, IF-RO-002, IF-PA-001, IF-PA-002, IF-PA-003.

IF-RO-001 is the tick advancement signal published by the Runtime Orchestrator
to all components; IF-RO-002 is the state commit notification.

