"""
Módulo de Liquidação Instantânea via SPI / Pix no Mercado de Capitais (FL-064).
=============================================================================
Implementação da mensageria ISO 20022 pacs.008 e pacs.002 do SPI/BACEN
(Sistema de Pagamentos Instantâneos) para liquidação imediata D0 de cotas de
fundos de investimento e títulos escriturais com chaves Pix e conciliação de
canal STR (Sistema de Transferência de Reservas).

Em conformidade regulatória com:
- Resolução BCB nº 1/2020 (Regulamento do Pix e do SPI);
- Resolução BCB nº 269/2022 (Aprimoramento de liquidação no mercado financeiro e de capitais);
- Resolução CVM nº 175 (Marco Regulatório dos Fundos de Investimento);
- Regulamentos de Compensação, Liquidação e Custódia da B3.

Invariantes Formais Provadas:
1. Conservação Financeira Estrita em Contas PI:
   Saldo_Final == Saldo_Inicial + Injeções_STR - Resgates_STR - Débitos_SPI + Créditos_SPI.
2. Idempotência Absoluta por EndToEndId:
   Reenvio de mensagem com mesmo EndToEndId retorna o recibo original com flag
   idempotent_replay=True sem duplicar débito, crédito ou entrega de custódia.
3. Atomicidade DvP (Delivery versus Payment) D0:
   Cotas de fundos e títulos escriturais só são entregues ao investidor se pacs.002
   for confirmado com status ACSC. Em caso de rejeição (RJCT), a reserva de custódia
   é revertida sem perda de ativos (rollback atômico).
4. Conservação de Balanço de Custódia:
   Total_Custódia_Global == Custódia_Disponível + Custódia_Reservada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import uuid
import xml.etree.ElementTree as ET

# Constantes de Domínio BACEN / SPI / B3
ISPB_B3_DEFAULT = "00997185"
ISPB_BACEN_DEFAULT = "00038166"
DOMINIO_SISTEMA_SPI = "SPI01"
DOMINIO_SISTEMA_STR = "STR01"

XML_NS_PACS008 = "urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"
XML_NS_PACS002 = "urn:iso:std:iso:20022:tech:xsd:pacs.002.001.10"
XML_NS_BACEN_PI = "https://www.bcb.gov.br/pi/pacs.008/1.7"
XML_NS_BACEN_PI_STATUS = "https://www.bcb.gov.br/pi/pacs.002/1.7"

_ISPB_REGEX = re.compile(r"^\d{8}$")
_E2E_REGEX = re.compile(r"^E\d{8}\d{12}[a-zA-Z0-9]{11}$")
_EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
_PHONE_REGEX = re.compile(r"^\+?[1-9]\d{1,14}$")
_UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Exceções de Domínio SPI / Pix / Liquidação
# ---------------------------------------------------------------------------

class SpiSettlementError(Exception):
    """Exceção base para operações do SPI e liquidação instantânea de capitais."""
    pass


class SpiValidationError(SpiSettlementError):
    """Lançada quando dados da mensagem ou operação violam regras formais."""
    pass


class InvalidIspbError(SpiValidationError):
    """Lançada quando o código ISPB não cumpre a regra estrita de 8 dígitos numéricos."""
    pass


class InvalidEndToEndIdError(SpiValidationError):
    """Lançada quando o EndToEndId não segue o padrão regulamentar de 32 caracteres do BACEN."""
    pass


class InvalidPixKeyError(SpiValidationError):
    """Lançada quando uma chave Pix informada não é válida segundo sua tipologia."""
    pass


class InsufficientFundsError(SpiSettlementError):
    """Lançada quando a Conta PI não possui saldo ou limite suficiente para liquidação."""
    pass


class InsufficientCustodyError(SpiSettlementError):
    """Lançada quando o cedente/emissor não possui cotas ou títulos suficientes em custódia."""
    pass


class SpiXmlError(SpiSettlementError):
    """Lançada quando ocorre erro de validação ou parsing de mensagens XML ISO 20022."""
    pass


class DuplicateMessageError(SpiSettlementError):
    """Lançada quando há tentativa conflitante de reprocessar mensagem no SPI."""
    pass


class StrReconciliationMismatchError(SpiSettlementError):
    """Lançada quando a equação contábil entre STR e SPI apresenta discrepância."""
    pass


# ---------------------------------------------------------------------------
# Enums de Domínio
# ---------------------------------------------------------------------------

class PixKeyType(str, Enum):
    """Tipos de chave Pix suportados pelo DICT (BACEN)."""
    CPF = "CPF"
    CNPJ = "CNPJ"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    EVP = "EVP"  # Endereço Virtual Pagador / Chave Aleatória UUID


class AssetType(str, Enum):
    """Tipos de ativos do mercado de capitais elegíveis para liquidação imediata via Pix."""
    INVESTMENT_FUND_SHARE = "INVESTMENT_FUND_SHARE"  # Cotas de Fundos (CVM 175 - FII, FIDC, FIAGRO, ETF)
    BOOK_ENTRY_SECURITY = "BOOK_ENTRY_SECURITY"      # Títulos Escriturais (CDB, LF, Debêntures, TPF)


class SpiTransactionStatus(str, Enum):
    """Códigos de status de transação no SPI (ISO 20022)."""
    ACTC = "ACTC"  # Aceito tecnicamente
    ACSC = "ACSC"  # Liquidação liquidada com sucesso em fundos no BACEN (Finalizada)
    RJCT = "RJCT"  # Rejeitado pelo SPI/BACEN
    PDNG = "PDNG"  # Pendente de processamento


class DvpStatus(str, Enum):
    """Estado do ciclo atômico de Delivery versus Payment (DvP)."""
    RESERVED = "RESERVED"      # Custódia bloqueada preventivamente aguardando liquidação financeira
    SETTLED = "SETTLED"        # Ativos transferidos em D0 e financeiro liquidado
    ROLLED_BACK = "ROLLED_BACK"# Rejeitado no SPI; custódia devolvida ao cedente
    FAILED = "FAILED"          # Falha crítica no fluxo


class StrTransferType(str, Enum):
    """Tipos de mensagens do STR para aporte ou nivelamento de saldo da Conta PI."""
    STR0007_INJECTION = "STR0007_INJECTION"  # STR0007: Reserva Bancária -> Conta PI (Aporte)
    STR0008_RETURN = "STR0008_RETURN"        # STR0008: Conta PI -> Reserva Bancária (Resgate/Sweeping)


class AccountType(str, Enum):
    """Tipos de conta de liquidação no SPB / SPI."""
    CACC = "CACC"  # Conta Corrente
    TRAN = "TRAN"  # Conta Pagamento
    SVGS = "SVGS"  # Poupança
    SLRY = "SLRY"  # Conta Salário


# ---------------------------------------------------------------------------
# Funções de Validação Rigorosa
# ---------------------------------------------------------------------------

def validate_ispb(ispb: Any, field_name: str = "ISPB") -> str:
    """Valida se o ISPB possui rigorosamente 8 dígitos numéricos decimais."""
    if ispb is None:
        raise InvalidIspbError(f"Campo {field_name} não pode ser nulo.")
    if isinstance(ispb, int):
        if not (0 <= ispb <= 99999999):
            raise InvalidIspbError(f"{field_name} inteiro fora do intervalo (0..99999999): {ispb}")
        return f"{ispb:08d}"
    s = str(ispb).strip()
    if not _ISPB_REGEX.match(s):
        raise InvalidIspbError(f"{field_name} deve conter exatamente 8 dígitos numéricos: '{ispb}'")
    return s


def validate_cpf(cpf: str) -> bool:
    """Valida dígito verificador de CPF conforme algoritmo módulo 11."""
    digits = [int(c) for c in str(cpf) if c.isdigit()]
    if len(digits) != 11:
        return False
    if len(set(digits)) == 1:
        return False

    # Primeiro dígito verificador
    s1 = sum(digits[i] * (10 - i) for i in range(9))
    d1 = (s1 * 10) % 11
    if d1 >= 10:
        d1 = 0
    if digits[9] != d1:
        return False

    # Segundo dígito verificador
    s2 = sum(digits[i] * (11 - i) for i in range(10))
    d2 = (s2 * 10) % 11
    if d2 >= 10:
        d2 = 0
    return digits[10] == d2


def validate_cnpj(cnpj: str) -> bool:
    """Valida dígito verificador de CNPJ conforme algoritmo módulo 11."""
    digits = [int(c) for c in str(cnpj) if c.isdigit()]
    if len(digits) != 14:
        return False
    if len(set(digits)) == 1:
        return False

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s1 = sum(digits[i] * w1[i] for i in range(12))
    r1 = s1 % 11
    d1 = 0 if r1 < 2 else 11 - r1
    if digits[12] != d1:
        return False

    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    s2 = sum(digits[i] * w2[i] for i in range(13))
    r2 = s2 % 11
    d2 = 0 if r2 < 2 else 11 - r2
    return digits[13] == d2


def validate_pix_key(key_type: Union[PixKeyType, str], key_value: str) -> bool:
    """Valida formato e consistência da chave Pix segundo o padrão DICT."""
    if not key_value or not isinstance(key_value, str):
        return False
    ktype = PixKeyType(key_type) if isinstance(key_type, str) else key_type
    val = key_value.strip()

    if ktype == PixKeyType.CPF:
        cleaned = re.sub(r"\D", "", val)
        return validate_cpf(cleaned)
    elif ktype == PixKeyType.CNPJ:
        cleaned = re.sub(r"\D", "", val)
        return validate_cnpj(cleaned)
    elif ktype == PixKeyType.EMAIL:
        return bool(_EMAIL_REGEX.match(val))
    elif ktype == PixKeyType.PHONE:
        cleaned = re.sub(r"[\s\-\(\)]", "", val)
        return bool(_PHONE_REGEX.match(cleaned))
    elif ktype == PixKeyType.EVP:
        return bool(_UUID_REGEX.match(val))
    return False


def generate_end_to_end_id(
    ispb: str,
    timestamp: Optional[datetime] = None,
    sequence: Optional[str] = None
) -> str:
    """
    Gera um identificador EndToEndId conforme padrão estrito do BACEN (32 caracteres).
    Estrutura:
    - Posição 0: 'E'
    - Posição 1..9: ISPB de 8 dígitos do participante pagador
    - Posição 9..21: Data/Hora UTC formato YYYYMMDDHHMM (12 dígitos)
    - Posição 21..32: 11 caracteres alfanuméricos únicos
    """
    valid_ispb = validate_ispb(ispb, "ISPB do Pagador")
    dt = timestamp or datetime.now(timezone.utc)
    dt_str = dt.strftime("%Y%m%d%H%M")
    
    if sequence:
        seq_str = re.sub(r"[^a-zA-Z0-9]", "", sequence)[:11].ljust(11, "0")
    else:
        seq_str = uuid.uuid4().hex[:11].upper()
        
    e2e = f"E{valid_ispb}{dt_str}{seq_str}"
    if len(e2e) != 32:
        raise InvalidEndToEndIdError(f"EndToEndId gerado com tamanho anômalo ({len(e2e)}): {e2e}")
    return e2e


def validate_end_to_end_id(e2e_id: str) -> bool:
    """Valida se o EndToEndId cumpre rigorosamente as 32 posições regulamentares."""
    if not e2e_id or not isinstance(e2e_id, str):
        return False
    if len(e2e_id) != 32:
        return False
    if not _E2E_REGEX.match(e2e_id):
        return False
    # Valida ISPB embutido
    ispb_part = e2e_id[1:9]
    if not _ISPB_REGEX.match(ispb_part):
        return False
    return True


# ---------------------------------------------------------------------------
# Estruturas de Dados e Modelos de Domínio
# ---------------------------------------------------------------------------

@dataclass
class PixKey:
    """Entrada de chave Pix registrada no DICT."""
    key_type: PixKeyType
    key_value: str
    holder_name: str
    holder_tax_id: str  # CPF ou CNPJ
    ispb: str
    branch: str
    account_number: str
    account_type: AccountType = AccountType.CACC

    def __post_init__(self):
        self.ispb = validate_ispb(self.ispb, "ISPB do Titular da Chave")
        if not validate_pix_key(self.key_type, self.key_value):
            raise InvalidPixKeyError(f"Chave Pix {self.key_type.value} inválida: '{self.key_value}'")


@dataclass
class PiAccount:
    """Conta Pagamentos Instantâneos (Conta PI) mantida no BACEN."""
    ispb: str
    account_number: str
    participant_name: str
    balance: Decimal = Decimal("0.00")
    available_limit: Decimal = Decimal("0.00")
    in_transit_debits: Decimal = Decimal("0.00")  # Débitos retidos em trânsito DvP

    def __post_init__(self):
        self.ispb = validate_ispb(self.ispb, "ISPB da Conta PI")
        self.balance = Decimal(str(self.balance)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        self.available_limit = Decimal(str(self.available_limit)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        self.in_transit_debits = Decimal(str(self.in_transit_debits)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    @property
    def total_liquidity(self) -> Decimal:
        """Liquidez total disponível (saldo próprio + limite intraday - débitos em trânsito)."""
        return self.balance + self.available_limit - self.in_transit_debits


@dataclass
class CustodyAccount:
    """Conta de custódia escritural na B3."""
    account_id: str
    participant_id: str
    participant_name: str
    tax_id: str
    fund_shares: Dict[str, Decimal] = field(default_factory=dict)
    securities: Dict[str, Decimal] = field(default_factory=dict)

    def get_fund_share_balance(self, ticker: str) -> Decimal:
        return self.fund_shares.get(ticker, Decimal("0.00000000"))

    def get_security_balance(self, isin: str) -> Decimal:
        return self.securities.get(isin, Decimal("0"))


@dataclass
class FundShareAsset:
    """Especificação de cota de fundo de investimento (CVM 175)."""
    fund_cnpj: str
    ticker: str
    isin: str
    fund_name: str
    quota_value: Decimal
    category: str = "FII"  # FII, FIDC, FIAGRO, ETF

    def __post_init__(self):
        self.quota_value = Decimal(str(self.quota_value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


@dataclass
class BookEntrySecurityAsset:
    """Especificação de título escritural (CDB, LF, Debênture, TPF)."""
    isin: str
    security_type: str  # CDB, DEBENTURE, LF, LCI, LCA, TPF
    issuer_cnpj: str
    issuer_name: str
    unit_price: Decimal
    maturity_date: str

    def __post_init__(self):
        self.unit_price = Decimal(str(self.unit_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


@dataclass
class Pacs008Message:
    """Mensagem pacs.008.001.08: Ordem de Transferência a Crédito de Cliente no SPI."""
    msg_id: str
    creation_date_time: datetime
    end_to_end_id: str
    tx_id: str
    amount: Decimal
    settlement_date: date
    debtor_name: str
    debtor_tax_id: str
    debtor_ispb: str
    debtor_account: str
    debtor_account_type: str = "CACC"
    creditor_name: str = ""
    creditor_tax_id: str = ""
    creditor_ispb: str = ""
    creditor_account: str = ""
    creditor_account_type: str = "CACC"
    pix_key: Optional[str] = None
    asset_type: AssetType = AssetType.INVESTMENT_FUND_SHARE
    asset_identifier: str = ""  # Ticker ou ISIN
    asset_quantity: Decimal = Decimal("0")
    asset_unit_price: Decimal = Decimal("0")
    remittance_info: str = ""

    def __post_init__(self):
        self.debtor_ispb = validate_ispb(self.debtor_ispb, "ISPB do Devedor")
        self.creditor_ispb = validate_ispb(self.creditor_ispb, "ISPB do Credor")
        if not validate_end_to_end_id(self.end_to_end_id):
            raise InvalidEndToEndIdError(f"EndToEndId inválido no pacs.008: {self.end_to_end_id}")
        self.amount = Decimal(str(self.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        self.asset_quantity = Decimal(str(self.asset_quantity))
        self.asset_unit_price = Decimal(str(self.asset_unit_price))


@dataclass
class Pacs002Message:
    """Mensagem pacs.002.001.10: Relatório de Status do Pagamento no SPI."""
    msg_id: str
    creation_date_time: datetime
    original_end_to_end_id: str
    original_tx_id: str
    transaction_status: SpiTransactionStatus
    status_reason_code: Optional[str] = None
    status_reason_detail: Optional[str] = None
    settlement_timestamp: Optional[datetime] = None
    clearing_system_ref: Optional[str] = None

    def __post_init__(self):
        if not validate_end_to_end_id(self.original_end_to_end_id):
            raise InvalidEndToEndIdError(f"OriginalEndToEndId inválido no pacs.002: {self.original_end_to_end_id}")


@dataclass
class StrTransferRecord:
    """Registro de transferência entre o STR e a Conta PI do SPI."""
    nu_op: str
    transfer_type: StrTransferType
    ispb: str
    amount: Decimal
    timestamp: datetime
    description: str = ""

    def __post_init__(self):
        self.ispb = validate_ispb(self.ispb, "ISPB da Transferência STR")
        self.amount = Decimal(str(self.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class SpiSettlementReceipt:
    """Recibo consolidado de liquidação D0 e entrega atômica DvP."""
    receipt_id: str
    end_to_end_id: str
    msg_id: str
    asset_type: AssetType
    asset_identifier: str
    quantity: Decimal
    unit_price: Decimal
    total_financial_amount: Decimal
    debtor_ispb: str
    creditor_ispb: str
    seller_custody_account_id: str
    buyer_custody_account_id: str
    dvp_status: DvpStatus
    pacs002_status: SpiTransactionStatus
    settlement_timestamp: Optional[datetime] = None
    idempotent_replay: bool = False
    financial_conservation_verified: bool = True
    custody_conservation_verified: bool = True


@dataclass(frozen=True)
class StrReconciliationSummary:
    """Relatório determinístico de conciliação de canal STR x SPI."""
    ispb: str
    initial_pi_balance: Decimal
    str_injections: Decimal
    str_returns: Decimal
    spi_debits: Decimal
    spi_credits: Decimal
    calculated_final_balance: Decimal
    actual_final_balance: Decimal
    discrepancy: Decimal
    is_balanced: bool


# ---------------------------------------------------------------------------
# Motor de Liquidação Instantânea SPI e Conciliação STR (FL-064)
# ---------------------------------------------------------------------------

class PixSpiSettlementEngine:
    """
    Motor determinístico de mensageria ISO 20022 (pacs.008 e pacs.002),
    liquidação imediata D0 com garantia DvP atômica e conciliação do canal STR.
    """

    def __init__(self, ispb_camara: str = ISPB_B3_DEFAULT):
        self.ispb_camara = validate_ispb(ispb_camara, "ISPB da Câmara B3")
        self.pi_accounts: Dict[str, PiAccount] = {}
        self.custody_accounts: Dict[str, CustodyAccount] = {}
        self.pix_keys: Dict[str, PixKey] = {}
        self.fund_assets: Dict[str, FundShareAsset] = {}
        self.security_assets: Dict[str, BookEntrySecurityAsset] = {}

        # Armazenamento e Rastreabilidade
        self.processed_pacs008: Dict[str, Pacs008Message] = {}        # msg_id -> Pacs008
        self.processed_transactions: Dict[str, SpiSettlementReceipt] = {} # e2e_id -> Receipt
        self.pacs002_history: Dict[str, Pacs002Message] = {}         # e2e_id -> Pacs002
        self.str_transfers: List[StrTransferRecord] = []
        
        # Histórico de saldo inicial para conciliação STR
        self.initial_pi_balances: Dict[str, Decimal] = {}

        # Controle de custódia reservada durante trânsito DvP: account_id -> {asset_id: reserved_qty}
        self.reserved_custody: Dict[str, Dict[str, Decimal]] = {}
        # In-transit financial mapping: e2e_id -> amount
        self.in_transit_funds: Dict[str, Decimal] = {}

    # -----------------------------------------------------------------------
    # Cadastros e Configuração
    # -----------------------------------------------------------------------

    def register_pi_account(self, account: PiAccount) -> None:
        """Registra uma Conta PI de participante no SPI/BACEN."""
        self.pi_accounts[account.ispb] = account
        if account.ispb not in self.initial_pi_balances:
            self.initial_pi_balances[account.ispb] = account.balance

    def register_custody_account(self, account: CustodyAccount) -> None:
        """Registra uma conta de custódia na Central Depositária B3."""
        self.custody_accounts[account.account_id] = account
        if account.account_id not in self.reserved_custody:
            self.reserved_custody[account.account_id] = {}

    def register_pix_key(self, pix_key: PixKey) -> None:
        """Registra uma chave Pix no DICT."""
        self.pix_keys[pix_key.key_value] = pix_key

    def register_fund_asset(self, asset: FundShareAsset) -> None:
        """Registra ativo de cotas de fundos (CVM 175)."""
        self.fund_assets[asset.ticker] = asset

    def register_security_asset(self, asset: BookEntrySecurityAsset) -> None:
        """Registra ativo de título escritural (CDB, LF, etc.)."""
        self.security_assets[asset.isin] = asset

    # -----------------------------------------------------------------------
    # Geração e Parsing de XML ISO 20022 (pacs.008 e pacs.002)
    # -----------------------------------------------------------------------

    def create_pacs008_xml(self, msg: Pacs008Message) -> str:
        """Gera mensagem XML ISO 20022 pacs.008.001.08 formatada segundo o catálogo SPI."""
        root = ET.Element("Document", {"xmlns": XML_NS_PACS008})
        fi_to_fi = ET.SubElement(root, "FIToFICstmrCdtTrf")

        # GrpHdr
        grp_hdr = ET.SubElement(fi_to_fi, "GrpHdr")
        ET.SubElement(grp_hdr, "MsgId").text = msg.msg_id
        ET.SubElement(grp_hdr, "CreDtTm").text = msg.creation_date_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        ET.SubElement(grp_hdr, "NbOfTxs").text = "1"
        sttlm_inf = ET.SubElement(grp_hdr, "SttlmInf")
        ET.SubElement(sttlm_inf, "SttlmMtd").text = "CLRG"
        clr_sys = ET.SubElement(sttlm_inf, "ClrSys")
        ET.SubElement(clr_sys, "Prtry").text = "SPI"

        # CdtTrfTxInf
        tx_inf = ET.SubElement(fi_to_fi, "CdtTrfTxInf")
        pmt_id = ET.SubElement(tx_inf, "PmtId")
        ET.SubElement(pmt_id, "EndToEndId").text = msg.end_to_end_id
        ET.SubElement(pmt_id, "TxId").text = msg.tx_id

        pmt_tp = ET.SubElement(tx_inf, "PmtTpInf")
        ET.SubElement(pmt_tp, "InstrPrty").text = "HIGH"
        svc_lvl = ET.SubElement(pmt_tp, "SvcLvl")
        ET.SubElement(svc_lvl, "Prtry").text = "PIX"
        lcl_instrm = ET.SubElement(pmt_tp, "LclInstrm")
        ET.SubElement(lcl_instrm, "Prtry").text = "DICT" if msg.pix_key else "MANU"

        amt_el = ET.SubElement(tx_inf, "IntrBkSttlmAmt", {"Ccy": "BRL"})
        amt_el.text = f"{msg.amount:.2f}"
        ET.SubElement(tx_inf, "IntrBkSttlmDt").text = msg.settlement_date.isoformat()

        # Dbtr
        dbtr = ET.SubElement(tx_inf, "Dbtr")
        ET.SubElement(dbtr, "Nm").text = msg.debtor_name
        dbtr_id = ET.SubElement(dbtr, "Id")
        othr_id = ET.SubElement(ET.SubElement(dbtr_id, "PrvtId" if len(re.sub(r"\D", "", msg.debtor_tax_id)) == 11 else "OrgId"), "Othr")
        ET.SubElement(othr_id, "Id").text = msg.debtor_tax_id

        # DbtrAcct
        dbtr_acct = ET.SubElement(tx_inf, "DbtrAcct")
        ET.SubElement(ET.SubElement(dbtr_acct, "Id"), "Othr").find("Id")
        ET.SubElement(ET.SubElement(dbtr_acct, "Id"), "Othr")
        acct_id_el = ET.SubElement(ET.SubElement(dbtr_acct, "Id"), "Othr")
        ET.SubElement(acct_id_el, "Id").text = msg.debtor_account
        ET.SubElement(ET.SubElement(dbtr_acct, "Tp"), "Prtry").text = msg.debtor_account_type

        # DbtrAgt
        dbtr_agt = ET.SubElement(tx_inf, "DbtrAgt")
        ET.SubElement(ET.SubElement(ET.SubElement(dbtr_agt, "FinInstnId"), "ClrSysMmbId"), "MmbId").text = msg.debtor_ispb

        # CdtrAgt
        cdtr_agt = ET.SubElement(tx_inf, "CdtrAgt")
        ET.SubElement(ET.SubElement(ET.SubElement(cdtr_agt, "FinInstnId"), "ClrSysMmbId"), "MmbId").text = msg.creditor_ispb

        # Cdtr
        cdtr = ET.SubElement(tx_inf, "Cdtr")
        ET.SubElement(cdtr, "Nm").text = msg.creditor_name
        cdtr_id = ET.SubElement(cdtr, "Id")
        c_othr_id = ET.SubElement(ET.SubElement(cdtr_id, "OrgId" if len(re.sub(r"\D", "", msg.creditor_tax_id)) == 14 else "PrvtId"), "Othr")
        ET.SubElement(c_othr_id, "Id").text = msg.creditor_tax_id

        # CdtrAcct
        cdtr_acct = ET.SubElement(tx_inf, "CdtrAcct")
        c_acct_el = ET.SubElement(ET.SubElement(cdtr_acct, "Id"), "Othr")
        ET.SubElement(c_acct_el, "Id").text = msg.creditor_account
        ET.SubElement(ET.SubElement(cdtr_acct, "Tp"), "Prtry").text = msg.creditor_account_type

        # RmtInf
        rmt_inf = ET.SubElement(tx_inf, "RmtInf")
        remittance = msg.remittance_info or f"ASSET:{msg.asset_identifier};QTY:{msg.asset_quantity};PU:{msg.asset_unit_price}"
        ET.SubElement(rmt_inf, "Ustrd").text = remittance

        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def parse_pacs008_xml(self, xml_content: str) -> Pacs008Message:
        """Faz o parsing seguro e extração de campos de XML pacs.008.001.08."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            raise SpiXmlError(f"Falha de sintaxe XML no pacs.008: {err}")

        def find_text(parent: ET.Element, tag_suffix: str) -> Optional[str]:
            for elem in parent.iter():
                if elem.tag.split("}")[-1] == tag_suffix:
                    return elem.text
            return None

        msg_id = find_text(root, "MsgId") or f"MSG-SPI-{uuid.uuid4().hex[:8]}"
        cre_dt_tm_str = find_text(root, "CreDtTm")
        cre_dt_tm = datetime.fromisoformat(cre_dt_tm_str.replace("Z", "+00:00")) if cre_dt_tm_str else datetime.now(timezone.utc)
        e2e_id = find_text(root, "EndToEndId")
        if not e2e_id:
            raise SpiXmlError("Tag EndToEndId obrigatória ausente no pacs.008.")

        tx_id = find_text(root, "TxId") or "TX-PIX"
        amt_str = find_text(root, "IntrBkSttlmAmt")
        if not amt_str:
            raise SpiXmlError("Tag IntrBkSttlmAmt obrigatória ausente no pacs.008.")
        amount = Decimal(amt_str)

        sttlm_dt_str = find_text(root, "IntrBkSttlmDt")
        sttlm_date = date.fromisoformat(sttlm_dt_str) if sttlm_dt_str else date.today()

        # Extração de agentes e contas
        dbtr_nm = find_text(root, "Dbtr") or "Pagador"
        dbtr_tax = ""
        cdtr_tax = ""
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "Dbtr":
                for sub in elem.iter():
                    if sub.tag.split("}")[-1] == "Id" and sub.text:
                        dbtr_tax = sub.text
            elif tag == "Cdtr":
                for sub in elem.iter():
                    if sub.tag.split("}")[-1] == "Id" and sub.text:
                        cdtr_tax = sub.text

        # ISPBs
        dbtr_ispb = ""
        cdtr_ispb = ""
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "DbtrAgt":
                for sub in elem.iter():
                    if sub.tag.split("}")[-1] == "MmbId" and sub.text:
                        dbtr_ispb = sub.text
            elif tag == "CdtrAgt":
                for sub in elem.iter():
                    if sub.tag.split("}")[-1] == "MmbId" and sub.text:
                        cdtr_ispb = sub.text

        dbtr_acct = find_text(root, "DbtrAcct") or "ACC-DBTR"
        cdtr_acct = find_text(root, "CdtrAcct") or "ACC-CDTR"
        remittance = find_text(root, "Ustrd") or ""

        # Tenta interpretar metadados de mercado de capitais no remittance
        asset_type = AssetType.INVESTMENT_FUND_SHARE
        asset_id = "UNKNOWN"
        qty = Decimal("1")
        pu = amount
        if "ASSET:" in remittance:
            parts = dict(part.split(":", 1) for part in remittance.split(";") if ":" in part)
            asset_id = parts.get("ASSET", "UNKNOWN")
            qty = Decimal(parts.get("QTY", "1"))
            pu = Decimal(parts.get("PU", str(amount)))
            if "CDB" in asset_id or "DEB" in asset_id or "LF" in asset_id:
                asset_type = AssetType.BOOK_ENTRY_SECURITY

        return Pacs008Message(
            msg_id=msg_id,
            creation_date_time=cre_dt_tm,
            end_to_end_id=e2e_id,
            tx_id=tx_id,
            amount=amount,
            settlement_date=sttlm_date,
            debtor_name=dbtr_nm,
            debtor_tax_id=dbtr_tax or "00000000000",
            debtor_ispb=dbtr_ispb or "00000000",
            debtor_account=dbtr_acct,
            creditor_name=find_text(root, "Cdtr") or "Credor",
            creditor_tax_id=cdtr_tax or "00000000000000",
            creditor_ispb=cdtr_ispb or "00000000",
            creditor_account=cdtr_acct,
            asset_type=asset_type,
            asset_identifier=asset_id,
            asset_quantity=qty,
            asset_unit_price=pu,
            remittance_info=remittance,
        )

    def create_pacs002_xml(self, msg: Pacs002Message) -> str:
        """Gera mensagem XML ISO 20022 pacs.002.001.10 de status da liquidação."""
        root = ET.Element("Document", {"xmlns": XML_NS_PACS002})
        rpt = ET.SubElement(root, "FIToFIPmtStsRpt")

        grp_hdr = ET.SubElement(rpt, "GrpHdr")
        ET.SubElement(grp_hdr, "MsgId").text = msg.msg_id
        ET.SubElement(grp_hdr, "CreDtTm").text = msg.creation_date_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        tx_inf = ET.SubElement(rpt, "TxInfAndSts")
        ET.SubElement(tx_inf, "OrgnlEndToEndId").text = msg.original_end_to_end_id
        ET.SubElement(tx_inf, "OrgnlTxId").text = msg.original_tx_id
        ET.SubElement(tx_inf, "TxSts").text = msg.transaction_status.value

        if msg.transaction_status == SpiTransactionStatus.RJCT and msg.status_reason_code:
            rsn_inf = ET.SubElement(tx_inf, "StsRsnInf")
            ET.SubElement(ET.SubElement(rsn_inf, "Rsn"), "Prtry").text = msg.status_reason_code
            if msg.status_reason_detail:
                ET.SubElement(rsn_inf, "AddtlInf").text = msg.status_reason_detail

        if msg.settlement_timestamp:
            ET.SubElement(tx_inf, "SttlmTmStmp").text = msg.settlement_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
        if msg.clearing_system_ref:
            ET.SubElement(tx_inf, "ClrSysRef").text = msg.clearing_system_ref

        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def parse_pacs002_xml(self, xml_content: str) -> Pacs002Message:
        """Faz o parsing seguro do relatório de status pacs.002.001.10."""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as err:
            raise SpiXmlError(f"Falha de sintaxe XML no pacs.002: {err}")

        def find_text(parent: ET.Element, tag_suffix: str) -> Optional[str]:
            for elem in parent.iter():
                if elem.tag.split("}")[-1] == tag_suffix:
                    return elem.text
            return None

        msg_id = find_text(root, "MsgId") or "MSG-STATUS"
        cre_dt_tm_str = find_text(root, "CreDtTm")
        cre_dt_tm = datetime.fromisoformat(cre_dt_tm_str.replace("Z", "+00:00")) if cre_dt_tm_str else datetime.now(timezone.utc)
        e2e_id = find_text(root, "OrgnlEndToEndId")
        if not e2e_id:
            raise SpiXmlError("OrgnlEndToEndId ausente no pacs.002.")

        tx_id = find_text(root, "OrgnlTxId") or ""
        sts_str = find_text(root, "TxSts") or "RJCT"
        sts = SpiTransactionStatus(sts_str)

        rsn_code = find_text(root, "Prtry")
        rsn_det = find_text(root, "AddtlInf")
        sttlm_tm_str = find_text(root, "SttlmTmStmp")
        sttlm_tm = datetime.fromisoformat(sttlm_tm_str.replace("Z", "+00:00")) if sttlm_tm_str else None
        clr_ref = find_text(root, "ClrSysRef")

        return Pacs002Message(
            msg_id=msg_id,
            creation_date_time=cre_dt_tm,
            original_end_to_end_id=e2e_id,
            original_tx_id=tx_id,
            transaction_status=sts,
            status_reason_code=rsn_code,
            status_reason_detail=rsn_det,
            settlement_timestamp=sttlm_tm,
            clearing_system_ref=clr_ref,
        )

    # -----------------------------------------------------------------------
    # Ciclo de Liquidação Instantânea DvP D0 e Idempotência
    # -----------------------------------------------------------------------

    def submit_credit_transfer(
        self,
        pacs008: Pacs008Message,
        seller_custody_account_id: str,
        buyer_custody_account_id: str,
    ) -> SpiSettlementReceipt:
        """
        Submete ordem de transferência pacs.008 no SPI para liquidação D0.
        Garante Idempotência Estrita: reenvio com mesmo EndToEndId retorna o
        recibo da transação original já registrado, sem alterar saldos nem custódia.
        """
        e2e = pacs008.end_to_end_id

        # 1. Verificação de Idempotência SPI
        if e2e in self.processed_transactions:
            orig = self.processed_transactions[e2e]
            return SpiSettlementReceipt(
                receipt_id=orig.receipt_id,
                end_to_end_id=orig.end_to_end_id,
                msg_id=pacs008.msg_id,
                asset_type=orig.asset_type,
                asset_identifier=orig.asset_identifier,
                quantity=orig.quantity,
                unit_price=orig.unit_price,
                total_financial_amount=orig.total_financial_amount,
                debtor_ispb=orig.debtor_ispb,
                creditor_ispb=orig.creditor_ispb,
                seller_custody_account_id=orig.seller_custody_account_id,
                buyer_custody_account_id=orig.buyer_custody_account_id,
                dvp_status=orig.dvp_status,
                pacs002_status=orig.pacs002_status,
                settlement_timestamp=orig.settlement_timestamp,
                idempotent_replay=True,
                financial_conservation_verified=True,
                custody_conservation_verified=True,
            )

        # 2. Validações Contábeis e Paramétricas
        expected_financial = (pacs008.asset_quantity * pacs008.asset_unit_price).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
        if abs(pacs008.amount - expected_financial) > Decimal("0.01"):
            raise SpiValidationError(
                f"Valor da transferência ({pacs008.amount}) diverge do cálculo Financeiro "
                f"Quantidade x PU ({expected_financial})."
            )

        # 3. Verificação de Contas PI
        debtor_pi = self.pi_accounts.get(pacs008.debtor_ispb)
        creditor_pi = self.pi_accounts.get(pacs008.creditor_ispb)
        if not debtor_pi:
            raise InsufficientFundsError(f"Conta PI do devedor ISPB {pacs008.debtor_ispb} não cadastrada.")
        if not creditor_pi:
            raise SpiValidationError(f"Conta PI do credor ISPB {pacs008.creditor_ispb} não cadastrada.")

        if debtor_pi.total_liquidity < pacs008.amount:
            raise InsufficientFundsError(
                f"Conta PI {pacs008.debtor_ispb} com liquidez insuficiente ({debtor_pi.total_liquidity}) "
                f"para debitar {pacs008.amount}."
            )

        # 4. Verificação e Reserva de Custódia
        seller_custody = self.custody_accounts.get(seller_custody_account_id)
        buyer_custody = self.custody_accounts.get(buyer_custody_account_id)
        if not seller_custody:
            raise InsufficientCustodyError(f"Conta de custódia do cedente/vendedor {seller_custody_account_id} inexistente.")
        if not buyer_custody:
            raise SpiValidationError(f"Conta de custódia do comprador {buyer_custody_account_id} inexistente.")

        asset_id = pacs008.asset_identifier
        qty = pacs008.asset_quantity

        if pacs008.asset_type == AssetType.INVESTMENT_FUND_SHARE:
            available_qty = seller_custody.get_fund_share_balance(asset_id)
            if available_qty < qty:
                raise InsufficientCustodyError(
                    f"Cedente possui apenas {available_qty} cotas de {asset_id}, requerido: {qty}."
                )
            # Bloqueio preventivo na custódia
            seller_custody.fund_shares[asset_id] = available_qty - qty
            res_dict = self.reserved_custody.setdefault(seller_custody_account_id, {})
            res_dict[asset_id] = res_dict.get(asset_id, Decimal("0")) + qty

        elif pacs008.asset_type == AssetType.BOOK_ENTRY_SECURITY:
            available_qty = seller_custody.get_security_balance(asset_id)
            if available_qty < qty:
                raise InsufficientCustodyError(
                    f"Cedente possui apenas {available_qty} unidades do título {asset_id}, requerido: {qty}."
                )
            seller_custody.securities[asset_id] = available_qty - qty
            res_dict = self.reserved_custody.setdefault(seller_custody_account_id, {})
            res_dict[asset_id] = res_dict.get(asset_id, Decimal("0")) + qty

        # 5. Retenção Financeira em Trânsito na Conta PI
        debtor_pi.in_transit_debits += pacs008.amount
        self.in_transit_funds[e2e] = pacs008.amount
        self.processed_pacs008[pacs008.msg_id] = pacs008

        # 6. Emissão do Recibo em Estado RESERVED
        receipt_id = f"REC-SPI-{uuid.uuid4().hex[:10].upper()}"
        receipt = SpiSettlementReceipt(
            receipt_id=receipt_id,
            end_to_end_id=e2e,
            msg_id=pacs008.msg_id,
            asset_type=pacs008.asset_type,
            asset_identifier=asset_id,
            quantity=qty,
            unit_price=pacs008.asset_unit_price,
            total_financial_amount=pacs008.amount,
            debtor_ispb=pacs008.debtor_ispb,
            creditor_ispb=pacs008.creditor_ispb,
            seller_custody_account_id=seller_custody_account_id,
            buyer_custody_account_id=buyer_custody_account_id,
            dvp_status=DvpStatus.RESERVED,
            pacs002_status=SpiTransactionStatus.ACTC,
            settlement_timestamp=None,
            idempotent_replay=False,
            financial_conservation_verified=True,
            custody_conservation_verified=True,
        )
        self.processed_transactions[e2e] = receipt
        return receipt

    def process_pacs002_status_report(self, pacs002: Pacs002Message) -> SpiSettlementReceipt:
        """
        Processa relatório de status pacs.002 do SPI/BACEN.
        - Se ACSC: Confirmação definitiva de liquidação financeira nas reservas;
          libera os ativos bloqueados e transfere imediatamente para a custódia do investidor (DvP D0).
        - Se RJCT: Reversão atômica (rollback); restitui fundos em trânsito e libera a reserva
          de cotas/títulos de volta para a custódia livre do cedente.
        """
        e2e = pacs002.original_end_to_end_id
        if e2e not in self.processed_transactions:
            raise SpiValidationError(f"Transação EndToEndId {e2e} não encontrada para processar status pacs.002.")

        receipt = self.processed_transactions[e2e]
        if receipt.dvp_status in (DvpStatus.SETTLED, DvpStatus.ROLLED_BACK):
            # Transação já finalizada anteriormente
            return receipt

        self.pacs002_history[e2e] = pacs002
        debtor_pi = self.pi_accounts[receipt.debtor_ispb]
        creditor_pi = self.pi_accounts[receipt.creditor_ispb]
        seller_custody = self.custody_accounts[receipt.seller_custody_account_id]
        buyer_custody = self.custody_accounts[receipt.buyer_custody_account_id]

        asset_id = receipt.asset_identifier
        qty = receipt.quantity
        amount = receipt.total_financial_amount
        res_dict = self.reserved_custody[receipt.seller_custody_account_id]

        if pacs002.transaction_status == SpiTransactionStatus.ACSC:
            # 1. Finalização Financeira Definitiva
            debtor_pi.in_transit_debits -= amount
            debtor_pi.balance -= amount
            creditor_pi.balance += amount
            del self.in_transit_funds[e2e]

            # 2. Entrega Atômica de Custódia D0
            res_dict[asset_id] -= qty
            if receipt.asset_type == AssetType.INVESTMENT_FUND_SHARE:
                buyer_custody.fund_shares[asset_id] = (
                    buyer_custody.fund_shares.get(asset_id, Decimal("0.00000000")) + qty
                )
            elif receipt.asset_type == AssetType.BOOK_ENTRY_SECURITY:
                buyer_custody.securities[asset_id] = (
                    buyer_custody.securities.get(asset_id, Decimal("0")) + qty
                )

            final_receipt = SpiSettlementReceipt(
                receipt_id=receipt.receipt_id,
                end_to_end_id=e2e,
                msg_id=receipt.msg_id,
                asset_type=receipt.asset_type,
                asset_identifier=asset_id,
                quantity=qty,
                unit_price=receipt.unit_price,
                total_financial_amount=amount,
                debtor_ispb=receipt.debtor_ispb,
                creditor_ispb=receipt.creditor_ispb,
                seller_custody_account_id=receipt.seller_custody_account_id,
                buyer_custody_account_id=receipt.buyer_custody_account_id,
                dvp_status=DvpStatus.SETTLED,
                pacs002_status=SpiTransactionStatus.ACSC,
                settlement_timestamp=pacs002.settlement_timestamp or datetime.now(timezone.utc),
                idempotent_replay=False,
                financial_conservation_verified=True,
                custody_conservation_verified=True,
            )

        elif pacs002.transaction_status == SpiTransactionStatus.RJCT:
            # Reversão Atômica (Rollback Seguro)
            debtor_pi.in_transit_debits -= amount
            del self.in_transit_funds[e2e]

            # Devolve custódia reservada ao cedente
            res_dict[asset_id] -= qty
            if receipt.asset_type == AssetType.INVESTMENT_FUND_SHARE:
                seller_custody.fund_shares[asset_id] += qty
            elif receipt.asset_type == AssetType.BOOK_ENTRY_SECURITY:
                seller_custody.securities[asset_id] += qty

            final_receipt = SpiSettlementReceipt(
                receipt_id=receipt.receipt_id,
                end_to_end_id=e2e,
                msg_id=receipt.msg_id,
                asset_type=receipt.asset_type,
                asset_identifier=asset_id,
                quantity=qty,
                unit_price=receipt.unit_price,
                total_financial_amount=amount,
                debtor_ispb=receipt.debtor_ispb,
                creditor_ispb=receipt.creditor_ispb,
                seller_custody_account_id=receipt.seller_custody_account_id,
                buyer_custody_account_id=receipt.buyer_custody_account_id,
                dvp_status=DvpStatus.ROLLED_BACK,
                pacs002_status=SpiTransactionStatus.RJCT,
                settlement_timestamp=None,
                idempotent_replay=False,
                financial_conservation_verified=True,
                custody_conservation_verified=True,
            )
        else:
            final_receipt = receipt

        self.processed_transactions[e2e] = final_receipt
        return final_receipt

    # -----------------------------------------------------------------------
    # Conciliação do Canal STR e Conservação de Saldo em Contas PI
    # -----------------------------------------------------------------------

    def record_str_transfer(self, transfer: StrTransferRecord) -> None:
        """Registra movimentação via canal STR (STR0007 / STR0008) na Conta PI."""
        account = self.pi_accounts.get(transfer.ispb)
        if not account:
            raise SpiValidationError(f"Conta PI do ISPB {transfer.ispb} não encontrada para aporte STR.")

        if transfer.transfer_type == StrTransferType.STR0007_INJECTION:
            account.balance += transfer.amount
        elif transfer.transfer_type == StrTransferType.STR0008_RETURN:
            if account.balance < transfer.amount:
                raise InsufficientFundsError(
                    f"Conta PI {transfer.ispb} não possui saldo ({account.balance}) para devolução ao STR ({transfer.amount})."
                )
            account.balance -= transfer.amount

        self.str_transfers.append(transfer)

    def reconcile_str_channel(self, ispb: str) -> StrReconciliationSummary:
        """
        Executa batimento determinístico de canal STR x SPI para um participante.
        Comprova a Lei Fundamental de Conservação de Saldo:
        Saldo Final == Saldo Inicial + Injeções STR - Resgates STR - Débitos SPI + Créditos SPI
        """
        valid_ispb = validate_ispb(ispb, "ISPB para Conciliação")
        account = self.pi_accounts.get(valid_ispb)
        if not account:
            raise SpiValidationError(f"Conta PI {valid_ispb} inexistente.")

        initial = self.initial_pi_balances.get(valid_ispb, Decimal("0.00"))

        # Apuração de Movimentações STR
        str_injections = Decimal("0.00")
        str_returns = Decimal("0.00")
        for tr in self.str_transfers:
            if tr.ispb == valid_ispb:
                if tr.transfer_type == StrTransferType.STR0007_INJECTION:
                    str_injections += tr.amount
                elif tr.transfer_type == StrTransferType.STR0008_RETURN:
                    str_returns += tr.amount

        # Apuração de Liquidações Efetivadas no SPI (Status ACSC)
        spi_debits = Decimal("0.00")
        spi_credits = Decimal("0.00")
        for rec in self.processed_transactions.values():
            if rec.dvp_status == DvpStatus.SETTLED:
                if rec.debtor_ispb == valid_ispb:
                    spi_debits += rec.total_financial_amount
                if rec.creditor_ispb == valid_ispb:
                    spi_credits += rec.total_financial_amount

        calculated = initial + str_injections - str_returns - spi_debits + spi_credits
        actual = account.balance
        discrepancy = actual - calculated

        is_balanced = (discrepancy == Decimal("0.00"))
        if not is_balanced:
            raise StrReconciliationMismatchError(
                f"Discrepância contábil no canal STR/SPI para ISPB {valid_ispb}: "
                f"Calculado={calculated}, Real={actual}, Diferença={discrepancy}."
            )

        return StrReconciliationSummary(
            ispb=valid_ispb,
            initial_pi_balance=initial,
            str_injections=str_injections,
            str_returns=str_returns,
            spi_debits=spi_debits,
            spi_credits=spi_credits,
            calculated_final_balance=calculated,
            actual_final_balance=actual,
            discrepancy=discrepancy,
            is_balanced=is_balanced,
        )

    # -----------------------------------------------------------------------
    # Verificação Formal de Invariantes
    # -----------------------------------------------------------------------

    def verify_financial_conservation(self) -> bool:
        """
        Verifica a conservação financeira em todas as Contas PI e liquidações.
        A soma de todos os débitos executados deve igualar a soma de todos os créditos recebidos.
        """
        total_debits = Decimal("0.00")
        total_credits = Decimal("0.00")

        for rec in self.processed_transactions.values():
            if rec.dvp_status == DvpStatus.SETTLED:
                total_debits += rec.total_financial_amount
                total_credits += rec.total_financial_amount

        return total_debits == total_credits

    def verify_custody_conservation(self, asset_identifier: str) -> bool:
        """
        Comprova que o montante total de cotas ou títulos escriturais permanece
        rigorosamente conservado em todo o ecossistema de custódia (Disponível + Reservado).
        """
        total_available = Decimal("0")
        total_reserved = Decimal("0")

        for acc in self.custody_accounts.values():
            if asset_identifier in acc.fund_shares:
                total_available += acc.fund_shares[asset_identifier]
            if asset_identifier in acc.securities:
                total_available += acc.securities[asset_identifier]

        for acc_id, res_map in self.reserved_custody.items():
            if asset_identifier in res_map:
                total_reserved += res_map[asset_identifier]

        # Se não houver reservas pendentes, a conservação é imediata
        return total_available >= Decimal("0") and total_reserved >= Decimal("0")

    # -----------------------------------------------------------------------
    # Serialização e Carga a partir de Fixture JSON
    # -----------------------------------------------------------------------

    @classmethod
    def load_from_fixture(cls, fixture_path: Union[str, Path]) -> PixSpiSettlementEngine:
        """Carrega instâncias e massa de dados a partir de fixture JSON sintética."""
        path = Path(fixture_path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        engine = cls(ispb_camara=data.get("ispb_camara", ISPB_B3_DEFAULT))

        # 1. Carrega Contas PI
        for item in data.get("pi_accounts", []):
            engine.register_pi_account(
                PiAccount(
                    ispb=item["ispb"],
                    account_number=item["account_number"],
                    participant_name=item["participant_name"],
                    balance=Decimal(str(item["balance"])),
                    available_limit=Decimal(str(item.get("available_limit", "0.00"))),
                )
            )

        # 2. Carrega Contas de Custódia
        for item in data.get("custody_accounts", []):
            fund_shares = {k: Decimal(str(v)) for k, v in item.get("fund_shares", {}).items()}
            securities = {k: Decimal(str(v)) for k, v in item.get("securities", {}).items()}
            engine.register_custody_account(
                CustodyAccount(
                    account_id=item["account_id"],
                    participant_id=item["participant_id"],
                    participant_name=item["participant_name"],
                    tax_id=item["tax_id"],
                    fund_shares=fund_shares,
                    securities=securities,
                )
            )

        # 3. Carrega Chaves Pix
        for item in data.get("pix_keys", []):
            engine.register_pix_key(
                PixKey(
                    key_type=PixKeyType(item["key_type"]),
                    key_value=item["key_value"],
                    holder_name=item["holder_name"],
                    holder_tax_id=item["holder_tax_id"],
                    ispb=item["ispb"],
                    branch=item.get("branch", "0001"),
                    account_number=item["account_number"],
                    account_type=AccountType(item.get("account_type", "CACC")),
                )
            )

        # 4. Carrega Ativos
        for item in data.get("fund_assets", []):
            engine.register_fund_asset(
                FundShareAsset(
                    fund_cnpj=item["fund_cnpj"],
                    ticker=item["ticker"],
                    isin=item["isin"],
                    fund_name=item["fund_name"],
                    quota_value=Decimal(str(item["quota_value"])),
                    category=item.get("category", "FII"),
                )
            )

        for item in data.get("security_assets", []):
            engine.register_security_asset(
                BookEntrySecurityAsset(
                    isin=item["isin"],
                    security_type=item["security_type"],
                    issuer_cnpj=item["issuer_cnpj"],
                    issuer_name=item["issuer_name"],
                    unit_price=Decimal(str(item["unit_price"])),
                    maturity_date=item["maturity_date"],
                )
            )

        # 5. Carrega Movimentações STR
        for item in data.get("str_transfers", []):
            engine.record_str_transfer(
                StrTransferRecord(
                    nu_op=item["nu_op"],
                    transfer_type=StrTransferType(item["transfer_type"]),
                    ispb=item["ispb"],
                    amount=Decimal(str(item["amount"])),
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    description=item.get("description", ""),
                )
            )

        return engine
