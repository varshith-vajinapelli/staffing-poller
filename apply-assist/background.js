// Talks to Gemini for the page (content scripts can't call it directly).

var MODELS = ['gemini-3.8-flash', 'gemini-3.5-flash-lite'];

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

async function askGemini(key, prompt) {
  var body = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { responseMimeType: 'application/json', temperature: 0.4 }
  };
  var lastError = '';
  for (var attempt = 0; attempt < 3; attempt++) {
    for (var i = 0; i < MODELS.length; i++) {
      var url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
                MODELS[i] + ':generateContent?key=' + key;
      try {
        var resp = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if (resp.ok) {
          var data = await resp.json();
          return JSON.parse(data.candidates[0].content.parts[0].text);
        }
        lastError = MODELS[i] + ' HTTP ' + resp.status;
      } catch (e) {
        lastError = MODELS[i] + ' ' + e;
      }
    }
    await sleep(5000);
  }
  throw new Error('Gemini failed: ' + lastError);
}

chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
  if (msg.type !== 'gemini') return false;
  askGemini(msg.key, msg.prompt)
    .then(function (result) { sendResponse({ ok: true, result: result }); })
    .catch(function (e) { sendResponse({ ok: false, error: String(e) }); });
  return true;  // keep the channel open for the async reply
});
