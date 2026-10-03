# -*- coding: utf-8 -*-
"""
Webhook API - Receives real-time events from Hikvision devices
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from datetime import datetime
import json


@frappe.whitelist(allow_guest=True)
def receive_device_event():
    """
    Webhook endpoint to receive real-time events from Hikvision devices.

    Expected payload format (based on Hikvision ISAPI event structure):
    {
        "AcsEvent": {
            "employeeNoString": "12345",
            "time": "2025-01-15T08:30:00+08:00",
            "deviceName": "Device-001",
            "deviceIP": "192.168.1.100"
        }
    }
    """
    try:
        # Get request data
        data = frappe.request.get_json() if frappe.request.is_json else frappe.form_dict

        if not data:
            return {"status": "error", "message": "No data received"}

        # Validate webhook secret if configured
        webhook_secret = frappe.db.get_single_value("Biometric Integration Settings", "webhook_secret")
        if webhook_secret:
            received_secret = frappe.request.headers.get("X-Webhook-Secret")
            if received_secret != webhook_secret:
                frappe.logger().warning("Invalid webhook secret received")
                return {"status": "error", "message": "Invalid secret"}

        # Process event
        event = data.get("AcsEvent") or data

        employee_no = event.get("employeeNoString")
        event_time = event.get("time")
        device_ip = event.get("deviceIP") or event.get("deviceIPAddress")

        if not employee_no or not event_time:
            return {"status": "error", "message": "Missing required fields"}

        # Parse event time
        try:
            # Handle different time formats
            if "T" in event_time:
                event_datetime = datetime.strptime(event_time[:19], "%Y-%m-%dT%H:%M:%S")
            else:
                event_datetime = datetime.strptime(event_time, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            frappe.log_error(f"Error parsing event time: {str(e)}", "Webhook Error")
            return {"status": "error", "message": "Invalid time format"}

        # Create or update Biometric Attendance Log
        from biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings import (
            _sync_for_single_device,
        )

        # Instead of calling sync, directly create the log entry
        attendance_log = frappe.get_all(
            "Biometric Attendance Log",
            filters={"employee_no": employee_no, "event_date": event_datetime.date()},
            limit=1,
        )

        if attendance_log:
            doc = frappe.get_doc("Biometric Attendance Log", attendance_log[0].name)
        else:
            doc = frappe.new_doc("Biometric Attendance Log")
            doc.employee_no = employee_no
            doc.event_date = event_datetime.date()

        # Set device_id if field exists
        if frappe.db.has_column("Biometric Attendance Log", "device_id") and device_ip:
            doc.device_id = device_ip

        # Add punch to table
        punch_row = {"punch_time": event_datetime.time(), "punch_type": "Auto"}

        if frappe.db.has_column("Biometric Attendance Punch Table", "device_id") and device_ip:
            punch_row["device_id"] = device_ip

        doc.append("punch_table", punch_row)
        doc.save(ignore_permissions=True)

        # Optionally trigger immediate sync to Employee Checkin
        settings = frappe.get_single("Biometric Integration Settings")
        if settings.get("auto_create_attendance", 0):
            from biometric_integration.employee_checkin_sync import sync_punches_to_employee_checkin

            sync_punches_to_employee_checkin()

        frappe.db.commit()

        # Log webhook event
        from biometric_integration.biometric_integration.audit_logger import log_webhook_event

        log_webhook_event(employee_no, event_datetime, device_ip, "Success")

        return {"status": "success", "message": "Event processed"}

    except Exception as e:
        frappe.log_error(f"Webhook error: {str(e)}", "Webhook Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_webhook_url():
    """
    Get webhook URL for device configuration.

    Returns:
        dict: Webhook URL and configuration details
    """
    site_url = frappe.utils.get_url()
    webhook_url = (
        f"{site_url}/api/method/biometric_integration.biometric_integration.api.webhook.receive_device_event"
    )

    settings = frappe.get_single("Biometric Integration Settings")
    webhook_secret = settings.get("webhook_secret")

    return {
        "webhook_url": webhook_url,
        "webhook_secret": webhook_secret,
        "method": "POST",
        "content_type": "application/json",
    }
