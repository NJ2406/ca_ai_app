import hashlib
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
import enum
from typing import List, Optional
import uuid

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import String, Numeric, ForeignKey, Date, DateTime, Text, Enum as SQLEnum, select, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class AccountClassification(str, enum.Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"

class InvoiceType(str, enum.Enum):
    OUTWARD_TAX_INVOICE = "OUTWARD_TAX_INVOICE"
    INWARD_PURCHASE = "INWARD_PURCHASE"

class TDSSegment(str, enum.Enum):
    NONE = "NONE"
    SEC_194C = "194C"
    SEC_194J = "194J"
    SEC_194I = "194I"

class ReconciliationStatus(str, enum.Enum):
    PENDING = "PENDING"
    MATCHED = "MATCHED"
    ITC_AVAILABLE = "ITC_AVAILABLE"
    ITC_INELIGIBLE = "ITC_INELIGIBLE"

class Account(Base):
    __tablename__ = "chart_of_accounts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    classification: Mapped[AccountClassification] = mapped_column(SQLEnum(AccountClassification), nullable=False)
    system_tag: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

class JournalTransaction(Base):
    __tablename__ = "journal_transactions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    voucher_number: Mapped[str] = mapped_column(String(50), unique=True)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    narration: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    lines: Mapped[List["JournalLine"]] = relationship(back_populates="transaction", cascade="all, delete-orphan")

class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("journal_transactions.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chart_of_accounts.id"), index=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    transaction: Mapped["JournalTransaction"] = relationship(back_populates="lines")
    account: Mapped["Account"] = relationship()

class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # GST rule enforces maximum 16 alphanumeric characters for invoice numbers
    invoice_number: Mapped[str] = mapped_column(String(16), index=True)
    irn_hash: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    invoice_type: Mapped[InvoiceType] = mapped_column(SQLEnum(InvoiceType), nullable=False)
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    party_gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    place_of_supply: Mapped[str] = mapped_column(String(2), nullable=False)
    origin_state: Mapped[str] = mapped_column(String(2), nullable=False)
    taxable_value: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    cgst: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    sgst: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    igst: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    tds_section: Mapped[TDSSegment] = mapped_column(SQLEnum(TDSSegment), default=TDSSegment.NONE)
    tds_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0.00)
    tds_deducted: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    net_receivable_payable: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=0.00)
    reconciled_status: Mapped[ReconciliationStatus] = mapped_column(SQLEnum(ReconciliationStatus), default=ReconciliationStatus.PENDING)
    invoice_date: Mapped[date] = mapped_column(Date, default=date.today)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS journal_lines, journal_transactions, invoices, chart_of_accounts CASCADE;"))
        await conn.execute(text("DROP TYPE IF EXISTS invoicetype, tdsegment, reconciliationstatus CASCADE;"))
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSessionLocal() as session:
        seed_accounts = [
            Account(code="1001", name="Sundry Debtors", classification=AccountClassification.ASSET, system_tag="DEBTORS"),
            Account(code="1002", name="TDS Receivable", classification=AccountClassification.ASSET, system_tag="TDS_RECEIVABLE"),
            Account(code="1003", name="ITC CGST Ledger", classification=AccountClassification.ASSET, system_tag="ITC_CGST"),
            Account(code="1004", name="ITC SGST Ledger", classification=AccountClassification.ASSET, system_tag="ITC_SGST"),
            Account(code="1005", name="ITC IGST Ledger", classification=AccountClassification.ASSET, system_tag="ITC_IGST"),
            Account(code="2001", name="Sundry Creditors", classification=AccountClassification.LIABILITY, system_tag="CREDITORS"),
            Account(code="2002", name="Output CGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_CGST"),
            Account(code="2003", name="Output SGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_SGST"),
            Account(code="2004", name="Output IGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_IGST"),
            Account(code="2005", name="TDS Payable", classification=AccountClassification.LIABILITY, system_tag="TDS_PAYABLE"),
            Account(code="4001", name="Revenue / Sales", classification=AccountClassification.REVENUE, system_tag="SALES_REV"),
            Account(code="5001", name="Operating Expenses / Purchases", classification=AccountClassification.EXPENSE, system_tag="EXPENSE_GEN"),
        ]
        session.add_all(seed_accounts)
        await session.commit()
    yield

class CreateInvoicePayload(BaseModel):
    invoice_number: str = Field(..., max_length=16)  # Strict statutory 16-character limit
    invoice_type: str = "OUTWARD_TAX_INVOICE"  # OUTWARD_TAX_INVOICE or INWARD_PURCHASE
    party_name: str
    party_gstin: str
    origin_state: str = "27"
    place_of_supply: str = "27"
    taxable_value: float
    gst_rate: float = 18.0
    tds_section: str = "NONE"

@app.get("/api/reset-db")
async def reset_db():
    async with engine.begin() as conn:
        from sqlalchemy import text
        await conn.execute(text("DROP TABLE IF EXISTS journal_lines, journal_transactions, invoices CASCADE;"))
        await conn.execute(text("DROP TYPE IF EXISTS invoicetype, tdsegment, reconciliationstatus CASCADE;"))
        await conn.run_sync(Base.metadata.create_all)
    return {"status": "Database schema recreated successfully with irn_hash and reconciliation columns"}


@app.post("/api/invoices")
async def create_invoice(payload: CreateInvoicePayload, db: AsyncSession = Depends(get_db)):
    try:
        taxable = Decimal(str(payload.taxable_value))
        rate = Decimal(str(payload.gst_rate))
        total_tax = (taxable * (rate / Decimal("100.00"))).quantize(Decimal("0.01"))
        
        if payload.origin_state == payload.place_of_supply:
            cgst = (total_tax / Decimal("2.00")).quantize(Decimal("0.01"))
            sgst = total_tax - cgst
            igst = Decimal("0.00")
        else:
            cgst, sgst, igst = Decimal("0.00"), Decimal("0.00"), total_tax

        tds_rates = {"NONE": Decimal("0.00"), "194C": Decimal("2.00"), "194J": Decimal("10.00"), "194I": Decimal("10.00")}
        tds_rate = tds_rates.get(payload.tds_section, Decimal("0.00"))
        tds_amt = (taxable * (tds_rate / Decimal("100.00"))).quantize(Decimal("0.01"))
        gross = taxable + total_tax
        net = gross - tds_amt

        # Generate 64-character GST E-Invoice IRN Hash
        seed = f"{payload.party_gstin}:{payload.invoice_number}:{date.today().year}:{payload.taxable_value}"
        irn_hash = hashlib.sha256(seed.encode()).hexdigest()

        is_outward = payload.invoice_type == "OUTWARD_TAX_INVOICE"
        inv = Invoice(
            invoice_number=payload.invoice_number,
            irn_hash=irn_hash,
            invoice_type=InvoiceType(payload.invoice_type),
            party_name=payload.party_name,
            party_gstin=payload.party_gstin,
            origin_state=payload.origin_state,
            place_of_supply=payload.place_of_supply,
            taxable_value=taxable,
            cgst=cgst,
            sgst=sgst,
            igst=igst,
            tds_section=TDSSegment(payload.tds_section),
            tds_rate=tds_rate,
            tds_deducted=tds_amt,
            total_amount=gross,
            net_receivable_payable=net,
            reconciled_status=ReconciliationStatus.ITC_AVAILABLE if not is_outward else ReconciliationStatus.MATCHED
        )
        db.add(inv)
        await db.flush()

        accounts = await db.execute(select(Account))
        acc_map = {acc.system_tag: acc.id for acc in accounts.scalars().all() if acc.system_tag}

        tx = JournalTransaction(
            voucher_number=f"JV-{inv.invoice_number}",
            narration=f"Automated Entry: {inv.invoice_type.value} - {inv.party_name}",
            transaction_date=inv.invoice_date
        )
        db.add(tx)
        await db.flush()

        if is_outward:
            # Debit Debtors, Debit TDS Receivable, Credit Sales, Credit Output GST
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["DEBTORS"], debit=net, credit=Decimal("0.00")))
            if tds_amt > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["TDS_RECEIVABLE"], debit=tds_amt, credit=Decimal("0.00")))
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["SALES_REV"], debit=Decimal("0.00"), credit=taxable))
            if cgst > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_CGST"], debit=Decimal("0.00"), credit=cgst))
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_SGST"], debit=Decimal("0.00"), credit=sgst))
            if igst > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_IGST"], debit=Decimal("0.00"), credit=igst))
        else:
            # Debit Expenses, Debit ITC GST, Credit Creditors, Credit TDS Payable
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["EXPENSE_GEN"], debit=taxable, credit=Decimal("0.00")))
            if cgst > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["ITC_CGST"], debit=cgst, credit=Decimal("0.00")))
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["ITC_SGST"], debit=sgst, credit=Decimal("0.00")))
            if igst > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["ITC_IGST"], debit=igst, credit=Decimal("0.00")))
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["CREDITORS"], debit=Decimal("0.00"), credit=net))
            if tds_amt > 0:
                db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["TDS_PAYABLE"], debit=Decimal("0.00"), credit=tds_amt))

        await db.commit()
        return {"status": "success", "invoice_number": inv.invoice_number, "irn": inv.irn_hash}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice))
    invs = res.scalars().all()
    sales = [i for i in invs if i.invoice_type == InvoiceType.OUTWARD_TAX_INVOICE]
    purchases = [i for i in invs if i.invoice_type == InvoiceType.INWARD_PURCHASE]

    total_sales_tax = sum(i.cgst + i.sgst + i.igst for i in sales)
    total_itc_tax = sum(i.cgst + i.sgst + i.igst for i in purchases)

    return {
        "metrics": {
            "total_revenue": float(sum(i.taxable_value for i in sales)),
            "total_expenses": float(sum(i.taxable_value for i in purchases)),
            "gst_output_liability": float(total_sales_tax),
            "itc_available": float(total_itc_tax),
            "net_gst_payable": float(max(Decimal("0.00"), total_sales_tax - total_itc_tax)),
            "tds_receivable": float(sum(i.tds_deducted for i in sales)),
            "tds_payable": float(sum(i.tds_deducted for i in purchases)),
            "net_receivables": float(sum(i.net_receivable_payable for i in sales))
        },
        "recent_invoices": [
            {
                "id": str(i.id),
                "invoice_number": i.invoice_number,
                "irn": i.irn_hash[:12] + "..." if i.irn_hash else "N/A",
                "party": i.party_name,
                "type": i.invoice_type.value,
                "amount": float(i.total_amount),
                "status": i.reconciled_status.value
            } for i in invs[-8:]
        ]
    }

# 1. Sales Register & Purchase Register
@app.get("/api/registers/sales")
async def get_sales_register(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice).where(Invoice.invoice_type == InvoiceType.OUTWARD_TAX_INVOICE))
    return res.scalars().all()

@app.get("/api/registers/purchases")
async def get_purchase_register(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice).where(Invoice.invoice_type == InvoiceType.INWARD_PURCHASE))
    return res.scalars().all()

# 2. GSTR-1 (Outward Supplies JSON)
@app.get("/api/reports/gstr1")
async def get_gstr1(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice).where(Invoice.invoice_type == InvoiceType.OUTWARD_TAX_INVOICE))
    invs = res.scalars().all()
    return {
        "gstin": "27AAACT2727Q1ZW",
        "fp": datetime.now().strftime("%m%Y"),
        "b2b": [
            {
                "ctin": i.party_gstin,
                "cfs": "Y",
                "inv": [{
                    "inum": i.invoice_number,
                    "idt": str(i.invoice_date),
                    "val": float(i.total_amount),
                    "pos": i.place_of_supply,
                    "rchrg": "N",
                    "irn": i.irn_hash,
                    "itms": [{
                        "num": 1,
                        "itm_det": {
                            "txval": float(i.taxable_value),
                            "rt": float((i.cgst + i.sgst + i.igst) / i.taxable_value * 100) if i.taxable_value > 0 else 0.0,
                            "iamt": float(i.igst),
                            "camt": float(i.cgst),
                            "samt": float(i.sgst),
                            "csamt": 0.0
                        }
                    }]
                }]
            } for i in invs
        ]
    }

# 3. GSTR-2B (Auto-Drafted Inward ITC Statement)
@app.get("/api/reports/gstr2b")
async def get_gstr2b(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice).where(Invoice.invoice_type == InvoiceType.INWARD_PURCHASE))
    invs = res.scalars().all()
    return {
        "gstin": "27AAACT2727Q1ZW",
        "generated_on": datetime.now().strftime("%d-%m-%Y"),
        "data": {
            "itc_summary": {
                "itc_avail": {
                    "cgst": float(sum(i.cgst for i in invs)),
                    "sgst": float(sum(i.sgst for i in invs)),
                    "igst": float(sum(i.igst for i in invs)),
                    "total": float(sum(i.cgst + i.sgst + i.igst for i in invs))
                }
            },
            "b2b": [
                {
                    "supplier_gstin": i.party_gstin,
                    "supplier_name": i.party_name,
                    "inv_no": i.invoice_number,
                    "inv_date": str(i.invoice_date),
                    "inv_val": float(i.total_amount),
                    "taxable": float(i.taxable_value),
                    "cgst": float(i.cgst),
                    "sgst": float(i.sgst),
                    "igst": float(i.igst),
                    "itc_eligibility": "Y"
                } for i in invs
            ]
        }
    }

# 4. GSTR-3B (Automated Summary Return with Statutory Cross-Utilization)
@app.get("/api/reports/gstr3b")
async def get_gstr3b(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice))
    invs = res.scalars().all()

    sales = [i for i in invs if i.invoice_type == InvoiceType.OUTWARD_TAX_INVOICE]
    purchases = [i for i in invs if i.invoice_type == InvoiceType.INWARD_PURCHASE]

    outward_cgst = sum(i.cgst for i in sales)
    outward_sgst = sum(i.sgst for i in sales)
    outward_igst = sum(i.igst for i in sales)

    itc_cgst = sum(i.cgst for i in purchases)
    itc_sgst = sum(i.sgst for i in purchases)
    itc_igst = sum(i.igst for i in purchases)

    # Statutory Offset Rules:
    # 1. IGST credit first offsets IGST liability, then CGST/SGST.
    # 2. CGST credit cannot offset SGST liability, and SGST cannot offset CGST.
    rem_igst_itc = itc_igst
    cash_igst = max(Decimal("0.00"), outward_igst - rem_igst_itc)
    rem_igst_itc = max(Decimal("0.00"), rem_igst_itc - outward_igst)

    cash_cgst = max(Decimal("0.00"), outward_cgst - itc_cgst - (rem_igst_itc / Decimal("2.00")))
    cash_sgst = max(Decimal("0.00"), outward_sgst - itc_sgst - (rem_igst_itc / Decimal("2.00")))

    return {
        "gstin": "27AAACT2727Q1ZW",
        "tax_period": datetime.now().strftime("%m%Y"),
        "table_3_1_outward_supplies": {
            "taxable_value": float(sum(i.taxable_value for i in sales)),
            "igst": float(outward_igst),
            "cgst": float(outward_cgst),
            "sgst": float(outward_sgst),
            "cess": 0.0
        },
        "table_4_eligible_itc": {
            "igst": float(itc_igst),
            "cgst": float(itc_cgst),
            "sgst": float(itc_sgst)
        },
        "table_6_1_payment_of_tax": {
            "net_cash_payable": {
                "igst": float(cash_igst),
                "cgst": float(cash_cgst),
                "sgst": float(cash_sgst),
                "total_cash": float(cash_igst + cash_cgst + cash_sgst)
            }
        }
    }
