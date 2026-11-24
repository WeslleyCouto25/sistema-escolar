/* Somente o componente editorial da disciplina; não interfere na navegação do portal. */
(() => {
  'use strict';
  const articles = [...document.querySelectorAll('.sigeu-editorial')];
  if (!articles.length) return;
  const states = new Map();
  const fmt = s => String(Math.floor(s / 60)).padStart(2,'0') + ':' + String(s % 60).padStart(2,'0');
  const feedback = (block, message) => {
    const el = block.querySelector('.se-feedback');
    if (el) el.textContent = message;
  };
  const showError = (article, message) => {
    const bar = article.querySelector('.se-studybar small');
    if (bar) bar.textContent = message;
  };
  function renderProgress(article) {
    const state = states.get(article);
    const required = [...article.querySelectorAll('.se-block[data-required="1"]')];
    const done = required.filter(block => state.done.has(block.dataset.resource)).length;
    article.querySelector('.se-completed-count').textContent = done;
    article.querySelector('.se-time').textContent = fmt(state.seconds);
    const percent = required.length ? Math.round(100 * done / required.length) : 0;
    const meter = article.querySelector('.se-meter');
    meter.setAttribute('aria-valuenow', String(percent));
    meter.firstElementChild.style.width = percent + '%';
    required.forEach(block => {
      const complete = state.done.has(block.dataset.resource);
      const label = block.querySelector('.se-state');
      label.textContent = complete ? '✓ Concluído' : 'Obrigatório';
      label.classList.toggle('se-done', complete);
      const indexLabel = article.querySelector('[data-index-status="' + block.dataset.resource + '"]');
      if (indexLabel) { indexLabel.textContent = complete ? '✓' : ''; indexLabel.classList.toggle('se-done',complete); }
      const button = block.querySelector('.se-mark');
      if (button) {
        button.disabled = complete;
        button.classList.toggle('se-done',complete);
        button.textContent = complete ? '✓ Leitura concluída' : 'Concluir leitura ✓';
      }
      if (complete && block.dataset.kind === 'video') {
        block.querySelector('.se-video-status').textContent = '✓ Videoaula principal concluída.';
      }
    });
    const proof = article.querySelector('.se-proof-link');
    if (proof) {
      const ready = done === required.length && required.length > 0;
      proof.hidden = !ready;
      proof.setAttribute('aria-disabled',String(!ready));
      article.querySelector('.se-proof-status').textContent = ready ? '✓ Recursos concluídos. A prova está liberada.' : 'Conclua os recursos obrigatórios para liberar a prova.';
    }
  }
  function requestEvent(article, payload) {
    const state = states.get(article);
    const job = async () => {
      const response = await fetch(article.dataset.endpoint, {
        method:'POST', credentials:'same-origin', cache:'no-store',
        headers:{'Content-Type':'application/json','X-Editorial-Token':article.dataset.token,'Accept':'application/json'},
        body:JSON.stringify(payload)
      });
      if (!response.ok) throw new Error('Não foi possível registrar o progresso. Atualize a página ou entre em contato com o suporte.');
      const data = await response.json();
      state.done = new Set(data.recursos);
      state.seconds = Math.min(600, Number(data.segundos) || 0);
      renderProgress(article);
      return data;
    };
    const next = state.queue.then(job);
    state.queue = next.catch(() => {});
    return next.catch(error => { showError(article,error.message); throw error; });
  }
  async function complete(article, block, fraction) {
    const state = states.get(article), id = block.dataset.resource;
    if (state.done.has(id) || state.pending.has(id)) return;
    state.pending.add(id);
    try { await requestEvent(article,{event:'complete',resource:id,fraction}); }
    catch (_) { feedback(block,'Não foi possível salvar. Tente novamente.'); }
    finally { state.pending.delete(id); }
  }
  function bindQuestions(article, block) {
    const questions = [...block.querySelectorAll('.se-question')];
    if (!questions.length) return;
    questions.forEach(question => {
      question.querySelectorAll('[data-choice]').forEach(button => {
        button.addEventListener('click', () => {
          if (question.dataset.solved === '1') return;
          const correct = button.dataset.choice === question.dataset.answer;
          button.classList.toggle('is-correct',correct);
          button.classList.toggle('is-wrong',!correct);
          const result = question.querySelector('.se-feedback');
          result.textContent = correct ? '✓ ' + result.dataset.explanation : 'Não é essa a alternativa. Releia e tente novamente.';
          if (correct) {
            question.dataset.solved = '1';
            question.querySelectorAll('button').forEach(b => b.disabled = true);
            if (questions.every(q => q.dataset.solved === '1')) {
              complete(article,block);
            }
          }
        });
      });
    });
  }
  function bindGraph(article,block) {
    const graph = block.querySelector('[data-graph]');
    if (!graph) return;
    const left = [...graph.querySelectorAll('[data-left]')], right = [...graph.querySelectorAll('[data-right]')];
    const svg = graph.querySelector('svg'), pairs = new Set();
    let selected = null;
    function draw() {
      svg.replaceChildren();
      const rect = graph.getBoundingClientRect();
      if (!rect.width) return;
      pairs.forEach(id => {
        const a = left.find(x => x.dataset.left === id).getBoundingClientRect();
        const b = right.find(x => x.dataset.right === id).getBoundingClientRect();
        const line = document.createElementNS('http://www.w3.org/2000/svg','line');
        line.setAttribute('x1',a.right-rect.left);line.setAttribute('y1',a.top+a.height/2-rect.top);
        line.setAttribute('x2',b.left-rect.left);line.setAttribute('y2',b.top+b.height/2-rect.top);
        line.setAttribute('stroke','#2d6a62');line.setAttribute('stroke-width','2.5');
        svg.appendChild(line);
      });
    }
    left.forEach(button => button.addEventListener('click', () => {
      if (pairs.has(button.dataset.left)) return;
      left.forEach(b=>b.classList.remove('is-selected'));
      selected=button;button.classList.add('is-selected');
      feedback(block,'Agora selecione a definição correspondente.');
    }));
    right.forEach(button=>button.addEventListener('click',()=>{
      if (!selected || pairs.has(button.dataset.right)) return;
      if (selected.dataset.left !== button.dataset.right) { feedback(block,'Associação incorreta. Tente outra definição.'); return; }
      pairs.add(button.dataset.right);
      selected.classList.remove('is-selected');selected.classList.add('is-correct');
      button.classList.add('is-correct');selected.disabled=true;button.disabled=true;selected=null;
      draw();feedback(block,'✓ ' + pairs.size + ' de ' + left.length + ' associações corretas.');
      if (pairs.size === left.length) complete(article,block);
    }));
    if ('ResizeObserver' in window) new ResizeObserver(draw).observe(graph);
    window.addEventListener('resize',draw,{passive:true});
  }
  function bindDeck(block) {
    const deck=block.querySelector('[data-deck]');
    if (!deck) return;
    const frames=[...deck.querySelectorAll('.se-frame')], play=deck.querySelector('.se-play');
    let index=0,timer=null;
    const show=i=>{index=(i+frames.length)%frames.length;frames.forEach((f,n)=>f.hidden=n!==index);deck.querySelector('.se-deck-count').textContent=(index+1)+' / '+frames.length;};
    const stop=()=>{if(timer){clearInterval(timer);timer=null;}play.textContent='Reproduzir';};
    deck.querySelector('.se-prev').onclick=()=>{stop();show(index-1);};
    deck.querySelector('.se-next').onclick=()=>{stop();show(index+1);};
    play.onclick=()=>{if(timer){stop();return;}play.textContent='Pausar';timer=setInterval(()=>{if(!deck.getClientRects().length || document.hidden){stop();return;}if(index===frames.length-1){stop();return;}show(index+1);},6500);};
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  }
  function bindLab(block) {
    const inputs=[...block.querySelectorAll('.se-lab-input')];
    if(!inputs.length)return;
    const update=()=>{
      const values=inputs.map(i=>Number(i.value));
      inputs.forEach((input,i)=>input.parentElement.querySelector('output').value=values[i]);
      block.querySelectorAll('.se-bars>div').forEach((bar,i)=>{bar.querySelector('span').style.height=values[i]*10+'%';bar.querySelector('small').textContent=values[i];});
      block.querySelector('.se-mean').textContent=(values.reduce((a,b)=>a+b,0)/values.length).toLocaleString('pt-BR',{maximumFractionDigits:2});
      block.querySelector('.se-range').textContent=Math.max(...values)-Math.min(...values);
    };
    inputs.forEach(i=>i.addEventListener('input',update));update();
  }
  function bindSelects(article,block) {
    const check=block.querySelector('.se-check-selects');
    if(!check)return;
    check.onclick=()=>{
      const inputs=[...block.querySelectorAll('.se-graded-select')];
      let correct=0;
      inputs.forEach(input=>{const ok=input.value===input.dataset.answer;input.classList.toggle('is-correct',ok);input.classList.toggle('is-wrong',!ok);correct+=Number(ok);});
      feedback(block,correct+' de '+inputs.length+' corretas.'+(correct===inputs.length?' ✓ Atividade concluída.':' Revise as associações.'));
      if(correct===inputs.length)complete(article,block);
    };
  }
  function bindOrder(article,block) {
    const list=block.querySelector('.se-order-list');
    if(!list)return;
    list.addEventListener('click',event=>{
      const button=event.target.closest('[data-move]');if(!button)return;
      const item=button.closest('li');
      if(button.dataset.move==='-1' && item.previousElementSibling)list.insertBefore(item,item.previousElementSibling);
      if(button.dataset.move==='1' && item.nextElementSibling)list.insertBefore(item.nextElementSibling,item);
    });
    block.querySelector('.se-check-order').onclick=()=>{
      const ok=[...list.children].every((item,i)=>Number(item.dataset.position)===i);
      feedback(block,ok?'✓ Sequência correta.':'Ainda há etapas fora de ordem. Revise a relação entre problema, ação e avaliação.');
      if(ok)complete(article,block);
    };
  }
  function bindAudio(block){
    const audio=block.querySelector('.se-audio');
    if(!audio)return;
    const status=audio.querySelector('.se-audio-status'), play=audio.querySelector('.se-speak'), stop=audio.querySelector('.se-stop-speech');
    if(!('speechSynthesis' in window)){play.disabled=true;stop.disabled=true;status.textContent='Síntese indisponível neste navegador. Utilize a transcrição.';return;}
    play.onclick=()=>{speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(audio.dataset.text);utterance.lang='pt-BR';utterance.rate=.9;utterance.onstart=()=>status.textContent='Reproduzindo síntese.';utterance.onend=()=>status.textContent='Síntese concluída.';utterance.onerror=()=>status.textContent='Não foi possível reproduzir. Utilize a transcrição.';speechSynthesis.speak(utterance);};
    stop.onclick=()=>{speechSynthesis.cancel();status.textContent='Reprodução interrompida.';};
  }
  function bindVideo(article,block){
    const video=block.querySelector('[data-required-video]');
    if(!video)return;
    const status=block.querySelector('.se-video-status');
    const update=()=>{
      if(!Number.isFinite(video.duration)||video.duration<=0)return;
      const state=states.get(article);
      if(state.done.has(block.dataset.resource))return;
      let watched=0;
      for(let i=0;i<video.played.length;i++) watched+=video.played.end(i)-video.played.start(i);
      const fraction=Math.min(1,watched/video.duration);
      status.textContent='Assistido nesta sessão: '+Math.floor(fraction*100)+'% (mínimo de 90%).';
      if(fraction>=.9)complete(article,block,fraction);
    };
    video.addEventListener('timeupdate',update);video.addEventListener('ended',update);
    video.addEventListener('error',()=>{status.textContent='Não foi possível carregar a videoaula. Tente recarregar a página ou acione o suporte.';});
    video.addEventListener('play',()=>{lastInteraction=Date.now();});
  }
  let lastInteraction=Date.now(), activeArticle=null;
  ['pointerdown','keydown','scroll','touchstart'].forEach(name=>document.addEventListener(name,()=>{lastInteraction=Date.now();},{passive:true}));
  articles.forEach(article=>{
    let done=[];try{done=JSON.parse(article.dataset.done||'[]');}catch(_){}
    states.set(article,{done:new Set(done),seconds:Number(article.dataset.seconds)||0,pending:new Set(),queue:Promise.resolve()});
    article.querySelectorAll('.se-block').forEach(block=>{
      const mark=block.querySelector('.se-mark');if(mark)mark.onclick=()=>complete(article,block);
      bindQuestions(article,block);bindGraph(article,block);bindDeck(block);bindLab(block);
      bindSelects(article,block);bindOrder(article,block);bindAudio(block);bindVideo(article,block);
    });
    renderProgress(article);
  });
  function currentArticle(){
    if(document.hidden)return null;
    const visible=articles.filter(a=>{const r=a.getBoundingClientRect();return a.getClientRects().length && r.bottom>100 && r.top<window.innerHeight-40;});
    if(!visible.length)return null;
    const middle=window.innerHeight*.45;
    const article=visible.find(a=>{const r=a.getBoundingClientRect();return r.top<=middle&&r.bottom>=middle;})||visible[0];
    const playing=[...article.querySelectorAll('video')].some(v=>!v.paused&&!v.ended);
    if(!playing && Date.now()-lastInteraction>90000)return null;
    return article;
  }
  function tick(){
    const article=currentArticle();
    if(!article){activeArticle=null;return;}
    if(states.get(article).seconds>=600){activeArticle=null;return;}
    const event=activeArticle===article?'heartbeat':'start';
    activeArticle=article;
    requestEvent(article,{event}).catch(()=>{activeArticle=null;});
  }
  document.addEventListener('visibilitychange',()=>{activeArticle=null;if(!document.hidden)tick();});
  window.addEventListener('pagehide',()=>{activeArticle=null;if('speechSynthesis' in window)speechSynthesis.cancel();});
  setInterval(tick,15000);tick();
  if('IntersectionObserver' in window && !matchMedia('(prefers-reduced-motion: reduce)').matches){
    const observer=new IntersectionObserver(entries=>entries.forEach(entry=>entry.target.classList.toggle('is-visible',entry.isIntersecting)),{threshold:0.01});
    articles.forEach(a=>a.querySelectorAll('.se-block').forEach(block=>{block.classList.add('se-reveal');observer.observe(block);}));
  }
})();