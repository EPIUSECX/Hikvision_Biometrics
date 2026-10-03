const employee_checkin_settings = frappe.listview_settings["Employee Checkin"] || {};
const hrms_employee_checkin_onload = employee_checkin_settings.onload;

employee_checkin_settings.onload = function (listview) {
	if (hrms_employee_checkin_onload) {
		hrms_employee_checkin_onload(listview);
	}

	// Show the newest biometric event first instead of the order in which
	// records happened to be imported. Practitioners can still re-sort the
	// current view from the standard Frappe sort control.
	listview.sort_by = "time";
	listview.sort_order = "desc";
	listview.sort_selector?.set_value("time", "desc");
};

frappe.listview_settings["Employee Checkin"] = employee_checkin_settings;
