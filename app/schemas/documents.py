from pydantic import BaseModel, Field
from typing import Optional, Union
from enum import Enum


class DocumentType(str, Enum):
    """The types of documents our system can process."""
    INVOICE = "invoice"
    CONTRACT = "contract"
    RECEIPT = "receipt"
    UNKNOWN = "unknown"


class DocumentStatus(str, Enum):
    """Processing states a document moves through."""
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvoiceExtraction(BaseModel):
    doc_type: str = "invoice"
    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    line_items: list[dict] = Field(default_factory=list)
    payment_terms: Optional[str] = None


class ContractExtraction(BaseModel):
    doc_type: str = "contract"
    contract_title: Optional[str] = None
    parties: list[str] = Field(default_factory=list)
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    contract_value: Optional[float] = None
    currency: Optional[str] = None
    governing_law: Optional[str] = None
    key_obligations: list[str] = Field(default_factory=list)
    termination_conditions: Optional[str] = None


class ReceiptExtraction(BaseModel):
    doc_type: str = "receipt"
    merchant_name: Optional[str] = None
    merchant_address: Optional[str] = None
    transaction_date: Optional[str] = None
    transaction_time: Optional[str] = None
    items: list[dict] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    payment_method: Optional[str] = None


class UnknownExtraction(BaseModel):
    doc_type: str = "unknown"
    summary: Optional[str] = None
    key_information: list[str] = Field(default_factory=list)


# Union type — Claude returns ONE of these depending on document type
DocumentExtraction = Union[
    InvoiceExtraction,
    ContractExtraction,
    ReceiptExtraction,
    UnknownExtraction,
]


class ExtractionResult(BaseModel):
    """The complete result saved to DynamoDB after processing."""
    document_id: str
    document_type: str
    extraction: dict
    confidence: float = Field(ge=0.0, le=1.0)
    model_id: str
    input_tokens: int
    output_tokens: int