# Contrato da integração i.cannal ↔ sistema interno

O i.cannal é quem chama: ele **envia** os médicos dos painéis e **busca** os
médicos cadastrados no sistema interno. O sistema interno só precisa ter os
dois endereços abaixo.

## Autenticação

Todo pedido do i.cannal chega com o cabeçalho:

```
Authorization: Bearer <chave gerada no sistema interno>
```

Chave errada ou revogada → `401`. Limite de pedidos → `429`. Tudo em JSON UTF-8.

## Regras que valem para os dois lados

- **Promotor = CPF.** Só os 11 dígitos, sem pontos nem traço.
- **Mesmo médico = CRM + UF.** O CRM vai só com números (sem "CRM", sem
  zeros à esquerda); a UF com 2 letras maiúsculas. Sem CRM, compare nome +
  cidade (sem acento, sem diferença de maiúsculas, ignorando "Dr."/"Dra.").
- Um médico nunca é cadastrado duas vezes.

## 1. Receber médicos do i.cannal

`POST /api/v1/medicos/sincronizar`

Corpo (até 100 médicos por pedido):

```json
{
  "medicos": [
    {
      "icannal_id": 123,
      "nome": "Dra. Maria Souza",
      "crm": "123456",
      "uf": "SP",
      "especialidade": "Pediatra",
      "cidade": "Sorocaba",
      "endereco": "Rua X, 100",
      "telefone": "1533330000",
      "telefone_pessoal": "",
      "situacao": "prospectado",
      "cliente": false,
      "promotor_cpf": "52998224725",
      "promotor_nome": "Ana Promotora"
    }
  ]
}
```

`situacao` pode ser: prospectado, agendado, visitado, revisitar.

Para cada médico:

1. Procure o promotor pelo `promotor_cpf`. Se não existir → `promotor_nao_encontrado`, e não cadastre.
2. Procure o médico por CRM + UF (ou nome + cidade). Se já existir → `ja_existe`, sem alterar nada.
3. Senão, cadastre o médico ligado a esse promotor → `criado`.

Resposta (um item por médico recebido, na mesma ordem):

```json
{
  "resultados": [
    { "icannal_id": 123, "status": "criado", "id": "987" },
    { "icannal_id": 124, "status": "ja_existe", "id": "555", "promotor_cpf": "39053344705", "promotor_nome": "Bruno" },
    { "icannal_id": 125, "status": "promotor_nao_encontrado" },
    { "icannal_id": 126, "status": "erro", "erro": "Sem nome" }
  ]
}
```

- `id` é o id do médico no sistema interno (texto ou número).
- Em `ja_existe`, mande `promotor_cpf` e `promotor_nome` de quem está com o
  médico no sistema interno: se for outro promotor, o i.cannal avisa o admin.
- Um médico com problema não pode derrubar o lote inteiro: responda os outros normalmente.
- Repetir o mesmo pedido não pode duplicar nada (o i.cannal pode reenviar se a conexão cair).

## 2. Entregar os médicos para o i.cannal

`GET /api/v1/medicos?alterado_desde=<ISO>&pagina=1&por_pagina=500`

- `alterado_desde` (opcional): só médicos criados ou alterados a partir desse momento (`>=`).
- `por_pagina`: até 1000.

Resposta:

```json
{
  "total": 1234,
  "pagina": 1,
  "por_pagina": 500,
  "proxima_pagina": 2,
  "dados": [
    {
      "id": "987",
      "nome": "Dra. Maria Souza",
      "crm": "123456",
      "uf": "SP",
      "especialidade": "Pediatra",
      "cidade": "Sorocaba",
      "endereco": "Rua X, 100",
      "telefone": "1533330000",
      "promotor_cpf": "52998224725",
      "promotor_nome": "Ana Promotora",
      "atualizado_em": "2026-11-18T13:45:00Z"
    }
  ]
}
```

- `proxima_pagina` é `null` na última página.
- `atualizado_em` em UTC, ISO 8601, com `Z` no final, sempre no mesmo formato
  (o i.cannal guarda o maior que recebeu e pede a partir dele na próxima vez).
- Ordene por `atualizado_em` e depois `id`.

## 3. Transferir médico de promotor

`POST /api/v1/medicos/transferir`

Usado pelo botão "Transferir no MICHAEL" (Admin → Integração), quando o
médico está com um promotor no i.cannal e com outro aqui. Passa o médico para
o promotor do CPF enviado.

```json
{ "id": "987", "promotor_cpf": "39053344705", "icannal_id": 124 }
```

Respostas (sempre `200`; `404` só se o endereço não existir):

```json
{ "ok": true, "id": "987", "promotor_cpf": "39053344705", "promotor_nome": "Bruno" }
{ "ok": false, "erro": "medico_nao_encontrado" }
{ "ok": false, "erro": "promotor_nao_encontrado" }
```

Atualize o `atualizado_em` do médico. Só muda o promotor do cadastro do
médico: pedidos, comissões e histórico já existentes não mudam.

## 4. Completar o CRM

`POST /api/v1/medicos/completar-crm`

Quando um médico que já está aqui sem CRM ganha o CRM no i.cannal:

```json
{ "id": "987", "crm": "123456", "uf": "SP", "icannal_id": 124 }
```

Respostas (sempre `200`):

```json
{ "ok": true }
{ "ok": false, "erro": "medico_nao_encontrado" }
{ "ok": false, "erro": "ja_tem_crm" }
{ "ok": false, "erro": "crm_em_uso" }
```

- Médico sem CRM → grava o CRM e a UF → `ok`. Se já tem exatamente esse CRM → `ok`.
- Já tem outro CRM → `ja_tem_crm`, sem alterar.
- Outro médico já tem esse CRM + UF → `crm_em_uso`, sem alterar.

## 5. Home do promotor

`GET /api/v1/promotores/<cpf>/home?competencia=AAAA-MM`

Os números de vendas de UM promotor (pelo CPF, 11 dígitos), os mesmos da Home
dele no MICHAEL. Sem `competencia` = mês atual. O i.cannal mostra isso na
página **Início** do próprio promotor e não grava nada no banco dele.

Promotor não encontrado: `{"ok": false, "erro": "promotor_nao_encontrado"}` (status 200).

```json
{
  "competencia": "2026-11",
  "em_andamento": true,
  "ate_dia": 18,
  "competencias": ["2026-11", "2026-10", "2026-09"],
  "indicadores": {
    "faturamento": {"valor": 8450.0, "variacao_pct": 184.9, "anterior_ate_dia": 2966.0, "anterior_mes_inteiro": 10700.0},
    "pedidos_pagos": {"valor": 5, "variacao_pct": 150.0},
    "ticket_medio": {"valor": 1690.0, "variacao_pct": 13.9},
    "frascos_vendidos": {"valor": 14, "variacao_pct": 133.3},
    "comissao": {"valor": 845.5, "variacao_pct": 184.9},
    "clientes_novos": {"valor": 2, "variacao_pct": 0, "pacientes_compraram": 5},
    "carteira": {"clientes": 438, "medicos": 43, "medicos_novos": 15}
  },
  "pedidos_pagos": [
    {"pedido": "10482", "cliente": "Nome do paciente", "pago_em": "2026-11-16T14:20:00Z", "pagamento": "Pix",
     "frascos": 3, "valor_base": 1890.0, "comissao": 189.0, "etapa": "Enviado"}
  ],
  "pacientes_recompra": {
    "vao_recomprar": 24, "acabaram_de_vencer": 2, "venceram_ha_mais_tempo": 385,
    "proximas": [{"nome": "Nome do paciente", "data": "2026-11-18"}],
    "lista": [
      {"nome": "Nome do paciente", "data": "2026-11-18", "situacao": "vai_recomprar",
       "medico_nome": "Nome do médico", "medico_crm": "123456", "medico_uf": "SP", "medico_cidade": "Sorocaba"}
    ]
  },
  "medicos": {
    "ja_prescreveram": 15, "nunca_prescreveram": 28,
    "ultimas_prescricoes": [{"nome": "Nome do médico", "data": "2026-11-09"}],
    "prescritores": [
      {"nome": "Nome do médico", "crm": "123456", "uf": "SP", "cidade": "Sorocaba", "ultima_prescricao": "2026-11-09",
       "pedidos_pagos_no_mes": 0}
    ]
  },
  "prescricoes_por_mes": {
    "no_mes": 1, "ultimos_12_meses": 57,
    "serie": [{"mes": "2025-12", "total": 4}, {"mes": "2026-11", "total": 1}]
  }
}
```

- `variacao_pct`: variação contra o mês anterior, no mesmo intervalo de dias quando o mês está em andamento (`null` se não há base).
- `competencias`: meses que têm dados, do mais novo para o mais antigo.
- `pacientes_recompra.lista`: os pacientes que vão recomprar e os que acabaram de vencer (`situacao`: `vai_recomprar` ou `acabou_de_vencer`), cada um com o médico que prescreveu. Até 300.
- `medicos.prescritores`: todos os médicos que já prescreveram para esse promotor. Até 500.
  `pedidos_pagos_no_mes`: quantos pedidos pagos de pacientes desse médico o promotor teve na competência (0 = sem venda no mês). O relatório diário do WhatsApp usa isso.
- `serie`: 12 meses, do mais antigo para o mais novo.
- Só esses campos: nada de CPF, telefone ou endereço de paciente.

O i.cannal junta isso com as visitas que o promotor registrou no mês (que
ficam no i.cannal) para mostrar quais médicos ele já visitou.

## O que o i.cannal faz com cada médico recebido

| Situação | O que acontece |
|---|---|
| Promotor (CPF) existe no i.cannal e o médico não está em nenhum painel | Entra no painel desse promotor como **Prospectado** |
| O médico já está no painel desse mesmo promotor | Só liga os dois cadastros |
| O médico está no painel de **outro** promotor | Não mexe; vira conflito em Admin → Integração |
| Não há promotor com esse CPF no i.cannal (ou a conta está desativada ou sem aprovação) | Ignora, sem conflito. Se o promotor for cadastrado aqui depois, os médicos dele entram sozinhos |
