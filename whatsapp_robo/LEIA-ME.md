# Robô do WhatsApp (relatório diário)

Abre o WhatsApp Web num navegador invisível do servidor, igual ao Comunica,
e envia os relatórios diários que o i.cannal pedir.

## Instalar (uma vez)

```
sudo bash /home/cannal/cannal/whatsapp_robo/instalar.sh
```

O script:
- confere o Node.js;
- instala o robô;
- cria a chave secreta entre o i.cannal e o robô em `/etc/cannal.env` (ela não aparece na tela);
- liga o serviço `icannal-whatsapp`, que sobe sozinho quando o servidor reinicia.

Depois: **i.cannal → Admin → Relatório diário → Conectar WhatsApp** e leia o
QR code pelo celular (WhatsApp → Aparelhos conectados → Conectar aparelho).

## Em espera (para poupar o servidor)

O navegador do robô só fica aberto quando precisa. Uns 10 minutos depois do
último envio ele fecha sozinho e o robô fica **em espera**, ainda conectado.
Na hora do relatório (ou num "Enviar agora" / teste), o i.cannal abre o
navegador de novo, o que leva alguns segundos. Não precisa ler o QR code de novo.

Para mudar o tempo, coloque em `/etc/cannal.env` (em minutos) e reinicie o robô:
```
WHATSAPP_ROBO_DORMIR_MIN=10
```

## Comandos úteis

| O quê | Comando |
| --- | --- |
| Ver se está rodando | `sudo systemctl status icannal-whatsapp` |
| Ver as últimas mensagens | `sudo journalctl -u icannal-whatsapp -n 50` |
| Reiniciar | `sudo systemctl restart icannal-whatsapp` |

## Bom saber

- Use um número da empresa. O robô aparece como um "aparelho conectado"
  nesse WhatsApp, igual ao WhatsApp Web.
- A sessão fica na pasta `whatsapp_robo/sessao/` do servidor. **Nunca suba essa
  pasta para o GitHub**: o `.gitignore` já a deixa de fora.
- O robô só escuta dentro do próprio servidor (127.0.0.1) e só obedece ao
  i.cannal, porque exige a chave.
