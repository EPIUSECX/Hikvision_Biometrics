# -*- coding: utf-8 -*-
"""
Error Handler - Handles errors with retry logic and exponential backoff
"""
import frappe
import time
from datetime import datetime, timedelta
from requests.exceptions import RequestException, Timeout, ConnectionError


class SyncError(Exception):
    """Custom exception for sync errors"""
    pass


def retry_with_backoff(func, max_retries=3, initial_delay=1, max_delay=60, backoff_factor=2):
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Function to retry
        max_retries: Maximum number of retries
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_factor: Multiplier for delay on each retry
    
    Returns:
        Result of function call
    
    Raises:
        Exception: If all retries fail
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func()
        except (RequestException, Timeout, ConnectionError) as e:
            last_exception = e
            if attempt < max_retries:
                frappe.logger().warning(
                    f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {delay} seconds..."
                )
                time.sleep(delay)
                delay = min(delay * backoff_factor, max_delay)
            else:
                frappe.logger().error(f"All {max_retries + 1} attempts failed: {str(e)}")
        except Exception as e:
            # Don't retry for non-network errors
            raise
    
    raise last_exception


def queue_failed_sync(device_ip, error_message, sync_type="device_sync"):
    """
    Queue a failed sync for retry later.
    
    Args:
        device_ip: Device IP address
        error_message: Error message
        sync_type: Type of sync (device_sync, checkin_sync, attendance_sync)
    
    Returns:
        str: Queue entry name
    """
    try:
        # Create a failed sync record
        if not frappe.db.exists("DocType", "Biometric Failed Sync"):
            # Log to error log if doctype doesn't exist
            frappe.log_error(
                f"Failed {sync_type} for device {device_ip}: {error_message}",
                "Biometric Sync Error"
            )
            return None
        
        failed_sync = frappe.new_doc("Biometric Failed Sync")
        failed_sync.device_ip = device_ip
        failed_sync.sync_type = sync_type
        failed_sync.error_message = error_message
        failed_sync.status = "Pending Retry"
        failed_sync.retry_count = 0
        failed_sync.insert(ignore_permissions=True)
        
        return failed_sync.name
    except Exception as e:
        frappe.log_error(
            f"Error queuing failed sync: {str(e)}",
            "Biometric Error Handler"
        )
        return None


def retry_failed_syncs():
    """
    Retry all pending failed syncs.
    Called by scheduler.
    """
    if not frappe.db.exists("DocType", "Biometric Failed Sync"):
        return
    
    failed_syncs = frappe.get_all(
        "Biometric Failed Sync",
        filters={"status": "Pending Retry"},
        fields=["name", "device_ip", "sync_type", "error_message", "retry_count"],
        limit=10  # Process 10 at a time
    )
    
    for sync in failed_syncs:
        try:
            # Increment retry count
            retry_count = sync.retry_count + 1
            
            if retry_count > 5:  # Max 5 retries
                frappe.db.set_value("Biometric Failed Sync", sync.name, {
                    "status": "Failed",
                    "retry_count": retry_count
                })
                continue
            
            # Attempt retry based on sync type
            success = False
            if sync.sync_type == "device_sync":
                # Retry device sync
                from biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings import sync_attendance_device_only
                try:
                    sync_attendance_device_only()
                    success = True
                except Exception as e:
                    frappe.log_error(f"Retry failed: {str(e)}", "Biometric Sync Retry")
            
            if success:
                frappe.db.set_value("Biometric Failed Sync", sync.name, {
                    "status": "Completed",
                    "retry_count": retry_count
                })
            else:
                frappe.db.set_value("Biometric Failed Sync", sync.name, {
                    "retry_count": retry_count
                })
        
        except Exception as e:
            frappe.log_error(
                f"Error retrying failed sync {sync.name}: {str(e)}",
                "Biometric Sync Retry Error"
            )
    
    frappe.db.commit()


def handle_device_connection_error(device_ip, error, context=None):
    """
    Handle device connection errors with appropriate logging and queuing.
    
    Args:
        device_ip: Device IP address
        error: Exception object
        context: Additional context dict
    """
    error_message = str(error)
    
    if isinstance(error, ConnectionError):
        error_type = "Connection Error"
    elif isinstance(error, Timeout):
        error_type = "Timeout Error"
    else:
        error_type = "Request Error"
    
    full_message = f"{error_type} for device {device_ip}: {error_message}"
    if context:
        full_message += f" Context: {context}"
    
    frappe.log_error(full_message, "Biometric Device Connection Error")
    
    # Queue for retry
    queue_failed_sync(device_ip, error_message, "device_sync")
    
    # Update device status if device monitoring is enabled
    try:
        from biometric_integration.biometric_integration.device_monitor import update_device_status
        update_device_status(device_ip, "Offline", error_message)
    except ImportError:
        pass  # Device monitor not available


def log_sync_operation(operation, device_ip=None, status="Success", details=None):
    """
    Log a sync operation for audit purposes.
    
    Args:
        operation: Operation name
        device_ip: Device IP (optional)
        status: Status (Success, Failed, Partial)
        details: Additional details dict
    """
    log_message = f"Sync Operation: {operation}"
    if device_ip:
        log_message += f" | Device: {device_ip}"
    log_message += f" | Status: {status}"
    
    if details:
        log_message += f" | Details: {details}"
    
    frappe.logger().info(log_message)
