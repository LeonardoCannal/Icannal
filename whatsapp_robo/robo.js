// Robô do WhatsApp do i.cannal.
//
// Abre o WhatsApp Web num navegador invisível (o mesmo jeito do Comunica),
// conecta pelo QR code e envia as mensagens que o i.cannal pedir.
//
// Para poupar o servidor, o navegador só fica aberto quando precisa: depois
// de alguns minutos sem enviar nada o robô entra "em espera" (fecha o
// navegador, mas guarda a conexão) e o i.cannal o acorda na hora do envio,
// sem precisar ler o QR code de novo.
// Só escuta no próprio servidor (127.0.0.1) e só obedece a quem mandar a
// chave certa (WHATSAPP_ROBO_CHAVE, que fica em /etc/cannal.env).
//
// Endereços (todos pedem o cabeçalho X-Robo-Chave):
//   GET  /status       → { estado, qr, numero, nome, erro }
//   POST /conectar     → liga o robô (gera o QR se ainda não estiver conectado)
//   POST /dormir       → fecha o navegador e fica em espera (guarda a conexão)
//   POST /desconectar  → sai do WhatsApp neste servidor e apaga a sessão
//   POST /enviar       → { numero, texto }  →  { ok, para } ou { ok:false, erro }
'use strict';

const http = require('http');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const PORTA = parseInt(process.env.WHATSAPP_ROBO_PORTA || '3011', 10);
const CHAVE = process.env.WHATSAPP_ROBO_CHAVE || '';
const PASTA_SESSAO = process.env.WHATSAPP_ROBO_SESSAO || path.join(__dirname, 'sessao');
const CHROME = process.env.WHATSAPP_ROBO_CHROME || undefined; // vazio = o Chrome que vem com o robô
const MIN_PARADO_PARA_DORMIR = parseFloat(process.env.WHATSAPP_ROBO_DORMIR_MIN || '10');
const MIN_QR_ESPERANDO = parseFloat(process.env.WHATSAPP_ROBO_QR_MIN || '5');
const ARQ_INFO = path.join(PASTA_SESSAO, 'icannal-info.json'); // número/nome, pra mostrar em espera

if (CHAVE.length < 16) {
  console.error('Falta WHATSAPP_ROBO_CHAVE (mínimo 16 caracteres) em /etc/cannal.env. Rode o instalar.sh.');
  process.exit(1);
}

// Carregado só aqui (nos testes, um WhatsApp de mentira entra no lugar).
const wwebjs = require(process.env.WHATSAPP_ROBO_MODULO || 'whatsapp-web.js');
const QRCode = require(process.env.WHATSAPP_ROBO_MODULO_QR || 'qrcode');

const estado = { estado: 'desligado', qr: null, numero: null, nome: null, erro: null, desde: new Date().toISOString() };
let cliente = null;
let fila = Promise.resolve(); // um envio por vez
let ultimaAtividade = Date.now();
let fechandoDeProposito = false;

function mudar(novo, extra = {}) {
  Object.assign(estado, { estado: novo, desde: new Date().toISOString() }, extra);
  console.log(`[robo] ${novo}${extra.erro ? ' — ' + extra.erro : ''}`);
}

function temSessaoSalva() {
  try { return fs.readdirSync(PASTA_SESSAO).some((n) => n.startsWith('session')); } catch (_) { return false; }
}

function lerInfo() {
  try { return JSON.parse(fs.readFileSync(ARQ_INFO, 'utf8')); } catch (_) { return {}; }
}

function guardarInfo(info) {
  try { fs.mkdirSync(PASTA_SESSAO, { recursive: true }); fs.writeFileSync(ARQ_INFO, JSON.stringify(info)); } catch (_) { /* só informativo */ }
}

function estadoParado() {
  // sem navegador aberto: "em espera" se já conectou antes, senão desligado
  if (temSessaoSalva()) {
    const info = lerInfo();
    return ['dormindo', { qr: null, numero: info.numero || null, nome: info.nome || null }];
  }
  return ['desligado', { qr: null, numero: null, nome: null }];
}

async function ligar() {
  if (cliente) return;
  mudar('iniciando', { qr: null, erro: null });
  const c = new wwebjs.Client({
    authStrategy: new wwebjs.LocalAuth({ clientId: 'icannal', dataPath: PASTA_SESSAO }),
    puppeteer: {
      headless: true,
      executablePath: CHROME,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--no-first-run'],
    },
  });
  cliente = c;

  c.on('qr', async (texto) => {
    try {
      const imagem = await QRCode.toDataURL(texto, { margin: 1, width: 300 });
      mudar('qr', { qr: imagem });
    } catch (e) {
      mudar('erro', { erro: 'Não consegui desenhar o QR code: ' + e.message });
    }
  });
  c.on('authenticated', () => mudar('autenticando', { qr: null }));
  c.on('ready', () => {
    const info = c.info || {};
    const dados = { numero: info.wid ? info.wid.user : null, nome: info.pushname || null };
    guardarInfo(dados);
    ultimaAtividade = Date.now();
    mudar('conectado', { qr: null, erro: null, ...dados });
  });
  c.on('auth_failure', (msg) => mudar('erro', { qr: null, erro: 'O WhatsApp recusou a conexão: ' + msg }));
  c.on('disconnected', async (motivo) => {
    if (fechandoDeProposito) return; // fomos nós que fechamos (em espera)
    mudar('desligado', { qr: null, numero: null, nome: null, erro: motivo ? 'Desconectado: ' + motivo : null });
    const velho = cliente; cliente = null;
    try { await velho.destroy(); } catch (_) { /* já fechado */ }
  });

  try {
    await c.initialize();
  } catch (e) {
    cliente = null;
    mudar('erro', { qr: null, erro: 'O navegador do robô não abriu: ' + e.message });
    try { await c.destroy(); } catch (_) { /* nada */ }
  }
}

// Fecha o navegador sem sair do WhatsApp: na próxima vez reconecta sozinho.
async function dormir(motivo) {
  const c = cliente;
  cliente = null;
  if (c) {
    fechandoDeProposito = true;
    try { await c.destroy(); } catch (_) { /* já fechado */ }
    fechandoDeProposito = false;
  }
  const [novo, extra] = estadoParado();
  mudar(novo, { ...extra, erro: motivo || null });
}

// A cada minuto: navegador aberto e parado há muito tempo → espera.
setInterval(() => {
  const parado = (Date.now() - ultimaAtividade) / 60000;
  const naFase = (Date.now() - Date.parse(estado.desde)) / 60000;
  if (estado.estado === 'conectado' && parado >= MIN_PARADO_PARA_DORMIR) {
    dormir();
  } else if (estado.estado === 'qr' && naFase >= MIN_QR_ESPERANDO) {
    dormir('O QR code expirou sem ninguém ler. Clique em Conectar WhatsApp para gerar outro.');
  }
}, parseFloat(process.env.WHATSAPP_ROBO_VERIFICA_SEG || '60') * 1000).unref();

async function desligar() {
  const c = cliente;
  cliente = null;
  if (c) {
    try { await c.logout(); } catch (_) { /* pode já estar desconectado */ }
    try { await c.destroy(); } catch (_) { /* nada */ }
  }
  try { fs.rmSync(PASTA_SESSAO, { recursive: true, force: true }); } catch (_) { /* nada */ }
  mudar('desligado', { qr: null, numero: null, nome: null, erro: null });
}

// Número brasileiro → só dígitos com o 55 na frente.
function normalizar(numero) {
  let d = String(numero || '').replace(/\D/g, '');
  if (d.startsWith('00')) d = d.slice(2);
  if (d.length === 10 || d.length === 11) d = '55' + d;
  return d;
}

function enviar(numero, texto) {
  const tarefa = fila.then(async () => {
    if (!cliente || estado.estado !== 'conectado') {
      return { ok: false, erro: 'O WhatsApp não está conectado.' };
    }
    const d = normalizar(numero);
    if (d.length < 12 || d.length > 13) return { ok: false, erro: 'Número de WhatsApp inválido.' };
    // getNumberId resolve o 9º dígito e confirma que o número tem WhatsApp
    const id = await cliente.getNumberId(d);
    ultimaAtividade = Date.now();
    if (!id) return { ok: false, erro: 'Esse número não tem WhatsApp.' };
    await cliente.sendMessage(id._serialized, texto);
    ultimaAtividade = Date.now();
    return { ok: true, para: id.user || d };
  }).catch((e) => ({ ok: false, erro: 'Falha ao enviar: ' + (e && e.message ? e.message : e) }));
  fila = tarefa.then(() => undefined);
  return tarefa;
}

function chaveConfere(req) {
  const recebida = Buffer.from(String(req.headers['x-robo-chave'] || ''));
  const certa = Buffer.from(CHAVE);
  return recebida.length === certa.length && crypto.timingSafeEqual(recebida, certa);
}

function responder(res, codigo, corpo) {
  const json = JSON.stringify(corpo);
  res.writeHead(codigo, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(json);
}

function lerCorpo(req) {
  return new Promise((ok, falha) => {
    let dados = '';
    req.on('data', (p) => { dados += p; if (dados.length > 64 * 1024) { falha(new Error('grande demais')); req.destroy(); } });
    req.on('end', () => { try { ok(dados ? JSON.parse(dados) : {}); } catch (e) { falha(e); } });
    req.on('error', falha);
  });
}

const servidor = http.createServer(async (req, res) => {
  if (!chaveConfere(req)) return responder(res, 401, { ok: false, erro: 'Chave errada.' });
  try {
    if (req.method === 'GET' && req.url === '/status') return responder(res, 200, estado);
    if (req.method === 'POST' && req.url === '/conectar') { ultimaAtividade = Date.now(); ligar(); return responder(res, 200, estado); }
    if (req.method === 'POST' && req.url === '/dormir') { await dormir(); return responder(res, 200, estado); }
    if (req.method === 'POST' && req.url === '/desconectar') { await desligar(); return responder(res, 200, estado); }
    if (req.method === 'POST' && req.url === '/enviar') {
      const corpo = await lerCorpo(req);
      const texto = String(corpo.texto || '').slice(0, 4000);
      if (!texto.trim()) return responder(res, 400, { ok: false, erro: 'Mensagem vazia.' });
      return responder(res, 200, await enviar(corpo.numero, texto));
    }
    return responder(res, 404, { ok: false, erro: 'Endereço não existe.' });
  } catch (e) {
    return responder(res, 400, { ok: false, erro: 'Pedido inválido.' });
  }
});

servidor.listen(PORTA, '127.0.0.1', () => {
  console.log(`[robo] ouvindo em 127.0.0.1:${PORTA}`);
  // Já estava conectado antes: fica em espera (sem abrir o navegador) até o
  // i.cannal precisar enviar.
  const [novo, extra] = estadoParado();
  mudar(novo, extra);
});

async function encerrar() {
  servidor.close();
  if (cliente) { try { await cliente.destroy(); } catch (_) { /* nada */ } }
  process.exit(0);
}
process.on('SIGTERM', encerrar);
process.on('SIGINT', encerrar);
