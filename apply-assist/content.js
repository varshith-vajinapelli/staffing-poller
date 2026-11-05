// Apply Assist: reads the application form on this page, fills the standard
// fields straight from your profile, asks Gemini for the rest, and highlights
// anything unsure. It NEVER clicks Submit.

(async function () {
  if (window.__applyAssistRan) return;          // popup clicked twice
  window.__applyAssistRan = true;

  // ---------- small helpers ----------

  function clean(text) {
    return (text || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  }

  function wait(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  function isVisible(el) {
    if (el.type === 'file') return true;          // file inputs are often hidden on purpose
    var r = el.getBoundingClientRect();
    var style = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  }

  // ---------- reading the form ----------

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
      if (el.disabled || (el.readOnly && type !== 'file')) return;
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

  // ---------- standard answers straight from your profile (no AI) ----------
  // Each rule: label pattern -> profile value. First match wins, so the more
  // specific patterns come first.

  function standardAnswer(field, p) {
    var q = field.label.toLowerCase();
    var kind = field.kind;
    if (kind === 'file') {
      if (/cover/.test(q)) return null;
      if (/resume|cv\b|attach/.test(q)) return 'RESUME';
      return null;
    }
    if (kind === 'textarea' || kind === 'radio' || kind === 'checkbox') return null;
    var rules = [
      [/preferred (first )?name|nickname/, p.preferredName],
      [/first name|given name/, p.firstName],
      [/last name|surname|family name/, p.lastName],
      [/^(full )?name$|full name|legal name/, (p.firstName + ' ' + p.lastName).trim()],
      [/e-?mail/, p.email],
      [/phone|mobile|cell/, kind === 'dropdown-search' || kind === 'select' ? p.country : p.phone],
      [/linkedin/, p.linkedin],
      [/github/, p.github],
      [/portfolio|personal (site|website)|website|links? to (relevant )?work/, p.website || p.github],
      [/zip|postal/, p.zip],
      [/^state$|state\/province|^province/, p.state],
      [/^country$|country of residence|^country\b/, p.country],
      [/location|^city$|current city|city\b/, p.city],
      [/school|university|college|institution/, p.school],
      [/\bgpa\b|grade point/, p.gpa],
      [/graduat|completion date/, p.graduation],
      [/\bmajor\b|field of study|discipline/, p.major],
      [/\bdegree\b/, p.degree]
    ];
    for (var i = 0; i < rules.length; i++) {
      if (rules[i][0].test(q)) return rules[i][1] || null;
    }
    return null;
  }

  // ---------- filling ----------

  function setNativeValue(el, value) {
    // React-based forms ignore plain el.value = x, so use the native setter.
    var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function sameText(a, b) {
    a = clean(a).toLowerCase();
    b = clean(b).toLowerCase();
    return a === b || (a.length > 1 && b.indexOf(a) >= 0) || (b.length > 1 && a.indexOf(b) >= 0);
  }

  // Words that mean "I'd rather not say" in EEO dropdowns.
  var DECLINE = /decline|prefer not|don.?t wish|do not wish|not to (answer|disclose|say)|choose not/;

  // Pick the option that best matches the answer: exact, then contained text,
  // then "decline"-style wording, then the option sharing the most words.
  function bestMatch(value, options) {
    var v = clean(value).toLowerCase();
    if (!v) return -1;
    var i;
    for (i = 0; i < options.length; i++) {
      if (clean(options[i]).toLowerCase() === v) return i;
    }
    for (i = 0; i < options.length; i++) {
      if (clean(options[i]).toLowerCase().indexOf(v) === 0) return i;   // "United States" -> "United States+1"
    }
    for (i = 0; i < options.length; i++) {
      if (sameText(value, options[i])) return i;
    }
    if (DECLINE.test(v)) {
      for (i = 0; i < options.length; i++) {
        if (DECLINE.test(clean(options[i]).toLowerCase())) return i;
      }
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
      // The dropdown's own container (react-select puts the menu inside it).
      menu = el.closest('[class*="container"], [class*="select"], [class*="Select"]');
      if (menu && !menu.querySelector('[role="option"], [class*="option"]')) menu = null;
    }
    if (!menu) return [];
    var opts = menu.querySelectorAll('[role="option"]');
    if (!opts.length) opts = menu.querySelectorAll('[class*="option"]');
    return Array.prototype.slice.call(opts).filter(function (o) { return clean(o.innerText); });
  }

  function openDropdown(el) {
    var control = el.closest('[class*="control"]') || el.parentElement;
    control.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
    el.focus();
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, bubbles: true }));
  }

  function closeDropdown(el) {
    el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
  }

  function clickOption(opt) {
    opt.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    opt.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }));
    opt.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }));
    opt.click();
  }

  // ---------- React dropdowns (via page-helper.js) ----------
  // Greenhouse-style dropdowns ignore fake clicks, so page-helper.js (running
  // in the page's own world) reads their real choices and selects one.

  var nextAaId = 1;
  var nextReqId = 1;

  function callHelper(el, cmd, extra) {
    if (!el.getAttribute('data-aa-id')) el.setAttribute('data-aa-id', String(nextAaId++));
    var req = extra || {};
    req.cmd = cmd;
    req.aaId = el.getAttribute('data-aa-id');
    req.reqId = nextReqId++;
    return new Promise(function (resolve) {
      var timer = setTimeout(function () { done({ ok: false }); }, 8000);
      function onReply(e) {
        var res = JSON.parse(e.detail);
        if (res.reqId === req.reqId) done(res);
      }
      function done(res) {
        clearTimeout(timer);
        document.removeEventListener('applyassist:response', onReply);
        resolve(res);
      }
      document.addEventListener('applyassist:response', onReply);
      document.dispatchEvent(new CustomEvent('applyassist:request', { detail: JSON.stringify(req) }));
    });
  }

  async function pickFromReactDropdown(field, value, isPlace) {
    var el = field.els[0];
    var labels = field.options || [];
    if (!labels.length && field.searchable) {
      // Choices load as you type (e.g. Location): search, then pick.
      var text = isPlace ? String(value).split(',')[0] : String(value);
      var res = await callHelper(el, 'search', { text: text });
      labels = res.ok ? res.labels : [];
    }
    var k = bestMatch(String(value), labels);
    if (k < 0 && isPlace && labels.length) k = 0;     // city search: take the top result
    if (k < 0) return false;
    var picked = await callHelper(el, 'select', { index: k });
    await wait(200);
    return picked.ok;
  }

  async function pickFromDropdown(el, value, isPlace) {
    // 1) open it and look at the choices without typing
    openDropdown(el);
    await wait(300);
    var opts = menuOptions(el);
    var k = bestMatch(value, opts.map(function (o) { return o.innerText; }));

    // 2) type the answer to filter (or to load choices that come from a search)
    if (k < 0) {
      setNativeValue(el, value);
      for (var t = 0; t < 8 && k < 0; t++) {     // wait up to ~2.4 s for search results
        await wait(300);
        opts = menuOptions(el);
        k = bestMatch(value, opts.map(function (o) { return o.innerText; }));
        if (k < 0 && isPlace && opts.length) k = 0;   // city search: take the top result
      }
    }
    if (k < 0) {
      setNativeValue(el, '');
      closeDropdown(el);
      return false;
    }
    clickOption(opts[k]);
    await wait(250);
    return true;
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
        var match = wanted.some(function (w) { return bestMatch(String(w), [field.options[j]]) === 0; });
        if (match && !input.checked) input.click();
        if (match) hit = true;
      });
      return hit;
    }
    if (field.kind === 'dropdown-search') {
      var isPlace = /location|city|address/.test(field.label.toLowerCase());
      if (field.react) return pickFromReactDropdown(field, value, isPlace);
      return pickFromDropdown(el, String(value), isPlace);
    }
    setNativeValue(el, String(value));
    el.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  }

  function mark(el, color) {
    var target = el.type === 'file' || el.type === 'radio' || el.type === 'checkbox'
                 ? (el.closest('fieldset, [role="group"], div') || el) : el;
    if (target.closest('[class*="control"]')) target = target.closest('[class*="control"]');
    target.style.outline = '3px solid ' + color;
    target.style.outlineOffset = '2px';
  }

  // ---------- on-page status box ----------

  var toast = null;
  function show(html) {
    if (!toast) {
      toast = document.createElement('div');
      toast.style.cssText = 'position:fixed;bottom:16px;right:16px;z-index:2147483647;' +
        'background:#202124;color:#fff;padding:12px 16px;border-radius:8px;font:14px Arial;' +
        'max-width:360px;max-height:50vh;overflow:auto;box-shadow:0 4px 12px rgba(0,0,0,.3);line-height:1.4';
      document.body.appendChild(toast);
    }
    toast.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  }

  // ---------- main ----------

  var fields = collectFields();
  // Ignore frames without a real form (ads, chat widgets, etc.).
  var meaningful = fields.filter(function (f) { return f.kind !== 'checkbox' || f.options.length > 1; });
  if (meaningful.length < 3) { window.__applyAssistRan = false; return; }

  // Read the real choices of React dropdowns so the AI can pick exact options.
  for (var d = 0; d < fields.length; d++) {
    if (fields[d].kind !== 'dropdown-search') continue;
    var info = await callHelper(fields[d].els[0], 'options');
    if (!info.ok) continue;
    fields[d].react = true;
    if (info.labels.length) fields[d].options = info.labels;
    else if (info.searchable) fields[d].searchable = true;
  }

  var saved = await chrome.storage.local.get(['profile', 'geminiKey', 'resume']);
  var profile = saved.profile || {};
  var results = {};      // idx -> 'ok' | 'unsure' | 'failed'

  // Step 1: standard fields straight from the profile.
  show('Apply Assist: filling your standard details...');
  for (var s = 0; s < fields.length; s++) {
    var f0 = fields[s];
    if (f0.current) continue;
    var std = standardAnswer(f0, profile);
    if (std && await fillField(f0, std, saved.resume)) results[f0.idx] = 'ok';
  }

  // Step 2: everything else goes to Gemini.
  var rest = fields.filter(function (f) { return !results[f.idx] && !f.current; });
  if (rest.length) {
    show('Apply Assist: writing answers for ' + rest.length + ' questions...');
    var jobText = clean((document.querySelector('main, [class*="description"], #content, article') ||
                         document.body).innerText).slice(0, 6000);
    var fieldList = rest.map(function (f) {
      var item = { idx: f.idx, type: f.kind, question: f.label };
      if (f.options) item.options = f.options.slice(0, 60);
      if (f.required) item.required = true;
      return item;
    });

    var prompt =
      'You fill out a job application for this candidate. Return ONLY JSON: ' +
      '{"answers": [{"idx": <field idx>, "value": <answer>, "sure": true|false}]}\n\n' +
      'CANDIDATE PROFILE (use these exact facts; never invent experience, employers, ' +
      'degrees, numbers or skills he does not have):\n' + JSON.stringify(profile, null, 1) + '\n\n' +
      'RULES:\n' +
      '- One answer per field you can answer. Skip a field only if nothing fits.\n' +
      '- For select/radio fields, "value" must be copied exactly from that field\'s options. ' +
      'For checkbox groups, "value" can be a list of option texts.\n' +
      '- If a question matches one in the profile\'s "savedAnswers", use that answer.\n' +
      '- "dropdown-search" fields WITH options: copy one option exactly. ' +
      'WITHOUT options, answer with the short standard wording ' +
      'such a choice would use ("Yes", "No", "United States", "Decline to self identify", ' +
      '"I am not a protected veteran", "I don\'t wish to answer").\n' +
      '- For a resume/CV file upload, value "RESUME". For any other file upload ' +
      '(cover letter, transcript, portfolio), skip it.\n' +
      '- Work authorization and sponsorship questions: answer truthfully from the profile\'s ' +
      '"workAuth". Never claim citizenship or a green card unless workAuth says so.\n' +
      '- Veteran status: he is not a veteran ("I am not a protected veteran"). Gender, race, ' +
      'ethnicity, disability and other voluntary questions: the decline / prefer not to say option.\n' +
      '- Yes/no questions about non-competes, prior employment at this company, relatives ' +
      'working there, or being a current contractor: "No".\n' +
      '- Salary: use the profile\'s desired pay if given, else "Open to discussion"; always sure=false.\n' +
      '- Open questions (why this company, tell us about yourself, AI usage, etc.): write in first ' +
      'person in his voice, described in the profile. 60-150 words, specific to THIS job and ' +
      'company from the job text, honest, no cliches, no forced project names.\n' +
      '- "sure": false for anything that is a guess, ambiguous, or that he should double-check ' +
      '(salary, start date, legal questions, anything not covered by the profile).\n\n' +
      'PAGE TITLE: ' + document.title + '\n' +
      'JOB / PAGE TEXT:\n' + jobText + '\n\n' +
      'FORM FIELDS:\n' + JSON.stringify(fieldList);

    var reply = await chrome.runtime.sendMessage({ type: 'gemini', key: saved.geminiKey, prompt: prompt });
    if (!reply || !reply.ok) {
      show('Apply Assist: your details are filled, but the AI request failed (' +
           escapeHtml(reply ? reply.error : 'no reply') + '). Click Fill again in a minute.');
      window.__applyAssistRan = false;
      return;
    }
    var answers = {};
    (reply.result.answers || []).forEach(function (a) { answers[a.idx] = a; });

    for (var i = 0; i < rest.length; i++) {
      var f = rest[i];
      var a = answers[f.idx];
      var ok = false;
      try { ok = a ? await fillField(f, a.value, saved.resume) : false; } catch (e) { ok = false; }
      if (ok) results[f.idx] = a.sure === false ? 'unsure' : 'ok';
    }
  }

  // Step 3: highlight and report.
  var filled = 0, unsureLabels = [], missingLabels = [];
  fields.forEach(function (f) {
    var r = results[f.idx];
    if (r) filled += 1;
    if (r === 'unsure') { unsureLabels.push(f.label); mark(f.els[0], '#f5c400'); }
    if (!r && !f.current && f.required) { missingLabels.push(f.label); mark(f.els[0], '#ff5f57'); }
  });

  function list(labels) {
    return '<ul style="margin:4px 0 6px 18px;padding:0">' + labels.map(function (l) {
      return '<li>' + escapeHtml(l.slice(0, 70)) + '</li>';
    }).join('') + '</ul>';
  }
  show('<b>Apply Assist: filled ' + filled + ' fields.</b><br>' +
       (unsureLabels.length ? '<span style="color:#f5c400">■</span> Double-check (yellow):' + list(unsureLabels) : '') +
       (missingLabels.length ? '<span style="color:#ff5f57">■</span> Fill yourself (red):' + list(missingLabels) : '') +
       'Review everything, then click Submit yourself.');
  window.__applyAssistRan = false;
})();
