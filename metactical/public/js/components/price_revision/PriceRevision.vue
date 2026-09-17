<template>
	<div class="pr-wrap">
		<!-- pick a revision to work on -->
		<div class="pr-bar">
			<div class="pr-field">
				<label>{{ __("Revision") }}</label>
				<select v-model="selected" @change="load" class="form-control input-sm">
					<option value="">{{ __("Select a revision…") }}</option>
					<option v-for="r in revisions" :key="r.name" :value="r.name">
						{{ r.name }} — {{ r.supplier }} ({{ r.status }})
					</option>
				</select>
			</div>
			<div class="pr-field">
				<label>{{ __("Or build one from a Purchase Order") }}</label>
				<div class="pr-inline">
					<input v-model="poName" class="form-control input-sm"
					       :placeholder="__('Purchase Order')" @keyup.enter="buildFromPO">
					<button class="btn btn-sm btn-default" :disabled="busy || !poName"
					        @click="buildFromPO">{{ __("Build") }}</button>
				</div>
			</div>
			<div class="pr-spacer"></div>
			<button class="btn btn-sm btn-default" :disabled="busy" @click="rebuildMatrix">
				{{ __("Rebuild Markup Matrix") }}
			</button>
		</div>

		<div v-if="error" class="pr-alert pr-alert-danger">{{ error }}</div>
		<div v-if="busy" class="pr-muted">{{ __("Working…") }}</div>

		<div v-if="doc" class="pr-doc">
			<!-- header facts -->
			<div class="pr-cards">
				<div class="pr-card">
					<span class="pr-k">{{ __("Supplier") }}</span>
					<span class="pr-v">{{ doc.supplier_name || doc.supplier }}</span>
					<span class="pr-sub">{{ doc.buying_price_list }}</span>
				</div>
				<div class="pr-card">
					<span class="pr-k">{{ __("Rate used") }}</span>
					<span class="pr-v">{{ fmtRate(doc.conversion_rate) }}</span>
					<span class="pr-sub">{{ doc.rate_source || __("manual") }}</span>
				</div>
				<div class="pr-card">
					<span class="pr-k">{{ __("Average cost change") }}</span>
					<span class="pr-v" :class="pctClass(doc.avg_cost_change_pct)">
						{{ fmtPct(doc.avg_cost_change_pct) }}
					</span>
					<span class="pr-sub">{{ doc.total_items }} {{ __("items") }}</span>
				</div>
				<div class="pr-card">
					<span class="pr-k">{{ __("Margin before → after") }}</span>
					<span class="pr-v">
						{{ fmtPct(doc.avg_margin_before) }} → {{ fmtPct(doc.avg_margin_after) }}
					</span>
					<span class="pr-sub">{{ doc.total_price_changes }} {{ __("price changes") }}</span>
				</div>
				<div class="pr-card" :class="{ 'pr-card-warn': doc.lines_needing_review > 0 }">
					<span class="pr-k">{{ __("Needs review") }}</span>
					<span class="pr-v">{{ doc.lines_needing_review }}</span>
					<span class="pr-sub">{{ __("lines") }}</span>
				</div>
			</div>

			<!-- cost changes -->
			<h5 class="pr-h">{{ __("Cost changes") }}</h5>
			<div class="pr-scroll">
				<table class="table table-bordered pr-table">
					<thead>
						<tr>
							<th>{{ __("Item") }}</th>
							<th class="num">{{ __("Old cost") }}</th>
							<th class="num">{{ __("New cost") }}</th>
							<th class="num">{{ __("Change") }}</th>
							<th class="num">{{ __("Duty") }}</th>
							<th class="num">{{ __("Landed (CAD)") }}</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="it in doc.items" :key="it.name">
							<td>
								<div class="pr-item">{{ it.item_code }}</div>
								<div class="pr-sub">{{ it.item_name }}</div>
							</td>
							<td class="num">{{ money(it.old_cost, doc.supplier_currency) }}</td>
							<td class="num strong">{{ money(it.new_cost, doc.supplier_currency) }}</td>
							<td class="num" :class="pctClass(it.cost_change_pct)">
								{{ fmtPct(it.cost_change_pct) }}
							</td>
							<td class="num">{{ fmtPct(it.duty_pct) }}</td>
							<td class="num">
								{{ money(it.landed_old_cad, doc.company_currency) }}
								→
								<b>{{ money(it.landed_new_cad, doc.company_currency) }}</b>
							</td>
						</tr>
					</tbody>
				</table>
			</div>

			<!-- price changes -->
			<h5 class="pr-h">
				{{ __("Price changes") }}
				<small class="pr-muted">{{ __("Edit a new price to override the suggestion.") }}</small>
			</h5>
			<div class="pr-scroll">
				<table class="table table-bordered pr-table">
					<thead>
						<tr>
							<th>{{ __("Item") }}</th>
							<th>{{ __("Price list") }}</th>
							<th class="num">{{ __("Landed") }}</th>
							<th class="num">{{ __("Old price") }}</th>
							<th class="num">{{ __("Old margin") }}</th>
							<th class="num">{{ __("Suggested") }}</th>
							<th class="num">{{ __("New price") }}</th>
							<th class="num">{{ __("New margin") }}</th>
							<th class="num">{{ __("Δ") }}</th>
							<th>{{ __("Action") }}</th>
						</tr>
					</thead>
					<tbody>
						<tr v-for="p in doc.prices" :key="p.name"
						    :class="{ 'pr-row-review': p.action === 'Review' }">
							<td>{{ p.item_code }}</td>
							<td>
								{{ p.price_list }}
								<span v-if="p.low_margin_exempt" class="pr-chip">{{ __("thin by design") }}</span>
							</td>
							<td class="num">{{ money(p.landed_in_list_ccy, p.currency) }}</td>
							<td class="num">{{ money(p.old_price, p.currency) }}</td>
							<td class="num">{{ fmtPct(p.old_margin_pct) }}</td>
							<td class="num">
								{{ money(p.suggested_price, p.currency) }}
								<div v-if="p.markup_source" class="pr-sub">
									{{ p.markup_used }}× · {{ p.markup_source }}
								</div>
							</td>
							<td class="num">
								<input type="number" step="0.01" class="form-control input-sm pr-price"
								       v-model.number="p.new_price" @change="dirty = true">
							</td>
							<td class="num" :class="{ 'pr-bad': p.below_floor }">
								{{ fmtPct(p.new_margin_pct) }}
							</td>
							<td class="num" :class="pctClass(p.margin_delta_pct)">
								{{ fmtPct(p.margin_delta_pct) }}
							</td>
							<td>
								<select v-model="p.action" class="form-control input-sm" @change="dirty = true">
									<option>Update</option>
									<option>Hold</option>
									<option>Skip</option>
									<option>Review</option>
								</select>
							</td>
						</tr>
					</tbody>
				</table>
			</div>

			<!-- actions -->
			<div class="pr-actions">
				<button class="btn btn-sm btn-default" :disabled="busy || !dirty" @click="save">
					{{ __("Save") }}
				</button>
				<button class="btn btn-sm btn-primary" :disabled="busy || doc.docstatus !== 0"
				        @click="submitDoc">
					{{ __("Submit for Review") }}
				</button>
				<button class="btn btn-sm btn-primary" :disabled="busy || doc.status !== 'Pending Review'"
				        @click="applyDoc">
					{{ __("Apply Prices") }}
				</button>
				<button class="btn btn-sm btn-danger" :disabled="busy || doc.status !== 'Applied'"
				        @click="revertDoc">
					{{ __("Revert") }}
				</button>
				<span class="pr-spacer"></span>
				<a :href="'/app/price-revision/' + doc.name" class="pr-link">{{ __("Open the document") }}</a>
			</div>
		</div>

		<div v-else-if="!busy" class="pr-empty">
			{{ __("Pick a revision, or build one from a Purchase Order whose costs have moved.") }}
		</div>
	</div>
</template>

<script>
export default {
	name: "PriceRevision",
	data() {
		return {
			revisions: [],
			selected: "",
			doc: null,
			poName: "",
			busy: false,
			dirty: false,
			error: "",
		};
	},
	mounted() {
		this.refresh();
	},
	methods: {
		__(s) {
			return window.__ ? window.__(s) : s;
		},
		async refresh() {
			this.error = "";
			this.busy = true;
			try {
				const r = await frappe.call({
					method: "frappe.client.get_list",
					args: {
						doctype: "Price Revision",
						fields: ["name", "supplier", "status"],
						filters: { docstatus: ["<", 2] },
						order_by: "modified desc",
						limit_page_length: 50,
					},
				});
				this.revisions = r.message || [];
				if (this.selected) await this.load();
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		async load() {
			if (!this.selected) {
				this.doc = null;
				return;
			}
			this.busy = true;
			this.error = "";
			try {
				const r = await frappe.call({
					method: "frappe.client.get",
					args: { doctype: "Price Revision", name: this.selected },
				});
				this.doc = r.message;
				this.dirty = false;
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		async buildFromPO() {
			this.busy = true;
			this.error = "";
			try {
				const r = await frappe.call({
					method: "metactical.price_revision.build.from_purchase_order",
					args: { purchase_order: this.poName },
				});
				await this.refresh();
				this.selected = r.message;
				await this.load();
				frappe.show_alert({ message: __("Built {0}", [r.message]), indicator: "green" });
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		async save() {
			this.busy = true;
			this.error = "";
			try {
				await frappe.call({
					method: "frappe.client.save",
					args: { doc: this.doc },
				});
				await this.load();
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		async submitDoc() {
			await this.run("frappe.client.submit", { doc: this.doc });
		},
		async applyDoc() {
			if (!(await this.confirm(__("Write these prices to Item Price?")))) return;
			await this.run(
				"metactical.metactical.doctype.price_revision.price_revision.apply_revision",
				{ name: this.doc.name }
			);
		},
		async revertDoc() {
			if (!(await this.confirm(__("Put every price this revision changed back?")))) return;
			await this.run(
				"metactical.metactical.doctype.price_revision.price_revision.revert_revision",
				{ name: this.doc.name }
			);
		},
		async run(method, args) {
			this.busy = true;
			this.error = "";
			try {
				await frappe.call({ method, args });
				await this.load();
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		async rebuildMatrix() {
			if (!(await this.confirm(__("Recompute every markup from live price history?")))) return;
			this.busy = true;
			try {
				const r = await frappe.call({
					method: "metactical.price_revision.matrix.rebuild_from_history",
				});
				frappe.show_alert({
					message: __("{0} markups rebuilt", [r.message.pairs_written]),
					indicator: "green",
				});
			} catch (e) {
				this.error = this.msg(e);
			} finally {
				this.busy = false;
			}
		},
		confirm(text) {
			return new Promise((res) => frappe.confirm(text, () => res(true), () => res(false)));
		},
		msg(e) {
			return (e && (e.message || e._server_messages)) || __("Something went wrong.");
		},
		money(v, ccy) {
			if (v === null || v === undefined || v === "") return "—";
			return format_currency(v, ccy);
		},
		fmtPct(v) {
			if (v === null || v === undefined || v === "") return "—";
			return `${Number(v).toFixed(1)}%`;
		},
		fmtRate(v) {
			return v ? Number(v).toFixed(4) : "—";
		},
		pctClass(v) {
			if (v === null || v === undefined) return "";
			return Number(v) > 0 ? "pr-up" : Number(v) < 0 ? "pr-down" : "";
		},
	},
};
</script>

<style scoped>
.pr-wrap { padding: 4px 0 40px; }
.pr-bar { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; margin-bottom: 18px; }
.pr-field { display: flex; flex-direction: column; gap: 4px; min-width: 240px; }
.pr-field label { font-size: 11px; color: var(--text-muted); margin: 0; }
.pr-inline { display: flex; gap: 6px; }
.pr-spacer { flex: 1 1 auto; }

.pr-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-bottom: 22px; }
.pr-card { border: 1px solid var(--border-color); border-radius: var(--border-radius-md, 6px);
	padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; background: var(--card-bg, #fff); }
.pr-card-warn { border-color: var(--orange-300, #e8b339); }
.pr-k { font-size: 11px; color: var(--text-muted); }
.pr-v { font-size: 17px; font-weight: 600; }
.pr-sub { font-size: 11px; color: var(--text-muted); }

.pr-h { margin: 22px 0 8px; font-size: 13px; font-weight: 600; display: flex; gap: 10px; align-items: baseline; }
.pr-scroll { overflow-x: auto; }
.pr-table { font-size: 12.5px; margin-bottom: 0; }
.pr-table th { font-weight: 500; color: var(--text-muted); font-size: 11px; white-space: nowrap; }
.pr-table td { vertical-align: middle; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.strong { font-weight: 600; }
.pr-item { font-weight: 500; }
.pr-price { width: 92px; text-align: right; display: inline-block; }
.pr-row-review { background: var(--yellow-50, #fdf5e0); }
.pr-up { color: var(--red-600, #c0392b); }
.pr-down { color: var(--green-600, #2e7d4f); }
.pr-bad { color: var(--red-600, #c0392b); font-weight: 600; }
.pr-chip { font-size: 10px; border: 1px solid var(--border-color); border-radius: 10px;
	padding: 0 6px; color: var(--text-muted); margin-left: 4px; }

.pr-actions { display: flex; gap: 8px; align-items: center; margin-top: 18px; flex-wrap: wrap; }
.pr-link { font-size: 12px; }
.pr-alert { padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; font-size: 12.5px; }
.pr-alert-danger { background: var(--red-50, #fdeaea); color: var(--red-600, #c0392b); }
.pr-muted { color: var(--text-muted); font-size: 12px; font-weight: 400; }
.pr-empty { color: var(--text-muted); padding: 40px 0; text-align: center; }
</style>
