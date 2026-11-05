/**
 * Sai's Google automations (Google Apps Script, runs on Google's servers).
 *
 *  syncTracker()  - twice a day: reads job emails in Gmail and updates the
 *                   Job_Application_Tracker sheet (new applications, rejections,
 *                   assessments, interviews). Silent: sends no email.
 *  dailyLeetCode() - every morning: emails the next NeetCode problem from the
 *                   "NeetCode Daily Plan" sheet and marks it sent.
 *
 * One-time setup:
 *  1. Project Settings -> Script Properties -> add GEMINI_API_KEY.
 *  2. Run setup() once and approve the permissions it asks for.
 */

var TRACKER_ID = '1F21twADpyLkofSAay9FOf_wLw1cVsClfnT3CLYXjflw';
var TRACKER_TAB = 'Applications';
var NEETCODE_ID = '1UBv4h2Fuij5zo6NCKYh7oBYSTpcMMSBll9tYX_En0_E';
var NEETCODE_TAB = 'Plan';
var GEMINI_MODELS = ['gemini-3.8-flash', 'gemini-3.5-flash-lite'];


// ---------- setup ----------

function setup() {
  // Remove old triggers so running setup() twice never doubles anything.
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });

  ScriptApp.newTrigger('syncTracker').timeBased().everyDays(1).atHour(13).nearMinute(43)
      .inTimezone('America/Chicago').create();
  ScriptApp.newTrigger('syncTracker').timeBased().everyDays(1).atHour(20).nearMinute(43)
      .inTimezone('America/Chicago').create();
  ScriptApp.newTrigger('dailyLeetCode').timeBased().everyDays(1).atHour(7).nearMinute(37)
      .inTimezone('America/Chicago').create();

  Logger.log('Triggers created: tracker 1:43 PM + 8:43 PM, LeetCode 7:37 AM (Central).');
}


// ---------- Gemini ----------

function askGemini(prompt) {
  var key = PropertiesService.getScriptProperties().getProperty('GEMINI_API_KEY');
  if (!key) throw new Error('GEMINI_API_KEY is not set in Script Properties');
  var body = {
    contents: [{ parts: [{ text: prompt }] }],
    generationConfig: { responseMimeType: 'application/json', temperature: 0 }
  };
  // Gemini is sometimes briefly overloaded, so retry a few times.
  for (var attempt = 0; attempt < 3; attempt++) {
    for (var i = 0; i < GEMINI_MODELS.length; i++) {
      var url = 'https://generativelanguage.googleapis.com/v1beta/models/' +
                GEMINI_MODELS[i] + ':generateContent?key=' + key;
      var resp = UrlFetchApp.fetch(url, {
        method: 'post', contentType: 'application/json',
        payload: JSON.stringify(body), muteHttpExceptions: true
      });
      if (resp.getResponseCode() === 200) {
        var data = JSON.parse(resp.getContentText());
        return JSON.parse(data.candidates[0].content.parts[0].text);
      }
      Logger.log('Gemini ' + GEMINI_MODELS[i] + ' failed: HTTP ' + resp.getResponseCode());
    }
    Utilities.sleep(20000);
  }
  throw new Error('All Gemini models failed');
}


// ---------- tracker sync ----------

function prettyDate(d) {
  return Utilities.formatDate(d, 'America/Chicago', 'MMM d, yyyy');   // "Nov 4, 2026"
}

function shortDate(d) {
  return Utilities.formatDate(d, 'America/Chicago', 'M/d');           // "11/4"
}

function syncTracker() {
  var sheet = SpreadsheetApp.openById(TRACKER_ID).getSheetByName(TRACKER_TAB);
  var rows = sheet.getDataRange().getValues();     // rows[0] = headers

  // Messages already handled in earlier runs (so nothing is added twice).
  var props = PropertiesService.getScriptProperties();
  var done = JSON.parse(props.getProperty('DONE_MESSAGE_IDS') || '[]');

  var query = 'newer_than:2d -from:me -from:hiring.cafe -subject:"staffing-firm jobs" ' +
      '-subject:"company jobs" -subject:LeetCode (application OR applied OR applying ' +
      'OR candidacy OR interview OR assessment OR unfortunately OR "next steps" OR ' +
      'recruiter OR position OR offer)';
  var emails = [];
  var newIds = [];
  GmailApp.search(query, 0, 50).forEach(function (thread) {
    thread.getMessages().forEach(function (m) {
      var id = m.getId();
      if (done.indexOf(id) >= 0) return;
      if (m.getDate() < new Date(Date.now() - 2 * 86400000)) return;
      newIds.push(id);
      emails.push({
        id: id,
        from: m.getFrom(),
        date: prettyDate(m.getDate()),
        subject: m.getSubject(),
        body: m.getPlainBody().replace(/\s+/g, ' ').slice(0, 1500)
      });
    });
  });
  if (emails.length === 0) {
    Logger.log('No new job emails.');
    return;
  }

  // Values the dropdown columns already use (D, E, F, H, K, L).
  var allowed = {};
  [3, 4, 5, 7, 10, 11].forEach(function (c) {
    var seen = {};
    for (var r = 1; r < rows.length; r++) if (rows[r][c] !== '') seen[rows[r][c]] = true;
    allowed[c] = Object.keys(seen);
  });

  var existing = [];
  var maxNum = 0;
  for (var r = 1; r < rows.length; r++) {
    existing.push((r + 1) + ' | ' + rows[r][2] + ' | ' + rows[r][6] + ' | ' +
                  rows[r][9] + ' | ' + rows[r][11]);
    var n = parseInt(rows[r][0], 10);
    if (n > maxNum) maxNum = n;
  }

  var prompt =
    'You update a job application tracker from emails. Return ONLY JSON:\n' +
    '{"add": [{"company","role","roleType","location","jobId","portal","companyType",' +
    '"realisticChance","dateApplied","link"}], ' +
    '"update": [{"row": <sheet row number>, "status", "note", "contact", "nextAction", "followUp"}]}\n\n' +
    'Rules:\n' +
    '- "add" ONLY for an application confirmation ("thanks for applying", "we received ' +
    'your application") whose company + role is NOT already in the tracker.\n' +
    '- "update" for news about an application already in the tracker (rejection, ' +
    'assessment, interview invite, recruiter reply). Use its row number.\n' +
    '- Ignore newsletters, job alerts, marketing and anything not about his applications.\n' +
    '- roleType must be one of: ' + allowed[7].join(', ') + '\n' +
    '- status must be one of: ' + allowed[11].join(', ') + '\n' +
    '- companyType must be one of: ' + allowed[3].join(', ') + '\n' +
    '- realisticChance must be one of: ' + allowed[5].join(', ') +
    ' (High = good fit for a new grad, Low = reach)\n' +
    '- portal is the applicant system if visible (e.g. ' + allowed[10].slice(0, 12).join(', ') + ').\n' +
    '- note: under 12 words. dateApplied like "Nov 4, 2026". Leave unknown fields "".\n\n' +
    'TRACKER (row | company | role | job id | status):\n' + existing.join('\n') +
    '\n\nEMAILS:\n' + JSON.stringify(emails);

  var result = askGemini(prompt);
  var today = new Date();
  var added = 0, updated = 0;

  (result.add || []).forEach(function (a) {
    if (!a.company || !a.role) return;
    maxNum += 1;
    var pick = function (col, value) { return allowed[col].indexOf(value) >= 0 ? value : ''; };
    sheet.appendRow([
      maxNum,
      a.dateApplied || prettyDate(today),
      a.company,
      pick(3, a.companyType),
      'Skip',
      pick(5, a.realisticChance),
      a.role,
      pick(7, a.roleType) || 'Full-time',
      a.location || '',
      a.jobId || '',
      a.portal || '',
      'Applied',
      'No', '', '', '',
      'Gmail', '',
      a.link || ''
    ]);
    added += 1;
  });

  (result.update || []).forEach(function (u) {
    var row = parseInt(u.row, 10);
    if (!(row >= 2 && row <= rows.length)) return;
    if (u.status && allowed[11].indexOf(u.status) >= 0) sheet.getRange(row, 12).setValue(u.status);
    if (u.note) {
      var notes = String(sheet.getRange(row, 18).getValue() || '');
      var line = shortDate(today) + ': ' + u.note;
      sheet.getRange(row, 18).setValue(notes ? notes + ' ' + line : line);
    }
    if (u.contact) sheet.getRange(row, 14).setValue(u.contact);
    if (u.nextAction) sheet.getRange(row, 15).setValue(u.nextAction);
    if (u.followUp) sheet.getRange(row, 16).setValue(u.followUp);
    updated += 1;
  });

  // Remember handled messages (keep the newest 1000).
  props.setProperty('DONE_MESSAGE_IDS', JSON.stringify(done.concat(newIds).slice(-1000)));
  Logger.log('Added ' + added + ', updated ' + updated + '.');
}


// ---------- daily LeetCode ----------

function dailyLeetCode() {
  var sheet = SpreadsheetApp.openById(NEETCODE_ID).getSheetByName(NEETCODE_TAB);
  var rows = sheet.getDataRange().getValues();   // A Order, B Type, C Topic, D Problem, E Difficulty, F Link, G Sent On
  var newRow = -1, reviewRow = -1, sentNew = 0, totalNew = 0;

  for (var r = 1; r < rows.length; r++) {
    var type = rows[r][1], sent = rows[r][6];
    if (type === 'New') {
      totalNew += 1;
      if (sent !== '') sentNew += 1;
      else if (newRow < 0) newRow = r;
    }
    if (type === 'Review' && sent === '' && reviewRow < 0) reviewRow = r;
  }

  var me = Session.getEffectiveUser().getEmail();
  if (newRow < 0) {
    GmailApp.sendEmail(me, 'LeetCode: plan finished',
        'You finished all ' + totalNew + ' problems in your plan. Ask Claude for the next set.');
    return;
  }
  var withReview = sentNew > 0 && sentNew % 3 === 0 && reviewRow >= 0;

  var p = rows[newRow];
  var tip = { pattern: '', hint: '' };
  try {
    tip = askGemini('For the LeetCode problem "' + p[3] + '" (' + p[2] + ' topic), return ONLY ' +
      'JSON {"pattern": "<one line: the technique it trains>", "hint": "<one short nudge ' +
      'that does not give away the solution>"}');
  } catch (e) {
    Logger.log('No hint today: ' + e);
  }

  var e = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); };
  var html = '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">' +
    '<p><b><a href="' + p[5] + '">' + e(p[3]) + '</a></b> (' + p[4] + ')<br>' +
    '<span style="color:#555">' + e(p[2]) + ' · ' + (sentNew + 1) + ' of ' + totalNew + '</span></p>';
  if (tip.pattern) html += '<p><b>Pattern:</b> ' + e(tip.pattern) + '</p>';
  if (tip.hint) html += '<p style="color:#888"><b>Hint (only if stuck):</b> ' + e(tip.hint) + '</p>';
  if (withReview) {
    var rv = rows[reviewRow];
    html += '<p><b>Review re-solve (' + e(rv[2]) + '):</b> <a href="' + rv[5] + '">' +
            e(rv[3]) + '</a>, try it in under 20 minutes.</p>';
  }
  html += '</div>';

  GmailApp.sendEmail(me, 'LeetCode: ' + p[3] + ' (' + p[2] + ' ' + (sentNew + 1) + '/' + totalNew + ')',
      p[3] + ' ' + p[5], { htmlBody: html });

  var today = prettyDate(new Date());
  sheet.getRange(newRow + 1, 7).setValue(today);
  if (withReview) sheet.getRange(reviewRow + 1, 7).setValue(today);
}
