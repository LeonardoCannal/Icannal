// Admin → Usuários: desativar/reativar conta e transferir o painel médico.
(function () {
  const aviso = (msg) => (window.mostrarAvisoNotif ? window.mostrarAvisoNotif(msg) : alert(msg));

  async function postar(url, corpo) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo || {}) });
    try { return await r.json(); } catch (_) { return { ok: false, mensagem: 'Resposta inesperada do servidor.' }; }
  }

  // ---------- Desativar / reativar ----------
  document.querySelectorAll('.btn-desativar-usuario').forEach((botao) => {
    botao.addEventListener('click', async () => {
      const nome = botao.dataset.nome;
      const desativar = botao.dataset.desativado !== '1';
      const pergunta = desativar
        ? `Desativar a conta de ${nome}?\n\nA pessoa não consegue mais entrar no i.cannal (e sai na hora, se estiver usando). Nada é apagado: painel, visitas e histórico ficam guardados, e dá para reativar depois.`
        : `Reativar a conta de ${nome}? A pessoa volta a conseguir entrar.`;
      if (!confirm(pergunta)) return;
      botao.disabled = true;
      const r = await postar(`/admin/usuarios/${botao.dataset.id}/desativar`, { desativar });
      if (r.ok) location.reload();
      else { botao.disabled = false; aviso(r.mensagem || 'Não foi possível mudar a conta.'); }
    });
  });

  // ---------- Transferir painel ----------
  const dialogo = document.getElementById('tr-dialogo');
  if (!dialogo) return;
  const destino = document.getElementById('tr-destino');
  const previa = document.getElementById('tr-previa');
  const confirmar = document.getElementById('tr-confirmar');
  const desativar = document.getElementById('tr-desativar');
  let origem = null, pedido = 0;

  function fechar() { dialogo.close(); }
  document.getElementById('tr-fechar').addEventListener('click', fechar);
  document.getElementById('tr-cancelar').addEventListener('click', fechar);
  dialogo.addEventListener('click', (e) => { if (e.target === dialogo) fechar(); });

  document.querySelectorAll('.btn-transferir-painel').forEach((botao) => {
    botao.addEventListener('click', () => {
      origem = { id: botao.dataset.id, nome: botao.dataset.nome };
      document.getElementById('tr-origem').textContent = origem.nome;
      document.getElementById('tr-origem2').textContent = origem.nome;
      Array.from(destino.options).forEach((o) => { o.hidden = o.value === origem.id; o.disabled = o.value === origem.id; });
      destino.value = '';
      previa.textContent = '';
      confirmar.disabled = true;
      // admin/própria conta não pode ser desativada por aqui
      const podeDesativar = !!document.querySelector(`.btn-desativar-usuario[data-id="${origem.id}"][data-desativado="0"]`);
      desativar.checked = podeDesativar;
      desativar.disabled = !podeDesativar;
      desativar.closest('label').hidden = !podeDesativar;
      dialogo.showModal();
      destino.focus();
    });
  });

  destino.addEventListener('change', async () => {
    confirmar.disabled = true;
    if (!destino.value) { previa.textContent = ''; return; }
    const meu = ++pedido;
    previa.textContent = 'Conferindo os painéis…';
    const r = await fetch(`/admin/usuarios/${origem.id}/transferir/previa?destino=${destino.value}`).then((x) => x.json()).catch(() => ({ ok: false }));
    if (meu !== pedido) return;
    if (!r.ok) { previa.textContent = r.mensagem || 'Não foi possível conferir.'; return; }
    const nomeDestino = destino.options[destino.selectedIndex].text;
    let t = `${r.total} médico(s) vão para ${nomeDestino}`;
    if (r.repetidos) t += `; ${r.repetidos} deles ele já tem e serão juntados`;
    if (r.clientes) t += `; ${r.clientes} cliente(s)`;
    if (r.agendamentos) t += `; ${r.agendamentos} visita(s) agendada(s) vão junto`;
    previa.textContent = t + '.';
    confirmar.disabled = r.total === 0;
  });

  document.getElementById('tr-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!destino.value) return;
    confirmar.disabled = true;
    confirmar.textContent = 'Transferindo…';
    const r = await postar(`/admin/usuarios/${origem.id}/transferir`, { destino: destino.value, desativar: desativar.checked && !desativar.disabled });
    confirmar.textContent = 'Transferir';
    if (!r.ok) { confirmar.disabled = false; aviso(r.mensagem || 'Não foi possível transferir.'); return; }
    fechar();
    aviso(`Painel transferido: ${r.movidos} médico(s) passados, ${r.juntados} juntado(s)` +
          (r.agendas_canceladas ? `, ${r.agendas_canceladas} agendamento(s) cancelado(s) por horário repetido` : '') +
          (r.desativado ? '. Conta desativada.' : '.'));
    setTimeout(() => location.reload(), 1800);
  });
})();
