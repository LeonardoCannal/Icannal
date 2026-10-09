# Backup automático do banco do i.cannal

Todo dia às 3h (horário de Brasília), o servidor tira uma cópia completa do
banco do Supabase e guarda em `/var/backups/icannal`. Ficam os últimos
14 dias; os mais antigos são apagados sozinhos. Se algum dia falhar, chega
um aviso no WhatsApp (se o CallMeBot estiver configurado).

O backup só **lê** o banco. Ele nunca altera nem apaga nada no Supabase.

Digite **um comando de cada vez** e espere terminar antes do próximo.

---

## Instalação (uma vez só)

Entre no servidor (`ssh root@IP_DO_VPS`) e rode:

### 1. Instalar a ferramenta de backup do Postgres

```
apt install -y postgresql-common
```
```
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
```
(se ele pedir confirmação, aperte Enter)
```
apt install -y postgresql-client-17
```

### 2. Atualizar o sistema (traz os arquivos de backup)

```
cd /home/cannal/cannal
```
```
git pull
```

### 3. Ligar o agendamento diário

```
cp /home/cannal/cannal/backup/icannal-backup.service /etc/systemd/system/
```
```
cp /home/cannal/cannal/backup/icannal-backup.timer /etc/systemd/system/
```
```
systemctl daemon-reload
```
```
systemctl enable --now icannal-backup.timer
```

### 4. Rodar o primeiro backup agora, pra conferir

```
systemctl start icannal-backup.service
```
```
journalctl -u icannal-backup -n 20 --no-pager
```
Deve aparecer uma linha começando com **Backup OK**. Se aparecer
**BACKUP FALHOU**, me mande a mensagem (ela não mostra sua senha).

Pra ver as cópias guardadas:
```
ls -lh /var/backups/icannal
```

E pra conferir quando vai rodar o próximo:
```
systemctl list-timers icannal-backup.timer
```

---

## Baixar uma cópia pro seu computador (recomendado 1x por semana)

No **PowerShell do seu computador** (não no servidor):
```
scp root@IP_DO_VPS:/var/backups/icannal/NOME_DO_ARQUIVO.sql.gz .
```
Troque `NOME_DO_ARQUIVO` pelo nome que aparece no `ls -lh` acima. O arquivo
cai na pasta onde o PowerShell está aberto. Guarde num lugar seguro: ele
tem os dados de todos os usuários e médicos.

---

## Testar a restauração (faça 1 vez, sem tocar no banco real)

1. No Supabase, crie um **projeto novo** (gratuito), só pra teste.
2. Nele, clique em **Connect** e copie o endereço de conexão do tipo
   **Session pooler** (porta 5432). Troque `[YOUR-PASSWORD]` pela senha
   que você escolheu ao criar esse projeto de teste.
3. No servidor, restaure a cópia mais recente nesse projeto de teste:
```
gunzip -c /var/backups/icannal/NOME_DO_ARQUIVO.sql.gz | psql "ENDERECO_DO_PROJETO_DE_TESTE" -v ON_ERROR_STOP=1
```
4. Abra o **Table Editor** do projeto de teste e confira se as tabelas
   (`usuarios`, `painel_medicos`...) estão lá com os dados.
5. Pronto. Pode apagar o projeto de teste.

> **Atenção:** nunca rode o comando de restauração apontando pro banco
> real. Restauração é só pra emergência ou pra teste num projeto separado.

---

## Em caso de emergência (banco real perdido)

Não tente sozinho: me chame com o nome do arquivo de backup mais recente.
O caminho é criar um projeto novo no Supabase, restaurar a cópia nele e
trocar a `DATABASE_URL` do `/etc/cannal.env` pro endereço novo.

---

## Configurações opcionais (em `/etc/cannal.env`)

| Variável | Pra quê |
|---|---|
| `BACKUP_DATABASE_URL` | Usar um endereço de banco diferente só pro backup. Normalmente não precisa: o script usa a `DATABASE_URL` e, se ela for do Supabase na porta 6543, troca sozinho pra 5432 (a 6543 não serve pra backup). |
| `WHATSAPP_TELEFONE` / `WHATSAPP_APIKEY` | As mesmas do aviso de "esqueci a senha". Com elas, uma falha no backup chega no seu WhatsApp. |
