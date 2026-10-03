---
name: Scope-Locked Epic
about: Create an independently closable WOW engineering epic with hard scope boundaries.
title: "Epic: "
labels: engineering
assignees: ""
---

# EPIC: [Initiative Title]

## 1. Executive Summary & Objective
- **Business Goal:** [1-2 sentences describing the shippable outcome]
- **Change Class:** [A / B / C]
- **Target Release Window:** [Sprint / date / milestone]
- **DRI / Lead:** [Name or agent role]

## 2. In Scope — Primary Core Deliverables (Phase 1)
List only the minimum viable deliverables required for the core objective.

- [ ] **Requirement 1:** [clear, measurable functionality]
- [ ] **Requirement 2:** [clear, measurable functionality]
- [ ] **Requirement 3:** [clear, measurable functionality]

## 3. Explicit Non-Goals / Out of Scope
These items MUST NOT hold this epic open.

- ❌ [Out-of-scope item 1]
- ❌ [Out-of-scope item 2]
- ❌ [Out-of-scope item 3]

## 4. Acceptance Criteria
- [ ] [Objective verification criterion]
- [ ] [Required tests / regression checks]
- [ ] [Operational or exact-SHA verification if applicable]
- [ ] V17 governance invariants preserved.

## 5. Mid-Flight Scope Split Protocol

If new work is discovered:

1. Is it required for Phase-1 acceptance, core stability, security, or a binding V17 governance invariant?
   - **YES:** add it to Phase 1 with the reason documented.
   - **NO:** create a separate follow-on ticket. Do not hold this epic open.

## 6. Candidate Follow-On Slices

| Ticket | Proposed capability | Priority | Triage |
| --- | --- | --- | --- |
| TBD |  |  | New Epic / Backlog / Drop |

## 7. Closure Criteria

This epic is **100% COMPLETE** when:

1. All Phase-1 acceptance criteria are verified.
2. Required code is merged and active in the target environment when deployment is part of scope.
3. Follow-on ideas are split or triaged.
4. A terminal engineering disposition is recorded.
