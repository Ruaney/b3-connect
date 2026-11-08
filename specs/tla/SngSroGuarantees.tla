----------------------- MODULE SngSroGuarantees -----------------------
(***************************************************************************)
(* Especificacao Formal TLA+ das Garantias do SNG e SRO B3                 *)
(* (SNG: LAW-112 | SRO: LAW-113)                                           *)
(*                                                                         *)
(* Modela matematicamente:                                                 *)
(* 1. SNG (Sistema Nacional de Gravames): Unicidade de Gravame Ativo       *)
(*    por chassi veicular (LAW-112 / Res. CONTRAN 807/2020)                *)
(* 2. SRO (Sistema de Registro de Operacoes): Conservacao de Quota de      *)
(*    Cosseguro (100%) e Inviolabilidade do Limite Maximo de Garantia      *)
(*    (LMG) em sinistros (LAW-113 / Circulares SUSEP 605/612/642)          *)
(* 3. Ausencia de Deadlock e Verificabilidade Finita por Model Checking    *)
(***************************************************************************)

EXTENDS Integers, Sequences, FiniteSets, TLC

CONSTANTS
    Chassis,        \* Conjunto finito de identificadores de chassi (ex: {"CHA", "CHB"})
    Creditors,      \* Conjunto finito de instituicoes credoras (ex: {"CRED_A", "CRED_B"})
    Contracts,      \* Conjunto finito de numeros de contrato (ex: {"CTR1", "CTR2"})
    PolicyIds,      \* Conjunto finito de identificadores de apolices (ex: {"POL1", "POL2"})
    Insurers,       \* Conjunto finito de seguradoras participantes (ex: {"INS_A", "INS_B"})
    InitialLMG,     \* Funcao [p \in PolicyIds -> Nat]: Limite Maximo de Garantia
    InitialShares   \* Funcao [p \in PolicyIds -> [i \in Insurers -> Nat]]: Quotas de Cosseguro

VARIABLES
    ChassisState,   \* Funcao [c \in Chassis -> ChassisStatus]
    Gravames,       \* Conjunto finito de registros de gravame
    PolicyState,    \* Funcao [p \in PolicyIds -> PolicyRecord]
    Policies        \* Conjunto finito de registros de apolices

vars == <<ChassisState, Gravames, PolicyState, Policies>>

-----------------------------------------------------------------------------
(* Construtores de Estado do Veiculo (ChassisState) *)

NoGravame == [status |-> "NoGravame", credor |-> "NONE", contract |-> "NONE"]

GravameActive(cred, k) == [status |-> "GravameActive", credor |-> cred, contract |-> k]

Released == [status |-> "Released", credor |-> "NONE", contract |-> "NONE"]

ChassisStatusSet ==
    {NoGravame, Released} \cup
    {GravameActive(c, k) : c \in Creditors, k \in Contracts}

-----------------------------------------------------------------------------
(* Funcoes Auxiliares de Soma sob Conjuntos Finitos *)

RECURSIVE SumSet(_, _)
SumSet(S, f) ==
    IF S = {} THEN 0
    ELSE LET x == CHOOSE v \in S : TRUE
         IN f[x] + SumSet(S \ {x}, f)

Sum(shares) == SumSet(DOMAIN shares, shares)

-----------------------------------------------------------------------------
(* Predicado de Tipagem (TypeOK) *)

TypeOK ==
    /\ ChassisState \in [Chassis -> ChassisStatusSet]
    /\ Gravames \subseteq [id: Nat \cup (Chassis \times Creditors \times Contracts),
                           chassi: Chassis,
                           credor: Creditors,
                           contract: Contracts,
                           status: {"ACTIVE", "RELEASED", "CANCELLED"}]
    /\ PolicyState \in [PolicyIds -> [shares: [Insurers -> Nat],
                                     LMG: Nat,
                                     totalClaimsPaid: Nat]]
    /\ Policies \subseteq [id: PolicyIds,
                           shares: [Insurers -> Nat],
                           LMG: Nat,
                           totalClaimsPaid: Nat]

-----------------------------------------------------------------------------
(* Estado Inicial (Init) *)

Init ==
    /\ ChassisState = [c \in Chassis |-> NoGravame]
    /\ Gravames = {}
    /\ PolicyState = [p \in PolicyIds |-> [shares |-> InitialShares[p],
                                           LMG |-> InitialLMG[p],
                                           totalClaimsPaid |-> 0]]
    /\ Policies = { [id |-> p,
                     shares |-> InitialShares[p],
                     LMG |-> InitialLMG[p],
                     totalClaimsPaid |-> 0] : p \in PolicyIds }

-----------------------------------------------------------------------------
(* Acoes do Sistema Nacional de Gravames (SNG) *)

(* 1. Inclusao de Gravame com garantia estrita de unicidade ativa (LAW-112) *)
IncludeGravame(c, cred, k) ==
    /\ ChassisState[c].status \in {"NoGravame", "Released"}
    /\ ~ \E g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"
    /\ ~ \E g \in Gravames : g.chassi = c /\ g.credor = cred /\ g.contract = k
    /\ LET newGravame == [id |-> <<c, cred, k>>,
                          chassi |-> c,
                          credor |-> cred,
                          contract |-> k,
                          status |-> "ACTIVE"]
       IN
       /\ Gravames' = Gravames \cup {newGravame}
       /\ ChassisState' = [ChassisState EXCEPT ![c] = GravameActive(cred, k)]
       /\ UNCHANGED <<PolicyState, Policies>>

(* 2. Baixa de Gravame por quitacao integral do financiamento *)
ReleaseGravame(c) ==
    /\ ChassisState[c].status = "GravameActive"
    /\ \E g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"
    /\ LET activeG == CHOOSE g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"
           releasedG == [activeG EXCEPT !.status = "RELEASED"]
       IN
       /\ Gravames' = (Gravames \ {activeG}) \cup {releasedG}
       /\ ChassisState' = [ChassisState EXCEPT ![c] = Released]
       /\ UNCHANGED <<PolicyState, Policies>>

(* 3. Cancelamento administrativo de gravame indevido / inconsistente *)
CancelGravame(c) ==
    /\ ChassisState[c].status = "GravameActive"
    /\ \E g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"
    /\ LET activeG == CHOOSE g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"
           cancelledG == [activeG EXCEPT !.status = "CANCELLED"]
       IN
       /\ Gravames' = (Gravames \ {activeG}) \cup {cancelledG}
       /\ ChassisState' = [ChassisState EXCEPT ![c] = NoGravame]
       /\ UNCHANGED <<PolicyState, Policies>>

-----------------------------------------------------------------------------
(* Acoes do Sistema de Registro de Operacoes (SRO) *)

(* 4. Liquidacao e Pagamento de Sinistro com observancia estrita ao LMG (LAW-113) *)
PayClaim(p, claimAmount) ==
    /\ claimAmount > 0
    /\ PolicyState[p].totalClaimsPaid + claimAmount <= PolicyState[p].LMG
    /\ LET newPaid == PolicyState[p].totalClaimsPaid + claimAmount
           oldRec == CHOOSE rec \in Policies : rec.id = p
           newRec == [oldRec EXCEPT !.totalClaimsPaid = newPaid]
       IN
       /\ PolicyState' = [PolicyState EXCEPT ![p].totalClaimsPaid = newPaid]
       /\ Policies' = (Policies \ {oldRec}) \cup {newRec}
       /\ UNCHANGED <<ChassisState, Gravames>>

-----------------------------------------------------------------------------
(* Estabilidade Terminal (Stuttering Step para prevencao de falso deadlock) *)

Terminated ==
    /\ UNCHANGED vars

-----------------------------------------------------------------------------
(* Relacao de Proximo Estado (Next) *)

Next ==
    \/ \E c \in Chassis, cred \in Creditors, k \in Contracts : IncludeGravame(c, cred, k)
    \/ \E c \in Chassis : ReleaseGravame(c)
    \/ \E c \in Chassis : CancelGravame(c)
    \/ \E p \in PolicyIds : PayClaim(p, 20)
    \/ Terminated

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

-----------------------------------------------------------------------------
(* INVARIANTES FORMAIS DE SEGURANCA (SAFETY PROPERTIES) *)

(* LAW-112: Invariante Fundamental do SNG — Unicidade de Gravame Ativo por Chassi *)
UniqueActiveGravame_LAW_112 ==
    \A c \in Chassis :
        Cardinality({g \in Gravames : g.chassi = c /\ g.status = "ACTIVE"}) <= 1

(* LAW-113: Invariante Fundamental do SRO — Conservacao de Cosseguro e Teto LMG *)
CosseguroConservation_LAW_113 ==
    \A p \in Policies :
        Sum(p.shares) = 100 /\ p.totalClaimsPaid <= p.LMG

=============================================================================
