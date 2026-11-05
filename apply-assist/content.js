// Apply Assist: reads the application form on this page, asks Gemini what
// to put in each field, fills it in, and highlights anything unsure.
// It NEVER clicks Submit.

(async function () {
  if (window.__applyAssistRan) return;          // popup clicked twice
  window.__applyAssistRan = true;

  // ---------- reading the form ----------

  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  }

  function isVisible(el) {
    if (el.type === 'file') return true;          // file inputs are often hidden on purpose
    var r = el.getBoundingClientRect();
    var style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  function labelFor(el) {
    if (el.id) {
      var lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab && clean(lab.innerText)) return clean(lab.innerText);
    }
    if (el.getAttribute('aria-labelledby')) {
      var ids = el.getAttribute('aria-labelledby').split(' ');
      var text = ids.map(function (id) {
        var n = document.getElementById(id);
        return n ? n.innerText : '';
      }).join(' ');
      if (clean(text)) return clean(text);
    }
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    var wrap = el.closest('label');
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);
    var fieldset = el.closest('fieldset');
    if (fieldset) {
      var legend = fieldset.querySelector('legend');
      if (legend && clean(legend.innerText)) return clean(legend.innerText);
    }
    // Fall back to the nearest container that has some text in it.
    var box = el.parentElement;
    for (var i = 0; i < 4 && box; i++) {
      var t = clean(box.innerText);
      if (t.length > 2) return t.slice(0, 200);
      box = box.parentElement;
    }
    return clean(el.name || el.placeholder || '');
  }

  function optionLabel(input) {
    if (input.id) {
      var lab = document.querySelector('label[for="' + CSS.escape(input.id) + '"]');
      if (lab) return clean(lab.innerText);
    }
    var wrap = input.closest('label');
    if (wrap) return clean(wrap.innerText);
    return clean(input.value);
  }

  function questionForGroup(input) {
    var fieldset = input.closest('fieldset');
    if (fieldset) {
      var legend = fieldset.querySelector('legend');
      if (legend) return clean(legend.innerText);
    }
    var box = input.closest('[role="radiogroup"], [role="group"]') || input.parentElement.parentElement;
    return clean(box ? box.innerText : input.name).slice(0, 200);
  }

  function collectFields() {
    var fields = [];
    var groups = {};
    var els = document.querySelectorAll('input, textarea, select');
    els.forEach(function (el) {
      var type = (el.type || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].indexOf(type) >= 0) return;
      if (el.disabled || el.readOnly && type !== 'file') return;
      if (!isVisible(el)) return;
      // Hidden helper boxes some sites add next to dropdowns (not real questions).
      if (type !== 'file' && (el.getAttribute('aria-hidden') === 'true' ||
                              el.getAttribute('tabindex') === '-1')) return;

      if (type === 'radio' || type === 'checkbox') {
        var name = el.name || el.id;
        if (!groups[name]) {
          groups[name] = { kind: type, label: questionForGroup(el), options: [], els: [] };
          fields.push(groups[name]);
        }
        groups[name].options.push(optionLabel(el));
        groups[name].els.push(el);
        return;
      }
      var field = { kind: type || el.tagName.toLowerCase(), label: labelFor(el), els: [el],
                    required: el.required || el.getAttribute('aria-required') === 'true' };
      if (el.tagName === 'SELECT') {
        field.kind = 'select';
        field.options = Array.prototype.map.call(el.options, function (o) { return clean(o.text); })
                             .filter(function (t) { return t; });
      } else if (el.tagName === 'TEXTAREA') {
        field.kind = 'textarea';
      } else if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete')) {
        field.kind = 'dropdown-search';
      }
      if (el.value && field.kind !== 'select' && type !== 'file') field.current = el.value;
      fields.push(field);
    });
    fields.forEach(function (f, i) { f.idx = i; });
    return fields;
  }

  // ---------- filling ----------

  function setNativeValue(el, value) {
    // React-based forms ignore plain el.value = x, so use the native setter.
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
  }

  function sameText(a, b) {
    a = clean(a).toLowerCase();
    b = clean(b).toLowerCase();
    return a === b || (a.length > 1 && b.indexOf(a) >= 0) || (b.length > 1 && a.indexOf(b) >= 0);
  }

  // Pick the option that best matches the AI's answer: exact or contained text
  // first, otherwise the option sharing the most words.
  function bestMatch(value, options) {
    var v = clean(value).toLowerCase();
    if (!v) return -1;
    for (var i = 0; i < options.length; i++) {
      if (clean(options[i]).toLowerCase() === v) return i;
    }
    for (var j = 0; j < options.length; j++) {
      if (sameText(value, options[j])) return j;
    }
    var words = v.split(/\W+/).filter(function (w) { return w.length > 2; });
    var best = -1, bestScore = 0;
    options.forEach(function (opt, k) {
      var o = clean(opt).toLowerCase();
      var score = words.filter(function (w) { return o.indexOf(w) >= 0; }).length;
      if (score > bestScore) { best = k; bestScore = score; }
    });
    return bestScore >= Math.max(1, Math.ceil(words.length / 2)) ? best : -1;
  }

  function menuOptions(el) {
    var id = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    var menu = id ? document.getElementById(id) : null;
    if (!menu) {
      var box = el.parentElement;
      for (var i = 0; i < 6 && box && !menu; i++) {
        if (box.querySelector('[role="listbox"], [role="option"]')) menu = box;
        box = box.parentElement;
      }
    }
    return menu ? Array.prototype.slice.call(menu.querySelectorAll('[role="option"]')) : [];
  }

  function wait(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function dataUrlToFile(dataUrl, name) {
    var parts = dataUrl.split(',');
    var mime = parts[0].match(/:(.*?);/)[1];
    var bytes = atob(parts[1]);
    var arr = new Uint8Array(bytes.length);
    for (var i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
    return new File([arr], name, { type: mime });
  }

  async function fillField(field, value, resume) {
    var el = field.els[0];
    if (value === null || value === undefined || value === '') return false;

    if (field.kind === 'file') {
      if (value !== 'RESUME' || !resume) return false;
      var dt = new DataTransfer();
      dt.items.add(dataUrlToFile(resume.dataUrl, resume.name));
      el.files = dt.files;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    if (field.kind === 'select') {
      var texts = Array.prototype.map.call(el.options, function (o) { return o.text; });
      var i = bestMatch(String(value), texts);
      if (i < 0) return false;
      el.selectedIndex = i;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    if (field.kind === 'radio' || field.kind === 'checkbox') {
      var wanted = Array.isArray(value) ? value : [value];
      var hit = false;
      field.els.forEach(function (input, j) {
        var match = wanted.some(function (w) { return sameText(String(w), field.options[j]); });
        if (match && !input.checked) { input.click(); hit = true; }
        if (match) hit = true;
      });
      return hit;
    }
    if (field.kind === 'dropdown-search') {
      // Searchable dropdowns: open it, type the answer, click the best option.
      var control = el.closest('[class*="control"]') || el.parentElement;
      control.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
      el.focus();
      await wait(300);
      var opts = menuOptions(el);
      if (!opts.length) {
        // Some dropdowns only show choices after typing.
        var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        setter.call(el, String(value).split(/[\s,(]/)[0]);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        await wait(600);
        opts = menuOptions(el);
      }
      var k = bestMatch(String(value), opts.map(function (o) { return o.innerText; }));
      if (k < 0) {
        el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
        return false;
      }
      opts[k].dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
      opts[k].click();
      await wait(200);
      return true;
    }
    setNativeValue(el, String(value));
    return true;
  }

  function mark(el, color) {
    var target = el.type === 'file' || el.type === 'radio' || el.type === 'checkbox'
                 ? (el.closest('fieldset, [role="group"], div') || el) : el;
    target.style.outline = '3px solid ' + color;
    target.style.outlineOffset = '2px';
  }

  // ---------- on-page status box ----------

  var toast = null;
  function show(text) {
    if (!toast) {
      toast = document.createElement('div');
      toast.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;' +
        'background:#202124;color:#fff;padding:12px 16px;border-radius:8px;font:14px Arial;' +
        'max-width:340px;box-shadow:0 4px 12px rgba(0,0,0,.3);line-height:1.4';
      document.body.appendChild(toast);
    }
    toast.innerHTML = text;
  }

  // ---------- main ----------

  var fields = collectFields();
  // Ignore frames without a real form (ads, chat widgets, etc.).
  var meaningful = fields.filter(function (f) { return f.kind !== 'checkbox' || f.options.length > 1; });
  if (meaningful.length < 3) return;

  var saved = await chrome.storage.local.get(['profile', 'geminiKey', 'resume']);
  var profile = saved.profile || {};

  var jobText = clean((document.querySelector('main, [class*="description"], #content, article') ||
                       document.body).innerText).slice(0, 6000);

  var fieldList = fields.map(function (f) {
    var item = { idx: f.idx, type: f.kind, question: f.label };
    if (f.options) item.options = f.options.slice(0, 60);
    if (f.required) item.required = true;
    if (f.current) item.alreadyFilled = f.current;
    return item;
  });

  show('Apply Assist: reading ' + fields.length + ' fields and writing answers...');

  var prompt =
    'You fill out a job application for this candidate. Return ONLY JSON: ' +
    '{"answers": [{"idx": <field idx>, "value": <answer>, "sure": true|false}]}\n\n' +
    'CANDIDATE PROFILE (use these exact facts; never invent experience, employers, ' +
    'degrees, numbers or skills he does not have):\n' + JSON.stringify(profile, null, 1) + '\n\n' +
    'RULES:\n' +
    '- One answer per field you can answer. Skip a field only if nothing fits.\n' +
    '- For select/radio fields, "value" must be copied exactly from that field\'s options. ' +
    'For checkbox groups, "value" can be a list of option texts.\n' +
    '- "dropdown-search" fields hide their choices: answer with the short standard wording ' +
    'such a choice would use ("Yes", "No", "United States", "Decline to self identify", ' +
    '"I am not a protected veteran", "I don\'t wish to answer").\n' +
    '- For a resume/CV file upload, value "RESUME". For any other file upload ' +
    '(cover letter, transcript, portfolio), skip it.\n' +
    '- Work authorization: he IS authorized to work in the US now (F-1 OPT, STEM-eligible). ' +
    'He WILL need sponsorship in the future. Answer every sponsorship question truthfully ' +
    'with that. Never claim citizenship or a green card.\n' +
    '- Gender, race, ethnicity, veteran, disability and similar voluntary questions: pick ' +
    'the "decline / prefer not to say / I don\'t wish to answer" option.\n' +
    '- Open questions (why this company, tell us about yourself, etc.): write in first ' +
    'person in his voice, described in the profile. 60-150 words, specific to THIS job and ' +
    'company from the job text, honest, no cliches, no forced project names.\n' +
    '- Leave fields that already have a value ("alreadyFilled") unless it is clearly wrong.\n' +
    '- "sure": false for anything that is a guess, ambiguous, or that he should double-check ' +
    '(salary expectations, start date, legal questions, anything not covered by the profile).\n\n' +
    'PAGE TITLE: ' + document.title + '\n' +
    'JOB / PAGE TEXT:\n' + jobText + '\n\n' +
    'FORM FIELDS:\n' + JSON.stringify(fieldList);

  var reply = await chrome.runtime.sendMessage({ type: 'gemini', key: saved.geminiKey, prompt: prompt });
  if (!reply || !reply.ok) {
    show('Apply Assist: AI request failed (' + (reply ? reply.error : 'no reply') +
         '). Try again in a minute.');
    window.__applyAssistRan = false;
    return;
  }

  var answers = {};
  (reply.result.answers || []).forEach(function (a) { answers[a.idx] = a; });

  var filled = 0, unsure = 0, missing = 0;
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    var a = answers[f.idx];
    var ok = false;
    try {
      ok = a ? await fillField(f, a.value, saved.resume) : false;
    } catch (e) {
      ok = false;
    }
    if (ok) {
      filled += 1;
      if (a.sure === false) { unsure += 1; mark(f.els[0], '#f5c400'); }
    } else if (f.required) {
      missing += 1;
      mark(f.els[0], '#ff5f57');
    }
  }

  show('<b>Apply Assist: filled ' + filled + ' fields.</b><br>' +
       (unsure ? '<span style="color:#f5c400">■</span> ' + unsure + ' to double-check (yellow)<br>' : '') +
       (missing ? '<span style="color:#ff5f57">■</span> ' + missing + ' required left empty (red)<br>' : '') +
       'Review everything, then click Submit yourself.');
  window.__applyAssistRan = false;
})();
