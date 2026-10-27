# Tivoli ERP — project instructions

## Objective and scope

Build an independent custom ERP for Tivoli. ERPNext and other ERP products are learning references; do not copy their code without explicit licence review. Use general-purpose open-source components with a documented dependency/licence inventory. The current authorized scope is the "Next" list in MILESTONE_ROADMAP.md. Items marked Blocked there wait on the named owner decision and must not be started by inferring a policy. Do not build further isolated Finance lab draft editors or worksheets. Do not claim the whole ERP is complete.

## Architecture

Python/Django supported 5.2 LTS line, Django REST Framework, PostgreSQL, React and TypeScript. Pin compatible versions and retain dependency lock files. Keep one modular backend. The frontend must not implement authoritative accounting, stock or permission decisions. Do not silently substitute Node/Express, an ERP platform, browser-local storage or SQLite for the required backend/database. If this environment cannot support the stack, explain the exact blocker.

Use environment variables for configuration and keep actual secrets out of the repository. Commit an `.env.example` containing placeholders only. Do not print secret values in logs, tests, README or handoff. Avoid hard-coded Replit URLs or platform-only authentication dependencies. Document local run steps outside Replit.

## Business structure

One legal company: Tivoli. Three configurable retail business units: Tivoli, Roche Bobois and Natuzzi. Product brand, business unit, legal company and physical location are distinct. TVF/NAT/RBB are item-code prefixes, not permission rules. Unit switchers cannot grant access.

Phase 1 sections: Retail & Sales; Procurement & Imports; Inventory & Warehouses; Delivery & After-Sales; Finance & Accounting; HR & Payroll; Governance & Administration. Later: full CRM/Marketing, Products/Brands, Projects/Fit-out, Factory and management intelligence. Shared masters support the core now. Keep unimplemented features visibly Planned.

## Mandatory controls

Server authorization by authenticated user, unit and action on every route. Scope lists, detail queries, exports, search and attachments when implemented. Deny by default. Separate read-stock and create-order grants. No shared privileged frontend token. Local/test authentication is not production identity readiness.

Money uses decimal arithmetic. Quantities have defined precision. Posted financial records require balanced journals and traceable corrections. Atomic stock reservation, idempotent commands and rollback behavior must be proven before those functions are represented as working. A display of correct totals is not test evidence.

Production and development are separate. Synthetic data only. Never put live credentials in the Agent workspace. No public publishing, real bank payments, outbound customer messaging, production migration or destructive reset. Do not purchase resources. A seed command must be repeatable and refuse to run unless explicitly configured as a demo environment.

## Delivery discipline

Read the milestone and references before editing. Write a short implementation plan, then implement the authorized increment. Run meaningful tests against PostgreSQL. Report pass/fail/blocked honestly and retain commands/results. Do not modify tests simply to hide failing behavior. Do not weaken authorization to make previews work.

The source of truth is the private GitHub repository (SamerTiv/Tai-erp), edited locally with Claude Code. Replit was used only for the initial build and is no longer the editor; this file keeps its historical name. Keep code portable. Add a README, build/run scripts, migrations and troubleshooting notes. Update REVIEW_HANDOFF.md with evidence and limitations. Stop at the batch boundary for review.
