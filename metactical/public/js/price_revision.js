import PriceRevision from "./components/price_revision/PriceRevision.vue";
import { createApp, h, getCurrentInstance } from "vue";

frappe.provide("metactical.price_revision");

metactical.price_revision.PriceRevision = class {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.init();
	}

	init() {
		const app = createApp({
			setup() {
				const instance = getCurrentInstance();
				function refresh() {
					const root = instance?.refs?.root;
					if (root && typeof root.refresh === "function") {
						root.refresh();
					}
				}
				return { refresh };
			},
			render() {
				return h(PriceRevision, { ref: "root" });
			},
		});

		this.vue_instance = app.mount("#price_revision_ui");
		return this.vue_instance;
	}
};
