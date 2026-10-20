/* Ledgerly interaction layer.
   Small, independent modules: API helper, theme, navigation, toasts, dialogs,
   scroll reveals, counters, spotlight/tilt/parallax, magnetic buttons, page
   progress, landing-page demos and form helpers. Every animated module checks
   prefers-reduced-motion and degrades to a static, fully readable page. */
(function () {
  'use strict';

  const root = document.documentElement;
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const reduceMotion = () => motionQuery.matches;
  const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  // ---- API helper (JSON + CSRF) -------------------------------------------------
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';

  async function api(url, { method = 'GET', body } = {}) {
    const options = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-CSRFToken'] = csrfToken;
      options.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(url, options);
    } catch (err) {
      throw new Error('Network error. Check your connection and try again.');
    }
    let data = {};
    try { data = await response.json(); } catch (_) { /* non-JSON error page */ }
    if (!response.ok) {
      const error = new Error(data.error || `Request failed (${response.status}).`);
      error.fields = data.fields || {};
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function formatINR(value, decimals = 2) {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency', currency: 'INR', minimumFractionDigits: decimals, maximumFractionDigits: decimals,
    }).format(Number(value) || 0).replace('-', '−');
  }

  function niceDate(iso) {
    const d = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  // ---- Toasts --------------------------------------------------------------------
  // Server flash messages arrive in .alerts; we lift them into a floating stack.
  const toastStack = document.createElement('div');
  toastStack.className = 'toast-stack';
  toastStack.setAttribute('role', 'status');
  toastStack.setAttribute('aria-live', 'polite');
  document.body.appendChild(toastStack);

  function dismiss(alert) {
    if (!alert || alert.classList.contains('is-leaving')) return;
    if (reduceMotion()) { alert.remove(); return; }
    alert.classList.add('is-leaving');
    alert.addEventListener('animationend', () => alert.remove(), { once: true });
  }

  function armToast(alert) {
    alert.querySelector('[data-dismiss]')?.addEventListener('click', () => dismiss(alert));
    const persistent = alert.classList.contains('alert-danger') || alert.classList.contains('alert-warning');
    if (persistent) return; // errors stay until the user closes them
    const ms = 5200;
    const bar = document.createElement('span');
    bar.className = 'toast-progress';
    alert.style.setProperty('--toast-ms', `${ms}ms`);
    alert.appendChild(bar);
    bar.addEventListener('animationend', () => dismiss(alert));
  }

  function toast(message, kind = 'success') {
    const icons = { success: 'fa-circle-check', info: 'fa-circle-info', warning: 'fa-triangle-exclamation', danger: 'fa-circle-xmark' };
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind}`;
    const icon = document.createElement('i');
    icon.className = `fa-solid ${icons[kind] || icons.info}`;
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('div');
    text.textContent = message;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'alert-close';
    close.setAttribute('aria-label', 'Dismiss message');
    close.setAttribute('data-dismiss', '');
    close.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
    alert.append(icon, text, close);
    toastStack.appendChild(alert);
    armToast(alert);
  }

  document.querySelectorAll('.alerts').forEach((group) => {
    group.querySelectorAll('.alert').forEach((alert) => { toastStack.appendChild(alert); armToast(alert); });
    group.remove();
  });

  window.Ledgerly = { api, formatINR, niceDate, toast };

  // ---- Theme -------------------------------------------------------------------------
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');
  const effectiveTheme = () => root.getAttribute('data-theme') || (darkQuery.matches ? 'dark' : 'light');
  document.querySelectorAll('[data-theme-toggle]').forEach((btn) => {
    const sync = () => btn.setAttribute('aria-label', effectiveTheme() === 'dark' ? 'Switch to light theme' : 'Switch to dark theme');
    sync();
    btn.addEventListener('click', () => {
      const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      const apply = () => {
        root.setAttribute('data-theme', next);
        try { localStorage.setItem('theme', next); } catch (_) { /* storage blocked */ }
        document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
        sync();
      };
      // A soft cross-fade between themes where the browser supports it.
      if (document.startViewTransition && !reduceMotion()) document.startViewTransition(apply); else apply();
    });
  });
  darkQuery.addEventListener('change', () => document.dispatchEvent(new CustomEvent('themechange')));

  function withTransition(apply) {
    if (document.startViewTransition && !reduceMotion()) document.startViewTransition(apply); else apply();
  }

  // ---- Light / dark / system mode buttons (Account > Appearance) -----------------
  const modeButtons = document.querySelectorAll('[data-theme-mode]');
  function syncModes() {
    const mode = root.getAttribute('data-theme') || 'system';
    modeButtons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.themeMode === mode)));
  }
  modeButtons.forEach((b) => b.addEventListener('click', () => withTransition(() => {
    const mode = b.dataset.themeMode;
    if (mode === 'system') {
      root.removeAttribute('data-theme');
      try { localStorage.removeItem('theme'); } catch (_) { /* storage blocked */ }
    } else {
      root.setAttribute('data-theme', mode);
      try { localStorage.setItem('theme', mode); } catch (_) { /* storage blocked */ }
    }
    syncModes();
    document.dispatchEvent(new CustomEvent('themechange'));
  })));
  syncModes();
  document.addEventListener('themechange', syncModes);

  // ---- Mobile navigation drawer ----------------------------------------------------
  const sidebar = document.getElementById('sidebar');
  const toggle = document.querySelector('[data-menu-toggle]');
  let backdrop = null;
  function closeMenu() {
    if (!sidebar) return;
    sidebar.classList.remove('is-open');
    toggle?.setAttribute('aria-expanded', 'false');
    backdrop?.remove();
    backdrop = null;
  }
  toggle?.addEventListener('click', () => {
    if (sidebar.classList.contains('is-open')) { closeMenu(); return; }
    sidebar.classList.add('is-open');
    toggle.setAttribute('aria-expanded', 'true');
    backdrop = document.createElement('div');
    backdrop.className = 'sidebar-backdrop';
    backdrop.addEventListener('click', closeMenu);
    document.body.appendChild(backdrop);
    sidebar.querySelector('a')?.focus();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });

  // ---- Sliding navigation indicator (sidebar) ------------------------------------
  const nav = document.querySelector('.sidebar .nav');
  if (nav) {
    const indicator = document.createElement('span');
    indicator.className = 'nav-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    nav.prepend(indicator);
    const current = nav.querySelector('a[aria-current="page"]');
    const moveTo = (link, instant = false) => {
      if (!link) { indicator.style.opacity = '0'; return; }
      if (instant) indicator.style.transition = 'none';
      indicator.style.transform = `translateY(${link.offsetTop}px)`;
      indicator.style.height = `${link.offsetHeight}px`;
      indicator.style.opacity = '1';
      if (instant) requestAnimationFrame(() => { indicator.style.transition = ''; });
    };
    moveTo(current, true);
    nav.querySelectorAll('a').forEach((link) => {
      link.addEventListener('mouseenter', () => moveTo(link));
      link.addEventListener('focus', () => moveTo(link));
    });
    nav.addEventListener('mouseleave', () => moveTo(current));
    window.addEventListener('resize', () => moveTo(current, true));
  }

  // ---- Sliding pill for landing-page section links -------------------------------
  const links = document.querySelector('.public-nav .links');
  if (links) {
    const pill = document.createElement('span');
    pill.className = 'link-pill';
    pill.setAttribute('aria-hidden', 'true');
    links.prepend(pill);
    const anchors = Array.from(links.querySelectorAll('a'));
    let active = null;
    const place = (a) => {
      if (!a) { pill.style.opacity = '0'; return; }
      pill.style.width = `${a.offsetWidth}px`;
      pill.style.transform = `translateX(${a.offsetLeft}px)`;
      pill.style.opacity = '1';
    };
    anchors.forEach((a) => a.addEventListener('mouseenter', () => place(a)));
    links.addEventListener('mouseleave', () => place(active));
    // Highlight the section currently in view.
    const sections = anchors.map((a) => document.querySelector(a.getAttribute('href'))).filter(Boolean);
    if ('IntersectionObserver' in window && sections.length) {
      const spy = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          active = anchors.find((a) => a.getAttribute('href') === `#${entry.target.id}`) || null;
          anchors.forEach((a) => a.classList.toggle('is-current', a === active));
          place(active);
        });
      }, { rootMargin: '-45% 0px -50% 0px' });
      sections.forEach((s) => spy.observe(s));
    }
  }

  // ---- Password visibility ------------------------------------------------------------
  document.querySelectorAll('[data-password-toggle]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const input = document.getElementById(btn.dataset.passwordToggle);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-pressed', String(show));
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.querySelector('i').className = show ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye';
    });
  });

  // ---- Password strength + confirmation feedback (registration) ------------------
  const pw = document.querySelector('[data-strength]');
  if (pw) {
    const meter = document.getElementById(pw.dataset.strength);
    const label = document.getElementById(`${pw.dataset.strength}-label`);
    const confirm = document.querySelector('[data-match]');
    const words = ['Too short', 'Weak', 'Fair', 'Good', 'Strong'];
    const score = (v) => {
      if (v.length < 8) return v.length ? 1 : 0;
      let s = 1;
      if (v.length >= 12) s += 1;
      if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s += 1;
      if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) s += 1;
      else if (/\d|[^A-Za-z0-9]/.test(v) && v.length >= 10) s += 0.5;
      return Math.min(4, Math.floor(s));
    };
    const update = () => {
      const s = score(pw.value);
      meter.dataset.score = String(s);
      if (label) label.textContent = pw.value ? `${words[s]}${pw.value.length < 8 ? ' (8 characters minimum)' : ''}` : 'At least 8 characters. A short phrase works well.';
      if (confirm && confirm.value) {
        const ok = confirm.value === pw.value;
        confirm.classList.toggle('is-valid', ok);
        const note = document.getElementById('match-note');
        if (note) {
          note.className = ok ? 'field-ok' : 'hint';
          note.textContent = ok ? 'Passwords match' : 'Passwords don\'t match yet';
        }
      }
    };
    pw.addEventListener('input', update);
    confirm?.addEventListener('input', update);
  }

  // ---- Confirm dialogs for destructive forms (with exit animation) --------------
  const dialog = document.getElementById('confirm-dialog');
  if (dialog) {
    dialog.addEventListener('cancel', (e) => { e.preventDefault(); closeDialog('cancel'); });
    dialog.querySelectorAll('button[value]').forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); closeDialog(b.value); }));
  }
  let pendingForm = null;
  function closeDialog(value) {
    const finish = () => {
      dialog.classList.remove('is-closing');
      dialog.close(value);
      if (value === 'confirm' && pendingForm) { pendingForm.dataset.confirmed = '1'; pendingForm.requestSubmit(); }
      pendingForm = null;
    };
    if (reduceMotion()) { finish(); return; }
    dialog.classList.add('is-closing');
    dialog.addEventListener('animationend', finish, { once: true });
  }
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.confirm || form.dataset.confirmed === '1') return;
    event.preventDefault();
    if (!dialog || typeof dialog.showModal !== 'function') {
      if (window.confirm(form.dataset.confirm)) { form.dataset.confirmed = '1'; form.requestSubmit(); }
      return;
    }
    pendingForm = form;
    dialog.querySelector('#confirm-text').textContent = form.dataset.confirm;
    dialog.querySelector('#confirm-ok').textContent = form.dataset.confirmLabel || 'Delete';
    dialog.showModal();
    dialog.querySelector('[value="cancel"]').focus();
  }, true);

  // ---- Page progress bar for navigations and form posts ---------------------------
  const progress = document.querySelector('.page-progress');
  const startProgress = () => progress?.classList.add('is-active');
  document.addEventListener('click', (e) => {
    const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || a.target === '_blank' || a.hasAttribute('download')) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin || (url.pathname === location.pathname && url.hash)) return;
    if (/\.(csv|json)$/.test(url.pathname)) return; // downloads don't navigate
    startProgress();
  });
  document.addEventListener('submit', (e) => {
    if (!e.defaultPrevented && e.target instanceof HTMLFormElement && e.target.method.toLowerCase() !== 'dialog') startProgress();
  });
  window.addEventListener('pageshow', () => progress?.classList.remove('is-active'));

  // ---- Count-up numbers (when they scroll into view) ---------------------------------
  function countUp(el) {
    const target = Number(el.dataset.countup);
    const decimals = Number(el.dataset.decimals || 0);
    if (!Number.isFinite(target) || reduceMotion()) return;
    const isMoney = el.textContent.includes('₹');
    const final = el.textContent;
    const start = performance.now();
    const duration = 1300;
    (function frame(now) {
      const t = Math.min(1, (now - start) / duration);
      const v = target * (1 - Math.pow(1 - t, 4));
      el.textContent = isMoney ? formatINR(v, decimals) : Math.round(v).toLocaleString('en-IN');
      if (t < 1) requestAnimationFrame(frame); else el.textContent = final;
    })(start);
  }

  // ---- Scroll reveals ----------------------------------------------------------------
  const revealables = document.querySelectorAll('[data-reveal], [data-countup], .score-ring');
  document.querySelectorAll('[data-reveal-group]').forEach((group) => {
    Array.from(group.querySelectorAll(':scope > [data-reveal]')).forEach((child, i) => child.style.setProperty('--i', i));
  });
  function show(el) {
    el.classList.add('is-visible');
    if (el.dataset.countup !== undefined && !el.dataset.counted) { el.dataset.counted = '1'; countUp(el); }
    if (el.classList.contains('score-ring') && el.dataset.score) el.style.setProperty('--value', el.dataset.score);
  }
  if ('IntersectionObserver' in window && !reduceMotion()) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => { if (entry.isIntersecting) { show(entry.target); io.unobserve(entry.target); } });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    revealables.forEach((el) => {
      if (el.classList.contains('score-ring') && el.dataset.score) el.style.setProperty('--value', 0);
      io.observe(el);
    });
  } else {
    revealables.forEach(show);
  }

  // ---- Pointer effects: card spotlight, tilt, parallax, stage glow ----------------
  if (finePointer) {
    let frame = 0;
    let last = null;
    const stage = document.querySelector('.aurora-bg');
    document.addEventListener('pointermove', (event) => {
      last = event;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const el = last.target instanceof Element ? last.target.closest('.card, .tile') : null;
        if (el) {
          const rect = el.getBoundingClientRect();
          el.style.setProperty('--mx', `${last.clientX - rect.left}px`);
          el.style.setProperty('--my', `${last.clientY - rect.top}px`);
        }
        if (stage) {
          stage.style.setProperty('--cx', `${last.clientX}px`);
          stage.style.setProperty('--cy', `${last.clientY}px`);
        }
      });
    }, { passive: true });
  }
  if (finePointer && !reduceMotion()) {
    document.querySelectorAll('[data-tilt]').forEach((card) => {
      const scope = card.closest('[data-tilt-scope]') || card;
      const layers = scope.querySelectorAll('[data-depth]');
      scope.addEventListener('pointermove', (event) => {
        const rect = card.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width - 0.5;
        const y = (event.clientY - rect.top) / rect.height - 0.5;
        card.style.setProperty('--tilt-y', `${(x * 10).toFixed(2)}deg`);
        card.style.setProperty('--tilt-x', `${(-y * 8).toFixed(2)}deg`);
        card.style.setProperty('--gx', `${(x + 0.5) * 100}%`);
        card.style.setProperty('--gy', `${(y + 0.5) * 100}%`);
        layers.forEach((layer) => {
          const depth = Number(layer.dataset.depth) || 10;
          layer.style.setProperty('--px', `${(-x * depth).toFixed(1)}px`);
          layer.style.setProperty('--py', `${(-y * depth).toFixed(1)}px`);
        });
      });
      scope.addEventListener('pointerleave', () => {
        ['--tilt-x', '--tilt-y'].forEach((p) => card.style.setProperty(p, '0deg'));
        card.style.setProperty('--gx', '75%');
        card.style.setProperty('--gy', '0%');
        layers.forEach((layer) => { layer.style.setProperty('--px', '0px'); layer.style.setProperty('--py', '0px'); });
      });
    });

    // Magnetic call-to-action buttons: drift a few pixels toward the cursor.
    document.querySelectorAll('[data-magnetic]').forEach((btn) => {
      btn.classList.add('magnetic');
      btn.addEventListener('pointermove', (e) => {
        const r = btn.getBoundingClientRect();
        const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
        const dy = (e.clientY - (r.top + r.height / 2)) / r.height;
        btn.style.transform = `translate(${(dx * 8).toFixed(1)}px, ${(dy * 6).toFixed(1)}px)`;
      });
      btn.addEventListener('pointerleave', () => { btn.style.transform = ''; });
    });
  }

  // ---- Transaction filters: on phones, everything but search folds behind a toggle ----
  document.querySelectorAll('[data-filters-toggle]').forEach((button) => {
    const form = button.closest('form');
    button.addEventListener('click', () => {
      const open = form.classList.toggle('is-open');
      button.setAttribute('aria-expanded', String(open));
    });
  });

  // ---- Pause decorative loops (aura, spinning card edge, ticker) while off-screen ----
  if ('IntersectionObserver' in window) {
    const pauser = new IntersectionObserver((entries) => {
      entries.forEach((entry) => entry.target.classList.toggle('is-paused', !entry.isIntersecting));
    });
    document.querySelectorAll('.hero-visual, .balance-card, .ticker').forEach((el) => pauser.observe(el));
  }

  // ---- Starfield (public pages; visible in dark mode) ------------------------------
  const starCanvas = document.querySelector('canvas.stars');
  if (starCanvas) {
    const ctx = starCanvas.getContext('2d');
    let stars = [];
    let width = 0;
    let height = 0;
    let raf = 0;
    const draw = (t) => {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#ffffff';
      for (const star of stars) {
        ctx.globalAlpha = reduceMotion() ? star.alpha : star.alpha * (0.55 + 0.45 * Math.sin(star.phase + t * star.speed));
        ctx.beginPath();
        ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
    const seed = () => {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      width = window.innerWidth;
      // Cover the tallest the viewport gets (phone toolbars collapse while scrolling), so a
      // toolbar sliding in or out never re-seeds; the stage clips the overhang.
      height = Math.max(window.innerHeight, window.screen.height || 0);
      starCanvas.width = width * dpr;
      starCanvas.height = height * dpr;
      starCanvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      stars = Array.from({ length: Math.round((width * height) / 8500) }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        r: Math.random() * 1.1 + 0.25,
        alpha: Math.random() * 0.55 + 0.4,
        phase: Math.random() * Math.PI * 2,
        speed: (Math.random() * 1.2 + 0.25) / 1000, // radians per ms: 5-25 s twinkles
      }));
    };
    // The theme decides: --stars-opacity is 0 in light mode.
    const visible = () => Number(getComputedStyle(root).getPropertyValue('--stars-opacity').trim() || 0) > 0;
    // Twinkles take 5-25 s, so 20 redraws a second is indistinguishable from 60 at a third of the cost.
    let timer = 0;
    const loop = () => { raf = requestAnimationFrame((t) => { draw(t); timer = setTimeout(loop, 50); }); };
    const start = () => {
      cancelAnimationFrame(raf);
      clearTimeout(timer);
      if (!visible()) { ctx.clearRect(0, 0, width, height); return; }
      if (reduceMotion() || document.hidden) draw(0); else loop();
    };
    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      if (window.innerWidth === width && window.innerHeight <= height) return; // toolbar show/hide
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { seed(); start(); }, 200);
    });
    document.addEventListener('visibilitychange', start);
    document.addEventListener('themechange', () => setTimeout(start, 60));
    seed();
    start();
  }

  // ---- Landing: typewriter demo of natural-language entry ------------------------------
  const typer = document.querySelector('[data-typewriter]');
  if (typer) {
    const textEl = typer.querySelector('[data-typed]');
    const chipsEl = document.querySelector(typer.dataset.typewriter);
    const examples = JSON.parse(typer.dataset.examples || '[]');
    const renderChips = (chips) => {
      chipsEl.replaceChildren(...chips.map((c) => { const s = document.createElement('span'); s.textContent = c; return s; }));
    };
    if (reduceMotion() || !examples.length) {
      if (examples.length) { textEl.textContent = examples[0].text; renderChips(examples[0].chips); }
    } else {
      let i = 0;
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const run = async () => {
        for (;;) {
          const ex = examples[i % examples.length];
          chipsEl.replaceChildren();
          textEl.textContent = '';
          for (const ch of ex.text) { textEl.textContent += ch; await sleep(38 + Math.random() * 40); }
          await sleep(450);
          renderChips(ex.chips);
          await sleep(2600);
          while (textEl.textContent.length) { textEl.textContent = textEl.textContent.slice(0, -2); await sleep(14); }
          i += 1;
        }
      };
      // Start only once the demo is on screen.
      const start = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { start.disconnect(); run(); }
      });
      start.observe(typer);
    }
  }

  // ---- Landing: rotating voice phrases --------------------------------------------------
  document.querySelectorAll('[data-rotate]').forEach((el) => {
    const phrases = JSON.parse(el.dataset.rotate || '[]');
    if (phrases.length < 2 || reduceMotion()) return;
    let i = 0;
    el.classList.add('rotating');
    setInterval(() => {
      el.classList.add('is-swapping');
      setTimeout(() => {
        i = (i + 1) % phrases.length;
        el.textContent = phrases[i];
        el.classList.remove('is-swapping');
      }, 350);
    }, 3200);
  });

  // ---- Keyboard shortcut: "/" opens the assistant ----------------------------------------
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag) || document.activeElement?.isContentEditable) return;
    const fab = document.getElementById('assistant-fab');
    if (fab) { e.preventDefault(); fab.click(); }
  });
})();
