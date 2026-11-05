// The toolbar popup: one button that runs the filler on the current tab.

var statusBox = document.getElementById('status');
var button = document.getElementById('fill');

document.getElementById('settings').addEventListener('click', function (e) {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

button.addEventListener('click', async function () {
  var saved = await chrome.storage.local.get(['profile', 'geminiKey']);
  if (!saved.geminiKey || !saved.profile || !saved.profile.firstName) {
    statusBox.textContent = 'Open Settings first: add your profile and Gemini key.';
    return;
  }
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  button.disabled = true;
  statusBox.textContent = 'Filling... watch the page.';
  try {
    // Run in every frame: many job sites put the form inside an iframe.
    await chrome.scripting.executeScript({
      target: { tabId: tabs[0].id, allFrames: true },
      files: ['content.js']
    });
    statusBox.textContent = 'Started. Progress shows on the page.';
  } catch (e) {
    statusBox.textContent = 'Could not run on this page: ' + e.message;
  }
  button.disabled = false;
});
