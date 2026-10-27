# Retail & Sales pilot review guide

**Owner clarification — internal ERP language:** use English navigation, form
labels, validation, dashboards and reports. English/Arabic is required only in
customer quotation/invoice documents. Arabic reviewer guidance in this document
is a translation of instructions, not a requirement for Arabic ERP controls.
Preserve supplied Arabic customer/item/document text and historical snapshots.
# دليل مراجعة تجريبي لقسم التجزئة والمبيعات

**Synthetic prototype only · no real payments · no production-readiness claim**  
**نموذج أولي ببيانات اصطناعية فقط · لا توجد مدفوعات حقيقية · لا يُعدّ جاهزاً للإنتاج**

This guide is for a bounded review of existing Retail & Sales workflows. It
does not approve legal wording, commercial terms, access assignments, or a
production launch. Arabic guidance below is a practical translation for this
review, not approved quotation terms or a registered Arabic company name.

هذا الدليل لمراجعة محدودة لسير العمل الموجود في التجزئة والمبيعات. ولا يعني
اعتماد نصوص قانونية أو شروط تجارية أو صلاحيات مستخدمين أو إطلاق النظام
للإنتاج. الترجمة العربية إرشادية للمراجعة وليست شروطاً معتمدة أو اسماً عربياً
مسجلاً للشركة.

## What each status means | معنى حالات الجاهزية

| English | العربية | Meaning |
| --- | --- | --- |
| Implemented | منفّذ | The prototype has the described screen or behavior. This alone is not proof of team acceptance. |
| Verified in isolation | تم التحقق في بيئة معزولة | Earlier recorded tests used disposable synthetic PostgreSQL data; browser checks used a temporary authenticated synthetic fixture where stated. They were not rerun for this guide. |
| Awaiting team acceptance | بانتظار قبول الفريق | A human reviewer has not recorded a pass or feedback for this pilot. |
| Unavailable / pending decision | غير متاح / بانتظار قرار | Do not bypass the existing denial or supply an unapproved rule or configuration. |

## Safe setup and role prerequisites | الإعداد الآمن ومتطلبات الصلاحيات

### Preferred populated review: temporary local synthetic session

On the project owner’s workstation, with the project’s existing dependencies
and PostgreSQL 16.15 already available, run:

```sh
RETAIL_TEAM_REVIEW=1 bash scripts/verify-retail-acceptance.sh
```

The existing script creates a fresh disposable PostgreSQL cluster under a
temporary directory, applies migrations there, prepares fictional test users
and records, and serves a loopback-only browser session. It prints the local
URL and the **path** to a private temporary credential file; it does not print
passwords. The owner opens that file privately and uses the temporary fictional
login only in that local review. Never paste the credential file or its values
into team feedback. The session and test credentials are removed on normal
exit, Ctrl+C, or the script’s 30-minute stop. If PostgreSQL 16.15 is installed
outside the script’s default location, set `RETAIL_PG_BIN` to that existing
installation’s `bin` directory. The script does not install tools.

Do not expose the loopback service publicly or substitute the shared demo or
production database. This temporary review creates **disposable local test
accounts**, not shared credentials or grants. It does not reset or reseed the
shared demo. If the owner does not have the required local setup, stop and
record the blocker; do not request a password or change shared access.

Для مراجعة مهيأة ببيانات اصطناعية، يشغّل مالك المشروع الأمر أعلاه على جهازه
مع تبعيات المشروع وPostgreSQL 16.15 المثبتين مسبقاً. ينشئ السكربت قاعدة
اختبار مؤقتة وحسابات خيالية محلية فقط، ويعرض عنوان loopback ومسار ملف
اعتماد خاصاً دون طباعة كلمات المرور. يفتح المالك الملف محلياً وبشكل خاص؛
لا تُنسخ بيانات الاعتماد إلى الملاحظات. يُحذف الاختبار عند الخروج أو
Ctrl+C أو بعد 30 دقيقة. لا تُفتح الخدمة للعامة ولا تُستخدم قاعدة العرض
المشتركة أو قاعدة الإنتاج.

### What the shared demo can currently show

The latest recorded shared-demo inspection in
[`RETAIL_TEAM_ACCEPTANCE.md`](RETAIL_TEAM_ACCEPTANCE.md#shared-review-environment--upgrade-verified-2026-10-20)
found five demo-marked accounts and nine `read_stock` grants, but no
`create_order` grants, quote approval-role assignments, approved selling-price
snapshots, or customer choices. That state permits only the navigation,
authorized/unavailable, and denial states for quote creation—not the populated
quotation or collection journey. It is a recorded snapshot, not a live check
made for this guide. Use only the owner’s existing privately held account; do
not reset a password, add grants, or create shared transactions to make the
journey pass.

آخر فحص موثق لبيئة العرض المشتركة وجد خمسة حسابات تجريبية وتسعة أذونات
`read_stock`، من دون أذونات `create_order` أو تعيينات أدوار اعتماد أو أسعار
معتمدة أو خيارات عملاء. لذا يمكن مراجعة التنقل وحالات عدم التوفر والرفض فقط،
ولا يمكن افتراض تهيئة رحلة عرض السعر أو التحصيل. هذه لقطة موثقة وليست فحصاً
مباشراً أُجري لهذا الدليل. لا تُضف صلاحيات أو معاملات مشتركة.

### Use only existing synthetic roles and configuration

- **Read-only reviewer:** may inspect only routes and fields the signed-in
  account already receives. A Favorite is navigation, never authority.
- **Salesperson:** the isolated fixture must already provide the applicable
  active `create_order` permission, its existing business-unit approval-role
  assignment, approved price snapshots, customer/item choices, and any
  required location scope. Quantity display needs separate `read_stock`
  authority. The server rechecks each operation; one permission does not imply
  another.
- **Approver:** use only an existing fictional role assignment in the isolated
  fixture and its existing access. Current discount ceilings are Salesperson
  10%, Manager 20%, Commercial GM 30%, and CEO exceptional 50%. No named
  employee is assigned here. Manager configuration visibility retains its
  existing gate.
- **Special orders:** use only if the current synthetic configuration permits
  the operation. Special-order value bands remain unconfigured; do not invent
  thresholds or payment terms.

- **مراجع للقراءة فقط:** يرى المسارات والحقول التي يتيحها الحساب الحالي فقط.
  المفضلة وسيلة تنقل وليست صلاحية.
- **مسؤول مبيعات:** يجب أن تتضمن بيئة الاختبار المعزولة مسبقاً صلاحية
  `create_order` المناسبة، وتعيين دور الاعتماد الحالي للوحدة، وأسعاراً معتمدة
  وخيارات عملاء/أصناف ونطاق موقع عند الحاجة. عرض الكمية يحتاج صلاحية
  `read_stock` منفصلة. يعيد الخادم التحقق من كل عملية.
- **المعتمد:** استخدم تعييناً خيالياً موجوداً في بيئة الاختبار فقط. الحدود
  الحالية هي 10% لمسؤول المبيعات و20% للمدير و30% للمدير التجاري العام و50%
  استثنائياً للرئيس التنفيذي. لا تُعيّن أشخاصاً حقيقيين ولا تتجاوز بوابة
  إعدادات المدير الحالية.

## Scenario A — create and review a multi-line quotation
## السيناريو أ — إنشاء عرض سعر متعدد البنود ومراجعته

1. Select the intended authorized business unit and open **Retail & Sales →
   Create a quote** (`#/retail/quote`). Select an existing customer and existing
   item/variant choices only. Check that there is no item-creation or price-edit
   control. If choices, access, or approved prices are unavailable, record
   **Unavailable** and stop; do not work around the gate.

   اختر وحدة العمل المصرح بها وافتح **التجزئة والمبيعات ← إنشاء عرض سعر**
   (`#/retail/quote`). اختر عميلاً وصنفاً/متغيراً موجوداً فقط. تحقق من عدم وجود
   إنشاء صنف أو تعديل سعر. عند غياب الخيارات أو الصلاحية أو السعر المعتمد،
   سجّل الحالة **غير متاح** وتوقف.

2. Add two lines in the unsaved form. Enter synthetic quantities and
   specifications, then add/edit/remove a line. Each line should retain its
   own item, variant, quantity, location where required, specifications, and
   approved selling-price snapshot. Review line amounts and the quote total
   before submission. “Editable draft” here means the **pre-submission form**;
   this feature does not promise in-place edits to an already submitted quote.

   أضف بندين في النموذج غير المحفوظ، وأدخل كميات ومواصفات اصطناعية، ثم أضف
   بنداً أو عدّله أو احذفه. يجب أن يحتفظ كل بند بصنفه ومتغيره وكميته وموقعه
   عند الحاجة ومواصفاته ولقطة سعر البيع المعتمدة. راجع إجمالي كل بند والإجمالي
   قبل الإرسال. المقصود بـ«مسودة قابلة للتعديل» هو النموذج **قبل الإرسال**؛
   لا يَعِد هذا العمل بتعديل عرض مُرسل بالفعل.

3. Use only the current account’s discount ceiling and provide its required
   reason. The existing automated example uses quantities 1.25 and 2.75,
   synthetic QAR prices 19.99 and 10.05, and 5.25% discount; expected total is
   QAR 49.86 under the existing rounding behavior. These are test inputs, not
   a real price list. If using a fixture with other prices, check its approved
   values instead. A price override or a discount above the assigned ceiling
   must be rejected by the server without a partial quote.
   For a validation check, enter a quantity with unsupported precision such as
   `1.001`. Expect a line-specific error, unchanged values on the other draft
   lines, and no partial quote. Exact lost-acknowledgement retries are covered
   by the isolated automated harness; reviewers should not manufacture a
   network failure. A direct unauthorized item/price override must be denied by
   the backend, not merely hidden from the form.

   استخدم حد الخصم المصرح للحساب الحالي وأدخل السبب المطلوب. يستخدم المثال
   الآلي كميات 1.25 و2.75 وأسعاراً اصطناعية بالريال 19.99 و10.05 وخصماً 5.25%؛
   الإجمالي المتوقع 49.86 ريالاً وفق التقريب الحالي. هذه قيم اختبار وليست
   قائمة أسعار فعلية. يجب أن يرفض الخادم تجاوز السعر المعتمد أو حد الخصم دون
   إنشاء عرض جزئي.

4. Review the returned quotation through **Retail & Sales → Quote collection
   & holds** (`#/retail/actions`). Confirm the saved line values, bilingual
   fields and missing-translation notices. Tivoli branding must remain separate
   from the exact registered English name **Tivoli Group L.L.C**; no Arabic
   registered name is supplied or inferred. Check item name, category, image or
   missing-image notice, description, specifications, quantity, fixed price,
   discount, total and reference. Long quotations repeat the reference/page
   count; totals and blank signature spaces appear once on the final screen
   preview page. They are not electronic signatures or a physical-page
   guarantee. Display unit, payment text, validity, delivery estimate, and
   notes are presentation fields; blank values stay unspecified and do not
   alter payment/approval/delivery gates. Terms and Conditions remain pending
   owner-supplied text. This is an **on-screen review preview**, not an issued
   client copy; no quote print/download/export control is provided.

    راجع العرض المحفوظ عبر **التجزئة والمبيعات ← تحصيلات وعروض الأسعار**
    (`#/retail/actions`). تحقق من ظهور علامة Tivoli التجارية منفصلة عن الاسم
    الإنجليزي المسجل **Tivoli Group L.L.C**؛ لم يُقدّم اسم عربي مسجل ولا يجوز
    تخمينه. راجع اسم الصنف وفئته وصورته أو تنبيه غيابها ووصفه ومواصفاته وكميته
    وسعره الثابت وخصمه وإجماليه ورقم المرجع. يتكرر المرجع ورقم الصفحة في
    العروض الطويلة؛ وتظهر الإجماليات ومساحات التوقيع الفارغة في صفحة المعاينة
    الأخيرة فقط. هذه ليست توقيعات إلكترونية أو ضماناً لتنسيق صفحات مطبوعة.
    وحدة العرض ونص الدفع والصلاحية وتقدير التسليم والملاحظات حقول عرض فقط؛
    وتظل القيم الفارغة غير محددة ولا تغير ضوابط الدفع أو الاعتماد أو التسليم.
    تظل الشروط والأحكام بانتظار نص المالك. هذه **معاينة على الشاشة للمراجعة**
    وليست نسخة صادرة للعميل؛ ولا توفر طباعة أو تنزيل أو تصديراً للعرض.

## Scenario B — simulated collection, reservation, readiness and delivery
## السيناريو ب — تحصيل اصطناعي وحجز وجاهزية وتسليم

Use only the configured disposable example if it is present:
`TEST-CUST-RETAIL-01`, `TVF-002-LIN`, `STIV-G`, two units at
QAR 2,000 each, with the existing permitted 5% discount. The example total is
QAR 3,800. If your fixture differs, use its existing approved values; do not
change the price to match this example.

استخدم المثال الاصطناعي المؤقت فقط إذا كان مهيأً: العميل
`TEST-CUST-RETAIL-01`، والصنف `TVF-002-LIN`، والموقع
`STIV-G`، وحدتان بسعر 2,000 ريال لكل منهما، وخصم 5% المسموح.
إجمالي المثال 3,800 ريال. إذا اختلفت بيانات الاختبار، استخدم القيم المعتمدة
الموجودة ولا تغيّر الأسعار لمطابقة المثال.

1. Request a three-working-day temporary hold in **Quote collection & holds**.
   Expected: the existing hold intent is shown. It does not itself reserve
   ledger stock. Whether a hold blocks another sale is pending owner
   clarification; do not test or claim a blocking rule.
2. Record the existing simulated deposit of QAR 1,900 only through the
   authorized action in the disposable fixture. Expected: the 50% threshold
   records a simulated collection and reserves the two available units;
   on-hand quantity does not decrease at reservation. This is not a real
   payment.
3. In **Delivery & After-Sales → Authorized quote lookup**, load the same quote
   and open **Readiness & handover actions**. Record readiness through the
   existing gate. Try handover while only half is collected: expected denial
   “Full payment required,” with no delivery or stock change.
4. Record the remaining QAR 1,900 simulated collection and retry handover
   only when allowed. Expected: the quote is delivered once; two units are
   delivered, the reservation is consumed once, remaining quantity is zero,
   and the quote remains delivered on an unchanged idempotent retry. Do not
   manufacture a network failure or replay an acknowledged delivery in the
   shared demo.
5. Review **Retail & Sales → Collection follow-up** (`#/retail/collections`).
   Expected: server-returned recorded merchandise collections and remaining
   balance are distinct from storage collections. This read-only page does not
   create business records and does not represent real payments, receivables,
   overdue status, or credit terms.

1. اطلب حجزاً مؤقتاً لثلاثة أيام عمل عبر **تحصيلات وعروض الأسعار**. المتوقع:
   يظهر طلب الحجز الحالي، لكنه لا يحجز كمية المخزون الدفترية. لا تزال مسألة
   منع بيع آخر أثناء الحجز بانتظار توضيح المالك؛ لا تختبر قاعدة منع غير معتمدة.
2. سجّل دفعة مقدمة اصطناعية قدرها 1,900 ريال فقط عبر الإجراء المصرح في بيئة
   الاختبار المؤقتة. المتوقع: يسجل حد 50% تحصيلاً اصطناعياً ويحجز الوحدتين
   المتاحتين، دون إنقاص كمية المخزون عند الحجز. ليست هذه دفعة حقيقية.
3. في **التسليم وما بعد البيع ← البحث المصرح عن عرض السعر**، حمّل العرض نفسه
   وافتح **إجراءات الجاهزية والتسليم النهائي**. سجّل الجاهزية وفق البوابة
   الحالية. يجب رفض التسليم النهائي عند تحصيل نصف المبلغ برسالة **يلزم سداد
   المبلغ كاملاً**، دون إنشاء تسليم أو تغيير للمخزون.
4. سجّل باقي المبلغ كتحصيل اصطناعي قدره 1,900 ريال ثم نفّذ التسليم عند السماح.
   المتوقع: يسجل العرض مرة واحدة، وتُسلّم وحدتان، ويُستهلك الحجز مرة واحدة،
   وتصبح الكمية المتبقية صفراً. تبقى حالة العرض «تم التسليم» عند إعادة الطلب
   المطابق بمفتاح idempotency نفسه. لا تصطنع فشل شبكة أو تكرر تسليماً مؤكداً
   في بيئة العرض المشتركة.
5. راجع **التجزئة والمبيعات ← متابعة التحصيل** (`#/retail/collections`).
   المتوقع: تظهر تحصيلات البضائع المسجلة والرصيد المتبقي من الخادم، منفصلة عن
   تحصيلات التخزين. الصفحة للقراءة فقط ولا تمثل مدفوعات حقيقية أو ذمماً
   محاسبية أو مبالغ متأخرة أو شروط ائتمان.

## Results to record | تسجيل نتائج المراجعة

For each row record the **synthetic test role and BU**, desktop/mobile width,
expected result, observed result, and pass/fail/blocked. Do not enter personal
or customer data, passwords, payment details, or real quote references.

سجّل لكل بند **الدور الاصطناعي ووحدة العمل** وعرض الشاشة والنتيجة المتوقعة
والفعلية وحالة النجاح/الفشل/التوقف. لا تُدخل بيانات شخصية أو بيانات عملاء
حقيقية أو كلمات مرور أو تفاصيل دفع.

| Check / الفحص | Expected / المتوقع | Result / النتيجة | Severity / الشدة | Feedback / الملاحظات |
| --- | --- | --- | --- | --- |
| Existing choices and access / الخيارات والصلاحيات الحالية | Authorized values only; missing setup stays unavailable / القيم المصرح بها فقط؛ والإعداد الناقص يظل غير متاح | Pass / Fail / Blocked | — | |
| Editable pre-submit multi-line draft / مسودة متعددة البنود قبل الإرسال | Line edits stay separate; validation/rejection creates no partial quote / تبقى تعديلات البنود منفصلة ولا ينشأ عرض جزئي عند الرفض | Pass / Fail / Blocked | — | |
| Fixed price and discount authority / السعر الثابت وصلاحية الخصم | Approved snapshot unchanged; server rejects override/excess / لا تتغير لقطة السعر ويرفض الخادم التجاوز | Pass / Fail / Blocked | — | |
| Simulated collection and reservation / التحصيل الاصطناعي والحجز | No real payment; reservation and stock changes follow existing gates / لا دفع حقيقي؛ وتتبع الكميات البوابات الحالية | Pass / Fail / Blocked | — | |
| Readiness/full-payment handover / الجاهزية والتسليم بعد السداد الكامل | Underpayment denied without writes; exact retry does not duplicate / يُرفض نقص السداد دون كتابة ولا تكرار عند إعادة الطلب | Pass / Fail / Blocked | — | |
| Scope and sensitive fields / النطاق والحقول الحساسة | BU/location denial enforced; ordinary sales response contains no cost/history/margin / يطبق رفض الوحدة والموقع ولا تظهر التكاليف أو السجل أو الهامش | Pass / Fail / Blocked | — | |
| Desktop/mobile and bilingual preview / العرض المكتبي والهاتف والمعاينة الثنائية | Labels, direction and missing translations are clear; review-only wording / وضوح التسميات والاتجاه والترجمات الناقصة والتنبيه إلى المعاينة | Pass / Fail / Blocked | — | |

**Severity / الشدة:** `Blocker / مانع` for a permission leak or duplicate
business result; `High / عالية` if a supported core action, amount, or state is
wrong; `Medium / متوسطة` for confusing workflow/error feedback or a mobile
usability problem; `Low / منخفضة` for a non-blocking wording/spacing issue.
Severity is a reviewer triage label, not a company approval rule.

## Verified boundaries and unresolved dependencies
## حدود التحقق والقرارات المعلقة

- **Previously verified in isolation:** the handoff records a real authenticated
  Chromium journey against Django and disposable PostgreSQL 16.15 at 1280px
  and 320px, plus focused PostgreSQL and rendered regressions. The recorded
  combined journey used two quote lines, rejected overpayment, exact retry,
  reservation, collection follow-up, readiness, full-payment handover, delivery
  quantities, BU denial, and revoked-access checks. The guide and UI wording
  correction do not rerun that journey.
- **Not team-accepted:** no team member’s pass/fail feedback is recorded here.
  A healthy sign-in page or a denied shared-demo state is not a successful
  populated sales journey.
- **Policy/configuration dependent:** temporary holds remain intent-only while
  stock-blocking policy is pending; special-order value bands remain
  unconfigured; only existing fictional approval assignments are used.
  Terms and Conditions, approved company contact/Arabic legal-name content,
  and quotation print/download/export entitlement remain pending. Refunds,
  credit notes, invoice/AR posting, partial delivery, actual payments, Finance
  approvals and production access are not added. The 14-day return intake is a
  provisional owner-provided business rule counted from delivery, not a
  verified statement of Qatar law; Finance/legal validation and exception/
  disposition rules remain pending.
- **Not production-ready:** prototype tests and a human pilot cannot certify
  security, accounting, law, uptime, or operational readiness.

- **تم التحقق سابقاً في عزلة:** يسجل ملف التسليم اختبار Chromium مسجلاً
  فعلياً مع Django وقاعدة PostgreSQL 16.15 مؤقتة عند 1280 و320 بكسل، إضافة
  إلى اختبارات PostgreSQL والواجهة. شمل المثال بندين ورفض المبلغ الزائد
  وإعادة الطلب المطابقة والحجز ومتابعة التحصيل والجاهزية والتسليم بعد السداد
  وكميات التسليم ورفض الوحدة الأخرى وسحب الصلاحية. لا يعيد هذا الدليل أو
  تعديل النص تشغيل تلك الرحلة.
- **لم يقبله الفريق بعد:** لا توجد نتيجة قبول أو ملاحظات موثقة من أعضاء
  الفريق هنا. نجاح صفحة الدخول أو ظهور حالة رفض في قاعدة العرض لا يثبت نجاح
  رحلة مبيعات كاملة.
- **قرارات/إعدادات معلقة:** تظل الحجوزات المؤقتة نوايا فقط إلى أن يُحسم أثرها
  على المخزون؛ وتظل نطاقات قيمة الطلب الخاص غير مهيأة. لم تُضف شروط وأحكام أو
  استردادات أو فواتير أو تسجيل محاسبي أو تسليم جزئي أو دفعات حقيقية أو صلاحيات
  إنتاج. قاعدة 14 يوماً هي قاعدة عمل مؤقتة من المالك تُحسب من تاريخ التسليم،
  وليست بياناً مؤكداً عن قانون قطر؛ ولا تزال مراجعة المالية/القانون والبيانات
  اللازمة معلقة.

Traceable detailed cases and recorded evidence remain in
[`RETAIL_TEAM_ACCEPTANCE.md`](RETAIL_TEAM_ACCEPTANCE.md),
[`QUOTATION_DOCUMENT_COMPLETENESS.md`](QUOTATION_DOCUMENT_COMPLETENESS.md),
[`BUSINESS_READINESS_CHECKLIST.md`](BUSINESS_READINESS_CHECKLIST.md), and
[`REVIEW_HANDOFF.md`](REVIEW_HANDOFF.md).

### Evidence index | فهرس الأدلة

These are **previously recorded** results for the implemented pilot behavior,
not tests rerun to write this guide:

- The handoff section **“2026-10-20 — Combined multi-item
  Retail-to-Delivery acceptance”** records **2/2** authenticated real-browser
  journeys at **1280px and 320px**, against real Django APIs and disposable
  PostgreSQL 16.15. It also records **5/5**
  `foundation.test_delivery_operations` and **6/6**
  `foundation.test_retail_multiline` tests.
- That same handoff entry records **131/131** Purchasing UI,
  **9/9** Retail UI, **6/6** collection-follow-up UI, and **13/13**
  Delivery UI cases; typecheck/build passed and contrast checks were
  **175/175**. These prior tests do not establish team acceptance.
- The underlying native journeys are in
  `scripts/retail-real-browser.test.mjs`; backend cases are in
  `foundation/test_delivery_operations.py` and
  `foundation/test_retail_multiline.py`. Rendered quotation-preview coverage
  is in `scripts/purchasing-quote-lines-ui.test.tsx`.
- For **this guide and the preview wording correction only**,
  `REVIEW_HANDOFF.md` records `npm run test:purchasing-quote-lines`
  (**22/22**), `npm run typecheck`, `npm run build`,
  `npm run test:contrast` (**175/175**), `git diff --check`, and local Markdown
  link validation. No backend suite or authenticated browser was rerun.
