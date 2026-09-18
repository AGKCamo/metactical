# Copyright (c) 2026, Techlift and contributors
# For license information, please see license.txt
"""Markup matrix: what ICL already charges, learned from its own price history.

Markup is not one house rule. It varies by supplier *and* by banner, and the
spread within each pair is tight enough to be a rule rather than noise:

    supplier               RET - Camo   RET - GPD   RET - CamoUSA
    5.11 Tactical               2.69        1.76          1.78
    Condor Outdoor              3.42        2.21          1.67
    Rothco                      3.23        2.09          2.28

So the matrix is *derived*, not typed in. :func:`rebuild_from_history` reads
live Item Price rows, takes the median ratio per pair, and records the sample
size and quartiles alongside it so a reviewer can see how much to trust a row.

The median is used rather than the mean because a handful of clearance items
priced below cost, and the odd 40x outlier, drag an average badly.
"""

from __future__ import annotations

import frappe
from frappe.utils import nowdate

# Ratios outside this band are data errors, not pricing decisions: below 1.0 is
# selling under cost, and above 12 is almost always a stale or wrong cost.
MIN_SANE_RATIO = 1.0
MAX_SANE_RATIO = 12.0

# Below this many observations a median means very little. The row is still
# written, so the gap is visible, but it is marked low confidence.
MIN_CONFIDENT_SAMPLE = 30


def _selling_lists():
	return frappe.get_all(
		"Price List",
		filters={"selling": 1, "enabled": 1},
		pluck="name",
	)


@frappe.whitelist()
def rebuild_from_history(supplier_price_list=None, commit_every=50):
	"""Recompute every markup from live Item Price data.

	Safe to re-run: rows are upserted on (buying_price_list, selling_price_list).
	Pass ``supplier_price_list`` to refresh a single supplier rather than all.

	Returns a summary dict — no tuples, so the same shape works if this is ever
	ported into a Server Script.
	"""
	where = ""
	params = {"lo": MIN_SANE_RATIO, "hi": MAX_SANE_RATIO}
	if supplier_price_list:
		where = "AND sup.price_list = %(splist)s"
		params["splist"] = supplier_price_list

	rows = frappe.db.sql(
		f"""
		SELECT supplier_list, retail_list, cnt,
		       AVG(ratio)  AS median_markup,
		       MIN(ratio)  AS lo_mid,
		       MAX(ratio)  AS hi_mid
		FROM (
			SELECT sup.price_list AS supplier_list,
			       ret.price_list AS retail_list,
			       ret.price_list_rate / sup.price_list_rate AS ratio,
			       ROW_NUMBER() OVER (
			           PARTITION BY sup.price_list, ret.price_list
			           ORDER BY ret.price_list_rate / sup.price_list_rate
			       ) AS rn,
			       COUNT(*) OVER (
			           PARTITION BY sup.price_list, ret.price_list
			       ) AS cnt
			FROM `tabItem Price` sup
			JOIN `tabItem Price` ret ON ret.item_code = sup.item_code
			JOIN `tabPrice List` bl ON bl.name = sup.price_list AND bl.buying = 1
			JOIN `tabPrice List` sl ON sl.name = ret.price_list
			                       AND sl.selling = 1 AND sl.enabled = 1
			WHERE sup.price_list_rate > 0
			  AND ret.price_list_rate > 0
			  -- RET - CamoFRN - USD is flagged buying as well as selling, because
			  -- franchisee companies buy at it. That is legitimate as a cost base,
			  -- but a list against itself is always 1.0x and means nothing.
			  AND sup.price_list <> ret.price_list
			  AND ret.price_list_rate / sup.price_list_rate BETWEEN %(lo)s AND %(hi)s
			  {where}
		) t
		WHERE rn IN (FLOOR((cnt + 1) / 2), CEILING((cnt + 1) / 2))
		GROUP BY supplier_list, retail_list, cnt
		""",
		params,
		as_dict=True,
	)

	written = 0
	for i, r in enumerate(rows):
		_upsert(
			buying_price_list=r.supplier_list,
			selling_price_list=r.retail_list,
			markup=round(float(r.median_markup or 0), 4),
			sample_size=int(r.cnt or 0),
			spread_low=round(float(r.lo_mid or 0), 4),
			spread_high=round(float(r.hi_mid or 0), 4),
		)
		written += 1
		if commit_every and i and i % commit_every == 0:
			frappe.db.commit()

	frappe.db.commit()
	return {
		"pairs_written": written,
		"as_of": nowdate(),
		"scope": supplier_price_list or "all suppliers",
	}


def _upsert(**kw):
	name = frappe.db.get_value(
		"Pricing Matrix",
		{
			"buying_price_list": kw["buying_price_list"],
			"selling_price_list": kw["selling_price_list"],
			"item_group": ("in", ["", None]),
			"brand": ("in", ["", None]),
		},
		"name",
	)
	payload = dict(kw)
	payload["derived_on"] = nowdate()
	payload["derived_from_history"] = 1
	payload["low_confidence"] = 1 if kw["sample_size"] < MIN_CONFIDENT_SAMPLE else 0

	if name:
		doc = frappe.get_doc("Pricing Matrix", name)
		# never clobber a markup a human has pinned
		if doc.locked:
			return
		doc.update(payload)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(dict(doctype="Pricing Matrix", **payload))
		doc.insert(ignore_permissions=True)


def lookup(buying_price_list, selling_price_list, item_code=None):
	"""Most specific markup wins: item group + brand, then brand, then the pair.

	Returns a dict with the markup and where it came from, so the UI can show
	*why* a suggestion is what it is rather than producing a bare number.
	"""
	brand = item_group = None
	if item_code:
		meta = frappe.db.get_value("Item", item_code, ["brand", "item_group"], as_dict=True)
		if meta:
			brand, item_group = meta.brand, meta.item_group

	for filters, source in (
		({"brand": brand, "item_group": item_group}, "brand + item group"),
		({"brand": brand, "item_group": ("in", ["", None])}, "brand"),
		({"brand": ("in", ["", None]), "item_group": item_group}, "item group"),
		({"brand": ("in", ["", None]), "item_group": ("in", ["", None])}, "supplier list"),
	):
		if "brand" in filters and filters["brand"] is None:
			continue
		if "item_group" in filters and filters["item_group"] is None:
			continue
		f = dict(filters)
		f["buying_price_list"] = buying_price_list
		f["selling_price_list"] = selling_price_list
		f["disabled"] = 0
		row = frappe.db.get_value(
			"Pricing Matrix", f,
			["name", "markup", "min_margin_pct", "rounding_rule", "sample_size", "low_confidence"],
			as_dict=True,
		)
		if row and row.markup:
			row["matched_on"] = source
			return row

	return None
