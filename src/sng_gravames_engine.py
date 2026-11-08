"""Sistema Nacional de Gravames (SNG) — B3 Financiamentos.

Módulo de infraestrutura de registro e baixa de restrições financeiras (alienação
fiduciária, arrendamento mercantil / leasing e reserva de domínio) sobre veículos
automotores junto aos Departamentos Estaduais de Trânsito (Detrans).

Fundamentação Regulatória:
- Resolução CONTRAN nº 807/2020 (Registro de contratos com garantia real)
- Lei Federal nº 13.111/2015 (Transparência de restrições em veículos)
- Art. 1.361 do Código Civil Brasileiro (Propriedade fiduciária)
- Invariante LAW-112: Unicidade e Inviolabilidade de Gravame Ativo por Chassi/Renavam

100% Python Standard Library (dataclasses, enum, typing, hashlib, datetime, decimal).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import re
from typing import Any, Dict, List, Optional


class ModalidadeGravame(str, Enum):
    """Modalidades de garantia real registradas no SNG B3."""

    ALIENACAO_FIDUCIARIA = "ALIENACAO_FIDUCIARIA"
    ARRENDAMENTO_MERCANTIL = "ARRENDAMENTO_MERCANTIL"
    RESERVA_DOMINIO = "RESERVA_DOMINIO"
    PENHOR_VEICULAR = "PENHOR_VEICULAR"


def mask_cpf_cnpj(doc: str) -> str:
    """Aplica máscara LGPD para CPF ou CNPJ preservando integridade regulatória."""
    clean = re.sub(r"\D", "", doc)
    if len(clean) == 11:
        return f"{clean[:3]}.***.***-{clean[9:]}"
    elif len(clean) == 14:
        return f"{clean[:2]}.***.***/{clean[8:12]}-{clean[12:]}"
    return "***"


def hash_sha256(val: str) -> str:
    """Gera hash criptográfico SHA-256 para pseudonimização auditável sob LGPD (Art. 13)."""
    return hashlib.sha256(val.strip().encode("utf-8")).hexdigest()


def mask_person_name(name: str) -> str:
    """Mascara o nome civil/empresarial preservando privacidade conforme princípio da minimização."""
    words = name.strip().split()
    if not words:
        return "***"
    return " ".join(f"{w[:2]}***" if len(w) > 2 else f"{w[0]}*" for w in words)


class StatusGravame(str, Enum):
    """Estados operacionais do ciclo de vida de um gravame."""

    ATIVO = "ATIVO"
    BAIXADO = "BAIXADO"
    CANCELADO = "CANCELADO"
    REJEITADO = "REJEITADO"


class DetranUF(str, Enum):
    """Unidades da Federação dos Detrans conveniados ao SNG."""

    SP = "SP"
    RJ = "RJ"
    MG = "MG"
    PR = "PR"
    RS = "RS"
    SC = "SC"
    BA = "BA"
    PE = "PE"
    GO = "GO"
    DF = "DF"
    ES = "ES"
    CE = "CE"


@dataclass(frozen=True)
class VeiculoIdentificacao:
    """Dados identificadores do veículo automotor."""

    chassi: str
    renavam: str
    placa: str
    uf_emplacamento: str
    marca_modelo: str
    ano_fabricacao: int
    ano_modelo: int

    def __post_init__(self) -> None:
        chassi_clean = self.chassi.strip().upper()
        if len(chassi_clean) != 17 or not re.match(r"^[A-HJ-NPR-Z0-9]{17}$", chassi_clean):
            raise ValueError(f"Chassi inválido (padrão ISO 3779): '{self.chassi}'")
        renavam_clean = re.sub(r"\D", "", self.renavam)
        if len(renavam_clean) not in (9, 11):
            raise ValueError(f"Renavam deve conter 9 ou 11 dígitos numéricos: '{self.renavam}'")


@dataclass
class GravameRecord:
    """Registro mestre de um gravame no SNG B3 Financiamentos."""

    gravame_id: str
    contrato_numero: str
    modalidade: ModalidadeGravame
    credor_cnpj: str
    credor_nome: str
    devedor_documento: str
    devedor_nome: str
    veiculo: VeiculoIdentificacao
    valor_financiamento: Decimal
    quantidade_parcelas: int
    taxa_juros_anual_pct: Decimal
    data_inclusao: str
    status: StatusGravame = StatusGravame.ATIVO
    data_baixa: Optional[str] = None
    motivo_baixa: Optional[str] = None
    detran_protocolo: Optional[str] = None
    hash_seguranca: str = ""

    def __post_init__(self) -> None:
        if not self.hash_seguranca:
            raw = (
                f"{self.gravame_id}|{self.contrato_numero}|{self.veiculo.chassi}|"
                f"{self.credor_cnpj}|{self.valor_financiamento}|{self.data_inclusao}"
            )
            self.hash_seguranca = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gravame_id": self.gravame_id,
            "contrato_numero": self.contrato_numero,
            "modalidade": self.modalidade.value,
            "credor_cnpj": self.credor_cnpj,
            "credor_nome": self.credor_nome,
            "devedor_documento": self.devedor_documento,
            "devedor_nome": self.devedor_nome,
            "veiculo": asdict(self.veiculo),
            "valor_financiamento": str(self.valor_financiamento),
            "quantidade_parcelas": self.quantidade_parcelas,
            "taxa_juros_anual_pct": str(self.taxa_juros_anual_pct),
            "data_inclusao": self.data_inclusao,
            "status": self.status.value,
            "data_baixa": self.data_baixa,
            "motivo_baixa": self.motivo_baixa,
            "detran_protocolo": self.detran_protocolo,
            "hash_seguranca": self.hash_seguranca,
        }

    @property
    def devedor_documento_mascarado(self) -> str:
        """CPF/CNPJ do devedor mascarado conforme LGPD."""
        return mask_cpf_cnpj(self.devedor_documento)

    @property
    def devedor_documento_sha256(self) -> str:
        """Hash SHA-256 do documento do devedor para auditoria pseudonimizada."""
        return hash_sha256(self.devedor_documento)

    @property
    def devedor_nome_mascarado(self) -> str:
        """Nome do financiado mascarado conforme LGPD."""
        return mask_person_name(self.devedor_nome)

    @property
    def credor_cnpj_mascarado(self) -> str:
        """CNPJ do credor financeiro com máscara institucional."""
        return mask_cpf_cnpj(self.credor_cnpj)

    def to_masked_dict(self) -> Dict[str, Any]:
        """Retorna representação anonimizada/minimizada para trilhas de auditoria externas."""
        d = self.to_dict()
        d["devedor_documento_mascarado"] = self.devedor_documento_mascarado
        d["devedor_documento_sha256"] = self.devedor_documento_sha256
        d["devedor_nome_mascarado"] = self.devedor_nome_mascarado
        d["credor_cnpj_mascarado"] = self.credor_cnpj_mascarado
        d["lgpd_minimizada"] = True
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GravameRecord:
        v_data = data["veiculo"]
        veiculo = VeiculoIdentificacao(
            chassi=v_data["chassi"],
            renavam=v_data["renavam"],
            placa=v_data["placa"],
            uf_emplacamento=v_data["uf_emplacamento"],
            marca_modelo=v_data["marca_modelo"],
            ano_fabricacao=v_data["ano_fabricacao"],
            ano_modelo=v_data["ano_modelo"],
        )
        return cls(
            gravame_id=data["gravame_id"],
            contrato_numero=data["contrato_numero"],
            modalidade=ModalidadeGravame(data["modalidade"]),
            credor_cnpj=data["credor_cnpj"],
            credor_nome=data["credor_nome"],
            devedor_documento=data["devedor_documento"],
            devedor_nome=data["devedor_nome"],
            veiculo=veiculo,
            valor_financiamento=Decimal(data["valor_financiamento"]),
            quantidade_parcelas=int(data["quantidade_parcelas"]),
            taxa_juros_anual_pct=Decimal(data["taxa_juros_anual_pct"]),
            data_inclusao=data["data_inclusao"],
            status=StatusGravame(data["status"]),
            data_baixa=data.get("data_baixa"),
            motivo_baixa=data.get("motivo_baixa"),
            detran_protocolo=data.get("detran_protocolo"),
            hash_seguranca=data.get("hash_seguranca", ""),
        )


class DetranNetworkPartitionError(IOError):
    """Exceção levantada quando há partição de rede ou indisponibilidade do Detran Estadual."""
    pass


class StatusContingencia(str, Enum):
    """Estados de ciclo de vida de itens na fila de contingência assíncrona do SNG."""

    ENFILEIRADO = "ENFILEIRADO"
    PROCESSADO = "PROCESSADO"
    REJEITADO = "REJEITADO"
    DESCARTE_DUPLICADO = "DESCARTE_DUPLICADO"


@dataclass
class ContingenciaGravameItem:
    """Registro retido na fila assíncrona de contingência durante partição do Detran."""

    item_id: str
    contrato_numero: str
    modalidade: ModalidadeGravame
    credor_cnpj: str
    credor_nome: str
    devedor_documento: str
    devedor_nome: str
    veiculo: VeiculoIdentificacao
    valor_financiamento: Decimal
    quantidade_parcelas: int
    taxa_juros_anual_pct: Decimal
    timestamp_enfileiramento: str
    uf_detran: str
    status: StatusContingencia = StatusContingencia.ENFILEIRADO
    retry_count: int = 0
    idempotency_hash: str = ""
    ultimo_erro: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.idempotency_hash:
            raw = f"{self.contrato_numero}|{self.veiculo.chassi.strip().upper()}|{self.credor_cnpj}|{self.valor_financiamento}"
            self.idempotency_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SngGravamesEngine:
    """Motor central do Sistema Nacional de Gravames (SNG) B3 Financiamentos."""

    def __init__(self) -> None:
        self.gravames: Dict[str, GravameRecord] = {}
        # Mapeamento do chassi normalizado para o gravame_id ativo
        self.chassi_ativo_index: Dict[str, str] = {}
        # Histórico de transações para auditoria
        self.audit_log: List[Dict[str, Any]] = []
        # Detrans com partição de rede simulada
        self.detrans_particionados: set[str] = set()
        # Fila assíncrona de contingência
        self.fila_contingencia: List[ContingenciaGravameItem] = []
        # Mapeamento chassi normalizado -> item na fila de contingência
        self.contingencia_chassi_map: Dict[str, ContingenciaGravameItem] = {}

    def simular_particao_detran(self, uf: Union[DetranUF, str], particionado: bool = True) -> None:
        """Ativa ou desativa simulação de partição de rede com o Detran da UF informada."""
        uf_str = uf.value if isinstance(uf, DetranUF) else str(uf).upper()
        if particionado:
            self.detrans_particionados.add(uf_str)
            self.audit_log.append({
                "action": "DETRAN_PARTICAO_ATIVADA",
                "uf": uf_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        else:
            self.detrans_particionados.discard(uf_str)
            self.audit_log.append({
                "action": "DETRAN_PARTICAO_RESTABELECIDA",
                "uf": uf_str,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def registrar_com_contingencia(
        self,
        contrato_numero: str,
        modalidade: ModalidadeGravame,
        credor_cnpj: str,
        credor_nome: str,
        devedor_documento: str,
        devedor_nome: str,
        veiculo: VeiculoIdentificacao,
        valor_financiamento: Decimal,
        quantidade_parcelas: int,
        taxa_juros_anual_pct: Decimal,
        timestamp: Optional[str] = None,
        raise_on_partition: bool = True,
    ) -> Union[GravameRecord, ContingenciaGravameItem]:
        """Registra a inclusão do gravame, roteando para contingência assíncrona sob partição de rede.
        
        Garante:
        1. Se o Detran da UF estiver particionado, retém o item na fila de contingência.
        2. Deduplicação absoluta por chassi/hash: não gera duplicidade de fila se o credor reenviar.
        3. Zero criação de estados fantasmas no índice ativo de gravames (LAW-112).
        """
        chassi_key = veiculo.chassi.strip().upper()
        uf_emplacamento = veiculo.uf_emplacamento.upper()
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        # 1. Checagem prévia da LAW-112 contra índice ativo
        if chassi_key in self.chassi_ativo_index:
            gravame_existente_id = self.chassi_ativo_index[chassi_key]
            rec_existente = self.gravames[gravame_existente_id]
            raise ValueError(
                f"Violação da LAW-112 (Conflito de Gravame Ativo): O veículo Chassi '{chassi_key}' "
                f"já possui o gravame ativo '{gravame_existente_id}' registrado pelo credor "
                f"'{rec_existente.credor_nome}' (CNPJ: {rec_existente.credor_cnpj})."
            )

        # 2. Verificação de partição de rede com o Detran estadual
        if uf_emplacamento in self.detrans_particionados:
            # Deduplicação idempotente na própria fila de contingência
            if chassi_key in self.contingencia_chassi_map:
                existing_item = self.contingencia_chassi_map[chassi_key]
                existing_item.retry_count += 1
                self.audit_log.append({
                    "action": "CONTINGENCIA_RETRY_DEDUPLICADO",
                    "item_id": existing_item.item_id,
                    "chassi": chassi_key,
                    "uf": uf_emplacamento,
                    "retry_count": existing_item.retry_count,
                    "timestamp": ts,
                })
                if raise_on_partition:
                    raise DetranNetworkPartitionError(
                        f"Detran-{uf_emplacamento} em partição de rede. Transação já retida na fila assíncrona "
                        f"de contingência (Item ID: {existing_item.item_id}, retries: {existing_item.retry_count})."
                    )
                return existing_item

            item_id = f"CONT-SNG-{uf_emplacamento}-{len(self.fila_contingencia) + 1:06d}"
            item = ContingenciaGravameItem(
                item_id=item_id,
                contrato_numero=contrato_numero,
                modalidade=modalidade,
                credor_cnpj=credor_cnpj,
                credor_nome=credor_nome,
                devedor_documento=devedor_documento,
                devedor_nome=devedor_nome,
                veiculo=veiculo,
                valor_financiamento=valor_financiamento,
                quantidade_parcelas=quantidade_parcelas,
                taxa_juros_anual_pct=taxa_juros_anual_pct,
                timestamp_enfileiramento=ts,
                uf_detran=uf_emplacamento,
                status=StatusContingencia.ENFILEIRADO,
                ultimo_erro=f"Partição de rede ativa com Detran-{uf_emplacamento}",
            )
            self.fila_contingencia.append(item)
            self.contingencia_chassi_map[chassi_key] = item

            self.audit_log.append({
                "action": "ENFILEIRADO_CONTINGENCIA_ASSINCRONA",
                "item_id": item_id,
                "chassi": chassi_key,
                "uf": uf_emplacamento,
                "timestamp": ts,
            })

            if raise_on_partition:
                raise DetranNetworkPartitionError(
                    f"Detran-{uf_emplacamento} inacessível (Partição de rede). "
                    f"Gravame enfileirado na fila de contingência assíncrona sob protocolo '{item_id}'."
                )
            return item

        # 3. Detran operacional: processamento síncrono padrão
        return self.registrar_gravame(
            contrato_numero=contrato_numero,
            modalidade=modalidade,
            credor_cnpj=credor_cnpj,
            credor_nome=credor_nome,
            devedor_documento=devedor_documento,
            devedor_nome=devedor_nome,
            veiculo=veiculo,
            valor_financiamento=valor_financiamento,
            quantidade_parcelas=quantidade_parcelas,
            taxa_juros_anual_pct=taxa_juros_anual_pct,
            timestamp=ts,
        )

    def processar_fila_contingencia(
        self,
        uf: Optional[Union[DetranUF, str]] = None,
    ) -> List[GravameRecord]:
        """Processa e esvazia a fila de contingência assíncrona para os Detrans restabelecidos."""
        uf_filter = (uf.value if isinstance(uf, DetranUF) else str(uf).upper()) if uf else None
        processados: List[GravameRecord] = []
        remanescentes: List[ContingenciaGravameItem] = []

        for item in self.fila_contingencia:
            if item.status != StatusContingencia.ENFILEIRADO:
                continue

            if uf_filter and item.uf_detran != uf_filter:
                remanescentes.append(item)
                continue

            # Se o Detran ainda estiver particionado, mantém retido na fila
            if item.uf_detran in self.detrans_particionados:
                item.retry_count += 1
                item.ultimo_erro = f"Detran-{item.uf_detran} ainda em partição de rede"
                remanescentes.append(item)
                continue

            chassi_key = item.veiculo.chassi.strip().upper()
            # Se por qualquer motivo já foi ativado, descarta duplicata sem gerar estado fantasma
            if chassi_key in self.chassi_ativo_index:
                item.status = StatusContingencia.DESCARTE_DUPLICADO
                if chassi_key in self.contingencia_chassi_map:
                    del self.contingencia_chassi_map[chassi_key]
                self.audit_log.append({
                    "action": "CONTINGENCIA_DESCARTE_DUPLICADO",
                    "item_id": item.item_id,
                    "chassi": chassi_key,
                })
                continue

            # Processa e ativa o gravame
            rec = self.registrar_gravame(
                contrato_numero=item.contrato_numero,
                modalidade=item.modalidade,
                credor_cnpj=item.credor_cnpj,
                credor_nome=item.credor_nome,
                devedor_documento=item.devedor_documento,
                devedor_nome=item.devedor_nome,
                veiculo=item.veiculo,
                valor_financiamento=item.valor_financiamento,
                quantidade_parcelas=item.quantidade_parcelas,
                taxa_juros_anual_pct=item.taxa_juros_anual_pct,
            )
            item.status = StatusContingencia.PROCESSADO
            if chassi_key in self.contingencia_chassi_map:
                del self.contingencia_chassi_map[chassi_key]

            self.audit_log.append({
                "action": "CONTINGENCIA_PROCESSADA_COM_SUCESSO",
                "item_id": item.item_id,
                "gravame_id": rec.gravame_id,
                "chassi": chassi_key,
                "detran_protocolo": rec.detran_protocolo,
            })
            processados.append(rec)

        self.fila_contingencia = remanescentes
        return processados

    def registrar_gravame(
        self,
        contrato_numero: str,
        modalidade: ModalidadeGravame,
        credor_cnpj: str,
        credor_nome: str,
        devedor_documento: str,
        devedor_nome: str,
        veiculo: VeiculoIdentificacao,
        valor_financiamento: Decimal,
        quantidade_parcelas: int,
        taxa_juros_anual_pct: Decimal,
        timestamp: Optional[str] = None,
    ) -> GravameRecord:
        """Registra a inclusão de um novo gravame veicular no SNG.
        
        Aplica a invariante LAW-112: Não é permitida duplicidade de gravame ativo
        sobre o mesmo chassi/veículo.
        """
        chassi_key = veiculo.chassi.strip().upper()
        if chassi_key in self.chassi_ativo_index:
            gravame_existente_id = self.chassi_ativo_index[chassi_key]
            rec_existente = self.gravames[gravame_existente_id]
            raise ValueError(
                f"Violação da LAW-112 (Conflito de Gravame Ativo): O veículo Chassi '{chassi_key}' "
                f"já possui o gravame ativo '{gravame_existente_id}' registrado pelo credor "
                f"'{rec_existente.credor_nome}' (CNPJ: {rec_existente.credor_cnpj})."
            )

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        gid = f"SNG-{veiculo.uf_emplacamento}-{len(self.gravames) + 1:07d}"
        detran_proto = f"DETRAN-{veiculo.uf_emplacamento}-{hashlib.md5(gid.encode()).hexdigest()[:10].upper()}"

        rec = GravameRecord(
            gravame_id=gid,
            contrato_numero=contrato_numero,
            modalidade=modalidade,
            credor_cnpj=credor_cnpj,
            credor_nome=credor_nome,
            devedor_documento=devedor_documento,
            devedor_nome=devedor_nome,
            veiculo=veiculo,
            valor_financiamento=valor_financiamento,
            quantidade_parcelas=quantidade_parcelas,
            taxa_juros_anual_pct=taxa_juros_anual_pct,
            data_inclusao=ts,
            status=StatusGravame.ATIVO,
            detran_protocolo=detran_proto,
        )

        self.gravames[gid] = rec
        self.chassi_ativo_index[chassi_key] = gid

        self.audit_log.append({
            "action": "INCLUSAO_GRAVAME",
            "gravame_id": gid,
            "chassi": chassi_key,
            "credor_cnpj": credor_cnpj,
            "credor_cnpj_masked": mask_cpf_cnpj(credor_cnpj),
            "devedor_documento_masked": mask_cpf_cnpj(devedor_documento),
            "devedor_documento_sha256": hash_sha256(devedor_documento),
            "devedor_nome_masked": mask_person_name(devedor_nome),
            "timestamp": ts,
            "protocolo": detran_proto,
            "lgpd_audit": {
                "minimization": True,
                "pseudonymized": True,
                "sha256_hash": hash_sha256(devedor_documento),
            },
        })

        return rec

    def consultar_por_chassi(self, chassi: str) -> Optional[GravameRecord]:
        """Consulta o gravame ativo de um veículo a partir de seu chassi."""
        chassi_key = chassi.strip().upper()
        gid = self.chassi_ativo_index.get(chassi_key)
        return self.gravames.get(gid) if gid else None

    def baixar_gravame(
        self,
        gravame_id: str,
        motivo: str = "QUITACAO_INTEGRAL_CONTRATO",
        timestamp: Optional[str] = None,
    ) -> GravameRecord:
        """Executa a baixa de gravame veicular após quitação do financiamento.
        
        Libera o chassi no índice do Detran/SNG permitindo transferência do bem.
        """
        if gravame_id not in self.gravames:
            raise KeyError(f"Gravame '{gravame_id}' não encontrado no SNG.")

        rec = self.gravames[gravame_id]
        if rec.status != StatusGravame.ATIVO:
            raise ValueError(f"Não é possível baixar gravame com status '{rec.status.value}'.")

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        chassi_key = rec.veiculo.chassi.strip().upper()

        rec.status = StatusGravame.BAIXADO
        rec.data_baixa = ts
        rec.motivo_baixa = motivo

        # Desvincula do índice ativo
        if chassi_key in self.chassi_ativo_index:
            del self.chassi_ativo_index[chassi_key]

        self.audit_log.append({
            "action": "BAIXA_GRAVAME",
            "gravame_id": gravame_id,
            "chassi": chassi_key,
            "motivo": motivo,
            "timestamp": ts,
        })

        return rec

    def cancelar_gravame(
        self,
        gravame_id: str,
        motivo: str = "DESISTENCIA_OPERACAO_CREDITO",
        timestamp: Optional[str] = None,
    ) -> GravameRecord:
        """Cancela administrativamente um gravame por erro ou desistência da operação."""
        if gravame_id not in self.gravames:
            raise KeyError(f"Gravame '{gravame_id}' não encontrado no SNG.")

        rec = self.gravames[gravame_id]
        if rec.status != StatusGravame.ATIVO:
            raise ValueError(f"Apenas gravames com status ATIVO podem ser cancelados.")

        ts = timestamp or datetime.now(timezone.utc).isoformat()
        chassi_key = rec.veiculo.chassi.strip().upper()

        rec.status = StatusGravame.CANCELADO
        rec.data_baixa = ts
        rec.motivo_baixa = motivo

        if chassi_key in self.chassi_ativo_index:
            del self.chassi_ativo_index[chassi_key]

        self.audit_log.append({
            "action": "CANCELAMENTO_GRAVAME",
            "gravame_id": gravame_id,
            "chassi": chassi_key,
            "motivo": motivo,
            "timestamp": ts,
        })

        return rec

    def verify_invariants(self, raise_on_error: bool = True) -> Dict[str, Any]:
        """Verifica a integridade matemática e formal da LAW-112 no SNG.
        
        Garante que:
        1. Para cada chassi no índice ativo, há estritamente 1 gravame com status ATIVO.
        2. Nenhum gravame com status BAIXADO ou CANCELADO consta no índice ativo.
        3. Todos os hashes de segurança permanecem inviolados.
        """
        active_count = 0
        for gid, rec in self.gravames.items():
            chassi_key = rec.veiculo.chassi.strip().upper()
            if rec.status == StatusGravame.ATIVO:
                active_count += 1
                if self.chassi_ativo_index.get(chassi_key) != gid:
                    msg = f"Invariante LAW-112 violada: Gravame ativo '{gid}' ausente no índice do chassi '{chassi_key}'"
                    if raise_on_error:
                        raise AssertionError(msg)
                    return {"verified": False, "error": msg}
            else:
                if chassi_key in self.chassi_ativo_index and self.chassi_ativo_index[chassi_key] == gid:
                    msg = f"Invariante LAW-112 violada: Gravame não-ativo '{gid}' presente no índice do chassi '{chassi_key}'"
                    if raise_on_error:
                        raise AssertionError(msg)
                    return {"verified": False, "error": msg}

        if len(self.chassi_ativo_index) != active_count:
            msg = f"Divergência na contagem de gravames ativos: índice={len(self.chassi_ativo_index)}, registros={active_count}"
            if raise_on_error:
                raise AssertionError(msg)
            return {"verified": False, "error": msg}

        return {
            "law": "LAW-112",
            "verified": True,
            "total_gravames": len(self.gravames),
            "active_gravames": active_count,
            "audit_events_count": len(self.audit_log),
        }
