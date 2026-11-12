/**
 * Storage for the card game rooms: one row per room in a sheet called "rooms".
 *   column A = room code, column B = last saved time, columns C... = the room as JSON text
 *   (cut into pieces because one cell holds at most 50,000 characters).
 *
 * How to use: Google Sheet > Extensions > Apps Script > paste this file, set TOKEN below to a
 * long random text of your own, then Deploy > New deployment > Web app:
 *   Execute as: Me      Who has access: Anyone
 * Copy the web app URL (ends with /exec) into the Streamlit secrets, together with the same TOKEN.
 * Anybody who knows the URL can call it, so TOKEN is what keeps strangers out: keep it secret
 * and never commit the real value anywhere.
 */
const TOKEN = 'CHANGE-ME-to-a-long-random-text';
const SHEET_NAME = 'rooms';
const CHUNK_SIZE = 45000;

function doPost(e) {
  let answer;
  try {
    const request = JSON.parse(e.postData.contents);
    if (request.token !== TOKEN) {
      answer = { ok: false, error: 'forbidden' };
    } else {
      const lock = LockService.getScriptLock();
      lock.waitLock(20000);  // one change at a time, so two saves never mix their pieces
      try {
        answer = handleRequest(request);
      } finally {
        lock.releaseLock();
      }
    }
  } catch (err) {
    answer = { ok: false, error: String(err) };
  }
  return ContentService.createTextOutput(JSON.stringify(answer))
    .setMimeType(ContentService.MimeType.JSON);
}

function getSheet() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  return book.getSheetByName(SHEET_NAME) || book.insertSheet(SHEET_NAME);
}

function findRow(sheet, code) {
  const last = sheet.getLastRow();
  if (last < 1) return 0;
  const codes = sheet.getRange(1, 1, last, 1).getValues();
  for (let i = 0; i < codes.length; i++) {
    if (String(codes[i][0]) === code) return i + 1;
  }
  return 0;
}

function handleRequest(request) {
  const sheet = getSheet();
  const row = findRow(sheet, request.code);

  if (request.action === 'load') {
    if (!row) return { ok: true, data: null };
    const width = Math.max(1, sheet.getLastColumn() - 2);
    const pieces = sheet.getRange(row, 3, 1, width).getValues()[0];
    return { ok: true, data: JSON.parse(pieces.join('')) };
  }

  if (request.action === 'save') {
    const text = JSON.stringify(request.data);
    const pieces = [];
    for (let i = 0; i < text.length; i += CHUNK_SIZE) pieces.push(text.substr(i, CHUNK_SIZE));
    const target = row || sheet.getLastRow() + 1;
    const width = Math.max(sheet.getLastColumn(), 2 + pieces.length);
    const wholeRow = sheet.getRange(target, 1, 1, width);
    wholeRow.clearContent();
    // Plain text format: otherwise Sheets turns pieces that look like numbers, dates or formulas into something else.
    const used = sheet.getRange(target, 1, 1, 2 + pieces.length);
    used.setNumberFormat('@');
    used.setValues([[request.code, new Date().toISOString()].concat(pieces)]);
    return { ok: true };
  }

  if (request.action === 'delete') {
    if (row) sheet.deleteRow(row);
    return { ok: true };
  }
  return { ok: false, error: 'unknown action' };
}
