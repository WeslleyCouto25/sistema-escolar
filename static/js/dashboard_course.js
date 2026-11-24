/* Navegação interna: nunca substitui o cabeçalho, sidebar ou rodapé. */
(() => {
  'use strict';
  const panel = document.getElementById('sigeu-course-panel');
  if (!panel || !document.body.classList.contains('dashboard-page')) return;
  const main = panel.parentElement;
  let pending = null;
  const sections = () => [...main.children].filter(el => el.classList.contains('content-section'));
  function close() {
    if (pending) pending.abort();
    pending = null;
    if (window.SigeuEditorial) window.SigeuEditorial.unmount();
    panel.querySelectorAll('video,audio').forEach(media => media.pause());
    panel.replaceChildren();
    panel.hidden = true;
    main.removeAttribute('aria-busy');
  }
  function restoreList() {
    if (typeof window.showSection === 'function') window.showSection('disciplinas');
    else close();
  }
  function showState(message, error = false) {
    const box = document.createElement('div');
    box.className = 'sigeu-course-message';
    box.setAttribute('role', error ? 'alert' : 'status');
    const text = document.createElement('p');
    text.textContent = message;
    const back = document.createElement('button');
    back.type = 'button';
    back.className = 'sigeu-course-back';
    back.textContent = 'Voltar às minhas disciplinas';
    back.onclick = restoreList;
    box.append(text,back);
    panel.replaceChildren(box);
  }
  function activateCourse(root) {
    const headers = [...root.querySelectorAll('[data-course-chapter]')];
    function setChapter(number, toggle = false) {
      const target = headers.find(h => h.dataset.courseChapter === String(number));
      if (!target || target.getAttribute('aria-disabled') === 'true') return false;
      const shouldOpen = !toggle || target.getAttribute('aria-expanded') !== 'true';
      headers.forEach(header => {
        const card = header.closest('.chapter-card');
        const open = shouldOpen && header === target;
        header.setAttribute('aria-expanded',String(open));
        card.querySelector('.chapter-content').classList.toggle('show',open);
        card.querySelector('.chapter-toggle').classList.toggle('rotated',open);
        if (!open) card.querySelectorAll('video,audio').forEach(m => m.pause());
      });
      root.querySelectorAll('.progress-step').forEach(step => {
        step.classList.toggle('current', shouldOpen && step.id === 'step-' + number);
      });
      return shouldOpen;
    }
    headers.forEach(header => {
      header.addEventListener('click', () => setChapter(header.dataset.courseChapter,true));
      header.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); setChapter(header.dataset.courseChapter,true);
        }
      });
    });
    root.querySelectorAll('.progress-step').forEach(step => {
      const n = step.id.replace('step-','');
      const header = headers.find(h => h.dataset.courseChapter === n);
      if (!header || header.getAttribute('aria-disabled') === 'true') return;
      step.tabIndex=0;step.setAttribute('role','button');step.setAttribute('aria-label','Abrir capítulo '+n);
      const activate = () => {
        setChapter(n);
        header.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
      };
      step.addEventListener('click',activate);
      step.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();activate();}});
    });
    const preferred = headers.find(h => h.dataset.courseChapter === root.dataset.current && h.getAttribute('aria-disabled') !== 'true') ||
                      headers.find(h => h.getAttribute('aria-disabled') !== 'true');
    if (preferred) setChapter(preferred.dataset.courseChapter);
    root.addEventListener('click',event=>{
      const back=event.target.closest('[data-course-back]');
      if(back){event.preventDefault();restoreList();}
    });
    if(window.SigeuEditorial)window.SigeuEditorial.mount(root);
    root.querySelector('h1')?.focus({preventScroll:true});
  }
  async function open(href) {
    const url = new URL(href,location.origin);
    if(url.origin!==location.origin || !/^\/disciplina\/\d+\/?$/.test(url.pathname)) return;
    close();
    const controller = new AbortController();
    pending = controller;
    sections().forEach(section => section.classList.remove('active'));
    document.querySelectorAll('.section-menu-item').forEach(item => item.classList.toggle('active',item.dataset.section==='disciplinas'));
    try { localStorage.setItem('activeSection','disciplinas'); } catch(_) {}
    panel.hidden=false;
    main.setAttribute('aria-busy','true');
    showState('Carregando o conteúdo da disciplina…');
    url.searchParams.set('embedded','1');
    try {
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Accept':'text/html','X-Requested-With':'XMLHttpRequest'}});
      if (pending!==controller) return;
      if(response.redirected) throw new Error('Sua sessão pode ter expirado. Atualize o Dashboard e entre novamente, se necessário.');
      if(!response.ok || response.headers.get('X-SIGEU-Course-Fragment')!=='1') throw new Error('Não foi possível abrir a disciplina. Confira sua matrícula ou tente novamente.');
      const doc=new DOMParser().parseFromString(await response.text(),'text/html');
      if(pending!==controller)return;
      const root=doc.querySelector('[data-sigeu-course]');
      if(!root || root.dataset.courseId!==url.pathname.split('/')[2])throw new Error('Conteúdo inválido. Nenhuma disciplina foi aberta.');
      // Fragmento controlado pelo servidor. Não executa scripts trazidos por HTML.
      root.querySelectorAll('script').forEach(script=>script.remove());
      panel.replaceChildren(document.importNode(root,true));
      activateCourse(panel.firstElementChild);
      // Mantém o endereço /dashboard; somente o painel central muda.
      panel.scrollIntoView({behavior:'auto',block:'start'});
    } catch(error) {
      if(error.name==='AbortError'||pending!==controller)return;
      showState(error.message || 'Não foi possível carregar o conteúdo.',true);
    } finally {
      if(pending===controller){pending=null;main.removeAttribute('aria-busy');}
    }
  }
  window.SigeuDashboardCourse={open,close};
  document.addEventListener('click',event=>{
    if(panel.hidden)return;
    const link=event.target.closest('a[href]');
    if(!link || link.target==='_blank'||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
    const url=new URL(link.href,location.origin);
    if(url.origin!==location.origin || url.pathname!=='/dashboard')return;
    const section=url.searchParams.get('section')||'dashboard';
    if(!document.getElementById(section+'-section'))return;
    event.preventDefault();event.stopPropagation();window.showSection(section);
  });
})();
