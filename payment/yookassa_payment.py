"""
YooKassa payment processor implementation.

YooKassa is a popular payment gateway in Russia and CIS countries.
This implementation handles payment creation, status checking, and webhook processing.

Setup:
1. Register at https://yookassa.ru/
2. Get your Shop ID and Secret Key
3. Add to .env:
   YOOKASSA_SHOP_ID=your_shop_id
   YOOKASSA_SECRET_KEY=your_secret_key
   YOOKASSA_RETURN_URL=https://your-domain.com/payment/return
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from datetime import datetime

try:
    import yookassa
    from yookassa import Payment, Configuration
    from yookassa.domain.notification import WebhookNotificationFactory
    YOOKASSA_AVAILABLE = True
except ImportError:
    YOOKASSA_AVAILABLE = False
    logging.warning("yookassa library not installed. Install with: pip install yookassa")

from payment.base import PaymentProcessor, PaymentStatus

logger = logging.getLogger(__name__)


class YooKassaPaymentProcessor(PaymentProcessor):
    """
    YooKassa payment processor implementation.
    
    Handles payment creation, status checking, and webhook processing
    for YooKassa payment gateway.
    """
    
    def __init__(self, shop_id: str, secret_key: str, return_url: str):
        """
        Initialize YooKassa payment processor.
        
        Args:
            shop_id: YooKassa Shop ID
            secret_key: YooKassa Secret Key
            return_url: URL to redirect user after payment
        """
        if not YOOKASSA_AVAILABLE:
            raise ImportError(
                "yookassa library is not installed. "
                "Install it with: pip install yookassa"
            )
        
        self.shop_id = shop_id
        self.secret_key = secret_key
        self.return_url = return_url
        
        # Configure YooKassa
        Configuration.account_id = shop_id
        Configuration.secret_key = secret_key
        
        logger.info("YooKassa payment processor initialized")
    
    async def create_payment(
        self,
        user_id: int,
        amount: float,
        currency: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a payment in YooKassa.
        
        Returns payment information including payment URL.
        """
        try:
            from core.config import Config

            # Prepare payment data
            payment_data = {
                "amount": {
                    "value": f"{amount:.2f}",
                    "currency": currency.upper()
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": self.return_url
                },
                "capture": True,
                "description": description,
                "metadata": {
                    "user_id": str(user_id),
                    **(metadata or {})
                }
            }

            # Receipt (54-FZ): some shops require it, otherwise payment creation fails with
            # "Receipt is missing or illegal" (invalid_request).
            receipt_required = str(getattr(Config, "YOOKASSA_RECEIPT_REQUIRED", "0")).strip() == "1"
            customer_email = (metadata or {}).get("customer_email") if metadata else None

            if receipt_required or customer_email:
                if not customer_email:
                    raise ValueError("Receipt required but customer_email is missing")
                try:
                    tax_system_code = int(getattr(Config, "YOOKASSA_TAX_SYSTEM_CODE", "2") or "2")
                except Exception:
                    tax_system_code = 2
                try:
                    vat_code = int(getattr(Config, "YOOKASSA_VAT_CODE", "1") or "1")
                except Exception:
                    vat_code = 1

                payment_data["receipt"] = {
                    "customer": {"email": customer_email},
                    "items": [
                        {
                            "description": description[:128],
                            "quantity": "1.00",
                            "amount": {"value": f"{amount:.2f}", "currency": currency.upper()},
                            "vat_code": vat_code,
                            # Defaults that work for digital services in most cases
                            "payment_mode": "full_payment",
                            "payment_subject": "service",
                        }
                    ],
                    "tax_system_code": tax_system_code,
                }
            
            # Create payment
            payment = await asyncio.to_thread(Payment.create, payment_data)
            
            payment_id = payment.id
            payment_url = payment.confirmation.confirmation_url if payment.confirmation else None
            
            logger.info(f"YooKassa payment created: {payment_id} for user {user_id}")
            
            return {
                "payment_id": payment_id,
                "payment_url": payment_url or "",
                "status": PaymentStatus.PENDING
            }
        except Exception as e:
            logger.error(f"Error creating YooKassa payment: {e}", exc_info=True)
            raise
    
    async def check_payment_status(self, payment_id: str) -> PaymentStatus:
        """Check payment status in YooKassa."""
        try:
            payment = await asyncio.to_thread(Payment.find_one, payment_id)
            
            # Map YooKassa status to our PaymentStatus
            status_map = {
                "pending": PaymentStatus.PENDING,
                "waiting_for_capture": PaymentStatus.PENDING,
                "succeeded": PaymentStatus.COMPLETED,
                "canceled": PaymentStatus.CANCELLED,
                "failed": PaymentStatus.FAILED
            }
            
            yookassa_status = payment.status
            return status_map.get(yookassa_status, PaymentStatus.FAILED)
        except Exception as e:
            logger.error(f"Error checking YooKassa payment status: {e}", exc_info=True)
            return PaymentStatus.FAILED
    
    async def get_payment_details(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Get full payment details from YooKassa."""
        try:
            payment = await asyncio.to_thread(Payment.find_one, payment_id)
            
            if payment.status != "succeeded":
                return None
            
            return {
                "payment_id": payment_id,
                "user_id": int(payment.metadata.get("user_id", 0)),
                "amount": float(payment.amount.value),
                "metadata": payment.metadata or {}
            }
        except Exception as e:
            logger.error(f"Error getting YooKassa payment details: {e}", exc_info=True)
            return None
    
    async def process_webhook(self, webhook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process YooKassa webhook notification.
        
        YooKassa sends webhooks when payment status changes.
        This method validates and processes the webhook.
        """
        try:
            # Parse webhook notification
            notification = WebhookNotificationFactory().create(webhook_data)
            
            # Get payment object from notification
            payment_object = notification.object
            
            # Check if payment is completed
            if payment_object.status != "succeeded":
                return None
            
            payment_id = payment_object.id
            
            # Get payment details
            payment = await asyncio.to_thread(Payment.find_one, payment_id)
            
            return {
                "payment_id": payment_id,
                "user_id": int(payment.metadata.get("user_id", 0)),
                "amount": float(payment.amount.value),
                "metadata": payment.metadata or {}
            }
        except Exception as e:
            logger.error(f"Error processing YooKassa webhook: {e}", exc_info=True)
            return None
