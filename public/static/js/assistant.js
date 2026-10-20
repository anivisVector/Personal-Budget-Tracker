/* Assistant panel: type or speak a command. The server classifies it and returns a
   factual reply plus (optionally) a proposed action; nothing is saved until the user
   confirms. All text is inserted with textContent, never innerHTML. */
(function () {
  'use strict';
  const panel = document.getElementById('assistant');
  const fab = document.getElementById('assistant-fab');
  if (!panel || !fab) return;
  const { api } = window.Ledgerly;

  const log = document.getElementById('assistant-log');
  const form = document.getElementById('assistant-form');
  const input = document.getElementById('assistant-input');
  const mic = document.getElementById('assistant-mic');
  const endpoints = {
    command: panel.dataset.endpoint,
    transaction: panel.dataset.transactions,
    budget: panel.dataset.budgets,
    category: panel.dataset.categories,
  };
  let pending = null;
  let busy = false;
  let lastWasVoice = false;

  // ---- Panel open/close -------------------------------------------------------
  function open() {
    panel.classList.remove('hidden');
    fab.setAttribute('aria-expanded', 'true');
    input.focus();
  }
  function close() {
    fab.setAttribute('aria-expanded', 'false');
    stopListening();
    window.speechSynthesis?.cancel();
    fab.focus();
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { panel.classList.add('hidden'); return; }
    panel.classList.add('is-closing');
    panel.addEventListener('animationend', () => {
      panel.classList.remove('is-closing');
      panel.classList.add('hidden');
    }, { once: true });
  }
  fab.addEventListener('click', () => (panel.classList.contains('hidden') ? open() : close()));
  panel.querySelector('[data-assistant-close]').addEventListener('click', close);
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

  // ---- Messages -----------------------------------------------------------------
  function bubble(text, who = 'bot') {
    const node = document.createElement('div');
    node.className = `bubble ${who}`;
    node.textContent = text;
    log.append(node);
    log.scrollTop = log.scrollHeight;
    return node;
  }

  function addButtons(node, buttons) {
    const row = document.createElement('div');
    row.className = 'confirm-row';
    buttons.forEach(({ label, primary, onClick }) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `btn btn-sm ${primary ? 'btn-primary' : ''}`;
      b.textContent = label;
      b.addEventListener('click', () => { row.remove(); onClick(); });
      row.append(b);
    });
    node.append(row);
    row.querySelector('button')?.focus();
  }

  function speak(text) {
    if (!lastWasVoice || !window.speechSynthesis || !text) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-IN';
    const voices = window.speechSynthesis.getVoices();
    utterance.voice = voices.find((v) => /en-IN/i.test(v.lang)) || voices.find((v) => /^en/i.test(v.lang)) || null;
    window.speechSynthesis.speak(utterance);
  }

  // ---- Actions --------------------------------------------------------------------
  async function runAction(action) {
    const routes = { transaction: endpoints.transaction, budget: endpoints.budget, category: endpoints.category };
    const url = routes[action.kind];
    if (!url) return;
    try {
      await api(url, { method: 'POST', body: action.payload });
      const done = {
        transaction: 'Done! Transaction saved.',
        budget: 'Done! Budget saved.',
        category: 'Done! Category created.',
      }[action.kind];
      const note = bubble(done);
      speak(done);
      addButtons(note, [{ label: 'Refresh page', primary: true, onClick: () => window.location.reload() }]);
    } catch (err) {
      bubble(`Couldn't save that: ${err.message}`);
      speak('Sorry, I could not save that.');
    }
  }

  function propose(action) {
    pending = action;
    const node = log.lastElementChild;
    addButtons(node, [
      { label: 'Confirm', primary: true, onClick: () => { const a = pending; pending = null; runAction(a); } },
      { label: 'Cancel', onClick: () => { pending = null; bubble('Okay, cancelled.'); } },
    ]);
  }

  async function send(text) {
    if (busy || !text.trim()) return;
    busy = true;
    bubble(text, 'user');
    const thinking = bubble('', 'bot typing');
    thinking.setAttribute('aria-label', 'Assistant is thinking');
    thinking.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));
    try {
      const result = await api(endpoints.command, { method: 'POST', body: { text } });
      thinking.remove();
      if (result.intent === 'confirm' && pending) {
        const a = pending; pending = null;
        log.querySelectorAll('.confirm-row').forEach((r) => r.remove());
        await runAction(a);
        return;
      }
      if (result.intent === 'cancel' && pending) {
        pending = null;
        log.querySelectorAll('.confirm-row').forEach((r) => r.remove());
        bubble('Okay, cancelled.');
        speak('Cancelled.');
        return;
      }
      bubble(result.display || result.speak);
      speak(result.speak);
      if (result.action) propose(result.action);
      if (result.intent === 'navigate' && result.url) setTimeout(() => { window.location.href = result.url; }, 700);
    } catch (err) {
      thinking.remove();
      bubble(err.message);
    } finally {
      busy = false;
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value;
    input.value = '';
    lastWasVoice = false;
    send(text);
  });
  panel.querySelectorAll('[data-example]').forEach((chip) => chip.addEventListener('click', () => { lastWasVoice = false; send(chip.textContent); }));

  // ---- Voice input (Web Speech API, Chromium browsers) ----------------------------------
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let listening = false;

  function stopListening() {
    if (recognition && listening) recognition.stop();
  }

  if (!Recognition) {
    mic.disabled = true;
    mic.title = 'Voice input needs Chrome or Edge. You can still type.';
  } else {
    recognition = new Recognition();
    recognition.lang = 'en-IN';
    recognition.interimResults = true;
    recognition.continuous = false;
    let finalText = '';
    recognition.onstart = () => {
      listening = true; finalText = '';
      mic.classList.add('is-listening'); mic.setAttribute('aria-pressed', 'true');
      input.placeholder = 'Listening…';
    };
    recognition.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t; else interim += t;
      }
      input.value = finalText || interim;
    };
    recognition.onerror = (event) => {
      const messages = { 'not-allowed': 'Microphone access was blocked. Allow it in your browser settings, or type instead.', 'no-speech': 'I didn\'t hear anything. Tap the mic and try again.' };
      bubble(messages[event.error] || 'Voice input stopped. You can type instead.');
    };
    recognition.onend = () => {
      listening = false;
      mic.classList.remove('is-listening'); mic.setAttribute('aria-pressed', 'false');
      input.placeholder = 'Type or tap the mic…';
      const text = (finalText || '').trim();
      if (text) { input.value = ''; lastWasVoice = true; send(text); }
    };
    mic.addEventListener('click', () => {
      if (listening) { recognition.stop(); return; }
      window.speechSynthesis?.cancel();
      try { recognition.start(); } catch (_) { /* already starting */ }
    });
  }
})();
