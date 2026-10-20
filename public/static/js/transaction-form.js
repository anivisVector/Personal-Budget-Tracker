/* Transaction form: keep the category list in sync with the type, and suggest a
   category from the user's own history as they type a title or note. */
(function () {
  'use strict';
  const form = document.getElementById('txn-form');
  if (!form) return;
  const { api } = window.Ledgerly;
  const select = document.getElementById('category');
  const title = document.getElementById('title');
  const note = document.getElementById('description');
  const slot = document.getElementById('suggestion');
  const allOptions = Array.from(select.options).map((o) => ({ value: o.value, text: o.text, type: o.dataset.type || '' }));

  const currentType = () => form.querySelector('input[name="type"]:checked')?.value || 'expense';

  function syncCategories() {
    const type = currentType();
    const keep = select.value;
    select.replaceChildren();
    allOptions
      .filter((o) => !o.type || o.type === type)
      .forEach((o) => select.add(new Option(o.text, o.value, false, o.value === keep)));
    if (!Array.from(select.options).some((o) => o.value === keep)) select.value = '';
  }
  form.querySelectorAll('input[name="type"]').forEach((r) => r.addEventListener('change', () => { syncCategories(); requestSuggestion(); }));
  syncCategories();

  let timer = null;
  let lastQuery = '';
  function requestSuggestion() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const text = `${title.value} ${note.value}`.trim();
      if (text.length < 3 || text === lastQuery) { if (text.length < 3) slot.replaceChildren(); return; }
      lastQuery = text;
      try {
        const { suggestion } = await api(form.dataset.suggest, { method: 'POST', body: { title: title.value, description: note.value, type: currentType() } });
        slot.replaceChildren();
        if (!suggestion || suggestion.category === select.value) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'suggest-chip';
        const icon = document.createElement('i');
        icon.className = 'fa-solid fa-wand-magic-sparkles';
        icon.setAttribute('aria-hidden', 'true');
        btn.append(icon, document.createTextNode(` Use ${suggestion.category} (${Math.round(suggestion.confidence * 100)}% match)`));
        btn.addEventListener('click', () => {
          const match = Array.from(select.options).find((o) => o.value.toLowerCase() === suggestion.category.toLowerCase());
          if (match) { select.value = match.value; slot.replaceChildren(); select.focus(); }
        });
        slot.append(btn);
      } catch (_) {
        slot.replaceChildren(); // suggestions are optional; stay quiet on failure
      }
    }, 450);
  }
  title.addEventListener('input', requestSuggestion);
  note.addEventListener('input', requestSuggestion);
})();
