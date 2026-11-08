# Areas: each person sees only their part of the ERP

Sam, 5–6 Nov 2026. After signing in, a person first sees a plain page with a tile for each area they work in,
and no menu. A tile opens the ERP with only that area's sections, and a showroom tile also switches to that
showroom's business unit. "All areas" in the menu goes back to the tiles. Someone with several areas can also open
the full ERP with all their sections. Someone with one area goes straight in. A link straight to a page skips the tiles.

| Area | Kind | Sections |
| --- | --- | --- |
| Tivoli Furniture (all Tivoli showrooms, textile and surface) | business | Retail & Sales, in unit TVL |
| Roche Bobois | business | Retail & Sales, in unit RBB |
| Natuzzi | business | Retail & Sales, in unit NAT |
| Factory | business | Factory (shows "coming soon" until the Factory section is in this version) |
| Projects & Fit-Out | business | Projects & Fit-Out (projects from the signed BOQ, tenders, bonds and guarantees, cash flow), behind its own go-live switch |
| Finance | shared | Finance & Accounting, Reports |
| Warehouse | shared | Inventory & Warehouses, Delivery & After-Sales |
| Logistics & Imports (a separate team from the warehouse) | shared | Logistics & Shipping, Procurement & Imports |
| Purchasing (IT makes purchase orders today) | shared | Purchasing Workbench, Procurement & Imports |
| HR & Admin | shared | HR & Payroll |
| ERP setup | shared | Governance & Administration, Reports |

Overview, Messages, My Page and the HR portal stay visible to everyone.

## Who sees which area

- The Master Admin always sees every area.
- Users and permissions has an "Areas this person sees" block, with "All areas" and one tick box per area.
  Master Admins and General Admins (for the people they manage) can change it, and every change is recorded.
- A person with nothing ticked sees the areas their permissions point to. This is worked out generously, so nobody
  testing Phase 1 loses a section they use. The first tick starts from that list.

Areas tidy the screen. They are not permissions: every page still checks the person's own permissions, and a
link to a section outside a person's areas opens nothing.

Code: `foundation/areas.py`, `UserArea` (migration `0099_user_areas`), `src/AreaHome.tsx`, `visibleSections` in
`src/sidebar-destinations.ts`.
