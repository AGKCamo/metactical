# Copyright (c) 2026, Techlift and contributors
# For license information, please see license.txt
"""Landed cost, margin and suggested-price maths for Price Revision.

Deliberately free of ``frappe`` imports so the arithmetic can be unit tested
without a site.  Everything here is pure: callers pass values in, get values
back.

Landed cost, as agreed with ICL (2026-09-17):

    landed (supplier currency) = cost * (1 + duty_rate / 100)
    landed (company currency)  = landed_supplier * conversion_rate

Freight is **not** included.  Most orders carry none, and the ones that do are
marked up heavily by hand instead.

The conversion rate is the one stamped on the Purchase Order — the rate the
goods were actually bought at — not today's rate.  It is stored on the revision
so a margin can be reproduced months later.
"""

from __future__ import annotations

# Selling lists that are not margin-bearing retail: the franchise list (other
# ICL companies buy at it) and the wholesale list.  They run thin on purpose,
# so the low-margin flag would cry wolf on every single line.
LOW_MARGIN_PRICE_LISTS = frozenset({
	"RET - CamoFRN - USD",
	"RET - GearStock - USD",
})

ROUNDING_RULES = ("0.99", "0.95", "whole", "none")


def _f(value) -> float:
	"""Frappe hands back None, '' and Decimal interchangeably."""
	if value is None or value == "":
		return 0.0
	return float(value)


def landed_cost(cost, duty_pct=0, conversion_rate=1.0):
	"""Landed cost in both the supplier's currency and the company's.

	``conversion_rate`` converts supplier currency to company currency, i.e.
	the Purchase Order's own ``conversion_rate``.  Returns a dict rather than a
	tuple — the Server Script sandbox on deverp cannot unpack sequences, and
	keeping the shapes identical across both codebases avoids surprises.
	"""
	base = _f(cost) * (1.0 + _f(duty_pct) / 100.0)
	return {
		"supplier_ccy": base,
		"company_ccy": base * (_f(conversion_rate) or 1.0),
	}


def landed_in_currency(landed, target_currency, supplier_currency, company_currency):
	"""Pick the right side of :func:`landed_cost` for a given price list.

	A price list quoted in the supplier's own currency needs no conversion at
	all — converting to CAD and back would only introduce rounding drift.
	Anything in a third currency is refused rather than guessed; the caller
	surfaces it as a line needing attention.
	"""
	if target_currency == company_currency:
		return landed["company_ccy"]
	if target_currency == supplier_currency:
		return landed["supplier_ccy"]
	return None


def margin_pct(price, landed):
	"""Gross margin as a percentage of the selling price.

	Returns ``None`` when there is no price to take a margin on, which reads
	as "unknown" on the grid rather than a misleading 0%.
	"""
	price = _f(price)
	if price <= 0:
		return None
	return (price - _f(landed)) / price * 100.0


def apply_rounding(price, rule="0.99"):
	"""Round a suggested price to the ending the banner actually uses.

	74-86% of live RET prices end in .99, so an unrounded suggestion reads as
	obviously wrong to whoever reviews it.
	"""
	price = _f(price)
	if price <= 0 or rule in (None, "", "none"):
		return price
	if rule == "whole":
		import math
		return float(math.ceil(price - 1e-9))
	if rule in ("0.99", "0.95"):
		cents = 0.99 if rule == "0.99" else 0.95
		whole = int(price)
		# Always round UP to the next ending, never to the nearest one. A
		# suggestion that rounds down hands back margin the cost increase was
		# meant to recover, and does it invisibly.
		candidate = whole + cents
		if price > candidate + 1e-9:
			candidate = whole + 1 + cents
		return round(candidate, 2)
	return price


def suggest_preserve_margin(old_price, old_landed, new_landed, rule="0.99"):
	"""Hold the existing margin percentage across a cost change.

	Needs no configuration, so it is always available even for a
	supplier/price-list pair the matrix has never seen.
	"""
	old_price = _f(old_price)
	old_landed = _f(old_landed)
	if old_price <= 0 or old_landed <= 0:
		return None
	m = (old_price - old_landed) / old_price
	if m >= 1.0:
		return None
	return apply_rounding(_f(new_landed) / (1.0 - m), rule)


def suggest_markup(new_landed, markup, rule="0.99"):
	"""Price from the markup matrix: landed cost times the learned multiplier."""
	markup = _f(markup)
	if markup <= 0:
		return None
	return apply_rounding(_f(new_landed) * markup, rule)


def build_price_line(
	*,
	price_list,
	list_currency,
	old_price,
	old_landed,
	new_landed,
	markup=None,
	rounding="0.99",
	min_margin_pct=None,
	cost_went_down=False,
):
	"""Everything the grid shows for one item on one price list.

	``cost_went_down`` implements ICL's hold rule: when a cost falls the retail
	price is left alone and the margin improves.  The suggestion is still
	calculated and shown, so a buyer can take it deliberately, but it is not
	proposed as the default action.

	The franchise and wholesale lists are exempt from the low-margin flag but
	*not* from the never-below-landed rule — the franchise price in particular
	must always sit above landed cost, since other ICL companies buy at it.
	"""
	old_margin = margin_pct(old_price, old_landed)

	by_matrix = suggest_markup(new_landed, markup, rounding) if markup else None
	by_hold = suggest_preserve_margin(old_price, old_landed, new_landed, rounding)
	suggested = by_matrix or by_hold

	if cost_went_down:
		proposed = _f(old_price)
		action = "Hold"
	elif suggested:
		proposed = suggested
		action = "Update"
	else:
		proposed = _f(old_price)
		action = "Review"

	# A price at or under landed cost is never a valid suggestion, on any list
	if proposed and _f(new_landed) and proposed <= _f(new_landed):
		action = "Review"

	new_margin = margin_pct(proposed, new_landed)
	exempt = price_list in LOW_MARGIN_PRICE_LISTS
	below_floor = bool(
		min_margin_pct is not None
		and not exempt
		and new_margin is not None
		and new_margin < _f(min_margin_pct)
	)
	if below_floor:
		action = "Review"

	return {
		"price_list": price_list,
		"currency": list_currency,
		"old_price": _f(old_price),
		"old_margin_pct": old_margin,
		"suggested_by_matrix": by_matrix,
		"suggested_by_margin": by_hold,
		"suggested_price": suggested,
		"new_price": proposed,
		"new_margin_pct": new_margin,
		"margin_delta_pct": (
			None if (old_margin is None or new_margin is None) else new_margin - old_margin
		),
		"below_floor": below_floor,
		"low_margin_exempt": exempt,
		"action": action,
	}
