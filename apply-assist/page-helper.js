// Runs in the page's own JavaScript world (not the extension's), so it can
// reach React dropdowns (react-select) that ignore fake clicks. Apply Assist's
// content.js talks to it through DOM events.

(function () {
  if (window.__applyAssistHelper) return;
  window.__applyAssistHelper = true;

  function selectFor(el) {
    var key = Object.keys(el).find(function (k) { return k.indexOf('__reactFiber') === 0; });
    var f = key ? el[key] : null;
    for (var d = 0; f && d < 40; d++) {
      if (f.stateNode && f.stateNode.selectOption && f.stateNode.props) return f.stateNode;
      f = f.return;
    }
    return null;
  }

  function labels(s) {
    var opts = s.props.options || [];
    var out = [];
    opts.forEach(function (o) {
      if (o && o.options) {  // grouped options
        o.options.forEach(function (x) { out.push({ label: s.getOptionLabel(x), opt: x }); });
      } else {
        out.push({ label: s.getOptionLabel(o), opt: o });
      }
    });
    return out;
  }

  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function handle(req) {
    var el = document.querySelector('[data-aa-id="' + req.aaId + '"]');
    if (!el) return { ok: false };
    var s = selectFor(el);
    if (!s) return { ok: false };

    if (req.cmd === 'options') {
      return { ok: true, labels: labels(s).map(function (x) { return x.label; }),
               searchable: typeof s.props.onInputChange === 'function' };
    }
    if (req.cmd === 'search') {
      // Dropdowns that load choices as you type (e.g. Location).
      s.props.onInputChange(req.text, { action: 'input-change', prevInputValue: '' });
      for (var i = 0; i < 12; i++) {
        await wait(500);
        s = selectFor(el);
        if (s && (s.props.options || []).length) break;
      }
      return { ok: true, labels: s ? labels(s).map(function (x) { return x.label; }) : [] };
    }
    if (req.cmd === 'select') {
      var all = labels(s);
      var pick = all[req.index];
      if (!pick) return { ok: false };
      s.selectOption(pick.opt);
      return { ok: true };
    }
    return { ok: false };
  }

  document.addEventListener('applyassist:request', async function (e) {
    var req = JSON.parse(e.detail);
    var res;
    try { res = await handle(req); } catch (err) { res = { ok: false, error: String(err) }; }
    res.reqId = req.reqId;
    document.dispatchEvent(new CustomEvent('applyassist:response', { detail: JSON.stringify(res) }));
  });
})();
