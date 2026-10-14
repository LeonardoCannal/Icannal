# Tradutor do i.cannal (LibreTranslate)

Traduz os artigos científicos (títulos e resumos) do inglês para o
português **dentro do próprio servidor**. É grátis para sempre: não tem
conta, nem cartão, nem cota. Os textos também não saem do servidor.

Usa em torno de 1 GB de memória e uns 2 GB de disco.

Digite **um comando de cada vez** e espere terminar antes do próximo.

---

## Instalação (uma vez só)

Antes, atualize o i.cannal como sempre (`git pull` etc.) para a pasta
`tradutor` chegar no servidor.

### 1. Memória de reserva (recomendado)

O servidor não tem memória de reserva ("swap"). Crie 2 GB, para o site
nunca ficar sem memória:

```
sudo fallocate -l 2G /swapfile
```
```
sudo chmod 600 /swapfile
```
```
sudo mkswap /swapfile
```
```
sudo swapon /swapfile
```
```
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Para conferir, rode `free -h`: a linha **Swap** deve mostrar **2.0Gi**.

### 2. Instalar o tradutor

```
python3 -m venv /home/cannal/libretranslate-venv
```
```
/home/cannal/libretranslate-venv/bin/pip install --upgrade pip
```
```
/home/cannal/libretranslate-venv/bin/pip install libretranslate
```
(esse último leva alguns minutos)

### 3. Ligar o tradutor

```
sudo cp /home/cannal/cannal/tradutor/libretranslate.service /etc/systemd/system/
```
```
sudo systemctl daemon-reload
```
```
sudo systemctl enable --now libretranslate
```

Na primeira vez ele baixa o "dicionário" inglês ↔ português, o que leva
uns 2 a 5 minutos. Para acompanhar:
```
journalctl -u libretranslate -f
```
Quando aparecer **Running on** ou **Serving on**, está pronto. Aperte
**Ctrl + C** para sair do acompanhamento (o tradutor continua ligado).

### 4. Testar

```
curl -s -X POST http://127.0.0.1:5000/translate -H 'Content-Type: application/json' -d '{"q":"Medical cannabis reduces chronic pain","source":"en","target":"pt"}'
```
Deve responder algo como `{"translatedText":"A cannabis medicinal reduz a dor crônica"}`.

### 5. Avisar o i.cannal que o tradutor existe

```
sudo nano /etc/cannal.env
```
No final do arquivo, acrescente:
```
TRADUTOR_URL=http://127.0.0.1:5000
```
Salve com **Ctrl + O**, **Enter** e **Ctrl + X**. Depois:
```
sudo systemctl restart cannal
```

Pronto: na página **Artigos**, os títulos do PubMed aparecem em português
e o botão **"Ler resumo em português"** traduz o resumo.

---

## Se algo der errado

- O i.cannal continua funcionando normalmente. Os artigos só aparecem em
  inglês até o tradutor voltar, e você recebe um aviso no Telegram.
- Ver o estado: `systemctl status libretranslate`
- Reiniciar: `sudo systemctl restart libretranslate`
- Desligar de vez: `sudo systemctl disable --now libretranslate` e apagar
  a linha `TRADUTOR_URL` do `/etc/cannal.env` (depois `sudo systemctl restart cannal`).
