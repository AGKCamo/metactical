// Copyright (c) 2026, Storebuilder Commerce Inc and contributors
// For license information, please see license.txt

// Migrated from Client Script "Inbound Shipment V3 List Bulk Delete" (List view).
// Adds a "Delete Drafts" bulk action that skips submitted rows.

frappe.listview_settings['Inbound Shipment V3'] = frappe.listview_settings['Inbound Shipment V3'] || {};

// A shipment still in transit past its ETA is the one worth chasing. The
// status pill belongs to the workflow, so the arrival date carries this
// instead: overdue in red, and how late it actually turned up once it lands.
frappe.listview_settings['Inbound Shipment V3'].add_fields =
    (frappe.listview_settings['Inbound Shipment V3'].add_fields || [])
        .concat(['expected_arrival', 'arrived_on', 'days_late', 'workflow_state']);

frappe.listview_settings['Inbound Shipment V3'].formatters = Object.assign(
    {}, frappe.listview_settings['Inbound Shipment V3'].formatters, {
        expected_arrival: function(value, df, doc) {
            if (!value) {
                return doc.workflow_state === 'In Transit'
                    ? '<span style="color:var(--orange-600)">' + __('no ETA') + '</span>'
                    : '';
            }
            var shown = frappe.datetime.str_to_user(value);
            var late = cint(doc.days_late);
            if (doc.arrived_on) {
                return late > 0
                    ? shown + ' <span style="color:var(--orange-600)">'
                        + __('({0}d late)', [late]) + '</span>'
                    : shown;
            }
            if (doc.workflow_state === 'In Transit'
                    && frappe.datetime.get_day_diff(frappe.datetime.get_today(), value) > 0) {
                return '<span style="color:var(--red-600)"><b>' + shown + '</b> '
                    + __('overdue') + '</span>';
            }
            return shown;
        }
    });

(function() {
    var prev = frappe.listview_settings['Inbound Shipment V3'].onload;
    frappe.listview_settings['Inbound Shipment V3'].onload = function(listview) {
        if (prev) prev(listview);
        listview.page.add_actions_menu_item(__('Delete Drafts'), function() {
            var picked = listview.get_checked_items() || [];
            if (!picked.length) {
                frappe.msgprint(__('Tick the rows you want to remove first.'));
                return;
            }
            var drafts = picked.filter(function(d) { return cint(d.docstatus) === 0; });
            var locked = picked.length - drafts.length;
            if (!drafts.length) {
                frappe.msgprint(__('Nothing selected is a draft. Submitted shipments must be cancelled first.'));
                return;
            }
            var names = drafts.map(function(d) { return d.name; });
            var msg = __('Permanently delete {0} draft {1}?', [names.length, 'shipment']);
            if (locked) {
                msg += '<br><br>' + __('{0} selected row(s) are not drafts and will be left alone.', [locked]);
            }
            msg += '<br><br><b>' + names.slice(0, 12).join('<br>') + '</b>'
                 + (names.length > 12 ? '<br>…' : '');
            frappe.confirm(msg, function() {
                frappe.call({
                    method: 'frappe.desk.reportview.delete_items',
                    args: { doctype: 'Inbound Shipment V3', items: names },
                    freeze: true,
                    freeze_message: __('Deleting {0}…', [names.length]),
                    callback: function() {
                        frappe.show_alert({ message: __('Deleted {0}', [names.length]), indicator: 'green' });
                        listview.refresh();
                    }
                });
            });
        });
    };
})();