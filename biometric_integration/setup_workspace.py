import json

import frappe


WORKSPACE_NAME = "Biometric Integration"


def _workspace_content():
    """Return a task-oriented workspace for an HR attendance practitioner."""
    blocks = [
        {
            "id": "biometric-header-start",
            "type": "header",
            "data": {
                "text": '<span class="h4"><b>Run time & attendance</b></span>',
                "col": 12,
            },
        },
    ]

    for block_id, shortcut_name in (
        ("setup", "Devices & Sync"),
        ("checkins", "Employee Check-ins"),
        ("attendance", "Attendance"),
        ("daily", "Daily Review"),
        ("manual", "Manual Correction"),
        ("monthly", "Monthly Summary"),
        ("shifts", "Shift Types"),
        ("employees", "Employees"),
    ):
        blocks.append(
            {
                "id": f"biometric-shortcut-{block_id}",
                "type": "shortcut",
                "data": {"shortcut_name": shortcut_name, "col": 3},
            }
        )

    blocks.extend(
        [
            {"id": "biometric-spacer-1", "type": "spacer", "data": {"col": 12}},
            {
                "id": "biometric-header-resources",
                "type": "header",
                "data": {
                    "text": '<span class="h4"><b>Practitioner resources</b></span>',
                    "col": 12,
                },
            },
            {
                "id": "biometric-card-daily",
                "type": "card",
                "data": {"card_name": "Daily Operations", "col": 4},
            },
            {
                "id": "biometric-card-setup",
                "type": "card",
                "data": {"card_name": "People & Setup", "col": 4},
            },
            {
                "id": "biometric-card-reports",
                "type": "card",
                "data": {"card_name": "Reports", "col": 4},
            },
        ]
    )
    return json.dumps(blocks)


def _append_shortcut(ws, *, label, link_to, shortcut_type="DocType", icon, color):
    values = {
        "type": shortcut_type,
        "label": label,
        "link_to": link_to,
        "icon": icon,
        "color": color,
    }
    if shortcut_type == "DocType":
        values["doc_view"] = "List"
    ws.append("shortcuts", values)


def _append_link(ws, *, label, link_to, link_type="DocType", is_query_report=0):
    values = {
        "type": "Link",
        "label": label,
        "link_type": link_type,
        "link_to": link_to,
    }
    if is_query_report:
        values.update(
            {
                "is_query_report": 1,
                "report_ref_doctype": "Biometric Attendance Log",
            }
        )
    ws.append("links", values)


def create_or_update_biometric_workspace():
    """Create or update the biometric workspace and export it in developer mode."""
    if frappe.db.exists("Workspace", WORKSPACE_NAME):
        ws = frappe.get_doc("Workspace", WORKSPACE_NAME)
    else:
        ws = frappe.new_doc("Workspace")
        ws.name = WORKSPACE_NAME

    ws.label = WORKSPACE_NAME
    ws.title = WORKSPACE_NAME
    ws.icon = "fingerprint"
    ws.indicator_color = "green"
    ws.module = "Biometric Integration"
    ws.public = 1
    ws.is_hidden = 0
    ws.content = _workspace_content()

    ws.set("shortcuts", [])
    _append_shortcut(
        ws,
        label="Devices & Sync",
        link_to="Biometric Integration Settings",
        icon="settings",
        color="blue",
    )
    _append_shortcut(
        ws,
        label="Employee Check-ins",
        link_to="Employee Checkin",
        icon="pointer",
        color="green",
    )
    _append_shortcut(
        ws,
        label="Attendance",
        link_to="Attendance",
        icon="calendar-check",
        color="cyan",
    )
    _append_shortcut(
        ws,
        label="Daily Review",
        link_to="Biometric Daily Report",
        shortcut_type="Report",
        icon="notepad-text",
        color="blue",
    )
    _append_shortcut(
        ws,
        label="Manual Correction",
        link_to="Biometric Manual Punch",
        icon="tool",
        color="orange",
    )
    _append_shortcut(
        ws,
        label="Monthly Summary",
        link_to="Biometric Monthly Report",
        shortcut_type="Report",
        icon="chart",
        color="purple",
    )
    _append_shortcut(
        ws,
        label="Shift Types",
        link_to="Shift Type",
        icon="milestone",
        color="gray",
    )
    _append_shortcut(
        ws,
        label="Employees",
        link_to="Employee",
        icon="users",
        color="green",
    )

    ws.set("links", [])

    ws.append("links", {"type": "Card Break", "label": "Daily Operations"})
    _append_link(ws, label="Employee Check-ins", link_to="Employee Checkin")
    _append_link(ws, label="Attendance", link_to="Attendance")
    _append_link(ws, label="Raw Device Events", link_to="Biometric Attendance Log")
    _append_link(ws, label="Manual Correction", link_to="Biometric Manual Punch")

    ws.append("links", {"type": "Card Break", "label": "People & Setup"})
    _append_link(ws, label="Devices & Sync", link_to="Biometric Integration Settings")
    _append_link(ws, label="Employees", link_to="Employee")
    _append_link(ws, label="Shift Types", link_to="Shift Type")
    _append_link(ws, label="Shift Assignments", link_to="Shift Assignment")

    ws.append("links", {"type": "Card Break", "label": "Reports"})
    _append_link(
        ws,
        label="Daily Punch Review",
        link_to="Biometric Daily Report",
        link_type="Report",
        is_query_report=1,
    )
    _append_link(
        ws,
        label="Monthly Hours Summary",
        link_to="Biometric Monthly Report",
        link_type="Report",
        is_query_report=1,
    )
    _append_link(
        ws,
        label="Shift Attendance",
        link_to="Shift Attendance",
        link_type="Report",
        is_query_report=1,
    )

    ws.set("roles", [])
    for role in ("System Manager", "HR Manager", "HR User", "CE HR"):
        if frappe.db.exists("Role", role):
            ws.append("roles", {"role": role})

    ws.save(ignore_permissions=True)
    frappe.db.commit()
    return ws.name
