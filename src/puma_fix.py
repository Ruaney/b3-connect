"""B3 PUMA Trading System - FIX Protocol and Drop Copy Engine.

Módulos para conectividade de negociação (Order Entry - FL-011) e recepção passiva
de execuções em tempo real (Drop Copy - FL-012) no PUMA Trading System da B3.
Implementação puramente determinística baseada na biblioteca padrão do Python.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


# ==============================================================================
# 1. Constantes e Tags FIX (FIX 4.4 / FIX 5.0 SP2 / B3 Custom)
# ==============================================================================

SOH = b"\x01"
PIPE = b"|"

# Standard Header Tags
TAG_BEGIN_STRING = 8
TAG_BODY_LENGTH = 9
TAG_MSG_TYPE = 35
TAG_SENDER_COMP_ID = 49
TAG_TARGET_COMP_ID = 56
TAG_MSG_SEQ_NUM = 34
TAG_SENDING_TIME = 52
TAG_APPL_VER_ID = 1128

# Standard Trailer Tags
TAG_CHECK_SUM = 10

# Session Tags
TAG_ENCRYPT_METHOD = 98
TAG_HEART_BT_INT = 108
TAG_RESET_SEQ_NUM_FLAG = 141
TAG_TEST_REQ_ID = 112
TAG_BEGIN_SEQ_NO = 7
TAG_END_SEQ_NO = 16
TAG_REF_SEQ_NUM = 45
TAG_REF_TAG_ID = 371
TAG_REF_MSG_TYPE = 372
TAG_SESSION_REJECT_REASON = 373
TAG_TEXT = 58
TAG_USERNAME = 553
TAG_PASSWORD = 554
TAG_DEFAULT_APPL_VER_ID = 1137

# Order Entry & Execution Tags
TAG_ACCOUNT = 1
TAG_AVG_PX = 6
TAG_CL_ORD_ID = 11
TAG_CUM_QTY = 14
TAG_CURRENCY = 15
TAG_EXEC_ID = 17
TAG_EXEC_INST = 18
TAG_LAST_PX = 31
TAG_LAST_QTY = 32
TAG_ORDER_ID = 37
TAG_ORDER_QTY = 38
TAG_ORD_STATUS = 39
TAG_ORD_TYPE = 40
TAG_ORIG_CL_ORD_ID = 41
TAG_PRICE = 44
TAG_SIDE = 54
TAG_SYMBOL = 55
TAG_TIME_IN_FORCE = 59
TAG_TRANSACT_TIME = 60
TAG_STOP_PX = 99
TAG_EX_DESTINATION = 100
TAG_ORD_REJ_REASON = 103
TAG_EXEC_TYPE = 150
TAG_LEAVES_QTY = 151
TAG_SECURITY_EXCHANGE = 207
TAG_NO_PARTY_IDS = 453
TAG_PARTY_ID = 448
TAG_PARTY_ID_SOURCE = 447
TAG_PARTY_ROLE = 452

# B3 Custom Tags
TAG_MEMO = 5149  # Anotação livre de mesa/algoritmo na B3

HEADER_TAGS_ORDER = [
    TAG_MSG_TYPE,
    TAG_APPL_VER_ID,
    TAG_SENDER_COMP_ID,
    TAG_TARGET_COMP_ID,
    TAG_MSG_SEQ_NUM,
    TAG_SENDING_TIME,
]


# ==============================================================================
# 2. Enumerações Canônicas
# ==============================================================================

class FixMsgType(str, Enum):
    """Tipos de mensagens FIX homologadas no PUMA Trading System."""
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    LOGOUT = "5"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    LOGON = "A"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    ORDER_CANCEL_REPLACE_REQUEST = "G"


class FixSide(str, Enum):
    """Sentido da oferta na B3."""
    BUY = "1"
    SELL = "2"


class FixOrdType(str, Enum):
    """Tipo da ordem."""
    MARKET = "1"
    LIMIT = "2"
    STOP = "3"
    STOP_LIMIT = "4"


class FixTimeInForce(str, Enum):
    """Validade temporal da oferta na câmara B3."""
    DAY = "0"        # No Dia
    GTC = "1"        # Good 'Til Canceled (Até Cancelar)
    IOC = "3"        # Immediate Or Cancel (Imediata ou Cancela)
    FOK = "4"        # Fill Or Kill (Tudo ou Nada)


class FixExecType(str, Enum):
    """Tipo do evento de execução emitido pela B3."""
    NEW = "0"
    PARTIAL_FILL = "1"
    FILL = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    STOPPED = "7"
    REJECTED = "8"
    SUSPENDED = "9"
    PENDING_NEW = "A"
    CALCULATED = "B"
    EXPIRED = "C"


class FixOrdStatus(str, Enum):
    """Status operacional da ordem no livro do PUMA."""
    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    DONE_FOR_DAY = "3"
    CANCELED = "4"
    REPLACED = "5"
    PENDING_CANCEL = "6"
    STOPPED = "7"
    REJECTED = "8"
    SUSPENDED = "9"
    PENDING_NEW = "A"
    CALCULATED = "B"
    EXPIRED = "C"


class FixPartyRole(int, Enum):
    """Papel do participante no modelo B3."""
    EXECUTING_BROKER = 1
    CLEARING_MEMBER = 4
    CLIENT_ID = 3
    EXECUTING_TRADER = 12


class SessionState(str, Enum):
    """Estado do autômato de sessão FIX."""
    DISCONNECTED = "DISCONNECTED"
    LOGON_SENT = "LOGON_SENT"
    ACTIVE = "ACTIVE"
    LOGOUT_SENT = "LOGOUT_SENT"


# ==============================================================================
# 3. Exceções Especializadas
# ==============================================================================

class FixError(Exception):
    """Erro base para operações com protocolo FIX."""
    pass


class FixProtocolError(FixError):
    """Violação estrutural de protocolo (ex.: ordenação de tags de cabeçalho/trailer)."""
    pass


class FixChecksumError(FixError):
    """Divergência matemática no Checksum da mensagem (Tag 10)."""
    pass


class FixBodyLengthError(FixError):
    """Divergência de contagem de bytes no BodyLength da mensagem (Tag 9)."""
    pass


class FixValidationError(FixError):
    """Violação de regras de validação de negócio ou dados obrigatórios."""
    pass


class FixSessionError(FixError):
    """Erro de gestão de sessão ou quebra de sequenciamento FIX."""
    pass


# ==============================================================================
# 4. Representação e Manipulação de Mensagem FIX (FixMessage)
# ==============================================================================

class FixMessage:
    """Representação canônica e determinística de uma mensagem FIX.
    
    Preserva a ordem exata de inserção de tags e suporta grupos repetidos
    (múltiplas ocorrências da mesma tag, como PartyIDs).
    """

    def __init__(self, fields: Optional[List[Tuple[int, str]]] = None) -> None:
        self.fields: List[Tuple[int, str]] = list(fields) if fields else []

    def get(self, tag: int, default: Optional[str] = None) -> Optional[str]:
        """Retorna o valor da primeira ocorrência da tag informada."""
        for t, v in self.fields:
            if t == tag:
                return v
        return default

    def get_int(self, tag: int, default: Optional[int] = None) -> Optional[int]:
        """Retorna o valor da tag convertido para inteiro."""
        val = self.get(tag)
        if val is None:
            return default
        try:
            return int(val)
        except ValueError:
            return default

    def get_float(self, tag: int, default: Optional[float] = None) -> Optional[float]:
        """Retorna o valor da tag convertido para ponto flutuante."""
        val = self.get(tag)
        if val is None:
            return default
        try:
            return float(val)
        except ValueError:
            return default

    def get_decimal(self, tag: int, default: Optional[Decimal] = None) -> Optional[Decimal]:
        """Retorna o valor da tag convertido para Decimal de alta precisão."""
        val = self.get(tag)
        if val is None:
            return default
        try:
            return Decimal(val)
        except Exception:
            return default

    def get_all(self, tag: int) -> List[str]:
        """Retorna todos os valores associados à tag informada."""
        return [v for t, v in self.fields if t == tag]

    def set(self, tag: int, value: Any) -> FixMessage:
        """Define ou substitui o primeiro valor da tag informada.
        
        Se a tag não existir, anexa ao final.
        """
        str_val = str(value.value if hasattr(value, "value") else value)
        for i, (t, _) in enumerate(self.fields):
            if t == tag:
                self.fields[i] = (tag, str_val)
                return self
        self.fields.append((tag, str_val))
        return self

    def append(self, tag: int, value: Any) -> FixMessage:
        """Anexa uma nova ocorrência da tag ao final (útil para grupos repetidos)."""
        str_val = str(value.value if hasattr(value, "value") else value)
        self.fields.append((tag, str_val))
        return self

    def remove(self, tag: int) -> FixMessage:
        """Remove todas as ocorrências da tag informada."""
        self.fields = [(t, v) for t, v in self.fields if t != tag]
        return self

    def has(self, tag: int) -> bool:
        """Indica se a tag está presente na mensagem."""
        return any(t == tag for t, _ in self.fields)

    @property
    def msg_type(self) -> Optional[str]:
        """Tipo da mensagem (Tag 35)."""
        return self.get(TAG_MSG_TYPE)

    @property
    def seq_num(self) -> Optional[int]:
        """Número sequencial da mensagem (Tag 34)."""
        return self.get_int(TAG_MSG_SEQ_NUM)

    @property
    def sender_comp_id(self) -> Optional[str]:
        """Identificador do remetente (Tag 49)."""
        return self.get(TAG_SENDER_COMP_ID)

    @property
    def target_comp_id(self) -> Optional[str]:
        """Identificador do destinatário (Tag 56)."""
        return self.get(TAG_TARGET_COMP_ID)

    @property
    def sending_time(self) -> Optional[str]:
        """Carimbo temporal de envio (Tag 52)."""
        return self.get(TAG_SENDING_TIME)

    @property
    def begin_string(self) -> str:
        """Versão do protocolo FIX (Tag 8)."""
        return self.get(TAG_BEGIN_STRING, "FIX.4.4")

    # --------------------------------------------------------------------------
    # Cálculo e Validação Determinística de Integridade (Tag 9 e Tag 10)
    # --------------------------------------------------------------------------

    def _get_body_fields(self) -> List[Tuple[int, str]]:
        """Retorna todos os campos intermediários (excluindo 8, 9 e 10), ordenando cabeçalho FIX padrão."""
        candidates = [(t, v) for t, v in self.fields if t not in (TAG_BEGIN_STRING, TAG_BODY_LENGTH, TAG_CHECK_SUM)]
        
        header_fields: List[Tuple[int, str]] = []
        for t in HEADER_TAGS_ORDER:
            for item in candidates:
                if item[0] == t and item not in header_fields:
                    header_fields.append(item)
                    break

        header_tags_set = {t for t, _ in header_fields}
        body_fields = [item for item in candidates if item[0] not in header_tags_set]

        return header_fields + body_fields

    def calculate_body_length(self, delimiter: bytes = SOH) -> int:
        """Calcula o tamanho exato do corpo da mensagem em bytes.
        
        Soma o tamanho de todos os campos que não sejam Tag 8, Tag 9 e Tag 10,
        incluindo seus respectivos delimitadores.
        """
        body_fields = self._get_body_fields()
        total_len = 0
        for t, v in body_fields:
            field_bytes = f"{t}={v}".encode("utf-8") + delimiter
            total_len += len(field_bytes)
        return total_len

    def calculate_checksum(self, delimiter: bytes = SOH) -> str:
        """Calcula o Checksum FIX (Tag 10) com base na mensagem montada.
        
        Calcula a soma de todos os bytes desde a Tag 8 até o delimitador anterior
        à Tag 10, módulo 256, formatado em 3 dígitos com zeros à esquerda.
        """
        # Garante integridade preliminar com Tag 9 calculada
        body_len = self.calculate_body_length(delimiter)
        begin_str = self.begin_string

        buffer = bytearray()
        buffer.extend(f"{TAG_BEGIN_STRING}={begin_str}".encode("utf-8") + delimiter)
        buffer.extend(f"{TAG_BODY_LENGTH}={body_len}".encode("utf-8") + delimiter)
        for t, v in self._get_body_fields():
            buffer.extend(f"{t}={v}".encode("utf-8") + delimiter)

        checksum_val = sum(buffer) % 256
        return f"{checksum_val:03d}"

    def encode(self, delimiter: bytes = SOH, compute_integrity: bool = True) -> bytes:
        """Serializa a mensagem FIX para fluxo binário de bytes com SOH.
        
        Se compute_integrity=True (padrão), recalcula deterministicamente
        o BodyLength (Tag 9) e o CheckSum (Tag 10).
        """
        if compute_integrity:
            body_len = self.calculate_body_length(delimiter)
            self.set(TAG_BODY_LENGTH, body_len)
            checksum = self.calculate_checksum(delimiter)
            self.set(TAG_CHECK_SUM, checksum)

        begin_str = self.begin_string
        body_len_val = self.get(TAG_BODY_LENGTH, str(self.calculate_body_length(delimiter)))
        checksum_val = self.get(TAG_CHECK_SUM, self.calculate_checksum(delimiter))

        buffer = bytearray()
        buffer.extend(f"{TAG_BEGIN_STRING}={begin_str}".encode("utf-8") + delimiter)
        buffer.extend(f"{TAG_BODY_LENGTH}={body_len_val}".encode("utf-8") + delimiter)
        for t, v in self._get_body_fields():
            buffer.extend(f"{t}={v}".encode("utf-8") + delimiter)
        buffer.extend(f"{TAG_CHECK_SUM}={checksum_val}".encode("utf-8") + delimiter)
        return bytes(buffer)

    def to_string(self, delimiter: str = "|", compute_integrity: bool = True) -> str:
        """Serializa a mensagem FIX para string com o delimitador especificado (ex.: pipe '|')."""
        delim_bytes = delimiter.encode("utf-8")
        raw_bytes = self.encode(delimiter=delim_bytes, compute_integrity=compute_integrity)
        return raw_bytes.decode("utf-8")

    @classmethod
    def decode(
        cls,
        raw: Union[bytes, str],
        delimiter: Optional[Union[bytes, str]] = None,
        validate_integrity: bool = True,
    ) -> FixMessage:
        """Desserializa e valida estritamente um pacote FIX textual ou binário.
        
        Verifica:
        1. Tag 8 (BeginString) como primeiro campo obrigatório.
        2. Tag 9 (BodyLength) como segundo campo obrigatório.
        3. Tag 10 (CheckSum) como último campo obrigatório.
        4. Exatidão matemática do BodyLength.
        5. Exatidão matemática do CheckSum mod 256.
        """
        if isinstance(raw, str):
            raw_bytes = raw.encode("latin1")
        else:
            raw_bytes = bytes(raw)

        raw_bytes = raw_bytes.strip(b"\r\n \t")
        if not raw_bytes:
            raise FixProtocolError("Mensagem FIX vazia recebida para decodificação.")

        # Detecção automática do delimitador se não fornecido
        if delimiter is None:
            if SOH in raw_bytes:
                active_delim = SOH
            elif PIPE in raw_bytes:
                active_delim = PIPE
            else:
                active_delim = SOH
        else:
            if isinstance(delimiter, str):
                active_delim = delimiter.encode("utf-8")
            else:
                active_delim = bytes(delimiter)

        # Divisão em partes respeitando delimitador
        parts = [p for p in raw_bytes.split(active_delim) if p]
        if len(parts) < 3:
            raise FixProtocolError(
                f"Mensagem FIX truncada ou inválida. Menos de 3 campos detectados: {parts}"
            )

        fields: List[Tuple[int, str]] = []
        for p in parts:
            if b"=" not in p:
                raise FixProtocolError(f"Campo FIX inválido sem sinal '=': {p.decode('latin1', errors='replace')}")
            tag_b, val_b = p.split(b"=", 1)
            try:
                tag_int = int(tag_b.decode("ascii"))
            except ValueError:
                raise FixProtocolError(f"Tag FIX não numérica: {tag_b.decode('latin1', errors='replace')}")
            fields.append((tag_int, val_b.decode("latin1", errors="replace")))

        # 1. Validação estrutural básica
        first_tag, _ = fields[0]
        second_tag, _ = fields[1]
        last_tag, stated_chk = fields[-1]

        if first_tag != TAG_BEGIN_STRING:
            raise FixProtocolError(
                f"Violação FIX: Tag 8 (BeginString) deve ser o primeiro campo. Encontrado: Tag {first_tag}"
            )
        if second_tag != TAG_BODY_LENGTH:
            raise FixProtocolError(
                f"Violação FIX: Tag 9 (BodyLength) deve ser o segundo campo. Encontrado: Tag {second_tag}"
            )
        if last_tag != TAG_CHECK_SUM:
            raise FixProtocolError(
                f"Violação FIX: Tag 10 (CheckSum) deve ser o último campo. Encontrado: Tag {last_tag}"
            )

        # 2. Validação estrita de integridade matemática
        if validate_integrity:
            # Encontra início e fim do corpo em bytes
            tag9_token = f"{TAG_BODY_LENGTH}={fields[1][1]}".encode("ascii") + active_delim
            tag9_idx = raw_bytes.find(tag9_token)
            if tag9_idx == -1:
                raise FixProtocolError("Inconsistência interna ao localizar Tag 9.")
            body_start = tag9_idx + len(tag9_token)

            tag10_token = f"{TAG_CHECK_SUM}={stated_chk}".encode("ascii")
            tag10_idx = raw_bytes.rfind(tag10_token)
            if tag10_idx == -1:
                raise FixProtocolError("Inconsistência interna ao localizar Tag 10.")
            body_end = tag10_idx  # O delimitador antes da tag 10 faz parte do corpo

            actual_body_len = body_end - body_start
            stated_body_len = int(fields[1][1])

            if actual_body_len != stated_body_len:
                raise FixBodyLengthError(
                    f"Divergência de BodyLength: declarado={stated_body_len}, calculado={actual_body_len} bytes."
                )

            # CheckSum: todos os bytes antes de '10='
            bytes_for_checksum = raw_bytes[:tag10_idx]
            calc_checksum_delim = sum(bytes_for_checksum) % 256
            # Caso a fixture use pipe | como representação visual de SOH \x01:
            bytes_soh_converted = bytes_for_checksum.replace(PIPE, SOH)
            calc_checksum_soh = sum(bytes_soh_converted) % 256

            stated_chk_int = int(stated_chk)
            if stated_chk_int not in (calc_checksum_delim, calc_checksum_soh):
                expected_str = f"{calc_checksum_delim:03d}"
                raise FixChecksumError(
                    f"Divergência de CheckSum: declarado='{stated_chk}', calculado='{expected_str}'."
                )

        return cls(fields=fields)

    def __repr__(self) -> str:
        return f"<FixMessage MsgType={self.msg_type} SeqNum={self.seq_num} Fields={len(self.fields)}>"


# ==============================================================================
# 5. Gestor de Sessão FIX (FixSessionManager)
# ==============================================================================

class FixSessionManager:
    """Gerenciador determinístico de estado e sequenciamento de sessão FIX.
    
    Controla logon, heartbeats, test requests, recuperação de gaps com resend
    e encerramento formal de sessão.
    """

    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str = "BVMF",
        begin_string: str = "FIX.4.4",
        heartbt_int: int = 30,
    ) -> None:
        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.begin_string = begin_string
        self.heartbt_int = heartbt_int
        self.out_seq_num = 1
        self.in_seq_num = 1
        self.state = SessionState.DISCONNECTED
        self.sent_messages: Dict[int, FixMessage] = {}
        self.received_messages: List[FixMessage] = []

    def _now_utc_str(self) -> str:
        """Gera timestamp UTC no formato canônico FIX."""
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

    def _prepare_header(self, msg: FixMessage, msg_type: str) -> FixMessage:
        """Popula os campos padrão de cabeçalho FIX."""
        msg.set(TAG_BEGIN_STRING, self.begin_string)
        msg.set(TAG_MSG_TYPE, msg_type)
        msg.set(TAG_SENDER_COMP_ID, self.sender_comp_id)
        msg.set(TAG_TARGET_COMP_ID, self.target_comp_id)
        msg.set(TAG_MSG_SEQ_NUM, self.out_seq_num)
        msg.set(TAG_SENDING_TIME, self._now_utc_str())
        self.sent_messages[self.out_seq_num] = msg
        self.out_seq_num += 1
        return msg

    def create_logon(
        self,
        reset_seq: bool = False,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> FixMessage:
        """Cria mensagem de autenticação Logon (35=A)."""
        if reset_seq:
            self.out_seq_num = 1
            self.in_seq_num = 1

        msg = FixMessage()
        msg.set(TAG_ENCRYPT_METHOD, 0)
        msg.set(TAG_HEART_BT_INT, self.heartbt_int)
        msg.set(TAG_RESET_SEQ_NUM_FLAG, "Y" if reset_seq else "N")
        if username:
            msg.set(TAG_USERNAME, username)
        if password:
            msg.set(TAG_PASSWORD, password)
        if self.begin_string == "FIXT.1.1":
            msg.set(TAG_DEFAULT_APPL_VER_ID, "9")

        self._prepare_header(msg, FixMsgType.LOGON)
        self.state = SessionState.LOGON_SENT
        return msg

    def create_heartbeat(self, test_req_id: Optional[str] = None) -> FixMessage:
        """Cria mensagem de Heartbeat (35=0), opcionalmente respondendo a um TestRequest."""
        msg = FixMessage()
        if test_req_id:
            msg.set(TAG_TEST_REQ_ID, test_req_id)
        self._prepare_header(msg, FixMsgType.HEARTBEAT)
        return msg

    def create_test_request(self, test_req_id: Optional[str] = None) -> FixMessage:
        """Cria mensagem de TestRequest (35=1) para forçar resposta de presença."""
        req_id = test_req_id or f"TEST_{self.out_seq_num}"
        msg = FixMessage()
        msg.set(TAG_TEST_REQ_ID, req_id)
        self._prepare_header(msg, FixMsgType.TEST_REQUEST)
        return msg

    def create_resend_request(self, begin_seq: int, end_seq: int = 0) -> FixMessage:
        """Cria pedido de retransmissão ResendRequest (35=2)."""
        msg = FixMessage()
        msg.set(TAG_BEGIN_SEQ_NO, begin_seq)
        msg.set(TAG_END_SEQ_NO, end_seq)
        self._prepare_header(msg, FixMsgType.RESEND_REQUEST)
        return msg

    def create_reject(
        self,
        ref_seq_num: int,
        ref_tag: Optional[int] = None,
        ref_msg_type: Optional[str] = None,
        reason: int = 0,
        text: str = "",
    ) -> FixMessage:
        """Cria mensagem de Reject de sessão (35=3)."""
        msg = FixMessage()
        msg.set(TAG_REF_SEQ_NUM, ref_seq_num)
        if ref_tag:
            msg.set(TAG_REF_TAG_ID, ref_tag)
        if ref_msg_type:
            msg.set(TAG_REF_MSG_TYPE, ref_msg_type)
        msg.set(TAG_SESSION_REJECT_REASON, reason)
        if text:
            msg.set(TAG_TEXT, text)
        self._prepare_header(msg, FixMsgType.REJECT)
        return msg

    def create_logout(self, text: str = "") -> FixMessage:
        """Cria mensagem de encerramento de sessão Logout (35=5)."""
        msg = FixMessage()
        if text:
            msg.set(TAG_TEXT, text)
        self._prepare_header(msg, FixMsgType.LOGOUT)
        self.state = SessionState.LOGOUT_SENT
        return msg

    def handle_inbound(self, msg: FixMessage) -> Tuple[str, Optional[FixMessage]]:
        """Processa mensagem recebida, validando sequência e autômato de estado.
        
        Retorna (status_descritivo, mensagem_de_resposta_opcional).
        """
        self.received_messages.append(msg)
        seq = msg.seq_num
        if seq is None:
            raise FixSessionError("Mensagem inbound recebida sem Tag 34 (MsgSeqNum).")

        mtype = msg.msg_type

        # Tratamento especial de Logon com reset de sequência
        if mtype == FixMsgType.LOGON and msg.get(TAG_RESET_SEQ_NUM_FLAG) == "Y":
            self.in_seq_num = 1

        # Verificação estrita de número de sequência
        if seq > self.in_seq_num:
            gap_begin = self.in_seq_num
            # Detectou salto na sequência -> solicita ResendRequest
            resend = self.create_resend_request(begin_seq=gap_begin, end_seq=0)
            return ("GAP_DETECTED", resend)

        if seq < self.in_seq_num:
            # Mensagem com sequência menor que o esperado
            reject = self.create_reject(
                ref_seq_num=seq,
                reason=5,  # Value is incorrect (out of range) for this tag
                text=f"MsgSeqNum={seq} menor que esperado {self.in_seq_num}",
            )
            return ("SEQUENCE_TOO_LOW", reject)

        # Sequência esperada bateu perfeitamente -> avança in_seq_num
        self.in_seq_num += 1

        if mtype == FixMsgType.LOGON:
            self.state = SessionState.ACTIVE
            return ("LOGON_ACCEPTED", None)

        if mtype == FixMsgType.HEARTBEAT:
            return ("HEARTBEAT_RECEIVED", None)

        if mtype == FixMsgType.TEST_REQUEST:
            test_req_id = msg.get(TAG_TEST_REQ_ID)
            hb = self.create_heartbeat(test_req_id=test_req_id)
            return ("TEST_REQUEST_ANSWERED", hb)

        if mtype == FixMsgType.LOGOUT:
            self.state = SessionState.DISCONNECTED
            return ("LOGOUT_ACCEPTED", None)

        return ("APPLICATION_MESSAGE", None)


# ==============================================================================
# 6. Cliente Puma Order Entry (PumaOrderEntryClient - FL-011)
# ==============================================================================

class PumaOrderEntryClient:
    """Cliente de negociação interativa para o PUMA Trading System da B3.
    
    Implementa validações estritas de negócio antes do envio de ordens
    (NewOrderSingle, Cancel, CancelReplace) e monitora ordens ativas.
    """

    def __init__(
        self,
        session: FixSessionManager,
        account: str,
        executing_broker: str,
        clearing_member: str,
    ) -> None:
        self.session = session
        self.account = account
        self.executing_broker = executing_broker
        self.clearing_member = clearing_member
        self.active_orders: Dict[str, Dict[str, Any]] = {}

    def new_order_single(
        self,
        symbol: str,
        side: Union[FixSide, str],
        order_qty: int,
        price: Optional[Union[float, Decimal]] = None,
        ord_type: Union[FixOrdType, str] = FixOrdType.LIMIT,
        time_in_force: Union[FixTimeInForce, str] = FixTimeInForce.DAY,
        cl_ord_id: Optional[str] = None,
        stop_px: Optional[Union[float, Decimal]] = None,
        memo: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> FixMessage:
        """Cria e valida rigorosamente uma oferta NewOrderSingle (35=D)."""
        # Validações de integridade de negócio
        if order_qty <= 0:
            raise FixValidationError(f"OrderQty deve ser estritamente positivo: {order_qty}")

        side_str = str(side.value if isinstance(side, FixSide) else side)
        if side_str not in (FixSide.BUY.value, FixSide.SELL.value):
            raise FixValidationError(f"Side inválido: {side_str}. Esperado 1 (Buy) ou 2 (Sell).")

        ord_type_str = str(ord_type.value if isinstance(ord_type, FixOrdType) else ord_type)
        if ord_type_str == FixOrdType.LIMIT.value:
            if price is None or float(price) <= 0:
                raise FixValidationError("Ordens limitadas (OrdType=2) exigem Price estritamente positivo.")

        if ord_type_str in (FixOrdType.STOP.value, FixOrdType.STOP_LIMIT.value):
            if stop_px is None or float(stop_px) <= 0:
                raise FixValidationError("Ordens Stop exigem StopPx estritamente positivo.")

        tif_str = str(time_in_force.value if isinstance(time_in_force, FixTimeInForce) else time_in_force)
        order_id_val = cl_ord_id or f"ORD_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"

        msg = FixMessage()
        msg.set(TAG_CL_ORD_ID, order_id_val)
        msg.set(TAG_ACCOUNT, self.account)
        msg.set(TAG_SYMBOL, symbol.upper())
        msg.set(TAG_SIDE, side_str)
        msg.set(TAG_TRANSACT_TIME, self.session._now_utc_str())
        msg.set(TAG_ORDER_QTY, order_qty)
        msg.set(TAG_ORD_TYPE, ord_type_str)
        if price is not None:
            msg.set(TAG_PRICE, f"{float(price):.2f}")
        if stop_px is not None:
            msg.set(TAG_STOP_PX, f"{float(stop_px):.2f}")
        msg.set(TAG_TIME_IN_FORCE, tif_str)
        msg.set(TAG_EX_DESTINATION, "BVMF")
        msg.set(TAG_SECURITY_EXCHANGE, "BVMF")

        # Repasse e identificadores de participante (PartyIDs)
        parties = [
            (self.executing_broker, FixPartyRole.EXECUTING_BROKER.value),
            (self.clearing_member, FixPartyRole.CLEARING_MEMBER.value),
        ]
        if client_id:
            parties.append((client_id, FixPartyRole.CLIENT_ID.value))

        msg.set(TAG_NO_PARTY_IDS, len(parties))
        for pid, role in parties:
            msg.append(TAG_PARTY_ID, pid)
            msg.append(TAG_PARTY_ID_SOURCE, "D")
            msg.append(TAG_PARTY_ROLE, role)

        # Tag customizada B3
        if memo:
            msg.set(TAG_MEMO, memo)

        self.session._prepare_header(msg, FixMsgType.NEW_ORDER_SINGLE)

        # Registra controle interno da ordem
        self.active_orders[order_id_val] = {
            "cl_ord_id": order_id_val,
            "order_id": None,
            "symbol": symbol.upper(),
            "side": side_str,
            "order_qty": order_qty,
            "price": float(price) if price is not None else None,
            "leaves_qty": order_qty,
            "cum_qty": 0,
            "avg_px": 0.0,
            "status": FixOrdStatus.PENDING_NEW.value,
        }

        return msg

    def cancel_order(
        self,
        orig_cl_ord_id: str,
        symbol: str,
        side: Union[FixSide, str],
        order_qty: int,
        cl_ord_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> FixMessage:
        """Cria pedido de cancelamento total OrderCancelRequest (35=F)."""
        cancel_id = cl_ord_id or f"CANC_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        side_str = str(side.value if isinstance(side, FixSide) else side)

        msg = FixMessage()
        msg.set(TAG_CL_ORD_ID, cancel_id)
        msg.set(TAG_ORIG_CL_ORD_ID, orig_cl_ord_id)
        if order_id:
            msg.set(TAG_ORDER_ID, order_id)
        msg.set(TAG_ACCOUNT, self.account)
        msg.set(TAG_SYMBOL, symbol.upper())
        msg.set(TAG_SIDE, side_str)
        msg.set(TAG_TRANSACT_TIME, self.session._now_utc_str())
        msg.set(TAG_ORDER_QTY, order_qty)

        self.session._prepare_header(msg, FixMsgType.ORDER_CANCEL_REQUEST)
        return msg

    def cancel_replace_order(
        self,
        orig_cl_ord_id: str,
        symbol: str,
        side: Union[FixSide, str],
        new_qty: int,
        new_price: Union[float, Decimal],
        ord_type: Union[FixOrdType, str] = FixOrdType.LIMIT,
        time_in_force: Union[FixTimeInForce, str] = FixTimeInForce.DAY,
        cl_ord_id: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> FixMessage:
        """Cria pedido de alteração de oferta OrderCancelReplaceRequest (35=G)."""
        replace_id = cl_ord_id or f"MOD_{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        side_str = str(side.value if isinstance(side, FixSide) else side)
        ord_type_str = str(ord_type.value if isinstance(ord_type, FixOrdType) else ord_type)
        tif_str = str(time_in_force.value if isinstance(time_in_force, FixTimeInForce) else time_in_force)

        msg = FixMessage()
        msg.set(TAG_CL_ORD_ID, replace_id)
        msg.set(TAG_ORIG_CL_ORD_ID, orig_cl_ord_id)
        if order_id:
            msg.set(TAG_ORDER_ID, order_id)
        msg.set(TAG_ACCOUNT, self.account)
        msg.set(TAG_SYMBOL, symbol.upper())
        msg.set(TAG_SIDE, side_str)
        msg.set(TAG_TRANSACT_TIME, self.session._now_utc_str())
        msg.set(TAG_ORDER_QTY, new_qty)
        msg.set(TAG_ORD_TYPE, ord_type_str)
        msg.set(TAG_PRICE, f"{float(new_price):.2f}")
        msg.set(TAG_TIME_IN_FORCE, tif_str)

        self.session._prepare_header(msg, FixMsgType.ORDER_CANCEL_REPLACE_REQUEST)
        return msg

    def process_execution_report(self, msg: FixMessage) -> Dict[str, Any]:
        """Processa um ExecutionReport (35=8) recebido da B3 e atualiza ordens ativas."""
        if msg.msg_type != FixMsgType.EXECUTION_REPORT:
            raise FixValidationError(f"Esperado ExecutionReport (35=8), recebido: {msg.msg_type}")

        cl_ord_id = msg.get(TAG_CL_ORD_ID, "")
        order_id = msg.get(TAG_ORDER_ID, "")
        exec_id = msg.get(TAG_EXEC_ID, "")
        exec_type = msg.get(TAG_EXEC_TYPE, "")
        ord_status = msg.get(TAG_ORD_STATUS, "")
        symbol = msg.get(TAG_SYMBOL, "")
        side = msg.get(TAG_SIDE, "")
        leaves_qty = msg.get_int(TAG_LEAVES_QTY, 0)
        cum_qty = msg.get_int(TAG_CUM_QTY, 0)
        avg_px = msg.get_float(TAG_AVG_PX, 0.0)
        last_px = msg.get_float(TAG_LAST_PX)
        last_qty = msg.get_int(TAG_LAST_QTY)
        text = msg.get(TAG_TEXT, "")

        record = {
            "cl_ord_id": cl_ord_id,
            "order_id": order_id,
            "exec_id": exec_id,
            "exec_type": exec_type,
            "ord_status": ord_status,
            "symbol": symbol,
            "side": side,
            "leaves_qty": leaves_qty,
            "cum_qty": cum_qty,
            "avg_px": avg_px,
            "last_px": last_px,
            "last_qty": last_qty,
            "text": text,
        }

        # Atualiza o livro de ordens ativas
        if cl_ord_id in self.active_orders:
            target = self.active_orders[cl_ord_id]
            target["order_id"] = order_id
            target["leaves_qty"] = leaves_qty
            target["cum_qty"] = cum_qty
            target["avg_px"] = avg_px
            target["status"] = ord_status

            if ord_status in (FixOrdStatus.FILLED.value, FixOrdStatus.CANCELED.value, FixOrdStatus.REJECTED.value):
                # Mantém histórico mas saldo zerado
                target["leaves_qty"] = 0

        return record


# ==============================================================================
# 7. Consumidor Drop Copy e Agregação de Risco (DropCopyStreamConsumer - FL-012)
# ==============================================================================

class DropCopyStreamConsumer:
    """Consumidor passivo do stream Drop Copy da B3 para monitoramento de risco intradiário.
    
    Agrega posições líquidas por ativo, volume negociado, VWAP e acompanha
    ordens abertas (working orders) e rejeições em tempo real.
    """

    def __init__(self) -> None:
        self.executions: List[Dict[str, Any]] = []
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.accounts_risk: Dict[str, Dict[str, Any]] = {}
        self.rejections: List[Dict[str, Any]] = []
        self.working_orders: Dict[str, Dict[str, Any]] = {}

    def _get_or_init_position(self, symbol: str) -> Dict[str, Any]:
        """Inicializa ou obtém registro de posição consolidada do ativo."""
        sym = symbol.upper()
        if sym not in self.positions:
            self.positions[sym] = {
                "symbol": sym,
                "net_quantity": 0,
                "buy_volume": 0,
                "sell_volume": 0,
                "gross_bought_value": Decimal("0.0"),
                "gross_sold_value": Decimal("0.0"),
                "total_trades": 0,
                "vwap_buy": 0.0,
                "vwap_sell": 0.0,
            }
        return self.positions[sym]

    def _get_or_init_account(self, account: str) -> Dict[str, Any]:
        """Inicializa ou obtém sumário de risco da conta."""
        acc = account or "DEFAULT"
        if acc not in self.accounts_risk:
            self.accounts_risk[acc] = {
                "account": acc,
                "total_orders": 0,
                "filled_orders": 0,
                "canceled_orders": 0,
                "rejected_orders": 0,
                "working_orders_count": 0,
                "gross_exposure": Decimal("0.0"),
            }
        return self.accounts_risk[acc]

    def consume_message(self, msg_or_raw: Union[FixMessage, str, bytes]) -> Optional[Dict[str, Any]]:
        """Ingere e consolida uma mensagem individual de Drop Copy.
        
        Suporta instâncias de FixMessage, strings com delimitador SOH ou pipe,
        ou bytes crus de rede.
        """
        if isinstance(msg_or_raw, FixMessage):
            msg = msg_or_raw
        else:
            msg = FixMessage.decode(msg_or_raw)

        # Drop Copy foca em ExecutionReport (35=8) e OrderCancelReject (35=9)
        mtype = msg.msg_type
        if mtype != FixMsgType.EXECUTION_REPORT:
            return None

        cl_ord_id = msg.get(TAG_CL_ORD_ID, "")
        order_id = msg.get(TAG_ORDER_ID, "")
        exec_id = msg.get(TAG_EXEC_ID, "")
        exec_type = msg.get(TAG_EXEC_TYPE, "")
        ord_status = msg.get(TAG_ORD_STATUS, "")
        symbol = msg.get(TAG_SYMBOL, "").upper()
        side = msg.get(TAG_SIDE, "")
        order_qty = msg.get_int(TAG_ORDER_QTY, 0)
        leaves_qty = msg.get_int(TAG_LEAVES_QTY, 0)
        cum_qty = msg.get_int(TAG_CUM_QTY, 0)
        avg_px = msg.get_float(TAG_AVG_PX, 0.0)
        last_px = msg.get_float(TAG_LAST_PX)
        last_qty = msg.get_int(TAG_LAST_QTY, 0)
        account = msg.get(TAG_ACCOUNT, "DEFAULT")
        text = msg.get(TAG_TEXT, "")
        transact_time = msg.get(TAG_TRANSACT_TIME, "")

        record = {
            "cl_ord_id": cl_ord_id,
            "order_id": order_id,
            "exec_id": exec_id,
            "exec_type": exec_type,
            "ord_status": ord_status,
            "symbol": symbol,
            "side": side,
            "order_qty": order_qty,
            "leaves_qty": leaves_qty,
            "cum_qty": cum_qty,
            "avg_px": avg_px,
            "last_px": last_px,
            "last_qty": last_qty,
            "account": account,
            "transact_time": transact_time,
            "text": text,
        }
        self.executions.append(record)

        pos = self._get_or_init_position(symbol)
        acc_summary = self._get_or_init_account(account)

        # 1. Tratamento de Execuções e Fills
        if exec_type in (FixExecType.PARTIAL_FILL.value, FixExecType.FILL.value) and last_qty > 0 and last_px:
            trade_val = Decimal(str(last_qty)) * Decimal(str(last_px))
            pos["total_trades"] += 1
            acc_summary["gross_exposure"] += trade_val

            if side == FixSide.BUY.value:
                pos["net_quantity"] += last_qty
                pos["buy_volume"] += last_qty
                pos["gross_bought_value"] += trade_val
                if pos["buy_volume"] > 0:
                    pos["vwap_buy"] = float(pos["gross_bought_value"] / Decimal(pos["buy_volume"]))
            elif side == FixSide.SELL.value:
                pos["net_quantity"] -= last_qty
                pos["sell_volume"] += last_qty
                pos["gross_sold_value"] += trade_val
                if pos["sell_volume"] > 0:
                    pos["vwap_sell"] = float(pos["gross_sold_value"] / Decimal(pos["sell_volume"]))

        # 2. Gestão de Ordens Abertas (Working Orders)
        if leaves_qty > 0 and ord_status in (FixOrdStatus.NEW.value, FixOrdStatus.PARTIALLY_FILLED.value):
            self.working_orders[cl_ord_id] = record
        else:
            if cl_ord_id in self.working_orders:
                del self.working_orders[cl_ord_id]

        # 3. Estatísticas por Conta
        acc_summary["working_orders_count"] = len(
            [o for o in self.working_orders.values() if o["account"] == account]
        )
        if ord_status == FixOrdStatus.FILLED.value:
            acc_summary["filled_orders"] += 1
        elif ord_status == FixOrdStatus.CANCELED.value:
            acc_summary["canceled_orders"] += 1
        elif ord_status == FixOrdStatus.REJECTED.value:
            acc_summary["rejected_orders"] += 1
            self.rejections.append(record)

        return record

    def consume_stream(self, stream: Iterable[Union[FixMessage, str, bytes]]) -> List[Dict[str, Any]]:
        """Ingere uma sequência/lote de mensagens Drop Copy."""
        results = []
        for item in stream:
            rec = self.consume_message(item)
            if rec:
                results.append(rec)
        return results

    def get_symbol_position(self, symbol: str) -> Dict[str, Any]:
        """Retorna a posição e métricas consolidadas do ativo."""
        pos = self._get_or_init_position(symbol)
        # Formata valores monetários para conveniência
        return {
            "symbol": pos["symbol"],
            "net_quantity": pos["net_quantity"],
            "buy_volume": pos["buy_volume"],
            "sell_volume": pos["sell_volume"],
            "gross_bought_value": float(pos["gross_bought_value"]),
            "gross_sold_value": float(pos["gross_sold_value"]),
            "total_trades": pos["total_trades"],
            "vwap_buy": round(pos["vwap_buy"], 4),
            "vwap_sell": round(pos["vwap_sell"], 4),
        }

    def get_account_risk_summary(self, account: Optional[str] = None) -> Dict[str, Any]:
        """Retorna o consolidado de risco intradiário por conta ou global."""
        if account:
            acc = self._get_or_init_account(account)
            return {
                "account": acc["account"],
                "working_orders_count": acc["working_orders_count"],
                "filled_orders": acc["filled_orders"],
                "canceled_orders": acc["canceled_orders"],
                "rejected_orders": acc["rejected_orders"],
                "gross_exposure": float(acc["gross_exposure"]),
            }

        # Consolidado geral
        total_working = len(self.working_orders)
        total_filled = sum(a["filled_orders"] for a in self.accounts_risk.values())
        total_canceled = sum(a["canceled_orders"] for a in self.accounts_risk.values())
        total_rejected = sum(a["rejected_orders"] for a in self.accounts_risk.values())
        total_exposure = float(sum(a["gross_exposure"] for a in self.accounts_risk.values()))

        return {
            "total_accounts": len(self.accounts_risk),
            "working_orders_count": total_working,
            "filled_orders": total_filled,
            "canceled_orders": total_canceled,
            "rejected_orders": total_rejected,
            "gross_exposure": total_exposure,
            "total_positions_tracked": len(self.positions),
        }

    def get_all_executions(self) -> List[Dict[str, Any]]:
        """Retorna o histórico completo de execuções capturadas."""
        return list(self.executions)

    def export_risk_snapshot(self) -> Dict[str, Any]:
        """Exporta um snapshot completo estruturado do estado de risco."""
        return {
            "summary": self.get_account_risk_summary(),
            "positions": {sym: self.get_symbol_position(sym) for sym in self.positions},
            "working_orders": list(self.working_orders.values()),
            "rejections": list(self.rejections),
            "total_executions": len(self.executions),
        }
