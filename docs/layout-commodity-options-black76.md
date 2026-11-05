# Especificação Técnica: Opções sobre Futuros de Commodities Agrícolas Black-76 (FL-064)

## 1. Visão Geral do Fluxo

O fluxo **FL-064** normatiza e implementa o ciclo de vida institucional completo de **Opções Listadas sobre Contratos Futuros Agropecuários** negociados no **PUMA Trading System** e compensados na **Câmara B3**:
1. **Soja em Grão FOB Santos (SJC / SOJ)**: Lote padrão de 450 sacas de 60 kg (27 t), cotado em USD/saca (tick USD 0,01).
2. **Milho em Grão (CCM)**: Lote padrão de 450 sacas de 60 kg (27 t), cotado em BRL/saca (tick R$ 0,01).
3. **Boi Gordo Indicador Cepea (BGI)**: Lote padrão de 330 arrobas líquidas de 15 kg (4.950 kg), cotado em BRL/arroba (tick R$ 0,01).
4. **Café Arábica tipo 4/5 (ICF)**: Lote padrão de 100 sacas de 60 kg (6 t), cotado em USD/saca (tick USD 0,05).

---

## 2. Modelo Quantitativo Black-76 (Fischer Black 1976)

Para opções sobre contratos futuros, o modelo analítico Black-76 desconta o preço a termo sem custos de carregamento embutidos no spot:

$$d_1 = \frac{\ln(F / K) + \frac{1}{2}\sigma^2 T}{\sigma \sqrt{T}}$$
$$d_2 = d_1 - \sigma \sqrt{T}$$

### Prêmios Analíticos:
- **Call**: $C = e^{-r T} [ F \cdot N(d_1) - K \cdot N(d_2) ]$
- **Put**: $P = e^{-r T} [ K \cdot N(-d_2) - F \cdot N(-d_1) ]$

### Gregas Analíticas de Primeira e Segunda Ordem:
- **Delta Call**: $\Delta_C = e^{-r T} N(d_1)$
- **Delta Put**: $\Delta_P = -e^{-r T} N(-d_1) = \Delta_C - e^{-r T}$
- **Gamma**: $\Gamma = \frac{e^{-r T} N'(d_1)}{F \sigma \sqrt{T}}$
- **Vega**: $\mathcal{V} = e^{-r T} F \sqrt{T} N'(d_1)$ (com $\mathcal{V}_{1\%} = \mathcal{V} \times 0{,}01$)
- **Theta Anual**: $\Theta_C = -\frac{e^{-r T} F N'(d_1) \sigma}{2\sqrt{T}} + r C$; $\Theta_P = -\frac{e^{-r T} F N'(d_1) \sigma}{2\sqrt{T}} + r P$
- **Theta Diário**: $\Theta_{\text{diário}} = \frac{\Theta_{\text{anual}}}{252}$
- **Rho**: $\rho_C = -T \cdot C$; $\rho_P = -T \cdot P$ (com $\rho_{1\%} = \rho \times 0{,}01$)
- **Vanna**: $\frac{\partial \Delta}{\partial \sigma} = -e^{-r T} N'(d_1) \frac{d_2}{\sigma}$
- **Volga (Vomma)**: $\frac{\partial \mathcal{V}}{\partial \sigma} = \mathcal{V} \frac{d_1 d_2}{\sigma}$

---

## 3. Regras de Exercício e Conversão em Futuros B3

1. **Exercício Automático no Vencimento**:
   - Call ITM se $(F_{\text{settle}} - K) \ge \text{tick\_size}$.
   - Put ITM se $(K - F_{\text{settle}}) \ge \text{tick\_size}$.
   - Opções com ganho intrínseco inferior a 1 tick ou OTM expiram sem exercício.
2. **Contrary Instruction (Instrução Contrária)**:
   - O participante titular pode formalizar veto ao exercício, preservando o status não exercido.
3. **Conversão Determinística em Contratos Futuros**:
   - **Titular de Call** $\to$ Posição Comprada no Futuro ao preço de Strike $K$.
   - **Lançador de Call** $\to$ Posição Vendida no Futuro ao preço de Strike $K$.
   - **Titular de Put** $\to$ Posição Vendida no Futuro ao preço de Strike $K$.
   - **Lançador de Put** $\to$ Posição Comprada no Futuro ao preço de Strike $K$.
4. **Ajuste Diário no D0 do Exercício**:
   - Long Future MTM: $(F_{\text{settle}} - K) \times \text{Multiplicador} \times Q$.
   - Short Future MTM: $(K - F_{\text{settle}}) \times \text{Multiplicador} \times Q$.
   - Saldo Líquido Multilateral: $\sum \text{MTM}_{\text{Holders}} + \sum \text{MTM}_{\text{Writers}} == 0{,}00$ (Zero-Sum).

---

## 4. Invariantes Formais Auditadas

1. **Paridade Put-Call Black-76**: $C - P == (F - K) \cdot e^{-r T}$.
2. **Conservação Física Bilateral**: Quantidade_Futuros_Criada == Quantidade_Opções_Exercidas.
3. **Conservação Financeira Zero-Sum**: Ganho MTM Holders + Perda MTM Writers == 0,00.
4. **Piso Regulamentar de 1 Tick**: Veto de exercício automático se ITM < tick.
5. **Inviolabilidade de Contrary Instruction**: Nenhuma posição sob contrary instruction gera futuros.
6. **Limites e Monotonicidade das Gregas**: Delta em $[0, e^{-r T}]$ para Calls e $[-e^{-r T}, 0]$ para Puts; Gamma e Vega $\ge 0$.
