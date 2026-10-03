// Copyright (c) 2025, NDV and contributors
// For license information, please see license.txt

const DEVICE_ACTIONS = __("Device Actions");

frappe.ui.form.on("Biometric Integration Settings", {
    refresh(frm) {
        render_device_summary(frm);

        frm.add_custom_button(__("Test Devices"), () => test_devices(frm), DEVICE_ACTIONS);
        frm.add_custom_button(__("Sync Now"), () => sync_now(frm), DEVICE_ACTIONS);
        frm.add_custom_button(
            __("Reprocess Existing Events"),
            () => reprocess_events(frm),
            DEVICE_ACTIONS
        );
    },
});

function render_device_summary(frm) {
    const wrapper = frm.get_field("devices_intro").$wrapper;
    wrapper.html(
        `<div class="text-muted">${__(
            "Add each attendance terminal below. Active devices are used by Test Devices and Sync Now."
        )}</div>`
    );

    frappe.call({
        method: "biometric_integration.biometric_integration.device_monitor.get_device_status_summary",
        callback(r) {
            const summary = r.message;
            if (!summary) return;

            let indicator = "gray";
            let status_text = __("No devices configured");
            if (summary.total) {
                indicator = summary.offline ? "red" : summary.online ? "green" : "orange";
                status_text = __("{0} online · {1} offline · {2} configured", [
                    summary.online,
                    summary.offline,
                    summary.total,
                ]);
            }

            wrapper.html(`
                <div class="d-flex justify-content-between align-items-center">
                    <div class="text-muted">${__(
                        "Active devices feed Employee Check-ins; HRMS then calculates Attendance from the complete IN/OUT sequence."
                    )}</div>
                    <span class="indicator ${indicator}">${frappe.utils.escape_html(status_text)}</span>
                </div>
            `);
        },
    });
}

function test_devices(frm) {
    frappe.call({
        method: "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.test_connection",
        freeze: true,
        freeze_message: __("Testing Hikvision devices..."),
        callback(r) {
            if (!r.message) return;

            const rows = r.message
                .map((device) => {
                    const ok = device.status === "success";
                    const indicator = ok ? "green" : "red";
                    const label = ok ? __("Connected") : __("Failed");
                    return `<tr>
                        <td>${frappe.utils.escape_html(device.label || "")}</td>
                        <td>${frappe.utils.escape_html(device.ip || "")}</td>
                        <td><span class="indicator ${indicator}">${label}</span></td>
                        <td>${frappe.utils.escape_html(device.message || "")}</td>
                    </tr>`;
                })
                .join("");

            frappe.msgprint({
                title: __("Device Connection Results"),
                message: `<div class="table-responsive"><table class="table table-bordered">
                    <thead><tr><th>${__("Device")}</th><th>${__("IP Address")}</th><th>${__(
                        "Status"
                    )}</th><th>${__("Details")}</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table></div>`,
                wide: true,
            });
            frm.reload_doc();
        },
    });
}

function sync_now(frm) {
    frappe.call({
        method: "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.sync_attendance",
        freeze: true,
        freeze_message: __("Fetching device events and updating HRMS check-ins..."),
        callback(r) {
            if (!r.message) return;
            frappe.msgprint({
                title: __("Attendance Sync Complete"),
                message: r.message,
                indicator: "green",
            });
            frm.reload_doc();
        },
    });
}

function reprocess_events(frm) {
    frappe.call({
        method: "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.sync_to_employee_checkin_only",
        freeze: true,
        freeze_message: __("Reprocessing stored events into HRMS check-ins..."),
        callback(r) {
            if (!r.message) return;
            frappe.msgprint({
                title: __("Reprocessing Complete"),
                message: r.message,
                indicator: "green",
            });
            frm.reload_doc();
        },
    });
}
