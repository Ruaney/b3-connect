"""Motor de Empréstimo Eletrônico de Ativos e Matching no PUMA BTC (FL-025).

Implementação pura e determinística em Python stdlib para o livro de ofertas de
aluguel de ações da B3 (Banco de Títulos CBLC / PUMA Trading System), suportando:
- Submissão de ordens FIX para Doador (Lender) e Tomador (Borrower).
- Algoritmo contínuo de correspondência (Matching Engine) com prioridade Preço-Tempo.
- Atendimento parcial e total de lotes de aluguel.
- Regra de taxa passiva (resting order priority) e compatibilidade de prazos.
- Geração automática de contratos de aluguel formalizados em D0 no leiaute IMBARQ011.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

try:
    from src.imbarq011 import LoanContractRecord, RECORD_LENGTH
except ImportError:
    try:
        from imbarq011 import LoanContractRecord, RECORD_LENGTH  # type: ignore
    except ImportError:
        LoanContractRecord = None  # type: ignore
        RECORD_LENGTH = 200


# ==============================================================================
# 1. Enumerações e Constantes
# ==============================================================================

class LendingSide(str, Enum):
    """Lado da operação de empréstimo de ativos no PUMA BTC."""
    LENDER = "LENDER"      # Doador (oferta de empréstimo / ask)
    BORROWER = "BORROWER"  # Tomador (ordem de tomada / bid)

    @property
    def is_lender(self) -> bool:
        return self == LendingSide.LENDER

    @property
    def is_borrower(self) -> bool:
        return self == LendingSide.BORROWER

    @classmethod
    def from_value(cls, val: Any) -> LendingSide:
        """Converte strings e códigos habituais (FIX/B3) para LendingSide."""
        if isinstance(val, LendingSide):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("LENDER", "LEND", "DOADOR", "DOACAO", "SELL", "2", "L"):
                return cls.LENDER
            if v in ("BORROWER", "BORROW", "TOMADOR", "TOMADA", "BUY", "1", "B"):
                return cls.BORROWER
        if val in (2, "2"):
            return cls.LENDER
        if val in (1, "1"):
            return cls.BORROWER
        raise ValueError(f"Lado de empréstimo inválido: {val!r}")


class RenewalType(str, Enum):
    """Tipo de renovação do contrato de empréstimo no BTC."""
    AUTOMATIC = "AUTOMATIC"          # Renovação automática no vencimento
    NON_AUTOMATIC = "NON_AUTOMATIC"  # Não automática / encerramento manual

    @classmethod
    def from_value(cls, val: Any) -> RenewalType:
        """Converte strings e códigos para RenewalType."""
        if isinstance(val, RenewalType):
            return val
        if isinstance(val, str):
            v = val.strip().upper()
            if v in ("AUTOMATIC", "AUTO", "A", "1"):
                return cls.AUTOMATIC
            if v in ("NON_AUTOMATIC", "NON-AUTOMATIC", "MANUAL", "M", "N", "2"):
                return cls.NON_AUTOMATIC
        if val in (1, "1"):
            return cls.AUTOMATIC
        if val in (2, "2"):
            return cls.NON_AUTOMATIC
        raise ValueError(f"Tipo de renovação inválido: {val!r}")


# ==============================================================================
# 2. Modelos de Dados Tipados (Dataclasses)
# ==============================================================================

@dataclass
class LendingOrder:
    """Ordem eletrônica de oferta ou demanda de aluguel no PUMA BTC."""
    order_id: str
    symbol: str
    isin: str
    side: LendingSide
    quantity: int
    rate: Decimal
    participant_id: int
    account: int
    min_term_days: int = 1
    max_term_days: int = 30
    renewal_type: RenewalType = RenewalType.NON_AUTOMATIC
    reversibility: str = "T"
    base_price: Decimal = Decimal("0.00")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    leaves_qty: int = field(init=False, default=0)
    cum_qty: int = field(init=False, default=0)
    cl_ord_id: Optional[str] = None

    def __post_init__(self) -> None:
        # Validação estrita de quantidade inteira positiva
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, (int, float)):
            raise ValueError(f"Quantidade deve ser um inteiro positivo: {self.quantity!r}")
        if isinstance(self.quantity, float):
            raise ValueError(f"Quantidade fracionária inválida: {self.quantity!r}")
        if self.quantity <= 0:
            raise ValueError(f"Quantidade deve ser estritamente positiva: {self.quantity}")

        # Normalização e validação de taxa
        if not isinstance(self.rate, Decimal):
            self.rate = Decimal(str(self.rate))
        if self.rate < Decimal("0"):
            raise ValueError(f"Taxa de aluguel não pode ser negativa: {self.rate}")

        # Normalização e validação de preço base
        if not isinstance(self.base_price, Decimal):
            self.base_price = Decimal(str(self.base_price))
        if self.base_price < Decimal("0"):
            raise ValueError(f"Preço base não pode ser negativo: {self.base_price}")

        # Normalização de tipos enumerados
        self.side = LendingSide.from_value(self.side)
        self.renewal_type = RenewalType.from_value(self.renewal_type)

        # Validação de prazos
        if self.min_term_days <= 0:
            raise ValueError(f"Prazo mínimo deve ser maior que zero: {self.min_term_days}")
        if self.max_term_days <= 0:
            raise ValueError(f"Prazo máximo deve ser maior que zero: {self.max_term_days}")
        if self.min_term_days > self.max_term_days:
            raise ValueError(
                f"Prazo mínimo ({self.min_term_days}) não pode ser maior que o prazo máximo ({self.max_term_days})"
            )

        # Reversibilidade padrão da B3 ('T', 'D', 'A', 'N')
        self.reversibility = (self.reversibility or "T").strip().upper()[:1]
        if self.reversibility not in ("T", "D", "A", "N"):
            self.reversibility = "T"

        # Inicialização do estado de saldo
        self.leaves_qty = self.quantity
        self.cum_qty = 0

    @property
    def is_filled(self) -> bool:
        """Indica se a ordem foi completamente executada."""
        return self.leaves_qty == 0

    @property
    def is_partial(self) -> bool:
        """Indica se a ordem teve atendimento parcial."""
        return self.cum_qty > 0 and self.leaves_qty > 0

    def to_dict(self) -> Dict[str, Any]:
        """Converte a ordem em representação serializável em dict/JSON."""
        return {
            "order_id": self.order_id,
            "cl_ord_id": self.cl_ord_id or self.order_id,
            "symbol": self.symbol,
            "isin": self.isin,
            "side": self.side.value,
            "quantity": self.quantity,
            "rate": str(self.rate),
            "participant_id": self.participant_id,
            "account": self.account,
            "min_term_days": self.min_term_days,
            "max_term_days": self.max_term_days,
            "renewal_type": self.renewal_type.value,
            "reversibility": self.reversibility,
            "base_price": str(self.base_price),
            "timestamp": self.timestamp.isoformat(),
            "leaves_qty": self.leaves_qty,
            "cum_qty": self.cum_qty,
        }

    def to_fix_new_order_single(self, delimiter: str = "|") -> str:
        """Gera a mensagem FIX NewOrderSingle (35=D) correspondente."""
        side_tag = "2" if self.side.is_lender else "1"
        renewal_tag = "1" if self.renewal_type == RenewalType.AUTOMATIC else "2"
        rate_str = f"{self.rate:.4f}"
        px_str = f"{self.base_price:.2f}"
        tags = [
            ("8", "FIX.4.4"),
            ("35", "D"),
            ("11", self.cl_ord_id or self.order_id),
            ("1", str(self.account)),
            ("448", str(self.participant_id)),
            ("55", self.symbol),
            ("48", self.isin),
            ("22", "4"),
            ("54", side_tag),
            ("38", str(self.quantity)),
            ("40", "2"),
            ("44", rate_str),
            ("9001", rate_str),
            ("9002", str(self.min_term_days)),
            ("9003", str(self.max_term_days)),
            ("9004", renewal_tag),
            ("9005", self.reversibility),
            ("9006", px_str),
            ("60", self.timestamp.strftime("%Y%m%d-%H:%M:%S")),
        ]
        body = delimiter.join(f"{t}={v}" for t, v in tags) + delimiter
        return body


@dataclass(frozen=True)
class LendingMatch:
    """Execução/casamento de aluguel entre doador e tomador formalizado em D0."""
    match_id: str
    contract_number: int
    symbol: str
    isin: str
    lender_order_id: str
    borrower_order_id: str
    lender_participant_id: int
    lender_account: int
    borrower_participant_id: int
    borrower_account: int
    quantity: int
    rate: Decimal
    term_days: int
    open_date: date
    maturity_date: date
    reversibility: str
    base_price: Decimal
    contract_value: Decimal
    renewal_type: RenewalType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_imbarq011_line(self) -> str:
        """Gera rigorosamente a linha posicional de 200 colunas (Registro 01 do IMBARQ011)."""
        tipo_registro = "01"
        num_contrato_str = f"{self.contract_number:015d}"
        part_doador_str = f"{self.lender_participant_id:08d}"
        conta_doador_str = f"{self.lender_account:012d}"
        part_tomador_str = f"{self.borrower_participant_id:08d}"
        conta_tomador_str = f"{self.borrower_account:012d}"
        ticker_str = f"{self.symbol:<12.12s}"
        isin_str = f"{self.isin:<12.12s}"
        qtd_str = f"{self.quantity:015d}"

        # Taxa de aluguel N(13)V4 (17 dígitos, 4 decimais implícitas)
        rate_int = int(round(self.rate * Decimal("10000")))
        rate_str = f"{rate_int:017d}"

        data_abertura_str = self.open_date.strftime("%Y%m%d")
        data_vencimento_str = self.maturity_date.strftime("%Y%m%d")
        rev_str = f"{self.reversibility[:1]:<1s}"

        # Preço base N(13)V2 (15 dígitos, 2 decimais implícitas)
        preco_base_int = int(round(self.base_price * Decimal("100")))
        preco_base_str = f"{preco_base_int:015d}"

        # Valor nocional N(15)V2 (17 dígitos, 2 decimais implícitas)
        nocional_int = int(round(self.contract_value * Decimal("100")))
        nocional_str = f"{nocional_int:017d}"

        filler = " " * 38

        line = (
            f"{tipo_registro}"
            f"{num_contrato_str}"
            f"{part_doador_str}"
            f"{conta_doador_str}"
            f"{part_tomador_str}"
            f"{conta_tomador_str}"
            f"{ticker_str}"
            f"{isin_str}"
            f"{qtd_str}"
            f"{rate_str}"
            f"{data_abertura_str}"
            f"{data_vencimento_str}"
            f"{rev_str}"
            f"{preco_base_str}"
            f"{nocional_str}"
            f"{filler}"
        )
        if len(line) != 200:
            raise ValueError(f"Comprimento da linha IMBARQ011 divergente de 200 colunas: {len(line)}")
        return line

    def to_imbarq011_record(self, line_number: int = 2) -> Any:
        """Retorna uma instância tipada de LoanContractRecord se imbarq011 estiver disponível."""
        raw = self.to_imbarq011_line()
        if LoanContractRecord is not None:
            return LoanContractRecord(
                tipo_registro="01",
                numero_contrato=self.contract_number,
                codigo_participante_doador=self.lender_participant_id,
                conta_doador=self.lender_account,
                codigo_participante_tomador=self.borrower_participant_id,
                conta_tomador=self.borrower_account,
                codigo_negociacao=self.symbol,
                codigo_isin=self.isin,
                quantidade_alugada=self.quantity,
                taxa_aluguel=self.rate,
                data_abertura=self.open_date,
                data_vencimento=self.maturity_date,
                tipo_reversibilidade=self.reversibility,
                preco_base=self.base_price,
                valor_contrato=self.contract_value,
                linha_numero=line_number,
                raw_line=raw,
            )
        return raw

    def to_fix_execution_report(
        self,
        for_side: LendingSide,
        exec_type: str = "2",
        cl_ord_id: str = "",
        delimiter: str = "|",
    ) -> str:
        """Gera mensagem ExecutionReport FIX (35=8) correspondente à contraparte."""
        is_lender = for_side.is_lender
        order_id = self.lender_order_id if is_lender else self.borrower_order_id
        part_id = self.lender_participant_id if is_lender else self.borrower_participant_id
        account = self.lender_account if is_lender else self.borrower_account
        side_tag = "2" if is_lender else "1"

        tags = [
            ("8", "FIX.4.4"),
            ("35", "8"),
            ("11", cl_ord_id or order_id),
            ("37", order_id),
            ("17", self.match_id),
            ("150", exec_type),
            ("39", exec_type),
            ("55", self.symbol),
            ("48", self.isin),
            ("22", "4"),
            ("54", side_tag),
            ("1", str(account)),
            ("448", str(part_id)),
            ("38", str(self.quantity)),
            ("32", str(self.quantity)),
            ("31", f"{self.rate:.4f}"),
            ("9007", str(self.contract_number)),
            ("60", self.timestamp.strftime("%Y%m%d-%H:%M:%S")),
        ]
        return delimiter.join(f"{t}={v}" for t, v in tags) + delimiter

    def to_dict(self) -> Dict[str, Any]:
        """Converte a execução em representação estruturada em dict/JSON."""
        return {
            "match_id": self.match_id,
            "contract_number": self.contract_number,
            "symbol": self.symbol,
            "isin": self.isin,
            "lender_order_id": self.lender_order_id,
            "borrower_order_id": self.borrower_order_id,
            "lender_participant_id": self.lender_participant_id,
            "lender_account": self.lender_account,
            "borrower_participant_id": self.borrower_participant_id,
            "borrower_account": self.borrower_account,
            "quantity": self.quantity,
            "rate": str(self.rate),
            "term_days": self.term_days,
            "open_date": self.open_date.isoformat(),
            "maturity_date": self.maturity_date.isoformat(),
            "reversibility": self.reversibility,
            "base_price": str(self.base_price),
            "contract_value": str(self.contract_value),
            "renewal_type": self.renewal_type.value,
            "timestamp": self.timestamp.isoformat(),
        }


# ==============================================================================
# 3. Motor Determinístico de Matching (BtcMatchingEngine)
# ==============================================================================

class BtcMatchingEngine:
    """Motor de correspondência de aluguel eletrônico (Banco de Títulos CBLC / PUMA BTC).

    Opera de forma determinística com ordenação estrita Preço-Tempo:
    - Doadores (Lenders): menor taxa pedida primeiro, FIFO no desempate.
    - Tomadores (Borrowers): maior taxa oferecida primeiro, FIFO no desempate.
    - Casamento sob regra de preço passivo (preço da ordem descansada no livro).
    - Suporte a matches parciais e divisão de lotes.
    - Formalização imediata de contratos D0 exportáveis no leiaute IMBARQ011.
    """

    def __init__(
        self,
        trade_date: Optional[date] = None,
        contract_seq_start: int = 202600001,
    ) -> None:
        self.trade_date: date = trade_date or date.today()
        self._contract_counter: int = contract_seq_start
        self._books: Dict[str, Dict[str, List[LendingOrder]]] = {}
        self._orders: Dict[str, LendingOrder] = {}
        self._matches: List[LendingMatch] = []

    def _next_contract_number(self) -> int:
        num = self._contract_counter
        self._contract_counter += 1
        return num

    def _get_symbol_queues(self, symbol: str) -> Dict[str, List[LendingOrder]]:
        if symbol not in self._books:
            self._books[symbol] = {"lenders": [], "borrowers": []}
        return self._books[symbol]

    def submit_order(self, order: LendingOrder) -> List[LendingMatch]:
        """Submete uma nova ordem ao motor de matching BTC.

        Executa cruzamentos compatíveis imediatamente. Caso reste saldo
        residual não preenchido (`leaves_qty > 0`), a ordem é inserida no livro.
        """
        if order.order_id in self._orders:
            raise ValueError(f"Ordem com order_id={order.order_id!r} já cadastrada no motor")

        self._orders[order.order_id] = order
        queues = self._get_symbol_queues(order.symbol)
        matches: List[LendingMatch] = []

        if order.side.is_borrower:
            # Ordem agressora de Tomador: busca os melhores Doadores (menor taxa)
            lenders_queue = queues["lenders"]
            i = 0
            while i < len(lenders_queue) and order.leaves_qty > 0:
                lender = lenders_queue[i]

                # Condição de matching: Tomador aceita pagar >= Taxa pedida pelo Doador
                if lender.rate > order.rate:
                    # Como lenders_queue está ordenada por taxa crescente, nenhuma outra atenderá
                    break

                # Verificação de compatibilidade de prazos
                term_start = max(lender.min_term_days, order.min_term_days)
                term_end = min(lender.max_term_days, order.max_term_days)
                if term_start > term_end:
                    # Prazos incompatíveis, avança para o próximo doador
                    i += 1
                    continue

                # Casamento viável!
                matched_term = term_start
                matched_qty = min(order.leaves_qty, lender.leaves_qty)
                matched_rate = lender.rate  # Regra de preço passivo (ordem descansada)

                # Preço base de cotação
                base_px = (
                    order.base_price
                    if order.base_price > Decimal("0")
                    else lender.base_price
                )
                contract_val = (Decimal(matched_qty) * base_px).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                contract_num = self._next_contract_number()
                mat_date = self.trade_date + timedelta(days=matched_term)
                rev = order.reversibility or lender.reversibility or "T"
                ren = order.renewal_type

                match = LendingMatch(
                    match_id=f"MTC-BTC-{contract_num}",
                    contract_number=contract_num,
                    symbol=order.symbol,
                    isin=order.isin or lender.isin,
                    lender_order_id=lender.order_id,
                    borrower_order_id=order.order_id,
                    lender_participant_id=lender.participant_id,
                    lender_account=lender.account,
                    borrower_participant_id=order.participant_id,
                    borrower_account=order.account,
                    quantity=matched_qty,
                    rate=matched_rate,
                    term_days=matched_term,
                    open_date=self.trade_date,
                    maturity_date=mat_date,
                    reversibility=rev,
                    base_price=base_px,
                    contract_value=contract_val,
                    renewal_type=ren,
                )

                # Atualiza saldos das ordens
                order.leaves_qty -= matched_qty
                order.cum_qty += matched_qty
                lender.leaves_qty -= matched_qty
                lender.cum_qty += matched_qty

                matches.append(match)
                self._matches.append(match)

                if lender.leaves_qty == 0:
                    lenders_queue.pop(i)
                else:
                    # Doador atendeu parcialmente e continua no topo
                    i += 1

            # Se ainda restou lote no Tomador, insere no livro de tomadores
            if order.leaves_qty > 0:
                queues["borrowers"].append(order)
                # Ordenação: maior taxa primeiro, depois timestamp mais antigo (FIFO)
                queues["borrowers"].sort(key=lambda o: (-o.rate, o.timestamp))

        else:
            # Ordem agressora de Doador: busca os melhores Tomadores (maior taxa)
            borrowers_queue = queues["borrowers"]
            i = 0
            while i < len(borrowers_queue) and order.leaves_qty > 0:
                borrower = borrowers_queue[i]

                # Condição de matching: Tomador ofereceu >= Taxa mínima pedida pelo Doador
                if borrower.rate < order.rate:
                    # Como borrowers_queue está ordenada por taxa decrescente, nenhuma outra atenderá
                    break

                # Verificação de compatibilidade de prazos
                term_start = max(order.min_term_days, borrower.min_term_days)
                term_end = min(order.max_term_days, borrower.max_term_days)
                if term_start > term_end:
                    i += 1
                    continue

                # Casamento viável!
                matched_term = term_start
                matched_qty = min(order.leaves_qty, borrower.leaves_qty)
                matched_rate = borrower.rate  # Regra de preço passivo (ordem descansada)

                base_px = (
                    borrower.base_price
                    if borrower.base_price > Decimal("0")
                    else order.base_price
                )
                contract_val = (Decimal(matched_qty) * base_px).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                contract_num = self._next_contract_number()
                mat_date = self.trade_date + timedelta(days=matched_term)
                rev = borrower.reversibility or order.reversibility or "T"
                ren = borrower.renewal_type

                match = LendingMatch(
                    match_id=f"MTC-BTC-{contract_num}",
                    contract_number=contract_num,
                    symbol=order.symbol,
                    isin=order.isin or borrower.isin,
                    lender_order_id=order.order_id,
                    borrower_order_id=borrower.order_id,
                    lender_participant_id=order.participant_id,
                    lender_account=order.account,
                    borrower_participant_id=borrower.participant_id,
                    borrower_account=borrower.account,
                    quantity=matched_qty,
                    rate=matched_rate,
                    term_days=matched_term,
                    open_date=self.trade_date,
                    maturity_date=mat_date,
                    reversibility=rev,
                    base_price=base_px,
                    contract_value=contract_val,
                    renewal_type=ren,
                )

                # Atualiza saldos das ordens
                order.leaves_qty -= matched_qty
                order.cum_qty += matched_qty
                borrower.leaves_qty -= matched_qty
                borrower.cum_qty += matched_qty

                matches.append(match)
                self._matches.append(match)

                if borrower.leaves_qty == 0:
                    borrowers_queue.pop(i)
                else:
                    i += 1

            # Se ainda restou lote no Doador, insere no livro de doadores
            if order.leaves_qty > 0:
                queues["lenders"].append(order)
                # Ordenação: menor taxa primeiro, depois timestamp mais antigo (FIFO)
                queues["lenders"].sort(key=lambda o: (o.rate, o.timestamp))

        return matches

    def add_order(self, order: LendingOrder) -> List[LendingMatch]:
        """Alias para submit_order."""
        return self.submit_order(order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancela uma ordem ativa no livro de ofertas.

        Retorna True se a ordem foi localizada e removida; False caso contrário.
        """
        order = self._orders.get(order_id)
        if not order or order.is_filled:
            return False

        queues = self._get_symbol_queues(order.symbol)
        target_queue = queues["lenders"] if order.side.is_lender else queues["borrowers"]
        if order in target_queue:
            target_queue.remove(order)
            order.leaves_qty = 0
            return True
        return False

    def get_order(self, order_id: str) -> Optional[LendingOrder]:
        """Obtém uma ordem pelo identificador."""
        return self._orders.get(order_id)

    def get_matches(self, symbol: Optional[str] = None) -> List[LendingMatch]:
        """Retorna as execuções ocorridas, opcionalmente filtradas por ativo."""
        if symbol is None:
            return list(self._matches)
        return [m for m in self._matches if m.symbol == symbol]

    def get_book(self, symbol: str) -> Dict[str, Any]:
        """Retorna o estado do livro de ofertas para um determinado ativo."""
        queues = self._get_symbol_queues(symbol)
        lenders = queues["lenders"]
        borrowers = queues["borrowers"]
        best_lender_rate = str(lenders[0].rate) if lenders else None
        best_borrower_rate = str(borrowers[0].rate) if borrowers else None

        return {
            "symbol": symbol,
            "lenders": [o.to_dict() for o in lenders],
            "borrowers": [o.to_dict() for o in borrowers],
            "best_lender_rate": best_lender_rate,
            "best_borrower_rate": best_borrower_rate,
            "total_lender_qty": sum(o.leaves_qty for o in lenders),
            "total_borrower_qty": sum(o.leaves_qty for o in borrowers),
        }

    def load_fixtures_from_json(self, json_path: Union[str, Path]) -> List[LendingMatch]:
        """Carrega e submete sequencialmente ordens a partir de um arquivo JSON de fixture."""
        p = Path(json_path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        orders_raw = data.get("orders", [])
        all_matches: List[LendingMatch] = []

        for ord_data in orders_raw:
            ts = None
            if "timestamp" in ord_data and ord_data["timestamp"]:
                try:
                    ts = datetime.fromisoformat(ord_data["timestamp"].replace("Z", "+00:00"))
                except ValueError:
                    ts = None

            order = LendingOrder(
                order_id=ord_data["order_id"],
                symbol=ord_data["symbol"],
                isin=ord_data["isin"],
                side=LendingSide.from_value(ord_data["side"]),
                quantity=int(ord_data["quantity"]),
                rate=Decimal(str(ord_data["rate"])),
                participant_id=int(ord_data["participant_id"]),
                account=int(ord_data["account"]),
                min_term_days=int(ord_data.get("min_term_days", 1)),
                max_term_days=int(ord_data.get("max_term_days", 30)),
                renewal_type=RenewalType.from_value(ord_data.get("renewal_type", "NON_AUTOMATIC")),
                reversibility=ord_data.get("reversibility", "T"),
                base_price=Decimal(str(ord_data.get("base_price", "0.00"))),
                timestamp=ts or datetime.now(timezone.utc),
            )
            matches = self.submit_order(order)
            all_matches.extend(matches)

        return all_matches

    def export_imbarq011(
        self,
        participant_id: int = 105,
        participant_name: str = "CORRETORA ATLAS EXEMPLO S/A",
        seq_remessa: int = 1,
        symbol: Optional[str] = None,
    ) -> str:
        """Gera o arquivo posicional IMBARQ011 completo e determinístico (200 colunas).

        Contém:
        - Header 00 (Abertura)
        - Detalhes 01 (Contratos de aluguel executados)
        - Trailler 99 (Checksum e totalizações)
        """
        matches = self.get_matches(symbol)

        # Header 00
        now = datetime.now(timezone.utc)
        data_geracao = now.strftime("%Y%m%d")
        hora_geracao = now.strftime("%H%M%S")
        data_movimento = self.trade_date.strftime("%Y%m%d")
        header = (
            f"00IMBARQ011001"
            f"{data_geracao}{hora_geracao}{data_movimento}"
            f"{participant_id:08d}"
            f"{participant_name:<30.30s}"
            f"{seq_remessa:06d}"
            f"{' ' * 120}"
        )
        if len(header) != 200:
            raise ValueError(f"Header IMBARQ011 deve ter 200 caracteres, obtido {len(header)}")

        # Detalhes 01
        detail_lines = [m.to_imbarq011_line() for m in matches]

        # Trailler 99
        total_linhas = len(detail_lines) + 2
        total_detalhes = len(detail_lines)
        total_quantidade = sum(m.quantity for m in matches)
        total_valor = sum(m.contract_value for m in matches)
        total_valor_int = int(round(total_valor * Decimal("100")))

        trailer = (
            f"99IMBARQ011"
            f"{total_linhas:08d}"
            f"{total_detalhes:08d}"
            f"{total_quantidade:018d}"
            f"{total_valor_int:018d}"
            f"{' ' * 137}"
        )
        if len(trailer) != 200:
            raise ValueError(f"Trailler IMBARQ011 deve ter 200 caracteres, obtido {len(trailer)}")

        lines = [header] + detail_lines + [trailer]
        return "\n".join(lines) + "\n"
