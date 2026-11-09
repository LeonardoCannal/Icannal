// Sino do admin: alertas de médicos "cliente" (já compraram do promotor) que
// foram agendados ou visitados. Entra como a 1ª seção do painel do sino nas
// telas do admin e no i.cannal (para admins). Ao abrir o sino, os alertas
// novos são marcados como lidos.
(function () {
  const painel = document.getElementById('notif-painel');
  const btn = document.getElementById('btn-notif');
  const badge = document.getElementById('notif-badge');
  if (!painel || !btn) return;

  const soClientes = painel.dataset.soClientes === '1'; // no i.cannal o sino só tem esses alertas
  const cabecalho = document.createElement('div');
  cabecalho.className = 'notif-cabecalho';
  cabecalho.textContent = 'Médicos clientes agendados ou visitados';
  const lista = document.createElement('div');
  lista.className = 'notif-lista notif-lista-clientes';
  lista.innerHTML = '<div class="notif-vazio">Carregando…</div>';
  painel.prepend(lista);
  painel.prepend(cabecalho);

  let naoLidos = 0;

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const ACAO = {
    agendado: (d) => 'agendou visita para ' + d,
    reagendado: (d) => 'reagendou a visita para ' + d,
    visitado: (d) => 'visitou' + (d ? ' — ' + d : ''),
  };

  function desenhar(alertas) {
    if (!alertas.length) {
      lista.innerHTML = '<div class="notif-vazio">Nenhum médico cliente agendado ou visitado ainda.</div>';
      return;
    }
    lista.innerHTML = alertas.map((a) => {
      const acao = (ACAO[a.tipo] || ((d) => a.tipo + ' ' + d))(a.detalhe);
      return `
        <div class="notif-item notif-item-cliente${a.lida ? '' : ' nao-lida'}">
          <div class="notif-item-info">
            <strong><span class="ponto-cliente" aria-hidden="true"></span>${esc(a.medico_nome)}</strong>
            <span>${esc(a.usuario_nome)} ${esc(acao)}</span>
            <span class="notif-quando">${esc(a.quando)}${a.medico_crm ? ' · ' + esc(a.medico_crm) : ''}</span>
          </div>
        </div>`;
    }).join('');
  }

  function mostrarBadge(n) {
    if (!badge) return;
    badge.textContent = n;
    badge.style.display = n > 0 ? 'flex' : 'none';
  }

  function carregar() {
    fetch('/admin/alertas-clientes')
      .then((r) => r.json())
      .then((d) => {
        naoLidos = d.nao_lidos || 0;
        desenhar(d.alertas || []);
        if (soClientes) mostrarBadge(naoLidos);
      })
      .catch(() => { lista.innerHTML = '<div class="notif-vazio">Não foi possível carregar.</div>'; });
  }

  carregar();

  // Abriu o sino: marca os alertas como lidos (o painel do sino continua
  // mostrando os últimos, só sem o destaque de "novo").
  btn.addEventListener('click', () => {
    if (!painel.classList.contains('aberto') || naoLidos === 0) return;
    const marcados = naoLidos;
    naoLidos = 0;
    fetch('/admin/alertas-clientes/lidos', { method: 'POST' })
      .then(() => {
        const atual = parseInt((badge && badge.textContent) || '0', 10) || 0;
        mostrarBadge(soClientes ? 0 : Math.max(0, atual - marcados));
        setTimeout(() => lista.querySelectorAll('.nao-lida').forEach((el) => el.classList.remove('nao-lida')), 2500);
      })
      .catch(() => { naoLidos = marcados; });
  });
})();
