"""
twilio_service.py - Twilio 2-Step SMS Verification for HDD File Recovery Suite.

Sends 6-digit one-time password (OTP) codes to the user's phone via Twilio SMS.
If Twilio credentials are not configured or empty, it smoothly operates in
simulation/development mode, logging the OTP so local and test environments
remain fully functional without failing authentication.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

import httpx

logger = logging.getLogger("frs.twilio")


def mask_phone_number(phone: Optional[str]) -> str:
    """Mask phone number for safe display in UI, e.g. +91 98765 43210 -> +91 ••••• ••210."""
    if not phone:
        return "+91 ••••• ••076"
    clean = str(phone).strip()
    if len(clean) <= 4:
        return clean
    prefix = clean[:3]
    last2 = clean[-2:]
    return f"{prefix} ••••• ••{last2}"


def generate_otp_code(digits: int = 6) -> str:
    """Generate a secure cryptographic 6-digit verification code."""
    min_val = 10 ** (digits - 1)
    max_val = (10 ** digits) - 1
    return str(secrets.randbelow(max_val - min_val + 1) + min_val)


def send_sms_otp(
    to_phone: str,
    code: str,
    account_sid: str,
    auth_token: str,
    from_number: str,
) -> dict[str, Any]:
    """
    Sends an SMS verification code via Twilio REST API.
    
    If credentials are missing or placeholders, runs in simulation mode.
    """
    to_phone = to_phone.strip()
    message_body = (
        f"Your HDD File Recovery verification code is: {code}. "
        "Valid for 5 minutes. Do not share this code with anyone."
    )

    is_configured = bool(
        account_sid
        and auth_token
        and from_number
        and not account_sid.startswith("your_")
        and not auth_token.startswith("your_")
        and not from_number.startswith("your_")
    )

    if not is_configured:
        logger.info(
            "[TWILIO SMS SIMULATION] Sending OTP %s to %s (Twilio keys not configured in backend/.env)",
            code,
            to_phone,
        )
        return {
            "success": True,
            "simulated": True,
            "to": to_phone,
            "code": code,
            "message": "OTP sent via development simulation (add Twilio keys in backend/.env for live SMS).",
        }

    # Live Twilio SMS dispatch via Twilio Messages API
    api_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {
        "To": to_phone,
        "From": from_number,
        "Body": message_body,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                api_url,
                data=data,
                auth=(account_sid, auth_token),
            )
            if resp.status_code in (200, 201):
                res_json = resp.json()
                logger.info(
                    "Twilio SMS dispatched successfully to %s, SID: %s",
                    mask_phone_number(to_phone),
                    res_json.get("sid"),
                )
                return {
                    "success": True,
                    "simulated": False,
                    "sid": res_json.get("sid"),
                    "to": to_phone,
                }
            else:
                err_detail = resp.text
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("message", err_detail)
                except Exception:
                    pass
                logger.error("Twilio API error HTTP %d: %s", resp.status_code, err_detail)
                return {
                    "success": False,
                    "simulated": False,
                    "error": f"Twilio SMS delivery failed: {err_detail}",
                    "status_code": resp.status_code,
                }
    except Exception as exc:
        logger.exception("Twilio network error connecting to %s", api_url)
        return {
            "success": False,
            "simulated": False,
            "error": f"Failed to contact Twilio service: {exc}",
        }
