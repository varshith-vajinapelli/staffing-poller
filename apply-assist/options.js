// Settings page: saves the profile, resume and Gemini key in chrome.storage.local.

var FIELDS = ['firstName', 'lastName', 'preferredName', 'email', 'phone', 'city', 'state',
  'zip', 'country', 'linkedin', 'github', 'website', 'school', 'degree', 'major',
  'graduation', 'gpa', 'relocate', 'startDate', 'howHeard', 'salary', 'workAuth',
  'savedAnswers',
  'about', 'voice'];

// Starting values (blank on purpose - fill yours in on the Settings page;
// what you save there is kept in your browser, not in this repo).
var DEFAULTS = {
  firstName: '', lastName: '', preferredName: '',
  email: '',
  city: '', state: '', country: 'United States',
  linkedin: '',
  github: '',
  school: '', degree: '',
  major: '', graduation: '',
  relocate: '', startDate: '',
  howHeard: 'Online job board',
  savedAnswers:
    'Non-compete or contract restrictions? -> No\n' +
    'Ever worked for or contracted with this company? -> No\n' +
    'Referred by an employee? -> (leave blank)\n' +
    'Relatives working here? -> No\n' +
    'At least 18 years old? -> Yes\n' +
    'Veteran status -> I am not a protected veteran',
  workAuth: '',
  about: '',
  voice: 'Warm, natural and concise. Sounds like a real person, not a template.'
};

async function load() {
  var saved = await chrome.storage.local.get(['profile', 'geminiKey', 'resume']);
  var profile = saved.profile || DEFAULTS;
  FIELDS.forEach(function (id) {
    document.getElementById(id).value = profile[id] || DEFAULTS[id] || '';
  });
  document.getElementById('geminiKey').value = saved.geminiKey || '';
  if (saved.resume) document.getElementById('resumeName').textContent = '(saved: ' + saved.resume.name + ')';
}

function readFile(file) {
  return new Promise(function (resolve, reject) {
    var reader = new FileReader();
    reader.onload = function () { resolve(reader.result); };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

document.getElementById('save').addEventListener('click', async function () {
  var profile = {};
  FIELDS.forEach(function (id) { profile[id] = document.getElementById(id).value.trim(); });
  var data = { profile: profile, geminiKey: document.getElementById('geminiKey').value.trim() };
  var file = document.getElementById('resume').files[0];
  if (file) data.resume = { name: file.name, dataUrl: await readFile(file) };
  await chrome.storage.local.set(data);
  document.getElementById('saved').textContent = 'Saved.';
  if (file) document.getElementById('resumeName').textContent = '(saved: ' + file.name + ')';
  setTimeout(function () { document.getElementById('saved').textContent = ''; }, 2500);
});

load();
