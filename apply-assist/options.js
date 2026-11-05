// Settings page: saves the profile, resume and Gemini key in chrome.storage.local.

var FIELDS = ['firstName', 'lastName', 'preferredName', 'email', 'phone', 'city', 'state',
  'zip', 'country', 'linkedin', 'github', 'website', 'school', 'degree', 'major',
  'graduation', 'gpa', 'relocate', 'startDate', 'howHeard', 'salary', 'workAuth',
  'savedAnswers',
  'about', 'voice'];

// Starting values; edit them on the page.
var DEFAULTS = {
  firstName: 'Varshith', lastName: 'Vajinapelli', preferredName: 'Sai',
  email: 'varshith.vajinapelli@gmail.com',
  city: 'Minneapolis', state: 'Minnesota', country: 'United States',
  linkedin: 'https://www.linkedin.com/in/varshith-vajinapelli',
  github: 'https://github.com/varshith-vajinapelli',
  school: 'University of Minnesota, Twin Cities', degree: "Bachelor's",
  major: 'Computer Science', graduation: 'December 2026',
  relocate: 'Yes, anywhere in the US', startDate: 'January 2027',
  howHeard: 'Online job board',
  savedAnswers:
    'Willing to relocate / currently local? -> Not local, but willing to relocate anywhere in the US.\n' +
    'Non-compete or contract restrictions? -> No\n' +
    'Ever worked for or contracted with this company? -> No\n' +
    'Referred by an employee? -> (leave blank)\n' +
    'Relatives working here? -> No\n' +
    'At least 18 years old? -> Yes\n' +
    'Can you pass a background check / drug test? -> Yes\n' +
    'Willing to work onsite / hybrid? -> Yes\n' +
    'Veteran status -> I am not a protected veteran',
  workAuth: 'Authorized to work in the US now on F-1 OPT (STEM OPT eligible, up to 3 years ' +
            'with no sponsorship needed to start). Will require H-1B sponsorship in the future. ' +
            'Not a US citizen or permanent resident.',
  about: 'Skills: C++, C, Python, JavaScript, SQL, Linux, Git, GitHub Actions. No Java.\n' +
         'Undergraduate TA for CSCI 3081W (C++ program design) and CSCI 1133 (intro Python); ' +
         'wrote two of the course labs.\n' +
         'Undergraduate research: StreamPeer, a C++ peer-to-peer audio/video streaming system.\n' +
         'GopherEvents: shipped full-stack web app.\n' +
         'Built a job-alert system: Python pollers on GitHub Actions that pull job APIs and use ' +
         'an LLM to screen postings, plus Google Apps Script automations.\n' +
         'Open-source contributions to Wikimedia.\n' +
         'Interests: backend and systems engineering. Motivated by new challenges.',
  voice: 'Warm, natural and a little poetic, but concise and concrete. Sounds like a real ' +
         'person, not a template.'
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
