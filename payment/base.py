"""
Payment system abstraction interface.

This module defines the abstract base class for payment processors.
Implementations (mock, Stripe, PayPal, etc.) should inherit from PaymentProcessor.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
from enum import Enum


class PaymentStatus(str, Enum):
    """Payment status enumeration."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentProcessor(ABC):
    """
    Abstract base class for payment processors.
    
    This allows the system to work with any payment provider by
    implementing this interface. Examples: Stripe, PayPal, YooKassa, etc.
    """
    
    @abstractmethod
    async def create_payment(
        self,
        user_id: int,
        amount: float,
        currency: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a payment request.
        
        Args:
            user_id: Telegram user ID
            amount: Payment amount
            currency: Currency code (e.g., 'USD', 'RUB')
            description: Payment description
            metadata: Additional metadata (e.g., tariff, referral_id)
        
        Returns:
            Dictionary with payment information:
            - payment_id: Unique payment identifier
            - payment_url: URL for user to complete payment
            - status: Payment status
        """
        pass
    
    @abstractmethod
    async def check_payment_status(self, payment_id: str) -> PaymentStatus:
        """
        Check the status of a payment.
        
        Args:
            payment_id: Payment identifier
        
        Returns:
            PaymentStatus enum value
        """
        pass
    
    @abstractmethod
    async def process_webhook(self, webhook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process payment webhook notification.
        
        Args:
            webhook_data: Webhook payload from payment provider
        
        Returns:
            Dictionary with payment information if payment is completed, None otherwise
        """
        pass

