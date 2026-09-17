# Copyright (c) 2026, Techlift and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, nowdate

from metactical.price_revision.costing import build_price_line, landed_cost, landed_in_currency
from metactical.price_revision.matrix import lookup as matrix_lookup


class PriceRevision(Document):
	def validate(self):
		self.set_currencies()
		self.recalculate()
		self.roll_up_summary()

	def on_submit(self):
		self.status = "Pending Review"

	def on_cancel(self):
		if self.status == "Applied":
			frappe.throw(
				_("{0} has already been applied. Revert it before cancelling, "
				  "so the prices it changed go back first.").format(self.name)
			)
		self.status = "Cancelled"

	# ------------------------------------------------------------------ setup

	def set_currencies(self):
		if self.buying_price_list and not self.supplier_currency:
			self.supplier_currency = frappe.db.get_value(
				"Price List", self.buying_price_list, "currency"
			)
		if self.company and not self.company_currency:
			self.company_currency = frappe.db.get_value(
				"Company", self.company, "default_currency"
			)
		if not self.conversion_rate:
			self.conversion_rate = 1.0
		if not self.effective_from:
			self.effective_from = nowdate()

	# ------------------------------------------------------- the actual maths

	def recalculate(self):
		"""Rebuild every price line from the item costs currently on the doc.

		Runs on every save so the grid can never drift from the costs above it.
		A ``new_price`` a person has typed is preserved; everything derived
		around it is recomputed.
		"""
		scoped = [d.price_list for d in (self.price_lists or [])]
		if not scoped or not self.items:
			return

		list_meta = {
			pl.name: pl
			for pl in frappe.get_all(
				"Price List",
				filters={"name": ("in", scoped)},
				fields=["name", "currency"],
			)
		}

		# keep anything a human typed, keyed on item + list
		typed = {
			(p.item_code, p.price_list): p
			for p in (self.prices or [])
			if p.get("__islocal") is None and flt(p.new_price)
		}

		rebuilt = []
		for item in self.items:
			landed_old = landed_cost(item.old_cost, item.duty_pct, self.conversion_rate)
			landed_new = landed_cost(item.new_cost, item.duty_pct, self.conversion_rate)
			item.landed_old_cad = landed_old["company_ccy"]
			item.landed_new_cad = landed_new["company_ccy"]
			item.supplier_currency = self.supplier_currency
			item.cost_change_pct = (
				(flt(item.new_cost) - flt(item.old_cost)) / flt(item.old_cost) * 100.0
				if flt(item.old_cost) else 0.0
			)
			cost_down = flt(item.new_cost) < flt(item.old_cost)

			for pl_name in scoped:
				meta = list_meta.get(pl_name)
				if not meta:
					continue

				old_in_ccy = landed_in_currency(
					landed_old, meta.currency, self.supplier_currency, self.company_currency
				)
				new_in_ccy = landed_in_currency(
					landed_new, meta.currency, self.supplier_currency, self.company_currency
				)

				old_price = frappe.db.get_value(
					"Item Price",
					{"item_code": item.item_code, "price_list": pl_name},
					"price_list_rate",
				) or 0

				if new_in_ccy is None:
					# a third currency — say so rather than invent a rate
					rebuilt.append(self._row(item, pl_name, meta.currency, {
						"old_price": old_price, "new_price": old_price,
						"action": "Review",
					}, note=_("{0} is in {1}; no rate from {2} on this revision.").format(
						pl_name, meta.currency, self.supplier_currency)))
					continue

				mx = matrix_lookup(self.buying_price_list, pl_name, item.item_code) or {}
				line = build_price_line(
					price_list=pl_name,
					list_currency=meta.currency,
					old_price=old_price,
					old_landed=old_in_ccy,
					new_landed=new_in_ccy,
					markup=mx.get("markup"),
					rounding=mx.get("rounding_rule") or self.rounding_rule or "0.99",
					min_margin_pct=mx.get("min_margin_pct"),
					cost_went_down=cost_down,
				)
				line["landed_in_list_ccy"] = new_in_ccy
				line["markup_used"] = mx.get("markup")
				line["markup_source"] = mx.get("matched_on")

				keep = typed.get((item.item_code, pl_name))
				if keep and flt(keep.new_price) != flt(line["new_price"]):
					line["new_price"] = flt(keep.new_price)
					line["action"] = keep.action or line["action"]
					line["new_margin_pct"] = (
						(flt(keep.new_price) - new_in_ccy) / flt(keep.new_price) * 100.0
						if flt(keep.new_price) else None
					)

				rebuilt.append(self._row(item, pl_name, meta.currency, line))

		self.set("prices", [])
		for r in rebuilt:
			self.append("prices", r)

	def _row(self, item, price_list, currency, line, note=None):
		row = {
			"item_code": item.item_code,
			"price_list": price_list,
			"currency": currency,
			"old_price": line.get("old_price"),
			"old_margin_pct": line.get("old_margin_pct"),
			"suggested_price": line.get("suggested_price"),
			"suggested_by_matrix": line.get("suggested_by_matrix"),
			"suggested_by_margin": line.get("suggested_by_margin"),
			"markup_used": line.get("markup_used"),
			"markup_source": line.get("markup_source"),
			"new_price": line.get("new_price"),
			"new_margin_pct": line.get("new_margin_pct"),
			"margin_delta_pct": line.get("margin_delta_pct"),
			"landed_in_list_ccy": line.get("landed_in_list_ccy"),
			"action": line.get("action"),
			"below_floor": 1 if line.get("below_floor") else 0,
			"low_margin_exempt": 1 if line.get("low_margin_exempt") else 0,
		}
		if note:
			row["note"] = note
		return row

	def roll_up_summary(self):
		self.total_items = len(self.items or [])
		changes = [p for p in (self.prices or []) if p.action == "Update"]
		self.total_price_changes = len(changes)
		self.lines_needing_review = len(
			[p for p in (self.prices or []) if p.action == "Review"]
		)

		costs = [flt(i.cost_change_pct) for i in (self.items or []) if flt(i.old_cost)]
		self.avg_cost_change_pct = sum(costs) / len(costs) if costs else 0

		before = [flt(p.old_margin_pct) for p in (self.prices or []) if p.old_margin_pct is not None]
		after = [flt(p.new_margin_pct) for p in (self.prices or []) if p.new_margin_pct is not None]
		self.avg_margin_before = sum(before) / len(before) if before else 0
		self.avg_margin_after = sum(after) / len(after) if after else 0


# --------------------------------------------------------------------- apply


@frappe.whitelist()
def apply_revision(name):
	"""Write the supplier cost and every accepted selling price to Item Price.

	Every write is logged with its previous value, which is what makes
	:func:`revert_revision` possible — ICL keeps one Item Price per item per
	list and has no price history otherwise, so the log *is* the history.
	"""
	doc = frappe.get_doc("Price Revision", name)
	if doc.docstatus != 1:
		frappe.throw(_("Submit the revision before applying it."))
	if doc.status == "Applied":
		frappe.throw(_("{0} has already been applied.").format(name))

	written = 0

	for item in doc.items:
		if not item.apply:
			continue
		written += _write_price(
			doc, item.item_code, doc.buying_price_list,
			doc.supplier_currency, flt(item.new_cost), buying=True,
		)

	for p in doc.prices:
		if p.action != "Update":
			continue
		written += _write_price(
			doc, p.item_code, p.price_list, p.currency, flt(p.new_price), buying=False,
		)

	doc.db_set("status", "Applied")
	doc.db_set("applied_on", now_datetime())
	doc.db_set("applied_by", frappe.session.user)
	frappe.db.commit()
	return {"written": written, "status": "Applied"}


def _write_price(doc, item_code, price_list, currency, rate, buying=False):
	if not rate:
		return 0

	existing = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": price_list},
		["name", "price_list_rate"],
		as_dict=True,
	)

	if existing:
		if flt(existing.price_list_rate) == flt(rate):
			return 0
		frappe.db.set_value("Item Price", existing.name, "price_list_rate", rate)
		old_rate, ip_name, action = flt(existing.price_list_rate), existing.name, "Applied"
	else:
		ip = frappe.get_doc({
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": price_list,
			"price_list_rate": rate,
			"currency": currency,
			"buying": 1 if buying else 0,
			"selling": 0 if buying else 1,
		})
		ip.insert(ignore_permissions=True)
		old_rate, ip_name, action = 0, ip.name, "Created"

	frappe.get_doc({
		"doctype": "Price Revision Log",
		"price_revision": doc.name,
		"item_code": item_code,
		"price_list": price_list,
		"currency": currency,
		"old_rate": old_rate,
		"new_rate": rate,
		"item_price": ip_name,
		"action": action,
		"changed_on": now_datetime(),
		"changed_by": frappe.session.user,
	}).insert(ignore_permissions=True)
	return 1


@frappe.whitelist()
def revert_revision(name):
	"""Put every price this revision changed back to what it was.

	Rows the revision created (no previous price existed) are left in place
	rather than deleted — something else may since have come to depend on
	them — but they are logged as reverted so the trail stays honest.
	"""
	doc = frappe.get_doc("Price Revision", name)
	if doc.status != "Applied":
		frappe.throw(_("Only an applied revision can be reverted."))

	logs = frappe.get_all(
		"Price Revision Log",
		filters={"price_revision": name, "reverted": 0},
		fields=["name", "item_price", "old_rate", "action"],
	)

	reverted = 0
	for log in logs:
		if log.action == "Applied" and log.item_price:
			frappe.db.set_value("Item Price", log.item_price, "price_list_rate", log.old_rate)
			reverted += 1
		frappe.db.set_value("Price Revision Log", log.name, "reverted", 1)

	doc.db_set("status", "Reverted")
	doc.db_set("reverted_on", now_datetime())
	doc.db_set("reverted_by", frappe.session.user)
	frappe.db.commit()
	return {"reverted": reverted, "left_in_place": len(logs) - reverted, "status": "Reverted"}


@frappe.whitelist()
def outstanding_for_purchase_order(purchase_order):
	"""Revisions still unapplied against a PO — the receiving gate reads this."""
	return frappe.get_all(
		"Price Revision",
		filters={
			"purchase_order": purchase_order,
			"status": ("in", ["Draft", "Pending Review"]),
			"docstatus": ("<", 2),
		},
		fields=["name", "status", "total_price_changes", "avg_cost_change_pct"],
	)
