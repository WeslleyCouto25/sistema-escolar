(() => {
  "use strict";
  const phone = String(window.SIGEU_WHATSAPP || '').replace(/\D/g,'');
  const backdrop = document.querySelector('#budget-backdrop');
  const modal = backdrop?.querySelector('.budget-modal');
  const menu = document.querySelector('#main-navigation');
  const navButton = document.querySelector('#open-menu');
  const budgetKind = document.querySelector('#budget-kind');
  let previousFocus = null;
  const openWA = (message) => {
    if (!/^55\d{10,11}$/.test(phone)) { alert('Contato WhatsApp indisponível. Verifique a configuração do atendimento.'); return; }
    // Dispara evento genérico para integração opcional com Google Tag Manager.
    window.dataLayer = window.dataLayer || [];
    window.dataLayer.push({event:'sigeu_whatsapp_click', page_type:'sigeu_solucoes_digitais', lead_type:'orcamento'});
    window.open('https://wa.me/' + phone + '?text=' + encodeURIComponent(message), '_blank', 'noopener,noreferrer');
  };
  const showModal = (kind='') => {
    if (!backdrop) return;
    previousFocus = document.activeElement;
    if (kind && budgetKind) {
      const match = [...budgetKind.options].find(opt => opt.textContent.toLowerCase().includes(kind.toLowerCase()) || opt.value.toLowerCase().includes(kind.toLowerCase()));
      if (match) budgetKind.value = match.value;
    }
    backdrop.hidden = false;
    document.body.classList.add('hide-scroll');
    modal?.focus();
  };
  const closeModal = () => {
    if (!backdrop) return;
    backdrop.hidden = true;
    document.body.classList.remove('hide-scroll');
    previousFocus?.focus?.();
  };
  document.querySelectorAll('[data-open-budget]').forEach(el=>el.addEventListener('click',()=>showModal(el.dataset.kind||'')));
  document.querySelectorAll('[data-close-budget]').forEach(el=>el.addEventListener('click',closeModal));
  backdrop?.addEventListener('click',ev => {if(ev.target===backdrop) closeModal();});
  document.addEventListener('keydown', ev => {
    if (ev.key==='Escape' && backdrop && !backdrop.hidden) closeModal();
    if (ev.key==='Tab' && backdrop && !backdrop.hidden && modal) {
      const focusables=[...modal.querySelectorAll('button,input,select,textarea,a[href]')].filter(x=>!x.disabled && x.offsetParent!==null);
      if(!focusables.length) {ev.preventDefault(); return;}
      const first=focusables[0], last=focusables[focusables.length-1];
      if(ev.shiftKey && document.activeElement===first){ev.preventDefault();last.focus();}
      else if(!ev.shiftKey && document.activeElement===last){ev.preventDefault();first.focus();}
    }
  });
  navButton?.addEventListener('click', () => {const open=menu?.classList.toggle('is-open'); navButton.setAttribute('aria-expanded',String(!!open));});
  menu?.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>{menu.classList.remove('is-open');navButton?.setAttribute('aria-expanded','false');}));
  document.querySelector('#budget-form')?.addEventListener('submit',ev=>{ev.preventDefault();const data=new FormData(ev.currentTarget);openWA(`Olá! Vim da página SIGEU Soluções Digitais. Quero orçamento para: ${data.get('kind')}. Detalhes: ${data.get('notes')||'Gostaria de conversar sobre possibilidades de baixo custo.'}. Tenho interesse em cartão até 12x, Pix com desconto ou boleto em 2x, conforme disponibilidade.`);});
  document.querySelector('#lead-form')?.addEventListener('submit',ev=>{ev.preventDefault();const d=new FormData(ev.currentTarget);openWA(`Olá! Vim do Google/site SIGEU. Solicito orçamento. Serviço: ${d.get('kind')}. Meu negócio: ${d.get('business')||'a informar'}. Faixa de orçamento: ${d.get('budget')}. Necessidade: ${d.get('request')||'a explicar'}. Quero saber sobre cartão até 12x, Pix com desconto ou boleto em 2x.`);});
  const input = document.querySelector('#busca-profissao');
  const items=[...document.querySelectorAll('#lista-profissoes .niche-link')];
  input?.addEventListener('input',()=>{const q=input.value.trim().toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[\u0300-\u036f]/g,'');let visible=0;items.forEach(el=>{const s=el.textContent.toLocaleLowerCase('pt-BR').normalize('NFD').replace(/[\u0300-\u036f]/g,'');el.hidden=!s.includes(q);if(!el.hidden) visible++;});const output=document.querySelector('#quantidade-resultados');if(output)output.textContent=`${visible} especialidade${visible===1?'':'s'} encontrada${visible===1?'':'s'}`;});
})();
