------------------------ MODULE CentralLiquidityBus ------------------------
(***************************************************************************)
(* Especificacao Formal TLA+ do Barramento Central de Liquidacao Unificada *)
(* e Compensacao Multilateral da B3 (FL-100 / LAW-106A, LAW-106B, LAW-106C)*)
(*                                                                         *)
(* Modela matematicamente:                                                 *)
(* 1. Equilibrio Contabil Soma-Zero Multilateral entre Membros (LAW-106A)  *)
(* 2. Contencao Deterministica de Inadimplencia via Default Waterfall em   *)
(*    camadas sem propagacao em cascata (LAW-106B)                         *)
(* 3. Atomicidade All-or-Nothing na liquidacao inter-camaras (LAW-106C)    *)
(* 4. Ausencia de Deadlock e Terminacao Garantida do Ciclo (Liveness)      *)
(***************************************************************************)

EXTENDS Integers, Sequences, FiniteSets, TLC

CONSTANTS
    Members,            \* Conjunto de Membros Compensadores (ex: {"M1", "M2", "M3"})
    Subsystems,         \* Conjunto de Camaras/Subsistemas (ex: {"PUMA", "NOME", "DREX"})
    SafeguardLayers,    \* Sequencia ordenada de camadas do Default Waterfall
    InitialCollateral,  \* Garantia individual inicial por membro
    InitialSafeguard    \* Saldo inicial do fundo mutualizado da Camara B3

VARIABLES
    cyclePhase,         \* Fase do ciclo: "CAPTURING", "NETTING", "WATERFALL", "SETTLED", "ROLLED_BACK"
    memberNetPositions, \* Funcao [m \in Members -> Int] (soma zero global)
    memberStatus,       \* Funcao [m \in Members -> {"SOLVENT", "DEFAULTED", "ABSORBED"}]
    collateralPool,     \* Funcao [m \in Members -> Nat]
    safeguardPool,      \* Nat: Saldo disponivel nas salvaguardas da CCP
    ccpEquity,          \* Nat: Capital proprio dedicado da B3
    settledSubsystems,  \* Conjunto de subsistemas liquidados no ciclo
    residualDeficit     \* Nat: Deficit de inadimplencia pendente de absorcao

vars == <<cyclePhase, memberNetPositions, memberStatus, collateralPool,
          safeguardPool, ccpEquity, settledSubsystems, residualDeficit>>

-----------------------------------------------------------------------------
(* Funcoes Auxiliares de Soma sob Conjuntos Finitos *)

RECURSIVE SumSet(_, _)
SumSet(S, f) ==
    IF S = {} THEN 0
    ELSE LET x == CHOOSE v \in S : TRUE
         IN f[x] + SumSet(S \ {x}, f)

-----------------------------------------------------------------------------
(* Estado Inicial *)

TypeOK ==
    /\ cyclePhase \in {"CAPTURING", "NETTING", "WATERFALL", "SETTLED", "ROLLED_BACK"}
    /\ memberNetPositions \in [Members -> Int]
    /\ memberStatus \in [Members -> {"SOLVENT", "DEFAULTED", "ABSORBED"}]
    /\ collateralPool \in [Members -> Nat]
    /\ safeguardPool \in Nat
    /\ ccpEquity \in Nat
    /\ settledSubsystems \subseteq Subsystems
    /\ residualDeficit \in Nat

Init ==
    /\ cyclePhase = "CAPTURING"
    /\ memberNetPositions = [m \in Members |-> 0]
    /\ memberStatus = [m \in Members |-> "SOLVENT"]
    /\ collateralPool = [m \in Members |-> InitialCollateral]
    /\ safeguardPool = InitialSafeguard
    /\ ccpEquity = InitialSafeguard
    /\ settledSubsystems = {}
    /\ residualDeficit = 0

-----------------------------------------------------------------------------
(* Transicoes de Estado (Next-State Relation) *)

(* 1. Registro bilateral de obrigacao compensavel entre dois membros (Soma-Zero) *)
RegisterObligation(payer, receiver, amount) ==
    /\ cyclePhase = "CAPTURING"
    /\ payer # receiver
    /\ amount > 0
    /\ amount <= InitialCollateral
    /\ memberNetPositions' = [memberNetPositions EXCEPT
                                ![payer] = memberNetPositions[payer] - amount,
                                ![receiver] = memberNetPositions[receiver] + amount]
    /\ UNCHANGED <<cyclePhase, memberStatus, collateralPool,
                   safeguardPool, ccpEquity, settledSubsystems, residualDeficit>>

(* 2. Encerramento da captura e congelamento para Netting Multilateral *)
CloseCaptureAndNet ==
    /\ cyclePhase = "CAPTURING"
    /\ cyclePhase' = "NETTING"
    /\ UNCHANGED <<memberNetPositions, memberStatus, collateralPool,
                   safeguardPool, ccpEquity, settledSubsystems, residualDeficit>>

(* 3. Declaracao de Inadimplencia de Membro Devedor Liquido *)
TriggerMemberDefault(m) ==
    /\ cyclePhase = "NETTING"
    /\ memberStatus[m] = "SOLVENT"
    /\ memberNetPositions[m] < 0
    /\ LET debt == -(memberNetPositions[m]) IN
       /\ memberStatus' = [memberStatus EXCEPT ![m] = "DEFAULTED"]
       /\ residualDeficit' = residualDeficit + debt
       /\ cyclePhase' = "WATERFALL"
       /\ UNCHANGED <<memberNetPositions, collateralPool,
                      safeguardPool, ccpEquity, settledSubsystems>>

(* 4. Execucao Deterministica do Default Waterfall em Camadas (LAW-106B) *)
ResolveDefaultViaWaterfall(m) ==
    /\ cyclePhase = "WATERFALL"
    /\ memberStatus[m] = "DEFAULTED"
    /\ residualDeficit > 0
    /\ LET collUsed == IF collateralPool[m] >= residualDeficit
                       THEN residualDeficit
                       ELSE collateralPool[m]
           remAfterColl == residualDeficit - collUsed
           safeUsed == IF safeguardPool >= remAfterColl
                       THEN remAfterColl
                       ELSE safeguardPool
           remAfterSafe == remAfterColl - safeUsed
           eqUsed   == IF ccpEquity >= remAfterSafe
                       THEN remAfterSafe
                       ELSE ccpEquity
           remFinal == remAfterSafe - eqUsed
       IN
       /\ remFinal = 0 \* Garantia de suficiencia patrimonial do Waterfall
       /\ collateralPool' = [collateralPool EXCEPT ![m] = collateralPool[m] - collUsed]
       /\ safeguardPool' = safeguardPool - safeUsed
       /\ ccpEquity' = ccpEquity - eqUsed
       /\ residualDeficit' = 0
       /\ memberStatus' = [memberStatus EXCEPT ![m] = "ABSORBED"]
       /\ memberNetPositions' = [memberNetPositions EXCEPT ![m] = 0]
       /\ cyclePhase' = "NETTING"
       /\ UNCHANGED <<settledSubsystems>>

(* 5. Liquidacao Atomica All-or-Nothing de todos os Subsistemas (LAW-106C) *)
ExecuteAtomicSettlement ==
    /\ cyclePhase = "NETTING"
    /\ \A m \in Members : memberStatus[m] \in {"SOLVENT", "ABSORBED"}
    /\ residualDeficit = 0
    /\ settledSubsystems' = Subsystems
    /\ cyclePhase' = "SETTLED"
    /\ UNCHANGED <<memberNetPositions, memberStatus, collateralPool,
                   safeguardPool, ccpEquity, residualDeficit>>

(* 6. Rollback Atomico Integral caso haja falha sistêmica externa *)
ExecuteAtomicRollback ==
    /\ cyclePhase = "NETTING"
    /\ settledSubsystems' = {}
    /\ memberNetPositions' = [m \in Members |-> 0]
    /\ cyclePhase' = "ROLLED_BACK"
    /\ UNCHANGED <<memberStatus, collateralPool, safeguardPool, ccpEquity, residualDeficit>>

(* 7. Estado terminal estavel (Evita falso positivo de deadlock no TLC) *)
Terminated ==
    /\ cyclePhase \in {"SETTLED", "ROLLED_BACK"}
    /\ UNCHANGED vars

Next ==
    \/ \E p, r \in Members : RegisterObligation(p, r, 10)
    \/ CloseCaptureAndNet
    \/ \E m \in Members : TriggerMemberDefault(m)
    \/ \E m \in Members : ResolveDefaultViaWaterfall(m)
    \/ ExecuteAtomicSettlement
    \/ ExecuteAtomicRollback
    \/ Terminated

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

-----------------------------------------------------------------------------
(* INVARIANTES FORMAIS DE SEGURANCA (SAFETY PROPERTIES) *)

(* LAW-106A: Equilibrio Contabil Multilateral Soma-Zero antes de absorcoes *)
ZeroSumAccountingConservation ==
    (\A m \in Members : memberStatus[m] = "SOLVENT") =>
        SumSet(Members, memberNetPositions) = 0

(* LAW-106B: Contencao de Inadimplencia — Nenhum membro solvente tem colateral confiscado
   sem que o inadimplente tenha primeiro exaurido 100% de suas garantias individuais *)
WaterfallOrderInviolability ==
    \A m \in Members :
        (memberStatus[m] = "ABSORBED" /\ safeguardPool < InitialSafeguard) =>
            collateralPool[m] = 0

(* LAW-106B: Nao-Propagacao — Nenhum ciclo pode encerrar SETTLED com deficit residual *)
NoUnabsorbedDefaultAtSettlement ==
    (cyclePhase = "SETTLED") => (residualDeficit = 0)

(* LAW-106C: Atomicidade Inter-Camaras (All-or-Nothing) *)
IntersystemSettlementAtomicity ==
    \/ settledSubsystems = {}
    \/ settledSubsystems = Subsystems

(* PROPRIEDADE TEMPORAL DE LIVENESS: Todo ciclo eventualmente atinge estado final *)
EventualCycleTermination ==
    <>(cyclePhase \in {"SETTLED", "ROLLED_BACK"})

=============================================================================
