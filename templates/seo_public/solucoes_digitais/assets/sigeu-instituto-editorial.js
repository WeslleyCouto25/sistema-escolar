
(()=>{"use strict";const $=(id)=>document.getElementById(id),form=document.querySelector("[data-simulator]");
const fmt=(n,d=0)=>new Intl.NumberFormat("pt-BR",{minimumFractionDigits:d,maximumFractionDigits:d}).format(n);
const calc=()=>{if(!form)return;const type=form.dataset.simulator,inputs=Object.fromEntries([...form.querySelectorAll("input[data-k]")].map(e=>[e.dataset.k,Number(e.value)]));let a=NaN,s="",d=0;const v=inputs;
switch(type){case"market":a=v.publico*v.taxa/100*v.ticket;s="Receita mensal potencial no cenário informado";break;
case"nps":{const t=v.prom+v.neut+v.det;a=t?100*(v.prom-v.det)/t:NaN;d=1;s="Escala NPS de −100 a +100; base: "+fmt(t)+" avaliações";break}
case"psych":a=v.itens*v.correlacao/(1+(v.itens-1)*v.correlacao);d=3;s="Alfa padronizado estimado pela correlação média interitens";break;
case"collect":a=Math.ceil(1.96*1.96*.25*v.deff/Math.pow(v.erro/100,2));s="Convites estimados: "+(v.resposta>0?fmt(Math.ceil(a/(v.resposta/100))):"—");break;
case"opinion":{let n0=1.96*1.96*.25/Math.pow(v.erro/100,2);a=Math.ceil(n0/(1+(n0-1)/v.populacao));s="Amostra aleatória simples hipotética, p=50%, confiança nominal 95%";break}
case"viability":{a=-v.investimento;for(let t=1;t<=v.anos;t++)a+=v.fluxo/Math.pow(1+v.desconto/100,t);s="VPL nominal de fluxos anuais constantes";break}
case"org":a=(v.nota-1)/4*100;s="Índice ilustrativo de 0 a 100, sem referência normativa";break;
case"stats":a=v.dp? v.diferenca/v.dp : NaN;d=2;s="Tamanho de efeito padronizado (d), sem teste de hipótese";break;
case"intelligence":a=v.a+v.b+v.c+v.d;s="CR4: participação conjunta das quatro maiores empresas";break;
}
const out=$("sim-output"),sub=$("sim-secondary"),isMoney=["market","viability"].includes(type),isPct=["org","intelligence"].includes(type);
let valid=Number.isFinite(a)&&[...form.querySelectorAll("input[data-k]")].every(e=>Number.isFinite(Number(e.value))&&Number(e.value)>=Number(e.min||-Infinity)&&Number(e.value)<=Number(e.max||Infinity));
if(type==="intelligence"&&a>100)valid=false;
if(type==="nps"&&v.prom+v.neut+v.det===0)valid=false;
if(type==="psych"&&(v.itens<2||v.correlacao<0||v.correlacao>1))valid=false;
if(valid){out.textContent=(isMoney?"R$ ":"")+fmt(a,d)+(isPct?"%":"");sub.textContent=s}else{out.textContent="Revise os dados";sub.textContent="Preencha parâmetros coerentes para atualizar o resultado."}
};
if(form){form.addEventListener("input",calc);calc();}
const progress=$("progress");if(progress){let scheduled=false;const update=()=>{const max=document.documentElement.scrollHeight-innerHeight;progress.style.width=(max>0?Math.max(0,scrollY/max*100):0)+"%";scheduled=false};addEventListener("scroll",()=>{if(!scheduled){scheduled=true;requestAnimationFrame(update)}},{passive:true});update()}
const wa=document.querySelectorAll("[data-wa]");for(const a of wa){const num=(a.dataset.phone||"").replace(/\D/g,"");if(!/^\d{12,15}$/.test(num)){a.removeAttribute("href");a.setAttribute("aria-disabled","true");a.title="Atendimento temporariamente indisponível: confirme o número de contato";continue}const text="Olá, Instituto SIGEU. Gostaria de uma proposta para "+(a.dataset.service||"serviços de pesquisa")+". Meu projeto é: ";a.href="https://wa.me/"+num+"?text="+encodeURIComponent(text);a.target="_blank";a.rel="noopener noreferrer"}
})();
