"""
Payment service for handling payment processing and access granting.

Coordinates between payment processor and user service to grant access
after successful payment.
"""

from decimal import Decimal, InvalidOperation
from typing import Optional, Dict, Any, Tuple

from core.database import Database
from core.models import Tariff
from payment.base import PaymentProcessor, PaymentStatus
from services.user_service import UserService


class PaymentService:
    """Service for payment processing operations."""
    
    # Tariff prices (in your currency - adjust as needed)
    TARIFF_PRICES = {
        Tariff.BASIC: 5000.0,     # Базовый тариф
        Tariff.FEEDBACK: 10000.0,  # С обратной связью от лидера - проверка заданий, ответы на вопросы
        Tariff.PREMIUM: 8000.0,    # Обратная связь + премиум сообщество
        Tariff.PRACTIC: 20000.0,   # Всё из Basic + Feedback + 3 онлайн интервью с разбором
    }
    
    def __init__(self, db: Database, payment_processor: PaymentProcessor):
        self.db = db
        self.payment_processor = payment_processor
        self.user_service = UserService(db)

    async def get_tariff_base_price(self, tariff: Tariff) -> float:
        """Return current base price for tariff (can be overridden via DB settings)."""
        return await self.db.get_online_tariff_price(tariff, self.TARIFF_PRICES[tariff])

    async def _apply_promo_to_amount(self, base_amount: float, promo_code: Optional[str]) -> Tuple[float, Optional[dict]]:
        code = (promo_code or "").strip()
        if not code:
            return float(base_amount), None
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            return float(base_amount), None

        discount_type = (promo.get("discount_type") or "").strip().lower()
        discount_value = float(promo.get("discount_value") or 0.0)

        if discount_type == "percent":
            amount = float(base_amount) * (1.0 - (discount_value / 100.0))
        else:
            # default: fixed amount discount
            amount = float(base_amount) - float(discount_value)

        return max(0.0, round(amount, 2)), promo
    
    async def initiate_payment(
        self,
        user_id: int,
        tariff: Tariff,
        referral_partner_id: Optional[str] = None,
        customer_email: Optional[str] = None,
        course_program: Optional[str] = None,
        promo_code: Optional[str] = None,
        upgrade_from: Optional[Tariff] = None,
        upgrade_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Initiate a payment for course access or upgrade.
        
        Args:
            user_id: User ID
            tariff: Target tariff
            referral_partner_id: Optional referral partner ID
            upgrade_from: If this is an upgrade, the current tariff
            upgrade_price: If this is an upgrade, the price difference to pay
        
        Returns payment information including payment URL.
        """
        from core.config import Config

        promo = None
        base_amount = None
        upgrade_base_amount = None

        # Upgrade flow: compute price difference (optionally discounted by promo)
        if upgrade_from is not None:
            if upgrade_price is None:
                new_base = await self.get_tariff_base_price(tariff)
                old_base = await self.get_tariff_base_price(upgrade_from)
                upgrade_base_amount = max(0.0, float(new_base) - float(old_base))
            else:
                # Backward-compatible: treat upgrade_price as base amount for upgrade
                upgrade_base_amount = max(0.0, float(upgrade_price))

            base_amount = upgrade_base_amount
            amount, promo = await self._apply_promo_to_amount(upgrade_base_amount, promo_code)
            description = f"Tariff Upgrade: {upgrade_from.value.upper()} → {tariff.value.upper()}"
        else:
            base_amount = await self.get_tariff_base_price(tariff)
            amount, promo = await self._apply_promo_to_amount(base_amount, promo_code)
            description = f"Course Access - {tariff.value.upper()} Tariff"
        
        currency = Config.PAYMENT_CURRENCY  # RUB, USD, EUR, etc.
        
        metadata = {
            "tariff": tariff.value,
            "user_id": user_id,
            "referral_partner_id": referral_partner_id
        }

        if customer_email:
            metadata["customer_email"] = customer_email

        if course_program:
            metadata["course_program"] = course_program

        if promo:
            metadata["promo_code"] = promo.get("code")
            metadata["promo_discount_type"] = promo.get("discount_type")
            metadata["promo_discount_value"] = promo.get("discount_value")
            metadata["base_amount"] = base_amount
        
        # Добавляем информацию об апгрейде в метаданные
        if upgrade_from is not None:
            metadata["upgrade_from"] = upgrade_from.value
            metadata["is_upgrade"] = True
            if upgrade_base_amount is not None:
                metadata["upgrade_base_amount"] = upgrade_base_amount
            # Store final upgrade amount for validation/backward-compatibility
            metadata["upgrade_price"] = amount
        
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

        try:
            tariff = Tariff(tariff_str)
        except Exception:
            logger.error(f"   Invalid tariff in payment metadata: {tariff_str}")
            return None

        # Idempotency: skip if already processed (still report success)
        processed_payment_id = payment_data.get("payment_id") or payment_id
        if processed_payment_id:
            try:
                if await self.db.is_payment_processed(processed_payment_id):
                    logger.info(f"   Payment {processed_payment_id} already processed; skipping.")
                    existing_user = await self.user_service.get_user(int(user_id))
                    return {
                        "user_id": user_id,
                        "tariff": tariff.value,
                        "user": existing_user,
                        "is_upgrade": bool(metadata.get("is_upgrade", False)),
                        "already_processed": True
                    }
            except Exception:
                # If idempotency check fails, continue to avoid blocking access
                logger.warning("   Failed to check payment idempotency; continuing.", exc_info=True)

        # Validate amount to prevent underpayment or tampered metadata
        def _to_decimal(value) -> Optional[Decimal]:
            try:
                return Decimal(str(value))
            except (InvalidOperation, TypeError):
                return None

        amount_value = _to_decimal(payment_data.get("amount"))
        expected_amount = None
        if metadata.get("is_upgrade", False):
            # Prefer metadata snapshot (promo included) for deterministic validation.
            promo_code = metadata.get("promo_code")
            promo_discount_type = metadata.get("promo_discount_type")
            promo_discount_value = metadata.get("promo_discount_value")
            base_amount_meta = metadata.get("base_amount") or metadata.get("upgrade_base_amount")

            if promo_code and promo_discount_type and promo_discount_value is not None and base_amount_meta is not None:
                base_amount_dec = _to_decimal(base_amount_meta)
                discount_dec = _to_decimal(promo_discount_value)
                if base_amount_dec is not None and discount_dec is not None:
                    if str(promo_discount_type).strip().lower() == "percent":
                        expected_amount = base_amount_dec * (Decimal("1") - (discount_dec / Decimal("100")))
                    else:
                        expected_amount = base_amount_dec - discount_dec

            if expected_amount is None:
                upgrade_price_meta = metadata.get("upgrade_price")
                expected_amount = _to_decimal(upgrade_price_meta)

            if expected_amount is None and metadata.get("upgrade_from"):
                try:
                    upgrade_from = Tariff(metadata.get("upgrade_from"))
                    new_base = await self.get_tariff_base_price(tariff)
                    old_base = await self.get_tariff_base_price(upgrade_from)
                    expected_amount = _to_decimal(max(0.0, float(new_base) - float(old_base)))
                except Exception:
                    expected_amount = None
        else:
            # Prefer metadata snapshot (promo included) for deterministic validation.
            promo_code = metadata.get("promo_code")
            promo_discount_type = metadata.get("promo_discount_type")
            promo_discount_value = metadata.get("promo_discount_value")
            base_amount_meta = metadata.get("base_amount")

            if promo_code and promo_discount_type and promo_discount_value is not None and base_amount_meta is not None:
                base_amount_dec = _to_decimal(base_amount_meta)
                discount_dec = _to_decimal(promo_discount_value)
                if base_amount_dec is not None and discount_dec is not None:
                    if str(promo_discount_type).strip().lower() == "percent":
                        expected_amount = base_amount_dec * (Decimal("1") - (discount_dec / Decimal("100")))
                    else:
                        expected_amount = base_amount_dec - discount_dec

            if expected_amount is None:
                expected_amount = _to_decimal(await self.get_tariff_base_price(tariff))

        if amount_value is not None and expected_amount is not None:
            tolerance = Decimal("0.01")
            if amount_value + tolerance < expected_amount:
                logger.error(
                    f"   Payment amount mismatch: received={amount_value} expected>={expected_amount}"
                )
                return None

        # Grant access to user
        logger.info(f"   Granting access to user {user_id} with tariff {tariff.value}")
        # Проверяем, это апгрейд или новый доступ
        is_upgrade = metadata.get("is_upgrade", False)
        
        if is_upgrade:
            # Это апгрейд - обновляем тариф пользователя
            user = await self.user_service.get_user(user_id)
            if user:
                user.tariff = tariff
                await self.db.update_user(user)
                logger.info(f"   ✅ User {user_id} upgraded to {tariff.value.upper()}")
            else:
                logger.error(f"   ❌ User {user_id} not found for upgrade")
                return None
        else:
            # Это новый доступ
            user = await self.user_service.grant_access(
                user_id=user_id,
                tariff=tariff,
                referral_partner_id=referral_partner_id
            )
        
        logger.info(f"   ✅ Access granted successfully to user {user_id}")
        
        if processed_payment_id:
            try:
                await self.db.try_mark_payment_processed(processed_payment_id)
            except Exception:
                logger.warning(
                    f"   Failed to mark payment {processed_payment_id} as processed",
                    exc_info=True
                )

        # Mark promo code as used (best-effort) and clear user promo binding
        promo_code = metadata.get("promo_code")
        if promo_code:
            try:
                await self.db.increment_promo_code_use(str(promo_code))
            except Exception:
                logger.warning("   Failed to increment promo usage", exc_info=True)
            try:
                await self.db.clear_user_promo_code(int(user_id))
            except Exception:
                logger.warning("   Failed to clear user promo code", exc_info=True)

        return {
            "user_id": user_id,
            "tariff": tariff.value,
            "user": user,
            "is_upgrade": is_upgrade
        }
