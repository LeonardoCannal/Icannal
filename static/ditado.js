// Notas por voz: coloca um botão de microfone embaixo de todo
// <textarea data-ditado>. O reconhecimento de fala é o do próprio navegador
// (Chrome, Edge, Safari): grátis e sem nada instalado. Onde o navegador não
// tiver, aparece só uma dica pra usar o microfone do teclado do celular.
//
// Comandos falados: "vírgula", "ponto final", "ponto de interrogação",
// "ponto de exclamação", "dois pontos", "nova linha".
(function () {
  const Reconhecimento = window.SpeechRecognition || window.webkitSpeechRecognition;
  const ativos = new Set();

  const COMANDOS = [
    [/\s*\bponto de interroga[cç][aã]o\b/gi, '?'],
    [/\s*\bponto de exclama[cç][aã]o\b/gi, '!'],
    [/\s*\bponto final\b/gi, '.'],
    [/\s*\bdois pontos\b/gi, ':'],
    [/\s*\bv[ií]rgula\b/gi, ','],
    [/\s*\b(nova linha|pr[oó]xima linha|par[aá]grafo)\b\s*/gi, '\n'],
  ];

  function aplicarComandos(texto) {
    let t = ' ' + texto.trim();
    for (const [re, sinal] of COMANDOS) t = t.replace(re, sinal);
    return t.replace(/([,.!?:])(?=[^\s\n])/g, '$1 ').replace(/ +\n/g, '\n').trim();
  }

  function capitalizarSePreciso(antes, texto) {
    const fimDeFrase = antes.trim() === '' || /[.!?]\s*$/.test(antes) || /\n\s*$/.test(antes);
    texto = texto.replace(/([.!?]\s+|\n)([a-zà-ú])/g, (m, a, b) => a + b.toUpperCase());
    return fimDeFrase ? texto.charAt(0).toUpperCase() + texto.slice(1) : texto;
  }

  // Coloca o texto falado onde está o cursor, com o espaço certo em volta.
  function inserir(campo, texto, novaLinha) {
    texto = aplicarComandos(texto);
    if (!texto) return;
    if (novaLinha && campo.value.trim()) {
      // começou a falar sem ter clicado no texto: vira uma linha nova no fim
      campo.value = campo.value.replace(/\s+$/, '') + '\n';
      campo.setSelectionRange(campo.value.length, campo.value.length);
    }
    const ini = campo.selectionStart ?? campo.value.length;
    const fim = campo.selectionEnd ?? campo.value.length;
    const antes = campo.value.slice(0, ini);
    const depois = campo.value.slice(fim);
    texto = capitalizarSePreciso(antes, texto);
    const precisaEspaco = antes && !/[\s\n]$/.test(antes) && !/^[,.!?:]/.test(texto);
    const novo = (precisaEspaco ? ' ' : '') + texto + (depois && !/^[\s\n]/.test(depois) ? ' ' : '');
    const max = campo.maxLength > 0 ? campo.maxLength : Infinity;
    const resultado = (antes + novo + depois).slice(0, max);
    campo.value = resultado;
    const pos = Math.min(antes.length + novo.length, resultado.length);
    campo.setSelectionRange(pos, pos);
    campo.dispatchEvent(new Event('input', { bubbles: true }));
  }

  const ICONE = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

  function preparar(campo) {
    if (campo.dataset.ditadoPronto) return;
    campo.dataset.ditadoPronto = '1';

    const barra = document.createElement('div');
    barra.className = 'ditado-barra';
    campo.insertAdjacentElement('afterend', barra);

    if (!Reconhecimento) {
      barra.innerHTML = '<span class="ditado-dica">Dica: dá pra ditar usando o microfone do teclado do celular.</span>';
      return;
    }

    barra.innerHTML =
      `<button type="button" class="btn-ditado" aria-pressed="false">${ICONE}<span class="btn-ditado-texto">Falar</span></button>` +
      '<span class="ditado-status" aria-live="polite"></span>';
    const botao = barra.querySelector('.btn-ditado');
    const rotulo = barra.querySelector('.btn-ditado-texto');
    const status = barra.querySelector('.ditado-status');

    let rec = null, ligado = false, semFala = 0, novaLinha = false, cursorNoCampo = false, ouviuAlgo = false;

    function mostrar(msg, erro) {
      status.textContent = msg || '';
      status.classList.toggle('erro', !!erro);
    }

    function visual(on) {
      botao.classList.toggle('gravando', on);
      botao.setAttribute('aria-pressed', on ? 'true' : 'false');
      rotulo.textContent = on ? 'Parar' : 'Falar';
    }

    function iniciarSessao() {
      // Uma frase por vez e religa sozinho: é o jeito que funciona igual no
      // Chrome do Android (o modo contínuo dele repete palavras) e no iPhone.
      rec = new Reconhecimento();
      rec.lang = 'pt-BR';
      rec.continuous = false;
      rec.interimResults = true;
      rec.maxAlternatives = 1;

      rec.onresult = (e) => {
        let parcial = '';
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) { inserir(campo, r[0].transcript, novaLinha); novaLinha = false; semFala = 0; ouviuAlgo = true; }
          else parcial += r[0].transcript;
        }
        mostrar(parcial ? '“' + parcial.trim() + '…”' : 'Ouvindo…');
      };
      rec.onerror = (e) => {
        if (e.error === 'no-speech') { semFala++; return; }
        if (e.error === 'aborted') return;
        ligado = false;
        const msgs = {
          'not-allowed': 'Libere o microfone para o i.cannal (no cadeado ao lado do endereço do site).',
          'service-not-allowed': 'O navegador bloqueou o ditado. Libere o microfone nas configurações.',
          'audio-capture': 'Nenhum microfone encontrado.',
          'network': 'O ditado precisa de internet. Confira a conexão e tente de novo.',
          'language-not-supported': 'Este navegador não entende português no ditado.',
        };
        mostrar(msgs[e.error] || 'Não deu para ouvir. Tente de novo.', true);
      };
      rec.onend = () => {
        if (ligado && semFala < 2) {
          try { rec.start(); return; } catch (_) { /* cai no parar abaixo */ }
        }
        if (ligado && semFala >= 2) mostrar(ouviuAlgo ? 'Pronto! Toque em Falar para continuar.' : 'Não ouvi nada. Toque em Falar e fale perto do celular.');
        else if (!status.classList.contains('erro')) mostrar('');
        ligado = false; rec = null; visual(false); ativos.delete(api);
      };
      rec.start();
    }

    function ligar() {
      ativos.forEach((a) => a.parar());
      ligado = true; semFala = 0; ouviuAlgo = false;
      mostrar('Ouvindo…');
      visual(true);
      ativos.add(api);
      // Se a pessoa não estava com o cursor no texto, a fala entra numa
      // linha nova no fim (não no meio de uma anotação antiga).
      novaLinha = !cursorNoCampo;
      if (novaLinha) campo.setSelectionRange(campo.value.length, campo.value.length);
      try { iniciarSessao(); }
      catch (_) { ligado = false; visual(false); ativos.delete(api); mostrar('Não foi possível ligar o microfone.', true); }
    }

    function parar() {
      ligado = false;
      if (rec) { try { rec.stop(); } catch (_) { /* já parou */ } }
      visual(false);
      ativos.delete(api);
    }

    const api = { parar };
    campo._ditado = api;

    // guardado antes do clique tirar o foco do campo
    botao.addEventListener('pointerdown', () => { cursorNoCampo = document.activeElement === campo; });
    botao.addEventListener('keydown', () => { cursorNoCampo = false; });
    campo.addEventListener('blur', () => { setTimeout(() => { if (document.activeElement !== botao) cursorNoCampo = false; }, 0); });
    botao.addEventListener('click', () => { if (ligado) parar(); else { ligar(); } cursorNoCampo = false; });
    // digitou no meio: o texto falado continua entrando onde o cursor está
    campo.addEventListener('keydown', (e) => { if (e.key === 'Escape' && ligado) { e.stopPropagation(); parar(); } });

    // Fechou o modal (por qualquer caminho): desliga o microfone.
    const overlay = campo.closest('.modal-overlay');
    if (overlay) {
      new MutationObserver(() => { if (!overlay.classList.contains('aberto') && ligado) parar(); })
        .observe(overlay, { attributes: true, attributeFilter: ['class'] });
    }
  }

  function prepararTodos(raiz) {
    (raiz || document).querySelectorAll('textarea[data-ditado]').forEach(preparar);
  }

  window.icannalDitado = {
    preparar: prepararTodos,
    pararTudo: () => ativos.forEach((a) => a.parar()),
    disponivel: !!Reconhecimento,
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden) window.icannalDitado.pararTudo(); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => prepararTodos());
  else prepararTodos();
})();
