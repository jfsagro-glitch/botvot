"""
Mock payment processor for development and testing.

This implementation simulates a payment system without actual payment processing.
In production, replace this with a real payment processor implementation.
"""

import asyncio
from typing import Optional, Dict, Any
from datetime import datetime

from payment.base import PaymentProcessor, PaymentStatus


class MockPaymentProcessor(PaymentProcessor):
    """
    Mock payment processor for development.
    
    Simulates payment flow:
    1. Creates a payment with 'pending' status
    2. Automatically completes after 5 seconds (simulating user payment)
    3. Stores payment state in memory (use database in production)
    """
    
    def __init__(self):
        self.payments: Dict[str, Dict[str, Any]] = {}
        self._auto_complete_tasks: Dict[str, asyncio.Task] = {}
    
    async def create_payment(
        self,
        user_id: int,
        amount: float,
        currency: str,
        description: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Create a mock payment."""
        payment_id = f"mock_payment_{user_id}_{int(datetime.utcnow().timestamp())}"
        
        payment_data = {
            "payment_id": payment_id,
            "user_id": user_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "metadata": metadata or {},
            "status": PaymentStatus.PENDING,
            "created_at": datetime.utcnow().isoformat()
        }
        
        self.payments[payment_id] = payment_data
        
        # Simulate automatic payment completion after 5 seconds
        task = asyncio.create_task(self._auto_complete_payment(payment_id))
        self._auto_complete_tasks[payment_id] = task
        
        return {
            "payment_id": payment_id,
            "payment_url": f"https://mock-payment.example.com/pay/{payment_id}",
            "status": PaymentStatus.PENDING
        }
    
    async def _auto_complete_payment(self, payment_id: str):
        """Automatically complete payment after 5 seconds (mock behavior)."""
        await asyncio.sleep(5)
        if payment_id in self.payments:
            self.payments[payment_id]["status"] = PaymentStatus.COMPLETED
            self.payments[payment_id]["completed_at"] = datetime.utcnow().isoformat()
    
    async def check_payment_status(self, payment_id: str) -> PaymentStatus:
        """Check payment status."""
        if payment_id not in self.payments:
            return PaymentStatus.FAILED
        
        payment = self.payments[payment_id]
        return PaymentStatus(payment["status"])
    
    async def get_payment_details(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Get full payment details by payment ID."""
        if payment_id not in self.payments:
            return None
        
        payment = self.payments[payment_id]
        # Return payment details
        # Status is PaymentStatus enum, convert to value for consistency
        return {
            "payment_id": payment_id,
            "user_id": payment["user_id"],
            "amount": payment["amount"],
            "metadata": payment["metadata"],
            "status": payment["status"]  # This is PaymentStatus enum
        }
    
    async def process_webhook(self, webhook_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process mock webhook.
        
        In a real implementation, this would validate webhook signature
        and extract payment information from the provider's webhook payload.
        """
        payment_id = webhook_data.get("payment_id")
        if not payment_id or payment_id not in self.payments:
            return None
        
        payment = self.payments[payment_id]
        if payment["status"] == PaymentStatus.COMPLETED:
            return {
                "payment_id": payment_id,
                "user_id": payment["user_id"],
                "amount": payment["amount"],
                "metadata": payment["metadata"]
            }
        
        return None

