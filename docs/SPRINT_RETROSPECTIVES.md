# Sprint Retrospectives

**Project:** MediNest:Priority-Based Medicine Booking and Allocation Module for Pharma Apps  
**Project ID:** 47  
**Department:** Computer Science and Engineering, PES University, Bangalore  
**Record prepared:** 16 November 2026  
**Time zone:** Asia/Kolkata

| Team member | SRN |
|---|---|
| Paturu Naga Jignas | PES2UG24AM110 |
| Namit Krishna Renjith | PES2UG24AM097 |
| Mayank Kedia | PES2UG24AM088 |
| Mrudul M | PES2UG24AM094 |

## Review basis

This record reviews the scheduled sprint ends and the final local implementation. It was compiled from the original SRS, Jira board 35, complete status changelogs for MBA-6 to MBA-23, Git history and the verification report. It is not a record of meetings held on the earlier dates. Attendance, anonymous feedback, voting, safety scores and Return on Time Invested (ROTI) scores were not recorded in the available material.

Jira contains four weekly sprints. All four are covered below. The 27 October and 7 November checkpoints also summarize the first and second halves of the development plan.

## Sprint schedule and recorded completion

| Sprint | Scheduled period | Scheduled review | Delivery items | Recorded Done at scheduled end | Done at final review |
|---|---|---|---:|---:|---:|
| Week 1 Foundations | 13-20 October 2026 | 20 October, 10:00 IST | 4 | 0 | 4 |
| Week 2 Booking and Priority | 20-27 October 2026 | 27 October, 10:00 IST | 5 | 0 | 5 |
| Week 3 Allocation and Waitlist | 27 October-3 November 2026 | 3 November, 10:00 IST | 5 | 0 | 5 |
| Week 4 Hardening and Demo | 3-7 November 2026 | 7 November, 23:59 IST | 4 | 0 | 4 |

These are issue counts, not story points. The first recorded Done transitions occurred on 14-16 November. A missing Done transition does not prove that no implementation work existed; it means Jira did not record completion by the scheduled end. Story-point estimates, velocity, burndown and escaped-defect counts are unavailable and are not estimated here.

## 1 Week 1 Foundations

**Scheduled review:** 20 October 2026  
**Goal:** establish the prescription data model, upload validation, external service boundaries and access controls.

| Issue | Work item | Owner | First recorded Done |
|---|---|---|---|
| MBA-6 | Prescription and medicine-request models | Paturu Naga Jignas | 15 November |
| MBA-7 | Upload validation and manual-review states | Namit Krishna Renjith | 14 November |
| MBA-8 | External service contracts and mock adapters | Mrudul M | 16 November |
| MBA-9 | Role-based access and patient data protection | Mayank Kedia | 16 November |

**What the design established:** the SRS separates prescription eligibility, patient ownership and service interfaces. This provides a clear basis for later booking and allocation work.

**Issue identified:** completion evidence was not linked to the scheduled sprint end. Access control also needs to cover record ownership, not only a role label.

**Follow-through:** retain tests for patient/prescription relationships, upload constraints and object-level authorization. These checks are present in the final automated suite. Closing the corresponding sprint in Jira remains an administrative follow-up.

## 2 Week 2 Booking and Priority

**Scheduled review:** 27 October 2026  
**Goal:** support structured prescriptions, minimal patient context, booking creation, deterministic priority and patient status views.

| Issue | Work item | Owner | First recorded Done |
|---|---|---|---|
| MBA-10 | Structured prescription import | Namit Krishna Renjith | 14 November |
| MBA-11 | Consent and UNKNOWN patient context | Mrudul M | 16 November |
| MBA-12 | Booking creation and lifecycle | Mayank Kedia | 16 November |
| MBA-13 | Explainable priority | Paturu Naga Jignas | 15 November |
| MBA-14 | Patient booking and waitlist views | Mayank Kedia | 16 November |

**What the design established:** priority inputs are limited to authorized urgency, relevant continuity information and waiting time. UNKNOWN context and a deterministic tie rule avoid unsupported inference.

**Issue identified:** state transitions, duplicate requests and prescription quantity limits must be tested together. A priority calculation alone does not establish a correct booking workflow.

**Follow-through:** the final suite checks duplicate and excess quantities, priority ties, consent metadata and patient ownership. The patient interface exposes plain-language status without other patients' information.

**First checkpoint:** the first two weeks planned nine delivery items. None had a recorded Done transition by 27 October; all nine are Done at the final review. This checkpoint reviews planned scope and recorded status, rather than claiming historical acceptance.

## 3 Week 3 Allocation and Waitlist

**Scheduled review:** 3 November 2026  
**Goal:** implement stock reservation, ordered waitlists, cancellation, expiry, pharmacist review and notifications.

| Issue | Work item | Owner | First recorded Done |
|---|---|---|---|
| MBA-15 | Concurrency-safe stock reservation | Namit Krishna Renjith | 14 November |
| MBA-16 | Ordered waitlists and restock promotion | Paturu Naga Jignas | 15 November |
| MBA-17 | Expiry, cancellation and partial allocation | Mayank Kedia | 16 November |
| MBA-18 | Pharmacist review and documented overrides | Namit Krishna Renjith | 14 November |
| MBA-19 | Booking lifecycle notifications | Mrudul M | 16 November |

**What the final implementation demonstrates:** stock, reservations, booking state, audit records and notification events are committed together. Eligibility is rechecked during promotion. Notification delivery is handled separately from the completed reservation.

**Issue identified:** concurrent requests, repeated restock events and partial failures can corrupt stock if treated as separate updates. Cancellation and expiry also need to trigger queue processing.

**Follow-through:** the final suite checks the last-unit race, rollback, idempotent restocks, expiry, partial allocation and notification failure. These results apply to the current local implementation; no earlier test-run result is inferred from a commit message.

## 4 Week 4 Hardening and Demo

**Scheduled review:** 7 November 2026  
**Goal:** complete manager views, safety rules, performance and recovery checks, requirement traceability and demonstration preparation.

| Issue | Work item | Owner | First recorded Done |
|---|---|---|---|
| MBA-20 | Manager audit and fulfilment views | Mayank Kedia | 16 November |
| MBA-21 | Safety boundaries and business rules | Paturu Naga Jignas | 15 November |
| MBA-22 | Performance, reliability and recovery | Namit Krishna Renjith | 14 November |
| MBA-23 | Traceability and end-to-end validation | Mrudul M | 16 November |

**What the final verification demonstrates:** 44 automated application tests pass. Functional requirements have test references, and the local performance test meets the SRS prototype response limits.

**Issue identified:** the earlier in-memory implementation required persistent storage, complete role interfaces and repeatable verification before a credible final demonstration. Jira Done status alone does not establish production deployment or user acceptance.

**Follow-through:** the final branch includes SQLite persistence, role-specific interfaces, a verification runner and CI configuration. Live provider integration, full accessibility/mobile review, deployment security and formal user acceptance remain outside the completed local verification.

**Second checkpoint:** Weeks 3-4 planned nine delivery items. None had a recorded Done transition by 7 November; all nine are Done at the final review.

## 5 Final review

**Review date:** 16 November 2026  
**Application baseline:** `1cc7c7c`, `refactor/booking-platform`

| Evidence | Final recorded result |
|---|---|
| Jira delivery items | 18 of 18 Done |
| Jira epics | 5 of 5 Done |
| Application tests | 44 passed; no failures or errors |
| Separate line-coverage run | 43 non-performance tests passed |
| Status-read p95 | 31.9 ms against a 1,000 ms target |
| Booking-submission p95 | 43.4 ms against a 2,000 ms target |
| Restock and queue processing | 60.6 ms against a 3,000 ms target |
| Python statement coverage | 717/803 statements (89.3%), coverage.py 7.17.3 |
| Branch coverage | 214/272 branch destinations (78.7%), coverage.py 7.17.3 |
| External integrations | Mocked/test interfaces; live providers not validated |
| User acceptance | Formal acceptance not recorded |

Performance figures are local prototype observations, excluding external-service latency. The detailed environment and test methodology are in [TEST_REPORT.md](TEST_REPORT.md).

### Start Stop Continue

- **Start:** attach test evidence when an issue is completed, and review the Jira sprint state at each scheduled end.
- **Stop:** using scheduled dates, commit labels or a Done badge as substitutes for execution evidence or acceptance.
- **Continue:** keep priority deterministic, enforce patient ownership on the server, and exercise stock and failure cases with repeatable fixtures.

These are recommendations from the record review. They are not attributed to a team brainstorming or voting session.

### Proposed SMART actions

| Action | Proposed owner | Completion criterion | Due point | Status |
|---|---|---|---|---|
| Archive the final unit/system test results and measured line/branch coverage with the submission | Paturu Naga Jignas | One reproducible evidence set and a PDF below 10 MB containing the required sections | Before final submission | Evidence archived; PDF prepared |
| Review acceptance gaps and reconcile Jira sprint closure | Namit Krishna Renjith | Record acceptance decisions and remaining work; confirm the correct sprint state with the team | Before project handover | Open |

Ownership is proposed for team confirmation. Existing Jira assignees in the sprint tables are recorded assignments.

## 6 Retrospective activities checklist

| Phase | Activity | Historical sprint sessions | Final record review |
|---|---|---|---|
| Preparation | Review previous action items | Previous meeting actions unavailable | Available work and remaining actions reviewed |
| Preparation | Gather objective metrics | No saved sprint velocity or burndown | Issue history and verification results reviewed |
| Preparation | Select retrospective framework | Not recorded | Start Stop Continue used for this record |
| Preparation | Set a safe environment | Not recorded | Team session required |
| Set the stage | Welcome and safety check | Not recorded | Team session required |
| Set the stage | Icebreaker | Not recorded | Team session required |
| Gather data | Brainstorming | Not recorded | Recommendations documented; team input pending |
| Gather data | Review sprint timeline | No earlier meeting record | Scheduled periods and actual status transitions compared |
| Generate insights | Affinity mapping | Not recorded | Themes identified: traceability, state consistency and evidence |
| Generate insights | Root cause analysis | Not recorded | Schedule/status mismatch observed; team root-cause discussion pending |
| Decide actions | Dot voting | Not recorded | Team vote pending |
| Decide actions | Define SMART actions | Not recorded | Two proposed actions documented above |
| Close session | Summarize commitments | Not recorded | Proposals summarized; team confirmation pending |
| Close session | Feedback and ROTI | Not recorded | No score reported |
| Follow-through | Integrate actions into backlog | Not recorded | Proposed follow-up; no Jira changes made by this documentation update |
| Follow-through | Publish and archive summary | No earlier summary found | This record and source evidence added to the repository |

## Sources

1. [Original SRS](../SRS.md), including the four team members and project ID 47.
2. [Jira board 35](https://jigexams.atlassian.net/jira/software/projects/MBA/boards/35), sprint metadata and issue changelogs reviewed on 16 November 2026. The compact source extract is [evidence/sprint-review.json](evidence/sprint-review.json).
3. [Verification report](TEST_REPORT.md) and [engineering record](ENGINEERING.md).
4. Git history, including `f3e359b` (`docs: distinguish assignment schedule from Git history`). Earlier scheduled author dates do not independently establish completion, meetings or acceptance.

At review time, Jira still labels Week 1 active and Weeks 2-4 future despite their elapsed scheduled dates. This record does not change those states.
