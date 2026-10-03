// Copyright (c) 2025, NDV and contributors
// For license information, please see license.txt

frappe.ui.form.on("Biometric Manual Punch", {
    refresh(frm) {
        if (frm.is_new()) {
            frm.set_intro(
                __("Saving this correction immediately updates the employee's HRMS check-in sequence."),
                "blue"
            );
        }
    },
});
