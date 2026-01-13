# -*- coding: utf-8 -*-
from __future__ import unicode_literals

app_name = "biometric_integration"
app_title = "Biometric Integration"
app_publisher = "Taomoor"
app_description = "Hikvision biometric devices Integration with ERPNext"
app_icon = "octicon octicon-device-camera"
app_color = "grey"
app_email = "tymuur@outlook.com"
app_license = "MIT"

# Scheduled Tasks
# ---------------

scheduler_events = {
    # Simple options (uncomment if you want daily instead of cron)
    # "daily": [
    #     "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.scheduled_attendance_sync"
    # ],

    "cron": {
        # Run every 15 minutes (change to */5, 0 2 * * *, etc. as you like)
        "*/15 * * * *": [
            "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.scheduled_attendance_sync"
        ],
        # Process attendance daily at 1 AM (optional - HRMS also processes via Shift Type)
        # HRMS processes auto-attendance via hourly_long scheduler in Shift Type
        # This is kept as backup/alternative method for biometric-specific processing
        "0 1 * * *": [
            "biometric_integration.biometric_integration.attendance_processor.create_attendance_from_checkins"
        ],
        # Monitor device health every hour
        "0 * * * *": [
            "biometric_integration.biometric_integration.device_monitor.monitor_all_devices"
        ],
        # Retry failed syncs every 30 minutes
        "*/30 * * * *": [
            "biometric_integration.biometric_integration.error_handler.retry_failed_syncs"
        ]
    },
    # Note: HRMS has its own auto-attendance processing via:
    # - hourly_long: "hrms.hr.doctype.shift_type.shift_type.process_auto_attendance_for_all_shifts"
    # This processes attendance for all shifts with enable_auto_attendance = 1
    # Our custom attendance processor can work alongside or as alternative
}

# Hooks
# -----

doc_events = {
    "Employee Checkin": {
        "after_insert": "biometric_integration.biometric_integration.attendance_processor.on_employee_checkin_insert"
    }
}

# Note on Auto-Attendance Integration:
# HRMS processes auto-attendance via Shift Type.process_auto_attendance() which is called by:
# - hourly_long scheduler: "hrms.hr.doctype.shift_type.shift_type.process_auto_attendance_for_all_shifts"
# This processes checkins grouped by shift and employee.
# Our custom attendance processor (create_attendance_from_checkins) can work alongside HRMS
# or as an alternative for biometric-specific processing that doesn't rely on Shift Type configuration.

# API Routes
# ----------
# Webhook endpoint is accessed via: /api/method/biometric_integration.biometric_integration.api.webhook.receive_device_event
# The function is whitelisted with allow_guest=True for device access
