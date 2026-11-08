"""The commission and reward calculations of the TOS-V2 incentive brief, as plain functions.

Nothing here reads or writes the database. Every function takes figures and the policy settings and
returns a Result: an amount, or "pending" with the reason (a missing input or a target of zero or
less never becomes an assumed achievement), plus the steps that explain it.

Every rate, band and weight below is the brief's working figure, marked Finance-test and
provisional. They are copied into each incentive year as its policy, where they can be changed and
approved; changing them here does not change a year already set up.

Step bands: each threshold applies from itself until the next one, with no in-between values.
Achievement is actual / target x 100, kept exact (not rounded) when it is compared with a band.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")
CENT = Decimal("0.01")

PLANS = {
    "sales_commission": "Sales commission",
    "supervisor_hybrid": "Supervisor hybrid",
    "business_growth": "Business growth",
    "management_bonus": "Management bonus",
    "performance_reward": "Performance reward",
}
CHANNELS = {"retail": "Retail", "wholesale": "Wholesale", "studio": "Studio", "": "No sales channel"}
RATINGS = {
    "unsatisfactory": "Unsatisfactory",
    "needs_improvement": "Needs improvement",
    "meets_most": "Meets most expectations",
    "meets": "Meets expectations",
    "exceeds": "Exceeds expectations",
    "exceptional": "Exceptional",
}
CATEGORIES = {
    "operations": "Factory, warehouse, operations and delivery, maintenance",
    "hub_admin": "Hub and administration",
    "creative_tender": "Creative, tender and estimation",
    "smart_living_non_sales": "Smart Living (not sales)",
    "general_management": "General management",
}
OPERATIONAL_PARTS = {
    "collections": "Collections and cash flow",
    "gp_margin": "GP and margin",
    "inventory": "Inventory improvement",
    "strategic": "Strategic execution",
}


def _bands(*pairs):
    return [{"from": str(start), "value": str(value)} for start, value in pairs]


DEFAULT_POLICY = {
    "label": "TOS-V2 working model (Finance-test figures, provisional until Finance, HR and management approve)",
    "cpi_weights": {"revenue": "40", "ebitda": "40", "operational": "20"},
    "revenue_score": _bands((0, 0), (85, 40), (90, 60), (95, 80), (100, 100), (105, 110), (110, 125), (120, 150)),
    "ebitda_score": _bands((0, 0), (60, 30), (70, 50), (80, 70), (90, 90), (100, 100), (110, 115), (120, 130), (130, 150)),
    "operational_score": _bands((0, 0), (80, 50), (90, 80), (100, 100), (110, 125)),
    "company_factor": _bands((0, 0), (70, 25), (80, 50), (90, 75), (95, 90), (100, 100), (105, 110), (110, 125), (120, 150)),
    "commission_gate": "100",
    "commission_schedules": {
        "retail": [
            {"from": "0", "to": "100", "rate": "0.70"},
            {"from": "100", "to": "110", "rate": "0.85"},
            {"from": "110", "to": "120", "rate": "1.00"},
            {"from": "120", "to": "130", "rate": "1.15"},
            {"from": "130", "to": "140", "rate": "1.30"},
            {"from": "140", "to": "150", "rate": "1.40"},
            {"from": "150", "to": "", "rate": "1.50"},
        ],
    },
    "gp_factor": _bands((0, 0), (90, 75), (95, 90), (100, 100)),
    "sales_cpi_bonus": _bands((0, 0), (100, 5), (105, "7.5"), (110, 10), (120, 15)),
    "hybrid": {"personal": "40", "team": "60"},
    "instalments": [{"month": 4, "percent": "40"}, {"month": 8, "percent": "30"}, {"month": 12, "percent": "30"}],
    "reward_bands": {"PR1": "1500", "PR2": "2500", "PR3": "3000", "PR4": "4000", "PR5": "6000", "PR6": "7500",
                     "M1": "18000", "M2": "30000", "M3": "45000"},
    "rating_factors": {"unsatisfactory": "0", "needs_improvement": "50", "meets_most": "80", "meets": "100",
                       "exceeds": "110", "exceptional": "120"},
    "category_weights": {
        "operations": {"company": "50", "unit": "30", "individual": "20"},
        "hub_admin": {"company": "50", "unit": "25", "individual": "25"},
        "creative_tender": {"company": "40", "unit": "40", "individual": "20"},
        "smart_living_non_sales": {"company": "40", "unit": "40", "individual": "20"},
        "general_management": {"company": "60", "unit": "25", "individual": "15"},
    },
    "max_multiplier": "150",
    # The brief's provisional general BU factor. Its caps are not given; 150% is a placeholder.
    "unit_factor": {"revenue": "25", "gp": "35", "collections": "15", "operational": "25", "component_cap": "150",
                    "overall_cap": "150"},
}


def default_policy():
    return deepcopy(DEFAULT_POLICY)


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def text(value):
    """A Decimal as plain text without trailing zeros (for percentages and factors)."""
    if value is None:
        return ""
    value = Decimal(value)
    return format(value.quantize(Decimal(1)) if value == value.to_integral() else value.normalize(), "f")


@dataclass
class Result:
    amount: Decimal | None = None
    pending: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    figures: dict = field(default_factory=dict)

    @property
    def is_pending(self):
        return bool(self.pending)


def _d(value):
    if value is None or value == "":
        return None
    return Decimal(str(value))


def achievement(actual, target):
    """actual / target x 100, or None when either is missing or the target is zero or less."""
    actual, target = _d(actual), _d(target)
    if actual is None or target is None or target <= 0:
        return None
    return actual * HUNDRED / target


def why_no_achievement(label, actual, target):
    if _d(target) is None:
        return f"{label}: the target is missing."
    if _d(target) <= 0:
        return f"{label}: the target is zero or less, so achievement cannot be worked out."
    if _d(actual) is None:
        return f"{label}: the actual figure is missing."
    return ""


def band(value, bands):
    """The value of the highest band whose start is at or below value (step bands, no interpolation)."""
    picked = ZERO
    for row in sorted(bands, key=lambda row: Decimal(row["from"])):
        if value >= Decimal(row["from"]):
            picked = Decimal(row["value"])
    return picked


def pct(value):
    return f"{text(Decimal(value).quantize(Decimal('0.01')))}%"


# ---------------------------------------------------------------- Company Performance Index


def company_index(policy, *, revenue_target, revenue_actual, ebitda_target, ebitda_actual, operational):
    """CPI = 40% revenue score + 40% EBITDA score + 20% operational score, then the company reward factor.

    operational: {"collections", "gp_margin", "inventory", "strategic"} achievements in %, each scored and averaged.
    figures: revenue/ebitda achievement and score, operational scores, cpi, company_factor, negative_ebitda.
    """
    result = Result()
    rev = achievement(revenue_actual, revenue_target)
    ebitda = achievement(ebitda_actual, ebitda_target)
    if rev is None:
        result.pending.append(why_no_achievement("Company revenue", revenue_actual, revenue_target))
    if ebitda is None:
        result.pending.append(why_no_achievement("Company EBITDA", ebitda_actual, ebitda_target))
    parts = {}
    for key, label in OPERATIONAL_PARTS.items():
        value = _d((operational or {}).get(key))
        if value is None:
            result.pending.append(f"Operational score: {label} is missing.")
        else:
            parts[key] = value
    negative = _d(ebitda_actual) is not None and _d(ebitda_actual) < 0
    result.figures["negative_ebitda"] = negative
    if result.is_pending:
        return result
    weights = {key: Decimal(value) for key, value in policy["cpi_weights"].items()}
    rev_score = band(rev, policy["revenue_score"])
    ebitda_score = band(ebitda, policy["ebitda_score"])
    op_scores = {key: band(value, policy["operational_score"]) for key, value in parts.items()}
    op_score = sum(op_scores.values(), ZERO) / len(op_scores)
    cpi = (weights["revenue"] * rev_score + weights["ebitda"] * ebitda_score + weights["operational"] * op_score) / HUNDRED
    factor = ZERO if negative else band(cpi, policy["company_factor"])
    result.figures.update({
        "revenue_achievement": rev, "revenue_score": rev_score, "ebitda_achievement": ebitda, "ebitda_score": ebitda_score,
        "operational_scores": op_scores, "operational_score": op_score, "cpi": cpi, "company_factor": factor,
    })
    result.steps += [
        f"Revenue {pct(rev)} of target scores {pct(rev_score)}.",
        f"EBITDA {pct(ebitda)} of target scores {pct(ebitda_score)}.",
        "Operational score is the average of " + ", ".join(
            f"{OPERATIONAL_PARTS[key].lower()} {pct(parts[key])} → {pct(score)}" for key, score in op_scores.items())
        + f" = {pct(op_score)}.",
        f"CPI = {text(weights['revenue'])}% × {pct(rev_score)} + {text(weights['ebitda'])}% × {pct(ebitda_score)} + "
        f"{text(weights['operational'])}% × {pct(op_score)} = {pct(cpi)}.",
        ("Actual EBITDA is negative, so the company reward factor is 0% and there is no sales CPI bonus."
         if negative else f"CPI {pct(cpi)} gives a company reward factor of {pct(factor)}."),
    ]
    result.amount = factor
    return result


# ---------------------------------------------------------------- unit factor


def unit_factor(policy, *, revenue_target, revenue_actual, gp_target, gp_actual, collections_target, collections_actual,
                operational):
    """The brief's provisional general BU factor: weighted achievements, each capped, and the total capped."""
    result = Result()
    settings = {key: Decimal(value) for key, value in policy["unit_factor"].items()}
    cap = settings["component_cap"]
    parts = {
        "revenue": ("Unit revenue", revenue_actual, revenue_target),
        "gp": ("Unit GP", gp_actual, gp_target),
        "collections": ("Unit collections", collections_actual, collections_target),
    }
    scores = {}
    for key, (label, actual, target) in parts.items():
        value = achievement(actual, target)
        if value is None:
            result.pending.append(why_no_achievement(label, actual, target))
        else:
            scores[key] = min(value, cap)
    if _d(operational) is None:
        result.pending.append("Unit operational achievement is missing.")
    else:
        scores["operational"] = min(Decimal(str(operational)), cap)
    if result.is_pending:
        return result
    total = sum((settings[key] * value for key, value in scores.items()), ZERO) / HUNDRED
    factor = min(total, settings["overall_cap"])
    result.steps.append("Unit factor = " + " + ".join(f"{text(settings[key])}% × {pct(value)}" for key, value in scores.items())
                        + f" = {pct(total)}" + (f", capped at {pct(factor)}." if factor != total else "."))
    result.figures = {"scores": scores, "unit_factor": factor}
    result.amount = factor
    return result


# ---------------------------------------------------------------- commission


def progressive(revenue, target, schedule):
    """Each rate applies only to its own slice of revenue (as % of target). Returns (amount, steps)."""
    amount, steps = ZERO, []
    for row in schedule:
        low = Decimal(row["from"]) * target / HUNDRED
        high = Decimal(row["to"]) * target / HUNDRED if row["to"] not in ("", None) else None
        top = revenue if high is None else min(revenue, high)
        if top <= low:
            continue
        slice_ = top - low
        part = slice_ * Decimal(row["rate"]) / HUNDRED
        amount += part
        upper = f"{row['to']}%" if high is not None else "and above"
        steps.append(f"{row['from']}% to {upper} of target: {money(slice_):,} × {row['rate']}% = {money(part):,}")
    return amount, steps


def personal_commission(policy, *, channel, revenue_target, revenue_actual, gp_target, gp_actual, collection_fraction,
                        label="Personal"):
    """Progressive commission × GP factor × Finance-approved collection-eligible fraction.

    figures: achievement, achieved (gate met), progressive, gp_factor, base (the validated commission).
    """
    result = Result()
    schedule = policy["commission_schedules"].get(channel or "")
    if not schedule:
        result.pending.append(f"There is no approved commission schedule for the {CHANNELS.get(channel or '', channel)} channel yet.")
    reached = achievement(revenue_actual, revenue_target)
    if reached is None:
        result.pending.append(why_no_achievement(f"{label} revenue", revenue_actual, revenue_target))
    fraction = _d(collection_fraction)
    if fraction is None:
        result.pending.append(f"{label}: the collection-eligible fraction approved by Finance is missing.")
    elif not ZERO <= fraction <= 1:
        result.pending.append(f"{label}: the collection-eligible fraction must be between 0 and 1.")
    if result.is_pending:
        return result
    gate = Decimal(policy["commission_gate"])
    revenue, target = Decimal(str(revenue_actual)), Decimal(str(revenue_target))
    result.figures.update({"achievement": reached, "achieved": reached >= gate})
    if reached < gate:
        result.steps.append(f"{label} revenue {money(revenue):,} is {pct(reached)} of the {money(target):,} target, below the "
                            f"{text(gate)}% gate, so there is no commission.")
        result.figures.update({"progressive": ZERO, "gp_factor": None, "base": ZERO})
        result.amount = ZERO
        return result
    gp = achievement(gp_actual, gp_target)
    if gp is None:
        result.pending.append(why_no_achievement(f"{label} GP", gp_actual, gp_target))
        return result
    amount, steps = progressive(revenue, target, schedule)
    gp_factor = band(gp, policy["gp_factor"])
    base = amount * gp_factor / HUNDRED * fraction
    result.steps.append(f"{label} revenue {money(revenue):,} is {pct(reached)} of the {money(target):,} target.")
    result.steps += steps
    result.steps.append(f"Progressive commission {money(amount):,}; GP {pct(gp)} of target gives a GP factor of {pct(gp_factor)}; "
                        f"collection-eligible fraction {text(fraction)}: {money(amount):,} × {pct(gp_factor)} × {text(fraction)} "
                        f"= {money(base):,}.")
    result.figures.update({"progressive": amount, "gp_achievement": gp, "gp_factor": gp_factor, "base": base})
    result.amount = base
    return result


def cpi_bonus(policy, *, base, achieved, company):
    """The additional sales CPI bonus on validated commission: only when the personal target is met and EBITDA is not negative."""
    if not achieved or base <= 0:
        return ZERO, ""
    if company.figures.get("negative_ebitda"):
        return ZERO, "Actual EBITDA is negative, so there is no sales CPI bonus."
    rate = band(company.figures["cpi"], policy["sales_cpi_bonus"])
    bonus = base * rate / HUNDRED
    return bonus, f"Sales CPI bonus: CPI {pct(company.figures['cpi'])} gives {pct(rate)} of {money(base):,} = {money(bonus):,}."


def sales_commission(policy, *, company, channel, revenue_target, revenue_actual, gp_target, gp_actual, collection_fraction):
    result = personal_commission(policy, channel=channel, revenue_target=revenue_target, revenue_actual=revenue_actual,
                                 gp_target=gp_target, gp_actual=gp_actual, collection_fraction=collection_fraction)
    if result.is_pending:
        return result
    if company.is_pending and result.figures["achieved"] and result.amount > 0:
        result.pending.append("The Company Performance Index is pending, so the sales CPI bonus cannot be worked out.")
        return result
    bonus, step = (ZERO, "") if company.is_pending else cpi_bonus(policy, base=result.amount, achieved=result.figures["achieved"],
                                                                  company=company)
    if step:
        result.steps.append(step)
    result.figures.update({"cpi_bonus": bonus, "referral_base": result.amount})
    result.amount = money(result.amount + bonus)
    return result


def supervisor_hybrid(policy, *, company, channel, personal, team, team_share):
    """40% of independently worked-out personal commission + 60% of the allocated team commission.

    personal / team: dicts of revenue_target, revenue_actual, gp_target, gp_actual, collection_fraction.
    The CPI bonus applies to the weighted personal part only, when the personal target is met.
    """
    result = Result()
    weights = {key: Decimal(value) for key, value in policy["hybrid"].items()}
    mine = personal_commission(policy, channel=channel, label="Personal", **personal)
    pool = personal_commission(policy, channel=channel, label="Team", **team) if team else None
    if pool is None:
        result.pending.append("The supervisor is not linked to a team.")
    share = _d(team_share)
    if share is None:
        result.pending.append("The supervisor's share of the team pool is missing.")
    result.pending += mine.pending + (pool.pending if pool else [])
    if result.is_pending:
        return result
    weighted_personal = mine.amount * weights["personal"] / HUNDRED
    if company.is_pending and mine.figures["achieved"] and weighted_personal > 0:
        result.pending.append("The Company Performance Index is pending, so the sales CPI bonus cannot be worked out.")
        return result
    team_part = pool.amount * weights["team"] / HUNDRED * share / HUNDRED
    bonus, step = (ZERO, "") if company.is_pending else cpi_bonus(policy, base=weighted_personal,
                                                                  achieved=mine.figures["achieved"], company=company)
    result.steps += mine.steps
    result.steps.append(f"Weighted personal part: {text(weights['personal'])}% × {money(mine.amount):,} = {money(weighted_personal):,}.")
    if step:
        result.steps.append(step)
    result.steps += pool.steps
    result.steps.append(f"Team part: {text(weights['team'])}% × {money(pool.amount):,} × {text(share)}% share = {money(team_part):,}.")
    result.figures = {"personal": mine.amount, "team_pool": pool.amount, "team_part": team_part, "cpi_bonus": bonus,
                      "referral_base": weighted_personal, "achieved": mine.figures["achieved"]}
    result.amount = money(weighted_personal + bonus + team_part)
    return result


# ---------------------------------------------------------------- fixed QAR rewards


def fixed_reward(policy, *, company, unit, band_code, category, rating, proration):
    """Approved fixed QAR target × weighted performance factor × proration.

    The weighted factor is capped at the maximum multiplier. With a company factor of zero there is no reward,
    whatever the unit and individual factors are.
    """
    result = Result()
    target = _d(policy["reward_bands"].get(band_code or ""))
    if target is None:
        result.pending.append("The reward band is missing." if not band_code else f"There is no amount for reward band {band_code}.")
    weights = policy["category_weights"].get(category or "")
    if weights is None:
        result.pending.append("The category is missing." if not category
                              else "The weights for this category are not confirmed yet.")
    factor = _d(policy["rating_factors"].get(rating or ""))
    if factor is None:
        result.pending.append("The individual performance rating is missing.")
    if _d(proration) is None:
        result.pending.append("The proration is missing.")
    if company.is_pending:
        result.pending.append("The Company Performance Index is pending.")
    if result.is_pending:
        return result
    company_factor = company.amount
    if company_factor == 0:
        result.steps.append("The company reward factor is 0%, so there is no fixed reward this year.")
        result.figures = {"target": target, "weighted": ZERO}
        result.amount = ZERO
        return result
    if unit is None or unit.is_pending:
        result.pending += (unit.pending if unit else ["The unit's figures are missing."])
        return result
    w = {key: Decimal(value) for key, value in weights.items()}
    weighted = (w["company"] * company_factor + w["unit"] * unit.amount + w["individual"] * factor) / HUNDRED
    capped = min(weighted, Decimal(policy["max_multiplier"]))
    reward = target * capped / HUNDRED * Decimal(str(proration))
    result.steps += [
        f"Weighted factor = {text(w['company'])}% × company {pct(company_factor)} + {text(w['unit'])}% × unit {pct(unit.amount)} "
        f"+ {text(w['individual'])}% × individual {pct(factor)} = {pct(weighted)}"
        + (f", capped at {pct(capped)}." if capped != weighted else "."),
        f"Reward = {money(target):,} × {pct(capped)} × proration {text(Decimal(str(proration)))} = {money(reward):,}.",
    ]
    result.figures = {"target": target, "weighted": capped}
    result.amount = money(reward)
    return result


# ---------------------------------------------------------------- payment schedule


def instalments(policy, amount):
    """Commission instalments for the following year: [(month, amount)]; every one but the last is rounded, the last is the rest."""
    amount = money(amount)
    rows = policy["instalments"]
    out, paid = [], ZERO
    for index, row in enumerate(rows):
        if index == len(rows) - 1:
            part = amount - paid
        else:
            part = money(amount * Decimal(row["percent"]) / HUNDRED)
            paid += part
        out.append((int(row["month"]), part))
    return out


def check_policy(policy):
    """Problems with a policy before it is saved: weights that do not add to 100, instalments that do not add to 100."""
    problems = []
    if sum(Decimal(value) for value in policy["cpi_weights"].values()) != HUNDRED:
        problems.append("The CPI weights must add up to 100%.")
    if sum(Decimal(row["percent"]) for row in policy["instalments"]) != HUNDRED:
        problems.append("The commission instalments must add up to 100%.")
    if sum(Decimal(value) for value in policy["hybrid"].values()) != HUNDRED:
        problems.append("The supervisor personal and team parts must add up to 100%.")
    for key, weights in policy["category_weights"].items():
        if sum(Decimal(value) for value in weights.values()) != HUNDRED:
            problems.append(f"The weights for {CATEGORIES.get(key, key).lower()} must add up to 100%.")
    if sum(Decimal(policy["unit_factor"][key]) for key in ("revenue", "gp", "collections", "operational")) != HUNDRED:
        problems.append("The unit factor weights must add up to 100%.")
    return problems
