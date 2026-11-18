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

## O que o i.cannal faz com cada médico recebido

| Situação | O que acontece |
|---|---|
| Promotor (CPF) existe no i.cannal e o médico não está em nenhum painel | Entra no painel desse promotor como **Prospectado** |
| O médico já está no painel desse mesmo promotor | Só liga os dois cadastros |
| O médico está no painel de **outro** promotor | Não mexe; vira conflito em Admin → Integração |
| Não há promotor com esse CPF no i.cannal (ou a conta está desativada) | Não mexe; vira conflito. Resolve sozinho quando o promotor for criado |
