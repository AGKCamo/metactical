frappe.pages["price-revision"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Price Revision"),
		single_column: true,
	});

	new PriceRevisionPage(wrapper);
};

class PriceRevisionPage {
	constructor(wrapper) {
		this.wrapper = $(wrapper);
		this.page = wrapper.page;
		this.page.main.append(`<div id="price_revision_ui"></div>`);
		this.app = new metactical.price_revision.PriceRevision(this.wrapper);

		const me = this;
		this.page.set_secondary_action(
			"",
			() => me.app.vue_instance.refresh(),
			"refresh"
		);
	}
}
