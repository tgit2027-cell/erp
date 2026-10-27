# Tivoli ERP Decision Pack

21 October 2026 · Sam. Copy of the shared decision pack; record decisions there and update this file to match.

## Purpose

Most of the remaining ERP work is waiting on owner and Finance decisions, not on code. This pack lists each open decision, who should make it, what it unblocks, and a suggested default where the documents support one. Taken from the repository review, the architecture and technical foundation specs, MILESTONE_ROADMAP.md and the previous agent’s MEMORY index.

Fill in the Decision column, or comment on a row. Once a row is decided, it goes into replit.md as an approved rule so the build agent can act on it. Suggested defaults are proposals only; nothing here is approved until you say so.

## Inventory and warehouses

Stock transfers are the biggest missing Inventory journey. The transfer service already exists, but no screen or API can run it until authority is decided.

| \#  | Decision needed                                               | Owner                  | Unblocks                                                   | Suggested default                                                                                                                | Decision |
|-----|---------------------------------------------------------------|------------------------|------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|----------|
| I1  | Who may move stock between locations, and is approval needed? | Operations head        | Transfer screen and API, the spec’s showroom-transfer test | A separate transfer grant, required on both source and destination locations; no approval for the same BU, approval for cross-BU |          |
| I2  | Which source and destination locations a transfer may use     | Operations head        | Transfer location scope                                    | Any location the user holds the transfer grant for; never implied by read-stock or capacity rights                               |          |
| I3  | Does a temporary hold block other sales of that stock?        | Retail head            | Hold behaviour, available-quantity rule                    | Hold blocks the held quantity until expiry on the working calendar                                                               |          |
| I4  | Negative stock: allowed or refused?                           | Finance and Operations | Spec section 5 requirement                                 | Refused everywhere                                                                                                               |          |
| I5  | Partial delivery and order cancellation rules                 | Retail head            | Partial delivery, cancellation, quote expiry               | Allow line-level partial delivery; cancellation releases reservations, refunds follow Finance rule F7                            |          |

## Procurement and imports

Foreign currency is the hard blocker here. The database only accepts QAR, so a EUR purchase from Roche Bobois or Natuzzi cannot be recorded, and the spec’s first proof starts with exactly that purchase.

| \#  | Decision needed                                                               | Owner                        | Unblocks                                  | Suggested default                                                                                            | Decision |
|-----|-------------------------------------------------------------------------------|------------------------------|-------------------------------------------|--------------------------------------------------------------------------------------------------------------|----------|
| P1  | Which currencies, and where exchange rates come from                          | Finance                      | Multi-currency purchases, the first proof | QAR company currency; EUR, USD and others allowed on transactions; rate per transaction with recorded source |          |
| P2  | Who may see and create procurement records (suppliers, costs, commitments)    | Procurement head             | Procurement access, supplier POs          | A separate procurement grant per BU; cost and margin visibility as its own grant                             |          |
| P3  | Which items belong to which business unit                                     | Procurement and Retail heads | Catalog scope, packing-list matching      | Explicit item-to-BU membership; TVF/NAT/RBB prefixes stay labels only                                        |          |
| P4  | Units of measure and conversions                                              | Procurement and Factory      | UOM, later factory raw materials          | Base unit per item plus approved conversions; pieces for furniture first                                     |          |
| P5  | Landed charges eligible for cost, and how a late charge after sale is treated | Finance                      | Landed cost, late-cost test               | Allocate by purchase value (as built); late cost to COGS for units already sold                              |          |

## Retail and customer documents

These are content and rights decisions, not code. The quotation preview is built and waits only on approved text.

| \#  | Decision needed                                                     | Owner              | Unblocks                          | Suggested default                                              | Decision |
|-----|---------------------------------------------------------------------|--------------------|-----------------------------------|----------------------------------------------------------------|----------|
| R1  | Approved Terms and Conditions text, English and Arabic              | Legal              | Quotation and invoice documents   | None; text must come from legal                                |          |
| R2  | Arabic registered company name and contact details                  | Owner              | Bilingual documents               | None; must be supplied                                         |          |
| R3  | Default quote validity                                              | Retail head        | Quote expiry                      | 30 days, editable per quote (already proposed in the roadmap)  |          |
| R4  | Who may print, download or send a quotation PDF                     | Retail head        | PDF output                        | Any user who can read the quote, with audit on each export     |          |
| R5  | Real customer master: who creates and edits customers               | Retail and Finance | Customer records, CRM basics      | Sales can create; Finance approves credit-related fields       |          |
| R6  | Notifications: which messages, channels and wording go to customers | Retail head        | Real sending (today dry run only) | Start with internal email only; customer SMS or WhatsApp later |          |

## Finance policy

Finance owns every row here, and these unblock the most work. The spec says Finance must approve posting timing, valuation, accounts, currency rules and expected journals before the first proof becomes a real specification. Until then the 14 lab draft editors stay isolated and post nothing.

| \#  | Decision needed                                                              | Unblocks                               | Suggested default                                                                  | Decision |
|-----|------------------------------------------------------------------------------|----------------------------------------|------------------------------------------------------------------------------------|----------|
| F1  | Chart of accounts and mapping of each transaction to accounts                | Real general ledger, all posting       | Start from the Dynamics chart, with BU as a dimension                              |          |
| F2  | Inventory valuation method                                                   | Cost of goods sold, landed cost        | Weighted average per item and location                                             |          |
| F3  | Revenue recognition timing                                                   | Delivery and invoice journals          | At delivery, matching the current test fixture                                     |          |
| F4  | Treatment of customer deposits                                               | Payment journals, AR                   | Liability until delivery                                                           |          |
| F5  | Exchange-rate gains and losses, and revaluation                              | Multi-currency journals                | Realised on payment; month-end revaluation of open items                           |          |
| F6  | Period close: who closes and reopens, and how closed-period corrections work | Period controls, closed-period test    | Finance manager closes; reopening needs a second approver                          |          |
| F7  | Refund and credit-note rules                                                 | Returns posting, refunds, credit notes | Refund only after approved inspection; credit note references the original invoice |          |
| F8  | Tax configuration (VAT readiness)                                            | Invoicing                              | No tax now; keep configurable for later, confirmed by Finance and legal            |          |
| F9  | Approval authority and segregation (DOFA)                                    | Approvals across all modules           | Value bands per role, no self-approval                                             |          |
| F10 | Which transaction classes leave Dynamics first, and when                     | Cutover plan, avoiding double posting  | Retail sales and stock first; GL stays in Dynamics until parallel run reconciles   |          |
| F11 | Who in Finance reviews the first-proof journals                              | Acceptance of the first proof          | Named Finance reviewer                                                             |          |

## Platform and production

The code is in the private GitHub repo SamerTiv/Tai-erp and is edited locally with Claude Code; Replit was used only for the initial build. There is still no hosting and no production login.

| \#  | Decision needed                              | Owner        | Unblocks                                                 | Suggested default                                                                       | Decision |
|-----|----------------------------------------------|--------------|----------------------------------------------------------|-----------------------------------------------------------------------------------------|----------|
| T0  | Whether SamerTiv/Tai-erp moves to a Tivoli-owned GitHub organisation | Owner        | Long-term ownership and access control | Transfer to a Tivoli organisation before go-live, so ownership doesn't sit with one personal account                     |          |
| T1  | Whether the old Replit preview is shut down             | Owner        | Closing the overexposed preview                          | Private preview only; no public port 8000; DEBUG off                                    |          |
| T2  | Hosting provider and region                  | Owner and IT | Production settings, backups, monitoring                 | Managed PostgreSQL plus containers in a Gulf or EU region (spec allows overseas copies) |          |
| T3  | Production login                             | IT           | Real users                                               | Microsoft Entra ID single sign-on with MFA on Tivoli’s tenant                           |          |
| T4  | Separate demo and production builds          | Engineering  | Untangling the 366 synthetic checks                      | Keep demo as its own settings profile; production access goes through one grant model   |          |
| T5  | Who owns production support and incidents    | Owner        | Go-live gate in spec section 8                           | Named internal owner plus a support partner                                             |          |
| T6  | Peak workload and acceptable response times  | Operations   | Load testing                                             | Define before benchmarking, per spec section 8                                          |          |

## Recommended order

1.  **This week:** T1, shutting down the old Replit preview if it is still running.
2.  **Finance workshop:** F1 to F5 and F11, plus P1 and P5. Together they let the build agent finish the spec’s first proof, from a EUR purchase through to invoice and reconciliation.
3.  **Operations and Retail:** I1 to I5 and R3 to R5. These finish the transfer, hold and delivery journeys.
4.  **Before any real data:** T2 to T6, F6 to F10, R1, R2 and R6.
