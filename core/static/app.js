/* =========================================================
   app.js — Elecciones 2026 UI Global
   Sidebar · toast API · HTMX hooks · Acta viewer modal
   password toggle · keyboard shortcuts
   ========================================================= */

/* Minimal vanilla JS: sidebar offcanvas, tabs, toast API, password show toggle, split review image zoom */
(function(){
  document.addEventListener('DOMContentLoaded', function(){
    /* ---------- Sidebar Bootstrap Offcanvas (≥5.3) ---------- */
    function getSidebarInst(){
      var el = document.getElementById('sidebarOffcanvas');
      if (!el) return null;
      if (!window.bootstrap || !window.bootstrap.Offcanvas) return null;
      try { return window.bootstrap.Offcanvas.getOrCreateInstance(el, { backdrop: true, scroll: true }); }
      catch(_) { return null; }
    }
    function closeSidebar(){
      // En desktop (≥992px) offcanvas no tiene efecto; no hacemos nada.
      if (window.matchMedia('(min-width: 992px)').matches) return;
      var inst = getSidebarInst();
      if (inst) { try { inst.hide(); } catch(_){} }
      // fallback por si la API no cargó aún
      var el = document.getElementById('sidebarOffcanvas');
      if (el) el.classList.remove('show');
    }
    // Cerrar sidebar al hacer clic en links cuando es móvil/tablet
    document.querySelectorAll('.sidebar-nav a.sidebar-link').forEach(function(a){
      a.addEventListener('click', function(){
        if (window.matchMedia('(max-width: 991.98px)').matches) closeSidebar();
      });
    });

    /* Tabs: soporta tab-btn + tab-panel y tab-headers/tab-body */
    document.querySelectorAll('[data-tabs]').forEach(function(grp){
      var heads = grp.querySelectorAll('.tab-nav button, .tabs-nav .tab-btn, .tab-headers button');
      var panels = grp.querySelectorAll('.tab-panel, .tab-body');
      heads.forEach(function(btn, idx){
        btn.addEventListener('click', function(ev){
          ev.preventDefault();
          var target = btn.getAttribute('data-tab-target');
          heads.forEach(function(h){ h.classList.remove('active'); h.setAttribute('aria-selected','false'); });
          btn.classList.add('active'); btn.setAttribute('aria-selected','true');
          panels.forEach(function(p){ p.classList.remove('active'); });
          if (target) {
            var el = document.querySelector(target);
            if (el) el.classList.add('active');
          } else if (panels[idx]) {
            panels[idx].classList.add('active');
          }
        });
      });
    });

    /* ================= GLOBAL ACTA VIEWER MODAL ================= */
    (function(){
      var modal  = document.getElementById('actaViewerModal');
      if (!modal) return;
      var img    = document.getElementById('actaViewerImg');
      var stage  = document.getElementById('actaViewerStage');
      var meta   = document.getElementById('actaViewerMeta');
      var splitA = document.getElementById('actaViewerSplitLink');
      var zLabel = document.getElementById('actaViewerZoomPct');
      var state = { scale: 1, rot: 0, acta_pk: null };

      function apply(){
        if (!img) return;
        img.style.transform = 'scale(' + state.scale.toFixed(3) + ') rotate(' + state.rot + 'deg)';
        if (zLabel) zLabel.textContent = 'Zoom: ' + Math.round(state.scale*100) + '% · Rotación: ' + state.rot + '°';
      }
      function open(pk, imgUrl, metaText, reviewUrl){
        state = { scale: 1, rot: 0, acta_pk: pk };
        if (img) { img.src = imgUrl || ''; img.alt = 'Acta mesa #' + (pk||''); }
        if (meta) meta.textContent = metaText || '—';
        if (splitA) splitA.href = reviewUrl || '#';
        apply();
        modal.setAttribute('aria-hidden','false');
        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
      }
      function close(){
        modal.setAttribute('aria-hidden','true');
        modal.classList.remove('open');
        document.body.style.overflow = '';
        if (img) img.style.transform = '';
      }
      // Delegación de botones del modal
      modal.addEventListener('click', function(ev){
        var t = ev.target.closest('[data-acta-act]');
        if (t) {
          ev.preventDefault();
          var act = t.getAttribute('data-acta-act');
          if (act === 'zoom-in')  { state.scale = Math.min(5, state.scale + 0.2); apply(); }
          else if (act === 'zoom-out') { state.scale = Math.max(0.25, state.scale - 0.2); apply(); }
          else if (act === 'rotate')    { state.rot = (state.rot + 90) % 360; apply(); }
          else if (act === 'reset')     { state = { scale: 1, rot: 0, acta_pk: state.acta_pk }; apply(); }
          else if (act === 'maximize')  { (modal.requestFullscreen || modal.webkitRequestFullscreen || function(){}).call(modal); }
          return;
        }
        if (ev.target.closest('[data-acta-close]')) { ev.preventDefault(); close(); }
      });
      // Rueda del mouse = scroll; Ctrl + Rueda = zoom
      stage && stage.addEventListener('wheel', function(ev){
        if (ev.ctrlKey || ev.metaKey) {
          ev.preventDefault();
          state.scale = Math.max(0.25, Math.min(5, state.scale + (ev.deltaY < 0 ? 0.15 : -0.15)));
          apply();
        }
      }, { passive: false });
      // Atajos teclado
      document.addEventListener('keydown', function(ev){
        if (!modal.classList.contains('open')) return;
        if (ev.key === 'Escape') { close(); }
        else if (ev.key === '+' || ev.key === '=') { state.scale = Math.min(5, state.scale + 0.2); apply(); }
        else if (ev.key === '-') { state.scale = Math.max(0.25, state.scale - 0.2); apply(); }
        else if (ev.key === 'r' || ev.key === 'R') { state = { scale:1, rot:0, acta_pk: state.acta_pk }; apply(); }
        else if (ev.key === 'g' || ev.key === 'G') { state.rot = (state.rot + 90) % 360; apply(); }
        else if (ev.key === 'f' || ev.key === 'F') { (modal.requestFullscreen || modal.webkitRequestFullscreen || function(){}).call(modal); }
      });
      // Botones de apertura desde la UI: data-acta-view="{pk}" data-img-url data-meta data-review-url
      document.addEventListener('click', function(ev){
        var a = ev.target.closest('[data-acta-view]');
        if (!a) return;
        ev.preventDefault();
        open(
          a.getAttribute('data-acta-view'),
          a.getAttribute('data-img-url')     || a.getAttribute('href'),
          a.getAttribute('data-meta')        || 'Mesa #' + a.getAttribute('data-acta-view'),
          a.getAttribute('data-review-url')  || ''
        );
      });
      // Exponer API global (uso desde scripts inline etc)
      window.ActaViewer = { open: open, close: close, get state(){ return state; } };
    })();

    /* Password toggle */
    document.querySelectorAll('.toggle-pwd').forEach(function(btn){
      btn.addEventListener('click', function(){
        var inp = document.querySelector(btn.dataset.target || '#id_password');
        if (!inp) return;
        var svgShow = btn.querySelector('.sh'); var svgHide = btn.querySelector('.hd');
        if (inp.type === 'password') { inp.type = 'text'; if(svgShow) svgShow.style.display='none'; if(svgHide) svgHide.style.display='block'; }
        else { inp.type = 'password'; if(svgShow) svgShow.style.display='block'; if(svgHide) svgHide.style.display='none'; }
      });
    });
  });

  /* Global toast API: window.showToast(msg, 'success|error|warn|info' [, ttl_ms]) */
  window.showToast = function(msg, type, ttl){
    type = type || 'info'; ttl = ttl || 2800;
    var root = document.getElementById('toast-root'); if (!root) return;
    var el = document.createElement('div');
    el.className = 'toast ' + type;
    var icon = {
      success:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
      error  :'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
      warn   :'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
      info   :'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
    }[type] || '';
    el.innerHTML = icon + '<span>' + msg + '</span>';
    root.appendChild(el);
    requestAnimationFrame(function(){ el.classList.add('show'); });
    setTimeout(function(){
      el.classList.remove('show');
      setTimeout(function(){ if (el.parentNode) el.parentNode.removeChild(el); }, 350);
    }, ttl);
  };

  /* Alias compatibilidad: window.toast.success/error/warn/info(msg, ttl_ms?) */
  window.toast = {
    success:function(m,t){ window.showToast(m,'success',t); },
    error  :function(m,t){ window.showToast(m,'error',t); },
    warn   :function(m,t){ window.showToast(m,'warn',t); },
    info   :function(m,t){ window.showToast(m,'info',t); }
  };

  /* HTMX event hooks */
  document.addEventListener('htmx:responseError', function(evt){
    window.showToast('Error de comunicación (' + (evt.detail.xhr ? evt.detail.xhr.status : '?') + '). Inténtalo nuevamente.', 'error');
  });
  document.addEventListener('htmx:afterRequest', function(evt){
    var hdr = evt.detail.xhr && evt.detail.xhr.getResponseHeader && evt.detail.xhr.getResponseHeader('X-Toast');
    if (hdr) { try { var j = JSON.parse(hdr); window.showToast(j.m, j.t); } catch(e){ window.showToast(hdr, 'info'); } }
  });
})();
