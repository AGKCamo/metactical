# Copyright (c) 2026, Techlift and contributors
# For license information, please see license.txt
"""Prefill a Price Revision from the document that revealed the cost change."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, nowdate

# Lists that exist but are not really retail banners, so they are not offered
# as default scope. They can still be added by hand.
NON_DEFAULT_SCOPE = {"RET - GorillaStoreOnly"}


def default_scope():
	"""Enabled selling lists that actually carry prices."""
	rows = frappe.db.sql(
		"""
		SELECT pl.name
		FROM `tabPrice List` pl
		WHERE pl.selling = 1 AND pl.enabled = 1
		  AND EXISTS (SELECT 1 FROM `tabItem Price` ip WHERE ip.price_list = pl.name)
		ORDER BY pl.name
		""",
		as_dict=True,
	)
	return [r.name for r in rows if r.name not in NON_DEFAULT_SCOPE]


def supplier_price_list(supplier):
	pl = frappe.db.get_value("Supplier", supplier, "default_price_list")
	if pl:
		return pl
	guess = "SUP - {0}".format(supplier)
	return guess if frappe.db.exists("Price List", guess) else None


@frappe.whitelist()
def from_purchase_order(purchase_order, include_unchanged=0):
	"""Build a draft revision from a PO whose rates differ from the cost list.

	The PO rate *is* the new cost: it is what the supplier confirmed and what
	will be invoiced. Anything matching the current cost list is left out, so a
	21-line order usually produces three or four rows rather than 21.
	"""
	po = frappe.get_doc("Purchase Order", purchase_order)
	buying_list = supplier_price_list(po.supplier)
	if not buying_list:
		frappe.throw(
			_("No supplier price list found for {0}. Set Default Price List on the "
			  "supplier first, so the revision knows which cost list to write.")
			.format(po.supplier)
		)

	doc = frappe.new_doc("Price Revision")
	doc.update({
		"supplier": po.supplier,
		"buying_price_list": buying_list,
		"source": "Purchase Order",
		"purchase_order": po.name,
		"company": po.company,
		"supplier_currency": po.currency,
		"conversion_rate": po.conversion_rate or 1.0,
		"rate_source": _("Purchase Order {0}").format(po.name),
		"effective_from": nowdate(),
	})

	for pl in default_scope():
		doc.append("price_lists", {"price_list": pl})

	seen = set()
	for it in po.items:
		if it.item_code in seen:
			continue
		seen.add(it.item_code)

		old_cost = flt(frappe.db.get_value(
			"Item Price",
			{"item_code": it.item_code, "price_list": buying_list},
			"price_list_rate",
		))
		new_cost = flt(it.rate)

		if not int(include_unchanged or 0) and old_cost and abs(new_cost - old_cost) < 0.005:
			continue

		doc.append("items", {
			"item_code": it.item_code,
			"po3_item": it.name,
			"old_cost": old_cost,
			"new_cost": new_cost,
			"duty_pct": flt(frappe.db.get_value("Item", it.item_code, "ifw_duty_rate")),
			"supplier_currency": po.currency,
			"apply": 1,
		})

	if not doc.items:
		frappe.throw(
			_("Every line on {0} already matches {1}. Nothing to revise.")
			.format(po.name, buying_list)
		)

	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def from_confirmation(confirmation):
	"""Build from a V3 Supplier Order Confirmation (deverp only).

	SOC3 is a Custom doctype that lives on the dev instance rather than in this
	app, so the whole path is guarded — on a site without V3 this simply is not
	available instead of raising an import-time error.
	"""
	if not frappe.db.exists("DocType", "Supplier Order Confirmation V3"):
		frappe.throw(_("Supplier Order Confirmation V3 is not installed on this site."))

	soc = frappe.get_doc("Supplier Order Confirmation V3", confirmation)
	po3 = frappe.get_doc("Purchase Order V3", soc.purchase_order_v3)
	native_po = po3.get("erp_purchase_order")

	buying_list = po3.get("buying_price_list") or supplier_price_list(po3.supplier)
	doc = frappe.new_doc("Price Revision")
	doc.update({
		"supplier": po3.supplier,
		"buying_price_list": buying_list,
		"source": "Supplier Confirmation",
		"supplier_order_confirmation": soc.name,
		"purchase_order": native_po,
		"company": po3.company,
		"supplier_currency": po3.currency,
		"conversion_rate": po3.conversion_rate or 1.0,
		"rate_source": _("Purchase Order V3 {0}").format(po3.name),
		"effective_from": nowdate(),
	})
	for pl in default_scope():
		doc.append("price_lists", {"price_list": pl})

	po_rates = {r.name: flt(r.rate) for r in po3.items}
	for line in soc.items:
		confirmed = flt(line.get("confirmed_rate"))
		ordered = po_rates.get(line.get("po3_item"), 0)
		if not confirmed or abs(confirmed - ordered) < 0.005:
			continue
		doc.append("items", {
			"item_code": line.item_code,
			"po3_item": line.get("po3_item"),
			"old_cost": ordered,
			"new_cost": confirmed,
			"duty_pct": flt(frappe.db.get_value("Item", line.item_code, "ifw_duty_rate")),
			"supplier_currency": po3.currency,
			"apply": 1,
		})

	if not doc.items:
		frappe.throw(_("{0} confirmed every line at the ordered cost.").format(soc.name))

	doc.insert(ignore_permissions=True)
	return doc.name
