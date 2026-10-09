# Monitor do i.cannal (avisos no Telegram)

A cada 5 minutos o servidor confere se está tudo bem e te avisa no Telegram
(e/ou WhatsApp) quando:

- o **site sai do ar**, ou o **banco de dados** (Supabase) para de responder
  (e avisa de novo quando voltar);
- o **disco** do servidor passa de 90% de uso;
- o **cadeado HTTPS** está perto de vencer sem ter sido renovado.

Um mesmo problema não gera enxurrada: 1 aviso quando começa e um lembrete a
cada 6 horas enquanto continuar.

Usa o mesmo `TELEGRAM_USUARIO` do `/etc/cannal.env`. O monitor só olha;
nunca altera nada no site nem no banco.

---

## Instalação (uma vez só)

Antes, atualize o sistema como sempre (`git pull` etc.), pra pasta `monitor`
chegar no servidor. Depois, um comando de cada vez:

```
sudo -i
```
```
cp /home/cannal/cannal/monitor/icannal-monitor.service /etc/systemd/system/
```
```
cp /home/cannal/cannal/monitor/icannal-monitor.timer /etc/systemd/system/
```
```
systemctl daemon-reload
```
```
systemctl enable --now icannal-monitor.timer
```

Conferir se rodou:
```
systemctl start icannal-monitor.service
```
```
journalctl -u icannal-monitor -n 10 --no-pager
```
Deve aparecer **Site OK**. Depois digite `exit` pra sair do root.

---

## O que ele NÃO pega

Se o servidor inteiro desligar (a Hostinger cair, por exemplo), o monitor
desliga junto e não consegue avisar. Pra cobrir isso, dá pra usar um
serviço externo gratuito (ex.: UptimeRobot) apontando para
`https://icannal.com.br/saude`.
