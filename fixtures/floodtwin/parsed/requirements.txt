# Request for Offer MMWA-2029-DT-114 — Section 3, Requirement Specification

**Meridian Metropolitan Water Authority (MMWA)**
Flood Response Digital Twin — Technical Study

*Fictional. Written as a test fixture for automated deliverable review. Any
resemblance to a real procurement is unintended. Obligation phrasing follows
RFC 2119 keyword conventions; structure follows the conventions of public
tender documents; the architecture-description vocabulary follows the pattern
of ISO/IEC/IEEE 42010.*

---

## 3.1 Purpose and scope

The Authority requires a logical architecture for a **flood response digital
twin** covering the drainage and coastal defence network of **Port Meridian**.

**R-001.** The Contractor SHALL scope the minimum viable product to the
administrative boundary of **Port Meridian** only. Adjacent municipalities are
out of scope for the MVP and SHALL be addressed in the roadmap.

**R-002.** The MVP SHALL support two scenarios: **tidal surge** overtopping of
coastal defences, and prolonged **rainfall intensity** exceeding drainage
capacity.

**R-003.** The architecture SHALL support **evacuation** decision support as a
consumer of twin outputs. The Contractor is not required to design the
evacuation process itself.

**R-004.** The Contractor SHALL NOT assume that any third-party sensor operator
will grant write access to their telemetry platform.

## 3.2 Architecture description

**R-010.** The deliverable SHALL identify all system components, their
responsibilities, and their interfaces.

**R-011.** Every interface SHALL carry a unique identifier and appear in a
consolidated interface catalogue.

**R-012.** For every capability the architecture describes, the deliverable
SHALL name exactly one component accountable for it. Where accountability is
shared, the deliverable SHALL state the arbitration rule.

**R-013.** Where two or more components may act on the same decision, the
deliverable SHALL state which is authoritative and under what conditions.

**R-014.** The deliverable SHALL declare the simulation time base in SI units,
and SHALL state the relationship between simulation time and wall-clock time.

**R-015.** Every numeric threshold governing an automated decision SHALL carry
either a value or a named owner accountable for determining it.

## 3.3 Analysis and evidence

**R-020.** The deliverable SHALL maintain a register of open gaps, each with a
unique identifier, a named owner, and defined closure evidence.

**R-021.** The Contractor SHALL document a **trade-off analysis** for each
subsystem, recording the options considered and the basis for selection.

**R-022.** The Contractor SHALL state the **maturity** of each proposed
component against a declared scale, and SHALL identify components below
production readiness.

**R-023.** The deliverable SHALL record **risks** to delivery, each with an
owner and a mitigation.

**R-024.** Claims that evidence exists elsewhere in the deliverable SHALL
resolve to the location cited.

## 3.4 Calibration and decision-making

**R-030.** Where the Contractor proposes a calibration activity, the deliverable
SHALL identify the decision-time consumer of that calibration's outputs.

**R-031.** The alerting decision SHALL be traceable to declared inputs.

**R-032.** The Contractor SHOULD describe how calibration outputs are refreshed
as conditions change.

## 3.5 Deliverable form

**R-040.** The deliverable SHALL be issued as a single document with numbered
sections and appendices.

**R-041.** Cross-references within the deliverable SHALL resolve.

**R-042.** The Contractor MAY propose alternative structures, provided the
mapping to this specification is stated.

---

## 3.6 Data handling

**R-050.** The Contractor SHALL identify every data store, its owner, and its
retention period.

**R-051.** Personal or location-identifying telemetry SHALL be distinguished from
aggregate telemetry throughout the architecture.

**R-052.** The deliverable SHALL state how data is deleted at end of retention.

**R-053.** The architecture SHALL NOT retain raw personal telemetry beyond the
period stated under R-050.

## 3.7 Security and access

The security section SHALL include:

a. An access control model naming the roles that may read alerts

b. A statement of how service-to-service authentication is performed

c. Key and credential rotation responsibilities

d. The trust boundary between Authority systems and third-party operators

## 3.8 Non-functional requirements

The deliverable SHALL state, for each component:

a. Its availability target

b. Its recovery point and recovery time objectives

c. The load it is expected to sustain

d. Its degradation behaviour when a dependency is unavailable

## 3.9 Replay and audit

**R-070.** The architecture SHALL support deterministic replay of any past tick
range.

**R-071.** Every alert emitted SHALL be reconstructable from stored state.

**R-072.** The deliverable SHOULD describe how replay is validated against the
original run.

## 3.10 Scenario management

**R-080.** The architecture SHALL provide a scenario library with versioned
scenario definitions.

**R-081.** Each scenario SHALL declare its input data set and its expected
outputs.

**R-082.** The Contractor MAY propose a scenario authoring interface.

## 3.11 Roadmap

**R-090.** The Contractor SHALL deliver a phased implementation roadmap.

**R-091.** The roadmap SHALL identify dependencies between phases.

**R-092.** The roadmap SHALL state which capabilities are available at the end of
each phase.

**R-093.** The roadmap SHALL identify the decision points at which the Authority
must act.

---

## Annex A — Evaluation criteria

An identified gap carrying a named owner and defined closure evidence scores
**above** a silent omission. The Authority will treat the deliberate deletion of
a gap register between revisions as a reduction in disclosure, not an
improvement in completeness.
