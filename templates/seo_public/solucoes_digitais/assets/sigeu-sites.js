// SIGEU: interação progressiva; links funcionam mesmo sem JS.
(function(){'use strict';
  const btn=document.getElementById('menu-toggle');
  const menu=document.getElementById('navlinks');
  if(btn&&menu){btn.addEventListener('click',()=>{let open=!menu.classList.contains('open');menu.classList.toggle('open',open);btn.setAttribute('aria-expanded',String(open));});menu.addEventListener('click',e=>{if(e.target.closest('a')){menu.classList.remove('open');btn.setAttribute('aria-expanded','false')}})}
  const form=document.querySelector('[data-orcamento]');
  if(form){form.addEventListener('submit',function(e){e.preventDefault();let n=window.SIGEU_WHATSAPP||'',servico=form.querySelector('[name="servico"]').value,detalhes=form.querySelector('[name="detalhes"]').value.trim(),cidade=form.querySelector('[name="cidade"]').value.trim();let mensagem='Olá! Vim da página '+document.title+'. Gostaria de um orçamento de '+servico+'.'+(cidade?' Cidade/UF: '+cidade+'.':'')+(detalhes?' Minha necessidade: '+detalhes+'.':'')+' Quero saber o escopo e as opções de pagamento (cartão até 12x, Pix com desconto ou boleto em 2x).';if(!/^55\d{10,11}$/.test(n)){alert('Número de WhatsApp comercial não configurado.');return}window.open('https://wa.me/'+n+'?text='+encodeURIComponent(mensagem),'_blank','noopener,noreferrer')})}
})();