"""
Payment service for handling payment processing and access granting.

Coordinates between payment processor and user service to grant access
after successful payment.
"""

from typing import Optional, Dict, Any

from core.database import Database
from core.models import Tariff
from payment.base import PaymentProcessor, PaymentStatus
from services.user_service import UserService


class PaymentService:
    """Service for payment processing operations."""
    
    # Tariff prices (in your currency - adjust as needed)
    TARIFF_PRICES = {
        Tariff.BASIC: 3000.0,      # Базовый тариф - только задания без обратной связи
        Tariff.FEEDBACK: 5000.0,   # С обратной связью от лидера
        Tariff.PREMIUM: 8000.0,    # Обратная связь + премиум сообщество
    }
    
    def __init__(self, db: Database, payment_processor: PaymentProcessor):
        self.db = db
        self.payment_processor = payment_processor
        self.user_service = UserService(db)
    
    async def initiate_payment(
        self,
        user_id: int,
        tariff: Tariff,
        referral_partner_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Initiate a payment for course access.
        
        Returns payment information including payment URL.
        """
        from core.config import Config
        
        amount = self.TARIFF_PRICES[tariff]
        currency = Config.PAYMENT_CURRENCY  # RUB, USD, EUR, etc.
        
        description = f"Course Access - {tariff.value.upper()} Tariff"
        
        metadata = {
            "tariff": tariff.value,
            "user_id": user_id,
            "referral_partner_id": referral_partner_id
        }
        
        payment_info = await self.payment_processor.create_payment(
            user_id=user_id,
            amount=amount,
            currency=currency,
            description=description,
            metadata=metadata
        )
        
        return payment_info
    
    async def check_payment(self, payment_id: str) -> PaymentStatus:
        """Check payment status."""
        return await self.payment_processor.check_payment_status(payment_id)
    
    async def process_payment_completion(
        self,
        payment_id: str,
        webhook_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Process completed payment and grant access.
        
        This should be called when payment webhook is received or
        when checking payment status shows completion.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        logger.info(f"🔄 Processing payment completion for: {payment_id}")
        
        # Get payment info from webhook or check status
        if webhook_data:
            payment_data = await self.payment_processor.process_webhook(webhook_data)
        else:
            # First check if payment is completed
            status = await self.check_payment(payment_id)
            logger.info(f"   Payment status check: {status.value}")
            
            if status != PaymentStatus.COMPLETED:
                logger.warning(f"   Payment not completed yet: {status.value}")
                return None
            
            # Fetch payment details from processor
            # For mock processor, use get_payment_details if available
            if hasattr(self.payment_processor, 'get_payment_details'):
                payment_data = await self.payment_processor.get_payment_details(payment_id)
                logger.info(f"   Payment data retrieved: {payment_data is not None}")
            else:
                # Fallback: try webhook with payment_id
                payment_data = await self.payment_processor.process_webhook({"payment_id": payment_id})
            
            if not payment_data:
                logger.error(f"   Failed to get payment data for {payment_id}")
                return None
            
            # Double-check status from payment data if available
            # Status might be PaymentStatus enum or string
            payment_status = payment_data.get("status")
            if payment_status:
                if isinstance(payment_status, PaymentStatus):
                    if payment_status != PaymentStatus.COMPLETED:
                        logger.warning(f"   Payment data status check failed: {payment_status.value}")
                        return None
                elif isinstance(payment_status, str):
                    if payment_status != PaymentStatus.COMPLETED.value:
                        logger.warning(f"   Payment data status check failed: {payment_status}")
                        return None
        
        if not payment_data:
            logger.error("   No payment data available")
            return None
        
        metadata = payment_data.get("metadata", {})
        user_id = metadata.get("user_id") or payment_data.get("user_id")
        tariff_str = metadata.get("tariff")
        referral_partner_id = metadata.get("referral_partner_id")
        
        logger.info(f"   Extracted: user_id={user_id}, tariff={tariff_str}, referral={referral_partner_id}")
        
        if not user_id or not tariff_str:
            logger.error(f"   Missing required data: user_id={user_id}, tariff={tariff_str}")
            return None
        
        tariff = Tariff(tariff_str)
        
        # Grant access to user
        logger.info(f"   Granting access to user {user_id} with tariff {tariff.value}")
        user = await self.user_service.grant_access(
            user_id=user_id,
            tariff=tariff,
            referral_partner_id=referral_partner_id
        )
        
        logger.info(f"   ✅ Access granted successfully to user {user_id}")
        
        return {
            "user_id": user_id,
            "tariff": tariff.value,
            "user": user
        }

