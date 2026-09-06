# PES UNIVERSITY, BANGALORE

## Department of Computer Science and Engineering

### Jackfruit Phase-1

---

# Software Requirements Specification

## Priority-Based Medicine Booking and Allocation Module for Pharma Apps

**Project ID:** 47  
**Version:** 1.0

## Team Details

| Project ID | Team Member | SRN |
|---|---|---|
| 47 | Paturu Naga Jignas | PES2UG24AM110 |
| 47 | Namit Krishna Renjith | PES2UG24AM097 |
| 47 | Mayank Kedia | PES2UG24AM088 |
| 47 | Mrudul M | PES2UG24AM094 |

**Prepared by:** Project Team  
**Organization:** PES University, Bangalore  
**Date:** 06 September 2026

---

# Table of Contents

1. [Introduction](#1-introduction)
   - [1.1 Purpose](#11-purpose)
   - [1.2 Intended Audience and Reading Suggestions](#12-intended-audience-and-reading-suggestions)
   - [1.3 Product Scope](#13-product-scope)
   - [1.4 References](#14-references)
2. [Overall Description](#2-overall-description)
   - [2.1 Product Perspective](#21-product-perspective)
   - [2.2 Product Functions](#22-product-functions)
   - [2.3 User Classes and Characteristics](#23-user-classes-and-characteristics)
   - [2.4 Operating Environment](#24-operating-environment)
   - [2.5 Design and Implementation Constraints](#25-design-and-implementation-constraints)
   - [2.6 Assumptions and Dependencies](#26-assumptions-and-dependencies)
3. [External Interface Requirements](#3-external-interface-requirements)
4. [Analysis Models](#4-analysis-models)
5. [System Features](#5-system-features)
   - [5.1 E-Prescription and Patient Context](#51-e-prescription-and-patient-context)
6. [Other Nonfunctional Requirements](#6-other-nonfunctional-requirements)
7. [Other Requirements](#7-other-requirements)
8. [Appendix A: Glossary](#appendix-a-glossary)
9. [Appendix B: Field Layouts](#appendix-b-field-layouts)
10. [Appendix C: Requirement Traceability Matrix](#appendix-c-requirement-traceability-matrix)

---

# Revision History

| Name | Date | Reason for Changes | Version |
|---|---|---|---|
| Paturu Naga Jignas | 06-09-2026 | Initial SRS content for Introduction, Overall Description, and E-Prescription and Patient Context | 1.0 |

---

# 1. Introduction

## 1.1 Purpose

This Software Requirements Specification defines the requirements for Version 1.0 of the **Priority-Based Medicine Booking and Allocation Module for Pharma Apps**.

The system is designed as an integrable pharmacy workflow module that supports prescription-based medicine booking and the allocation of limited medicine stock among eligible requests.

The module uses the following information during the booking and allocation workflow:

- prescription information;
- current medicine availability;
- verified urgency;
- patient-history information relevant to continuity of therapy;
- waitlist state; and
- pharmacy allocation rules.

The system supports prescription handling, priority-based booking, medicine allocation, waitlist management, notifications, pharmacist intervention, and auditability.

The system does not diagnose medical conditions, prescribe medicines, or independently determine clinical urgency.

## 1.2 Intended Audience and Reading Suggestions

This document is intended for:

- project developers;
- project evaluators;
- software testers;
- pharmacists and pharmacy managers;
- system administrators; and
- developers responsible for integrating prescription, inventory, and notification services.

The SRS is organized into product description, interface requirements, analysis models, system features, nonfunctional requirements, other requirements, and supporting appendices.

## 1.3 Product Scope

The **Priority-Based Medicine Booking and Allocation Module for Pharma Apps** manages prescription-medicine requests when medicine availability is limited.

The system provides the following capabilities:

- receive uploaded or electronic prescriptions;
- record prescription validation status;
- associate prescriptions with patients;
- identify medicines requested through a prescription;
- obtain patient-history information required for allocation;
- create medicine bookings;
- check medicine availability;
- prioritize eligible medicine requests;
- reserve available stock;
- place unfulfilled requests on a waitlist;
- re-evaluate waitlisted requests when stock becomes available;
- send booking and waitlist notifications;
- support authorized pharmacist overrides; and
- maintain an audit trail of important decisions.

The module is intended for integration with pharmacy applications, hospital or clinic pharmacy systems, pharmacy chains, and e-pharmacy systems.

### Objectives

1. Provide a consistent method for processing limited medicine stock.
2. Prevent allocation beyond available inventory.
3. Maintain a transparent ordering of eligible requests.
4. Support continuity of medication where relevant patient history is available.
5. Automatically manage unfulfilled requests through a waitlist.
6. Provide pharmacists with controlled override capability.
7. Maintain traceability of prescription, booking, allocation, and waitlist decisions.

### Scope Exclusions

Version 1.0 does not include:

- medical diagnosis;
- medicine prescribing;
- autonomous emergency triage;
- payment processing;
- insurance processing;
- delivery-route optimization;
- a complete e-commerce pharmacy platform; or
- autonomous medicine substitution.

## 1.4 References

1. PES University, Department of Computer Science and Engineering, **JACKFRUIT - SE MINI-PROJECT GUIDELINES 2026**.
2. Ayushman Bharat Digital Mission, **ABDM Documentation and FAQ**.
3. NRCeS/NHA, **FHIR Implementation Guide for ABDM — MedicationRequest**.
4. NRCeS/NHA, **FHIR Implementation Guide for ABDM — PrescriptionRecord**.
5. Central Drugs Standard Control Organisation, **Drugs and Cosmetics Act, 1940 and Rules, 1945**.
6. Ministry of Health and Family Welfare, **Telemedicine Practice Guidelines**.
7. Ministry of Electronics and Information Technology, **Digital Personal Data Protection Rules, 2025**.

---

# 2. Overall Description

## 2.1 Product Perspective

The system is a software module intended to operate within or alongside an existing pharmacy application.

It focuses on the workflow between a prescription-based medicine request and medicine allocation. Existing systems may continue to manage medicine catalogs, payments, delivery, procurement, and other pharmacy operations.

The module may communicate with:

- a patient-facing pharmacy application;
- a pharmacist interface;
- an electronic-prescription source;
- a patient-record source;
- an inventory management system;
- a notification service; and
- an audit or reporting store.

The system shall use a deterministic and explainable allocation policy. Medicine availability shall be treated as an allocation constraint. Priority shall be determined from authorized and relevant inputs such as verified urgency, continuity-of-therapy information, and waiting time.

An authorized pharmacist shall be able to review and override a system decision when required.

## 2.2 Product Functions

### Prescription and Patient Context

The system shall:

- receive an uploaded prescription or supported electronic prescription;
- record prescription source and validation status;
- associate the prescription with the relevant patient;
- identify medicine requests in the prescription;
- obtain only the patient-history information required by the allocation policy; and
- record authorization information when required for access to external health records.

### Booking and Priority

The system shall:

- create medicine bookings;
- accept verified urgency only from an authorized source;
- calculate an explainable priority order;
- maintain the rules or reason codes used to determine priority; and
- use waiting time when resolving otherwise equivalent requests.

### Inventory and Allocation

The system shall:

- retrieve current allocatable inventory;
- reserve medicine stock;
- prevent allocation beyond available quantity;
- support the configured partial-allocation policy; and
- release reserved stock after cancellation or reservation expiry.

### Waitlist Management

The system shall:

- create a waitlist entry when an eligible request cannot be fulfilled;
- order active waitlist entries using the configured priority policy;
- re-evaluate the waitlist when stock changes;
- promote the highest-priority eligible request when stock becomes available; and
- remove or skip cancelled, expired, or otherwise ineligible requests.

### Notifications

The system shall:

- notify users when booking status changes;
- notify users when a request enters the waitlist;
- notify users when medicine becomes available and is reserved;
- notify users about reservation expiry or cancellation where applicable; and
- record notification status.

### Pharmacist Control and Audit

The system shall:

- allow authorized pharmacists to override applicable priority or allocation decisions;
- require a reason for an override;
- record the user, time, previous state, new state, and reason for an override; and
- support authorized review of allocation history.

## 2.3 User Classes and Characteristics

### 2.3.1 Patient / Caregiver

A patient or authorized caregiver may:

- provide or select a prescription;
- request prescribed medicines;
- view booking status;
- view waitlist status; and
- receive notifications.

The patient shall only access records and bookings for which they are authorized.

A patient shall not directly set or modify a verified urgency value used by the allocation system.

### 2.3.2 Pharmacist

A pharmacist is an authorized operational user who may:

- review prescriptions;
- validate prescription information;
- confirm operational urgency;
- review medicine availability;
- process medicine fulfilment;
- handle exceptional cases; and
- override applicable allocation decisions.

### 2.3.3 Pharmacy Manager

A pharmacy manager may:

- review allocation activity;
- inspect overrides;
- review waitlist and fulfilment information; and
- manage approved operational policies where authorized.

### 2.3.4 External Prescription System

An external prescription system may provide:

- patient identifiers;
- prescription metadata;
- medication requests; and
- prescription source identifiers.

### 2.3.5 Inventory System

An inventory system may provide:

- medicine identifiers;
- pharmacy or location identifiers;
- available stock quantities;
- reserved quantities;
- batch or expiry information; and
- stock-change events.

### 2.3.6 Notification Service

A notification service may deliver booking, waitlist, reservation, and cancellation notifications.

## 2.4 Operating Environment

The system shall be implementable as a web-based software module consisting of:

- browser-based user interfaces;
- an application or API service;
- persistent data storage;
- a prescription integration interface;
- an inventory integration interface; and
- a notification interface.

The final programming language, framework, database, deployment platform, and supporting tools shall be defined in the project plan.

External systems shall be accessed through defined interfaces so that test implementations or mock services can be used when a production integration is unavailable.

## 2.5 Design and Implementation Constraints

1. Prescription-required medicines shall require an acceptable prescription state before automatic allocation.
2. Patient-entered free text shall not directly determine verified urgency.
3. Only patient-history data required by the allocation policy shall be provided to the allocation process.
4. Medicine allocation shall prevent concurrent overselling.
5. Pharmacist overrides shall be recorded in the audit trail.
6. Restricted medicine categories may require manual pharmacist review or exclusion from the prototype.
7. The system shall not claim to diagnose, prescribe, or provide emergency medical care.
8. External prescription, inventory, and notification services shall be accessed through defined interfaces.
9. Health and prescription data shall be protected using appropriate authentication, authorization, secure communication, and data-minimization controls.
10. The design should permit structured electronic prescription integration compatible with relevant FHIR concepts.

## 2.6 Assumptions and Dependencies

### Assumptions

1. The pharmacy inventory system provides sufficiently current stock information.
2. Prescription validity can be determined through the configured validation workflow.
3. Verified urgency can be supplied or confirmed by an authorized user or system.
4. Patient-history information relevant to continuity of therapy can be represented using a limited set of attributes.
5. Every booking and prescription can be uniquely identified.
6. Inventory can be reserved and released through a controlled operation.

### Dependencies

The system depends on:

- prescription data;
- patient identity data;
- medicine catalog data;
- inventory data;
- authentication and authorization;
- persistent storage; and
- notification services.

The following policy values shall be configurable:

- urgency categories;
- reservation duration;
- partial-allocation behavior;
- notification channels; and
- pharmacy or branch scope.

---

# 3. External Interface Requirements

## 3.1 User Interfaces

TBD

## 3.2 Software Interfaces

TBD

## 3.3 Communications Interfaces

TBD

---

# 4. Analysis Models

TBD

---

# 5. System Features

## 5.1 E-Prescription and Patient Context

### 5.1.1 Description and Priority

The E-Prescription and Patient Context feature provides the prescription and patient information required by the medicine booking and allocation workflow.

The feature supports:

- prescription ingestion;
- prescription validation state;
- patient association;
- medication-request extraction or import;
- minimum required patient-history context; and
- traceability between prescription, patient, medicine request, and booking.

**Priority:** High

### 5.1.2 Stimulus/Response Sequences

#### A. Prescription Upload

1. The patient or pharmacist submits a supported prescription file.
2. The system assigns a unique prescription identifier.
3. The system records the prescription source.
4. The system validates the file against configured structural constraints.
5. The prescription enters the appropriate validation state.
6. After successful validation, the prescription becomes available for medicine booking.

#### B. Electronic Prescription Import

1. An external prescription system provides a supported structured prescription.
2. The system validates the input structure.
3. The system maps supported prescription and medication-request fields to the internal data model.
4. The system records the external source identifier where available.
5. The prescription enters the configured validation workflow.
6. A valid prescription becomes available for booking.

#### C. Invalid or Incomplete Prescription

1. A prescription is submitted or imported.
2. The system determines that required information is invalid, missing, expired, or requires manual review.
3. The system records the corresponding validation state.
4. The system records the reason for the validation result.
5. The system prevents automatic allocation where the prescription state does not permit allocation.

#### D. Patient Context Retrieval

1. An eligible booking requires priority evaluation.
2. The system identifies the relevant patient.
3. The system requests only the patient-history attributes required by the configured allocation policy.
4. The context provider returns the available value or an explicit unknown value.
5. The system provides the minimum required context to the priority-allocation process.

### 5.1.3 Functional Requirements

**REQ-EP-01:** The system shall assign a unique identifier to each prescription received by the system.

**REQ-EP-02:** The system shall record the source of each prescription.

**REQ-EP-03:** The system shall validate uploaded prescription files against configured file-type and file-size constraints.

**REQ-EP-04:** The system shall support at least the prescription states `RECEIVED`, `VALIDATION_PENDING`, `VALID`, `INVALID`, `EXPIRED`, and `MANUAL_REVIEW_REQUIRED`.

**REQ-EP-05:** The system shall prevent prescriptions in `INVALID` or `EXPIRED` state from being used for automatic medicine allocation.

**REQ-EP-06:** The system shall associate each prescription with one patient identity in the internal data model.

**REQ-EP-07:** The system shall store or reference one or more medication requests associated with a prescription.

**REQ-EP-08:** A medication request shall include a medicine identifier or medicine name and a requested quantity before it can enter allocation.

**REQ-EP-09:** The system shall record the prescription-authored date and prescriber reference when these values are available.

**REQ-EP-10:** The system shall provide an interface for mapping a supported structured electronic prescription into the internal prescription and medication-request model.

**REQ-EP-11:** The system shall reject a structured prescription payload that does not satisfy the configured input schema.

**REQ-EP-12:** When patient-history context is required for priority evaluation, the system shall retrieve only the attributes required by the configured allocation policy.

**REQ-EP-13:** When required patient-history information is unavailable, the system shall represent the value as `UNKNOWN` and shall not infer it from unrelated patient information.

**REQ-EP-14:** The system shall record authorization or consent metadata when required for access to an external health-record source.

**REQ-EP-15:** The system shall not provide unrelated patient-history information to the priority-allocation process.

**REQ-EP-16:** The system shall record a validation or rejection reason when a prescription cannot proceed automatically.

**REQ-EP-17:** The system shall maintain traceable relationships among patient, prescription, medication request, and resulting booking.

**REQ-EP-18:** The system shall support authorized manual review of prescriptions in `MANUAL_REVIEW_REQUIRED` state.

**REQ-EP-19:** The system shall timestamp prescription creation or import and every prescription validation-state change.

**REQ-EP-20:** The system shall preserve the source identifier of an imported electronic prescription when the source provides one.

## 5.2 Priority-Based Medicine Booking

TBD

## 5.3 Medicine Allocation and Waitlist Management

TBD

## 5.4 Notification Management

TBD

---

# 6. Other Nonfunctional Requirements

## 6.1 Performance Requirements

TBD

## 6.2 Safety Requirements

TBD

## 6.3 Security Requirements

TBD

## 6.4 Software Quality Attributes

TBD

## 6.5 Business Rules

TBD

---

# 7. Other Requirements

TBD

---

# Appendix A: Glossary

| Term | Definition |
|---|---|
| ABDM | Ayushman Bharat Digital Mission |
| Allocation | Reservation or assignment of available medicine stock to an eligible booking |
| Allocatable Stock | Stock available for reservation after existing reservations and stock constraints |
| Continuity of Therapy | Ongoing use of a medicine where interruption may be relevant to allocation policy |
| E-Prescription | Prescription information represented electronically |
| FHIR | Fast Healthcare Interoperability Resources |
| Medication Request | A request or order for a medicine associated with a prescription |
| Patient Context | Minimum patient information required by the allocation policy |
| Priority Policy | Rules used to order eligible medicine requests |
| Verified Urgency | Urgency value supplied or confirmed through an authorized workflow |
| Waitlist | Ordered collection of eligible requests that cannot currently be allocated |
| TBD | To Be Determined |

---

# Appendix B: Field Layouts

TBD

---

# Appendix C: Requirement Traceability Matrix

| Sl. No. | Requirement ID | Brief Description of Requirement | Architecture Reference | Design Reference | Code File Reference | Test Case ID | System Test Case ID |
|---:|---|---|---|---|---|---|---|
| 1 | REQ-EP-01 | Assign unique prescription ID | TBD | TBD | TBD | TBD | TBD |
| 2 | REQ-EP-02 | Record prescription source | TBD | TBD | TBD | TBD | TBD |
| 3 | REQ-EP-03 | Validate uploaded file type and size | TBD | TBD | TBD | TBD | TBD |
| 4 | REQ-EP-04 | Maintain prescription validation state | TBD | TBD | TBD | TBD | TBD |
| 5 | REQ-EP-05 | Prevent invalid or expired prescription allocation | TBD | TBD | TBD | TBD | TBD |
| 6 | REQ-EP-06 | Associate prescription with patient | TBD | TBD | TBD | TBD | TBD |
| 7 | REQ-EP-07 | Store or reference medication requests | TBD | TBD | TBD | TBD | TBD |
| 8 | REQ-EP-08 | Require medicine identifier/name and quantity | TBD | TBD | TBD | TBD | TBD |
| 9 | REQ-EP-09 | Record authored date and prescriber when available | TBD | TBD | TBD | TBD | TBD |
| 10 | REQ-EP-10 | Support structured prescription mapping | TBD | TBD | TBD | TBD | TBD |
| 11 | REQ-EP-11 | Reject invalid structured prescription payload | TBD | TBD | TBD | TBD | TBD |
| 12 | REQ-EP-12 | Retrieve minimum required patient-history attributes | TBD | TBD | TBD | TBD | TBD |
| 13 | REQ-EP-13 | Represent unavailable context as UNKNOWN | TBD | TBD | TBD | TBD | TBD |
| 14 | REQ-EP-14 | Record required authorization or consent metadata | TBD | TBD | TBD | TBD | TBD |
| 15 | REQ-EP-15 | Exclude unrelated patient history from allocation | TBD | TBD | TBD | TBD | TBD |
| 16 | REQ-EP-16 | Record validation or rejection reason | TBD | TBD | TBD | TBD | TBD |
| 17 | REQ-EP-17 | Maintain patient-prescription-request-booking traceability | TBD | TBD | TBD | TBD | TBD |
| 18 | REQ-EP-18 | Support authorized manual review | TBD | TBD | TBD | TBD | TBD |
| 19 | REQ-EP-19 | Timestamp prescription state changes | TBD | TBD | TBD | TBD | TBD |
| 20 | REQ-EP-20 | Preserve imported prescription source identifier | TBD | TBD | TBD | TBD | TBD |
