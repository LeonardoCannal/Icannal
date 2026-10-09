# i.cannal — Mapeamento de médicos prescritores

Sistema de busca de médicos por região, com captação de dados de 5 fontes
públicas: Doctoralia, Sechat, Ama-me, Kaya Doc e Cannaceia. Traz nome, CRM,
especialidade, cidade, UF, endereço (quando disponível) e telefone (quando
disponível). Tem cadastro com CPF, login, painel de administrador e um
Painel Médico pessoal por usuário (controle de prospecção/visita).

## Cadastro, login e painel admin

- Ao acessar o site, quem não estiver logado é levado pra tela de login.
- **O login é feito por CPF + senha** (não por e-mail). O e-mail continua
  sendo pedido só no cadastro, como identificação adicional.
- Quem não tem conta clica em "Cadastre-se" (nome, e-mail, **CPF**, senha
  e confirmação de senha). O CPF é validado pelo algoritmo oficial dos
  dígitos verificadores — CPF inválido ou já cadastrado é bloqueado.
- Os campos de senha (login e cadastro) têm um botão de "olhinho" pra
  mostrar/ocultar o que foi digitado.
- **Só o e-mail `leonardo@grupocannal.com` vira administrador
  automaticamente**, não importa a ordem de cadastro. Qualquer outra
  conta é usuário comum. Pra adicionar outro admin fixo, é só me pedir
  (ou editar a lista `EMAILS_ADMIN` no `auth.py`).
- Administradores veem um link "Painel Admin" no menu, com a lista de
  todo mundo que já se cadastrou (nome, e-mail, CPF, data de cadastro,
  último login e quantos médicos tem no Painel Médico de cada um).
- Pra promover outra pessoa a admin depois, ainda não tem uma tela pra
  isso — é preciso editar o banco de dados diretamente. Avise se quiser
  que eu monte essa tela também.

## Painel Médico (por usuário)

- A aba "Histórico" foi substituída pela aba **"Painel Médico"**.
- Na busca, cada médico encontrado tem um botão "Adicionar ao painel".
  Ele entra automaticamente na coluna **Prospectados**.
- Se esse mesmo médico aparecer de novo numa busca futura (do mesmo
  usuário), em vez do botão aparece a marca **"Presente no painel
  médico"** — a identificação é feita pelo CRM (ou por nome+cidade,
  quando o CRM não foi encontrado).
- Na aba Painel Médico, cada card tem um botão pra mover o médico entre
  as colunas **Prospectados** e **Visitados**.
- O painel é individual — cada usuário só vê e mexe no seu próprio.
- **Só administradores** podem exportar o painel de qualquer usuário
  pra Excel, direto pela tela de Painel Admin (botão "Exportar" ao lado
  de cada usuário que já tem algo no painel). A exportação geral de
  Excel que existia antes na tela de busca foi removida.

## Links externos

Os resultados da busca não têm mais links clicáveis pro perfil do
médico em nenhum site externo (Doctoralia incluso) — só o texto com os
dados captados.

### Banco de dados: local (SQLite) ou externo (PostgreSQL)

O i.cannal escolhe sozinho qual banco usar, sem você mudar nada no código:

- **Rodando local ou no `.exe`**: usa um arquivo `cannal.db` (SQLite) do
  lado do programa. Não precisa configurar nada.
- **Rodando no Render com um banco PostgreSQL conectado**: usa esse banco
  externo automaticamente, através da variável de ambiente
  `DATABASE_URL` que o Render preenche sozinho. Os cadastros ficam
  permanentes, mesmo quando o serviço reinicia ou é atualizado.

**Como conectar o PostgreSQL no Render (opção 1):**

1. No painel do Render, clique em "New" > "PostgreSQL". Dê um nome (ex:
   "cannal-db") e escolha o plano gratuito.
2. Espere o banco ser criado (1-2 minutos).
3. Entre no seu Web Service do i.cannal (o mesmo de sempre) e vá em
   "Environment". Adicione uma variável `DATABASE_URL` com o valor da
   "Internal Database URL" que aparece na página do banco que você
   acabou de criar (o Render às vezes já sugere isso automaticamente
   como "Add from Database" — se aparecer essa opção, é só usar).
4. Salve — o Render reinicia o serviço sozinho. A partir daí, os
   cadastros vão pro banco PostgreSQL, e a tabela de usuários é criada
   automaticamente na primeira vez que o i.cannal roda.

**Como conectar o Supabase (opção 2):**

1. Crie uma conta em supabase.com e um projeto novo (plano gratuito).
2. No painel do projeto, vá em "Connect" (ou "Project Settings" >
   "Database").
3. Copie a "Connection string" no modo **Transaction pooler** (não a
   "Direct connection") — isso é importante: o i.cannal abre uma conexão
   nova a cada busca, e o modo pooler aguenta bem esse tipo de uso; o
   modo direto tem um limite baixo de conexões simultâneas no plano
   gratuito e pode travar rápido.
4. A connection string vem parecida com:
   `postgresql://postgres.xxxxxxxx:[SUA-SENHA]@aws-0-xxxxx.pooler.supabase.com:6543/postgres`
   Troque `[SUA-SENHA]` pela senha do banco que você definiu ao criar o
   projeto.
5. No Render, vá em "Environment" do seu Web Service e adicione essa
   string completa como `DATABASE_URL`.
6. Salve — o Render reinicia sozinho, e a tabela de usuários é criada
   automaticamente na primeira vez que o i.cannal roda contra esse banco.

**Importante:** eu não tenho como testar a conexão com um PostgreSQL ou
Supabase de verdade no meu ambiente (não tenho acesso a um banco real
aqui), então revisei o código com cuidado, mas o primeiro teste de
verdade vai ser você criando uma conta no site publicado. Se der algum
erro, me manda a mensagem que aparecer que a gente ajusta.

Também é importante definir a variável de ambiente `SECRET_KEY` com um
valor fixo quando for hospedar (no Render: Settings > Environment).
Sem isso, toda vez que o servidor reiniciar, todo mundo é deslogado
automaticamente, porque uma chave nova é gerada a cada vez.

## Start Command no Render (importante pra buscas com várias especialidades)

Use este Start Command no Render, em vez de só `gunicorn app:app`:

```
gunicorn app:app --timeout 120 --workers 2 --threads 4
```

O `--timeout 120` dá mais tempo pro servidor responder buscas grandes
(muitas especialidades selecionadas de uma vez) antes de desistir. As
buscas já rodam em paralelo internamente, mas com conexões de internet
mais lentas ou sites bloqueando temporariamente, uma folga extra ajuda.

## Transformar em programa (.exe) para deixar só no PC

Se você quiser um arquivo que abre o i.cannal com um duplo clique (sem precisar
abrir terminal nem digitar comandos), dá pra empacotar tudo com o PyInstaller:

1. No terminal, dentro da pasta do projeto, instale o PyInstaller:
   ```
   pip install pyinstaller
   ```
2. Gere o executável:
   ```
   pyinstaller --onefile --add-data "templates;templates" --add-data "static;static" --name i.cannal app.py
   ```
   (no Mac/Linux, troque o `;` por `:` nas duas partes)
3. Espere terminar. O arquivo final aparece em `dist/i.cannal.exe`.
4. Dê dois cliques em `i.cannal.exe` — ele abre uma janela preta (é o servidor
   rodando) e, sozinho, abre o navegador já na tela do sistema.
5. Pra fechar o programa, é só fechar aquela janela preta.

Você pode copiar só o `i.cannal.exe` pra área de trabalho ou qualquer pasta —
ele não precisa mais dos outros arquivos do projeto pra funcionar.

## Como rodar

1. Abra a pasta `catalina` no VS Code.
2. Crie um ambiente virtual (opcional, mas recomendado):
   ```
   python -m venv venv
   venv\Scripts\activate      (Windows)
   source venv/bin/activate   (Mac/Linux)
   ```
3. Instale as dependências:
   ```
   pip install -r requirements.txt
   ```
4. Rode o servidor:
   ```
   python app.py
   ```
5. Acesse no navegador: http://localhost:5000

## Como funciona

- `app.py`: servidor Flask. Serve a página e expõe `/api/buscar`,
  `/api/painel` (listar), `/api/painel/adicionar`, `/api/painel/mover` e
  `/admin/exportar/<id>` (exportação do painel de um usuário, admin only).
- `auth.py`: cadastro (com CPF), login, sessão, painel admin e toda a
  lógica do Painel Médico (adicionar, mover, listar, contar por usuário).
  Usa SQLite local por padrão, ou PostgreSQL externo automaticamente se
  a variável de ambiente `DATABASE_URL` estiver definida.
- `scraper.py`: faz a captação em cada fonte (Doctoralia, Sechat, Ama-me,
  Kaya Doc, Cannaceia) e devolve os médicos encontrados, já filtrados pela
  cidade pesquisada. Pro Doctoralia, visita a página individual de cada
  médico (em paralelo) pra tentar captar o telefone, que só aparece ali.
- `templates/index.html`: interface (identidade visual i.cannal: fundo #343C4C
  na barra do logo, #88AD36 na barra abaixo, tipografia Montserrat, marca
  d'água do ícone verde), com filtro de especialidades (com busca por
  texto e limite de 3 por vez), abas de Buscar e Painel Médico, e
  resultados sem link para sites externos.
- `templates/login.html`, `cadastro.html`, `admin.html`, `erro_acesso.html`:
  telas de autenticação e o painel de usuários cadastrados (com CPF e
  exportação do painel de cada um).

## Confiabilidade por fonte

- **Doctoralia**: captação testada e filtrada por cidade (usa a própria URL
  do perfil do médico pra confirmar a cidade). Não expõe telefone.
- **Sechat**: captação testada com base na estrutura pública da lista de
  prescritores. É a única fonte que costuma trazer telefone e endereço
  completo diretamente.
- **Ama-me, Kaya Doc, Cannaceia**: captação best-effort/experimental. Essas
  páginas parecem carregar a lista de médicos de forma mais dinâmica (tipo
  um aplicativo dentro do site), então o scraper pode retornar poucos ou
  nenhum resultado até ser ajustado com base no que aparecer de verdade ao
  rodar. Se isso acontecer, é só avisar com o que você vê na tela (ou um
  print) que dá pra ajustar o `scraper.py`.

## Limitações conhecidas

- **CRM**: extraído por regras heurísticas (regex) em várias fontes. Pode
  vir como "Não encontrado" em alguns casos.
- **Telefone**: o Sechat é a fonte mais confiável (mostra na própria
  página de listagem). No Doctoralia, o telefone só existe na página
  INDIVIDUAL de cada médico (por trás do botão "Mostrar número de
  telefone") — por isso o i.cannal visita o perfil de cada médico
  encontrado (em paralelo, pra não demorar demais) só pra captar esse
  dado. Isso deixa a busca no Doctoralia um pouco mais lenta que antes,
  e se o médico não tiver deixado o telefone visível daquele jeito
  específico, aparece como "Não disponível".
- **Limite de 3 especialidades por busca**: é intencional, pra manter as
  buscas rápidas e não sobrecarregar os sites de origem com muitas
  requisições de uma vez.
- **Fontes com erro não travam a busca**: se uma fonte falhar (bloqueio
  temporário, mudança na página), as outras continuam normalmente — um
  aviso aparece na tela informando quais fontes não responderam.
- **Estrutura dos sites**: qualquer um deles pode mudar o HTML das páginas
  a qualquer momento, o que pode quebrar a captação daquela fonte
  especificamente — pode ser necessário ajustar `scraper.py`.
- **Termos de uso**: scraping automatizado pode não estar de acordo com os
  Termos de Uso de cada site. Recomendado para uso pessoal/interno, com
  volume baixo de requisições.

## Variáveis de ambiente do servidor

| Variável | Pra quê |
|---|---|
| `SECRET_KEY` | Chave fixa e secreta das sessões de login (obrigatória em produção; sem ela o login cai de forma intermitente). |
| `DATABASE_URL` | Conexão com o banco (Supabase/Postgres). Sem ela, o sistema usa um arquivo SQLite local. |
| `COOKIE_SEGURO=1` | Não é mais necessária: o cookie de login passa a ser "só HTTPS" sozinho quando o acesso é por HTTPS. Pode deixar ou tirar. |
| `DB_POOL=0` | Opcional. Desliga o reaproveitamento de conexões com o banco (volta a abrir uma conexão por consulta). Só use se aparecer algum erro de conexão com o Supabase. |
| `DB_POOL_MAX` | Opcional (padrão 4). Quantas conexões com o banco cada processo do servidor mantém abertas. |
| `WHATSAPP_TELEFONE` | Opcional. Número de WhatsApp (com código do país, só números — ex.: `5515996609680`) que recebe um aviso a cada pedido de "esqueci minha senha". Sem essa variável, o aviso simplesmente não é enviado (a notificação continua aparecendo normal no sino do painel admin). |
| `WHATSAPP_APIKEY` | Opcional, usada junto com `WHATSAPP_TELEFONE`. A chave que o [CallMeBot](https://www.callmebot.com) devolve depois de você autorizar o bot no seu WhatsApp. |
| `TELEGRAM_USUARIO` | Opcional. Seu nome de usuário do Telegram (ex.: `@seunome`) para receber os avisos importantes do site: novo cadastro aguardando aprovação, pedido de senha, ações de outros admins (aprovar, dar acesso de admin, excluir conta), muitas senhas erradas, fonte de busca fora do ar, erro no site, falha no backup e (com o monitor) site/banco fora do ar. Antes, mande `/start` para o `@CallMeBot_txtbot` no Telegram. Não precisa de chave. |

## Monitor (site fora do ar, disco, certificado)

Um verificador roda no servidor a cada 5 minutos e avisa no Telegram se o
site ou o banco pararem de responder. Instalação em
[`monitor/LEIA-ME.md`](monitor/LEIA-ME.md).

## Backup do banco

Backup automático diário (3h), com 14 dias de cópias e aviso no WhatsApp se falhar. Instalação, como baixar uma cópia e como restaurar: veja `backup/LEIA-ME.md`.

## Segurança embutida

- Depois de 8 tentativas de login erradas seguidas no mesmo CPF, o login espera 10 minutos.
- Textos de médicos e usuários são escapados antes de aparecer na tela.
- Datas de agendamento são validadas (formato, passado, limite de 2 anos).
