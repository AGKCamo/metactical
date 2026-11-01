// Copyright (c) 2026, Storebuilder Commerce Inc and contributors
// For license information, please see license.txt

// Migrated from Client Script "Inbound Shipment V3 Form" (Form view).

// Only suppliers flagged is_transporter are carriers, and a carrier's
// services are its own - picking UPS must not offer FedEx Ground.
function ins3_carrier_queries(frm) {
    frm.set_query('carrier', function () {
        return { filters: { is_transporter: 1, disabled: 0 } };
    });
    frm.set_query('carrier', 'boxes', function () {
        return { filters: { is_transporter: 1, disabled: 0 } };
    });
    frm.set_query('carrier_service', function () {
        return { filters: { carrier: frm.doc.carrier || '', disabled: 0 } };
    });
    frm.set_query('carrier_service', 'boxes', function (doc, cdt, cdn) {
        var row = locals[cdt][cdn];
        return { filters: { carrier: row.carrier || frm.doc.carrier || '', disabled: 0 } };
    });
    frm.set_query('carrier', 'tracking', function () {
        return { filters: { is_transporter: 1, disabled: 0 } };
    });
    frm.set_query('carrier_service', 'tracking', function (doc, cdt, cdn) {
        var row = locals[cdt][cdn];
        return { filters: { carrier: row.carrier || frm.doc.carrier || '', disabled: 0 } };
    });
    // Goods land in a stock warehouse; the bin is chosen at receiving. Without
    // this the field offers every one of the thousands of bin records.
    frm.set_query('destination_warehouse', function () {
        return {
            query: 'metactical.procurement_v3.inbound.destination_warehouse_query',
            filters: { company: frm.doc.company || '' }
        };
    });
}

// ---------------------------------------------------------------------------
// Tracking numbers arrive as a blob in an email - one master and a list under
// it. Typing into the box and pressing Enter files each one as its own row,
// and a whole pasted list lands in one go.
// ---------------------------------------------------------------------------
function ins3_add_tracking(frm) {
    var raw = frm.doc.add_tracking;
    if (!raw) return;
    var have = {};
    (frm.doc.tracking || []).forEach(function (r) {
        if (r.tracking_no) have[String(r.tracking_no).trim()] = true;
    });
    var added = 0, dupes = 0;
    String(raw).split(/[\s,;]+/).forEach(function (num) {
        num = num.trim();
        if (!num) return;
        if (have[num]) { dupes++; return; }
        have[num] = true;
        added++;
        var row = frm.add_child('tracking', { tracking_no: num });
        // the first number on the shipment is the one the rest hang off
        if ((frm.doc.tracking || []).length === 1) row.is_master = 1;
    });
    frm.set_value('add_tracking', '');
    frm.refresh_field('tracking');
    if (added) {
        frappe.show_alert({
            message: __('{0} tracking number(s) added.', [added]), indicator: 'green' });
    }
    if (dupes) {
        frappe.show_alert({
            message: __('{0} already on this shipment.', [dupes]), indicator: 'orange' });
    }
}

// The field is a doorway, not a value: Enter files the number rather than
// saving the form, which is what every other tag-style input does.
function ins3_bind_tracking_box(frm) {
    var f = frm.fields_dict.add_tracking;
    if (!f || !f.$input || f._ins3_bound) return;
    f._ins3_bound = true;
    f.$input.on('keydown', function (e) {
        if (e.which !== 13) return;
        e.preventDefault();
        e.stopPropagation();
        frm.doc.add_tracking = f.$input.val();
        ins3_add_tracking(frm);
    });
}

// Spells out how the arrival date was arrived at, so a store asking "why does
// it say the 14th" gets an answer off the document instead of a guess.
function ins3_eta_note(frm) {
    if (frm.is_new() || !frm.doc.expected_arrival) return;
    var d = frappe.datetime.str_to_user;
    var bits = [__('Expected <b>{0}</b>', [d(frm.doc.expected_arrival)])];
    if (frm.doc.eta_source === 'Manual') {
        bits.push(__('set by hand'));
    } else if (frm.doc.eta_source === 'Carrier API') {
        bits.push(__('from the carrier'));
    } else if (frm.doc.ship_date && frm.doc.carrier_eta) {
        bits.push(__('shipped {0}, carrier {1}', [d(frm.doc.ship_date), d(frm.doc.carrier_eta)]));
    }
    if (frm.doc.route && frm.doc.route !== 'Direct' && frm.doc.route_days) {
        bits.push(__('+{0} day(s) {1}', [frm.doc.route_days, frm.doc.route]));
    }
    if (frm.doc.arrived_on) {
        var late = frm.doc.days_late || 0;
        bits.push(late > 0
            ? __('arrived {0}, <b>{1} day(s) late</b>', [d(frm.doc.arrived_on), late])
            : __('arrived {0}, on time', [d(frm.doc.arrived_on)]));
    }
    frm.dashboard.set_headline(bits.join(' &nbsp;·&nbsp; '));
}

function ins3_fetch(frm) {
    if (!frm.doc.purchase_order_v3) return;
    if ((frm.doc.items || []).some(function(d) { return d.item_code; })) return;
    frappe.call({
        method: 'frappe.client.get_list',
        args: { doctype: 'Supplier Order Confirmation V3',
                filters: { purchase_order_v3: frm.doc.purchase_order_v3, docstatus: ['<', 2] },
                fields: ['name'], limit_page_length: 1 },
        callback: function(r) {
            var socName = (r.message || [])[0] ? r.message[0].name : null;
            if (socName && !frm.doc.supplier_order_confirmation_v3) {
                frm.set_value('supplier_order_confirmation_v3', socName);
            }
            var after = function(promised, notComing) {
                frappe.db.get_doc('Purchase Order V3', frm.doc.purchase_order_v3).then(function(po) {
                    frm.clear_table('items');
                    var skipped = 0;
                    (po.items || []).forEach(function(r) {
                        if (['Received','Closed Short','Cancelled','Supplier Stock Out','Discontinued'].indexOf(r.line_status) !== -1) { skipped++; return; }
                        if (notComing[r.name]) { skipped++; return; }
                        var p = promised[r.name];
                        var shipped = flt(r.shipped_qty);
                        var split = (r.line_status === 'Back-ordered') ||
                            (p && ['Back-ordered','Partial - Balance Back-ordered'].indexOf(p.line_status) !== -1);
                        var base;
                        if (p && split) { base = (shipped < flt(p.confirmed_qty)) ? flt(p.confirmed_qty) : flt(r.qty); }
                        else if (p) { base = flt(p.confirmed_qty); }
                        else { base = flt(r.qty); }
                        var out = base - flt(r.shipped_qty);
                        if (out <= 0) { skipped++; return; }
                        var d = frm.add_child('items');
                        d.po3_item = r.name;
                        d.item_code = r.item_code;
                        d.qty = out;
                    });
                    frm.refresh_field('items');
                    var n = (frm.doc.items || []).length;
                    frappe.show_alert({
                        message: n ? __('Pulled {0} expected line(s){1}', [n, socName ? __(' from confirmation {0}', [socName]) : ''])
                                   : __('Nothing outstanding to ship'),
                        indicator: n ? 'green' : 'orange' }, 7);
                });
            };
            if (!socName) { after({}, {}); return; }
            frappe.call({
                method: 'frappe.client.get_list',
                args: { doctype: 'Supplier Order Confirmation V3 Item', parent: 'Supplier Order Confirmation V3',
                        filters: { parent: socName }, fields: ['po3_item','confirmed_qty','line_status'],
                        limit_page_length: 0 },
                callback: function(r2) {
                    var promised = {}, notComing = {};
                    (r2.message || []).forEach(function(l) {
                        promised[l.po3_item] = l;
                        if (['Supplier Stock Out','Discontinued','Cancelled by Supplier'].indexOf(l.line_status) !== -1)
                            notComing[l.po3_item] = 1;
                    });
                    after(promised, notComing);
                }
            });
        }
    });
}

frappe.ui.form.on('Inbound Shipment V3', {
    onload: function (frm) { ins3_carrier_queries(frm); },
    carrier: function (frm) {
        // changing the carrier invalidates a service belonging to the old one
        if (frm.doc.carrier_service) frm.set_value('carrier_service', null);
    },

    add_tracking: function (frm) { ins3_add_tracking(frm); },

    route: function (frm) {
        // cross-docked freight is re-shipped from the crossing, which is a day
        // or two on top of the carrier's own transit time
        if (frm.doc.route && frm.doc.route !== 'Direct') {
            if (!frm.doc.route_days) frm.set_value('route_days', 2);
        } else {
            frm.set_value('route_days', 0);
        }
    },

    purchase_order_v3: ins3_fetch,
    onload_post_render: function(frm) {
        if (frm.is_new()) ins3_fetch(frm);
        ins3_bind_tracking_box(frm);
    },
    refresh: function(frm) {
        ins3_bind_tracking_box(frm);
        ins3_eta_note(frm);
        if (frm.doc.docstatus === 0 && frm.doc.purchase_order_v3) {
            frm.add_custom_button(__('Fetch Expected Lines'), function() {
                frm.clear_table('items'); frm.refresh_field('items'); ins3_fetch(frm);
            });
        }
        if (!frm.is_new() && (frm.doc.tracking || []).length) {
            frm.add_custom_button(__('Refresh From Carrier'), function() {
                frappe.call({
                    method: 'metactical.procurement_v3.carriers.refresh',
                    args: { shipment: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Asking the carrier…'),
                    callback: function(res) {
                        var m = res.message || {};
                        if ((m.problems || []).length) {
                            frappe.msgprint({
                                title: __('Nothing came back'),
                                message: (m.problems || []).join('<br>'),
                                indicator: 'orange'
                            });
                        }
                        if (m.checked) frm.reload_doc();
                    }
                });
            });
        }
        if (frm.doc.supplier_order_confirmation_v3) {
            frm.add_custom_button(__('Confirmation'), function() {
                frappe.set_route('Form','Supplier Order Confirmation V3', frm.doc.supplier_order_confirmation_v3);
            }, __('View'));
        }
        if (frm.doc.purchase_order_v3) {
            frm.add_custom_button(__('Purchase Order V3'), function() {
                frappe.set_route('Form','Purchase Order V3', frm.doc.purchase_order_v3);
            }, __('View'));
        }
    }
});

frappe.ui.form.on('Inbound Shipment V3 Box', {
    carrier: function (frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, 'carrier_service', null);
    }
});

frappe.ui.form.on('Inbound Shipment V3 Tracking', {
    carrier: function (frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, 'carrier_service', null);
    },
    // only one number can be the master the others hang off
    is_master: function (frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (!row.is_master) return;
        (frm.doc.tracking || []).forEach(function (r) {
            if (r.name !== row.name && r.is_master) {
                frappe.model.set_value(r.doctype, r.name, 'is_master', 0);
            }
        });
    },
    tracking_remove: function (frm) {
        var rows = frm.doc.tracking || [];
        if (rows.length && !rows.some(function (r) { return r.is_master; })) {
            frappe.model.set_value(rows[0].doctype, rows[0].name, 'is_master', 1);
        }
    }
});

// ---------------------------------------------------------------------------
// Migrated from Client Script "Inbound Shipment V3 Stale State Guard" (Form view).
// Refuses a workflow action when the document has already moved on in the DB.
// Especially relevant here: posting a Goods Receipt V3 can flip this shipment to
// Received server-side, so an open form can easily be looking at a stale state.
// ---------------------------------------------------------------------------

frappe.ui.form.on('Inbound Shipment V3', {
    before_workflow_action: function(frm) {
        return new Promise(function(resolve, reject) {
            if (frm.is_new() || !frm.doc.name) { resolve(); return; }
            frappe.db.get_value(frm.doctype, frm.doc.name, 'workflow_state')
                .then(function(r) {
                    var server = (r && r.message) ? r.message.workflow_state : null;
                    if (!server || server === frm.doc.workflow_state) { resolve(); return; }
                    frm.reload_doc().then(function() {
                        frappe.show_alert({
                            message: __('This document had already moved to <b>{0}</b>. Refreshed — pick the action you want from the updated list.', [server]),
                            indicator: 'orange'
                        }, 10);
                    });
                    reject();
                })
                .catch(function() { resolve(); });   // never block on a lookup failure
        });
    }
});