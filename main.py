import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
import enum
from typing import List, Optional
import uuid

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
    TAX_INVOICE = "TAX_INVOICE"
    PURCHASE_INVOICE = "PURCHASE_INVOICE"

class TDSSegment(str, enum.Enum):
    NONE = "NONE"
    SEC_194C = "194C"
    SEC_194J = "194J"
    SEC_194I = "194I"

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
    invoice_number: Mapped[str] = mapped_column(String(16), unique=True, index=True)
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
    invoice_date: Mapped[date] = mapped_column(Date, default=date.today)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(Account.id)))
        if result.scalar() == 0:
            seed_accounts = [
                Account(code="1001", name="Sundry Debtors", classification=AccountClassification.ASSET, system_tag="DEBTORS"),
                Account(code="1002", name="TDS Receivable", classification=AccountClassification.ASSET, system_tag="TDS_RECEIVABLE"),
                Account(code="2001", name="Sundry Creditors", classification=AccountClassification.LIABILITY, system_tag="CREDITORS"),
                Account(code="2002", name="Output CGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_CGST"),
                Account(code="2003", name="Output SGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_SGST"),
                Account(code="2004", name="Output IGST Payable", classification=AccountClassification.LIABILITY, system_tag="OUTPUT_IGST"),
                Account(code="2005", name="TDS Payable", classification=AccountClassification.LIABILITY, system_tag="TDS_PAYABLE"),
                Account(code="4001", name="Revenue / Sales", classification=AccountClassification.REVENUE, system_tag="SALES_REV"),
                Account(code="5001", name="Operating Expenses", classification=AccountClassification.EXPENSE, system_tag="EXPENSE_GEN"),
            ]
            session.add_all(seed_accounts)
            await session.commit()
    yield

app = FastAPI(title="AutoCA API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

class CreateInvoicePayload(BaseModel):
    invoice_number: str
    party_name: str
    party_gstin: str
    origin_state: str = "27"
    place_of_supply: str = "27"
    taxable_value: float
    gst_rate: float = 18.0
    tds_section: str = "NONE"

@app.get("/")
def read_root():
    return {"status": "AutoCA Engine Online"}

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

        inv = Invoice(
            invoice_number=payload.invoice_number,
            invoice_type=InvoiceType.TAX_INVOICE,
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
            net_receivable_payable=net
        )
        db.add(inv)
        await db.flush()

        accounts = await db.execute(select(Account))
        acc_map = {acc.system_tag: acc.id for acc in accounts.scalars().all() if acc.system_tag}

        tx = JournalTransaction(
            voucher_number=f"JV-{inv.invoice_number}",
            narration=f"Auto Voucher: {inv.invoice_number} - {inv.party_name}",
            transaction_date=inv.invoice_date
        )
        db.add(tx)
        await db.flush()

        db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["DEBTORS"], debit=net, credit=Decimal("0.00")))
        if tds_amt > Decimal("0.00"):
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["TDS_RECEIVABLE"], debit=tds_amt, credit=Decimal("0.00")))
        db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["SALES_REV"], debit=Decimal("0.00"), credit=taxable))

        if cgst > 0:
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_CGST"], debit=Decimal("0.00"), credit=cgst))
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_SGST"], debit=Decimal("0.00"), credit=sgst))
        if igst > 0:
            db.add(JournalLine(transaction_id=tx.id, account_id=acc_map["OUTPUT_IGST"], debit=Decimal("0.00"), credit=igst))

        await db.commit()
        return {"status": "success", "invoice_number": inv.invoice_number}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice))
    invs = res.scalars().all()
    return {
        "metrics": {
            "total_revenue": float(sum(i.taxable_value for i in invs)),
            "gst_collected": float(sum(i.cgst + i.sgst + i.igst for i in invs)),
            "tds_credit": float(sum(i.tds_deducted for i in invs)),
            "outstanding_receivables": float(sum(i.net_receivable_payable for i in invs))
        },
        "recent_invoices": [
            {"id": str(i.id), "invoice_number": i.invoice_number, "party": i.party_name, "amount": float(i.total_amount)}
            for i in invs[-5:]
        ]
    }

@app.get("/api/reports/gstr1")
async def get_gstr1(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Invoice))
    invs = res.scalars().all()
    return {
        "b2b": [
            {
                "ctin": i.party_gstin,
                "inum": i.invoice_number,
                "idt": str(i.invoice_date),
                "val": float(i.total_amount),
                "pos": i.place_of_supply,
                "txval": float(i.taxable_value),
                "iamt": float(i.igst),
                "camt": float(i.cgst),
                "samt": float(i.sgst)
            } for i in invs
        ]
    }
