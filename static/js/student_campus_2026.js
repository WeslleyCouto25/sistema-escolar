/* SIGEU Campus: modal de validação, navegação acessível e sem bibliotecas extras. */
(() => {
  "use strict";
  const body = document.body;
  if (!body || !body.classList.contains("student-page")) return;

  const navToggle = document.querySelector(".sigeu-nav-toggle");
  const compactKey = "sigeu-2026-sidebar-compact";
  try {
    if (window.localStorage.getItem(compactKey) === "1") {
      body.classList.add("sigeu-sidebar-compact");
    }
  } catch (_) {}
  if (navToggle) {
    navToggle.addEventListener("click", () => {
      body.classList.toggle("sigeu-sidebar-compact");
      navToggle.setAttribute("aria-expanded", String(!body.classList.contains("sigeu-sidebar-compact")));
      try { window.localStorage.setItem(compactKey, body.classList.contains("sigeu-sidebar-compact") ? "1" : "0"); }
      catch (_) {}
    });
  }

  if (!body.classList.contains("dashboard-page")) return;
  let overlay = null;
  let previousFocus = null;
  let currentAbort = null;

  function closeModal() {
    if (currentAbort) currentAbort.abort();
    currentAbort = null;
    if (overlay) overlay.remove();
    overlay = null;
    if (previousFocus && previousFocus.isConnected) previousFocus.focus();
  }

  function makeModal() {
    previousFocus = document.activeElement;
    overlay = document.createElement("div");
    overlay.className = "sigeu-access-overlay";
    overlay.innerHTML = [
      '<section class="sigeu-access-card" role="dialog" aria-modal="true" aria-labelledby="sigeu-access-title" aria-describedby="sigeu-access-description">',
      ' <div class="sigeu-access-symbol" aria-hidden="true"><i class="fas fa-user-shield"></i></div>',
      ' <h2 id="sigeu-access-title">Validando seu acesso</h2>',
      ' <p id="sigeu-access-description" aria-live="polite">Consultando a situação da matrícula e da documentação...</p>',
      ' <div class="sigeu-access-step"><i class="fas fa-credit-card" aria-hidden="true"></i><div>Confirmação do pagamento e registro administrativo.</div></div>',
      ' <div class="sigeu-access-step"><i class="fas fa-file-circle-check" aria-hidden="true"></i><div>Prazo de até 24 horas para validação interna da documentação.</div></div>',
      ' <div class="sigeu-access-step"><i class="fas fa-book-open" aria-hidden="true"></i><div>Capítulos disponibilizados segundo o calendário acadêmico da matrícula.</div></div>',
      ' <div class="sigeu-access-actions"><button type="button" class="sigeu-access-close">Entendi</button><button type="button" class="sigeu-access-proceed main" hidden>Continuar para a disciplina</button></div>',
      '</section>'
    ].join("");
    document.body.appendChild(overlay);
    overlay.querySelector(".sigeu-access-close").addEventListener("click", closeModal);
    overlay.addEventListener("click", event => { if (event.target === overlay) closeModal(); });
    overlay.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); closeModal(); }
      if (event.key === "Tab") {
        const focusable = Array.from(overlay.querySelectorAll("button:not([hidden])"));
        if (focusable.length) {
          const first = focusable[0], last = focusable[focusable.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault(); last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault(); first.focus();
          }
        }
      }
    });
    overlay.querySelector(".sigeu-access-close").focus();
    return overlay;
  }

  async function showCourseAccess(href, courseId) {
    closeModal();
    const box = makeModal();
    const title = box.querySelector("#sigeu-access-title");
    const message = box.querySelector("#sigeu-access-description");
    const proceed = box.querySelector(".sigeu-access-proceed");
    const controller = new AbortController();
    currentAbort = controller;
    try {
      const response = await fetch("/verificar-acesso/" + encodeURIComponent(courseId), {
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
        signal: controller.signal
      });
      if (!response.ok) throw new Error("A verificação não pôde ser concluída.");
      const data = await response.json();
      if (!overlay || overlay !== box) return;
      title.textContent = data.titulo || "Validação de documentação";
      const standardNotice = "A disciplina é liberada 24 horas após a confirmação do pagamento, prazo destinado à validação interna da documentação.";
      message.textContent = data.acesso_permitido
        ? ("Validação de acesso concluída. " + (data.mensagem || "") + " " + standardNotice)
        : (data.mensagem || standardNotice);
      if (data.acesso_permitido) {
        proceed.hidden = false;
        proceed.addEventListener("click", () => {
          const destination = href;
          closeModal();
          window.location.assign(destination);
        }, {once:true});
      } else {
        proceed.hidden = true;
      }
    } catch (error) {
      if (error.name === "AbortError") return;
      if (!overlay || overlay !== box) return;
      title.textContent = "Validação temporariamente indisponível";
      message.textContent = "Não foi possível confirmar seu acesso agora. Nenhuma etapa foi liberada indevidamente. Tente novamente em alguns instantes.";
    }
  }

  // Fase de captura: intercepta também links de acesso rápido da barra lateral,
  // sem executar em paralelo o antigo verificador do dashboard.
  document.addEventListener("click", event => {
    const anchor = event.target.closest('a[href]');
    if (!anchor || !body.classList.contains("dashboard-page")) return;
    const path = new URL(anchor.href, window.location.origin);
    if (path.origin !== window.location.origin) return;
    const found = path.pathname.match(/^\/disciplina\/(\d+)\/?$/);
    if (!found) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    showCourseAccess(path.pathname, found[1]);
  }, true);
})();
