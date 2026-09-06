# SRS Contribution — External Interfaces and Priority-Based Medicine Booking

## 3. External Interface Requirements

### 3.1 User Interfaces

The system shall provide role-appropriate interfaces for patients, pharmacists, and pharmacy managers.

#### 3.1.1 Patient Interface

The patient interface shall allow an authorized user to:

- submit or select a prescription;
- select medicines associated with a valid prescription;
- create a medicine booking;
- view the current booking status;
- view whether a request is allocated or waitlisted;
- view reservation expiry where applicable;
- cancel an eligible booking;
- receive clear status and error messages.

The patient interface shall not:

- expose another patient's identity, prescription, booking, or waitlist information;
- allow a patient to directly set or modify a verified urgency value;
- display internal allocation scores or sensitive patient-history attributes;
- imply that the application provides emergency medical care.

Patient-facing statuses shall use plain language corresponding to internal states such as:

- Prescription Under Review
- Prescription Accepted
- Prescription Rejected
- Booking Submitted
- Medicine Reserved
- Waitlisted
- Ready for Pickup / Fulfilment
- Cancelled
- Reservation Expired

#### 3.1.2 Pharmacist Interface

The pharmacist interface shall allow an authorized pharmacist to:

- search and review prescription-linked bookings;
- view prescription validation status;
- review medicine requests and requested quantities;
- confirm or assign an authorized urgency category according to pharmacy policy;
- view current inventory information;
- view the factors used by the priority policy;
- view the waitlist for a medicine without unnecessary patient information;
- approve or reject manual-review prescriptions;
- override an applicable priority or allocation decision;
- enter a mandatory reason for an override;
- mark a booking as ready, fulfilled, rejected, or cancelled where authorized.

The interface shall visually distinguish:

- system-generated priority;
- pharmacist-confirmed information;
- pharmacist overrides.

#### 3.1.3 Pharmacy Manager Interface

The pharmacy manager interface shall support authorized viewing of:

- active bookings;
- active waitlists;
- allocation history;
- pharmacist overrides;
- reservation expiry events;
- fulfilment metrics;
- notification delivery status.

Policy configuration capabilities, if implemented, shall be restricted to authorized users.

#### 3.1.4 Interface Validation and Error Handling

The system shall display validation messages when:

- a required prescription is missing;
- a prescription is invalid, expired, or awaiting review;
- a requested medicine is not associated with the prescription where such association is required;
- a requested quantity is invalid;
- a booking is duplicated;
- inventory is unavailable;
- a booking cannot be cancelled;
- an unauthorized action is attempted.

Error messages shall not expose stack traces, database details, authentication secrets, or sensitive patient information.

### 3.2 Software Interfaces

#### 3.2.1 Prescription Interface

The prescription interface shall support one or more of the following configured inputs:

- prescription document upload;
- structured internal prescription JSON;
- a FHIR-compatible prescription adapter.

The structured interface shall support mapping of:

- prescription identifier;
- patient identifier;
- prescription status;
- authored date;
- prescriber reference where available;
- medicine identifier;
- medicine name;
- requested quantity;
- source identifier.

A FHIR adapter may map relevant concepts from ABDM-compatible `MedicationRequest` and `PrescriptionRecord` structures into the internal model.

#### 3.2.2 Inventory Interface

The inventory interface shall provide, for a medicine and pharmacy location:

- medicine identifier;
- available or on-hand quantity;
- reserved quantity;
- allocatable quantity;
- batch information where available;
- expiry information where available;
- inventory status;
- last-updated timestamp.

The interface shall support inventory update or restock events.

Inventory operations used for allocation shall support a concurrency-safe reservation mechanism.

#### 3.2.3 Patient Context Interface

The patient-context interface shall provide only the attributes required by the configured allocation policy.

At minimum, the interface may provide:

- patient identifier;
- continuity-of-therapy indicator;
- context status such as AVAILABLE or UNKNOWN;
- source timestamp;
- authorization or consent reference where required.

The interface shall not provide unrelated medical history to the allocation component.

#### 3.2.4 Notification Interface

The notification interface shall support event requests containing:

- notification event identifier;
- recipient reference;
- channel;
- template identifier;
- booking identifier;
- non-sensitive template parameters;
- creation timestamp.

The notification service shall return or record delivery status.

#### 3.2.5 Authentication and Authorization Interface

The system shall integrate with an authentication mechanism capable of identifying:

- patient/caregiver;
- pharmacist;
- pharmacy manager;
- administrator.

Authorization shall be enforced server-side for protected operations.

### 3.3 Communications Interfaces

The system shall use HTTPS for communication between browser clients, application services, and external network services.

External APIs shall exchange structured data using JSON unless a selected integration requires another documented format.

The system shall:

- validate incoming API payloads;
- reject malformed requests;
- use authenticated requests for protected interfaces;
- apply configurable timeouts to external service calls;
- handle external service failures without corrupting booking or allocation state;
- prevent duplicate effects for retried idempotent operations where applicable.

Sensitive prescription and patient information shall not be included in URLs or query strings when avoidable.

Notification text sent through channels visible on device lock screens shall contain the minimum information necessary to communicate status.

---

# 5.2 Priority-Based Medicine Booking

## 5.2.1 Description and Priority

The Priority-Based Medicine Booking feature creates and maintains medicine bookings and determines the ordering of eligible requests when allocation decisions are required.

The feature shall use an explainable and deterministic policy based on authorized inputs.

The recommended ordering factors are:

1. eligibility;
2. verified urgency;
3. continuity-of-therapy context;
4. waiting time;
5. deterministic tie-breaking.

Medicine availability is treated as an allocation constraint and is evaluated by the allocation component.

A patient's commercial value, subscription status, payment value, or unrelated personal characteristics shall not influence medicine priority.

**Priority:** High

## 5.2.2 Stimulus/Response Sequences

### A. Create Eligible Booking

1. An authorized user selects a valid prescription.
2. The user selects an associated medicine request.
3. The user submits a booking request.
4. The system validates the prescription and booking information.
5. The system creates a unique booking identifier.
6. The booking enters eligibility evaluation.
7. The system obtains the configured priority inputs.
8. The system records a priority result and reason codes.
9. The booking is forwarded to allocation.

### B. Booking with Invalid Prescription

1. A user attempts to create a booking.
2. The associated prescription is invalid or expired.
3. The system rejects automatic booking progression.
4. The system records the reason.
5. The user receives an appropriate status message.

### C. Verified Urgency

1. A booking requires priority evaluation.
2. An authorized pharmacist or trusted source supplies or confirms an urgency category.
3. The system records the urgency value, source, actor, and timestamp.
4. The priority policy uses the verified urgency value.
5. The result and reason codes are stored.

### D. Missing Patient Context

1. A booking requires continuity-of-therapy context.
2. The patient-context source cannot provide the required value.
3. The system records the context as `UNKNOWN`.
4. The system applies the configured policy for unknown context.
5. The system does not infer medical history from unrelated data.

### E. Priority Re-Evaluation

1. A policy-relevant input changes.
2. The system determines that re-evaluation is required.
3. The priority policy is executed again.
4. The new result and policy version are stored.
5. The audit trail records the change.

### F. Equivalent Requests

1. Two or more eligible bookings have equivalent verified urgency and continuity context.
2. The system compares waiting time.
3. The older eligible booking is ordered before a newer equivalent booking.
4. If all configured factors remain equal, the configured deterministic tie-break rule is applied.

## 5.2.3 Functional Requirements

**REQ-PB-01:** The system shall assign a unique identifier to each medicine booking.

**REQ-PB-02:** The system shall associate each booking with one patient, one prescription, and at least one medicine request.

**REQ-PB-03:** The system shall prevent automatic progression of a booking when the associated prescription does not satisfy the configured eligibility policy.

**REQ-PB-04:** The system shall record the booking creation timestamp.

**REQ-PB-05:** The system shall maintain a booking status from the configured booking lifecycle.

**REQ-PB-06:** The system shall accept a verified urgency value only from an authorized user or trusted configured source.

**REQ-PB-07:** The patient-facing interface shall not permit a patient to directly modify a verified urgency value.

**REQ-PB-08:** The system shall record the source, actor, and timestamp associated with a verified urgency value when available.

**REQ-PB-09:** The priority policy shall use only configured and authorized input attributes.

**REQ-PB-10:** The priority policy shall include verified urgency as a ranking factor.

**REQ-PB-11:** The priority policy shall support a continuity-of-therapy value as a ranking factor.

**REQ-PB-12:** The priority policy shall support an explicit `UNKNOWN` continuity value.

**REQ-PB-13:** The priority policy shall use booking waiting time to order otherwise equivalent eligible requests.

**REQ-PB-14:** The system shall apply a deterministic tie-break rule when all configured ranking factors are equivalent.

**REQ-PB-15:** The system shall record the policy version used for each priority evaluation.

**REQ-PB-16:** The system shall record one or more reason codes explaining the factors that determined the priority result.

**REQ-PB-17:** An authorized pharmacist shall be able to view the reason codes associated with a booking priority.

**REQ-PB-18:** The system shall re-evaluate priority when a configured policy-relevant input changes.

**REQ-PB-19:** The system shall timestamp each priority evaluation.

**REQ-PB-20:** The system shall preserve the previous and new priority result when a priority change is auditable.

**REQ-PB-21:** The priority policy shall not use purchase value, customer lifetime value, subscription tier, marketing engagement, or payment amount as medicine-priority factors.

**REQ-PB-22:** The priority policy shall not use unrelated protected or personal characteristics as ranking factors.

**REQ-PB-23:** The system shall prevent a booking in `CANCELLED`, `REJECTED`, `FULFILLED`, or `EXPIRED` state from participating in active priority ordering.

**REQ-PB-24:** The system shall reject a booking request with a non-positive requested quantity.

**REQ-PB-25:** The system shall detect a configurable duplicate-booking condition before creating a second equivalent active request.

**REQ-PB-26:** When a duplicate active booking is detected, the system shall reject or flag the request according to configured policy.

**REQ-PB-27:** The system shall provide the allocation component with the booking identifier, medicine request, current eligibility state, and priority result.

**REQ-PB-28:** The system shall not expose other patients' priority inputs or health information to a patient viewing their own booking.

**REQ-PB-29:** The patient interface shall display a plain-language booking status.

**REQ-PB-30:** The system shall record a reason when a booking is rejected before allocation.
