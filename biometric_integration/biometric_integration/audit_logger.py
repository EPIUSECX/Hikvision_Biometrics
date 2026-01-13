# -*- coding: utf-8 -*-
"""
Audit Logger - Comprehensive audit trail for all operations
"""
import frappe
from frappe.utils import now_datetime
from datetime import datetime


def log_sync_operation(operation_type, device_ip=None, status="Success", details=None, error=None):
    """
    Log a sync operation to audit trail.
    
    Args:
        operation_type: Type of operation (device_sync, checkin_sync, attendance_sync)
        device_ip: Device IP address (optional)
        status: Status (Success, Failed, Partial)
        details: Additional details dict
        error: Error message if any
    """
    try:
        if not frappe.db.exists("DocType", "Biometric Audit Log"):
            # Fallback to error log if doctype doesn't exist
            message = f"Operation: {operation_type} | Device: {device_ip} | Status: {status}"
            if details:
                message += f" | Details: {details}"
            if error:
                message += f" | Error: {error}"
            frappe.log_error(message, "Biometric Audit Log")
            return
        
        audit_log = frappe.new_doc("Biometric Audit Log")
        audit_log.operation_type = operation_type
        audit_log.device_ip = device_ip
        audit_log.status = status
        audit_log.error_message = error
        audit_log.timestamp = now_datetime()
        
        if details:
            audit_log.details = str(details)
        
        audit_log.insert(ignore_permissions=True)
        frappe.db.commit()
    
    except Exception as e:
        frappe.log_error(
            f"Error creating audit log: {str(e)}",
            "Audit Logger Error"
        )


def log_device_interaction(device_ip, action, status, response_time=None, error=None):
    """
    Log device interaction.
    
    Args:
        device_ip: Device IP address
        action: Action performed (fetch_events, health_check, etc.)
        status: Status (Success, Failed)
        response_time: Response time in seconds
        error: Error message if any
    """
    log_sync_operation(
        operation_type="device_interaction",
        device_ip=device_ip,
        status=status,
        details={
            "action": action,
            "response_time": response_time
        },
        error=error
    )


def log_data_transformation(transformation_type, source_count, target_count, errors=None):
    """
    Log data transformation operations.
    
    Args:
        transformation_type: Type (punches_to_checkin, checkin_to_attendance)
        source_count: Number of source records
        target_count: Number of target records created
        errors: List of errors if any
    """
    log_sync_operation(
        operation_type="data_transformation",
        status="Success" if not errors else "Partial",
        details={
            "transformation_type": transformation_type,
            "source_count": source_count,
            "target_count": target_count,
            "errors": errors
        }
    )


def log_webhook_event(employee_no, event_time, device_ip, status, error=None):
    """
    Log webhook event reception.
    
    Args:
        employee_no: Employee number from device
        event_time: Event timestamp
        device_ip: Device IP address
        status: Status (Success, Failed)
        error: Error message if any
    """
    log_sync_operation(
        operation_type="webhook_event",
        device_ip=device_ip,
        status=status,
        details={
            "employee_no": employee_no,
            "event_time": str(event_time)
        },
        error=error
    )


def get_audit_summary(start_date=None, end_date=None, operation_type=None):
    """
    Get summary of audit logs.
    
    Args:
        start_date: Start date for filtering
        end_date: End date for filtering
        operation_type: Filter by operation type
    
    Returns:
        dict: Summary statistics
    """
    try:
        if not frappe.db.exists("DocType", "Biometric Audit Log"):
            return {"total": 0, "success": 0, "failed": 0}
        
        filters = {}
        if start_date:
            filters["timestamp"] = [">=", start_date]
        if end_date:
            if "timestamp" in filters:
                filters["timestamp"][1] = ["between", [start_date, end_date]]
            else:
                filters["timestamp"] = ["<=", end_date]
        if operation_type:
            filters["operation_type"] = operation_type
        
        logs = frappe.get_all(
            "Biometric Audit Log",
            filters=filters,
            fields=["status"],
            limit=1000
        )
        
        total = len(logs)
        success = sum(1 for log in logs if log.status == "Success")
        failed = total - success
        
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "success_rate": (success / total * 100) if total > 0 else 0
        }
    
    except Exception as e:
        frappe.log_error(
            f"Error getting audit summary: {str(e)}",
            "Audit Logger Error"
        )
        return {"total": 0, "success": 0, "failed": 0}
