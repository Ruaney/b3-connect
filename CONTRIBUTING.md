# Guia de Contribuição — B3 Integration Atlas

Agradecemos o seu interesse em contribuir com o **B3 Integration Atlas**! Este documento estabelece as diretrizes técnicas, arquiteturais e de qualidade necessárias para colaborar com o ecossistema aberto de engenharia financeira e integração de infraestruturas de mercado (FMI) da B3.

---

## 1. Princípios Arquiteturais e Diretrizes de Design

O Atlas de Integrações B3 foi concebido como uma especificação viva e biblioteca de referência técnica com nível institucional de confiabilidade. Para assegurar a portabilidade em qualquer ambiente financeiro corporativo ou container restrito, todo o código do projeto segue os seguintes postulados:

### 1.1. 100% Python Standard Library (Zero Dependências Externas)
- **Zero runtime dependencies**: O core do projeto (`src/`) e seus scripts de auditoria (`scripts/`) **não dependem de pacotes externos** (como `requests`, `pandas`, `pydantic` ou `pytest`).
- **Exclusividade da biblioteca padrão**: Utilizamos apenas módulos nativos do Python 3.10+ (`dataclasses`, `enum`, `typing`, `decimal`, `datetime`, `struct`, `urllib`, `json`, `hashlib`, `unittest`).
- **Motivação**: Bancos, corretoras e assets possuem políticas estritas de segurança da informação (SecOps) e restrições de instalação de pacotes via PyPI em redes seguras (RCC/DMZ). Um repositório *zero-dependency* pode ser auditado e incorporado diretamente sem risco de ataque à cadeia de suprimentos (*supply chain attack*).

### 1.2. Aritmética Financeira Estrita com `Decimal`
- **Ponto flutuante binário (`float`) é terminantemente proibido** para representação de montantes financeiros, preços unitários, quantidades contratuais, taxas de corretagem ou emolumentos.
- Todo cálculo monetário deve operar com `decimal.Decimal` e arredondamento explícito (`ROUND_HALF_UP` ou `ROUND_DOWN` conforme manual regulatório da B3).
- Conversões e formatações para texto ou layouts de tamanho fixo devem preservar o número de casas decimais exigido pela câmara (e.g., 2 a 8 decimais em derivativos e taxas).

### 1.3. Tipagem Estrita (MyPy Strict Mode & PEP 484/585/604)
- Todo o código deve incluir anotações de tipo completas em assinaturas de funções, atributos e retornos.
- Use `from __future__ import annotations` para suporte a sintaxes modernas.
- Tipos genéricos nativos (`dict[str, Any]`, `list[T]`, `tuple[...]`) ou `typing` (`Optional[T]`, `Union[A, B]`).
- Proibido o uso de `Any` cego sem documentação de justificativa.

### 1.4. Provas Formais e Invariantes de Conservação Financeira
- Nenhum novo fluxo pode ser adicionado sem a formulação de sua respectiva **Lei de Invariante Formal**.
- As invariantes traduzem leis físicas e financeiras da pós-negociação e mercado:
  - *Conservação de Custódia*: `Total == Disponível + Garantias + Bloqueado Judicial`
  - *Soma-Zero Financeira Multilateral*: `Soma(Débitos) == Soma(Créditos)`
  - *Atomicidade DvP/PvP*: Entrega contra pagamento indivisível (nenhum ativo é transferido sem contrapartida financeira confirmada).
- Cada módulo deve conter testes unitários exaustivos e ser verificado pelo script `scripts/verify_invariants.py`.

---

## 2. Padrões de Código e Estilo

- **PEP 8**: Linhas com no máximo 100 caracteres; indentação de 4 espaços; nomes em `snake_case` para funções/métodos e `PascalCase` para classes/dataclasses.
- **Docstrings**: Formato Google ou Sphinx em português, detalhando a fundamentação regulatória (e.g., Ofício Circular B3, Resolução CVM ou CMN) e as regras de negócio implementadas.
- **Tratamento de Exceções**: Lance exceções tipadas e semânticas derivadas de exceções base do projeto (e.g., `InvariantViolationError`, `LayoutParsingError`). Mensagens de erro devem ser determinísticas e informativas.

---

## 3. Ambiente de Desenvolvimento Local

### 3.1. Pré-requisitos
- Python 3.10 ou superior instalado localmente.
- Git configurado.

### 3.2. Clonando o Repositório
```bash
git clone https://github.com/your-org/b3-integration-atlas.git
cd b3-integration-atlas
```

### 3.3. Execução da Suíte de Testes
Toda a validação utiliza o módulo `unittest` nativo do Python:

```bash
# Executa todos os mais de 1.500 testes unitários automatizados
python3 -m unittest discover tests

# Executa testes de um módulo específico (exemplo: Puma SBE)
python3 -m unittest tests/test_puma_sbe.py
```

### 3.4. Auditoria de Invariantes e Catálogo
```bash
# Verifica formalmente todas as 91 Leis de Conservação e Invariantes
python3 scripts/verify_invariants.py

# Valida a integridade do catálogo e dos arquivos referenciados
python3 scripts/test_catalog.py --check-files

# Emite o relatório de cobertura do inventário
python3 scripts/get_coverage.py
```

---

## 4. Como Adicionar um Novo Fluxo B3

Ao submeter a especificação ou implementação de um novo fluxo (FL-XXX):

1. **Atualize o Catálogo (`catalog/flows.json`)**:
   - Defina `id`, `name`, `system`, `interface`, `stage` e `description`.
   - Adicione os artefatos associados em `spec`, `module`, `fixture` e `tests`.
2. **Atualize o Catálogo de Fontes (`catalog/sources.json`)** se houver novos manuais, ofícios circulares ou regulamentos.
3. **Crie a Especificação (`docs/layout-*.md` ou `specs/`)**:
   - Dicionário de campos, posições de corte (se flat-file), tipos de dados e limites.
4. **Implemente o Módulo (`src/<nome_modulo>.py`)**:
   - 100% Python Standard Library.
   - Dataclasses imutáveis ou com validação pós-inicialização.
   - Método `verify_invariants()` ou verificação estrita de regras.
5. **Crie a Fixture Realista (`fixtures/<nome_fixture>.json` ou `.txt`)**:
   - Cenários válidos e anomalias para testes de borda.
6. **Escreva os Testes Unitários (`tests/test_<nome_modulo>.py`)**:
   - Cobertura de cenários nominais, reversões, rejeições e quebras de invariante.
7. **Integre à Verificação Formal (`scripts/verify_invariants.py`)**:
   - Registre a função `verify_<modulo>_invariants` numerando a Lei e comprovando a matemática.

---

## 5. Padrão de Commits e Branching

Utilizamos o padrão **Conventional Commits**:

| Prefixo | Significado | Exemplo |
|---|---|---|
| `feat:` | Nova funcionalidade ou módulo de fluxo | `feat(sro): implementa registro de cosseguro no SRO` |
| `fix:` | Correção de bug ou ajuste de layout | `fix(imbarq004): corrige posição do campo de dígito verificador` |
| `docs:` | Atualização de documentação ou especificações | `docs(certification): adiciona checklist de homologação CET` |
| `test:` | Adição ou refatoração de testes unitários | `test(puma-sbe): adiciona benchmark de throughput para NewOrderSingle` |
| `refactor:` | Refatoração de código sem alteração funcional | `refactor(pipeline): otimiza detecção de quebras em custódia` |
| `perf:` | Melhoria de desempenho ou latência | `perf(sbe): otimiza unpack struct zero-allocation` |
| `chore:` | Tarefas de governança, CI/CD ou scripts | `chore(gov): atualiza roadmap evolutivo com Drex Fase 2` |

### Fluxo de Branches
1. Crie uma branch a partir da `main`:
   ```bash
   git checkout -b feat/meu-novo-fluxo
   ```
2. Realize alterações atômicas com mensagens claras.
3. Assegure que `python3 -m unittest discover tests` e `python3 scripts/verify_invariants.py` passam com 100% de sucesso.
4. Abra um Pull Request detalhado.

---

## 6. Checklist de Submissão de Pull Request

Antes de submeter o seu Pull Request, verifique:
- [ ] O código segue estritamente 100% da Biblioteca Padrão do Python (sem dependências externas).
- [ ] Nenhum `float` é utilizado para grandezas monetárias ou quantidades de contratos (uso de `Decimal`).
- [ ] Todos os métodos e classes possuem anotações de tipo estritas (compatíveis com MyPy).
- [ ] O novo código é acompanhado de testes unitários em `tests/`.
- [ ] `python3 -m unittest discover tests` roda limpo sem falhas ou erros.
- [ ] `python3 scripts/verify_invariants.py` é executado e todas as Leis de Conservação são provadas.
- [ ] `python3 scripts/test_catalog.py --check-files` valida o catálogo sem inconsistências.
- [ ] A documentação e o catálogo JSON foram devidamente atualizados.
- [ ] O Código de Conduta (`CODE_OF_CONDUCT.md`) é respeitado integralmente.
